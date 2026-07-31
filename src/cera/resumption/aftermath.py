"""Provider-free aftermath reasoning, composition, and atomic resumption."""

from __future__ import annotations

import json

from cera.composer import (
    ComposerExecutionResult,
    ComposerSubmission,
    ComposerValidationReceipt,
    FakeComposerFixture,
    FakeSceneComposerPort,
    RealizationKind,
    SceneComposerReceipt,
    SceneComposerUnavailable,
    SemanticCategory,
)
from cera.contracts import (
    AcceptedStoryArtifact,
    AftermathDecision,
    AftermathRecordType,
    ExposureStatus,
)
from cera.errors import ContractValidationError, ErrorCode
from cera.evidence import EvidenceDocument
from cera.ids import IdKind, TypedId, deterministic_id
from cera.schema import from_mapping
from cera.serialization import domain_sha256, text_sha256
from cera.storage import AuthorityRecord, CommitMode, SourceRecord, TurnCommitBundle
from cera.storage.sqlite_store import SQLiteAuthorityStore

from .fake import (
    AftermathReasonerUnavailable,
    FakeAftermathReasonerFixture,
    FakeSceneReasonerResumptionPort,
)
from .models import (
    AftermathComposerRequest,
    AftermathReasonerReceipt,
    AftermathReasonerRequest,
    ExternalReceiptStatus,
    ResumptionFailure,
    StoredExternalReceipt,
)


_AFTERMATH_HARD_BOUNDARIES = (
    "Resume only after the externally completed interval.",
    "Use only the validated non-graphic receipt and authorized committed evidence.",
    "Do not reconstruct or narrate the blocked interval.",
    "Physiological response, vocalization, freezing, silence, compliance, and failure to resist never establish consent or enjoyment.",
    "Do not author Ted's next unsupplied dialogue, thought, feeling, motive, or action.",
)
_FORBIDDEN_SEMANTIC_EDGES = frozenset(
    (
        source,
        target,
    )
    for source in (
        SemanticCategory.BODILY_RESPONSE,
        SemanticCategory.VOCALIZATION,
        SemanticCategory.FREEZE,
        SemanticCategory.SILENCE,
        SemanticCategory.COMPLIANCE,
        SemanticCategory.FAILURE_TO_RESIST,
    )
    for target in (
        SemanticCategory.DESIRE,
        SemanticCategory.ATTRACTION,
        SemanticCategory.PLEASURE,
        SemanticCategory.CONSENT,
    )
)


class AftermathCoordinator:
    def __init__(self, store: SQLiteAuthorityStore) -> None:
        self.store = store

    def reason(
        self,
        receipt_id: TypedId,
        port: FakeSceneReasonerResumptionPort,
        *,
        fixture: FakeAftermathReasonerFixture,
    ) -> StoredExternalReceipt:
        stored = self.store.get_external_receipt(receipt_id)
        if stored.status is not ExternalReceiptStatus.RECEIPT_VALIDATED_PENDING:
            return stored
        blocked = self.store.get_blocked_turn(stored.receipt.checkpoint_id)
        assert stored.validation_receipt is not None
        request = AftermathReasonerRequest(
            checkpoint=blocked.bundle.checkpoint,
            receipt=stored.receipt,
            validation_receipt=stored.validation_receipt,
            projection=blocked.bundle.projection,
            hard_boundaries=_AFTERMATH_HARD_BOUNDARIES,
        )
        if port.aftermath_fixture != fixture:
            raise ResumptionFailure(
                ErrorCode.REASONER_CONTRACT_INVALID,
                "fake aftermath fixture does not match requested fixture",
            )
        try:
            decision = port.resume_after_external_event(request)
        except AftermathReasonerUnavailable as exc:
            raise ResumptionFailure(ErrorCode.REASONER_UNAVAILABLE, str(exc)) from exc
        self._validate_decision(request, decision)
        reasoner_receipt = AftermathReasonerReceipt(
            schema_version=AftermathReasonerReceipt.SCHEMA_VERSION,
            provider_receipt_id=deterministic_id(
                IdKind.PROVIDER_RECEIPT,
                "cera.fake_aftermath_reasoner.receipt.v1",
                f"{request.request_sha256}|{fixture.fixture_sha256}|{decision.decision_sha256}",
            ),
            reasoner_request_sha256=request.request_sha256,
            external_receipt_sha256=stored.receipt.receipt_sha256,
            projection_sha256=blocked.bundle.projection.projection_sha256
            if blocked.bundle.projection is not None
            else None,
            decision_sha256=decision.decision_sha256,
            adapter_role="fake_scene_reasoner",
            fixture_id=fixture.fixture_id,
            fixture_sha256=fixture.fixture_sha256,
            external_provider_calls=0,
        )
        return self.store.store_aftermath_decision(decision, reasoner_receipt)

    def _validate_decision(
        self, request: AftermathReasonerRequest, decision: AftermathDecision
    ) -> None:
        if (
            decision.receipt_id != request.receipt.receipt_id
            or decision.receipt_sha256 != request.receipt.receipt_sha256
            or decision.checkpoint_id != request.checkpoint.checkpoint_id
            or decision.checkpoint_sha256 != request.checkpoint.checkpoint_sha256
        ):
            raise ResumptionFailure(
                ErrorCode.REASONER_CONTRACT_INVALID, "aftermath decision binding mismatch"
            )
        if request.projection is None:
            if decision.projection_id is not None or decision.projection_reconciliation:
                raise ResumptionFailure(
                    ErrorCode.REASONER_CONTRACT_INVALID,
                    "decision reconciles a projection that does not exist",
                )
        elif (
            decision.projection_id != request.projection.projection_id
            or decision.projection_sha256 != request.projection.projection_sha256
        ):
            raise ResumptionFailure(
                ErrorCode.REASONER_CONTRACT_INVALID, "projection reconciliation binding mismatch"
            )
        receipt_refs = {str(request.receipt.receipt_id)} | {
            str(step.event_step_id) for step in request.receipt.ordered_events
        }
        event_ids = {
            str(candidate.record_id)
            for candidate in decision.authority_candidates
            if candidate.record_type is AftermathRecordType.OBJECTIVE_EVENT
        }
        knowledge_owners = {
            str(owner)
            for step in request.receipt.ordered_events
            for owner in step.knowledge_owners
        }
        selected = set(decision.scene_decision.responding_npc_ids)
        if (
            not selected
            or request.checkpoint.protected_user_id in selected
            or not {str(value) for value in selected}.issubset(knowledge_owners)
        ):
            raise ResumptionFailure(
                ErrorCode.REASONER_CONTRACT_INVALID,
                "aftermath selected a protected, unaware, or absent responder",
            )
        if any(
            beat.actor_id.kind is IdKind.CHARACTER and beat.actor_id not in selected
            for beat in decision.scene_decision.current_segment.ordered_beats
        ):
            raise ResumptionFailure(
                ErrorCode.REASONER_CONTRACT_INVALID,
                "aftermath beat actor is not a selected responder",
            )
        expected_record_types = {
            AftermathRecordType.OBJECTIVE_EVENT: "event_fact",
            AftermathRecordType.CHARACTER_MEMORY: "memory",
            AftermathRecordType.RELATIONSHIP_EVIDENCE: "relationship",
            AftermathRecordType.MATERIAL_STATE: "material",
            AftermathRecordType.DEVELOPMENT_OVERLAY: "development",
        }
        source_id = deterministic_id(
            IdKind.SOURCE, "cera.aftermath.source.v1", request.receipt.receipt_sha256
        )
        expected_generation = self.store.get_branch(request.receipt.branch_id).generation + 1
        for candidate in decision.authority_candidates:
            payload = json.loads(candidate.payload_json)
            try:
                document = from_mapping(EvidenceDocument, payload)
            except ContractValidationError as exc:
                raise ResumptionFailure(
                    ErrorCode.REASONER_CONTRACT_INVALID,
                    f"aftermath candidate is not a retrievable evidence document: {exc}",
                ) from exc
            if (
                document.record_id != candidate.record_id
                or document.record_type.value != expected_record_types[candidate.record_type]
                or document.branch_origin_id != request.receipt.branch_id
                or document.genesis_revision_id != request.receipt.genesis_revision_id
                or document.content_class != "protected_non_graphic"
                or source_id not in document.source_refs
                or document.valid_from_generation != expected_generation
            ):
                raise ResumptionFailure(
                    ErrorCode.REASONER_CONTRACT_INVALID,
                    "aftermath evidence document has incorrect identity or scope",
                )
            if (
                not {str(value) for value in document.knowledge_owner_ids}.issubset(
                    knowledge_owners
                )
                or (
                    document.owner_id is not None
                    and str(document.owner_id) not in knowledge_owners
                )
            ):
                raise ResumptionFailure(
                    ErrorCode.REASONER_CONTRACT_INVALID,
                    "aftermath evidence transfers knowledge beyond receipt owners",
                )
            if any(
                "\n" in value or len(value) > 500
                for value in (document.title, document.abstract, document.claim)
            ):
                raise ResumptionFailure(
                    ErrorCode.REASONER_CONTRACT_INVALID,
                    "aftermath evidence text is not a bounded non-graphic claim",
                )
            sections = json.loads(document.sections_json)
            evidence_refs = set(sections.get("evidence_refs", ()))
            if not evidence_refs or not evidence_refs.issubset(receipt_refs):
                raise ResumptionFailure(
                    ErrorCode.REASONER_CONTRACT_INVALID,
                    "aftermath candidate lacks receipt-bound evidence",
                )
            if sections.get("genesis_effect", "none") != "none":
                raise ResumptionFailure(
                    ErrorCode.REASONER_CONTRACT_INVALID,
                    "aftermath cannot rewrite Genesis",
                )
            if candidate.record_type is AftermathRecordType.CHARACTER_MEMORY:
                if document.visibility.value != "owner_private":
                    raise ResumptionFailure(
                        ErrorCode.REASONER_CONTRACT_INVALID,
                        "trauma memory must remain owner-private",
                    )
                if document.owner_id is None or str(document.owner_id) not in knowledge_owners:
                    raise ResumptionFailure(
                        ErrorCode.REASONER_CONTRACT_INVALID,
                        "memory owner lacks direct receipt knowledge",
                    )
                if not set(sections.get("objective_event_refs", ())).issubset(event_ids):
                    raise ResumptionFailure(
                        ErrorCode.REASONER_CONTRACT_INVALID,
                        "memory objective event reference is invalid",
                    )
        latest = request.receipt.ordered_events[-1]
        if latest.reproductive_exposure in (ExposureStatus.POSSIBLE, ExposureStatus.ESTABLISHED):
            material_payloads = [
                json.loads(json.loads(candidate.payload_json)["sections_json"])
                for candidate in decision.authority_candidates
                if candidate.record_type is AftermathRecordType.MATERIAL_STATE
            ]
            if not any(
                payload.get("reproductive_exposure") == latest.reproductive_exposure.value
                and payload.get("conception_status") == latest.conception_status.value
                for payload in material_payloads
            ):
                raise ResumptionFailure(
                    ErrorCode.REASONER_CONTRACT_INVALID,
                    "material state failed to preserve reproductive uncertainty",
                )

    def compose(
        self,
        receipt_id: TypedId,
        port: FakeSceneComposerPort,
        *,
        fixture: FakeComposerFixture,
    ) -> ComposerExecutionResult:
        stored = self.store.get_external_receipt(receipt_id)
        if stored.status is ExternalReceiptStatus.AFTERMATH_VALIDATED:
            assert stored.commit_bundle is not None
            raise ResumptionFailure(
                ErrorCode.STATE_CONFLICT,
                "aftermath composition is already staged; resume commit without recomposing",
            )
        if stored.status is not ExternalReceiptStatus.AFTERMATH_DECIDED:
            raise ResumptionFailure(
                ErrorCode.STATE_CONFLICT, "receipt is not ready for aftermath composition"
            )
        assert stored.aftermath_decision is not None
        blocked = self.store.get_blocked_turn(stored.receipt.checkpoint_id)
        source = SourceRecord.from_payload(
            source_id=deterministic_id(
                IdKind.SOURCE, "cera.aftermath.source.v1", stored.receipt.receipt_sha256
            ),
            request_id=stored.receipt.request_id,
            branch_id=stored.receipt.branch_id,
            payload={
                "source_kind": "validated_external_completion_receipt",
                "external_receipt_id": str(stored.receipt.receipt_id),
                "external_receipt_sha256": stored.receipt.receipt_sha256,
                "checkpoint_id": str(stored.receipt.checkpoint_id),
                "checkpoint_sha256": stored.receipt.checkpoint_sha256,
            },
        )
        request = AftermathComposerRequest(
            aftermath_decision=stored.aftermath_decision,
            source_record=source,
            parent_artifact_id=blocked.bundle.checkpoint.starting_artifact_id,
            generation_id=blocked.bundle.checkpoint.generation_id,
            hard_boundaries=_AFTERMATH_HARD_BOUNDARIES,
        )
        if getattr(port, "fixture", None) != fixture:
            raise ResumptionFailure(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "fake composer fixture does not match requested fixture",
            )
        try:
            adapter_call = port.compose(request)  # type: ignore[arg-type]
        except SceneComposerUnavailable as exc:
            raise ResumptionFailure(ErrorCode.COMPOSER_UNAVAILABLE, str(exc)) from exc
        submission = adapter_call.submission
        self._validate_composer_submission(request, submission)
        candidate = submission.candidate
        manifest = submission.manifest
        decision = stored.aftermath_decision.scene_decision
        sequence_sha256 = domain_sha256(
            "cera.sequence_plan.v1", (decision.current_segment, decision.future_segments)
        )
        composer_receipt = SceneComposerReceipt(
            schema_version=SceneComposerReceipt.SCHEMA_VERSION,
            provider_receipt_id=adapter_call.provider_receipt_id,
            adapter_role=adapter_call.adapter_role,
            adapter_version=adapter_call.adapter_version,
            adapter_evidence_id=adapter_call.adapter_evidence_id,
            adapter_evidence_sha256=adapter_call.adapter_evidence_sha256,
            provider_receipt_sha256=adapter_call.provider_receipt_sha256,
            composer_request_sha256=request.request_sha256,
            source_sha256=source.source_sha256,
            safe_ledger_sha256=stored.receipt.receipt_sha256,
            protected_source_envelope_sha256=None,
            decision_sha256=domain_sha256("cera.scene_decision.v1", decision),
            sequence_plan_sha256=sequence_sha256,
            candidate_sha256=candidate.candidate_sha256,
            manifest_sha256=manifest.manifest_sha256,
            outcome="candidate_returned",
            external_provider_calls=adapter_call.external_provider_calls,
        )
        validation = ComposerValidationReceipt(
            schema_version=ComposerValidationReceipt.SCHEMA_VERSION,
            validation_receipt_id=deterministic_id(
                IdKind.VALIDATION,
                "cera.aftermath_composer_validation.v1",
                f"{request.request_sha256}|{candidate.candidate_sha256}|{manifest.manifest_sha256}",
            ),
            composer_request_sha256=request.request_sha256,
            candidate_sha256=candidate.candidate_sha256,
            manifest_sha256=manifest.manifest_sha256,
            validation_kind="structural_candidate_validation",
            status="accepted_in_memory",
            quarantined_advisory_count=0,
            quarantined_advisory_sha256=(),
            semantic_quality_proven=False,
            story_state_committed=False,
        )
        key = f"{request.request_sha256}|{candidate.candidate_sha256}|{manifest.manifest_sha256}"
        artifact = AcceptedStoryArtifact(
            schema_version=AcceptedStoryArtifact.SCHEMA_VERSION,
            artifact_id=deterministic_id(
                IdKind.ARTIFACT, "cera.aftermath.accepted_artifact.v1", key
            ),
            branch_id=stored.receipt.branch_id,
            generation_id=stored.receipt.generation_id,
            parent_artifact_id=stored.receipt.starting_artifact_id,
            source_id=source.source_id,
            decision_id=decision.decision_id,
            accepted_prose=candidate.story_text,
            prose_sha256=candidate.story_text_sha256,
            responding_npc_ids=decision.responding_npc_ids,
            realized_beat_ids=manifest.realized_beat_ids,
            validation_receipt_id=validation.validation_receipt_id,
            transaction_id=deterministic_id(
                IdKind.TRANSACTION, "cera.aftermath.transaction.v1", key
            ),
            status="accepted",
        )
        return ComposerExecutionResult(
            candidate=candidate,
            manifest=manifest,
            composer_receipt=composer_receipt,
            validation_receipt=validation,
            accepted_artifact=artifact,
            advisory_realization_metadata=(),
            quarantined_advisory_metadata=(),
            provider_call_receipt=adapter_call.provider_call_receipt,
        )

    @staticmethod
    def _validate_composer_submission(
        request: AftermathComposerRequest, submission: ComposerSubmission
    ) -> None:
        candidate = submission.candidate
        manifest = submission.manifest
        decision = request.aftermath_decision.scene_decision
        decision_sha = domain_sha256("cera.scene_decision.v1", decision)
        sequence_sha = domain_sha256(
            "cera.sequence_plan.v1", (decision.current_segment, decision.future_segments)
        )
        if not candidate.complete_core or candidate.is_outline or candidate.is_partial_draft:
            raise ResumptionFailure(
                ErrorCode.COMPOSER_CONTRACT_INVALID, "aftermath Composer returned an incomplete Core"
            )
        if (
            candidate.awaits_detailer
            or candidate.contains_internal_labels
            or candidate.contains_ui_markup
            or candidate.contains_provider_diagnostics
        ):
            raise ResumptionFailure(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "aftermath candidate contains disallowed presentation or workflow content",
            )
        if (
            manifest.candidate_sha256 != candidate.candidate_sha256
            or manifest.decision_sha256 != decision_sha
            or manifest.sequence_plan_sha256 != sequence_sha
            or manifest.floor_owner_id != decision.floor_owner_id
        ):
            raise ResumptionFailure(
                ErrorCode.COMPOSER_CONTRACT_INVALID, "aftermath manifest binding mismatch"
            )
        if set(manifest.realized_beat_ids) != {
            beat.beat_id for beat in decision.current_segment.ordered_beats
        }:
            raise ResumptionFailure(
                ErrorCode.COMPOSER_CONTRACT_INVALID, "aftermath beats were not fully realized"
            )
        if {value.character_id for value in manifest.move_realizations} != set(
            decision.responding_npc_ids
        ) or {value.character_id for value in manifest.participant_realizations} != set(
            decision.responding_npc_ids
        ):
            raise ResumptionFailure(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "aftermath manifest cast does not match the decision",
            )
        moves = {move.character_id: move for move in decision.character_moves}
        if any(
            realization.selected_intent_sha256
            != text_sha256(moves[realization.character_id].selected_intent)
            or realization.action_direction_sha256
            != text_sha256(moves[realization.character_id].action_direction)
            for realization in manifest.move_realizations
        ):
            raise ResumptionFailure(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "aftermath Composer changed a validated character move",
            )
        if manifest.source_unit_coverage:
            raise ResumptionFailure(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "aftermath prose cannot claim realization of the blocked source interval",
            )
        if any(span.source_unit_id is not None for span in manifest.character_spans):
            raise ResumptionFailure(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "aftermath spans cannot bind protected blocked source units",
            )
        if any(
            (edge.source, edge.target) in _FORBIDDEN_SEMANTIC_EDGES
            for edge in manifest.semantic_inferences
        ):
            raise ResumptionFailure(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "aftermath prose used a prohibited consent or enjoyment inference",
            )
        if (
            not manifest.terminal_state_preserved
            or not manifest.stops_before_protected_user_choice
            or manifest.introduced_major_objective_or_participant
        ):
            raise ResumptionFailure(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "aftermath candidate violated terminal state or protected-user ownership",
            )
        for span in manifest.character_spans:
            if span.end > len(candidate.story_text):
                raise ResumptionFailure(
                    ErrorCode.COMPOSER_CONTRACT_INVALID, "aftermath span exceeds candidate text"
                )
            if span.kind in (RealizationKind.DEPARTURE, RealizationKind.DESTINATION):
                # These are allowed for NPCs but never prove a protected-user action; owner
                # validation below limits spans to the selected NPC cast.
                pass
            if span.owner_id not in decision.responding_npc_ids:
                raise ResumptionFailure(
                    ErrorCode.COMPOSER_CONTRACT_INVALID,
                    "aftermath candidate authored an unselected character",
                )

    def stage(
        self, receipt_id: TypedId, composition: ComposerExecutionResult
    ) -> StoredExternalReceipt:
        stored = self.store.get_external_receipt(receipt_id)
        if stored.status is ExternalReceiptStatus.COMMITTED:
            return stored
        if stored.status is ExternalReceiptStatus.AFTERMATH_VALIDATED:
            return stored
        if stored.status is not ExternalReceiptStatus.AFTERMATH_DECIDED:
            raise ResumptionFailure(
                ErrorCode.STATE_CONFLICT, "receipt is not ready for commit staging"
            )
        assert stored.aftermath_decision is not None
        assert stored.validation_receipt is not None
        assert stored.aftermath_reasoner_receipt is not None
        blocked = self.store.get_blocked_turn(stored.receipt.checkpoint_id)
        branch = self.store.get_branch(stored.receipt.branch_id)
        if branch.head_artifact_id != stored.receipt.starting_artifact_id:
            raise ResumptionFailure(ErrorCode.RECEIPT_STALE, "branch head changed before commit")
        artifact = composition.accepted_artifact
        if (
            artifact.branch_id != stored.receipt.branch_id
            or artifact.generation_id != stored.receipt.generation_id
            or artifact.parent_artifact_id != stored.receipt.starting_artifact_id
        ):
            raise ResumptionFailure(
                ErrorCode.COMPOSER_CONTRACT_INVALID, "accepted aftermath artifact binding mismatch"
            )
        decision = stored.aftermath_decision.scene_decision
        expected_decision_sha = domain_sha256("cera.scene_decision.v1", decision)
        expected_sequence_sha = domain_sha256(
            "cera.sequence_plan.v1", (decision.current_segment, decision.future_segments)
        )
        if (
            composition.composer_receipt.source_sha256
            != SourceRecord.from_payload(
                source_id=artifact.source_id,
                request_id=stored.receipt.request_id,
                branch_id=stored.receipt.branch_id,
                payload={
                    "source_kind": "validated_external_completion_receipt",
                    "external_receipt_id": str(stored.receipt.receipt_id),
                    "external_receipt_sha256": stored.receipt.receipt_sha256,
                    "checkpoint_id": str(stored.receipt.checkpoint_id),
                    "checkpoint_sha256": stored.receipt.checkpoint_sha256,
                },
            ).source_sha256
            or composition.composer_receipt.safe_ledger_sha256
            != stored.receipt.receipt_sha256
            or composition.composer_receipt.decision_sha256 != expected_decision_sha
            or composition.composer_receipt.sequence_plan_sha256 != expected_sequence_sha
            or composition.composer_receipt.candidate_sha256
            != composition.candidate.candidate_sha256
            or composition.validation_receipt.candidate_sha256
            != composition.candidate.candidate_sha256
            or composition.validation_receipt.validation_receipt_id
            != artifact.validation_receipt_id
        ):
            raise ResumptionFailure(
                ErrorCode.COMPOSER_CONTRACT_INVALID,
                "aftermath composition receipts do not bind the staged artifact",
            )
        records = tuple(
            AuthorityRecord(
                record_id=candidate.record_id,
                branch_id=stored.receipt.branch_id,
                artifact_id=artifact.artifact_id,
                record_type=candidate.record_type.value,
                payload_json=candidate.payload_json,
                payload_sha256=candidate.payload_sha256,
                supersedes=candidate.supersedes,
            )
            for candidate in stored.aftermath_decision.authority_candidates
        )
        source = SourceRecord.from_payload(
            source_id=artifact.source_id,
            request_id=stored.receipt.request_id,
            branch_id=stored.receipt.branch_id,
            payload={
                "source_kind": "validated_external_completion_receipt",
                "external_receipt_id": str(stored.receipt.receipt_id),
                "external_receipt_sha256": stored.receipt.receipt_sha256,
                "checkpoint_id": str(stored.receipt.checkpoint_id),
                "checkpoint_sha256": stored.receipt.checkpoint_sha256,
            },
        )
        bundle = TurnCommitBundle(
            transaction_id=artifact.transaction_id,
            idempotency_key=f"aftermath:{stored.receipt.idempotency_key}",
            mode=CommitMode.APPEND,
            branch_id=stored.receipt.branch_id,
            expected_generation=branch.generation,
            expected_head_artifact_id=branch.head_artifact_id,
            source=source,
            artifact=artifact,
            authority_records=records,
            validation_receipt_ids=(
                stored.validation_receipt.validation_receipt_id,
                composition.validation_receipt.validation_receipt_id,
            ),
            lookup_receipt_ids=(),
            provider_receipt_ids=(
                stored.aftermath_reasoner_receipt.provider_receipt_id,
                composition.composer_receipt.provider_receipt_id,
            ),
            external_receipt_id=stored.receipt.receipt_id,
            blocked_checkpoint_id=blocked.bundle.checkpoint.checkpoint_id,
        )
        return self.store.stage_aftermath_commit(receipt_id, bundle)

    def commit_staged(self, receipt_id: TypedId):
        stored = self.store.get_external_receipt(receipt_id)
        if stored.status not in (
            ExternalReceiptStatus.AFTERMATH_VALIDATED,
            ExternalReceiptStatus.COMMITTED,
        ):
            raise ResumptionFailure(
                ErrorCode.STATE_CONFLICT, "aftermath commit bundle is not staged"
            )
        assert stored.commit_bundle is not None
        return self.store.commit_turn(stored.commit_bundle)

    def stage_and_commit(
        self, receipt_id: TypedId, composition: ComposerExecutionResult
    ):
        self.stage(receipt_id, composition)
        return self.commit_staged(receipt_id)

