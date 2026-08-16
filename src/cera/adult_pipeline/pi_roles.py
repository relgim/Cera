"""Concrete protected Pi + DeepSeek adapters for Adult Scene and Filter.

The ordinary :class:`~cera.pi_scene.pi_adapter.PiSceneAdapter` deliberately
returns prose only.  Adult Scene and Adult Filter need closed structured role
outputs, so this module reuses its pinned command construction, confined
Writer view, JSON event parser, operation ledger, and completion checks while
supplying role-specific system prompts.  Exact adult inputs and outputs are
written only through protected runtime paths/debug records.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

from cera.errors import ContractValidationError, ErrorCode, StateConflictError
from cera.pi_scene.contracts import SceneRoute
from cera.pi_scene.pi_adapter import (
    MAX_TOOL_CALLS_PER_INVOCATION,
    PiSceneAdapter,
    PiSceneInvocationV1,
    _parse_pi_json_stream,
    _prepare_control_dir,
)
from cera.pi_scene.store import AcceptedPiSessionV1
from cera.pi_scene.writer_view import (
    MaterializedWriterViewV1,
    WriterViewInputV1,
    WriterViewMaterializer,
    verify_writer_view,
)
from cera.provider_dispatch_guard import (
    assert_provider_dispatch_allowed,
    is_external_provider_boundary,
)
from cera.providers.models import (
    ProviderRetryableFailureCategory,
    ProviderTransportError,
)
from cera.schema import from_mapping
from cera.serialization import canonical_json, canonical_sha256, text_sha256

from .acceptance import (
    AdultFilterExecutionBindingV1,
    AdultSceneSessionBindingV1,
    AdultSceneSessionBindingV2,
)
from .contracts import (
    AdultCodexProjectionV2,
    AdultCurrentDataUseV1,
    AdultDecisionStepV1,
    AdultFilterConflictClass,
    AdultFilterConflictV1,
    AdultFilterDecisionV1,
    AdultFilterInvocationV1,
    AdultFilterPassV1,
    AdultFilterRequestV1,
    AdultFilterVerdict,
    AdultNextRoute,
    AdultProjectionEffectV1,
    AdultProjectionEventV1,
    AdultProjectionPresenceChangeV1,
    AdultProtectedEventV1,
    AdultProtectedFullRecordV1,
    AdultProviderReceiptV1,
    AdultProviderReceiptV2,
    AdultProviderRole,
    AdultRouteTransitionV1,
    AdultSceneInvocationV1,
    AdultSceneOutputV1,
    AdultSceneRequestV1,
    AdultSessionScope,
)

ADULT_PI_ROLE_COMPATIBILITY_VERSION = "cera.adult_pipeline.pi_roles.v8"

_SCENE_SYSTEM_PROMPT = """You are CERA's sole DeepSeek Adult Scene logic and prose owner. Call the context tool exactly once. The confined view is the complete current authority. Decide the characters' causal and psychological response, realize the complete visible scene, and select whether the next logic owner remains adult or returns to ordinary Codex. Adult scenes intentionally allow more protected-user realization freedom than ordinary scenes: while the confined authority establishes adult identity, current capacity, current consent, and freedom to stop, you may realize Ted's plausible immediate physical actions, bodily reactions, and limited in-scene dialogue needed for natural flow inside the currently authorized interaction. Do not invent or override consent, withdrawal, a major lasting decision, a memory, or a permanent preference; bodily response never establishes consent or lasting preference, and current consent never authorizes an adjacent act. Adult craft is realization guidance only and never chooses the route. Do not expose files, tools, policies, or analysis. Return exactly one JSON object and no Markdown with keys decision_path, exact_story_prose, resulting_state, unresolved_threads, next_route, next_route_reason. decision_path is a non-empty ordered array of objects with exactly decision_key, character_id, concise_decision, evidence_refs. evidence_refs may cite only current_context evidence_ref values. Cite source:current when a decision relies on exact_current_source or the adult_handoff. unresolved_threads and evidence_refs are arrays of strings. next_route is adult or ordinary. Do not author schemas, hashes, branch or transaction custody, or a logic_owner field."""

_FILTER_SYSTEM_PROMPT = """You are CERA's independent DeepSeek Adult Filter. Call the context tool exactly once. Validate the exact Adult Scene request and exact candidate output in the confined view. Do not regenerate, continue, rewrite, or soften the candidate. Also reject only for a severe reader-facing quality failure: incoherence, clearly wrong character voice or logic, severe repetition, a missing central scene action, premature closure, or materially inadequate realization. Use severe_reader_quality for that narrow floor except when the existing severe_incompleteness class precisely applies; do not reject harmless wording, staging, pacing, or style variation. On pass, extract a protected full record and a non-explicit Codex projection. On reject, return one anchored conflict. Return exactly one JSON object and no Markdown. Pass shape: {\"verdict\":\"pass\",\"protected_record\":{\"events\":[{\"event_key\":string,\"protected_summary\":string,\"character_ids\":[string],\"durable_effects\":[string],\"knowledge_owner_ids\":[string]}],\"current_data_uses\":[{\"evidence_ref\":string,\"decision_key\":string,\"concise_use\":string}],\"resulting_protected_state\":string,\"unresolved_threads\":[string]},\"codex_projection\":{\"events\":[{\"event_key\":string,\"non_explicit_summary\":string,\"lasting_story_meaning\":string}],\"presence_changes\":[{\"character_id\":string,\"direction\":\"enter\"|\"leave\",\"effective_after_event_key\":string}],\"durable_effects\":[{\"effect_key\":string,\"effect_kind\":\"material\"|\"knowledge\"|\"relationship\"|\"character_development\",\"source_event_key\":string,\"subject_ids\":[string],\"non_explicit_effect\":string,\"target_key\":string,\"visibility\":\"public\"|\"character_private\",\"knowledge_owner_id\":string|null}],\"resulting_public_state\":string,\"unresolved_threads\":[string]}}. Reject shape: {\"verdict\":\"reject\",\"conflict\":{\"conflict_class\":string,\"concise_explanation\":string,\"decision_key\":string|null,\"exact_quote\":string|null}}. Event keys must copy the Scene decision keys in order. The projection must remain non-explicit and must not expose adult-role-private current context. Do not author schemas, hashes, exact prose copies, decision-path copies, route transitions, identity, or transaction custody; Python binds those exact values."""
_FILTER_SYSTEM_PROMPT += """ You may combine adjacent Scene decisions into one summary event using the earliest covered decision key. Full-record and projection events must use the same non-empty ordered subset of exact Scene decision keys. current_data_uses may be a valid unique subset of the Scene's decision/evidence pairs. Every knowledge_owner_id must also appear in that protected event's character_ids. A public durable effect requires knowledge_owner_id null; a character_private effect requires one knowledge owner included in subject_ids."""
_FILTER_SYSTEM_PROMPT += """ Do not reason aloud or place analysis before the JSON object. Python already preserves and binds the exact story prose, so never copy, retell, paraphrase at length, or reconstruct that prose in protected_summary, projection summaries, states, effects, or unresolved threads. Record only the smallest faithful set of durable facts, changes, knowledge, and unresolved consequences needed for protected continuity and the non-explicit projection. Prefer one combined event when adjacent decisions have the same durable consequence; do not create bookkeeping entries for transient staging, wording, or details with no lasting state effect."""

_SCENE_PROMPT = "Load the exact confined authority and return the Adult Scene JSON now."
_FILTER_PROMPT = "Load the exact protected candidate and return the Adult Filter JSON now."


@dataclass(frozen=True, slots=True)
class AdultRoleViewContextV1:
    """Branch-scoped material already authorized for the protected Writer view."""

    world_id: str
    branch_id: str
    scene_id: str
    turn_id: str
    candidate_id: str
    current_state: Mapping[str, Any]
    characters: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    relationships: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    recent_prose: Sequence[Mapping[str, Any] | str] = ()
    relevant_memories: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    voice_examples: Mapping[str, Mapping[str, Any] | str] = field(default_factory=dict)
    accepted_records: Sequence[Mapping[str, Any]] = ()

    def __post_init__(self) -> None:
        for name in ("world_id", "branch_id", "scene_id", "turn_id", "candidate_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ContractValidationError(f"adult role view {name} is empty")
        if not self.current_state:
            raise ContractValidationError("adult role view requires current state")


@dataclass(frozen=True, slots=True)
class StructuredAdultRoleResultV1:
    raw_json: str
    session_id: str
    session_dir: Path
    session_id_sha256: str
    provider: str
    model: str
    provider_operations: int
    finish_status: str
    request_binding_sha256: str
    invocation_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.invocation_id, str) or not self.invocation_id.strip():
            raise ContractValidationError("adult structured role invocation ID is empty")


@dataclass(frozen=True, slots=True)
class AdultSceneRoleExecutionV1:
    """Protected Scene result plus the exact Pi invocation ledger locator."""

    invocation: AdultSceneInvocationV1
    session_binding: AdultSceneSessionBindingV1 | AdultSceneSessionBindingV2
    provider_invocation_id: str

    def __post_init__(self) -> None:
        if self.session_binding.provider_receipt_sha256 != canonical_sha256(
            self.invocation.receipt
        ):
            raise ContractValidationError("adult Scene role execution changed its receipt")
        if not self.provider_invocation_id.strip():
            raise ContractValidationError("adult Scene role execution lost its invocation ID")


@dataclass(frozen=True, slots=True)
class AdultFilterRoleExecutionV1:
    """Protected Filter result plus the exact Pi invocation ledger locator."""

    invocation: AdultFilterInvocationV1
    execution_binding: AdultFilterExecutionBindingV1
    provider_invocation_id: str

    def __post_init__(self) -> None:
        if self.execution_binding.provider_receipt_sha256 != canonical_sha256(
            self.invocation.receipt
        ):
            raise ContractValidationError("adult Filter role execution changed its receipt")
        if not self.provider_invocation_id.strip():
            raise ContractValidationError("adult Filter role execution lost its invocation ID")


class StructuredAdultRoleTransport(Protocol):
    @property
    def external_provider_boundary(self) -> bool: ...

    def invoke_structured_role(
        self,
        *,
        role: AdultProviderRole,
        view: MaterializedWriterViewV1,
        candidate_id: str,
        session_dir: Path,
        system_prompt: str,
        prompt: str,
        accepted_parent_session: AcceptedPiSessionV1 | None,
    ) -> StructuredAdultRoleResultV1: ...


class WriterViewMaterializationPort(Protocol):
    def materialize(self, source: WriterViewInputV1) -> MaterializedWriterViewV1: ...


def _adult_provider_failure(
    category: ProviderRetryableFailureCategory,
    *,
    diagnostic: str,
    provider_calls_observed: int,
) -> ProviderTransportError:
    """Create one closed Adult Pi boundary failure without inspecting messages."""

    return ProviderTransportError(
        ErrorCode.COMPOSER_UNAVAILABLE,
        "Pi adult role did not return an accepted provider envelope",
        safe_diagnostics=(diagnostic,),
        external_provider_calls_observed=provider_calls_observed,
        retryable_failure_category=category,
    )


def _parse_adult_pi_stream(stdout: str) -> Any:
    """Separate incomplete streams from deterministic invalid JSON envelopes."""

    if not isinstance(stdout, str):
        raise _adult_provider_failure(
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
            diagnostic="provider_envelope:non_text_stream",
            provider_calls_observed=1,
        )
    saw_session = False
    saw_provider_completion = False
    for raw_line in stdout.splitlines():
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            raise _adult_provider_failure(
                ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
                diagnostic="provider_envelope:non_json_event",
                provider_calls_observed=1,
            ) from None
        if not isinstance(event, Mapping):
            raise _adult_provider_failure(
                ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
                diagnostic="provider_envelope:non_object_event",
                provider_calls_observed=1,
            )
        if event.get("type") == "session":
            session_id = event.get("id")
            if not isinstance(session_id, str) or not session_id.strip():
                raise _adult_provider_failure(
                    ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
                    diagnostic="provider_envelope:invalid_session_header",
                    provider_calls_observed=1,
                )
            saw_session = True
        elif event.get("type") == "message_end":
            message = event.get("message")
            if (
                isinstance(message, Mapping)
                and message.get("role") == "assistant"
                and isinstance(message.get("usage"), Mapping)
            ):
                saw_provider_completion = True
    if not saw_session or not saw_provider_completion:
        raise _adult_provider_failure(
            ProviderRetryableFailureCategory.PROVIDER_STREAM_INCOMPLETE,
            diagnostic="provider_stream:missing_terminal_evidence",
            provider_calls_observed=1,
        )
    try:
        return _parse_pi_json_stream(stdout)
    except (ContractValidationError, StateConflictError) as exc:
        raise _adult_provider_failure(
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
            diagnostic=f"provider_envelope:{type(exc).__name__}",
            provider_calls_observed=1,
        ) from None


def _validate_adult_pi_protocol(parsed: Any) -> None:
    """Reject an invalid context-tool protocol independently of completion status."""

    if (
        parsed.tool_call_count != MAX_TOOL_CALLS_PER_INVOCATION
        or parsed.failed_tool_call_count
        or parsed.completed_context_tool_calls != MAX_TOOL_CALLS_PER_INVOCATION
        or parsed.tool_protocol_error_count
    ):
        raise _adult_provider_failure(
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
            diagnostic="provider_protocol:context_contract_invalid",
            provider_calls_observed=1,
        )


def _validate_adult_pi_completion(parsed: Any) -> None:
    """Accept a complete object at a limit; reject other non-terminal completions."""

    finish_status = parsed.finish_status.casefold()
    if finish_status not in {"stop", "length", "max_tokens", "token_limit"}:
        raise _adult_provider_failure(
            ProviderRetryableFailureCategory.PROVIDER_COMPLETION_INCOMPLETE,
            diagnostic="provider_completion:non_stop",
            provider_calls_observed=1,
        )


def _adult_invalid_json_category(parsed: Any) -> ProviderRetryableFailureCategory:
    """A limit-ended invalid object is incomplete; a stopped invalid object is invalid."""

    if parsed.finish_status.casefold() in {"length", "max_tokens", "token_limit"}:
        return ProviderRetryableFailureCategory.PROVIDER_COMPLETION_INCOMPLETE
    return ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID


class LazyProtectedWriterViewMaterializer:
    """Avoid filesystem mutation until the role dispatch guard has passed."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def materialize(self, source: WriterViewInputV1) -> MaterializedWriterViewV1:
        return WriterViewMaterializer(self.root).materialize(source)


class PiStructuredAdultRoleTransport:
    """Pinned Pi JSON-mode runner with exact per-operation accounting."""

    def __init__(self, adapter: PiSceneAdapter) -> None:
        self.adapter = adapter

    @property
    def external_provider_boundary(self) -> bool:
        return is_external_provider_boundary(self.adapter)

    def invoke_structured_role(
        self,
        *,
        role: AdultProviderRole,
        view: MaterializedWriterViewV1,
        candidate_id: str,
        session_dir: Path,
        system_prompt: str,
        prompt: str,
        accepted_parent_session: AcceptedPiSessionV1 | None,
    ) -> StructuredAdultRoleResultV1:
        assert_provider_dispatch_allowed(
            f"adult_pipeline.pi.{role.value}",
            external_provider_boundary=self.external_provider_boundary,
        )
        verified = verify_writer_view(view.root)
        if verified.manifest_sha256 != view.manifest_sha256 or verified.purpose != "writer":
            raise StateConflictError("adult Pi role Writer-view binding changed")
        if role is AdultProviderRole.FILTER and accepted_parent_session is not None:
            raise ContractValidationError("adult Filter cannot inherit a parent session")
        session_dir = session_dir.resolve()
        session_dir.mkdir(parents=True, exist_ok=True)
        invocation = PiSceneInvocationV1(
            route=SceneRoute.ADULT,
            purpose="writer",
            view=verified,
            prompt=prompt,
            candidate_id=candidate_id,
            session_dir=session_dir,
            accepted_parent_session=accepted_parent_session,
            # Adult Scene history is complete in the Python-materialized view.
            # The accepted parent remains provenance only and is never forked.
            force_rehydrate=(
                role is AdultProviderRole.SCENE and accepted_parent_session is not None
            ),
        )
        command = self.adapter.command_for(invocation, system_prompt=system_prompt)
        environment = dict(os.environ)
        environment.update(
            {
                "CERA_PI_VIEW_ROOT": str(verified.root),
                "PI_TELEMETRY": "0",
                "CERA_PI_MAX_TOOL_CALLS": str(MAX_TOOL_CALLS_PER_INVOCATION),
                "CERA_PI_PURPOSE": "writer",
            }
        )
        request_binding = canonical_sha256(
            {
                "compatibility_version": ADULT_PI_ROLE_COMPATIBILITY_VERSION,
                "role": role.value,
                "view_manifest_sha256": verified.manifest_sha256,
                "prompt": prompt,
                "system_prompt": system_prompt,
                "model": self.adapter.model,
                "thinking": "off",
                "tools": ["context"],
                "session_mode": (
                    "python_state_rehydrate"
                    if role is AdultProviderRole.SCENE
                    else "candidate_isolated"
                ),
                "parent_session_id_sha256": (
                    None
                    if accepted_parent_session is None
                    else accepted_parent_session.session_id_sha256
                ),
            }
        )
        invocation_id = self.adapter.operation_ledger.begin(
            candidate_id=candidate_id,
            purpose=role.value,
            route=SceneRoute.ADULT.value,
            request_sha256=request_binding,
        )
        started = time.perf_counter()
        try:
            process = self.adapter._process_runner(  # noqa: SLF001 - pinned compatibility seam
                command,
                _prepare_control_dir(session_dir),
                environment,
                self.adapter.timeout_seconds,
                lambda line: self.adapter.operation_ledger.observe_line(invocation_id, line),
            )
        except ProviderTransportError as exc:
            duration_ms = max(0, round((time.perf_counter() - started) * 1000))
            self.adapter.operation_ledger.finish(
                invocation_id,
                status="failed",
                failure_type=type(exc).__name__,
                duration_ms=duration_ms,
                failure_category=(
                    None
                    if exc.retryable_failure_category is None
                    else exc.retryable_failure_category.value
                ),
            )
            raise
        except OSError as exc:
            duration_ms = max(0, round((time.perf_counter() - started) * 1000))
            self.adapter.operation_ledger.finish(
                invocation_id,
                status="failed",
                failure_type=type(exc).__name__,
                duration_ms=duration_ms,
                failure_category=(ProviderRetryableFailureCategory.PROVIDER_PROCESS_FAILED.value),
            )
            raise _adult_provider_failure(
                ProviderRetryableFailureCategory.PROVIDER_PROCESS_FAILED,
                diagnostic=f"process_start:{type(exc).__name__}",
                provider_calls_observed=0,
            ) from None
        except BaseException as exc:
            duration_ms = max(0, round((time.perf_counter() - started) * 1000))
            self.adapter.operation_ledger.finish(
                invocation_id,
                status="failed",
                failure_type=type(exc).__name__,
                duration_ms=duration_ms,
            )
            raise
        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        if process.returncode != 0:
            self.adapter.operation_ledger.finish(
                invocation_id,
                status="failed",
                failure_type=f"process_exit_{process.returncode}",
                duration_ms=duration_ms,
                failure_category=(ProviderRetryableFailureCategory.PROVIDER_PROCESS_FAILED.value),
            )
            raise _adult_provider_failure(
                ProviderRetryableFailureCategory.PROVIDER_PROCESS_FAILED,
                diagnostic=(
                    f"process_exit:{process.returncode}:stderr_sha256:{text_sha256(process.stderr)}"
                ),
                provider_calls_observed=1,
            )
        try:
            parsed = _parse_adult_pi_stream(process.stdout)
            _validate_adult_pi_protocol(parsed)
            self.adapter.operation_ledger.assert_completed(
                invocation_id,
                parsed_operations=parsed.provider_operations,
            )
            raw_json = _normalize_provider_json_object(parsed.output_text)
            if not raw_json:
                raise _adult_provider_failure(
                    _adult_invalid_json_category(parsed),
                    diagnostic="provider_envelope:empty_output",
                    provider_calls_observed=1,
                )
            try:
                _json_object(raw_json, f"{role.value} output")
            except (ContractValidationError, StateConflictError) as exc:
                raise _adult_provider_failure(
                    _adult_invalid_json_category(parsed),
                    diagnostic=f"provider_envelope:{type(exc).__name__}",
                    provider_calls_observed=1,
                ) from None
            _validate_adult_pi_completion(parsed)
        except BaseException as exc:
            self.adapter.operation_ledger.finish(
                invocation_id,
                status="failed",
                failure_type=type(exc).__name__,
                duration_ms=duration_ms,
                failure_category=(
                    exc.retryable_failure_category.value
                    if isinstance(exc, ProviderTransportError)
                    and exc.retryable_failure_category is not None
                    else None
                ),
            )
            raise
        self.adapter.operation_ledger.finish(
            invocation_id,
            status="completed",
            output_sha256=text_sha256(raw_json),
            duration_ms=duration_ms,
        )
        if self.adapter.readable_debug is not None and self.adapter.readable_debug.enabled:
            self.adapter.readable_debug.write(
                stage=f"deepseek-{role.value}",
                identity=candidate_id,
                protected=True,
                sections={
                    "Role": role.value,
                    "DeepSeek system prompt": system_prompt,
                    "Pi invocation prompt": prompt,
                    "Complete confined protected Writer view": self.adapter.readable_debug.writer_view(
                        verified.root
                    ),
                    "DeepSeek structured output": raw_json,
                    "Provider operation count": parsed.provider_operations,
                    "Duration ms": duration_ms,
                },
            )
        return StructuredAdultRoleResultV1(
            raw_json=raw_json,
            session_id=parsed.session_id,
            session_dir=session_dir,
            session_id_sha256=text_sha256(parsed.session_id),
            provider=self.adapter.provider,
            model=self.adapter.model,
            provider_operations=parsed.provider_operations,
            finish_status=parsed.finish_status,
            request_binding_sha256=request_binding,
            invocation_id=invocation_id,
        )


class PiDeepSeekAdultScenePort:
    """Sole adult logic/prose role over a retained accepted-branch session."""

    def __init__(
        self,
        *,
        transport: StructuredAdultRoleTransport,
        materializer: WriterViewMaterializationPort,
        context: AdultRoleViewContextV1,
        session_root: Path,
        accepted_parent_session: AcceptedPiSessionV1 | None = None,
    ) -> None:
        self.transport = transport
        self.materializer = materializer
        self.context = context
        self.session_root = session_root.resolve()
        self.accepted_parent_session = accepted_parent_session

    @property
    def external_provider_boundary(self) -> bool:
        return is_external_provider_boundary(self.transport)

    def generate_adult_scene(self, request: AdultSceneRequestV1) -> AdultSceneInvocationV1:
        return self.execute_adult_scene(request).invocation

    def prepare_adult_scene(self, request: AdultSceneRequestV1) -> MaterializedWriterViewV1:
        assert_provider_dispatch_allowed(
            "adult_pipeline.scene.pi",
            external_provider_boundary=self.external_provider_boundary,
        )
        view = self.materializer.materialize(
            _view_input(
                self.context,
                role=AdultProviderRole.SCENE,
                primary=request,
                exact_source=request.exact_current_source,
                craft_index={
                    "adult_craft_selection": _craft_provider_projection(request.retrieved_craft)
                },
            )
        )
        _assert_primary_binding(view, request, request.exact_current_source)
        return view

    def execute_adult_scene(self, request: AdultSceneRequestV1) -> AdultSceneRoleExecutionV1:
        view = self.prepare_adult_scene(request)
        result = self.transport.invoke_structured_role(
            role=AdultProviderRole.SCENE,
            view=view,
            candidate_id=self.context.candidate_id,
            session_dir=self.session_root / "adult-scene",
            system_prompt=_SCENE_SYSTEM_PROMPT,
            prompt=_SCENE_PROMPT,
            accepted_parent_session=self.accepted_parent_session,
        )
        try:
            output = _normalize_scene_evidence_refs(
                _decode_scene_output(result.raw_json),
                request,
            )
        except (ContractValidationError, StateConflictError) as exc:
            raise _adult_provider_failure(
                ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
                diagnostic=f"provider_scene_output:{type(exc).__name__}",
                provider_calls_observed=1,
            ) from None
        receipt = _receipt(
            result,
            role=AdultProviderRole.SCENE,
            request_sha256=canonical_sha256(request),
            output_sha256=canonical_sha256(output),
            terminalized=False,
            accepted_parent_session=self.accepted_parent_session,
        )
        parent_session_id_sha256 = (
            None
            if self.accepted_parent_session is None
            else self.accepted_parent_session.session_id_sha256
        )
        binding = AdultSceneSessionBindingV2(
            schema_version=AdultSceneSessionBindingV2.SCHEMA_VERSION,
            scene_request_sha256=canonical_sha256(request),
            writer_view_manifest_sha256=view.manifest_sha256,
            provider_receipt_sha256=canonical_sha256(receipt),
            transport_request_binding_sha256=result.request_binding_sha256,
            session_id=result.session_id,
            session_id_sha256=result.session_id_sha256,
            session_path=str(result.session_dir),
            parent_session_id_sha256=parent_session_id_sha256,
            rehydrated=True,
        )
        _write_durable_binding(
            self.session_root / "ADULT_SCENE_SESSION_BINDING.json",
            binding,
        )
        invocation = AdultSceneInvocationV1(
            output=output,
            receipt=receipt,
        )
        return AdultSceneRoleExecutionV1(
            invocation=invocation,
            session_binding=binding,
            provider_invocation_id=result.invocation_id,
        )

    def accepted_session_candidate(self, *, accepted_turn_id: str) -> AcceptedPiSessionV1:
        binding = self.scene_session_binding()
        return AcceptedPiSessionV1(
            accepted_turn_id=accepted_turn_id,
            session_id=binding.session_id,
            session_path=binding.session_path,
            session_id_sha256=binding.session_id_sha256,
        )

    def scene_session_binding(
        self,
    ) -> AdultSceneSessionBindingV1 | AdultSceneSessionBindingV2:
        return _load_scene_session_binding(self.session_root / "ADULT_SCENE_SESSION_BINDING.json")


class PiDeepSeekAdultFilterPort:
    """Fresh candidate-isolated Filter with protected exact input."""

    def __init__(
        self,
        *,
        transport: StructuredAdultRoleTransport,
        materializer: WriterViewMaterializationPort,
        context: AdultRoleViewContextV1,
        session_root: Path,
    ) -> None:
        self.transport = transport
        self.materializer = materializer
        self.context = context
        self.session_root = session_root.resolve()

    @property
    def external_provider_boundary(self) -> bool:
        return is_external_provider_boundary(self.transport)

    def validate_and_stage(self, request: AdultFilterRequestV1) -> AdultFilterInvocationV1:
        return self.execute_adult_filter(request).invocation

    def prepare_adult_filter(self, request: AdultFilterRequestV1) -> MaterializedWriterViewV1:
        assert_provider_dispatch_allowed(
            "adult_pipeline.filter.pi",
            external_provider_boundary=self.external_provider_boundary,
        )
        provider_input = _filter_provider_projection(request)
        view = self.materializer.materialize(
            _view_input(
                self.context,
                role=AdultProviderRole.FILTER,
                primary=provider_input,
                exact_source=request.scene_output.exact_story_prose,
                craft_index={
                    "adult_craft_selection": _craft_provider_projection(
                        request.scene_request.retrieved_craft
                    )
                },
            )
        )
        _assert_primary_binding(view, provider_input, request.scene_output.exact_story_prose)
        return view

    def execute_adult_filter(self, request: AdultFilterRequestV1) -> AdultFilterRoleExecutionV1:
        view = self.prepare_adult_filter(request)
        filter_candidate_id = f"{self.context.candidate_id}:adult-filter"
        result = self.transport.invoke_structured_role(
            role=AdultProviderRole.FILTER,
            view=view,
            candidate_id=filter_candidate_id,
            session_dir=self.session_root / "adult-filter",
            system_prompt=_FILTER_SYSTEM_PROMPT,
            prompt=_FILTER_PROMPT,
            accepted_parent_session=None,
        )
        try:
            decision = _decode_filter_decision(result.raw_json, request)
        except (ContractValidationError, StateConflictError) as exc:
            raise _adult_provider_failure(
                ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
                diagnostic=f"provider_filter_output:{type(exc).__name__}",
                provider_calls_observed=1,
            ) from None
        receipt = _receipt(
            result,
            role=AdultProviderRole.FILTER,
            request_sha256=canonical_sha256(request),
            output_sha256=canonical_sha256(decision),
            terminalized=True,
        )
        binding = AdultFilterExecutionBindingV1(
            schema_version=AdultFilterExecutionBindingV1.SCHEMA_VERSION,
            filter_request_sha256=canonical_sha256(request),
            writer_view_manifest_sha256=view.manifest_sha256,
            provider_receipt_sha256=canonical_sha256(receipt),
            transport_request_binding_sha256=result.request_binding_sha256,
            session_id_sha256=result.session_id_sha256,
            session_path=str(result.session_dir),
            session_terminalized=True,
        )
        _write_durable_binding(
            self.session_root / "ADULT_FILTER_EXECUTION_BINDING.json",
            binding,
        )
        invocation = AdultFilterInvocationV1(
            decision=decision,
            receipt=receipt,
        )
        return AdultFilterRoleExecutionV1(
            invocation=invocation,
            execution_binding=binding,
            provider_invocation_id=result.invocation_id,
        )

    def filter_execution_binding(self) -> AdultFilterExecutionBindingV1:
        return _load_durable_binding(
            self.session_root / "ADULT_FILTER_EXECUTION_BINDING.json",
            AdultFilterExecutionBindingV1,
        )


def _view_input(
    context: AdultRoleViewContextV1,
    *,
    role: AdultProviderRole,
    primary: object,
    exact_source: str,
    craft_index: Mapping[str, Any],
) -> WriterViewInputV1:
    candidate_id = (
        context.candidate_id
        if role is AdultProviderRole.SCENE
        else f"{context.candidate_id}:adult-filter"
    )
    return WriterViewInputV1(
        world_id=context.world_id,
        branch_id=context.branch_id,
        scene_id=context.scene_id,
        turn_id=context.turn_id,
        candidate_id=candidate_id,
        route=SceneRoute.ADULT,
        user_prompt=exact_source,
        primary_authority=cast(Mapping[str, Any], _primitive(primary)),
        current_state=context.current_state,
        characters=context.characters,
        relationships=context.relationships,
        recent_prose=context.recent_prose,
        relevant_memories=context.relevant_memories,
        voice_examples=context.voice_examples,
        craft_index=craft_index,
        accepted_records=context.accepted_records,
        purpose="writer",
    )


def _assert_primary_binding(view: MaterializedWriterViewV1, primary: object, source: str) -> None:
    verified = verify_writer_view(view.root)
    try:
        stored = json.loads((verified.root / "ADULT_HANDOFF.json").read_text(encoding="utf-8"))
        stored_source = (verified.root / "USER_PROMPT.txt").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateConflictError("protected adult role view is unreadable") from exc
    if stored != _primitive(primary) or stored_source != source:
        raise StateConflictError("protected adult role view lost exact input custody")


def _receipt(
    result: StructuredAdultRoleResultV1,
    *,
    role: AdultProviderRole,
    request_sha256: str,
    output_sha256: str,
    terminalized: bool,
    accepted_parent_session: AcceptedPiSessionV1 | None = None,
) -> AdultProviderReceiptV1 | AdultProviderReceiptV2:
    if role is AdultProviderRole.SCENE:
        return AdultProviderReceiptV2(
            schema_version=AdultProviderReceiptV2.SCHEMA_VERSION,
            role=role,
            session_scope=AdultSessionScope.ACCEPTED_BRANCH,
            provider=result.provider,
            model=result.model,
            session_id_sha256=result.session_id_sha256,
            request_sha256=request_sha256,
            output_sha256=output_sha256,
            provider_operations=result.provider_operations,
            finish_status=result.finish_status,
            session_terminalized=terminalized,
            parent_session_id_sha256=(
                None
                if accepted_parent_session is None
                else accepted_parent_session.session_id_sha256
            ),
            rehydrated=True,
        )
    return AdultProviderReceiptV1(
        schema_version=AdultProviderReceiptV1.SCHEMA_VERSION,
        role=role,
        session_scope=AdultSessionScope.CANDIDATE,
        provider=result.provider,
        model=result.model,
        session_id_sha256=result.session_id_sha256,
        request_sha256=request_sha256,
        output_sha256=output_sha256,
        provider_operations=result.provider_operations,
        finish_status=result.finish_status,
        session_terminalized=terminalized,
    )


def _load_scene_session_binding(
    path: Path,
) -> AdultSceneSessionBindingV1 | AdultSceneSessionBindingV2:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateConflictError("adult role execution binding is unavailable") from exc
    if not isinstance(payload, dict):
        raise StateConflictError("adult role execution binding is invalid")
    schema_version = payload.get("schema_version")
    if schema_version == AdultSceneSessionBindingV2.SCHEMA_VERSION:
        return cast(
            AdultSceneSessionBindingV2,
            from_mapping(AdultSceneSessionBindingV2, payload),
        )
    if schema_version == AdultSceneSessionBindingV1.SCHEMA_VERSION:
        return cast(
            AdultSceneSessionBindingV1,
            from_mapping(AdultSceneSessionBindingV1, payload),
        )
    raise StateConflictError("adult Scene session binding schema is unsupported")


def _decode_scene_output(raw: str) -> AdultSceneOutputV1:
    value = _json_object(raw, "adult Scene output")
    _keys(
        value,
        {
            "decision_path",
            "exact_story_prose",
            "resulting_state",
            "unresolved_threads",
            "next_route",
            "next_route_reason",
        },
        "adult Scene output",
    )
    steps = tuple(
        AdultDecisionStepV1(
            decision_key=_string(item, "decision_key"),
            character_id=_string(item, "character_id"),
            concise_decision=_string(item, "concise_decision"),
            evidence_refs=_strings(item, "evidence_refs"),
        )
        for item in _objects(value, "decision_path", required=True)
    )
    return AdultSceneOutputV1(
        schema_version=AdultSceneOutputV1.SCHEMA_VERSION,
        logic_owner="deepseek_adult_scene",
        decision_path=_normalize_scene_decision_keys(steps),
        exact_story_prose=_string(value, "exact_story_prose"),
        resulting_state=_string(value, "resulting_state"),
        unresolved_threads=_strings(value, "unresolved_threads"),
        next_route=AdultNextRoute(_string(value, "next_route")),
        next_route_reason=_string(value, "next_route_reason"),
    )


def _normalize_scene_decision_keys(
    steps: tuple[AdultDecisionStepV1, ...],
) -> tuple[AdultDecisionStepV1, ...]:
    """Make repeated provider-local keys unique without changing decision content."""

    reserved = {step.decision_key for step in steps}
    used: set[str] = set()
    normalized: list[AdultDecisionStepV1] = []
    for step in steps:
        key = step.decision_key
        if key in used:
            ordinal = 2
            while True:
                suffix = f"_{ordinal}"
                candidate = f"{key[: 96 - len(suffix)]}{suffix}"
                if candidate not in reserved and candidate not in used:
                    key = candidate
                    break
                ordinal += 1
            step = replace(step, decision_key=key)
        used.add(key)
        normalized.append(step)
    return tuple(normalized)


def _normalize_scene_evidence_refs(
    output: AdultSceneOutputV1,
    request: AdultSceneRequestV1,
) -> AdultSceneOutputV1:
    """Repair reference bookkeeping without changing DeepSeek's scene or decisions."""

    allowed = {value.evidence_ref for value in request.current_context}
    source_ref = "source:current"
    normalized: list[AdultDecisionStepV1] = []
    for step in output.decision_path:
        retained = tuple(ref for ref in step.evidence_refs if ref in allowed)
        if not retained and source_ref in allowed:
            retained = (source_ref,)
        normalized.append(replace(step, evidence_refs=retained))
    return replace(output, decision_path=tuple(normalized))


def _decode_filter_decision(
    raw: str,
    request: AdultFilterRequestV1,
) -> AdultFilterDecisionV1:
    value = _json_object(raw, "adult Filter output")
    verdict = _string(value, "verdict")
    if verdict == AdultFilterVerdict.REJECT.value:
        _keys(value, {"verdict", "conflict"}, "adult Filter rejection")
        conflict = _object(value, "conflict")
        _keys(
            conflict,
            {"conflict_class", "concise_explanation", "decision_key", "exact_quote"},
            "adult Filter conflict",
        )
        return AdultFilterDecisionV1(
            schema_version=AdultFilterDecisionV1.SCHEMA_VERSION,
            verdict=AdultFilterVerdict.REJECT,
            passed=None,
            conflict=AdultFilterConflictV1(
                conflict_class=AdultFilterConflictClass(_string(conflict, "conflict_class")),
                concise_explanation=_string(conflict, "concise_explanation"),
                decision_key=_nullable_string(conflict, "decision_key"),
                exact_quote=_nullable_string(conflict, "exact_quote"),
            ),
        )
    if verdict != AdultFilterVerdict.PASS.value:
        raise ContractValidationError("adult Filter verdict is invalid")
    _keys(value, {"verdict", "protected_record", "codex_projection"}, "adult Filter pass")
    scene = request.scene_output
    protected = _object(value, "protected_record")
    _keys(
        protected,
        {
            "events",
            "current_data_uses",
            "resulting_protected_state",
            "unresolved_threads",
        },
        "adult Filter protected record",
    )
    events = tuple(_decode_protected_event(item) for item in _objects(protected, "events"))
    uses = _normalize_filter_current_data_uses(
        tuple(_decode_data_use(item) for item in _objects(protected, "current_data_uses")),
        scene,
    )
    full = AdultProtectedFullRecordV1(
        schema_version=AdultProtectedFullRecordV1.SCHEMA_VERSION,
        scene_output_sha256=canonical_sha256(scene),
        exact_story_prose=scene.exact_story_prose,
        exact_story_prose_sha256=text_sha256(scene.exact_story_prose),
        decision_path=scene.decision_path,
        events=events,
        current_data_uses=uses,
        resulting_protected_state=_string(protected, "resulting_protected_state"),
        unresolved_threads=_strings(protected, "unresolved_threads"),
    )
    projected = _object(value, "codex_projection")
    _keys(
        projected,
        {
            "events",
            "presence_changes",
            "durable_effects",
            "resulting_public_state",
            "unresolved_threads",
        },
        "adult Filter Codex projection",
    )
    projection = AdultCodexProjectionV2(
        schema_version=AdultCodexProjectionV2.SCHEMA_VERSION,
        protected_full_record_sha256=canonical_sha256(full),
        events=tuple(_decode_projection_event(item) for item in _objects(projected, "events")),
        presence_changes=tuple(
            _decode_presence_change(item) for item in _objects(projected, "presence_changes")
        ),
        durable_effects=tuple(
            _decode_projection_effect(item) for item in _objects(projected, "durable_effects")
        ),
        resulting_public_state=_string(projected, "resulting_public_state"),
        unresolved_threads=_strings(projected, "unresolved_threads"),
    )
    transition = AdultRouteTransitionV1(
        schema_version=AdultRouteTransitionV1.SCHEMA_VERSION,
        current_route=AdultNextRoute.ADULT,
        next_route=scene.next_route,
        return_to_codex=scene.next_route is AdultNextRoute.ORDINARY,
        concise_reason=scene.next_route_reason,
    )
    return AdultFilterDecisionV1(
        schema_version=AdultFilterDecisionV1.SCHEMA_VERSION,
        verdict=AdultFilterVerdict.PASS,
        passed=AdultFilterPassV1(
            protected_full_record=full,
            codex_projection=projection,
            route_transition=transition,
        ),
        conflict=None,
    )


def _normalize_filter_current_data_uses(
    uses: tuple[AdultCurrentDataUseV1, ...],
    scene: AdultSceneOutputV1,
) -> tuple[AdultCurrentDataUseV1, ...]:
    """Retain only unambiguous Filter bookkeeping pairs present in the Scene."""

    available = {
        (step.decision_key, evidence_ref)
        for step in scene.decision_path
        for evidence_ref in step.evidence_refs
    }
    retained: list[AdultCurrentDataUseV1] = []
    seen: set[tuple[str, str]] = set()
    for use in uses:
        pair = (use.decision_key, use.evidence_ref)
        if pair not in available or pair in seen:
            continue
        seen.add(pair)
        retained.append(use)
    return tuple(retained)


def _decode_protected_event(value: Mapping[str, Any]) -> AdultProtectedEventV1:
    _keys(
        value,
        {
            "event_key",
            "protected_summary",
            "character_ids",
            "durable_effects",
            "knowledge_owner_ids",
        },
        "adult protected event",
    )
    character_ids = _strings(value, "character_ids")
    knowledge_owner_ids = _strings(value, "knowledge_owner_ids")
    character_ids = tuple(dict.fromkeys((*character_ids, *knowledge_owner_ids)))
    return AdultProtectedEventV1(
        event_key=_string(value, "event_key"),
        protected_summary=_string(value, "protected_summary"),
        character_ids=character_ids,
        durable_effects=_strings(value, "durable_effects"),
        knowledge_owner_ids=knowledge_owner_ids,
    )


def _decode_data_use(value: Mapping[str, Any]) -> AdultCurrentDataUseV1:
    _keys(value, {"evidence_ref", "decision_key", "concise_use"}, "adult current-data use")
    return AdultCurrentDataUseV1(
        evidence_ref=_string(value, "evidence_ref"),
        decision_key=_string(value, "decision_key"),
        concise_use=_string(value, "concise_use"),
    )


def _decode_projection_event(value: Mapping[str, Any]) -> AdultProjectionEventV1:
    _keys(
        value,
        {"event_key", "non_explicit_summary", "lasting_story_meaning"},
        "adult projection event",
    )
    return AdultProjectionEventV1(
        event_key=_string(value, "event_key"),
        non_explicit_summary=_string(value, "non_explicit_summary"),
        lasting_story_meaning=_string(value, "lasting_story_meaning"),
    )


def _decode_presence_change(value: Mapping[str, Any]) -> AdultProjectionPresenceChangeV1:
    _keys(
        value,
        {"character_id", "direction", "effective_after_event_key"},
        "adult projection presence change",
    )
    return AdultProjectionPresenceChangeV1(
        character_id=_string(value, "character_id"),
        direction=_string(value, "direction"),
        effective_after_event_key=_string(value, "effective_after_event_key"),
    )


def _decode_projection_effect(value: Mapping[str, Any]) -> AdultProjectionEffectV1:
    _keys(
        value,
        {
            "effect_key",
            "effect_kind",
            "source_event_key",
            "subject_ids",
            "non_explicit_effect",
            "target_key",
            "visibility",
            "knowledge_owner_id",
        },
        "adult projection effect",
    )
    visibility = _string(value, "visibility")
    knowledge_owner_id = _nullable_string(value, "knowledge_owner_id")
    if visibility == "public":
        knowledge_owner_id = None
    return AdultProjectionEffectV1(
        effect_key=_string(value, "effect_key"),
        effect_kind=_string(value, "effect_kind"),
        source_event_key=_string(value, "source_event_key"),
        subject_ids=_strings(value, "subject_ids"),
        non_explicit_effect=_string(value, "non_explicit_effect"),
        target_key=_string(value, "target_key"),
        visibility=visibility,
        knowledge_owner_id=knowledge_owner_id,
    )


def _primitive(value: object) -> object:
    return json.loads(canonical_json(value))


def _craft_provider_projection(selection: object) -> dict[str, Any]:
    value = cast(dict[str, Any], _primitive(selection))
    return {
        "mode": value["mode"],
        "covered_axes": value["covered_axes"],
        "excerpts": value["excerpts"],
    }


def _filter_provider_projection(request: AdultFilterRequestV1) -> dict[str, Any]:
    scene_request = request.scene_request
    scene_output = request.scene_output
    return {
        "scene_request": {
            "entry_reason": scene_request.entry_reason.value,
            "adult_handoff": scene_request.adult_handoff,
            "exact_current_source": scene_request.exact_current_source,
            "accepted_safe_continuity": scene_request.accepted_safe_continuity,
            "accepted_protected_continuity": scene_request.accepted_protected_continuity,
            "autonomy_mode": scene_request.autonomy_mode,
            "depth_mode": scene_request.depth_mode,
            "current_context": _primitive(scene_request.current_context),
            "retrieved_craft": _craft_provider_projection(scene_request.retrieved_craft),
            "hard_boundaries": list(scene_request.hard_boundaries),
        },
        "scene_output": {
            "decision_path": _primitive(scene_output.decision_path),
            "exact_story_prose": scene_output.exact_story_prose,
            "resulting_state": scene_output.resulting_state,
            "unresolved_threads": list(scene_output.unresolved_threads),
            "next_route": scene_output.next_route.value,
            "next_route_reason": scene_output.next_route_reason,
        },
    }


def _write_durable_binding(path: Path, value: object) -> None:
    payload = canonical_json(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") != payload:
            raise StateConflictError("adult role execution binding already differs")
        return
    stage = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        with stage.open("x", encoding="utf-8", newline="") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(stage, path)
    finally:
        if stage.exists():
            stage.unlink()


def _load_durable_binding[BindingT](
    path: Path,
    target: type[BindingT],
) -> BindingT:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateConflictError("adult role execution binding is unavailable") from exc
    if not isinstance(payload, dict):
        raise StateConflictError("adult role execution binding is invalid")
    return cast(BindingT, from_mapping(target, payload))


def _json_object(raw: str, label: str) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip() or raw.strip() != raw:
        raise ContractValidationError(f"{label} is not one exact JSON object")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContractValidationError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ContractValidationError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _normalize_provider_json_object(raw: str) -> str:
    """Recover one unambiguous complete JSON object from a harmless envelope."""

    value = raw.strip()
    fenced_candidates: list[tuple[str, bool]] = []
    cursor = 0
    while (start := value.find("```json", cursor)) >= 0:
        content_start = start + len("```json")
        if value.startswith("\r\n", content_start):
            content_start += 2
        elif value.startswith("\n", content_start):
            content_start += 1
        else:
            cursor = content_start
            continue
        end = value.find("```", content_start)
        if end < 0:
            break
        candidate = value[content_start:end].strip()
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(decoded, dict):
                fenced_candidates.append((candidate, not value[end + 3 :].strip()))
        cursor = end + 3
    if len(fenced_candidates) == 1 and fenced_candidates[0][1]:
        return fenced_candidates[0][0]

    decoder = json.JSONDecoder()
    candidates: list[str] = []
    for start, character in enumerate(value):
        if character != "{":
            continue
        try:
            decoded, end = decoder.raw_decode(value, start)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict) and not value[end:].strip():
            candidates.append(value[start:end])
    if len(candidates) == 1:
        return candidates[0]
    return value


def _keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ContractValidationError(f"{label} field set changed")


def _object(value: Mapping[str, Any], key: str) -> dict[str, Any]:
    item = value.get(key)
    if not isinstance(item, dict):
        raise ContractValidationError(f"{key} must be an object")
    return cast(dict[str, Any], item)


def _objects(
    value: Mapping[str, Any],
    key: str,
    *,
    required: bool = False,
) -> tuple[dict[str, Any], ...]:
    items = value.get(key)
    if (
        not isinstance(items, list)
        or (required and not items)
        or not all(isinstance(item, dict) for item in items)
    ):
        raise ContractValidationError(f"{key} must be an object array")
    return tuple(cast(dict[str, Any], item) for item in items)


def _string(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise ContractValidationError(f"{key} must be a string")
    return item


def _nullable_string(value: Mapping[str, Any], key: str) -> str | None:
    item = value.get(key)
    if item is not None and not isinstance(item, str):
        raise ContractValidationError(f"{key} must be a string or null")
    return item


def _strings(value: Mapping[str, Any], key: str) -> tuple[str, ...]:
    items = value.get(key)
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        raise ContractValidationError(f"{key} must be a string array")
    return tuple(cast(list[str], items))
