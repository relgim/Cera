from __future__ import annotations

from pathlib import Path
import json
import runpy
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ContinuousLiveCallBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        namespace = runpy.run_path(
            str(ROOT / "scripts" / "audit_continuous_live_call_budget.py")
        )
        cls.audit = staticmethod(namespace["audit"])
        cls.default_sol_limit = namespace["DEFAULT_SOL_LIMIT"]
        cls.default_deepseek_limit = namespace["DEFAULT_DEEPSEEK_LIMIT"]

    def test_creator_authorized_default_ceiling_funds_a_complete_route(self) -> None:
        self.assertEqual(self.default_sol_limit, 500)
        self.assertEqual(self.default_deepseek_limit, 500)
        result = self.audit(
            sol_limit=self.default_sol_limit,
            deepseek_limit=self.default_deepseek_limit,
        )
        self.assertEqual(
            result["totals"],
            {
                "sol_dispatches": 500 - result["remaining"]["sol"],
                "deepseek_dispatches": 500 - result["remaining"]["deepseek"],
            },
        )
        self.assertTrue(result["may_begin_next_ten_turn_route"])

    def test_immutable_continuous_evidence_is_counted_conservatively(self) -> None:
        result = self.audit(sol_limit=50, deepseek_limit=25)
        self.assertEqual(
            result["accounting_policy"],
            "provider_stage_started_or_governed_probe_dispatch_started_"
            "counts_as_one_dispatch",
        )
        self.assertEqual(
            sum(
                value["sol_dispatches"]
                for value in result["runs"]
                if value["version"] <= 11
            ),
            36,
        )
        self.assertEqual(
            sum(
                value["deepseek_dispatches"]
                for value in result["runs"]
                if value["version"] <= 11
            ),
            18,
        )
        v12 = next(value for value in result["runs"] if value["version"] == 12)
        self.assertEqual(v12["sol_dispatches"], 9)
        self.assertEqual(v12["deepseek_dispatches"], 4)
        v13 = next(value for value in result["runs"] if value["version"] == 13)
        self.assertEqual(v13["sol_dispatches"], 6)
        self.assertEqual(v13["deepseek_dispatches"], 3)
        probe_sol = sum(
            value["sol_dispatches"] for value in result["transport_probes"]
        )
        self.assertGreaterEqual(result["totals"]["sol_dispatches"], 45 + probe_sol)
        self.assertGreaterEqual(result["totals"]["deepseek_dispatches"], 22)
        self.assertEqual(
            result["remaining"]["sol"],
            50 - result["totals"]["sol_dispatches"],
        )
        self.assertEqual(
            result["remaining"]["deepseek"],
            25 - result["totals"]["deepseek_dispatches"],
        )
        self.assertFalse(result["may_begin_next_ten_turn_route"])
        self.assertEqual(
            [value["version"] for value in result["runs"][:13]],
            list(range(1, 14)),
        )

    def test_more_sol_alone_cannot_bypass_deepseek_budget(self) -> None:
        result = self.audit(sol_limit=100, deepseek_limit=25)
        probe_sol = sum(
            value["sol_dispatches"] for value in result["transport_probes"]
        )
        self.assertEqual(
            result["remaining"]["sol"],
            100 - result["totals"]["sol_dispatches"],
        )
        self.assertEqual(
            result["remaining"]["deepseek"],
            25 - result["totals"]["deepseek_dispatches"],
        )
        self.assertFalse(result["may_begin_next_ten_turn_route"])

    def test_persistent_probe_dispatch_markers_are_counted_conservatively(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence_root = Path(directory)
            probe = (
                evidence_root
                / "persistent_codex_transport_probe_2026-07-29_v7"
            )
            probe.mkdir()
            (probe / "summary.json").write_text(
                json.dumps(
                    {
                        "qualification_id": "probe-fixture",
                        "status": "failed",
                        "calls": [
                            {
                                "index": 1,
                                "evidence_identity": "eval:first",
                                "role": "scene_reasoner",
                                "dispatch_started": True,
                            },
                            {
                                "index": 2,
                                "evidence_identity": "eval:second",
                                "role": "scene_reasoner",
                                "dispatch_started": False,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = self.audit(
                sol_limit=4,
                deepseek_limit=2,
                evidence_root=evidence_root,
            )
        self.assertEqual(
            result["totals"],
            {"sol_dispatches": 1, "deepseek_dispatches": 0},
        )
        self.assertEqual(result["remaining"], {"sol": 3, "deepseek": 2})
        self.assertEqual(
            result["transport_probes"],
            [
                {
                    "version": 7,
                    "qualification_id": "probe-fixture",
                    "status": "failed",
                    "sol_dispatches": 1,
                    "deepseek_dispatches": 0,
                }
            ],
        )

    def test_malformed_persistent_probe_cannot_be_silently_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence_root = Path(directory)
            probe = (
                evidence_root
                / "persistent_codex_transport_probe_2026-07-29_v1"
            )
            probe.mkdir()
            (probe / "summary.json").write_text(
                json.dumps({"calls": "not-a-list"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "calls are malformed"):
                self.audit(
                    sol_limit=50,
                    deepseek_limit=25,
                    evidence_root=evidence_root,
                )

    def test_d150_probe_requires_explicit_category_and_dispatch_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence_root = Path(directory)
            passed = evidence_root / "correction_probe_v1"
            passed.mkdir()
            (passed / "summary.json").write_text(
                json.dumps(
                    {
                        "goal_authority_id": "D-150",
                        "provider_category": "sol",
                        "dispatch_started": True,
                        "status": "passed",
                    }
                ),
                encoding="utf-8",
            )
            prepared_only = evidence_root / "correction_probe_v2"
            prepared_only.mkdir()
            (prepared_only / "summary.json").write_text(
                json.dumps(
                    {
                        "goal_authority_id": "D-150",
                        "provider_category": "deepseek",
                        "dispatch_started": False,
                        "status": "failed_before_dispatch",
                    }
                ),
                encoding="utf-8",
            )
            result = self.audit(
                sol_limit=5,
                deepseek_limit=5,
                evidence_root=evidence_root,
            )
        self.assertEqual(
            result["goal_probes"],
            [
                {
                    "evidence_directory": "correction_probe_v1",
                    "status": "passed",
                    "provider_category": "sol",
                    "dispatches": 1,
                },
                {
                    "evidence_directory": "correction_probe_v2",
                    "status": "failed_before_dispatch",
                    "provider_category": "deepseek",
                    "dispatches": 0,
                },
            ],
        )
        self.assertEqual(
            result["totals"],
            {"sol_dispatches": 1, "deepseek_dispatches": 0},
        )

    def test_governed_multistage_probe_counts_each_started_provider_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence_root = Path(directory)
            probe = evidence_root / "composer_boundary_probe_v1"
            probe.mkdir()
            (probe / "summary.json").write_text(
                json.dumps(
                    {
                        "goal_authority_id": "D-167",
                        "status": "failed",
                        "provider_stages": [
                            {
                                "stage": "deepseek_composition",
                                "provider_category": "deepseek",
                                "dispatch_started": True,
                            },
                            {
                                "stage": "sol_verification",
                                "provider_category": "sol",
                                "dispatch_started": True,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = self.audit(
                sol_limit=5,
                deepseek_limit=5,
                evidence_root=evidence_root,
            )
        self.assertEqual(
            result["totals"],
            {"sol_dispatches": 1, "deepseek_dispatches": 1},
        )
        self.assertEqual(
            result["goal_probes"],
            [
                {
                    "evidence_directory": "composer_boundary_probe_v1",
                    "status": "failed",
                    "provider_dispatches": {"sol": 1, "deepseek": 1},
                    "dispatches": 2,
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
