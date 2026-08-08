"""Bounded branch context projection for the initial Pi Scene profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from cera.errors import ContractValidationError

from .contracts import SceneRoute
from .runtime import LeanSceneTurnInputV1
from .store import LeanSceneStore


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

    def __post_init__(self) -> None:
        for value in (self.world_id, self.branch_id, self.scene_id, self.public_scene_state):
            if not isinstance(value, str) or not value.strip():
                raise ContractValidationError("Pi Scene context seed is incomplete")
        if not self.accepted_present_character_ids:
            raise ContractValidationError("Pi Scene context seed requires explicit presence")
        if not self.characters:
            raise ContractValidationError("Pi Scene context seed requires characters")


class AcceptedBranchContextProvider:
    """Build each turn from immutable accepted receipts and derived records."""

    def __init__(self, store: LeanSceneStore, seed: PiSceneContextSeedV1) -> None:
        self.store = store
        self.seed = seed

    def __call__(
        self,
        route: SceneRoute,
        source: str,
        messages: Sequence[Mapping[str, str]],
    ) -> LeanSceneTurnInputV1:
        del messages
        accepted = self.store.recent_accepted_payloads(
            world_id=self.seed.world_id,
            branch_id=self.seed.branch_id,
            limit=6,
            adult_full=False,
        )
        recent_prose = tuple(
            value["receipt"]["exact_accepted_prose"]
            for value in accepted
            if isinstance(value.get("receipt"), Mapping)
            and value["receipt"].get("route") == SceneRoute.ORDINARY.value
            and isinstance(value["receipt"].get("exact_accepted_prose"), str)
        )
        public_state = self.seed.public_scene_state
        unresolved: tuple[str, ...] = ()
        if accepted:
            latest = accepted[-1]
            derived = latest.get("ordinary_record") or latest.get("adult_projection")
            if isinstance(derived, Mapping):
                candidate_state = derived.get("resulting_public_state")
                if isinstance(candidate_state, str) and candidate_state.strip():
                    public_state = candidate_state
                candidate_threads = derived.get("unresolved_threads")
                if isinstance(candidate_threads, list) and all(
                    isinstance(value, str) and value.strip() for value in candidate_threads
                ):
                    unresolved = tuple(candidate_threads)
        current_state = {
            "accepted_present_character_ids": list(
                self.seed.accepted_present_character_ids
            ),
            "public_scene_state": public_state,
            "unresolved_threads": list(unresolved),
            "hard_boundaries": [
                "Respect the exact accepted branch and current conversational floor.",
            ],
        }
        return LeanSceneTurnInputV1(
            world_id=self.seed.world_id,
            branch_id=self.seed.branch_id,
            scene_id=self.seed.scene_id,
            exact_user_source=source,
            current_state=current_state,
            characters=self.seed.characters,
            relationships=self.seed.relationships,
            recent_prose=recent_prose,
            relevant_memories=self.seed.relevant_memories,
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
