from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from cera.continuous.job4_terminal import (
    ContinuousJob4TerminalEvidenceV5,
    decode_continuous_job4_terminal_evidence,
)
from cera.continuous.scripted_job4 import SCRIPTED_JOB4_FIXTURE_SHA256
from cera.continuous.sessions import (
    ContinuousSessionRole,
    ContinuousThreadArchiveEvidenceV1,
)
from cera.continuous.thread_lineage import (
    ContinuousThreadLineageLedger,
    ContinuousThreadLineageReceiptV1,
)
from cera.errors import ContractValidationError, StateConflictError
from cera.registry import build_schema_registry
from cera.serialization import text_sha256

from tests import test_continuous_branch_materialization as branch_fixture_module


ROOT = Path(__file__).resolve().parents[1]


def _archive_evidence(
    role: ContinuousSessionRole,
    thread_sha256: str,
    *,
    archive_completed: bool = True,
    resume_succeeded: bool | None = False,
    backend_selectable: bool | None = False,
) -> ContinuousThreadArchiveEvidenceV1:
    return ContinuousThreadArchiveEvidenceV1(
        role=role,
        provider_thread_id_sha256=thread_sha256,
        archive_reason_sha256=text_sha256("test terminal disposition"),
        archive_request_completed=archive_completed,
        resume_succeeded_after_archive=resume_succeeded,
        backend_selectable_after_archive=backend_selectable,
        coordinator_selectable_as_accepted_ancestry=False,
        archive_error_type=None if archive_completed else "RuntimeError",
    )


class ContinuousThreadLineageLedgerTests(unittest.TestCase):
    def test_closed_lineage_roundtrip_is_deeply_immutable(self) -> None:
        ledger = ContinuousThreadLineageLedger()
        planner = text_sha256("planner-initial")
        validator = text_sha256("validator-initial")
        child = text_sha256("planner-fork-child")
        common = {
            "world_id": "world-test",
            "session_compatibility_sha256": text_sha256("compatibility"),
        }
        ledger.register_thread(
            role="planner",
            purpose="primary_planner",
            branch_id="main",
            provider_thread_sha256=planner,
            parent_provider_thread_sha256=None,
            creation_operation="create",
            **common,
        )
        ledger.register_thread(
            role="validator",
            purpose="primary_validator",
            branch_id="main",
            provider_thread_sha256=validator,
            parent_provider_thread_sha256=None,
            creation_operation="create",
            **common,
        )
        ledger.register_thread(
            role="planner",
            purpose="accepted_checkpoint_fork_child",
            branch_id="child",
            provider_thread_sha256=child,
            parent_provider_thread_sha256=planner,
            creation_operation="fork",
            **common,
        )
        ledger.record_adoption(
            child,
            reason_sha256=text_sha256("adopt child"),
            superseded_thread_sha256=planner,
        )
        ledger.record_archive(
            _archive_evidence(ContinuousSessionRole.PLANNER, planner)
        )
        receipt = ledger.freeze(
            authorized_active_threads={"planner": child, "validator": validator}
        )
        self.assertEqual(receipt.status, "verified")
        self.assertEqual(
            ContinuousThreadLineageReceiptV1.from_dict(receipt.to_dict()),
            receipt,
        )
        self.assertEqual(build_schema_registry().decode(receipt.to_dict()), receipt)
        self.assertIs(
            ledger.freeze(
                authorized_active_threads={
                    "planner": child,
                    "validator": validator,
                }
            ),
            receipt,
        )
        archived = next(
            entry for entry in receipt.entries if entry.provider_thread_sha256 == planner
        )
        with self.assertRaises(FrozenInstanceError):
            archived.archive_evidence.verified = False  # type: ignore[misc]
        with self.assertRaisesRegex(StateConflictError, "frozen"):
            ledger.record_resume(child)

    def test_unknown_duplicate_orphan_unresolved_and_contradictory_fail_closed(self) -> None:
        common = {
            "role": "planner",
            "purpose": "primary_planner",
            "world_id": "world-test",
            "branch_id": "main",
            "session_compatibility_sha256": text_sha256("compatibility"),
            "parent_provider_thread_sha256": None,
            "creation_operation": "create",
        }
        duplicate = ContinuousThreadLineageLedger()
        thread = text_sha256("duplicate")
        duplicate.register_thread(provider_thread_sha256=thread, **common)
        with self.assertRaisesRegex(StateConflictError, "already registered"):
            duplicate.register_thread(provider_thread_sha256=thread, **common)

        unresolved = ContinuousThreadLineageLedger()
        unresolved.register_thread(provider_thread_sha256=thread, **common)
        unresolved_receipt = unresolved.freeze(authorized_active_threads={})
        self.assertEqual(unresolved_receipt.status, "failed")
        self.assertIn("unresolved_thread", unresolved_receipt.failure_codes)

        unknown_active = ContinuousThreadLineageLedger().freeze(
            authorized_active_threads={"planner": text_sha256("unknown")}
        )
        self.assertIn(
            "unknown_authorized_active_thread", unknown_active.failure_codes
        )

        orphan = ContinuousThreadLineageLedger()
        orphan.register_thread(
            role="planner",
            purpose="accepted_checkpoint_fork_child",
            world_id="world-test",
            branch_id="child",
            session_compatibility_sha256=text_sha256("child compatibility"),
            provider_thread_sha256=text_sha256("orphan child"),
            parent_provider_thread_sha256=text_sha256("missing parent"),
            creation_operation="fork",
        )
        orphan_receipt = orphan.freeze(authorized_active_threads={})
        self.assertIn("orphaned_thread_parent", orphan_receipt.failure_codes)

        contradictory = ContinuousThreadLineageLedger()
        contradictory.register_thread(provider_thread_sha256=thread, **common)
        contradictory.record_archive(
            _archive_evidence(ContinuousSessionRole.PLANNER, thread)
        )
        contradictory_receipt = contradictory.freeze(
            authorized_active_threads={"planner": thread}
        )
        self.assertIn(
            "contradictory_thread_disposition",
            contradictory_receipt.failure_codes,
        )

        resumed = ContinuousThreadLineageLedger()
        resumed_thread = text_sha256("resumed existing thread")
        resumed.register_thread(
            provider_thread_sha256=resumed_thread,
            creation_operation="resume",
            **{key: value for key, value in common.items() if key != "creation_operation"},
        )
        resumed.record_resume(resumed_thread)
        resumed_receipt = resumed.freeze(
            authorized_active_threads={"planner": resumed_thread}
        )
        self.assertEqual(resumed_receipt.status, "verified")

    def test_tamper_extra_fields_and_selectable_archive_are_rejected(self) -> None:
        ledger = ContinuousThreadLineageLedger()
        thread = text_sha256("archived")
        ledger.register_thread(
            role="planner",
            purpose="primary_planner",
            world_id="world-test",
            branch_id="main",
            session_compatibility_sha256=text_sha256("compatibility"),
            provider_thread_sha256=thread,
            parent_provider_thread_sha256=None,
            creation_operation="create",
        )
        ledger.record_archive(
            _archive_evidence(
                ContinuousSessionRole.PLANNER,
                thread,
                backend_selectable=True,
            )
        )
        receipt = ledger.freeze(authorized_active_threads={})
        self.assertEqual(receipt.status, "failed")
        self.assertIn("unverified_archive", receipt.failure_codes)

        raw = receipt.to_dict()
        raw["entries"][0]["unexpected"] = True
        with self.assertRaisesRegex(ContractValidationError, "fields changed"):
            ContinuousThreadLineageReceiptV1.from_dict(raw)


class _Fixture:
    def __enter__(
        self,
    ) -> branch_fixture_module.ContinuousBranchMaterializationTests:
        self.case = branch_fixture_module.ContinuousBranchMaterializationTests(
            methodName="runTest"
        )
        self.case.setUp()
        return self.case

    def __exit__(self, *_args: object) -> None:
        self.case.tearDown()


class ContinuousThreadLifecycleRuntimeTests(unittest.TestCase):
    def _assert_fork_cut_archives_child(self, stage: str, branch: str) -> None:
        with _Fixture() as fixture:
            def failpoint(observed: str) -> None:
                if observed == stage:
                    raise RuntimeError(f"cut:{stage}")

            fixture.runtime._thread_lifecycle_failpoint = failpoint
            target, materialization, branch_receipt = fixture._materialize(branch)
            before_threads = set(fixture.port._parents)
            with self.assertRaisesRegex(RuntimeError, "cut:"):
                fixture.runtime.fork_planner_session_for_branch(
                    target_compatibility=target,
                    materialization_receipt=materialization,
                    branch_receipt=branch_receipt,
                )
            child_threads = set(fixture.port._parents) - before_threads
            self.assertEqual(len(child_threads), 1)
            child_id = child_threads.pop()
            self.assertNotIn(child_id, fixture.port._valid)
            receipt = fixture.runtime.finalize_thread_lineage(
                authorized_active=True
            )
            self.assertEqual(receipt.status, "verified")
            child_entry = next(
                entry
                for entry in receipt.entries
                if entry.provider_thread_sha256 == text_sha256(child_id)
            )
            self.assertEqual(child_entry.terminal_disposition, "verified_archived")

    def test_every_fork_setup_cut_archives_the_physical_child(self) -> None:
        stages = (
            "after_physical_fork_creation",
            "after_summary_delivery_reconstruction",
            "after_child_branch_validation_before_descriptor_append",
            "after_child_descriptor_append",
            "after_child_accepted_reference_save:1",
            "before_child_snapshot_persistence",
            "after_child_snapshot_persistence",
        )
        for index, stage in enumerate(stages, 1):
            with self.subTest(stage=stage):
                self._assert_fork_cut_archives_child(stage, f"cut-child-{index}")

    def test_successful_fork_can_be_adopted_or_terminally_cleaned_up(self) -> None:
        with _Fixture() as fixture:
            target, materialization, branch_receipt = fixture._materialize(
                "adopted-child"
            )
            forked = fixture.runtime.fork_planner_session_for_branch(
                target_compatibility=target,
                materialization_receipt=materialization,
                branch_receipt=branch_receipt,
            )
            prior_sha256 = fixture.parent.ensure_session().provider_thread_id_sha256
            fixture.runtime.adopt_forked_planner_session(
                forked, reason="accepted child route ownership"
            )
            receipt = fixture.runtime.finalize_thread_lineage(
                authorized_active=True
            )
            self.assertEqual(receipt.status, "verified")
            dispositions = {
                entry.provider_thread_sha256: entry.terminal_disposition
                for entry in receipt.entries
            }
            self.assertEqual(dispositions[prior_sha256], "verified_archived")
            self.assertEqual(
                dispositions[
                    forked.coordinator.handle.provider_thread_id_sha256
                ],
                "authorized_active",
            )

        with _Fixture() as fixture:
            target, materialization, branch_receipt = fixture._materialize(
                "auxiliary-child"
            )
            forked = fixture.runtime.fork_planner_session_for_branch(
                target_compatibility=target,
                materialization_receipt=materialization,
                branch_receipt=branch_receipt,
            )
            evidence = fixture.runtime.archive_auxiliary_planner_session(
                forked, reason="auxiliary qualification complete"
            )
            self.assertTrue(evidence.verified)
            self.assertEqual(
                fixture.runtime.finalize_thread_lineage(
                    authorized_active=True
                ).status,
                "verified",
            )

    def test_failure_after_child_adoption_terminalizes_both_planners(self) -> None:
        with _Fixture() as fixture:
            target, materialization, branch_receipt = fixture._materialize(
                "adoption-cut-child"
            )
            forked = fixture.runtime.fork_planner_session_for_branch(
                target_compatibility=target,
                materialization_receipt=materialization,
                branch_receipt=branch_receipt,
            )

            def failpoint(stage: str) -> None:
                if stage == "after_child_adoption_before_return":
                    raise RuntimeError("cut:after_child_adoption_before_return")

            fixture.runtime._thread_lifecycle_failpoint = failpoint
            with self.assertRaisesRegex(RuntimeError, "after_child_adoption"):
                fixture.runtime.adopt_forked_planner_session(
                    forked, reason="test child adoption"
                )
            validator = fixture.runtime.validator_session.ensure_session()
            receipt = fixture.runtime.thread_lineage.freeze(
                authorized_active_threads={
                    "validator": validator.provider_thread_id_sha256
                }
            )
            self.assertEqual(receipt.status, "verified")
            planner_entries = [
                entry for entry in receipt.entries if entry.role == "planner"
            ]
            self.assertEqual(len(planner_entries), 2)
            self.assertTrue(
                all(
                    entry.terminal_disposition == "verified_archived"
                    for entry in planner_entries
                )
            )

    def test_every_reconstruction_cut_archives_the_new_thread(self) -> None:
        stages = (
            "after_physical_reconstruction_creation",
            "after_reconstruction_summary_delivery",
            "after_reconstruction_accepted_reference_save:1",
            "after_reconstruction_adoption_before_return",
        )
        for index, stage in enumerate(stages, 1):
            with self.subTest(stage=stage), _Fixture() as fixture:
                child_branch = f"reconstruction-cut-{index}"
                target = fixture._target(child_branch)
                bundle = fixture._reconstruction_bundle(child_branch)
                materialization = fixture.runtime.materialize_planner_branch(
                    target_compatibility=target,
                    required_character_summaries=bundle.character_summaries,
                )
                branch_receipt = fixture.parent.build_branch_fork_receipt(
                    target,
                    branch_materialization_receipt_sha256=(
                        materialization.receipt_sha256
                    ),
                )

                def failpoint(observed: str) -> None:
                    if observed == stage:
                        raise RuntimeError(f"cut:{stage}")

                fixture.runtime._thread_lifecycle_failpoint = failpoint
                before_threads = set(fixture.port._parents)
                with self.assertRaisesRegex(RuntimeError, "cut:"):
                    fixture.runtime.reconstruct_planner_session(
                        bundle=bundle,
                        expected_compatibility=target,
                        branch_receipt=branch_receipt,
                        materialization_receipt=materialization,
                    )
                rebuilt_threads = set(fixture.port._parents) - before_threads
                self.assertEqual(len(rebuilt_threads), 1)
                rebuilt_id = rebuilt_threads.pop()
                self.assertNotIn(rebuilt_id, fixture.port._valid)
                validator = fixture.runtime.validator_session.ensure_session()
                active = {
                    "validator": validator.provider_thread_id_sha256,
                }
                if not fixture.parent._terminally_archived:
                    active["planner"] = (
                        fixture.parent.ensure_session().provider_thread_id_sha256
                    )
                receipt = fixture.runtime.thread_lineage.freeze(
                    authorized_active_threads=active
                )
                self.assertEqual(receipt.status, "verified")
                rebuilt_entry = next(
                    entry
                    for entry in receipt.entries
                    if entry.provider_thread_sha256 == text_sha256(rebuilt_id)
                )
                self.assertEqual(
                    rebuilt_entry.terminal_disposition, "verified_archived"
                )

    def test_archive_request_resume_and_selectability_failures_are_terminal_failures(self) -> None:
        for failure in ("archive", "resume", "selectable"):
            with self.subTest(failure=failure), _Fixture() as fixture:
                target, materialization, branch_receipt = fixture._materialize(
                    f"archive-failure-{failure}"
                )
                original_archive = fixture.port.archive
                original_resume = fixture.port.resume
                original_selectable = (
                    fixture.port.selectable_as_active_or_accepted_ancestry
                )

                def archive(handle, reason):
                    if failure == "archive" and handle.provider_thread_id not in {
                        fixture.parent.handle.provider_thread_id,
                        fixture.runtime.validator_session.handle.provider_thread_id,
                    }:
                        raise RuntimeError("archive request failed")
                    return original_archive(handle, reason)

                def resume(handle):
                    if (
                        failure == "resume"
                        and handle.provider_thread_id not in fixture.port._valid
                    ):
                        return True
                    return original_resume(handle)

                def selectable(handle):
                    if (
                        failure == "selectable"
                        and handle.provider_thread_id not in fixture.port._valid
                    ):
                        return True
                    return original_selectable(handle)

                fixture.port.archive = archive
                fixture.port.resume = resume
                fixture.port.selectable_as_active_or_accepted_ancestry = selectable

                def failpoint(stage: str) -> None:
                    if stage == "after_child_descriptor_append":
                        raise RuntimeError("force child cleanup")

                fixture.runtime._thread_lifecycle_failpoint = failpoint
                with self.assertRaises(StateConflictError):
                    fixture.runtime.fork_planner_session_for_branch(
                        target_compatibility=target,
                        materialization_receipt=materialization,
                        branch_receipt=branch_receipt,
                    )
                parent = fixture.parent.handle.provider_thread_id_sha256
                validator = (
                    fixture.runtime.validator_session.handle.provider_thread_id_sha256
                )
                receipt = fixture.runtime.thread_lineage.freeze(
                    authorized_active_threads={
                        "planner": parent,
                        "validator": validator,
                    }
                )
                self.assertEqual(receipt.status, "failed")
                self.assertIn("unverified_archive", receipt.failure_codes)


class ContinuousThreadLineageJob4Tests(unittest.TestCase):
    def _run_cli(self, root: Path, *, failpoint: str | None = None):
        cycle = root / "cycle"
        (cycle / "receipts").mkdir(parents=True)
        authorization = "b" * 64
        cycle_id = "cycle:thread-lineage-focused"
        task_id = "task:thread-lineage-focused"
        (cycle / "CYCLE_MANIFEST.json").write_text(
            json.dumps(
                {
                    "cycle_id": cycle_id,
                    "job4": {
                        "task_id": task_id,
                        "authorization_record_sha256": authorization,
                    },
                }
            ),
            encoding="utf-8",
        )
        (cycle / "receipts" / "TRIGGER_SENT.json").write_text(
            "{}\n", encoding="utf-8"
        )
        source_database = root / "source.sqlite3"
        connection = sqlite3.connect(source_database)
        connection.execute("CREATE TABLE qualification(value TEXT)")
        connection.execute("INSERT INTO qualification VALUES ('unchanged')")
        connection.commit()
        connection.close()
        head = subprocess.check_output(
            ("git", "rev-parse", "HEAD"), cwd=ROOT, text=True
        ).strip()
        command = [
            sys.executable,
            str(ROOT / "scripts" / "run_continuous_planner_validator_job4.py"),
            "--confirm-provider-free-scripted-v10",
            "--expected-scripted-fixture-sha256",
            SCRIPTED_JOB4_FIXTURE_SHA256,
            "--cycle-directory",
            str(cycle),
            "--source-database",
            str(source_database),
            "--runtime-root",
            str(root / "runtime"),
            "--expected-checkpoint-sha",
            head,
            "--expected-cycle-id",
            cycle_id,
            "--expected-task-id",
            task_id,
            "--expected-authorization-sha256",
            authorization,
            "--maximum-provider-calls",
            "10",
        ]
        if failpoint is not None:
            command.extend(("--provider-free-test-failpoint", failpoint))
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        detail = json.loads(
            (root / "runtime" / "JOB4_DETAIL.json").read_text(encoding="utf-8")
        )
        return completed, detail

    def test_each_fork_reference_save_cut_archives_child(self) -> None:
        for index in (1, 2):
            with self.subTest(index=index), TemporaryDirectory() as directory:
                completed, detail = self._run_cli(
                    Path(directory).resolve(),
                    failpoint=f"after_child_accepted_reference_save:{index}",
                )
                self.assertEqual(completed.returncode, 1, completed.stderr)
                lineage = ContinuousThreadLineageReceiptV1.from_dict(
                    detail["thread_lineage"]
                )
                self.assertEqual(lineage.status, "verified")
                fork_entries = [
                    entry
                    for entry in lineage.entries
                    if entry.creation_operation == "fork"
                ]
                self.assertEqual(len(fork_entries), 1)
                self.assertEqual(
                    fork_entries[0].terminal_disposition, "verified_archived"
                )

    def test_partial_primary_thread_construction_archives_the_created_planner(self) -> None:
        with TemporaryDirectory() as directory:
            completed, detail = self._run_cli(
                Path(directory).resolve(),
                failpoint="after_primary_planner_creation",
            )
            self.assertEqual(completed.returncode, 1, completed.stderr)
            lineage = ContinuousThreadLineageReceiptV1.from_dict(
                detail["thread_lineage"]
            )
            self.assertEqual(lineage.status, "verified")
            self.assertEqual(len(lineage.entries), 1)
            self.assertEqual(lineage.entries[0].role, "planner")
            self.assertEqual(
                lineage.entries[0].terminal_disposition, "verified_archived"
            )
            self.assertTrue(detail["thread_archival"]["planner"])
            self.assertFalse(detail["thread_archival"]["validator"])

    def test_integrated_multi_lineage_terminal_v5_and_restart_are_stable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            completed, detail = self._run_cli(root)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            terminal = decode_continuous_job4_terminal_evidence(
                detail["terminal_evidence"]
            )
            self.assertIsInstance(terminal, ContinuousJob4TerminalEvidenceV5)
            self.assertEqual(terminal.thread_lineage.status, "verified")
            self.assertGreaterEqual(len(terminal.thread_lineage.entries), 4)
            self.assertTrue(
                all(
                    entry.terminal_disposition == "verified_archived"
                    for entry in terminal.thread_lineage.entries
                )
            )
            active_ledger = ContinuousThreadLineageLedger()
            active_planner = text_sha256("terminal active planner")
            active_ledger.register_thread(
                role="planner",
                purpose="primary_planner",
                world_id="world-test",
                branch_id="main",
                session_compatibility_sha256=text_sha256(
                    "terminal active compatibility"
                ),
                provider_thread_sha256=active_planner,
                parent_provider_thread_sha256=None,
                creation_operation="create",
            )
            invalid_terminal = ContinuousJob4TerminalEvidenceV5.build(
                execution_status=terminal.execution_status,
                provider_calls=terminal.effect_evidence.provider_calls,
                capability_ledger=terminal.capability_ledger,
                capability_boundary_evidence=(
                    terminal.capability_boundary_evidence
                ),
                postconditions=terminal.postconditions,
                thread_archival_evidence=terminal.thread_archival_evidence,
                thread_lineage=active_ledger.freeze(
                    authorized_active_threads={"planner": active_planner}
                ),
            )
            self.assertIn(
                "thread_lineage_retains_active_thread",
                invalid_terminal.failure_codes,
            )
            result_path = root / "cycle" / "source" / "JOB4_RESULT.json"
            before = result_path.read_bytes()
            restarted = subprocess.run(
                completed.args,
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(restarted.returncode, 1, restarted.stderr)
            self.assertIn(
                "already committed", restarted.stderr + restarted.stdout
            )
            self.assertEqual(result_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
