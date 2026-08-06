"""Provider-free raw-chat/accepted-world bridge for sequence-first Stage 6.

The bridge freezes the latest user source, loads physical presence only from
the accepted branch head or explicit scene-initialization authority, and then
hands a typed request to the additive sequence-first adapter.  It performs no
name, alias, mention, retrieval, cast, responder, or salience inference.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from cera.errors import ContractValidationError
from cera.sequence_first.accepted_world import AcceptedWorldAuthorityAssemblerPort
from cera.sequence_first.contracts import (
    PrimarySequenceStatus,
    SequenceFirstPlannedTerminalArtifactV1,
    SequenceFirstTurnRequestV1,
)
from cera.serialization import canonical_json, canonical_sha256, text_sha256
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
class SequenceFirstStage6TurnCustodyV1:
    """Python-issued identities and exact-source policy for one raw request."""

    request_id: str
    candidate_id: str
    turn_id: str
    transaction_id: str
    current_source_key: str


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
        authority_assembler: AcceptedWorldAuthorityAssemblerPort,
    ) -> None:
        self._adapter = adapter
        self._transaction = transaction
        self._authority_assembler = authority_assembler

    def accepted_generation(self, *, world_id: str, branch_id: str) -> int:
        return self._transaction.accepted_generation(
            world_id=world_id,
            branch_id=branch_id,
        )

    def prepare(
        self,
        *,
        raw_request: Mapping[str, Any],
        world_id: str,
        branch_id: str,
        custody: SequenceFirstStage6TurnCustodyV1,
        scene_initialization: ExplicitSceneInitializationV1 | None = None,
    ) -> SequenceFirstPreparedStage6TurnV1:
        parsed = SillyTavernChatRequest.from_mapping(raw_request)
        authority = self._authority_assembler.assemble(
            world_id=world_id,
            branch_id=branch_id,
        )
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
            # Stage 6 never wraps a whole raw message as protected authority.
            # Ted-owned Planner items must cite exact source quotes, which the
            # semantic contract verifies as bounded substrings.
            protected_source_claims=(),
            hard_boundaries=authority.hard_boundaries,
            scene_reinitialization=scene_reinitialization,
        )
        accepted_state = AcceptedSceneStateV1(
            world_id=world_id,
            branch_id=branch_id,
            scene_id=scene_id,
            parent_accepted_turn_id=accepted_head.accepted_turn_id,
            accepted_head_sha256=accepted_head.active_head_sha256,
            known_character_ids=authority.known_character_ids,
            accepted_present_character_ids=present,
            explicitly_authorized_remote_character_ids=(
                authority.explicitly_authorized_remote_character_ids
            ),
            current_public_scene_state=public_state,
            prior_realized_sequence=prior_sequence,
            character_deltas=authority.character_deltas,
            evidence_records=authority.evidence_records,
            approved_targets=authority.approved_targets,
            persistence_targets=authority.persistence_targets,
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
    ) -> SequenceFirstRunResultV1:
        return self._adapter.generate(
            ingress=prepared.ingress,
            accepted_state=prepared.accepted_state,
        )

    def retain_planned_terminal(
        self,
        prepared: SequenceFirstPreparedStage6TurnV1,
        result: SequenceFirstRunResultV1,
        *,
        provider_calls: int,
        provider_evidence_json: str,
    ) -> SequenceFirstPlannedTerminalArtifactV1:
        if result.candidate is not None:
            raise ContractValidationError(
                "qualified Stage 6 result cannot become a planned terminal"
            )
        if result.intended_sequence.custody != prepared.request.custody:
            raise ContractValidationError(
                "planned Stage 6 result changed prepared request custody"
            )
        # Parse and canonicalize once so the retained hash binds JSON rather
        # than caller formatting.
        canonical_provider_evidence = canonical_json(
            json.loads(provider_evidence_json)
        )
        artifact = SequenceFirstPlannedTerminalArtifactV1(
            schema_version=SequenceFirstPlannedTerminalArtifactV1.SCHEMA_VERSION,
            request=prepared.request,
            request_binding_sha256=canonical_sha256(prepared.request),
            intended_sequence=result.intended_sequence,
            intended_sequence_binding_sha256=(
                result.intended_sequence.binding_sha256
            ),
            primary_sequence_status=PrimarySequenceStatus.PLANNED,
            attempt_receipts=result.attempt_receipts,
            terminal_validator_decision=result.terminal_validator_decision,
            terminal_reader_verdict=result.terminal_reader_verdict,
            provider_calls=provider_calls,
            provider_evidence_json_sha256=text_sha256(
                canonical_provider_evidence
            ),
        )
        self._transaction.write_planned_terminal(artifact)
        return artifact

    def load_planned_terminal(
        self,
        *,
        world_id: str,
        branch_id: str,
        turn_id: str,
    ) -> SequenceFirstPlannedTerminalArtifactV1:
        return self._transaction.load_planned_terminal(
            world_id=world_id,
            branch_id=branch_id,
            turn_id=turn_id,
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
