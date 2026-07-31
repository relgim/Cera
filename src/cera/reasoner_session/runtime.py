"""Active branch-bound stored-session binding for the SillyTavern Reasoner.

Python owns the ledger and accepted checkpoint.  The provider thread is a
disposable continuity cache: every turn still carries the complete current
authority packet and every publication still passes the ordinary Python
transaction boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from threading import RLock

from cera.composer import ArtifactPublicationMode
from cera.contracts import BehavioralTurnControls
from cera.ids import IdKind, TypedId, deterministic_id
from cera.providers import (
    CodexSDKTransport,
    ProviderSchemaDialect,
    StoredCodexThreadRunner,
    codex_reasoner_candidate,
    project_provider_output_schema,
)
from cera.providers.codex_worker import CODEX_REASONER_BASE_INSTRUCTIONS
from cera.reasoner import CodexSceneReasonerPort
from cera.reasoner.codex import (
    CODEX_REASONER_ADAPTER_VERSION,
    CODEX_REASONER_PROMPT_VERSION,
    build_codex_reasoner_prompt,
)
from cera.reasoner.drafts import codex_reasoner_draft_v6_json_schema
from cera.reasoner.mcp_bridge import MCP_TOOL_CONTRACT_VERSION
from cera.schema import from_mapping
from cera.serialization import canonical_sha256, text_sha256, to_primitive

from .codex_stored import (
    CodexStoredThreadSessionPort,
    OpenAICodexStoredThreadBackend,
)
from .coordinator import BranchBoundReasonerSessionCoordinator
from .models import (
    ConstraintBinding,
    ContextAuthorityDelta,
    ContextEvidenceBinding,
    EvidenceDelivery,
    ReasonerSessionCompatibility,
    SessionReconstructionBundle,
    SessionRole,
    SessionTurnMode,
)


_PACKET_MARKER = "The complete authoritative packet follows as canonical JSON:"
_PROVIDER_CONTEXT_POLICY = (
    "STORED-THREAD AUTHORITY POLICY: Earlier CERA packets and Reasoner drafts "
    "in this branch thread are provisional context, never story authority. "
    "The newest CERA packet is the complete current authority and supersedes "
    "every conflict. Never cite provider conversation as evidence, character "
    "knowledge, canon, or durable memory. Python remains final authority."
)
_SUPPORTED_EFFORTS = frozenset({"medium", "high", "xhigh"})


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _stable_reasoner_instructions() -> str:
    prompt = build_codex_reasoner_prompt(
        {"tool_policy": {"evidence_tools_available": True}}
    )
    delimiter = "\n" + _PACKET_MARKER + "\n"
    if prompt.count(delimiter) != 1:
        raise RuntimeError("CERA Reasoner prompt has no stable split boundary")
    return prompt.split(delimiter, 1)[0]


_STABLE_REASONER_INSTRUCTIONS = _stable_reasoner_instructions()
_STORED_BASE_INSTRUCTIONS = (
    CODEX_REASONER_BASE_INSTRUCTIONS
    + "\n\n"
    + _PROVIDER_CONTEXT_POLICY
    + "\n\n"
    + _STABLE_REASONER_INSTRUCTIONS
)


class _StablePrefixStoredTransport:
    """Move stable Reasoner instructions to the stored thread's base prefix."""

    def __init__(self, transport: CodexSDKTransport) -> None:
        self.transport = transport
        self.route = transport.route

    def invoke(self, prompt: str, **kwargs):
        delimiter = "\n" + _PACKET_MARKER + "\n"
        if prompt.count(delimiter) != 1:
            raise RuntimeError("active Reasoner prompt cannot be split exactly once")
        stable, packet = prompt.split(delimiter, 1)
        if stable != _STABLE_REASONER_INSTRUCTIONS:
            raise RuntimeError("active Reasoner stable instructions changed")
        return self.transport.invoke(
            _PACKET_MARKER + "\n" + packet,
            **kwargs,
        )


@dataclass(frozen=True, slots=True)
class StoredReasonerCandidateBinding:
    checkpoint_id: TypedId
    reasoner_port: CodexSceneReasonerPort
    effort: str
    provider_thread_id_sha256: str


class NativeStoredReasonerSessionRuntime:
    """Bind active SillyTavern turns to one accepted Codex checkpoint tree."""

    MODE = "branch_bound_native_stored_v1"

    def __init__(
        self,
        store,
        *,
        repository_root: Path,
        session_port=None,
    ) -> None:
        if not repository_root.is_absolute() or not repository_root.is_dir():
            raise ValueError("stored Reasoner repository root is unavailable")
        self.store = store
        self.repository_root = repository_root
        self._lock = RLock()
        self._closed = False
        self._codex_context = None
        self._codex = None
        self._backend = None
        if session_port is not None:
            self._port = session_port
            return

        from openai_codex import Codex, CodexConfig

        self._codex_context = Codex(
            CodexConfig(config_overrides=("mcp_servers={}",), env={})
        )
        self._codex = self._codex_context.__enter__()
        try:
            if self._codex.account().account is None:
                raise RuntimeError("ChatGPT Codex session is unavailable")
            self._backend = OpenAICodexStoredThreadBackend(
                codex=self._codex,
                model="gpt-5.6-sol",
                cwd=str(repository_root),
                base_instructions=_STORED_BASE_INSTRUCTIONS,
                service_name="cera_sillytavern_stored_reasoner",
            )
            self._port = CodexStoredThreadSessionPort(
                self._backend,
                base_instruction_sha256=text_sha256(_STORED_BASE_INSTRUCTIONS),
            )
        except Exception:
            self._codex_context.__exit__(None, None, None)
            raise

    @property
    def status(self) -> dict[str, object]:
        return {
            "mode": self.MODE,
            "active": not self._closed,
            "model": "gpt-5.6-sol",
            "reasoning_efforts": ("medium", "high", "xhigh"),
            "verifier_effort": "medium",
        }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._codex_context is not None:
                self._codex_context.__exit__(None, None, None)

    def begin_candidate(
        self,
        application_request,
        *,
        effort: str,
        workspace: Path,
    ) -> StoredReasonerCandidateBinding:
        normalized_effort = effort.casefold()
        if normalized_effort not in _SUPPORTED_EFFORTS:
            raise ValueError("unsupported Sol reasoning effort")
        reasoner_request = application_request.reasoner_request
        with self._lock:
            self._assert_open()
            compatibility = self._compatibility(reasoner_request, normalized_effort)
            reconstruction = self._reconstruction(reasoner_request, compatibility)
            self._restore_active_descriptor(compatibility)
            coordinator = BranchBoundReasonerSessionCoordinator(self.store, self._port)
            ledger = coordinator.ensure_session(compatibility, reconstruction)
            delta = self._delta(application_request, ledger.session_id)
            checkpoint = coordinator.begin_candidate(delta)
        route = codex_reasoner_candidate(
            model="gpt-5.6-sol",
            effort=normalized_effort,
        )
        transport = _StablePrefixStoredTransport(
            CodexSDKTransport(
                route,
                workspace=workspace,
                runner=StoredCodexThreadRunner(
                    checkpoint.provider_handle.provider_thread_id
                ),
            )
        )
        return StoredReasonerCandidateBinding(
            checkpoint_id=checkpoint.checkpoint_id,
            reasoner_port=CodexSceneReasonerPort(
                transport,
                evidence_tools_enabled=True,
            ),
            effort=normalized_effort,
            provider_thread_id_sha256=text_sha256(
                checkpoint.provider_handle.provider_thread_id
            ),
        )

    def bind_review(self, checkpoint_id: TypedId, review_id: TypedId) -> None:
        with self._lock:
            self._assert_open()
            BranchBoundReasonerSessionCoordinator(
                self.store, self._port
            ).bind_creator_review(checkpoint_id, review_id)

    def rebind_review(
        self,
        *,
        prior_review_id: TypedId,
        replacement_review_id: TypedId,
    ) -> None:
        with self._lock:
            self._assert_open()
            checkpoint = self.store.reasoner_checkpoint_for_review(prior_review_id)
            if checkpoint is None:
                raise RuntimeError("Reasoner candidate review binding is missing")
            BranchBoundReasonerSessionCoordinator(
                self.store, self._port
            ).rebind_creator_review(
                checkpoint.checkpoint_id,
                prior_review_id=prior_review_id,
                replacement_review_id=replacement_review_id,
            )

    def reject_review(self, review_id: TypedId) -> None:
        with self._lock:
            self._assert_open()
            checkpoint = self._checkpoint_for_review(review_id)
            self._restore_descriptor(checkpoint)
            BranchBoundReasonerSessionCoordinator(
                self.store, self._port
            ).reject_candidate(checkpoint.checkpoint_id)

    def invalidate_checkpoint(self, checkpoint_id: TypedId, *, reason: str) -> None:
        with self._lock:
            self._assert_open()
            checkpoint = self.store.get_reasoner_checkpoint(checkpoint_id)
            self._restore_descriptor(checkpoint)
            BranchBoundReasonerSessionCoordinator(
                self.store, self._port
            ).invalidate_candidate(checkpoint_id, reason)

    def accept_review(self, review_id: TypedId, published) -> None:
        with self._lock:
            self._assert_open()
            checkpoint = self._checkpoint_for_review(review_id)
            self._restore_descriptor(checkpoint)
            receipt = published.publication.receipt
            artifact = self.store.get_artifact(receipt.new_artifact_id)
            event_state_sha256 = canonical_sha256(
                {
                    "artifact_id": str(artifact.artifact_id),
                    "realized_beat_ids": tuple(
                        str(value) for value in artifact.realized_beat_ids
                    ),
                    "inserted_record_ids": tuple(
                        str(value) for value in receipt.inserted_record_ids
                    ),
                }
            )
            BranchBoundReasonerSessionCoordinator(
                self.store, self._port
            ).accept_candidate(
                checkpoint.checkpoint_id,
                artifact_id=artifact.artifact_id,
                accepted_prose_sha256=artifact.prose_sha256,
                accepted_event_state_sha256=event_state_sha256,
                commit_sha256=receipt.transaction_sha256,
            )

    def _compatibility(self, request, effort: str) -> ReasonerSessionCompatibility:
        snapshot = request.prepared_turn.evidence_snapshot
        route = codex_reasoner_candidate(model="gpt-5.6-sol", effort=effort)
        provider_schema = project_provider_output_schema(
            codex_reasoner_draft_v6_json_schema(),
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        return ReasonerSessionCompatibility(
            schema_version=ReasonerSessionCompatibility.SCHEMA_VERSION,
            world_id=snapshot.world_id,
            branch_id=snapshot.branch_id,
            role=SessionRole.SCENE_REASONER,
            provider=route.provider.value,
            model=route.model_name,
            reasoning_effort=effort,
            service_tier="standard",
            transport_version=route.transport_version,
            adapter_version=CODEX_REASONER_ADAPTER_VERSION,
            prompt_version=CODEX_REASONER_PROMPT_VERSION,
            provider_schema_sha256=canonical_sha256(provider_schema),
            base_instruction_sha256=text_sha256(_STORED_BASE_INSTRUCTIONS),
            tool_contract_version=MCP_TOOL_CONTRACT_VERSION,
            genesis_revision_id=snapshot.genesis_revision_id,
            authority_policy_version="cera.owner_architecture.v2",
            privacy_projection_version=snapshot.visibility_policy_version,
            protected_user_id=request.prepared_turn.request.protected_user_id,
            autonomy_profile_version=BehavioralTurnControls.SCHEMA_VERSION,
        )

    def _reconstruction(
        self,
        request,
        compatibility: ReasonerSessionCompatibility,
    ) -> SessionReconstructionBundle:
        snapshot = request.prepared_turn.evidence_snapshot
        branch = self.store.get_branch(snapshot.branch_id)
        evidence = self._evidence_bindings(request)
        constraints = self._constraint_bindings(
            snapshot.world_id,
            snapshot.branch_id,
        )
        context_sha256 = canonical_sha256(
            {
                "branch_id": str(branch.branch_id),
                "head_artifact_id": (
                    str(branch.head_artifact_id)
                    if branch.head_artifact_id is not None
                    else None
                ),
                "generation": branch.generation,
                "authority_revision": branch.authority_revision,
                "evidence": evidence,
                "constraints": constraints,
            }
        )
        return SessionReconstructionBundle(
            schema_version=SessionReconstructionBundle.SCHEMA_VERSION,
            bundle_id=deterministic_id(
                IdKind.CONTEXT_DELTA,
                "cera.sillytavern_session_reconstruction.v1",
                (
                    f"{compatibility.compatibility_sha256}|{snapshot.snapshot_token}|"
                    f"{context_sha256}"
                ),
            ),
            compatibility_sha256=compatibility.compatibility_sha256,
            world_id=snapshot.world_id,
            branch_id=snapshot.branch_id,
            accepted_head_artifact_id=branch.head_artifact_id,
            generation=branch.generation,
            authority_revision=branch.authority_revision,
            evidence_snapshot_token=snapshot.snapshot_token,
            evidence=evidence,
            active_constraints=constraints,
            accepted_receipt_ids=(),
            authoritative_context_sha256=context_sha256,
            created_at=_utc_now(),
        )

    def _delta(self, application_request, session_id: TypedId) -> ContextAuthorityDelta:
        request = application_request.reasoner_request
        snapshot = request.prepared_turn.evidence_snapshot
        ledger = self.store.get_reasoner_session(session_id)
        accepted = self.store.get_reasoner_checkpoint(ledger.accepted_checkpoint_id)
        mode = (
            SessionTurnMode.REGENERATE
            if application_request.composer_plan.publication_mode
            is ArtifactPublicationMode.REGENERATE
            else SessionTurnMode.APPEND
        )
        parent_checkpoint_id = accepted.checkpoint_id
        if mode is SessionTurnMode.REGENERATE:
            parent_checkpoint_id = (
                accepted.parent_checkpoint_id or accepted.checkpoint_id
            )
        return ContextAuthorityDelta(
            schema_version=ContextAuthorityDelta.SCHEMA_VERSION,
            delta_id=deterministic_id(
                IdKind.CONTEXT_DELTA,
                "cera.sillytavern_context_delta.v1",
                f"{session_id}|{request.request_sha256}",
            ),
            session_id=session_id,
            parent_checkpoint_id=parent_checkpoint_id,
            world_id=snapshot.world_id,
            branch_id=snapshot.branch_id,
            request_id=request.prepared_turn.request.request_id,
            turn_mode=mode,
            replaces_artifact_id=(
                accepted.accepted_head_artifact_id
                if mode is SessionTurnMode.REGENERATE
                else None
            ),
            generation=accepted.generation,
            accepted_head_artifact_id=accepted.accepted_head_artifact_id,
            authority_revision=accepted.authority_revision,
            evidence_snapshot_token=snapshot.snapshot_token,
            current_evidence=self._evidence_bindings(request),
            revoked_evidence_ids=(),
            active_constraints=self._constraint_bindings(
                snapshot.world_id,
                snapshot.branch_id,
            ),
            source_sha256=request.prepared_turn.request.source_sha256,
            turn_packet_sha256=request.request_sha256,
            created_at=_utc_now(),
        )

    @staticmethod
    def _evidence_bindings(request) -> tuple[ContextEvidenceBinding, ...]:
        bindings = []
        for exact in request.seed_dossier.exact_seed_evidence:
            try:
                sections = json.loads(exact.sections_json)
            except json.JSONDecodeError:
                sections = {}
            section_ids = (
                tuple(sorted(str(value) for value in sections))
                if isinstance(sections, dict)
                else ()
            )
            bindings.append(
                ContextEvidenceBinding(
                    record_id=exact.metadata.record_id,
                    version=exact.metadata.record_version,
                    payload_sha256=canonical_sha256(to_primitive(exact)),
                    delivery=EvidenceDelivery.EXACT_INLINE,
                    exact_section_ids=section_ids,
                )
            )
        return tuple(bindings)

    def _constraint_bindings(
        self, world_id: TypedId, branch_id: TypedId
    ) -> tuple[ConstraintBinding, ...]:
        return tuple(
            ConstraintBinding(value.constraint_id, value.record_sha256)
            for value in self.store.active_creator_constraints(world_id, branch_id)
        )

    def _checkpoint_for_review(self, review_id: TypedId):
        checkpoint = self.store.reasoner_checkpoint_for_review(review_id)
        if checkpoint is None:
            raise RuntimeError("Reasoner candidate review binding is missing")
        return checkpoint

    def _restore_active_descriptor(
        self, compatibility: ReasonerSessionCompatibility
    ) -> None:
        active = self.store.active_reasoner_session_for_branch_role(
            compatibility.branch_id,
            role=compatibility.role.value,
        )
        if active is None:
            return
        self._restore_descriptor(
            self.store.get_reasoner_checkpoint(active.accepted_checkpoint_id)
        )

    def _restore_descriptor(self, checkpoint) -> None:
        events = self.store.provider_thread_custody_events(checkpoint.checkpoint_id)
        restore = getattr(self._port, "restore_descriptor", None)
        if events and callable(restore):
            restore(
                checkpoint.provider_handle,
                events[-1].descriptor,
            )

    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError("stored Reasoner session runtime is closed")
