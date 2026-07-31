"""Request-bound MCP projection of the existing CERA evidence tools.

The bridge owns no story authority and exposes no filesystem or database API.
It wraps one ``ReasonerEvidenceToolPort`` instance, and therefore one immutable
snapshot and one evidence budget, for the lifetime of a single Codex turn.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
import hmac
from importlib.metadata import version
import re
import secrets
import socket
import threading
import time
from typing import ClassVar

from cera.active_runtime import ACTIVE_RUNTIME_PROFILE
from cera.contracts import EvidenceRecordType
from cera.errors import (
    ContractValidationError,
    ErrorCode,
    EvidenceServiceError,
    IdentityError,
)
from cera.evidence import (
    CharacterSectionsRequest,
    ContinuityRequest,
    EvidenceBatch,
    EvidenceAmbiguityPolicy,
    EvidenceFetchRequest,
    EvidenceQueryPlan,
    EvidenceSearchRequest,
    EvidenceSnapshot,
)
from cera.ids import IdKind, TypedId, deterministic_id, require_kind
from cera.providers import CodexMcpRuntimeBinding, ProviderCallResult
from cera.schema import from_mapping, require_schema
from cera.serialization import canonical_sha256, domain_sha256, re_is_sha256, to_primitive

from .fake import ReasonerEvidenceToolPort, ResolveEntitiesRequest


MCP_SDK_VERSION = "1.29.0"
MCP_TOOL_CONTRACT_VERSION = ACTIVE_RUNTIME_PROFILE.reasoner.tool_contract_version
SUPPORTED_MCP_TOOL_CONTRACT_VERSIONS = (
    "cera.reasoner_evidence_mcp.v4",
    "cera.reasoner_evidence_mcp.v5",
    "cera.reasoner_evidence_mcp.v6",
    MCP_TOOL_CONTRACT_VERSION,
)
MCP_SERVER_NAME = "cera_request_evidence"
MCP_TOKEN_ENVIRONMENT_VARIABLE = "CERA_REQUEST_EVIDENCE_TOKEN"
MAX_FETCH_CITATION_ALIASES = 32


class McpEvidenceToolName(StrEnum):
    GET_TURN_SNAPSHOT = "cera_get_turn_snapshot"
    RESOLVE_ENTITIES = "cera_resolve_entities"
    SEARCH_EVIDENCE = "cera_search_evidence"
    SEARCH_QUERY_PLAN = "cera_search_query_plan"
    FETCH_EVIDENCE = "cera_fetch_evidence"
    GET_CHARACTER_SECTIONS = "cera_get_character_sections"
    GET_CONTINUITY = "cera_get_continuity"


ENABLED_MCP_EVIDENCE_TOOLS = tuple(value.value for value in McpEvidenceToolName)


_SAFE_DIAGNOSTIC = re.compile(r"^[A-Za-z0-9_.\[\]-]{1,160}$")


def _safe_diagnostic_token(value: str) -> bool:
    return bool(_SAFE_DIAGNOSTIC.fullmatch(value))


def _safe_contract_diagnostic(
    operation: McpEvidenceToolName,
    exc: Exception,
) -> tuple[str | None, str]:
    """Reduce decoder failures to field path plus issue class, never values."""

    message = str(exc)
    reason = "cross_field_constraint"
    if "missing required fields:" in message:
        reason = "missing_required"
        candidate = message.split("missing required fields:", 1)[1].split(",", 1)[0]
        field = candidate.strip()
    elif "contains unknown fields:" in message:
        reason = "unknown_field"
        candidate = message.split("contains unknown fields:", 1)[1].split(",", 1)[0]
        field = candidate.strip()
    else:
        if " must be " in message or " is not a valid " in message:
            reason = "invalid_type_or_format"
        elif "invalid enum" in message or "invalid" in message:
            reason = "invalid_value"
        match = re.search(
            r"(?:EvidenceSearchRequest|EvidenceFetchRequest|CharacterSectionsRequest|"
            r"ContinuityRequest|ResolveEntitiesRequest|EvidenceQueryPlan)\.([A-Za-z0-9_.\[\]-]+)",
            message,
        )
        field = match.group(1) if match else None
    if field is not None:
        field = field.strip().replace("[]", "[item]")
        if not _safe_diagnostic_token(field):
            field = None
    operation_root = operation.value.removeprefix("cera_")
    safe_path = f"{operation_root}.{field}" if field else operation_root
    if not _safe_diagnostic_token(safe_path):
        safe_path = None
    return safe_path, reason


def _pre_dispatch_diagnostic(error: BaseException) -> tuple[str, str]:
    """Reduce framework argument-validation detail to value-free tokens."""

    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        errors = getattr(current, "errors", None)
        if callable(errors):
            try:
                rows = errors()
            except Exception:
                rows = ()
            if rows and isinstance(rows[0], dict):
                row = rows[0]
                location = row.get("loc", ())
                if not isinstance(location, (list, tuple)):
                    location = ()
                parts = [
                    str(value)
                    for value in location
                    if isinstance(value, (str, int))
                ]
                field_path = "input" + (
                    "." + ".".join(parts) if parts else ""
                )
                issue = row.get("type")
                reason = (
                    issue.replace("_", "-")
                    if isinstance(issue, str)
                    else "framework-validation"
                )
                field_path = field_path[:160]
                reason = reason[:160]
                if _safe_diagnostic_token(field_path) and _safe_diagnostic_token(reason):
                    return field_path, reason
        current = current.__cause__ or current.__context__
    return "input", "framework-validation"


@dataclass(frozen=True, slots=True)
class McpEvidenceToolCallReceipt:
    call_index: int
    tool_name: McpEvidenceToolName
    request_sha256: str
    result_sha256: str | None
    success: bool
    error_code: ErrorCode | None
    lookup_receipt_id: TypedId | None
    returned_bytes: int
    error_field_path: str | None = None
    error_reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.call_index) is not int or self.call_index < 1:
            raise ContractValidationError("MCP tool call index must be positive")
        if not re_is_sha256(self.request_sha256):
            raise ContractValidationError("MCP tool request hash must be SHA-256")
        if self.result_sha256 is not None and not re_is_sha256(self.result_sha256):
            raise ContractValidationError("MCP tool result hash must be SHA-256")
        if self.success != (self.error_code is None and self.result_sha256 is not None):
            raise ContractValidationError("MCP tool success and error state disagree")
        if self.lookup_receipt_id is not None:
            require_kind(
                self.lookup_receipt_id,
                IdKind.LOOKUP_RECEIPT,
                "lookup_receipt_id",
            )
        if type(self.returned_bytes) is not int or self.returned_bytes < 0:
            raise ContractValidationError("MCP returned bytes cannot be negative")
        if self.success:
            if self.error_field_path is not None or self.error_reason is not None:
                raise ContractValidationError(
                    "successful MCP call cannot carry an error diagnostic"
                )
        elif not self.error_reason:
            raise ContractValidationError(
                "failed MCP call requires a privacy-safe error reason"
            )
        if self.error_field_path is not None and not _safe_diagnostic_token(
            self.error_field_path
        ):
            raise ContractValidationError("MCP error field path is not privacy-safe")
        if self.error_reason is not None and not _safe_diagnostic_token(
            self.error_reason
        ):
            raise ContractValidationError("MCP error reason is not privacy-safe")


@dataclass(frozen=True, slots=True)
class McpEvidenceBridgeReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.mcp_evidence_bridge_receipt.v2"

    schema_version: str
    bridge_receipt_id: TypedId
    provider_receipt_id: TypedId
    reasoner_request_sha256: str
    snapshot_token: TypedId
    snapshot_binding_sha256: str
    bridge_binding_sha256: str
    server_name: str
    transport: str
    tool_contract_version: str
    enabled_tools: tuple[str, ...]
    calls: tuple[McpEvidenceToolCallReceipt, ...]
    exact_evidence_ids: tuple[TypedId, ...]
    provider_observed_tool_calls: int
    cumulative_evidence_bytes: int
    authoritative_store_writes: int
    required: bool
    credential_retained: bool
    raw_source_retained: bool
    story_prose_retained: bool
    private_evidence_retained: bool

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        require_kind(
            self.bridge_receipt_id,
            IdKind.MCP_BRIDGE_RECEIPT,
            "bridge_receipt_id",
        )
        require_kind(
            self.provider_receipt_id,
            IdKind.PROVIDER_RECEIPT,
            "provider_receipt_id",
        )
        require_kind(self.snapshot_token, IdKind.SNAPSHOT, "snapshot_token")
        for value in (
            self.reasoner_request_sha256,
            self.snapshot_binding_sha256,
            self.bridge_binding_sha256,
        ):
            if not re_is_sha256(value):
                raise ContractValidationError("MCP bridge hashes must be SHA-256")
        if self.server_name != MCP_SERVER_NAME:
            raise ContractValidationError("MCP bridge server identity changed")
        if self.transport != "loopback_streamable_http":
            raise ContractValidationError("MCP bridge transport is not approved")
        if self.tool_contract_version not in SUPPORTED_MCP_TOOL_CONTRACT_VERSIONS:
            raise ContractValidationError("MCP tool contract version is unsupported")
        if self.enabled_tools != ENABLED_MCP_EVIDENCE_TOOLS:
            raise ContractValidationError("MCP evidence tool allow-list changed")
        if tuple(call.call_index for call in self.calls) != tuple(
            range(1, len(self.calls) + 1)
        ):
            raise ContractValidationError("MCP tool calls are not a complete sequence")
        if self.provider_observed_tool_calls != len(self.calls):
            raise ContractValidationError("provider and bridge MCP call counts disagree")
        for evidence_id in self.exact_evidence_ids:
            require_kind(evidence_id, IdKind.EVIDENCE, "exact_evidence_ids")
        if len(self.exact_evidence_ids) != len(set(self.exact_evidence_ids)):
            raise ContractValidationError("MCP exact evidence IDs must be unique")
        if min(self.provider_observed_tool_calls, self.cumulative_evidence_bytes) < 0:
            raise ContractValidationError("MCP bridge counters cannot be negative")
        if self.authoritative_store_writes != 0:
            raise ContractValidationError("MCP bridge cannot write story authority")
        if not self.required:
            raise ContractValidationError("CERA evidence MCP bridge must be required")
        if any(
            (
                self.credential_retained,
                self.raw_source_retained,
                self.story_prose_retained,
                self.private_evidence_retained,
            )
        ):
            raise ContractValidationError("MCP bridge receipt retained protected content")

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256("cera.mcp_evidence_bridge_receipt.v2", self)


class McpEvidenceBridgeError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


class McpEvidenceDispatcher:
    """Strict JSON-to-domain adapter around an existing evidence tool port."""

    def __init__(
        self,
        tools: ReasonerEvidenceToolPort,
        *,
        reasoner_request_sha256: str,
        snapshot: EvidenceSnapshot,
        maximum_tool_calls: int = 12,
    ) -> None:
        if not re_is_sha256(reasoner_request_sha256):
            raise ContractValidationError("reasoner request hash must be SHA-256")
        self.tools = tools
        self.reasoner_request_sha256 = reasoner_request_sha256
        self.snapshot = snapshot
        if type(maximum_tool_calls) is not int or not 1 <= maximum_tool_calls <= 32:
            raise ContractValidationError("MCP maximum tool calls must be 1..32")
        self.maximum_tool_calls = maximum_tool_calls
        self.calls: list[McpEvidenceToolCallReceipt] = []
        self._citation_alias_by_evidence_id: dict[TypedId, str] = {}
        self._lock = threading.Lock()

    @property
    def citation_aliases(self) -> dict[str, TypedId]:
        """Return request-local aliases for exact evidence fetched this turn."""

        return {
            alias: evidence_id
            for evidence_id, alias in self._citation_alias_by_evidence_id.items()
        }

    @property
    def bridge_binding_sha256(self) -> str:
        return domain_sha256(
            MCP_TOOL_CONTRACT_VERSION,
            {
                "reasoner_request_sha256": self.reasoner_request_sha256,
                "snapshot_token": str(self.snapshot.snapshot_token),
                "snapshot_binding_sha256": self.snapshot.binding_sha256,
                "server_name": MCP_SERVER_NAME,
                "enabled_tools": ENABLED_MCP_EVIDENCE_TOOLS,
                "required": True,
                "maximum_tool_calls": self.maximum_tool_calls,
            },
        )

    def invoke(self, tool_name: str, payload: dict[str, object] | None = None) -> dict:
        try:
            operation = McpEvidenceToolName(tool_name)
        except ValueError:
            raise McpEvidenceBridgeError(
                ErrorCode.EVIDENCE_BRIDGE_CONTRACT_INVALID,
                "unknown CERA evidence MCP tool",
            ) from None
        request_payload = {} if payload is None else payload
        if not isinstance(request_payload, dict):
            raise McpEvidenceBridgeError(
                ErrorCode.EVIDENCE_BRIDGE_CONTRACT_INVALID,
                "CERA evidence MCP arguments must be an object",
            )
        request_sha256 = canonical_sha256(
            {"tool_name": operation.value, "arguments": request_payload}
        )
        with self._lock:
            call_index = len(self.calls) + 1
            try:
                if call_index > self.maximum_tool_calls:
                    raise EvidenceServiceError(
                        ErrorCode.EVIDENCE_LIMIT_EXCEEDED,
                        "request-bound MCP tool-call budget exceeded",
                    )
                result = self._dispatch(operation, request_payload)
                primitive = to_primitive(result)
                if not isinstance(primitive, dict):
                    raise ContractValidationError(
                        "CERA evidence MCP result must be an object"
                    )
                if isinstance(result, EvidenceBatch) and result.exact_records:
                    rendered_records = primitive.get("exact_records")
                    if not isinstance(rendered_records, list) or len(
                        rendered_records
                    ) != len(result.exact_records):
                        raise ContractValidationError(
                            "exact evidence projection did not preserve record order"
                        )
                    for exact, rendered in zip(
                        result.exact_records, rendered_records, strict=True
                    ):
                        if not isinstance(rendered, dict):
                            raise ContractValidationError(
                                "exact evidence projection must contain objects"
                            )
                        alias = self._citation_alias_by_evidence_id.get(
                            exact.evidence_id
                        )
                        if alias is None:
                            next_index = len(self._citation_alias_by_evidence_id) + 1
                            if next_index > MAX_FETCH_CITATION_ALIASES:
                                raise EvidenceServiceError(
                                    ErrorCode.EVIDENCE_LIMIT_EXCEEDED,
                                    "request-local citation alias limit exceeded",
                                )
                            alias = f"evidence:fetch_{next_index:03d}"
                            self._citation_alias_by_evidence_id[
                                exact.evidence_id
                            ] = alias
                        rendered["citation_alias"] = alias
                primitive["cera_tool_budget"] = self._tool_budget(
                    operation=operation,
                    result=result,
                    call_index=call_index,
                )
                result_sha256 = canonical_sha256(primitive)
                lookup_receipt_id = None
                returned_bytes = 0
                if isinstance(result, EvidenceBatch):
                    lookup_receipt_id = result.receipt.lookup_receipt_id
                    returned_bytes = result.receipt.returned_bytes
                self.calls.append(
                    McpEvidenceToolCallReceipt(
                        call_index,
                        operation,
                        request_sha256,
                        result_sha256,
                        True,
                        None,
                        lookup_receipt_id,
                        returned_bytes,
                        None,
                        None,
                    )
                )
                return primitive
            except (ContractValidationError, EvidenceServiceError, IdentityError) as exc:
                code = (
                    exc.code
                    if isinstance(exc, EvidenceServiceError)
                    else ErrorCode.EVIDENCE_BRIDGE_CONTRACT_INVALID
                )
                field_path, reason = _safe_contract_diagnostic(operation, exc)
                self.calls.append(
                    McpEvidenceToolCallReceipt(
                        call_index,
                        operation,
                        request_sha256,
                        None,
                        False,
                        code,
                        None,
                        0,
                        field_path,
                        reason,
                    )
                )
                diagnostic = f"; field={field_path}" if field_path else ""
                raise McpEvidenceBridgeError(
                    code,
                    f"CERA evidence MCP call failed: {code.value}{diagnostic}; issue={reason}",
                ) from None

    def _tool_budget(
        self,
        *,
        operation: McpEvidenceToolName,
        result: object,
        call_index: int,
    ) -> dict[str, object]:
        """Project enforced counters and a deterministic next-step hint.

        The model previously had to infer a shared search budget from lookup
        receipts.  Publishing it directly prevents it from mistaking entity
        resolution for a free alternate search route.
        """

        status = self.tools.evidence_budget_status()
        remaining = status["remaining_followup_searches"]
        if isinstance(result, EvidenceBatch) and result.exact_records:
            next_step = "complete_if_exact_evidence_suffices"
        elif isinstance(result, EvidenceBatch) and result.references:
            next_step = "fetch_relevant_exact_references_before_any_new_search"
        elif remaining == 0:
            next_step = "use_fetched_or_seed_evidence_or_return_insufficient"
        elif operation is McpEvidenceToolName.GET_TURN_SNAPSHOT:
            next_step = "use_seed_or_start_one_bounded_query_plan"
        else:
            next_step = "refine_once_if_needed_or_return_insufficient"
        return {
            **status,
            "total_tool_calls_maximum": self.maximum_tool_calls,
            "total_tool_calls_used": call_index,
            "total_tool_calls_remaining": max(
                0, self.maximum_tool_calls - call_index
            ),
            "search_budget_shared_by": [
                McpEvidenceToolName.RESOLVE_ENTITIES.value,
                McpEvidenceToolName.SEARCH_EVIDENCE.value,
                McpEvidenceToolName.SEARCH_QUERY_PLAN.value,
            ],
            "exact_fetch_uses_followup_search_budget": False,
            "next_step": next_step,
        }

    def record_pre_dispatch_failure(
        self,
        tool_name: str,
        payload: object,
        error: BaseException,
    ) -> None:
        """Record FastMCP argument rejection without retaining argument values."""

        try:
            operation = McpEvidenceToolName(tool_name)
        except ValueError:
            return
        request_sha256 = canonical_sha256(
            {"tool_name": operation.value, "arguments": payload}
        )
        field_path, reason = _pre_dispatch_diagnostic(error)
        with self._lock:
            self.calls.append(
                McpEvidenceToolCallReceipt(
                    call_index=len(self.calls) + 1,
                    tool_name=operation,
                    request_sha256=request_sha256,
                    result_sha256=None,
                    success=False,
                    error_code=ErrorCode.EVIDENCE_BRIDGE_CONTRACT_INVALID,
                    lookup_receipt_id=None,
                    returned_bytes=0,
                    error_field_path=field_path,
                    error_reason=reason,
                )
            )

    def _dispatch(self, operation: McpEvidenceToolName, payload: dict[str, object]):
        if operation is McpEvidenceToolName.GET_TURN_SNAPSHOT:
            if payload:
                raise ContractValidationError("turn snapshot tool accepts no arguments")
            return self.tools.get_turn_snapshot()
        if operation is McpEvidenceToolName.FETCH_EVIDENCE and "evidence_id" in payload:
            unknown = set(payload) - {"evidence_id", "sections"}
            if unknown:
                raise ContractValidationError(
                    "EvidenceFetchRequest contains unknown fields: "
                    + ", ".join(sorted(unknown))
                )
            sections = payload.get("sections")
            if not isinstance(sections, (list, tuple)):
                raise ContractValidationError(
                    "EvidenceFetchRequest.sections must be an array"
                )
            request = EvidenceFetchRequest(
                evidence_ids=(
                    TypedId.parse(payload.get("evidence_id"), IdKind.EVIDENCE),
                ),
                sections=tuple(sections),
                include_superseded_audit=False,
            )
            return self.tools.fetch_evidence(request)
        model_type = {
            McpEvidenceToolName.RESOLVE_ENTITIES: ResolveEntitiesRequest,
            McpEvidenceToolName.SEARCH_EVIDENCE: EvidenceSearchRequest,
            McpEvidenceToolName.SEARCH_QUERY_PLAN: EvidenceQueryPlan,
            McpEvidenceToolName.FETCH_EVIDENCE: EvidenceFetchRequest,
            McpEvidenceToolName.GET_CHARACTER_SECTIONS: CharacterSectionsRequest,
            McpEvidenceToolName.GET_CONTINUITY: ContinuityRequest,
        }[operation]
        request = from_mapping(model_type, payload)
        if operation is McpEvidenceToolName.RESOLVE_ENTITIES:
            return self.tools.resolve_entities(request)
        if operation is McpEvidenceToolName.SEARCH_EVIDENCE:
            return self.tools.search_evidence(request)
        if operation is McpEvidenceToolName.SEARCH_QUERY_PLAN:
            return self.tools.search_query_plan(request)
        if operation is McpEvidenceToolName.FETCH_EVIDENCE:
            return self.tools.fetch_evidence(request)
        if operation is McpEvidenceToolName.GET_CHARACTER_SECTIONS:
            return self.tools.get_character_sections(request)
        return self.tools.get_continuity(request)


class RequestBoundMcpEvidenceBridge:
    """One-turn authenticated loopback MCP server over a dispatcher."""

    def __init__(
        self,
        tools: ReasonerEvidenceToolPort,
        *,
        reasoner_request_sha256: str,
        snapshot: EvidenceSnapshot,
        minimum_tool_calls: int = 0,
        startup_timeout_seconds: int = 10,
        tool_timeout_seconds: int = 10,
        maximum_tool_calls: int = 12,
    ) -> None:
        self.dispatcher = McpEvidenceDispatcher(
            tools,
            reasoner_request_sha256=reasoner_request_sha256,
            snapshot=snapshot,
            maximum_tool_calls=maximum_tool_calls,
        )
        self.minimum_tool_calls = minimum_tool_calls
        self.startup_timeout_seconds = startup_timeout_seconds
        self.tool_timeout_seconds = tool_timeout_seconds
        self.maximum_tool_calls = maximum_tool_calls
        self._token = secrets.token_urlsafe(32)
        self._url: str | None = None
        self._server = None
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._thread_errors: list[BaseException] = []

    @property
    def citation_aliases(self) -> dict[str, TypedId]:
        return self.dispatcher.citation_aliases

    @property
    def runtime_binding(self) -> CodexMcpRuntimeBinding:
        if self._url is None:
            raise McpEvidenceBridgeError(
                ErrorCode.EVIDENCE_BRIDGE_UNAVAILABLE,
                "CERA evidence MCP bridge is not running",
            )
        return CodexMcpRuntimeBinding(
            server_name=MCP_SERVER_NAME,
            url=self._url,
            bearer_token_environment_variable=MCP_TOKEN_ENVIRONMENT_VARIABLE,
            bearer_token=self._token,
            enabled_tools=ENABLED_MCP_EVIDENCE_TOOLS,
            binding_sha256=self.dispatcher.bridge_binding_sha256,
            startup_timeout_seconds=self.startup_timeout_seconds,
            tool_timeout_seconds=self.tool_timeout_seconds,
            minimum_tool_calls=self.minimum_tool_calls,
            maximum_tool_calls=self.maximum_tool_calls,
        )

    def start(self) -> "RequestBoundMcpEvidenceBridge":
        if self._thread is not None:
            raise McpEvidenceBridgeError(
                ErrorCode.EVIDENCE_BRIDGE_CONTRACT_INVALID,
                "CERA evidence MCP bridge cannot be started twice",
            )
        try:
            if version("mcp") != MCP_SDK_VERSION:
                raise RuntimeError("MCP SDK version mismatch")
            import uvicorn
            from mcp.server.auth.provider import AccessToken
            from mcp.server.auth.settings import AuthSettings
            from mcp.server.fastmcp import FastMCP
        except Exception as exc:
            raise McpEvidenceBridgeError(
                ErrorCode.EVIDENCE_BRIDGE_UNAVAILABLE,
                f"CERA evidence MCP runtime is unavailable ({type(exc).__name__})",
            ) from None

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(256)
        port = int(listener.getsockname()[1])
        self._socket = listener
        self._url = f"http://127.0.0.1:{port}/mcp"
        expected_token = self._token

        class SingleRequestTokenVerifier:
            async def verify_token(self, candidate: str):
                if not hmac.compare_digest(candidate, expected_token):
                    return None
                return AccessToken(
                    token=candidate,
                    client_id="cera-runtime-codex",
                    scopes=["cera.evidence.read"],
                )

        dispatcher = self.dispatcher

        class AuditedFastMCP(FastMCP):
            async def call_tool(self, name, arguments):
                before = len(dispatcher.calls)
                try:
                    return await super().call_tool(name, arguments)
                except Exception as exc:
                    if len(dispatcher.calls) == before:
                        dispatcher.record_pre_dispatch_failure(
                            name,
                            arguments,
                            exc,
                        )
                    raise

        mcp = AuditedFastMCP(
            MCP_SERVER_NAME,
            instructions=(
                "Request-bound read-only CERA evidence. Search returns references; "
                "hard decisions require exact fetch. The resolve, search, and query-plan "
                "tools share exactly four search operations. Read cera_tool_budget after "
                "every call, never call a shared-budget tool when remaining is zero, and "
                "never repeat a failed tool call. Never transfer private knowledge."
            ),
            host="127.0.0.1",
            port=port,
            json_response=True,
            stateless_http=True,
            max_request_body_size=65_536,
            token_verifier=SingleRequestTokenVerifier(),
            auth=AuthSettings(
                issuer_url=f"http://127.0.0.1:{port}/",
                resource_server_url=self._url,
                required_scopes=["cera.evidence.read"],
            ),
        )

        @mcp.tool(
            name=McpEvidenceToolName.GET_TURN_SNAPSHOT.value,
            description=(
                "Return the immutable request-bound snapshot, access scope, and current "
                "cera_tool_budget before retrieval."
            ),
            structured_output=True,
        )
        def get_turn_snapshot() -> dict[str, object]:
            return self.dispatcher.invoke(McpEvidenceToolName.GET_TURN_SNAPSHOT.value)

        @mcp.tool(
            name=McpEvidenceToolName.RESOLVE_ENTITIES.value,
            description=(
                "Resolve known typed entity IDs to broad compact references. This is a "
                "search and consumes one of the same four follow-up search operations; "
                "do not use it merely to restate typed IDs already present in the request."
            ),
            structured_output=True,
        )
        def resolve_entities(
            entity_ids: list[str], limit: int = 8
        ) -> dict[str, object]:
            return self.dispatcher.invoke(
                McpEvidenceToolName.RESOLVE_ENTITIES.value,
                {"entity_ids": entity_ids, "limit": limit},
            )

        @mcp.tool(
            name=McpEvidenceToolName.SEARCH_EVIDENCE.value,
            description=(
                "Search authorized evidence by bounded terms, entity IDs, tags, or "
                "record types. This consumes one of four shared search operations. "
                "Results are references, not exact decision evidence."
            ),
            structured_output=True,
        )
        def search_evidence(
            terms: list[str] | None = None,
            entity_ids: list[str] | None = None,
            tags: list[str] | None = None,
            record_types: list[EvidenceRecordType] | None = None,
            limit: int = 8,
        ) -> dict[str, object]:
            return self.dispatcher.invoke(
                McpEvidenceToolName.SEARCH_EVIDENCE.value,
                {
                    "terms": terms or [],
                    "entity_ids": entity_ids or [],
                    "tags": tags or [],
                    "record_types": [str(value) for value in record_types or []],
                    "limit": limit,
                },
            )

        @mcp.tool(
            name=McpEvidenceToolName.SEARCH_QUERY_PLAN.value,
            description=(
                "Search authorized evidence using one bounded primary phrase set and "
                "explicit paraphrase variants. Results are references; exact decision "
                "evidence still requires fetch. This consumes one of four shared search "
                "operations. Terms within one set are all required; alternate sets are OR "
                "variants, so prefer one or two discriminative terms per set."
            ),
            structured_output=True,
        )
        def search_query_plan(
            primary_terms: list[str],
            alternate_term_sets: list[list[str]] | None = None,
            entity_ids: list[str] | None = None,
            tags: list[str] | None = None,
            record_types: list[EvidenceRecordType] | None = None,
            limit: int = 8,
            maximum_variants: int = 4,
        ) -> dict[str, object]:
            return self.dispatcher.invoke(
                McpEvidenceToolName.SEARCH_QUERY_PLAN.value,
                {
                    "schema_version": EvidenceQueryPlan.SCHEMA_VERSION,
                    "primary_terms": primary_terms,
                    "alternate_term_sets": alternate_term_sets or [],
                    "entity_ids": entity_ids or [],
                    "tags": tags or [],
                    "record_types": [str(value) for value in record_types or []],
                    "limit": limit,
                    "maximum_variants": maximum_variants,
                    "ambiguity_policy": (
                        EvidenceAmbiguityPolicy.RETURN_BOUNDED_CANDIDATES.value
                    ),
                },
            )

        @mcp.tool(
            name=McpEvidenceToolName.FETCH_EVIDENCE.value,
            description=(
                "Fetch exact authorized sections for evidence IDs returned by search. "
                "Use this before citing evidence in a hard decision."
            ),
            structured_output=True,
        )
        def fetch_evidence(
            evidence_id: str,
            sections: list[str],
        ) -> dict[str, object]:
            return self.dispatcher.invoke(
                McpEvidenceToolName.FETCH_EVIDENCE.value,
                {
                    "evidence_id": evidence_id,
                    "sections": sections,
                },
            )

        @mcp.tool(
            name=McpEvidenceToolName.GET_CHARACTER_SECTIONS.value,
            description="Fetch authorized sections for one character without a full dossier dump.",
            structured_output=True,
        )
        def get_character_sections(
            character_id: str,
            sections: list[str],
            limit: int = 8,
        ) -> dict[str, object]:
            return self.dispatcher.invoke(
                McpEvidenceToolName.GET_CHARACTER_SECTIONS.value,
                {"character_id": character_id, "sections": sections, "limit": limit},
            )

        @mcp.tool(
            name=McpEvidenceToolName.GET_CONTINUITY.value,
            description="Traverse authorized linked evidence from exact starting evidence IDs.",
            structured_output=True,
        )
        def get_continuity(
            starting_evidence_ids: list[str],
            sections: list[str],
            maximum_depth: int = 2,
        ) -> dict[str, object]:
            return self.dispatcher.invoke(
                McpEvidenceToolName.GET_CONTINUITY.value,
                {
                    "starting_evidence_ids": starting_evidence_ids,
                    "sections": sections,
                    "maximum_depth": maximum_depth,
                },
            )

        server = uvicorn.Server(
            uvicorn.Config(
                mcp.streamable_http_app(),
                host="127.0.0.1",
                port=port,
                log_level="error",
                lifespan="on",
            )
        )
        self._server = server

        def serve() -> None:
            try:
                asyncio.run(server.serve(sockets=[listener]))
            except BaseException as exc:  # captured for the owning request
                self._thread_errors.append(exc)

        self._thread = threading.Thread(
            target=serve,
            name="cera-request-evidence-mcp",
            daemon=True,
        )
        self._thread.start()
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            if server.started:
                return self
            if self._thread_errors or not self._thread.is_alive():
                break
            time.sleep(0.01)
        self.stop(suppress_errors=True)
        raise McpEvidenceBridgeError(
            ErrorCode.EVIDENCE_BRIDGE_UNAVAILABLE,
            "CERA evidence MCP bridge failed to start",
        )

    def stop(self, *, suppress_errors: bool = False) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        alive = self._thread is not None and self._thread.is_alive()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        self._url = None
        if not suppress_errors and (alive or self._thread_errors):
            raise McpEvidenceBridgeError(
                ErrorCode.EVIDENCE_BRIDGE_UNAVAILABLE,
                "CERA evidence MCP bridge did not stop cleanly",
            )

    def finalize(self, provider_result: ProviderCallResult) -> McpEvidenceBridgeReceipt:
        calls = tuple(self.dispatcher.calls)
        if provider_result.tool_call_count != len(calls):
            raise McpEvidenceBridgeError(
                ErrorCode.EVIDENCE_BRIDGE_CONTRACT_INVALID,
                "provider and bridge MCP call counts disagree",
            )
        if provider_result.tool_names != tuple(call.tool_name.value for call in calls):
            raise McpEvidenceBridgeError(
                ErrorCode.EVIDENCE_BRIDGE_CONTRACT_INVALID,
                "provider and bridge MCP tool sequences disagree",
            )
        if any(value != MCP_SERVER_NAME for value in provider_result.tool_server_names):
            raise McpEvidenceBridgeError(
                ErrorCode.EVIDENCE_BRIDGE_CONTRACT_INVALID,
                "provider observed an unbound MCP server",
            )
        exact_evidence = getattr(self.dispatcher.tools, "exact_evidence", {})
        cumulative_bytes = getattr(
            self.dispatcher.tools, "cumulative_returned_bytes", 0
        )
        receipt_id = deterministic_id(
            IdKind.MCP_BRIDGE_RECEIPT,
            "cera.mcp_evidence_bridge.receipt.v2",
            (
                f"{self.dispatcher.reasoner_request_sha256}|"
                f"{provider_result.receipt.provider_receipt_id}|"
                f"{domain_sha256('cera.mcp_evidence_bridge.calls.v2', calls)}"
            ),
        )
        return McpEvidenceBridgeReceipt(
            schema_version=McpEvidenceBridgeReceipt.SCHEMA_VERSION,
            bridge_receipt_id=receipt_id,
            provider_receipt_id=provider_result.receipt.provider_receipt_id,
            reasoner_request_sha256=self.dispatcher.reasoner_request_sha256,
            snapshot_token=self.dispatcher.snapshot.snapshot_token,
            snapshot_binding_sha256=self.dispatcher.snapshot.binding_sha256,
            bridge_binding_sha256=self.dispatcher.bridge_binding_sha256,
            server_name=MCP_SERVER_NAME,
            transport="loopback_streamable_http",
            tool_contract_version=MCP_TOOL_CONTRACT_VERSION,
            enabled_tools=ENABLED_MCP_EVIDENCE_TOOLS,
            calls=calls,
            exact_evidence_ids=tuple(sorted(exact_evidence, key=str)),
            provider_observed_tool_calls=provider_result.tool_call_count,
            cumulative_evidence_bytes=cumulative_bytes,
            authoritative_store_writes=0,
            required=True,
            credential_retained=False,
            raw_source_retained=False,
            story_prose_retained=False,
            private_evidence_retained=False,
        )

    def __enter__(self) -> "RequestBoundMcpEvidenceBridge":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop(suppress_errors=exc_type is not None)
