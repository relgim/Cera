"""Bounded branch context projection for the initial Pi Scene profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import to_primitive

from .branch_state import (
    AcceptedBranchContextSource,
    BranchStateReducerV1,
    GenesisBranchStateV1,
)
from .contracts import SceneRoute
from .runtime import LeanSceneTurnInputV1


@dataclass(frozen=True, slots=True)
class PiSceneContextSeedV1:
    world_id: str
    branch_id: str
    scene_id: str
    accepted_present_character_ids: tuple[str, ...]
    public_scene_state: str
    characters: Mapping[str, Mapping[str, object]]
    relationships: Mapping[str, Mapping[str, object]]
    relevant_memories: Mapping[str, Mapping[str, object]]
    voice_examples: Mapping[str, Mapping[str, object] | str]
    ordinary_craft_index: Mapping[str, object]
    adult_craft_index: Mapping[str, object]
    adult_handoff: Mapping[str, object]
    genesis_revision: str = "cera.pi_scene.context_seed.v1"

    def __post_init__(self) -> None:
        for value in (
            self.world_id,
            self.branch_id,
            self.scene_id,
            self.public_scene_state,
            self.genesis_revision,
        ):
            if not isinstance(value, str) or not value.strip():
                raise ContractValidationError("Pi Scene context seed is incomplete")
        if not self.accepted_present_character_ids:
            raise ContractValidationError("Pi Scene context seed requires explicit presence")
        if not self.characters:
            raise ContractValidationError("Pi Scene context seed requires characters")


class AcceptedBranchContextProvider:
    """Build each turn from immutable accepted receipts and derived records."""

    def __init__(
        self,
        store: AcceptedBranchContextSource,
        seed: PiSceneContextSeedV1,
    ) -> None:
        self.store = store
        self.seed = seed
        self.reducer = BranchStateReducerV1(
            GenesisBranchStateV1.from_mappings(
                world_id=seed.world_id,
                genesis_revision=seed.genesis_revision,
                scene_id=seed.scene_id,
                accepted_present_character_ids=seed.accepted_present_character_ids,
                resulting_public_state=seed.public_scene_state,
                characters=seed.characters,
                relationships=seed.relationships,
                memories=seed.relevant_memories,
            )
        )

    def __call__(
        self,
        route: SceneRoute,
        source: str,
        messages: Sequence[Mapping[str, str]],
    ) -> LeanSceneTurnInputV1:
        del messages
        accepted = tuple(
            self.store.accepted_branch_payloads(
                world_id=self.seed.world_id,
                branch_id=self.seed.branch_id,
            )
        )
        checkpoint = self.reducer.reduce(
            branch_id=self.seed.branch_id,
            payloads=accepted,
            scene_id=self.seed.scene_id,
        )
        if (
            route is SceneRoute.ORDINARY
            and checkpoint.pending_adult_projection_turn_ids
        ):
            raise StateConflictError(
                "ordinary Codex continuity requires the pending adult projection"
            )
        recent = (
            self.store.recent_ordinary_context_payloads(
                world_id=self.seed.world_id,
                branch_id=self.seed.branch_id,
                limit=6,
            )
            if route is SceneRoute.ORDINARY
            else self.store.recent_adult_context_payloads(
                world_id=self.seed.world_id,
                branch_id=self.seed.branch_id,
                limit=6,
            )
        )
        recent_prose_values: list[str] = []
        for value in recent:
            receipt = value.get("receipt")
            if not isinstance(receipt, Mapping):
                continue
            accepted_prose = receipt.get("exact_accepted_prose")
            if not isinstance(accepted_prose, str):
                continue
            # The store has already separated the ordinary/Codex-safe and
            # protected-adult views.  Adult exact prose is reachable only
            # through the DeepSeek-owner API and never through the reducer.
            recent_prose_values.append(accepted_prose)
        recent_prose = tuple(recent_prose_values)
        current_state: dict[str, Any] = {
            "accepted_present_character_ids": list(
                checkpoint.accepted_present_character_ids
            ),
            "public_scene_state": checkpoint.resulting_public_state,
            "unresolved_threads": list(checkpoint.unresolved_threads),
            "durable_changes": [
                to_primitive(value) for value in checkpoint.durable_changes
            ],
            "adult_public_continuity": [
                to_primitive(value) for value in checkpoint.adult_public_continuity
            ],
            "accepted_lineage": {
                "authority": "accepted_branch_only",
                "accepted_entries": [
                    to_primitive(value) for value in checkpoint.accepted_lineage
                ],
                "next_parent_accepted_turn_id": checkpoint.last_accepted_turn_id,
                "next_parent_accepted_head_sha256": (
                    checkpoint.last_accepted_receipt_sha256
                ),
            },
            "provisional_canon_lineage": [
                {
                    "lineage_entry_id": value.lineage_entry_id,
                    "provisional_canon_id": value.provisional_canon_id,
                    "parent_lineage_entry_id": value.parent_lineage_entry_id,
                    "status": value.status,
                    "authority_id": value.authority_id,
                }
                for value in checkpoint.provisional_canon_lineage
            ],
            "branch_lineage": [
                to_primitive(value) for value in checkpoint.branch_lineage
            ],
            "accepted_state_checkpoint_sha256": checkpoint.checkpoint_sha256,
            "accepted_state_order": checkpoint.accepted_order,
            "pending_ordinary_recording_turn_ids": list(
                checkpoint.pending_ordinary_recording_turn_ids
            ),
            "pending_adult_projection_turn_ids": list(
                checkpoint.pending_adult_projection_turn_ids
            ),
            "genesis_revision": checkpoint.genesis_revision,
            "genesis_sha256": checkpoint.genesis_sha256,
            "hard_boundaries": [
                "Respect the exact accepted branch and current conversational floor.",
            ],
        }
        return LeanSceneTurnInputV1(
            world_id=self.seed.world_id,
            branch_id=self.seed.branch_id,
            scene_id=checkpoint.scene_id,
            exact_user_source=source,
            current_state=current_state,
            characters=checkpoint.character_mapping(),
            relationships=checkpoint.relationship_mapping(),
            recent_prose=recent_prose,
            relevant_memories=checkpoint.memory_mapping(),
            voice_examples=self.seed.voice_examples,
            craft_index=(
                self.seed.ordinary_craft_index
                if route is SceneRoute.ORDINARY
                else self.seed.adult_craft_index
            ),
            adult_handoff=(
                None if route is SceneRoute.ORDINARY else self.seed.adult_handoff
            ),
        )


def initial_hana_seed() -> PiSceneContextSeedV1:
    """Minimal non-production seed for the bounded readiness smoke."""

    return PiSceneContextSeedV1(
        world_id="world-pi-scene-smoke",
        branch_id="branch-main",
        scene_id="scene-hana-room",
        accepted_present_character_ids=("character:ted", "character:hana"),
        public_scene_state="Ted and Hana are speaking privately in Hana's room.",
        characters={
            "character:hana": {
                "name": "Hana Hanezawa",
                "age": 38,
                "role": "adult household mother",
                "character_logic": (
                    "Warm, trusting, observant, and conflict-averse; she retains "
                    "her own choices, boundaries, private thoughts, and reactions."
                ),
                "voice": "Gentle, attentive, plain-spoken, and emotionally restrained.",
            }
        },
        relationships={
            "ted-hana": {
                "participants": ["character:ted", "character:hana"],
                "summary": "They are building trust and Hana remains attentive to boundaries.",
            }
        },
        relevant_memories={},
        voice_examples={
            "hana": {
                "guidance": "Use warm, concise sentences with small signs of hesitation when uncertain."
            }
        },
        ordinary_craft_index={
            "approved_material": ["dialogue pacing", "domestic staging", "character interiority"]
        },
        adult_craft_index={
            "approved_material": [
                "consensual adult pacing",
                "clear mutual boundaries",
                "character-specific reactions",
            ]
        },
        adult_handoff={
            "schema_version": "cera.pi_scene.adult_handoff.v1",
            "participant_ids": ["character:ted", "character:hana"],
            "all_participants_adults": True,
            "consent_and_capacity": "Both adults explicitly choose and can pause the interaction.",
            "causal_direction": (
                "Continue one mutually chosen intimate beat, preserve Hana's agency, "
                "then return the conversational floor to Ted."
            ),
            "stopping_boundary": "Stop after Hana makes the next choice legible to Ted.",
        },
    )


def initial_hanezawa_doorway_seed() -> PiSceneContextSeedV1:
    """Creator-test seed aligned to the visible SillyTavern doorway greeting."""

    return PiSceneContextSeedV1(
        world_id="world-hanezawa-creator-test",
        branch_id="branch-main",
        scene_id="scene-hanezawa-entryway",
        accepted_present_character_ids=("character:sakura",),
        public_scene_state=(
            "On the evening of September 2, the doorbell has just rung at the "
            "Hanezawa residence. Ted remains outside the closed front door and "
            "has not yet been independently verified. Sakura is the only NPC at "
            "the entryway and owns the immediate conversational floor. Hana is "
            "in the kitchen, Mia is in the common room, and Tomi, Enne, Aoi, and "
            "Yuuni are elsewhere in the house; none of them is in the current "
            "entryway scene merely because the introduction mentioned them."
        ),
        characters={
            "character:sakura": {
                "name": "Sakura Hanezawa",
                "role": "adult eldest daughter and current doorway host",
                "character_logic": (
                    "Composed, observant, and protective of household privacy; "
                    "she separates polite acknowledgment from identity verification."
                ),
                "voice": "Calm, precise, courteous, and firm without hostility.",
            },
            "character:hana": {
                "name": "Hana Hanezawa",
                "age": 38,
                "role": "adult household mother",
                "character_logic": "Warm, trusting, kind, and conflict-averse, while retaining agency.",
                "voice": "Gentle, attentive, and emotionally restrained.",
            },
            "character:mia": {
                "name": "Mia Hanezawa",
                "role": "adult daughter",
                "character_logic": "Warm and socially attentive; do not surface her without scene relevance.",
                "voice": "Friendly and natural.",
            },
            "character:tomi": {
                "name": "Tomi Hanezawa",
                "role": "adult daughter",
                "character_logic": "Energetic and direct; do not surface her without scene relevance.",
                "voice": "Brisk and candid.",
            },
            "character:enne": {
                "name": "Enne Hanezawa",
                "role": "adult daughter",
                "character_logic": "Analytical and private; do not surface her without scene relevance.",
                "voice": "Dry and economical.",
            },
            "character:aoi": {
                "name": "Aoi Hanezawa",
                "role": "adult daughter",
                "character_logic": "Composed and perceptive; do not surface her without scene relevance.",
                "voice": "Measured and understated.",
            },
            "character:yuuni": {
                "name": "Yuuni Hanezawa",
                "role": "adult daughter",
                "character_logic": "Expressive and musical; do not surface her without scene relevance.",
                "voice": "Lively but considerate.",
            },
        },
        relationships={
            "ted-household": {
                "participants": [
                    "character:ted",
                    "character:sakura",
                    "character:hana",
                ],
                "summary": (
                    "Ted claims to be the new tenant, but the household has not "
                    "yet verified his identity or purpose at the door."
                ),
            }
        },
        relevant_memories={},
        voice_examples={
            "sakura": {
                "guidance": (
                    "Acknowledge the visitor politely, request purpose or intended "
                    "contact, and disclose no unnecessary household information."
                )
            }
        },
        ordinary_craft_index={
            "approved_material": [
                "doorway staging",
                "verification-first dialogue",
                "household privacy",
                "character-specific pacing",
            ]
        },
        adult_craft_index={
            "approved_material": [
                "consensual adult pacing",
                "clear mutual boundaries",
                "character-specific reactions",
            ]
        },
        adult_handoff={
            "schema_version": "cera.pi_scene.adult_handoff.v1",
            "participant_ids": ["character:ted", "character:hana"],
            "all_participants_adults": True,
            "consent_and_capacity": "Both adults explicitly choose and can pause the interaction.",
            "causal_direction": (
                "Continue one mutually chosen intimate beat, preserve Hana's agency, "
                "then return the conversational floor to Ted."
            ),
            "stopping_boundary": "Stop after Hana makes the next choice legible to Ted.",
        },
    )
