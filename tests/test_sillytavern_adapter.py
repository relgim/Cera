from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from cera.contracts import AcceptedStoryArtifact, EvidenceRecordType, SceneDepthMode
from cera.genesis.hanezawa_builder import CHARACTER_IDS
from cera.ids import IdKind, TypedId
from cera.runtime import HanezawaHumanTestWorld
from cera.serialization import text_sha256
from cera.sillytavern import (
    CeraSillyTavernAdapter,
    SillyTavernChatRequest,
    SillyTavernTurnReply,
    continuity_seeds,
    contextual_genesis_seeds,
    parse_cera_controls,
    resolve_session_branch,
    select_candidate_cast,
    verifier_continuity_context,
)
from cera.storage import CommitMode, SourceRecord, TurnCommitBundle


ROOT = Path(__file__).resolve().parents[1]
SESSION = "a" * 64


class RecordingExecutor:
    def __init__(self) -> None:
        self.prepared = []

    def execute(
        self,
        prepared,
        *,
        workspace_root: Path,
        reasoning_effort: str = "medium",
    ) -> SillyTavernTurnReply:
        self.prepared.append(prepared)
        self.workspace_root = workspace_root
        self.reasoning_effort = reasoning_effort
        return SillyTavernTurnReply(
            prose="Sakura keeps one hand on the door and waits for Ted to answer.",
            request_id=str(
                prepared.reasoner_request.prepared_turn.request.request_id
            ),
            artifact_id="artifact:fake-sillytavern-accepted",
            generation=1,
            provider_calls=3,
            exact_replay=False,
        )


class SillyTavernAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(
            prefix="cera-sillytavern-adapter-test-"
        )
        cls.world = HanezawaHumanTestWorld.initialize(
            ROOT,
            Path(cls.temporary.name) / "world.sqlite3",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_control_parser_strips_transport_markers_exactly(self) -> None:
        parsed = parse_cera_controls(
            f"[[CERA_SESSION:{SESSION}]]\n"
            "[[CERA_DEPTH:LONG]]\n"
            "[[CERA_REGENERATE:regen_123]]\n"
            "Ted remains outside and asks Sakura where to leave his shoes."
        )
        self.assertEqual(parsed.session_key, SESSION)
        self.assertEqual(parsed.depth, "long")
        self.assertEqual(parsed.regeneration_key, "regen_123")
        self.assertEqual(
            parsed.raw_message,
            "Ted remains outside and asks Sakura where to leave his shoes.",
        )

    def test_initial_candidate_pool_is_household_reachable_not_preselected(self) -> None:
        present, eligible = select_candidate_cast(
            "Ted waits outside without entering.",
            self.world,
        )
        self.assertEqual(present, (self.world.protected_user_id, *CHARACTER_IDS.values()))
        self.assertEqual(eligible, tuple(CHARACTER_IDS.values()))

    def test_explicit_names_do_not_preclude_a_justified_household_entrant(self) -> None:
        present, eligible = select_candidate_cast(
            "Ted asks Sakura whether Mia is available.",
            self.world,
        )
        self.assertEqual(present, (self.world.protected_user_id, *CHARACTER_IDS.values()))
        self.assertEqual(eligible, tuple(CHARACTER_IDS.values()))

    def test_adapter_prepares_raw_turn_through_v2_ingress(self) -> None:
        executor = RecordingExecutor()
        adapter = CeraSillyTavernAdapter(self.world, executor)
        request = SillyTavernChatRequest.from_mapping(
            {
                "model": "cera-alpha",
                "stream": False,
                "messages": [
                    {"role": "system", "content": "Presentation shell only."},
                    {
                        "role": "user",
                        "content": (
                            f"[[CERA_SESSION:{SESSION}]]\n"
                            "[[CERA_DEPTH:AUTO]]\n"
                            "Ted stays outside. \"Hello, I'm Ted.\""
                        ),
                    },
                ],
            }
        )
        reply = adapter.complete(request)
        self.assertEqual(reply.provider_calls, 3)
        self.assertEqual(len(executor.prepared), 1)
        reasoner_request = executor.prepared[0].reasoner_request
        turn = reasoner_request.prepared_turn.request
        self.assertEqual(
            reasoner_request.source_view.units[0].safe_text,
            'Ted stays outside. "Hello, I\'m Ted."',
        )
        self.assertEqual(
            reasoner_request.prepared_turn.present_character_ids,
            (self.world.protected_user_id, *CHARACTER_IDS.values()),
        )
        self.assertEqual(
            reasoner_request.prepared_turn.eligible_responding_npc_ids,
            tuple(CHARACTER_IDS.values()),
        )
        self.assertIn(
            "default to developed ordinary scope",
            reasoner_request.seed_dossier.scene_anchors[-1],
        )
        self.assertEqual(
            executor.prepared[0]
            .application_request.composer_plan.response_profile_version,
            "cera-sillytavern-auto-v6",
        )
        self.assertIs(reasoner_request.scene_depth_mode, SceneDepthMode.AUTO)
        initial_scenario = next(
            value
            for value in reasoner_request.seed_dossier.scene_anchors
            if value.startswith("Python privacy-filtered creator scenario projection:")
        )
        projection = json.loads(initial_scenario.split(": ", 1)[1])
        projected_text = " ".join(value["text"] for value in projection["facts"])
        self.assertIn(
            "The doorbell has just rung",
            projected_text,
        )
        self.assertIn("Sakura will handle the formal arrival", projected_text)
        self.assertNotIn("Tomi is privately curious", projected_text)
        self.assertNotIn("Aoi is outwardly cooperative", projected_text)
        self.assertEqual(
            projection["source_record_id"],
            str(self.world.initial_scenario_record_id),
        )
        self.assertEqual(
            executor.prepared[0]
            .application_request.composer_plan.established_scene_context,
            (initial_scenario,),
        )
        self.assertEqual(continuity_seeds(self.world), ())

    def test_epic_is_a_typed_binding_not_only_a_prompt_marker(self) -> None:
        executor = RecordingExecutor()
        adapter = CeraSillyTavernAdapter(self.world, executor)
        adapter.complete(
            SillyTavernChatRequest.from_mapping(
                {
                    "model": "cera-alpha",
                    "stream": False,
                    "cera_session_id": "st_chat_epic_contract",
                    "cera_scene_depth": "EPIC",
                    "messages": [
                        {"role": "user", "content": "Hello, is this the Hanezawa home?"},
                    ],
                }
            )
        )
        prepared = executor.prepared[0]
        self.assertIs(
            prepared.reasoner_request.scene_depth_mode,
            SceneDepthMode.EPIC,
        )
        self.assertEqual(
            prepared.application_request.composer_plan.response_profile_version,
            "cera-sillytavern-epic-v6",
        )
        self.assertIn(
            "affirmative scope request",
            prepared.reasoner_request.seed_dossier.scene_anchors[-1],
        )

    def test_household_information_cues_prefetch_public_rule_source_text(self) -> None:
        for cue in (
            "Please begin the orientation.",
            "Could you show me the house?",
            "What rules apply in the shared spaces?",
        ):
            with self.subTest(cue=cue):
                seeds = contextual_genesis_seeds(cue, self.world)
                self.assertEqual(
                    tuple(value.record_id for value in seeds),
                    self.world.public_household_rule_record_ids,
                )
                self.assertTrue(
                    all(value.sections == ("claim", "source_text") for value in seeds)
                )
        self.assertEqual(
            contextual_genesis_seeds("Hello, Hana.", self.world),
            (),
        )

    def test_short_and_medium_are_typed_depth_controls(self) -> None:
        for label, expected in (
            ("SHORT", SceneDepthMode.SHORT),
            ("MEDIUM", SceneDepthMode.MEDIUM),
        ):
            with self.subTest(label=label):
                executor = RecordingExecutor()
                CeraSillyTavernAdapter(self.world, executor).complete(
                    SillyTavernChatRequest.from_mapping(
                        {
                            "model": "cera-alpha",
                            "stream": False,
                            "cera_session_id": f"st_chat_{label.casefold()}_contract",
                            "cera_scene_depth": label,
                            "messages": [{"role": "user", "content": "Hello."}],
                        }
                    )
                )
                prepared = executor.prepared[0]
                self.assertIs(prepared.reasoner_request.scene_depth_mode, expected)
                self.assertIn(
                    label.casefold(),
                    prepared.application_request.composer_plan.response_profile_version,
                )

    def test_initial_scenario_projects_private_state_only_for_aware_owner(self) -> None:
        projection = self.world.initial_scenario_projection(
            (CHARACTER_IDS["Tomi"],)
        )
        payload = json.loads(projection.split(": ", 1)[1])
        text = " ".join(value["text"] for value in payload["facts"])
        self.assertIn("Tomi is privately curious", text)
        self.assertNotIn("Enne is aware", text)
        tomi = next(
            value
            for value in payload["facts"]
            if value["text"].startswith("Tomi is privately curious")
        )
        self.assertEqual(
            tomi["scope"],
            f"owner_private:{CHARACTER_IDS['Tomi']}",
        )

    def test_regeneration_excludes_the_candidate_head_from_seed_continuity(self) -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="cera-sillytavern-regeneration-test-"
        )
        self.addCleanup(temporary.cleanup)
        world = HanezawaHumanTestWorld.initialize(
            ROOT,
            Path(temporary.name) / "world.sqlite3",
        )
        self._commit_main_artifact(world, "regeneration-head", "Rejected candidate prose.")
        head = world.store.get_branch(world.branch_id).head_artifact_id
        assert head is not None
        executor = RecordingExecutor()
        CeraSillyTavernAdapter(world, executor).complete(
            SillyTavernChatRequest.from_mapping(
                {
                    "model": "cera-alpha",
                    "stream": False,
                    "cera_session_id": "st_chat_regeneration_contract",
                    "cera_scene_depth": "LONG",
                    "cera_regeneration_key": "regen_contract_1",
                    "messages": [
                        {"role": "assistant", "content": "Rejected candidate prose."},
                        {"role": "user", "content": "Please answer the same cue differently."},
                    ],
                }
            )
        )
        prepared = executor.prepared[0]
        record_types = {
            value.metadata.record_type
            for value in prepared.reasoner_request.seed_dossier.exact_seed_evidence
        }
        self.assertEqual(
            record_types,
            {EvidenceRecordType.RELATIONSHIP, EvidenceRecordType.GENESIS_FACT},
        )
        self.assertEqual(
            continuity_seeds(
                world,
                exclude_artifact_id=head,
            ),
            (),
        )
        self.assertEqual(
            verifier_continuity_context(
                world,
                exclude_artifact_id=head,
            ),
            (),
        )

    def test_accepted_reply_is_bounded_verifier_continuity(self) -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="cera-sillytavern-verifier-continuity-test-"
        )
        self.addCleanup(temporary.cleanup)
        world = HanezawaHumanTestWorld.initialize(
            ROOT,
            Path(temporary.name) / "world.sqlite3",
        )
        accepted = "Sakura leaves Ted's suitcase at the entry and directs him to wait."
        self._commit_main_artifact(world, "verifier-continuity", accepted)
        contexts = verifier_continuity_context(world)
        self.assertEqual(len(contexts), 1)
        self.assertIn("immutable presentation continuity", contexts[0])
        self.assertIn(accepted, contexts[0])

    def test_typed_request_controls_do_not_require_prompt_markers(self) -> None:
        executor = RecordingExecutor()
        adapter = CeraSillyTavernAdapter(self.world, executor)
        request = SillyTavernChatRequest.from_mapping(
            {
                "model": "cera-alpha",
                "stream": False,
                "cera_session_id": "st_chat_1234567890abcdef",
                "cera_scene_depth": "AUTO",
                "cera_reasoning_effort": "HIGH",
                "messages": [
                    {"role": "user", "content": "Hello, Hanezawa residence?"},
                ],
            }
        )
        adapter.complete(request)
        self.assertEqual(executor.reasoning_effort, "high")
        turn = executor.prepared[0].reasoner_request.prepared_turn.request
        self.assertEqual(
            str(turn.session_id),
            "session:st-st_chat_1234567890abcdef",
        )
        self.assertEqual(
            executor.prepared[0].reasoner_request.source_view.units[0].safe_text,
            "Hello, Hanezawa residence?",
        )

    def test_typed_controls_and_legacy_markers_must_agree(self) -> None:
        with self.assertRaisesRegex(ValueError, "conflicts"):
            parse_cera_controls(
                f"[[CERA_SESSION:{SESSION}]]\nHello.",
                session_key="b" * 64,
                depth="auto",
            )

    def test_sol_effort_is_typed_and_defaults_to_medium(self) -> None:
        self.assertEqual(
            parse_cera_controls("Hello.", session_key=SESSION).reasoning_effort,
            "medium",
        )
        self.assertEqual(
            parse_cera_controls(
                "Hello.", session_key=SESSION, reasoning_effort="XHIGH"
            ).reasoning_effort,
            "xhigh",
        )
        with self.assertRaisesRegex(ValueError, "reasoning effort"):
            parse_cera_controls(
                "Hello.", session_key=SESSION, reasoning_effort="maximum"
            )

    def test_new_chat_gets_an_isolated_root_branch(self) -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="cera-sillytavern-branch-test-"
        )
        self.addCleanup(temporary.cleanup)
        world = HanezawaHumanTestWorld.initialize(
            ROOT,
            Path(temporary.name) / "world.sqlite3",
        )
        accepted = "Sakura confirms the residence and asks who is calling."
        self._commit_main_artifact(world, "existing-chat", accepted)
        executor = RecordingExecutor()
        adapter = CeraSillyTavernAdapter(world, executor)

        existing_request = SillyTavernChatRequest.from_mapping(
            {
                "model": "cera-alpha",
                "stream": False,
                "cera_session_id": "st_chat_existing",
                "messages": [
                    {"role": "assistant", "content": accepted},
                    {"role": "user", "content": "My name is Ted."},
                ],
            }
        )
        existing_branch = resolve_session_branch(
            existing_request,
            parse_cera_controls(
                existing_request.latest_user_content,
                session_key=existing_request.cera_session_id,
            ),
            world,
        )
        self.assertEqual(existing_branch, world.branch_id)

        new_request = SillyTavernChatRequest.from_mapping(
            {
                "model": "cera-alpha",
                "stream": False,
                "cera_session_id": "st_chat_new",
                "messages": [
                    {"role": "assistant", "content": "The doorbell rang."},
                    {"role": "user", "content": "Hello, Hanezawa residence?"},
                ],
            }
        )
        adapter.complete(new_request)
        new_branch = (
            executor.prepared[-1]
            .reasoner_request.prepared_turn.request.branch_id
        )
        self.assertNotEqual(new_branch, world.branch_id)
        self.assertEqual(world.store.get_branch(new_branch).generation, 0)
        self.assertEqual(continuity_seeds(world, branch_id=new_branch), ())
        self.assertEqual(len(world.store.visible_artifact_ids(world.branch_id)), 1)

        adapter.complete(new_request)
        repeated_branch = (
            executor.prepared[-1]
            .reasoner_request.prepared_turn.request.branch_id
        )
        self.assertEqual(repeated_branch, new_branch)

    @staticmethod
    def _commit_main_artifact(
        world: HanezawaHumanTestWorld,
        suffix: str,
        prose: str,
    ) -> None:
        def ident(kind: IdKind, name: str) -> TypedId:
            return TypedId(kind, f"st-{suffix}-{name}")

        source = SourceRecord.from_payload(
            source_id=ident(IdKind.SOURCE, "source"),
            request_id=ident(IdKind.REQUEST, "request"),
            branch_id=world.branch_id,
            payload={"test": suffix},
        )
        transaction_id = ident(IdKind.TRANSACTION, "transaction")
        artifact = AcceptedStoryArtifact(
            schema_version=AcceptedStoryArtifact.SCHEMA_VERSION,
            artifact_id=ident(IdKind.ARTIFACT, "artifact"),
            branch_id=world.branch_id,
            generation_id=ident(IdKind.GENERATION, "generation"),
            parent_artifact_id=None,
            source_id=source.source_id,
            decision_id=ident(IdKind.DECISION, "decision"),
            accepted_prose=prose,
            prose_sha256=text_sha256(prose),
            responding_npc_ids=(CHARACTER_IDS["Sakura"],),
            realized_beat_ids=(ident(IdKind.BEAT, "beat"),),
            validation_receipt_id=ident(IdKind.VALIDATION, "validation"),
            transaction_id=transaction_id,
            status="accepted",
        )
        world.store.commit_turn(
            TurnCommitBundle(
                transaction_id=transaction_id,
                idempotency_key=f"st-{suffix}",
                mode=CommitMode.APPEND,
                branch_id=world.branch_id,
                expected_generation=0,
                expected_head_artifact_id=None,
                source=source,
                artifact=artifact,
                validation_receipt_ids=(artifact.validation_receipt_id,),
            )
        )

    def test_missing_session_marker_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "session identity"):
            parse_cera_controls("[[CERA_DEPTH:AUTO]]\nHello.")


if __name__ == "__main__":
    unittest.main()
