"""Request-bound read-only MCP view of one continuous world branch."""

from __future__ import annotations

import asyncio
import hmac
import inspect
import json
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any, Protocol, cast

from cera.errors import ContractValidationError, ProviderToolRequestError, StateConflictError
from cera.providers import CodexMcpRuntimeBinding
from cera.serialization import canonical_sha256, domain_sha256, text_sha256

from .evidence import (
    EvidenceVisibility,
    RequestEvidenceBindingRegistry,
    RequestEvidenceBindingV1,
)
from .sessions import ContinuousSessionRole, WorldPathAccessPolicyV1

WORLD_MCP_SDK_VERSION = "1.29.0"
WORLD_MCP_SERVER_NAME = "cera_continuous_world"
WORLD_MCP_TOKEN_ENV = "CERA_REQUEST_EVIDENCE_TOKEN"
GET_TURN_CONTEXT_TOOL_DESCRIPTION = (
    "Call first with character_ids omitted; never select multiple explicit IDs. "
    "Returned dossiers are complete, including relationship and memory context."
)
GET_CHARACTER_CONTEXT_TOOL_DESCRIPTION = (
    "Fetch one omitted actor only. The returned dossier is complete, including "
    "relationship and memory context; do not refetch its subsets."
)
GET_RELATIONSHIP_CONTEXT_TOOL_DESCRIPTION = (
    "Fetch this narrow subset only for an actor whose complete dossier has not "
    "returned; a complete dossier already includes relationship and memory context."
)
GET_MEMORY_CONTEXT_TOOL_DESCRIPTION = (
    "Fetch this narrow subset only for an actor whose complete dossier has not "
    "returned; a complete dossier already includes relationship and memory context."
)
NAMED_WORLD_MCP_INSTRUCTIONS = (
    "Read-only request-bound CERA world lookup. Call get_turn_context first with "
    "character_ids omitted and never select multiple explicit IDs. A returned "
    "dossier is complete, including relationship and memory context; fetch one "
    "omitted actor or one narrow subset only while that actor has no complete dossier."
)
COGNITION_NAMED_WORLD_MCP_INSTRUCTIONS = (
    NAMED_WORLD_MCP_INSTRUCTIONS
    + " Returned context_refs are request-local context only and are forbidden in "
    "cognition citation fields. Search rows are locators only. Only evidence_refs "
    "from a successful eligible get_exact_record call may support a hard factual "
    "decision; an oversize exact record remains context only."
)
COGNITION_SEARCH_EVIDENCE_TOOL_DESCRIPTION = (
    "Return authorized per-record locators. Every returned context_ref is context "
    "only and cannot be cited; fetch an advertised record with get_exact_record."
)
COGNITION_GET_EXACT_RECORD_TOOL_DESCRIPTION = (
    "Fetch one record advertised by a successful search in this request. Cite its "
    "evidence_ref only when citation_eligibility is eligible_after_exact_fetch; a "
    "context_ref, including an oversize exact record, is never citable."
)
WORLD_MCP_TOOLS = (
    "cera_world_list",
    "cera_world_search",
    "cera_world_read",
)
WORLD_MCP_MAXIMUM_CALLS = 32


@dataclass(frozen=True, slots=True)
class ContinuousWorldToolCallV1:
    call_index: int
    tool_name: str
    request_sha256: str
    returned_bytes: int
    duration_ns: int
    success: bool
    provider_request_failure: bool = False


class AdditionalWorldToolHandler(Protocol):
    """Optional typed extension that leaves historical generic tools intact."""

    tool_names: tuple[str, ...]
    provider_request_failure_policy_id: str

    @property
    def binding_sha256(self) -> str: ...

    def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


def _typed_provider_contract_id(
    handler: AdditionalWorldToolHandler | None,
) -> str | None:
    """Detect the opt-in typed seam without changing legacy handler shape."""

    if handler is None:
        return None
    contract_id = getattr(handler, "provider_contract_id", None)
    if contract_id is None:
        return None
    if not isinstance(contract_id, str) or not contract_id.strip():
        raise ContractValidationError("additional world provider contract is invalid")
    for hook_name in ("finalize_provider_turn", "abort_provider_turn"):
        if not callable(getattr(handler, hook_name, None)):
            raise ContractValidationError(
                "typed additional world handler lacks provider-turn hooks"
            )
    try:
        parameters = inspect.signature(handler.invoke).parameters.values()
    except (TypeError, ValueError) as exc:
        raise ContractValidationError(
            "typed additional world handler invoke contract is unavailable"
        ) from exc
    accepts_kwargs = any(value.kind is inspect.Parameter.VAR_KEYWORD for value in parameters)
    names = {
        value.name
        for value in parameters
        if value.kind
        in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
    }
    if not accepts_kwargs and not {
        "provider_call_index",
        "provider_request_sha256",
    }.issubset(names):
        raise ContractValidationError("typed additional world handler lacks provider call custody")
    return contract_id


class ContinuousWorldToolDispatcher:
    """Mechanical world lookup with role and current-candidate isolation."""

    def __init__(
        self,
        branch_root: Path,
        role: ContinuousSessionRole,
        *,
        world_id: str | None = None,
        branch_id: str | None = None,
        current_turn_id: str | None = None,
        maximum_calls: int = WORLD_MCP_MAXIMUM_CALLS,
        evidence_registry: RequestEvidenceBindingRegistry | None = None,
        require_private_search_scope: bool = False,
        allowed_private_character_ids: tuple[str, ...] | None = None,
        additional_tool_handler: AdditionalWorldToolHandler | None = None,
        require_additional_provider_contract: bool = False,
    ) -> None:
        self.branch_root = branch_root.resolve()
        self.role = role
        self.current_turn_id = current_turn_id
        if not self.branch_root.is_dir():
            raise ContractValidationError("continuous world branch root is unavailable")
        if not 1 <= maximum_calls <= WORLD_MCP_MAXIMUM_CALLS:
            raise ContractValidationError("continuous world MCP call ceiling is invalid")
        self.maximum_calls = maximum_calls
        self.policy = WorldPathAccessPolicyV1(str(self.branch_root))
        self.calls: list[ContinuousWorldToolCallV1] = []
        self.local_debug_calls: list[dict[str, Any]] = []
        self.require_private_search_scope = require_private_search_scope
        if allowed_private_character_ids is not None and (
            len(allowed_private_character_ids) != len(set(allowed_private_character_ids))
            or any(
                not isinstance(value, str) or not value.startswith("character:")
                for value in allowed_private_character_ids
            )
        ):
            raise ContractValidationError("continuous world private-character allowance is invalid")
        self.allowed_private_character_ids = (
            None
            if allowed_private_character_ids is None
            else frozenset(allowed_private_character_ids)
        )
        self.additional_tool_handler = additional_tool_handler
        if type(require_additional_provider_contract) is not bool:
            raise ContractValidationError(
                "continuous world provider-contract requirement is invalid"
            )
        self.additional_provider_contract_id = _typed_provider_contract_id(additional_tool_handler)
        if require_additional_provider_contract and self.additional_provider_contract_id is None:
            raise ContractValidationError("cognition world tools require a typed provider contract")
        semantic_world_id, semantic_branch_id = self._semantic_identity(
            world_id=world_id,
            branch_id=branch_id,
        )
        self.evidence_registry = evidence_registry or RequestEvidenceBindingRegistry(
            world_id=semantic_world_id,
            branch_id=semantic_branch_id,
            turn_id=current_turn_id or "request_lookup",
        )
        if (
            self.evidence_registry.world_id != semantic_world_id
            or self.evidence_registry.branch_id != semantic_branch_id
        ):
            raise StateConflictError(
                "continuous world evidence registry changed semantic branch scope"
            )
        self._lock = threading.Lock()

    @property
    def tool_names(self) -> tuple[str, ...]:
        additional = (
            () if self.additional_tool_handler is None else self.additional_tool_handler.tool_names
        )
        if len(additional) != len(set(additional)) or set(additional).intersection(WORLD_MCP_TOOLS):
            raise StateConflictError("continuous world additional tool names changed")
        # A typed request gets only its named semantic surface.  Historical
        # callers with no extension retain the exact generic three-tool set.
        # Mixing both would let a role bypass named-tool visibility policy by
        # guessing a generic path.
        return WORLD_MCP_TOOLS if not additional else additional

    def _semantic_identity(
        self,
        *,
        world_id: str | None,
        branch_id: str | None,
    ) -> tuple[str, str]:
        """Resolve semantic custody without treating hashed directories as IDs."""

        if (world_id is None) != (branch_id is None):
            raise ContractValidationError(
                "continuous world semantic identity must be supplied together"
            )
        identity_path = self.branch_root / "BRANCH_IDENTITY.json"
        identity: dict[str, Any] | None = None
        if identity_path.is_file() and not identity_path.is_symlink():
            raw = json.loads(identity_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or set(raw) != {"world_id", "branch_id"}:
                raise StateConflictError("continuous world branch identity changed")
            if not all(
                isinstance(raw.get(name), str) and str(raw[name]).strip()
                for name in ("world_id", "branch_id")
            ):
                raise StateConflictError("continuous world branch identity is invalid")
            identity = raw
        if world_id is not None and branch_id is not None:
            if not world_id.strip() or not branch_id.strip():
                raise ContractValidationError("continuous world semantic identity is incomplete")
            if identity is not None and identity != {
                "world_id": world_id,
                "branch_id": branch_id,
            }:
                raise PermissionError(
                    "continuous world semantic identity does not match its branch root"
                )
            return world_id, branch_id
        if identity is not None:
            return str(identity["world_id"]), str(identity["branch_id"])
        # Historical continuous-world fixtures predate BRANCH_IDENTITY.json.
        # They use semantic directory names rather than Pi Scene hash keys.
        return self.branch_root.parent.name, self.branch_root.name

    @property
    def binding_sha256(self) -> str:
        payload: dict[str, Any] = {
            "branch_root_sha256": text_sha256(str(self.branch_root).casefold()),
            "world_id": self.evidence_registry.world_id,
            "branch_id": self.evidence_registry.branch_id,
            "role": self.role.value,
            "current_turn_id": self.current_turn_id,
            "tools": self.tool_names,
            "maximum_calls": self.maximum_calls,
        }
        if (
            self.allowed_private_character_ids is not None
            or self.additional_tool_handler is not None
        ):
            payload.update(
                {
                    "private_character_ids": (
                        None
                        if self.allowed_private_character_ids is None
                        else sorted(self.allowed_private_character_ids)
                    ),
                    "additional_binding_sha256": (
                        None
                        if self.additional_tool_handler is None
                        else self.additional_tool_handler.binding_sha256
                    ),
                    "provider_request_failure_policy_id": (
                        None
                        if self.additional_tool_handler is None
                        else self.additional_tool_handler.provider_request_failure_policy_id
                    ),
                }
            )
        if self.additional_provider_contract_id is not None:
            payload["additional_provider_contract_id"] = self.additional_provider_contract_id
        return domain_sha256(
            "cera.continuous_world_mcp.v1",
            payload,
        )

    def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name not in self.tool_names or not isinstance(arguments, dict):
            raise ContractValidationError("continuous world MCP request is invalid")
        started = time.perf_counter_ns()
        request_hash = canonical_sha256({"tool": tool_name, "arguments": arguments})
        success = False
        provider_request_failure = False
        returned_bytes = 0
        with self._lock:
            if len(self.calls) >= self.maximum_calls:
                raise StateConflictError("continuous world MCP reached its hard runaway ceiling")
            try:
                if tool_name == "cera_world_list":
                    result = self._list(**arguments)
                elif tool_name == "cera_world_search":
                    result = self._search(**arguments)
                elif tool_name == "cera_world_read":
                    result = self._read(**arguments)
                else:
                    if self.additional_tool_handler is None:
                        raise ContractValidationError(
                            "continuous world additional tool is unavailable"
                        )
                    if self.additional_provider_contract_id is None:
                        result = self.additional_tool_handler.invoke(tool_name, arguments)
                    else:
                        result = cast(Any, self.additional_tool_handler).invoke(
                            tool_name,
                            arguments,
                            provider_call_index=len(self.calls) + 1,
                            provider_request_sha256=request_hash,
                        )
                returned_bytes = len(
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                )
                success = True
                self.local_debug_calls.append(
                    {
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "result_paths": [
                            value.get("path")
                            for value in result.get("records", [])
                            if isinstance(value, dict) and isinstance(value.get("path"), str)
                        ]
                        if isinstance(result, dict)
                        else [],
                        "exact_path": (
                            result.get("path")
                            if isinstance(result, dict) and isinstance(result.get("path"), str)
                            else None
                        ),
                        "returned_bytes": returned_bytes,
                        "evidence_binding": (
                            result.get("evidence_binding") if isinstance(result, dict) else None
                        ),
                        "evidence_refs": (
                            result.get("evidence_refs", []) if isinstance(result, dict) else []
                        ),
                        "context_refs": (
                            result.get("context_refs", []) if isinstance(result, dict) else []
                        ),
                    }
                )
                return result
            except ProviderToolRequestError:
                provider_request_failure = True
                raise
            finally:
                self.calls.append(
                    ContinuousWorldToolCallV1(
                        call_index=len(self.calls) + 1,
                        tool_name=tool_name,
                        request_sha256=request_hash,
                        returned_bytes=returned_bytes,
                        duration_ns=time.perf_counter_ns() - started,
                        success=success,
                        provider_request_failure=provider_request_failure,
                    )
                )

    def failed_tool_calls_are_provider_request_failures(
        self,
        server_names: tuple[str, ...],
        tool_names: tuple[str, ...],
        tool_call_count: int,
        failed_tool_call_count: int,
    ) -> bool:
        """Reconcile content-free provider observations with local call origins."""

        with self._lock:
            calls = tuple(self.calls)
        if (
            failed_tool_call_count <= 0
            or tool_call_count != len(calls)
            or len(server_names) != tool_call_count
            or any(value != WORLD_MCP_SERVER_NAME for value in server_names)
            or tuple(value.tool_name for value in calls) != tool_names
        ):
            return False
        failed_calls = tuple(value for value in calls if not value.success)
        return len(failed_calls) == failed_tool_call_count and all(
            value.provider_request_failure for value in failed_calls
        )

    def record_pre_dispatch_failure(
        self,
        tool_name: str,
        arguments: object,
        *,
        framework_argument_validation: bool,
        expected_call_count: int,
        duration_ns: int,
    ) -> None:
        """Record a framework-rejected call without retaining argument values."""

        if (
            tool_name not in self.tool_names
            or not isinstance(arguments, dict)
            or type(framework_argument_validation) is not bool
            or type(expected_call_count) is not int
            or type(duration_ns) is not int
            or duration_ns < 0
        ):
            return
        try:
            request_hash = canonical_sha256({"tool": tool_name, "arguments": arguments})
        except Exception:
            return
        handler = self.additional_tool_handler
        provider_request_failure = (
            handler is not None
            and tool_name in handler.tool_names
            and framework_argument_validation
        )
        with self._lock:
            # A concurrent or already-dispatched call makes the observation
            # ambiguous.  Leave it unrecorded so reconciliation fails closed.
            if len(self.calls) != expected_call_count or len(self.calls) >= self.maximum_calls:
                return
            self.calls.append(
                ContinuousWorldToolCallV1(
                    call_index=len(self.calls) + 1,
                    tool_name=tool_name,
                    request_sha256=request_hash,
                    returned_bytes=0,
                    duration_ns=duration_ns,
                    success=False,
                    provider_request_failure=provider_request_failure,
                )
            )

    def _allowed_roots(self) -> tuple[Path, ...]:
        roots = [self.branch_root / "ACTIVE", self.branch_root / "DERIVED"]
        if self.role is ContinuousSessionRole.VALIDATOR and self.current_turn_id:
            roots.append(self.branch_root / "CANDIDATES" / self.current_turn_id / "ACTIVE_VIEW")
        return tuple(value for value in roots if value.is_dir())

    def _authorized_path(self, relative_path: str) -> Path:
        if (
            not isinstance(relative_path, str)
            or not relative_path.strip()
            or Path(relative_path).is_absolute()
            or ".." in Path(relative_path).parts
            or ":" in relative_path
        ):
            raise PermissionError("continuous world path is invalid")
        target = (self.branch_root / relative_path).resolve()
        self.policy.authorize(
            self.role,
            str(target),
            current_turn_id=self.current_turn_id,
        )
        if not target.is_file() or target.is_symlink():
            raise FileNotFoundError("continuous world record is unavailable")
        return target

    def _list(self, prefix: str = "", limit: int = 100) -> dict[str, Any]:
        if not isinstance(prefix, str) or type(limit) is not int or not 1 <= limit <= 100:
            raise ContractValidationError("continuous world list arguments are invalid")
        rows = []
        for root in self._allowed_roots():
            for path in sorted(value for value in root.rglob("*") if value.is_file()):
                relative = path.relative_to(self.branch_root).as_posix()
                if prefix and not relative.casefold().startswith(prefix.casefold()):
                    continue
                descriptor = self._visible_descriptor(path)
                if descriptor is None:
                    continue
                rows.append(descriptor)
                if len(rows) == limit:
                    return {"records": rows, "truncated": True}
        return {"records": rows, "truncated": False}

    def _search(
        self,
        terms: list[str],
        record_types: list[str] | None = None,
        limit: int = 20,
        knowledge_owner_id: str | None = None,
    ) -> dict[str, Any]:
        if (
            not isinstance(terms, list)
            or not terms
            or not all(isinstance(value, str) and value.strip() for value in terms)
            or type(limit) is not int
            or not 1 <= limit <= 100
            or (
                knowledge_owner_id is not None
                and (
                    not isinstance(knowledge_owner_id, str)
                    or not knowledge_owner_id.startswith("character:")
                )
            )
        ):
            raise ContractValidationError("continuous world search arguments are invalid")
        types = {value.casefold() for value in (record_types or [])}
        needles = tuple(value.casefold() for value in terms)
        if (
            knowledge_owner_id is not None
            and self.allowed_private_character_ids is not None
            and knowledge_owner_id not in self.allowed_private_character_ids
        ):
            # Reject the owner before any private path is opened or any query
            # term is compared, closing cross-owner term probing.
            raise PermissionError("continuous world private search owner is unauthorized")
        rows = []
        for root in self._allowed_roots():
            for path in sorted(value for value in root.rglob("*") if value.is_file()):
                relative = path.relative_to(self.branch_root).as_posix()
                record_type = relative.split("/", 2)[1].casefold() if "/" in relative else ""
                if types and record_type not in types:
                    continue
                descriptor = self._visible_descriptor(path)
                if descriptor is None:
                    continue
                if (
                    self.require_private_search_scope
                    and (
                        descriptor["visibility"] != EvidenceVisibility.PUBLIC.value
                        or descriptor["source_visibility"] != "public"
                    )
                    and (
                        descriptor["knowledge_owner_id"] is None
                        or descriptor["knowledge_owner_id"] != knowledge_owner_id
                    )
                ):
                    # Do not evaluate the query against another character's
                    # private bytes.  This closes yes/no term-probing while
                    # retaining explicit owner-scoped character lookup.
                    continue
                text = path.read_text(encoding="utf-8")
                haystack = (relative + "\n" + text).casefold()
                if not all(value in haystack for value in needles):
                    continue
                row = descriptor
                row["matched_terms"] = list(terms)
                rows.append(row)
                if len(rows) == limit:
                    return {"records": rows, "truncated": True}
        return {"records": rows, "truncated": False}

    def _read(self, path: str) -> dict[str, Any]:
        target = self._authorized_path(path)
        raw = target.read_bytes()
        if len(raw) > 1_048_576:
            raise StateConflictError("continuous world record exceeds exact-read ceiling")
        text = raw.decode("utf-8")
        descriptor = self._descriptor(target)
        content = json.loads(text) if target.suffix.casefold() == ".json" else text
        visibility, knowledge_owner_id, source_visibility = self._visibility(
            target,
            content,
        )
        if visibility is EvidenceVisibility.CREATOR_PRIVATE:
            raise PermissionError("creator-private evidence is not exposed to provider sessions")
        if (
            visibility is EvidenceVisibility.CHARACTER_PRIVATE
            and self.allowed_private_character_ids is not None
            and knowledge_owner_id not in self.allowed_private_character_ids
        ):
            raise PermissionError("continuous world private record owner is unauthorized")
        relative = target.relative_to(self.branch_root).as_posix()
        read_operation_sha256 = canonical_sha256(
            {
                "tool": "cera_world_read",
                "path": relative,
                "source_sha256": descriptor["content_sha256"],
                "call_index": len(self.calls) + 1,
            }
        )
        binding = self.evidence_registry.allocate_world_record(
            relative_path=relative,
            source_sha256=descriptor["content_sha256"],
            record_revision=descriptor["revision"],
            record_type=_record_type(relative),
            visibility=visibility,
            knowledge_owner_id=(
                str(knowledge_owner_id) if knowledge_owner_id is not None else None
            ),
            exact_read_operation_sha256=read_operation_sha256,
        )
        return {
            **descriptor,
            "content": content,
            "source_visibility": source_visibility,
            "evidence_binding": _binding_descriptor(binding),
        }

    def _descriptor(self, path: Path) -> dict[str, Any]:
        relative = path.relative_to(self.branch_root).as_posix()
        text = path.read_text(encoding="utf-8")
        revision = None
        payload: object = text
        if path.suffix.casefold() == ".json":
            payload = json.loads(text)
            if isinstance(payload, dict):
                revision = payload.get("_cera_revision")
        visibility, owner_id, source_visibility = self._visibility(path, payload)
        return {
            "path": relative,
            "revision": revision,
            "byte_count": len(text.encode("utf-8")),
            "content_sha256": text_sha256(text),
            "visibility": visibility.value,
            "source_visibility": source_visibility,
            "knowledge_owner_id": owner_id,
        }

    def _visible_descriptor(self, path: Path) -> dict[str, Any] | None:
        descriptor = self._descriptor(path)
        if descriptor["visibility"] == EvidenceVisibility.CREATOR_PRIVATE.value:
            return None
        return descriptor

    def _visibility(
        self,
        path: Path,
        content: object,
    ) -> tuple[EvidenceVisibility, str | None, str]:
        raw_visibility = "public"
        owner_id: object = None
        if isinstance(content, dict):
            raw_visibility = str(content.get("visibility", "public")).casefold()
            owner_id = (
                content.get("knowledge_owner_id")
                or content.get("owner_character_id")
                or content.get("character_id")
            )
            if owner_id is None:
                record = content.get("record")
                if isinstance(record, dict):
                    raw_visibility = str(
                        content.get("visibility", record.get("visibility", "public"))
                    ).casefold()
                    owner_id = record.get("owner_id")
                    owners = record.get("knowledge_owner_ids")
                    subjects = record.get("subject_ids")
                    if owner_id is None and isinstance(owners, list) and len(owners) == 1:
                        owner_id = owners[0]
                    if owner_id is None and isinstance(subjects, list):
                        character_subjects = [
                            value
                            for value in subjects
                            if isinstance(value, str) and value.startswith("character:")
                        ]
                        if len(character_subjects) == 1:
                            owner_id = character_subjects[0]
        record_type = _record_type(path.relative_to(self.branch_root).as_posix())
        if raw_visibility in {"creator_private", "creator-only"}:
            return (
                EvidenceVisibility.CREATOR_PRIVATE,
                str(owner_id) if owner_id is not None else None,
                raw_visibility,
            )
        if raw_visibility in {
            "private",
            "character_private",
            "owner_private",
            "system_private",
        } or record_type in {"characters", "character_summaries", "current_character_dossiers"}:
            if isinstance(owner_id, str) and owner_id.startswith("character:"):
                return EvidenceVisibility.CHARACTER_PRIVATE, owner_id, raw_visibility
            # System-private multi-owner/world records remain available only
            # inside this already branch-confined role view.  The evidence DTO
            # has no system-private variant, so retain the exact source label
            # while using its public-shaped binding rather than inventing an
            # NPC knowledge owner.
            return EvidenceVisibility.PUBLIC, None, raw_visibility
        return EvidenceVisibility.PUBLIC, None, raw_visibility


class ContinuousWorldMcpBridge:
    """Ephemeral authenticated loopback MCP server for one role and turn."""

    def __init__(self, dispatcher: ContinuousWorldToolDispatcher) -> None:
        self.dispatcher = dispatcher
        self._token = secrets.token_urlsafe(32)
        self._url: str | None = None
        self._server = None
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._errors: list[BaseException] = []
        self._additional_finalization: object | None = None
        self._provider_turn_finalized = False

    @property
    def runtime_binding(self) -> CodexMcpRuntimeBinding:
        if self._url is None:
            raise StateConflictError("continuous world MCP bridge is not running")
        return CodexMcpRuntimeBinding(
            server_name=WORLD_MCP_SERVER_NAME,
            url=self._url,
            bearer_token_environment_variable=WORLD_MCP_TOKEN_ENV,
            bearer_token=self._token,
            enabled_tools=self.dispatcher.tool_names,
            binding_sha256=self.dispatcher.binding_sha256,
            startup_timeout_seconds=10,
            tool_timeout_seconds=30,
            minimum_tool_calls=0,
            maximum_tool_calls=self.dispatcher.maximum_calls,
            failed_tool_call_provider_request_classifier=(
                self.dispatcher.failed_tool_calls_are_provider_request_failures
            ),
        )

    def start(self) -> ContinuousWorldMcpBridge:
        if self._thread is not None:
            raise StateConflictError("continuous world MCP bridge cannot start twice")
        if version("mcp") != WORLD_MCP_SDK_VERSION:
            raise RuntimeError("continuous world MCP SDK version changed")
        import uvicorn
        from mcp.server.auth.provider import AccessToken
        from mcp.server.auth.settings import AuthSettings
        from mcp.server.fastmcp import FastMCP
        from mcp.server.fastmcp.exceptions import ToolError
        from pydantic_core import ValidationError as PydanticValidationError

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(256)
        self._socket = listener
        port = int(listener.getsockname()[1])
        self._url = f"http://127.0.0.1:{port}/mcp"
        expected_token = self._token

        class TokenVerifier:
            async def verify_token(self, candidate: str) -> Any:
                if not hmac.compare_digest(candidate, expected_token):
                    return None
                return AccessToken(
                    token=candidate,
                    client_id="cera-continuous-runtime",
                    scopes=["cera.world.read"],
                )

        dispatcher = self.dispatcher

        class AuditedFastMCP(FastMCP):
            async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
                before = len(dispatcher.calls)
                started = time.perf_counter_ns()
                try:
                    return await super().call_tool(name, arguments)
                except Exception as exc:
                    dispatcher.record_pre_dispatch_failure(
                        name,
                        arguments,
                        framework_argument_validation=(
                            isinstance(exc, ToolError)
                            and isinstance(exc.__cause__, PydanticValidationError)
                        ),
                        expected_call_count=before,
                        duration_ns=time.perf_counter_ns() - started,
                    )
                    raise

        mcp = AuditedFastMCP(
            WORLD_MCP_SERVER_NAME,
            instructions=(
                (
                    COGNITION_NAMED_WORLD_MCP_INSTRUCTIONS
                    if dispatcher.additional_provider_contract_id is not None
                    else NAMED_WORLD_MCP_INSTRUCTIONS
                )
                if "get_turn_context" in dispatcher.tool_names
                else (
                    "Read-only current-branch CERA world lookup. Search/list locate "
                    "records; read returns exact content. Reaching 32 calls is a "
                    "terminal failure."
                )
            ),
            host="127.0.0.1",
            port=port,
            json_response=True,
            stateless_http=True,
            max_request_body_size=65_536,
            token_verifier=TokenVerifier(),
            auth=AuthSettings(
                issuer_url=f"http://127.0.0.1:{port}/",
                resource_server_url=self._url,
                required_scopes=["cera.world.read"],
            ),
        )

        if "cera_world_list" in self.dispatcher.tool_names:

            @mcp.tool(name="cera_world_list", structured_output=True)
            def world_list(prefix: str = "", limit: int = 100) -> dict[str, Any]:
                return self.dispatcher.invoke("cera_world_list", {"prefix": prefix, "limit": limit})

            @mcp.tool(name="cera_world_search", structured_output=True)
            def world_search(
                terms: list[str],
                record_types: list[str] | None = None,
                limit: int = 20,
                knowledge_owner_id: str | None = None,
            ) -> dict[str, Any]:
                return self.dispatcher.invoke(
                    "cera_world_search",
                    {
                        "terms": terms,
                        "record_types": record_types,
                        "limit": limit,
                        "knowledge_owner_id": knowledge_owner_id,
                    },
                )

            @mcp.tool(name="cera_world_read", structured_output=True)
            def world_read(path: str) -> dict[str, Any]:
                return self.dispatcher.invoke("cera_world_read", {"path": path})

        if "get_turn_context" in self.dispatcher.tool_names:

            @mcp.tool(
                name="get_turn_context",
                description=GET_TURN_CONTEXT_TOOL_DESCRIPTION,
                structured_output=True,
            )
            def get_turn_context(
                character_ids: list[str] | None = None,
            ) -> dict[str, Any]:
                return self.dispatcher.invoke("get_turn_context", {"character_ids": character_ids})

            @mcp.tool(
                name="get_character_context",
                description=GET_CHARACTER_CONTEXT_TOOL_DESCRIPTION,
                structured_output=True,
            )
            def get_character_context(character_id: str) -> dict[str, Any]:
                return self.dispatcher.invoke(
                    "get_character_context", {"character_id": character_id}
                )

            @mcp.tool(
                name="search_evidence",
                description=(
                    COGNITION_SEARCH_EVIDENCE_TOOL_DESCRIPTION
                    if dispatcher.additional_provider_contract_id is not None
                    else None
                ),
                structured_output=True,
            )
            def search_evidence(
                terms: list[str],
                character_id: str | None = None,
                limit: int = 20,
            ) -> dict[str, Any]:
                return self.dispatcher.invoke(
                    "search_evidence",
                    {
                        "terms": terms,
                        "character_id": character_id,
                        "limit": limit,
                    },
                )

            @mcp.tool(
                name="get_exact_record",
                description=(
                    COGNITION_GET_EXACT_RECORD_TOOL_DESCRIPTION
                    if dispatcher.additional_provider_contract_id is not None
                    else (
                        "When search_evidence is available for this role, use its "
                        "prior record ID; otherwise use request authority, never invention."
                    )
                ),
                structured_output=True,
            )
            def get_exact_record(record_id: str) -> dict[str, Any]:
                return self.dispatcher.invoke("get_exact_record", {"record_id": record_id})

            @mcp.tool(
                name="get_relationship_context",
                description=GET_RELATIONSHIP_CONTEXT_TOOL_DESCRIPTION,
                structured_output=True,
            )
            def get_relationship_context(character_id: str) -> dict[str, Any]:
                return self.dispatcher.invoke(
                    "get_relationship_context", {"character_id": character_id}
                )

            @mcp.tool(
                name="get_memory_context",
                description=GET_MEMORY_CONTEXT_TOOL_DESCRIPTION,
                structured_output=True,
            )
            def get_memory_context(character_id: str) -> dict[str, Any]:
                return self.dispatcher.invoke("get_memory_context", {"character_id": character_id})

            @mcp.tool(name="get_thread_context", structured_output=True)
            def get_thread_context() -> dict[str, Any]:
                return self.dispatcher.invoke("get_thread_context", {})

            @mcp.tool(name="get_voice_examples", structured_output=True)
            def get_voice_examples(character_id: str) -> dict[str, Any]:
                return self.dispatcher.invoke("get_voice_examples", {"character_id": character_id})

            @mcp.tool(name="get_craft_context", structured_output=True)
            def get_craft_context() -> dict[str, Any]:
                return self.dispatcher.invoke("get_craft_context", {})

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
            except BaseException as exc:
                self._errors.append(exc)

        self._thread = threading.Thread(target=serve, name="cera-continuous-world-mcp", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if server.started:
                return self
            if self._errors or not self._thread.is_alive():
                break
            time.sleep(0.01)
        self.stop(suppress_errors=True)
        raise RuntimeError("continuous world MCP bridge failed to start")

    def stop(self, *, suppress_errors: bool = False) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        alive = self._thread is not None and self._thread.is_alive()
        if self._socket is not None:
            self._socket.close()
        self._url = None
        if not suppress_errors and (alive or self._errors):
            raise RuntimeError("continuous world MCP bridge did not stop cleanly")

    def finalize(self, provider_result: Any) -> dict[str, Any]:
        handler = self.dispatcher.additional_tool_handler
        try:
            expected = tuple(value.tool_name for value in self.dispatcher.calls)
            if provider_result.tool_names != expected:
                raise StateConflictError("continuous world MCP tool sequence changed")
            if any(value != WORLD_MCP_SERVER_NAME for value in provider_result.tool_server_names):
                raise StateConflictError("continuous world MCP server identity changed")
            successful_binding_keys: set[str] = set()
            for call in self.dispatcher.local_debug_calls:
                binding = call.get("evidence_binding")
                if isinstance(binding, dict) and isinstance(binding.get("binding_key"), str):
                    successful_binding_keys.add(str(binding["binding_key"]))
                refs = call.get("evidence_refs", ())
                if isinstance(refs, list | tuple):
                    successful_binding_keys.update(
                        value for value in refs if isinstance(value, str)
                    )
                context_refs = call.get("context_refs", ())
                if isinstance(context_refs, list | tuple):
                    successful_binding_keys.update(
                        value for value in context_refs if isinstance(value, str)
                    )
            finalize_additional = (
                None if handler is None else getattr(handler, "finalize_provider_turn", None)
            )
            self._additional_finalization = (
                None
                if not callable(finalize_additional)
                else finalize_additional(tuple(self.dispatcher.calls))
            )
            self._provider_turn_finalized = True
            return {
                "binding_sha256": self.dispatcher.binding_sha256,
                "tool_call_count": len(self.dispatcher.calls),
                "returned_bytes": sum(value.returned_bytes for value in self.dispatcher.calls),
                "ceiling_reached": (len(self.dispatcher.calls) >= self.dispatcher.maximum_calls),
                "calls": [
                    {
                        "call_index": value.call_index,
                        "tool_name": value.tool_name,
                        "request_sha256": value.request_sha256,
                        "returned_bytes": value.returned_bytes,
                        "duration_ns": value.duration_ns,
                        "success": value.success,
                    }
                    for value in self.dispatcher.calls
                ],
                "local_debug_calls": self.dispatcher.local_debug_calls,
                "evidence_bindings": [
                    _binding_descriptor(value)
                    for value in self.dispatcher.evidence_registry.bindings
                    if value.relative_path is not None
                    and value.binding_key in successful_binding_keys
                ],
            }
        except BaseException:
            self._additional_finalization = None
            self._provider_turn_finalized = True
            abort_additional = (
                None if handler is None else getattr(handler, "abort_provider_turn", None)
            )
            if callable(abort_additional):
                abort_additional()
            raise

    def take_additional_finalization(self) -> object | None:
        """Move an opaque typed handler result without projecting it to debug."""

        if not self._provider_turn_finalized:
            raise StateConflictError("continuous world MCP additional result is not finalized")
        value = self._additional_finalization
        self._additional_finalization = None
        return value

    def abort_provider_turn(self) -> None:
        handler = self.dispatcher.additional_tool_handler
        abort_additional = (
            None if handler is None else getattr(handler, "abort_provider_turn", None)
        )
        if callable(abort_additional):
            abort_additional()

    def __enter__(self) -> ContinuousWorldMcpBridge:
        return self.start()

    def __exit__(
        self,
        exc_type: object | None,
        exc: object | None,
        _traceback: object | None,
    ) -> None:
        self.stop(suppress_errors=exc_type is not None)


def _record_type(relative_path: str) -> str:
    parts = relative_path.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[0] == "DERIVED" and parts[1] == "CharacterSummaries":
        return "character_summaries"
    if len(parts) >= 2 and parts[0] in {"ACTIVE", "DERIVED"}:
        return parts[1].casefold()
    return parts[0].casefold()


def _binding_descriptor(value: RequestEvidenceBindingV1) -> dict[str, Any]:
    return {
        "schema_version": value.schema_version,
        "binding_key": value.binding_key,
        "kind": value.kind.value,
        "world_id": value.world_id,
        "branch_id": value.branch_id,
        "turn_id": value.turn_id,
        "source_identity": value.source_identity,
        "source_sha256": value.source_sha256,
        "protected_user_allowance_scope": value.protected_user_allowance_scope,
        "authority_classification": value.authority_classification.value,
        "relative_path": value.relative_path,
        "record_revision": value.record_revision,
        "record_type": value.record_type,
        "visibility": value.visibility.value,
        "knowledge_owner_id": value.knowledge_owner_id,
        "exact_read_operation_sha256": value.exact_read_operation_sha256,
        "accepted_turn_id": value.accepted_turn_id,
        "acceptance_receipt_sha256": value.acceptance_receipt_sha256,
        "accepted_envelope_sha256": value.accepted_envelope_sha256,
        "provider_thread_sha256": value.provider_thread_sha256,
        "session_snapshot_sha256": value.session_snapshot_sha256,
        "synchronization_receipt_sha256": value.synchronization_receipt_sha256,
    }
