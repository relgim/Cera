"""Python-owned publication boundary for validated ordinary turns."""

from __future__ import annotations

import json

from cera.composer import ArtifactPublicationMode, CompositionMode
from cera.contracts import (
    Certainty,
    DecisionRoute,
    EvidenceAuthority,
    EvidenceRecordType,
    KnowledgeRoute,
    TruthStatus,
    Visibility,
)
from cera.errors import ContractValidationError
from cera.evidence import EvidenceDocument, EvidenceEpistemicClass, EvidenceWorldMode
from cera.ids import IdKind, deterministic_id
from cera.kernel import TurnRoute
from cera.serialization import canonical_json, domain_sha256
from cera.storage import (
    AuthorityRecord,
    CommitMode,
    ReceiptCategory,
    ReceiptRecord,
    PostPublicationWorkRequest,
    SQLiteAuthorityStore,
    StoredCommit,
    TurnCommitBundle,
)

from .models import IngressPublicationEvidence, LiveShapedTurnResult


class OrdinaryTurnCommitBuilder:
    """Convert one fully validated ordinary result into an auditable commit bundle.

    It writes one direct accepted-turn event plus one system-private accepted
    reply material record.  The material record preserves the exact immutable
    presentation artifact for later continuity without treating creative prose
    as an objective event summary. It may also write the exact subset of
    one-step development atoms independently verified in the accepted prose.
    No unverified model proposal becomes durable. Requested post-publication
    consumers are scheduled in the same story transaction.
    """

    REASONER_EVIDENCE_SCHEMA = "cera.turn_reasoner_receipt_evidence.v1"
    COMPOSER_EVIDENCE_SCHEMA = "cera.turn_composer_receipt_evidence.v1"
    REALIZATION_VERIFIER_EVIDENCE_SCHEMA = (
        "cera.turn_realization_verifier_receipt_evidence.v1"
    )

    def build(
        self,
        result: LiveShapedTurnResult,
        *,
        post_publication_work_requests: tuple[PostPublicationWorkRequest, ...] = (),
        ingress_evidence: IngressPublicationEvidence | None = None,
    ) -> TurnCommitBundle:
        if not isinstance(result, LiveShapedTurnResult):
            raise ContractValidationError("ordinary publication requires a live-shaped result")

        request = result.context.request
        prepared = request.prepared_turn
        turn_request = prepared.request
        snapshot = prepared.evidence_snapshot
        artifact = result.accepted_artifact
        decision = result.reasoner.outcome.decision
        if decision is None:
            raise ContractValidationError("ordinary publication requires a validated decision")
        if (
            prepared.route is not TurnRoute.ORDINARY
            or decision.route is not DecisionRoute.ORDINARY
            or request.source_packet.mode is not CompositionMode.ORDINARY
            or request.adult_binding is not None
        ):
            raise ContractValidationError(
                "ordinary publication rejects adult, blocked, and aftermath routes"
            )
        if (
            result.receipt.story_state_committed
            or result.receipt.story_authority_writes != 0
            or result.composer.validation_receipt.story_state_committed
            or not result.realization_verification.accepted
        ):
            raise ContractValidationError("pre-commit evidence cannot claim an earlier commit")
        if (
            result.receipt.request_id != turn_request.request_id
            or result.receipt.branch_id != turn_request.branch_id
            or result.receipt.generation_id != turn_request.generation_id
            or result.receipt.snapshot_token != snapshot.snapshot_token
        ):
            raise ContractValidationError("live-shaped receipt changed turn identity")
        if (
            artifact.branch_id != turn_request.branch_id
            or artifact.generation_id != turn_request.generation_id
            or artifact.source_id != prepared.source_record.source_id
            or artifact.decision_id != decision.decision_id
            or artifact.validation_receipt_id
            != result.final_acceptance.receipt_id
        ):
            raise ContractValidationError("accepted artifact changed publication bindings")
        if request.publication_mode is ArtifactPublicationMode.APPEND:
            mode = CommitMode.APPEND
            expected_parent = snapshot.branch_head_artifact_id
            replaces_artifact_id = None
        else:
            mode = CommitMode.REGENERATE
            expected_parent = request.publication_parent_artifact_id
            replaces_artifact_id = request.replaces_artifact_id
            if replaces_artifact_id != snapshot.branch_head_artifact_id:
                raise ContractValidationError(
                    "regeneration target changed after lineage validation"
                )
        if artifact.parent_artifact_id != expected_parent:
            raise ContractValidationError("accepted artifact changed its validated lineage")
        if (
            turn_request.parent_artifact_id != snapshot.branch_head_artifact_id
            or snapshot.request_id != turn_request.request_id
            or snapshot.branch_id != turn_request.branch_id
            or snapshot.world_id != turn_request.world_id
            or snapshot.genesis_revision_id != turn_request.genesis_revision_id
        ):
            raise ContractValidationError("prepared snapshot is not the append publication base")
        source = prepared.source_record
        if (
            source.request_id != turn_request.request_id
            or source.branch_id != turn_request.branch_id
        ):
            raise ContractValidationError("source ledger identity changed before publication")
        source_payload = json.loads(source.payload_json)
        if source_payload.get("source_sha256") != turn_request.source_sha256:
            raise ContractValidationError("source ledger does not bind the protected source hash")

        if ingress_evidence is not None:
            if (
                ingress_evidence.request_id != turn_request.request_id
                or ingress_evidence.snapshot_token != snapshot.snapshot_token
                or ingress_evidence.source_sha256 != turn_request.source_sha256
                or ingress_evidence.reasoner_request_sha256
                != result.reasoner.receipt.reasoner_request_sha256
            ):
                raise ContractValidationError(
                    "ingress publication evidence changed the accepted turn binding"
                )
        pipeline_lookup_receipts = _unique_receipts(
            (
                *result.reasoner.evidence_lookup_receipts,
                *result.context.lookup_receipts,
            )
        )
        pipeline_lookup_ids = tuple(
            value.lookup_receipt_id for value in pipeline_lookup_receipts
        )
        if pipeline_lookup_ids != result.receipt.evidence_lookup_receipt_ids:
            raise ContractValidationError("lookup receipt payloads do not match turn receipt")
        lookup_receipts = _unique_receipts(
            (
                *(ingress_evidence.seed_lookup_receipts if ingress_evidence else ()),
                *pipeline_lookup_receipts,
            )
        )
        lookup_ids = tuple(value.lookup_receipt_id for value in lookup_receipts)

        validation_receipts = [
            result.context.receipt,
            result.composer.validation_receipt,
            result.realization_verification.receipt,
            result.final_acceptance,
            result.receipt,
        ]
        if ingress_evidence is not None:
            validation_receipts.insert(0, ingress_evidence)
        if result.reasoner.state_delta_validation_receipt is not None:
            validation_receipts.insert(
                1, result.reasoner.state_delta_validation_receipt
            )
        validation_ids = tuple(_validation_id(value) for value in validation_receipts)
        provider_ids = [
            result.reasoner.receipt.provider_receipt_id,
            result.composer.composer_receipt.provider_receipt_id,
        ]
        verifier_provider_receipt = (
            result.realization_verification.provider_call_receipt
        )
        if verifier_provider_receipt is not None:
            provider_ids.append(verifier_provider_receipt.provider_receipt_id)

        receipt_records = [
            ReceiptRecord.from_payload(
                receipt_id=result.reasoner.receipt.provider_receipt_id,
                category=ReceiptCategory.PROVIDER,
                schema_version=self.REASONER_EVIDENCE_SCHEMA,
                payload={
                    "scene_reasoner_receipt": result.reasoner.receipt,
                    "provider_call_receipt": result.reasoner.provider_call_receipt,
                    "mcp_bridge_receipt": result.reasoner.mcp_bridge_receipt,
                },
            ),
            ReceiptRecord.from_payload(
                receipt_id=result.composer.composer_receipt.provider_receipt_id,
                category=ReceiptCategory.PROVIDER,
                schema_version=self.COMPOSER_EVIDENCE_SCHEMA,
                payload={
                    "scene_composer_receipt": result.composer.composer_receipt,
                    "provider_call_receipt": result.composer.provider_call_receipt,
                },
            ),
        ]
        if verifier_provider_receipt is not None:
            receipt_records.append(
                ReceiptRecord.from_payload(
                    receipt_id=verifier_provider_receipt.provider_receipt_id,
                    category=ReceiptCategory.PROVIDER,
                    schema_version=self.REALIZATION_VERIFIER_EVIDENCE_SCHEMA,
                    payload={
                        "scene_realization_verification_receipt": (
                            result.realization_verification.receipt
                        ),
                        "provider_call_receipt": verifier_provider_receipt,
                    },
                )
            )
        receipt_records.extend(
            ReceiptRecord.from_payload(
                receipt_id=value.lookup_receipt_id,
                category=ReceiptCategory.LOOKUP,
                schema_version=value.schema_version,
                payload=value,
            )
            for value in lookup_receipts
        )
        receipt_records.extend(
            ReceiptRecord.from_payload(
                receipt_id=_validation_id(value),
                category=ReceiptCategory.VALIDATION,
                schema_version=value.schema_version,
                payload=value,
            )
            for value in validation_receipts
        )

        event_document = _accepted_turn_event(result)
        event_record = AuthorityRecord.from_payload(
            record_id=event_document.record_id,
            branch_id=turn_request.branch_id,
            artifact_id=artifact.artifact_id,
            record_type=event_document.record_type.value,
            payload=event_document,
        )
        reply_document = _accepted_reply_material(result, event_document.record_id)
        reply_record = AuthorityRecord.from_payload(
            record_id=reply_document.record_id,
            branch_id=turn_request.branch_id,
            artifact_id=artifact.artifact_id,
            record_type=reply_document.record_type.value,
            payload=reply_document,
        )
        development_records = tuple(
            AuthorityRecord.from_payload(
                record_id=document.record_id,
                branch_id=turn_request.branch_id,
                artifact_id=artifact.artifact_id,
                record_type=document.record_type.value,
                payload=document,
            )
            for document in _accepted_development_atoms(
                result,
                event_document.record_id,
            )
        )

        bundle = TurnCommitBundle(
            transaction_id=artifact.transaction_id,
            idempotency_key=turn_request.idempotency_key,
            mode=mode,
            branch_id=turn_request.branch_id,
            expected_generation=snapshot.generation,
            expected_head_artifact_id=snapshot.branch_head_artifact_id,
            source=source,
            artifact=artifact,
            authority_records=(event_record, reply_record, *development_records),
            validation_receipt_ids=validation_ids,
            lookup_receipt_ids=lookup_ids,
            provider_receipt_ids=tuple(provider_ids),
            receipt_records=tuple(receipt_records),
            post_publication_work_requests=post_publication_work_requests,
            replaces_artifact_id=replaces_artifact_id,
        )
        if bundle.artifact_sha256 != result.receipt.accepted_artifact_sha256:
            raise ContractValidationError("commit artifact hash changed after turn validation")
        if domain_sha256(
            "cera.accepted_story_artifact.v1", artifact
        ) != result.receipt.accepted_artifact_sha256:
            raise ContractValidationError("live-shaped artifact evidence is inconsistent")
        return bundle


class OrdinaryTurnCommitCoordinator:
    """Build and atomically commit one ordinary append or regeneration turn."""

    def __init__(
        self,
        store: SQLiteAuthorityStore,
        builder: OrdinaryTurnCommitBuilder | None = None,
    ) -> None:
        self.store = store
        self.builder = builder or OrdinaryTurnCommitBuilder()

    def commit(
        self,
        result: LiveShapedTurnResult,
        *,
        post_publication_work_requests: tuple[PostPublicationWorkRequest, ...] = (),
        ingress_evidence: IngressPublicationEvidence | None = None,
    ) -> StoredCommit:
        return self.store.commit_turn(
            self.builder.build(
                result,
                post_publication_work_requests=post_publication_work_requests,
                ingress_evidence=ingress_evidence,
            )
        )


def _validation_id(value):
    for field_name in (
        "assembly_receipt_id",
        "validation_receipt_id",
        "verification_id",
        "receipt_id",
    ):
        receipt_id = getattr(value, field_name, None)
        if receipt_id is not None:
            return receipt_id
    raise ContractValidationError("validation receipt payload has no typed receipt ID")


def _unique_receipts(values):
    """Deduplicate identical receipts while rejecting identity collisions."""

    by_id = {}
    for value in values:
        existing = by_id.get(value.lookup_receipt_id)
        if existing is not None and existing != value:
            raise ContractValidationError(
                "lookup receipt identity maps to different payloads"
            )
        by_id[value.lookup_receipt_id] = value
    return tuple(by_id.values())


def _accepted_turn_event(result: LiveShapedTurnResult) -> EvidenceDocument:
    """Create the direct event evidence required by deferred consolidation.

    This record contains validated current-segment structure, not a model-authored
    memory or psychological interpretation. It remains system-private so later
    owner-specific knowledge must be derived and validated explicitly.
    """

    request = result.context.request
    prepared = request.prepared_turn
    snapshot = prepared.evidence_snapshot
    artifact = result.accepted_artifact
    decision = result.reasoner.outcome.decision
    assert decision is not None
    participants = tuple(
        dict.fromkeys(
            (
                prepared.request.protected_user_id,
                *decision.responding_npc_ids,
            )
        )
    )
    verified_beat_ids = set(
        result.realization_verification.receipt.verified_beat_ids
    )
    beats = tuple(
        {
            "beat_id": str(value.beat_id),
            "actor_id": str(value.actor_id),
            "state": value.state.value,
            "neutral_event": value.neutral_event,
            "evidence_ids": tuple(str(item) for item in value.evidence_ids),
        }
        for value in decision.current_segment.ordered_beats
        if value.beat_id in verified_beat_ids
    )
    if (
        tuple(value["beat_id"] for value in beats)
        != tuple(
            str(value)
            for value in result.realization_verification.receipt.verified_beat_ids
        )
        or tuple(artifact.realized_beat_ids)
        != result.realization_verification.receipt.verified_beat_ids
    ):
        raise ContractValidationError(
            "accepted artifact beats lack independent realization verification"
        )
    sections = {
        "event": {
            "status": "accepted_turn_realized",
            "participants": tuple(str(value) for value in participants),
            "beats": beats,
            "stop_before": decision.current_segment.stop_before,
        },
        "source_coverage": tuple(
            {
                "source_unit_id": str(value.source_unit_id),
                "ordinal": value.ordinal,
                "preserved_state": value.preserved_state.value,
            }
            for value in result.composer.manifest.source_unit_coverage
        ),
        "artifact_binding": {
            "artifact_id": str(artifact.artifact_id),
            "artifact_sha256": result.receipt.accepted_artifact_sha256,
            "prose_sha256": artifact.prose_sha256,
            "decision_id": str(artifact.decision_id),
            "generation_id": str(artifact.generation_id),
            "source_id": str(artifact.source_id),
            "source_sha256": prepared.request.source_sha256,
        },
    }
    synthetic = snapshot.world_mode is EvidenceWorldMode.SYNTHETIC_FIXTURE
    return EvidenceDocument(
        schema_version=EvidenceDocument.SCHEMA_VERSION,
        record_id=deterministic_id(
            IdKind.EVENT,
            "cera.accepted_turn_event.v1",
            str(artifact.artifact_id),
        ),
        record_version=1,
        record_type=EvidenceRecordType.EVENT_FACT,
        epistemic_class=EvidenceEpistemicClass.OBJECTIVE_FACT,
        truth_status=(TruthStatus.NONCANONICAL if synthetic else TruthStatus.OBJECTIVE),
        title="Accepted turn event",
        abstract="Validated current-segment beats realized by the accepted story artifact.",
        claim="The accepted artifact realized the current-segment event structure bound here.",
        authority=(
            EvidenceAuthority.SYNTHETIC_FIXTURE
            if synthetic
            else EvidenceAuthority.VALIDATED_EVENT
        ),
        subject_ids=participants,
        owner_id=None,
        knowledge_owner_ids=participants,
        visibility=Visibility.SYSTEM_PRIVATE,
        knowledge_route=KnowledgeRoute.DIRECT,
        certainty=Certainty.ESTABLISHED,
        content_class="ordinary",
        genesis_revision_id=snapshot.genesis_revision_id,
        branch_origin_id=snapshot.branch_id,
        valid_from_generation=snapshot.generation + 1,
        valid_to_generation=None,
        source_refs=(prepared.source_record.source_id, artifact.artifact_id),
        supersedes=(),
        tags=("accepted_turn_event", "ordinary", "current_segment"),
        expandable_sections=("event", "source_coverage", "artifact_binding"),
        linked_record_ids=(),
        sections_json=canonical_json(sections),
    )


def _accepted_reply_material(
    result: LiveShapedTurnResult,
    event_record_id,
) -> EvidenceDocument:
    """Preserve exact accepted prose as presentation evidence, not event truth."""

    request = result.context.request
    prepared = request.prepared_turn
    snapshot = prepared.evidence_snapshot
    artifact = result.accepted_artifact
    decision = result.reasoner.outcome.decision
    assert decision is not None
    participants = tuple(
        dict.fromkeys(
            (
                prepared.request.protected_user_id,
                *decision.responding_npc_ids,
            )
        )
    )
    synthetic = snapshot.world_mode is EvidenceWorldMode.SYNTHETIC_FIXTURE
    sections = {
        "accepted_reply": {
            "prose": artifact.accepted_prose,
            "prose_sha256": artifact.prose_sha256,
        },
        "artifact_binding": {
            "artifact_id": str(artifact.artifact_id),
            "generation_id": str(artifact.generation_id),
            "decision_id": str(artifact.decision_id),
            "source_id": str(artifact.source_id),
            "event_record_id": str(event_record_id),
        },
    }
    return EvidenceDocument(
        schema_version=EvidenceDocument.SCHEMA_VERSION,
        record_id=deterministic_id(
            IdKind.MATERIAL,
            "cera.accepted_reply_material.v1",
            str(artifact.artifact_id),
        ),
        record_version=1,
        record_type=EvidenceRecordType.MATERIAL,
        epistemic_class=EvidenceEpistemicClass.OBJECTIVE_FACT,
        truth_status=(
            TruthStatus.NONCANONICAL if synthetic else TruthStatus.OBJECTIVE
        ),
        title="Accepted visible reply",
        abstract=(
            "Exact immutable presentation-neutral reply accepted for this "
            "branch generation."
        ),
        claim=(
            "The accepted_reply section is the exact validated visible reply; "
            "its wording is presentation continuity, not an independent claim "
            "that every narrative phrase is objective event truth."
        ),
        authority=(
            EvidenceAuthority.SYNTHETIC_FIXTURE
            if synthetic
            else EvidenceAuthority.ACCEPTED_SOURCE
        ),
        subject_ids=participants,
        owner_id=None,
        knowledge_owner_ids=participants,
        visibility=Visibility.SYSTEM_PRIVATE,
        knowledge_route=KnowledgeRoute.DIRECT,
        certainty=Certainty.ESTABLISHED,
        content_class="ordinary",
        genesis_revision_id=snapshot.genesis_revision_id,
        branch_origin_id=snapshot.branch_id,
        valid_from_generation=snapshot.generation + 1,
        valid_to_generation=None,
        source_refs=(prepared.source_record.source_id, artifact.artifact_id),
        supersedes=(),
        tags=(
            "accepted_reply",
            "presentation_continuity",
            "immutable_branch_artifact",
        ),
        expandable_sections=("accepted_reply", "artifact_binding"),
        linked_record_ids=(event_record_id,),
        sections_json=canonical_json(sections),
    )


def _accepted_development_atoms(
    result: LiveShapedTurnResult,
    event_record_id,
) -> tuple[EvidenceDocument, ...]:
    """Compile only Sol-verified one-step proposals into owner-private evidence."""

    plan = result.reasoner.outcome.behavioral_scene_plan
    if plan is None or not plan.development_atoms:
        if result.realization_verification.receipt.verified_development_atom_ids:
            raise ContractValidationError(
                "verification retained development without a behavioral plan"
            )
        return ()
    verified_ids = set(
        result.realization_verification.receipt.verified_development_atom_ids
    )
    proposed = {value.atom_id: value for value in plan.development_atoms}
    if not verified_ids.issubset(proposed):
        raise ContractValidationError(
            "verification retained an unknown development proposal"
        )
    request = result.context.request
    snapshot = request.prepared_turn.evidence_snapshot
    artifact = result.accepted_artifact
    synthetic = snapshot.world_mode is EvidenceWorldMode.SYNTHETIC_FIXTURE
    documents = []
    for proposal in plan.development_atoms:
        if proposal.atom_id not in verified_ids:
            continue
        sections = {
            "development_atom": {
                "proposal_id": str(proposal.atom_id),
                "owner_id": str(proposal.owner_character_id),
                "kind": proposal.kind.value,
                "strength_before": proposal.strength_before.value,
                "strength_after": proposal.strength_after.value,
                "summary": proposal.summary,
                "source_event_id": str(event_record_id),
                "source_scene_block_ids": tuple(
                    str(value) for value in proposal.source_scene_block_ids
                ),
                "evidence_record_ids": tuple(
                    str(value) for value in proposal.evidence_record_ids
                ),
                "predecessor_development_ids": tuple(
                    str(value)
                    for value in proposal.predecessor_development_ids
                ),
                "inference_limit": proposal.inference_limit,
                "genesis_effect": "none",
            }
        }
        documents.append(
            EvidenceDocument(
                schema_version=EvidenceDocument.SCHEMA_VERSION,
                record_id=deterministic_id(
                    IdKind.DEVELOPMENT,
                    "cera.accepted_development_atom.v1",
                    f"{artifact.artifact_id}|{proposal.atom_id}",
                ),
                record_version=1,
                record_type=EvidenceRecordType.DEVELOPMENT,
                epistemic_class=EvidenceEpistemicClass.VALIDATED_DERIVED,
                truth_status=(
                    TruthStatus.NONCANONICAL if synthetic else TruthStatus.DERIVED
                ),
                title="Accepted atomic character development",
                abstract=(
                    "One bounded character-owned change supported by a validated "
                    "accepted scene."
                ),
                claim=proposal.summary,
                authority=(
                    EvidenceAuthority.SYNTHETIC_FIXTURE
                    if synthetic
                    else EvidenceAuthority.VALIDATED_DERIVED
                ),
                subject_ids=(proposal.owner_character_id,),
                owner_id=proposal.owner_character_id,
                knowledge_owner_ids=(proposal.owner_character_id,),
                visibility=Visibility.OWNER_PRIVATE,
                knowledge_route=KnowledgeRoute.INFERRED,
                certainty=Certainty.BELIEVED,
                content_class="ordinary",
                genesis_revision_id=snapshot.genesis_revision_id,
                branch_origin_id=snapshot.branch_id,
                valid_from_generation=snapshot.generation + 1,
                valid_to_generation=None,
                source_refs=(event_record_id, artifact.artifact_id),
                supersedes=(),
                tags=(
                    "atomic_development",
                    proposal.kind.value,
                    proposal.strength_after.value,
                ),
                expandable_sections=("development_atom",),
                linked_record_ids=tuple(
                    dict.fromkeys(
                        (
                            *proposal.evidence_record_ids,
                            *proposal.predecessor_development_ids,
                        )
                    )
                ),
                sections_json=canonical_json(sections),
            )
        )
    return tuple(documents)
