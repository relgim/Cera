from __future__ import annotations

import unittest
from dataclasses import dataclass, field

from cera.cognition import (
    CharacterAutonomyMode,
    CognitionPlanV1,
    CognitionTurnContextV1,
    LogicRoute,
    PersistentCognitionPlannerSession,
)
from cera.cognition.prompting import (
    COGNITION_PLANNER_BASE_INSTRUCTIONS,
    COGNITION_PLANNER_PROFILE,
)
from cera.cognition.provider_schema import cognition_plan_json_schema
from cera.pi_scene.cognition_planner import RetainedCognitionPlannerAdapter
from cera.pi_scene.http_contracts import LeanSceneRequestControlsV1
from cera.pi_scene.runtime import PlannerTurnInputV1
from cera.sequence_first.contracts import ProviderReferenceScopeV1

from .test_cognition_contracts import _plan, _turn


def _context() -> CognitionTurnContextV1:
    return CognitionTurnContextV1(
        turn=_turn(),
        autonomy_mode=CharacterAutonomyMode.BOTH,
        logic_route=LogicRoute.ORDINARY,
        available_provisional_record_ids=("provisional:door_claim",),
    )


@dataclass
class _FakeBackend:
    result: CognitionPlanV1
    thread_id: str = "thread:cognition"
    resumable: bool = True
    starts: list[tuple[str, str]] = field(default_factory=list)
    calls: list[tuple[str, str, CognitionTurnContextV1]] = field(default_factory=list)

    def start_stored_thread(self, *, base_instructions: str, profile: str) -> str:
        self.starts.append((base_instructions, profile))
        return self.thread_id

    def run_cognition_turn(
        self,
        *,
        thread_id: str,
        prompt: str,
        context: CognitionTurnContextV1,
        reference_scope: ProviderReferenceScopeV1,
    ) -> CognitionPlanV1:
        del reference_scope
        self.calls.append((thread_id, prompt, context))
        return self.result

    def is_resumable(self, thread_id: str) -> bool:
        return self.resumable and thread_id == self.thread_id


class CognitionProviderContractTests(unittest.TestCase):
    def test_schema_closes_autonomy_and_provisional_scope(self) -> None:
        context = _context()
        schema = cognition_plan_json_schema(
            context=context,
            reference_scope=ProviderReferenceScopeV1.from_turn(context.turn),
        )
        properties = schema["properties"]
        decision = properties["decision_records"]["items"]["properties"]
        autonomy = decision["autonomy_application"]["properties"]
        self.assertIs(autonomy["mind_precedence_applied"]["const"], True)
        self.assertIs(autonomy["body_precedence_applied"]["const"], True)
        provisional = properties["provisional_dependencies"]["items"]["properties"]
        self.assertEqual(
            provisional["provisional_record_id"]["enum"],
            ["provisional:door_claim"],
        )
        rendered = repr(schema)
        for forbidden in ("world_id", "branch_id", "candidate_id", "transaction_id"):
            self.assertNotIn(forbidden, rendered)

    def test_persistent_session_installs_stable_prompt_once(self) -> None:
        backend = _FakeBackend(_plan())
        persisted: list[str] = []
        session = PersistentCognitionPlannerSession(
            backend,
            persist_thread_id=persisted.append,
        )
        self.assertEqual(session.plan(_context()), _plan())
        self.assertEqual(session.plan(_context()), _plan())
        self.assertEqual(
            backend.starts,
            [(COGNITION_PLANNER_BASE_INSTRUCTIONS, COGNITION_PLANNER_PROFILE)],
        )
        self.assertEqual(persisted, ["thread:cognition"])
        self.assertEqual(len(backend.calls), 2)
        self.assertTrue(all("[CURRENT COGNITION TURN]" in call[1] for call in backend.calls))
        self.assertTrue(
            all(COGNITION_PLANNER_BASE_INSTRUCTIONS not in call[1] for call in backend.calls)
        )

    def test_pi_adapter_propagates_global_autonomy_into_cognition(self) -> None:
        backend = _FakeBackend(_plan())
        session = PersistentCognitionPlannerSession(backend)
        adapter = RetainedCognitionPlannerAdapter(session)
        result = adapter.plan(
            PlannerTurnInputV1(
                world_id="world:test",
                branch_id="branch:main",
                scene_id="scene:door",
                exact_user_source="Ted asks Sakura to open the door.",
                current_state={
                    "accepted_present_character_ids": [
                        "character:ted",
                        "character:sakura_hanezawa",
                    ],
                    "explicitly_authorized_remote_character_ids": [],
                    "public_scene_state": "Ted and Sakura are inside by the closed door.",
                    "unresolved_threads": [],
                    "hard_boundaries": [],
                    "approved_targets": [],
                    "durable_changes": [],
                    "provisional_canon_lineage": [],
                },
                characters={"character:sakura_hanezawa": {"name": "Sakura"}},
                relationships={},
                relevant_memories={},
                accepted_records=(),
                request_controls=LeanSceneRequestControlsV1(
                    schema_version=LeanSceneRequestControlsV1.SCHEMA_VERSION,
                    session_id="test-session",
                    character_autonomy="both",
                ),
            )
        )
        self.assertEqual(result.sequence, result.decision_bundle["sequence"])
        self.assertEqual(
            adapter.last_context.autonomy_mode,
            CharacterAutonomyMode.BOTH,
        )


if __name__ == "__main__":
    unittest.main()
