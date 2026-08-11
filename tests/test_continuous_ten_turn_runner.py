from __future__ import annotations

import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from cera.evaluation import RealGenesisSandbox
from cera.evidence import (
    EvidenceAmbiguityPolicy,
    EvidenceQueryPlan,
    EvidenceRecordType,
)
from cera.genesis.hanezawa_builder import CHARACTER_IDS
from cera.providers.codex_sdk_compat import (
    CODEX_SDK_COMPATIBILITY_ID,
    CODEX_SDK_COMPATIBILITY_SOURCE_SHA256,
    EXPECTED_ROUTE_NOTIFICATION_SHA256,
)
from cera.serialization import text_sha256


ROOT = Path(__file__).resolve().parents[1]


class _CapturedCodexTransport:
    instances: list["_CapturedCodexTransport"] = []

    def __init__(self, route, *, workspace: Path, runner=None) -> None:
        self.route = route
        self.workspace = workspace
        self.runner = runner
        self.instances.append(self)


class _CapturedDeepSeekTransport:
    instances: list["_CapturedDeepSeekTransport"] = []

    def __init__(self, route) -> None:
        self.route = route
        self.instances.append(self)


class _RunnerCounters:
    def __init__(self, launches: int, submissions: int) -> None:
        self.process_launch_count = launches
        self.request_submission_count = submissions


class ContinuousTenTurnRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        scripts = str(ROOT / "scripts")
        sys.path.insert(0, scripts)
        try:
            cls.namespace = runpy.run_path(
                str(ROOT / "scripts" / "run_continuous_ten_turn_qualification.py")
            )
        finally:
            sys.path.remove(scripts)
        cls.route = cls.namespace["ROUTE"]
        cls.build_application = staticmethod(cls.namespace["build_application"])
        cls.persistent_session_state = staticmethod(
            cls.namespace["persistent_session_state"]
        )
        cls.sdk_compatibility_metadata = staticmethod(
            cls.namespace["_sdk_compatibility_metadata"]
        )
        cls.main = staticmethod(cls.namespace["main"])

    def setUp(self) -> None:
        _CapturedCodexTransport.instances.clear()
        _CapturedDeepSeekTransport.instances.clear()

    def test_v20_route_is_complete_and_materially_differs_from_v18_and_v19(
        self,
    ) -> None:
        self.assertEqual([value.index for value in self.route], list(range(1, 11)))
        self.assertEqual(len({value.key for value in self.route}), 10)
        self.assertEqual(
            [value.required_memory_id for value in self.route if value.required_memory_id],
            ["T-M03"],
        )
        self.assertEqual([value.index for value in self.route if value.regeneration], [9])
        self.assertEqual(self.route[6].eligible, ("Tomi", "Mia", "Yuuni"))
        self.assertEqual(
            [value.index for value in self.route if value.require_all_eligible],
            [4, 6, 7, 10],
        )
        v18 = (
            ROOT
            / "evaluation"
            / "evidence"
            / "continuous_ten_turn_qualification_2026-07-29_v18"
            / "turns"
        )
        prior = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(v18.glob("*.json"))
        ]
        self.assertEqual(len(prior), 9)
        prior_hashes = {value["raw_message_sha256"] for value in prior}
        v19_turns = (
            ROOT
            / "evaluation"
            / "evidence"
            / "continuous_ten_turn_qualification_2026-07-29_v19"
            / "turns"
        )
        prior_hashes.update(
            json.loads(path.read_text(encoding="utf-8"))["raw_message_sha256"]
            for path in sorted(v19_turns.glob("*.json"))
        )
        self.assertTrue(
            all(text_sha256(value.message) not in prior_hashes for value in self.route)
        )
        route_text = " ".join(value.message for value in self.route)
        self.assertNotIn("shared-device", route_text)
        self.assertNotIn("troubleshooting", route_text)
        self.assertNotIn("coffee-and-bookstore", route_text)
        self.assertIn("stopwatch", route_text)
        self.assertIn("training", route_text)

    def test_build_application_uses_persistent_runner_only_without_mcp(self) -> None:
        reasoner_runner = object()
        verifier_runner = object()
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            self.build_application.__globals__,
            {
                "CodexSDKTransport": _CapturedCodexTransport,
                "CodexStructuredOutputTransport": _CapturedCodexTransport,
                "DeepSeekChatTransport": _CapturedDeepSeekTransport,
            },
        ):
            root = Path(directory)
            for name in ("ordinary", "memory"):
                (root / name / "reasoner").mkdir(parents=True)
                (root / name / "verifier").mkdir()
            self.build_application(
                store=object(),
                service=object(),
                evidence_tools_enabled=False,
                persistent_reasoner_runner=reasoner_runner,
                verifier_runner=verifier_runner,
                workspace_root=root / "ordinary",
                output_dir=root / "output-ordinary",
            )
            self.build_application(
                store=object(),
                service=object(),
                evidence_tools_enabled=True,
                persistent_reasoner_runner=reasoner_runner,
                verifier_runner=verifier_runner,
                workspace_root=root / "memory",
                output_dir=root / "output-memory",
            )
        self.assertEqual(len(_CapturedCodexTransport.instances), 4)
        ordinary_reasoner, ordinary_verifier, memory_reasoner, memory_verifier = (
            _CapturedCodexTransport.instances
        )
        self.assertIs(ordinary_reasoner.runner, reasoner_runner)
        self.assertIs(ordinary_verifier.runner, verifier_runner)
        self.assertIsNone(memory_reasoner.runner)
        self.assertIs(memory_verifier.runner, verifier_runner)
        self.assertEqual(len(_CapturedDeepSeekTransport.instances), 2)
        self.assertTrue(
            all(
                transport.route.model_name == "deepseek-v4-flash"
                for transport in _CapturedDeepSeekTransport.instances
            )
        )

    def test_summary_metadata_binds_current_composite_compatibility_source(
        self,
    ) -> None:
        metadata = self.sdk_compatibility_metadata()
        self.assertIn(
            "_sdk_compatibility_metadata",
            self.main.__code__.co_names,
        )
        self.assertEqual(
            metadata["compatibility_id"],
            CODEX_SDK_COMPATIBILITY_ID,
        )
        self.assertEqual(
            metadata["route_notification_source_sha256"],
            CODEX_SDK_COMPATIBILITY_SOURCE_SHA256,
        )
        self.assertNotEqual(
            metadata["route_notification_source_sha256"],
            EXPECTED_ROUTE_NOTIFICATION_SHA256,
        )

    def test_v20_paraphrase_terms_resolve_tomi_running_memory(self) -> None:
        with RealGenesisSandbox.create(ROOT, revision="v1_2") as sandbox:
            snapshot = sandbox.open_snapshot(
                "continuous-v20-tomi-memory",
                scope=sandbox.character_scope("Tomi"),
            )
            result = sandbox.service.search_query_plan(
                snapshot,
                EvidenceQueryPlan(
                    schema_version=EvidenceQueryPlan.SCHEMA_VERSION,
                    primary_terms=(
                        "movement",
                        "stamina",
                        "competition",
                    ),
                    alternate_term_sets=(
                        ("running", "joy", "open roads"),
                        ("training", "without an audience", "naturally hers"),
                    ),
                    entity_ids=(CHARACTER_IDS["Tomi"],),
                    tags=(),
                    record_types=(EvidenceRecordType.MEMORY,),
                    limit=5,
                    maximum_variants=3,
                    ambiguity_policy=EvidenceAmbiguityPolicy.REQUIRE_UNAMBIGUOUS,
                ),
            )
            expected = sandbox.record_with_payload_value("memory_id", "T-M03")
        self.assertEqual(
            tuple(value.metadata.record_id for value in result.references),
            (expected.record_id,),
        )
        self.assertGreaterEqual(result.receipt.followup_search_count, 1)

    def test_session_evidence_keeps_role_counters_separate(self) -> None:
        state = self.persistent_session_state(
            _RunnerCounters(5, 9),
            _RunnerCounters(10, 10),
        )
        self.assertEqual(
            state,
            {
                "scene_reasoner": {
                    "process_launch_count": 5,
                    "request_submission_count": 9,
                },
                "scene_realization_verifier": {
                    "process_launch_count": 10,
                    "request_submission_count": 10,
                },
            },
        )

    def test_insufficient_budget_stops_before_proof_output_or_provider_setup(self) -> None:
        proof_loader = Mock()
        tree_cleanup_loader = Mock()
        completion_registration_loader = Mock()
        relational_boundary_loader = Mock()
        cli_verifier_loader = Mock()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "must-not-exist"
            argv = [
                "run_continuous_ten_turn_qualification.py",
                "--confirm-live",
                "--output-dir",
                str(output),
            ]
            with patch.dict(
                self.main.__globals__,
                {
                    "audit_live_call_budget": lambda **_kwargs: {
                        "may_begin_next_ten_turn_route": False,
                        "remaining": {"sol": 10, "deepseek": 7},
                    },
                    "load_persistent_codex_qualification": proof_loader,
                    "load_persistent_codex_tree_cleanup_qualification": tree_cleanup_loader,
                    "load_persistent_codex_completion_registration_qualification": (
                        completion_registration_loader
                    ),
                    "load_codex_cli_verifier_qualification": cli_verifier_loader,
                    "load_relational_boundary_qualification": (
                        relational_boundary_loader
                    ),
                },
            ), patch.object(sys, "argv", argv):
                with self.assertRaisesRegex(RuntimeError, "insufficient"):
                    self.main()
            self.assertFalse(output.exists())
        proof_loader.assert_not_called()
        tree_cleanup_loader.assert_not_called()
        completion_registration_loader.assert_not_called()
        relational_boundary_loader.assert_not_called()
        cli_verifier_loader.assert_not_called()

    def test_completion_registration_proof_failure_stops_before_output(self) -> None:
        proof_loader = Mock(return_value=object())
        tree_cleanup_loader = Mock(return_value=object())
        completion_registration_loader = Mock(
            side_effect=RuntimeError("completion registration proof rejected")
        )
        relational_boundary_loader = Mock()
        cli_verifier_loader = Mock()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "must-not-exist"
            argv = [
                "run_continuous_ten_turn_qualification.py",
                "--confirm-live",
                "--output-dir",
                str(output),
            ]
            with patch.dict(
                self.main.__globals__,
                {
                    "audit_live_call_budget": lambda **_kwargs: {
                        "may_begin_next_ten_turn_route": True,
                        "remaining": {"sol": 420, "deepseek": 470},
                    },
                    "load_persistent_codex_qualification": proof_loader,
                    "load_persistent_codex_tree_cleanup_qualification": (
                        tree_cleanup_loader
                    ),
                    "load_persistent_codex_completion_registration_qualification": (
                        completion_registration_loader
                    ),
                    "load_codex_cli_verifier_qualification": cli_verifier_loader,
                    "load_relational_boundary_qualification": (
                        relational_boundary_loader
                    ),
                },
            ), patch.object(sys, "argv", argv):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "completion registration proof rejected",
                ):
                    self.main()
            self.assertFalse(output.exists())
        proof_loader.assert_called_once()
        tree_cleanup_loader.assert_called_once()
        completion_registration_loader.assert_called_once()
        relational_boundary_loader.assert_not_called()
        cli_verifier_loader.assert_not_called()

    def test_relational_boundary_proof_failure_stops_before_output(self) -> None:
        proof_loader = Mock(return_value=object())
        tree_cleanup_loader = Mock(return_value=object())
        completion_registration_loader = Mock(return_value=object())
        cli_verifier_loader = Mock(return_value=object())
        relational_boundary_loader = Mock(
            side_effect=RuntimeError("relational boundary proof rejected")
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "must-not-exist"
            argv = [
                "run_continuous_ten_turn_qualification.py",
                "--confirm-live",
                "--output-dir",
                str(output),
            ]
            with patch.dict(
                self.main.__globals__,
                {
                    "audit_live_call_budget": lambda **_kwargs: {
                        "may_begin_next_ten_turn_route": True,
                        "remaining": {"sol": 419, "deepseek": 468},
                    },
                    "load_persistent_codex_qualification": proof_loader,
                    "load_persistent_codex_tree_cleanup_qualification": (
                        tree_cleanup_loader
                    ),
                    "load_persistent_codex_completion_registration_qualification": (
                        completion_registration_loader
                    ),
                    "load_codex_cli_verifier_qualification": cli_verifier_loader,
                    "load_relational_boundary_qualification": (
                        relational_boundary_loader
                    ),
                },
            ), patch.object(sys, "argv", argv):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "relational boundary proof rejected",
                ):
                    self.main()
            self.assertFalse(output.exists())
        proof_loader.assert_called_once()
        tree_cleanup_loader.assert_called_once()
        completion_registration_loader.assert_called_once()
        cli_verifier_loader.assert_called_once()
        relational_boundary_loader.assert_called_once()


if __name__ == "__main__":
    unittest.main()
