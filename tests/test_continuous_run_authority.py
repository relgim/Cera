from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cera.continuous.run_authority import (
    ProviderBoundaryAuthorityGuardV1,
    ProviderRoleAuthorityV1,
    RunAuthorityV1,
)
from cera.continuous.operation_evidence import ProviderOperationEvidenceStoreV1
from cera.errors import StateConflictError


class EvidenceFake:
    def __init__(self) -> None:
        self.calls = []

    def snapshot(self):
        return {"calls": list(self.calls), "thread_lifecycles": []}


class LedgerFake:
    def __init__(self) -> None:
        self.dispatched_call_count = 0


def authority() -> RunAuthorityV1:
    roles = tuple(
        ProviderRoleAuthorityV1(
            role=role,
            route=f"route:{role}",
            model="gpt-5.6-sol" if role != "writer" else "deepseek-v4-flash",
            effort="medium" if role != "writer" else "non-thinking",
            adapter=f"adapter:{role}",
            prompt=f"prompt:{role}",
            schema=f"schema:{role}",
            instructions_sha256=(str(index) * 64),
        )
        for index, role in enumerate(("planner", "writer", "validator", "reader"), 1)
    )
    return RunAuthorityV1(
        queue_revision="0063",
        active_goal=3,
        run_id="2026-08-06-cera-sequence-first-stage3-live-v8",
        source_commit="a" * 40,
        source_tree="b" * 40,
        world_id="world-v8",
        branch_id="branch-main",
        session_id="session-v8",
        turn_source_sha256="c" * 64,
        profile_sha256="d" * 64,
        roles=roles,
        maximum_writer_attempts=3,
        call_ceilings=(("codex_sol_family", 14), ("deepseek_family", 6)),
        excluded_effects=("production_route_change", "installed_sillytavern_change"),
    )


def pointer(value: RunAuthorityV1, **changes):
    result = {
        "status": "active",
        "pause_lifted": True,
        "queue_revision": value.queue_revision,
        "roadmap_revision": "0008",
        "active_goal": value.active_goal,
        "authorized_identity_after_gates": value.run_id,
        "provider_calls_authorized_now": {
            "codex_sol_family": 14,
            "deepseek_family": 6,
        },
    }
    result.update(changes)
    return result


class RunAuthorityTests(unittest.TestCase):
    def build_guard(self, root: Path):
        value = authority()
        path = root / "pointer.json"
        path.write_text(json.dumps(pointer(value)), encoding="utf-8")
        evidence = EvidenceFake()
        ledger = LedgerFake()
        guard = ProviderBoundaryAuthorityGuardV1(
            pointer_path=path,
            initial_roadmap_revision="0008",
            authority=value,
            evidence=evidence,
            ledger=ledger,
        )
        return value, path, evidence, ledger, guard

    def test_same_revision_and_identical_higher_fingerprint_continue(self) -> None:
        with TemporaryDirectory() as temporary:
            value, path, _evidence, _ledger, guard = self.build_guard(Path(temporary))
            guard.prove_before_provider_call()
            path.write_text(
                json.dumps(
                    pointer(
                        value,
                        roadmap_revision="0009",
                        continue_active_identity_at_safe_boundary=True,
                        run_authority_sha256=value.sha256,
                    )
                ),
                encoding="utf-8",
            )
            guard.prove_before_provider_call()

    def test_fingerprint_changes_for_every_governed_authority_dimension(self) -> None:
        value = authority()
        validator = next(role for role in value.roles if role.role == "validator")
        changed_validator = replace(validator, schema="schema:validator:v2")
        changed_roles = tuple(
            changed_validator if role.role == "validator" else role
            for role in value.roles
        )
        variants = (
            replace(value, queue_revision="0064"),
            replace(value, active_goal=4),
            replace(value, run_id="different-run"),
            replace(value, source_commit="e" * 40),
            replace(value, source_tree="f" * 40),
            replace(value, world_id="world-other"),
            replace(value, branch_id="branch-other"),
            replace(value, session_id="session-other"),
            replace(value, turn_source_sha256="e" * 64),
            replace(value, profile_sha256="f" * 64),
            replace(value, roles=changed_roles),
            replace(value, maximum_writer_attempts=2),
            replace(
                value,
                call_ceilings=(("codex_sol_family", 13), ("deepseek_family", 6)),
            ),
            replace(
                value,
                excluded_effects=(
                    "production_route_change",
                    "installed_sillytavern_change",
                    "remote_operation",
                ),
            ),
        )
        self.assertTrue(all(candidate.sha256 != value.sha256 for candidate in variants))

    def test_higher_revision_requires_explicit_identical_fingerprint(self) -> None:
        with TemporaryDirectory() as temporary:
            value, path, _evidence, _ledger, guard = self.build_guard(Path(temporary))
            variants = (
                {"continue_active_identity_at_safe_boundary": False, "run_authority_sha256": value.sha256},
                {"continue_active_identity_at_safe_boundary": True, "run_authority_sha256": "f" * 64},
                {"continue_active_identity_at_safe_boundary": True},
            )
            for changes in variants:
                with self.subTest(changes=changes):
                    path.write_text(
                        json.dumps(pointer(value, roadmap_revision="0009", **changes)),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(StateConflictError, "identical explicit"):
                        guard.prove_before_provider_call()

    def test_pause_identity_goal_queue_and_reduced_ceiling_stop(self) -> None:
        with TemporaryDirectory() as temporary:
            value, path, _evidence, _ledger, guard = self.build_guard(Path(temporary))
            variants = (
                ({"pause_lifted": False}, "paused or inactive"),
                ({"authorized_identity_after_gates": "different-run"}, "run identity"),
                ({"active_goal": 4}, "goal changed"),
                ({"queue_revision": "0064"}, "queue changed"),
            )
            for changes, message in variants:
                with self.subTest(changes=changes):
                    path.write_text(json.dumps(pointer(value, **changes)), encoding="utf-8")
                    with self.assertRaisesRegex(StateConflictError, message):
                        guard.prove_before_provider_call()
            path.write_text(
                json.dumps(
                    pointer(
                        value,
                        provider_calls_authorized_now={
                            "codex_sol_family": 1,
                            "deepseek_family": 0,
                        },
                    )
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(StateConflictError, "below submitted"):
                guard.prove_before_provider_call(
                    submitted_calls_by_family={
                        "codex_sol_family": 1,
                        "deepseek_family": 1,
                    }
                )

    def test_update_during_call_finishes_terminal_evidence_then_gates_next(self) -> None:
        with TemporaryDirectory() as temporary:
            value, path, evidence, ledger, guard = self.build_guard(Path(temporary))
            guard.prove_before_provider_call()
            path.write_text(
                json.dumps(pointer(value, roadmap_revision="0009")),
                encoding="utf-8",
            )
            evidence.calls.append({"call_id": "call-1", "terminal": True})
            ledger.dispatched_call_count = 1
            with self.assertRaisesRegex(StateConflictError, "identical explicit"):
                guard.prove_before_provider_call()
            self.assertTrue(evidence.calls[0]["terminal"])
            self.assertEqual(ledger.dispatched_call_count, 1)

    def test_v7_authority_change_before_validator_preserves_uncalled_thread(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            value = authority()
            pointer_path = root / "pointer.json"
            pointer_path.write_text(json.dumps(pointer(value)), encoding="utf-8")
            evidence = ProviderOperationEvidenceStoreV1(
                root / "operation_evidence",
                stage="v7-exact-authority-change-fixture",
            )
            evidence.begin_turn("turn-0001")
            ledger = LedgerFake()
            guard = ProviderBoundaryAuthorityGuardV1(
                pointer_path=pointer_path,
                initial_roadmap_revision="0008",
                authority=value,
                evidence=evidence,
                ledger=ledger,
            )
            guard.prove_before_provider_call()
            pointer_path.write_text(
                json.dumps(pointer(value, roadmap_revision="0009")),
                encoding="utf-8",
            )
            validator_thread_sha256 = "9" * 64

            with self.assertRaisesRegex(
                StateConflictError,
                "identical explicit run authority",
            ):
                try:
                    guard.prove_before_provider_call()
                finally:
                    evidence.record_archival(
                        role="validator",
                        archived=True,
                        resumable=False,
                        disposition="archived_before_provider_dispatch",
                        thread_identity_sha256=validator_thread_sha256,
                    )

            snapshot = evidence.snapshot()
            self.assertEqual(snapshot["calls"], [])
            self.assertEqual(ledger.dispatched_call_count, 0)
            self.assertEqual(len(snapshot["thread_lifecycles"]), 1)
            self.assertFalse(
                snapshot["thread_lifecycles"][0]["value"]["provider_dispatched"]
            )
            self.assertFalse((root / "validator_operation_0001").exists())

    def test_call_count_and_terminal_evidence_must_match(self) -> None:
        with TemporaryDirectory() as temporary:
            _value, _path, evidence, ledger, guard = self.build_guard(Path(temporary))
            evidence.calls.append({"call_id": "call-1", "terminal": False})
            with self.assertRaisesRegex(StateConflictError, "not terminal"):
                guard.prove_before_provider_call()
            evidence.calls[0]["terminal"] = True
            with self.assertRaisesRegex(StateConflictError, "counts differ"):
                guard.prove_before_provider_call()
            ledger.dispatched_call_count = 1
            guard.prove_before_provider_call()


if __name__ == "__main__":
    unittest.main()
