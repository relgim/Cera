"""Provider-free raw-chat/accepted-world bridge for sequence-first Stage 6.

The bridge freezes the latest user source, loads physical presence only from
the accepted branch head or explicit scene-initialization authority, and then
hands a typed request to the additive sequence-first adapter.  It performs no
name, alias, mention, retrieval, cast, responder, or salience inference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from cera.errors import ContractValidationError
from cera.sequence_first.contracts import (
    ApprovedTargetV1,
    CharacterDeltaV1,
    EvidenceRecordV1,
    PersistenceTargetCustodyV1,
    ProtectedSourceClaimV1,
    SequenceFirstTurnRequestV1,
    VoiceCueV1,
)
from cera.sequence_first.runtime import (
    SequenceFirstCoordinator,
    SequenceFirstRunResultV1,
)
from cera.sequence_first.world import (
    SequenceFirstAcceptedHeadV1,
    SequenceFirstWorldTransaction,
)

from .models import SillyTavernChatRequest
from .sequence_first_adapter import (
    AcceptedSceneStateV1,
    FrozenSillyTavernIngressV1,
    SequenceFirstSillyTavernAdapter,
)


class SequenceFirstPresenceAuthorityError(ContractValidationError):
    """No accepted or explicit authority can establish current presence."""


@dataclass(frozen=True, slots=True)
class ExplicitSceneInitializationV1:
    """Typed creator/Codex scene authority; raw chat cannot manufacture it."""

    scene_id: str
    accepted_present_character_ids: tuple[str, ...]
    current_public_scene_state: str
    unresolved_threads: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.scene_id.strip():
            raise ContractValidationError("scene initialization requires a scene ID")
        if not self.current_public_scene_state.strip():
            raise ContractValidationError(
                "scene initialization requires a public scene state"
            )
        if len(self.accepted_present_character_ids) != len(
            set(self.accepted_present_character_ids)
        ):
            raise ContractValidationError(
                "scene initialization presence contains duplicates"
            )


@dataclass(frozen=True, slots=True)
class SequenceFirstStage6StateProjectionV1:
    """Accepted repository projections that do not decide physical presence."""

    known_character_ids: tuple[str, ...]
    explicitly_authorized_remote_character_ids: tuple[str, ...] = ()
    character_deltas: tuple[CharacterDeltaV1, ...] = ()
    evidence_records: tuple[EvidenceRecordV1, ...] = ()
    approved_targets: tuple[ApprovedTargetV1, ...] = ()
    persistence_targets: tuple[PersistenceTargetCustodyV1, ...] = ()


@dataclass(frozen=True, slots=True)
class SequenceFirstStage6TurnCustodyV1:
    """Python-issued identities and exact-source policy for one raw request."""

    request_id: str
    candidate_id: str
    turn_id: str
    transaction_id: str
    current_source_key: str
    protected_source_claims: tuple[ProtectedSourceClaimV1, ...]
    hard_boundaries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SequenceFirstPreparedStage6TurnV1:
    raw_request: SillyTavernChatRequest
    ingress: FrozenSillyTavernIngressV1
    accepted_head: SequenceFirstAcceptedHeadV1
    accepted_state: AcceptedSceneStateV1
    request: SequenceFirstTurnRequestV1


class SequenceFirstStage6Bridge:
    """Dedicated Stage 6 entrypoint with no historical semantic fallback."""

    def __init__(
        self,
        *,
        adapter: SequenceFirstSillyTavernAdapter,
        transaction: SequenceFirstWorldTransaction,
    ) -> None:
        self._adapter = adapter
        self._transaction = transaction

    def prepare(
        self,
        *,
        raw_request: Mapping[str, Any],
        world_id: str,
        branch_id: str,
        custody: SequenceFirstStage6TurnCustodyV1,
        state_projection: SequenceFirstStage6StateProjectionV1,
        scene_initialization: ExplicitSceneInitializationV1 | None = None,
    ) -> SequenceFirstPreparedStage6TurnV1:
        parsed = SillyTavernChatRequest.from_mapping(raw_request)
        accepted_head = self._transaction.load_accepted_head(
            world_id=world_id,
            branch_id=branch_id,
        )
        if scene_initialization is None:
            if parsed.cera_scene_change:
                raise SequenceFirstPresenceAuthorityError(
                    "raw scene-change control lacks explicit scene initialization"
                )
            if accepted_head.accepted_turn_id is None:
                raise SequenceFirstPresenceAuthorityError(
                    "branch has no accepted presence; explicit scene initialization is required"
                )
            scene_id = accepted_head.scene_id
            present = accepted_head.accepted_present_character_ids
            public_state = accepted_head.current_public_scene_state
            prior_sequence = accepted_head.prior_realized_sequence
            unresolved_threads = accepted_head.unresolved_threads
            scene_reinitialization = False
        else:
            scene_id = scene_initialization.scene_id
            present = scene_initialization.accepted_present_character_ids
            public_state = scene_initialization.current_public_scene_state
            prior_sequence = None
            unresolved_threads = scene_initialization.unresolved_threads
            scene_reinitialization = True

        exact_source = parsed.latest_user_content
        ingress = FrozenSillyTavernIngressV1(
            request_id=custody.request_id,
            candidate_id=custody.candidate_id,
            turn_id=custody.turn_id,
            transaction_id=custody.transaction_id,
            exact_current_source=exact_source,
            current_source_key=custody.current_source_key,
            protected_source_claims=custody.protected_source_claims,
            hard_boundaries=custody.hard_boundaries,
            scene_reinitialization=scene_reinitialization,
        )
        accepted_state = AcceptedSceneStateV1(
            world_id=world_id,
            branch_id=branch_id,
            scene_id=scene_id,
            parent_accepted_turn_id=accepted_head.accepted_turn_id,
            accepted_head_sha256=accepted_head.active_head_sha256,
            known_character_ids=state_projection.known_character_ids,
            accepted_present_character_ids=present,
            explicitly_authorized_remote_character_ids=(
                state_projection.explicitly_authorized_remote_character_ids
            ),
            current_public_scene_state=public_state,
            prior_realized_sequence=prior_sequence,
            character_deltas=state_projection.character_deltas,
            evidence_records=state_projection.evidence_records,
            approved_targets=state_projection.approved_targets,
            persistence_targets=state_projection.persistence_targets,
            unresolved_threads=unresolved_threads,
        )
        request = self._adapter.prepare_request(
            ingress=ingress,
            accepted_state=accepted_state,
        )
        return SequenceFirstPreparedStage6TurnV1(
            raw_request=parsed,
            ingress=ingress,
            accepted_head=accepted_head,
            accepted_state=accepted_state,
            request=request,
        )

    def generate(
        self,
        prepared: SequenceFirstPreparedStage6TurnV1,
        *,
        voice_cues: tuple[VoiceCueV1, ...],
    ) -> SequenceFirstRunResultV1:
        return self._adapter.generate(
            ingress=prepared.ingress,
            accepted_state=prepared.accepted_state,
            voice_cues=voice_cues,
        )

    def accept_and_reload(
        self,
        prepared: SequenceFirstPreparedStage6TurnV1,
        result: SequenceFirstRunResultV1,
        *,
        creator_accepted: bool,
    ) -> SequenceFirstAcceptedHeadV1:
        if result.candidate is None or result.candidate.custody != prepared.request.custody:
            raise ContractValidationError(
                "Stage 6 result does not match the prepared request custody"
            )
        SequenceFirstCoordinator.commit(
            result,
            transaction=self._transaction,
            expected_parent_accepted_turn_id=(
                prepared.accepted_state.parent_accepted_turn_id
            ),
            creator_accepted=creator_accepted,
        )
        return self._transaction.load_accepted_head(
            world_id=prepared.accepted_state.world_id,
            branch_id=prepared.accepted_state.branch_id,
        )
