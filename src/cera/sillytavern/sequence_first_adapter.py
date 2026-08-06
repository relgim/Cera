"""Provider-free SillyTavern boundary for the additive sequence-first route.

This adapter freezes source and accepted state.  It never parses character
labels or mentions and never creates presence or responder authority.
"""

from __future__ import annotations

from dataclasses import dataclass

from cera.serialization import text_sha256
from cera.sequence_first.contracts import (
    ApprovedTargetV1,
    CharacterDeltaV1,
    EvidenceRecordV1,
    PersistenceTargetCustodyV1,
    ProtectedSourceClaimV1,
    SequenceCustodyEnvelopeV1,
    SequenceDraftV1,
    SequenceFirstTurnRequestV1,
    SequenceFirstTurnSemanticInputV1,
)
from cera.sequence_first.runtime import SequenceFirstCoordinator, SequenceFirstRunResultV1


@dataclass(frozen=True, slots=True)
class AcceptedSceneStateV1:
    world_id: str
    branch_id: str
    scene_id: str
    parent_accepted_turn_id: str | None
    accepted_head_sha256: str | None
    known_character_ids: tuple[str, ...]
    accepted_present_character_ids: tuple[str, ...]
    explicitly_authorized_remote_character_ids: tuple[str, ...]
    current_public_scene_state: str
    prior_realized_sequence: SequenceDraftV1 | None
    character_deltas: tuple[CharacterDeltaV1, ...]
    evidence_records: tuple[EvidenceRecordV1, ...]
    approved_targets: tuple[ApprovedTargetV1, ...]
    persistence_targets: tuple[PersistenceTargetCustodyV1, ...]
    unresolved_threads: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FrozenSillyTavernIngressV1:
    request_id: str
    candidate_id: str
    turn_id: str
    transaction_id: str
    exact_current_source: str
    current_source_key: str
    protected_source_claims: tuple[ProtectedSourceClaimV1, ...]
    hard_boundaries: tuple[str, ...]
    scene_reinitialization: bool = False


class SequenceFirstSillyTavernAdapter:
    """Exact Stage 6 new-route entrypoint; no old cast-selection helper is used."""

    def __init__(self, coordinator: SequenceFirstCoordinator) -> None:
        self._coordinator = coordinator

    @staticmethod
    def prepare_request(
        *,
        ingress: FrozenSillyTavernIngressV1,
        accepted_state: AcceptedSceneStateV1,
    ) -> SequenceFirstTurnRequestV1:
        accepted_presence = accepted_state.accepted_present_character_ids
        semantics = SequenceFirstTurnSemanticInputV1(
            exact_current_source=ingress.exact_current_source,
            current_source_key=ingress.current_source_key,
            protected_source_claims=ingress.protected_source_claims,
            known_character_ids=accepted_state.known_character_ids,
            accepted_present_character_ids=accepted_presence,
            explicitly_authorized_remote_character_ids=(
                accepted_state.explicitly_authorized_remote_character_ids
            ),
            current_public_scene_state=accepted_state.current_public_scene_state,
            prior_realized_sequence=(
                None
                if ingress.scene_reinitialization
                else accepted_state.prior_realized_sequence
            ),
            character_deltas=accepted_state.character_deltas,
            evidence_records=accepted_state.evidence_records,
            approved_targets=accepted_state.approved_targets,
            unresolved_threads=accepted_state.unresolved_threads,
            hard_boundaries=ingress.hard_boundaries,
            scene_reinitialization=ingress.scene_reinitialization,
        )
        custody = SequenceCustodyEnvelopeV1(
            request_id=ingress.request_id,
            candidate_id=ingress.candidate_id,
            world_id=accepted_state.world_id,
            branch_id=accepted_state.branch_id,
            scene_id=accepted_state.scene_id,
            turn_id=ingress.turn_id,
            parent_accepted_turn_id=accepted_state.parent_accepted_turn_id,
            exact_source_sha256=text_sha256(ingress.exact_current_source),
            accepted_head_sha256=accepted_state.accepted_head_sha256,
            transaction_id=ingress.transaction_id,
            persistence_targets=accepted_state.persistence_targets,
        )
        return SequenceFirstTurnRequestV1(
            semantic_input=semantics,
            custody=custody,
        )

    def generate(
        self,
        *,
        ingress: FrozenSillyTavernIngressV1,
        accepted_state: AcceptedSceneStateV1,
    ) -> SequenceFirstRunResultV1:
        request = self.prepare_request(
            ingress=ingress,
            accepted_state=accepted_state,
        )
        return self._coordinator.generate(request)
