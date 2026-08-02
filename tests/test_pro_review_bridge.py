from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_SCRIPT = PROJECT_ROOT / "tools" / "pro_review_bridge.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")
CHECKPOINT_ID = "2026-07-31-checkpoint-001"
CHECKPOINT_SHA = "248dfbc969a2961338d8f9b35c61bda4f4e6010b"
BRIDGE_SHA = "0982dabb6e548177d058b81679b8cbf1d6dd7192"

sys.path.insert(0, str(PROJECT_ROOT / "tools"))
import pro_review_cycle as review_cycle  # noqa: E402
import pro_review_cycle_core as review_cycle_core  # noqa: E402
from cera.continuous.scripted_job4 import SCRIPTED_JOB4_FIXTURE_SHA256  # noqa: E402
from cera.continuous.job4_terminal import (  # noqa: E402
    ContinuousJob4OperationalCountersV1,
    ContinuousJob4PostconditionsV1,
    ContinuousJob4TerminalEvidenceV1,
)
from cera.serialization import canonical_bytes  # noqa: E402
from scripts.run_continuous_planner_validator_job4 import (  # noqa: E402
    build_canonical_job4_result,
    build_report,
)


@unittest.skipUnless(POWERSHELL, "Windows PowerShell is required")
class ProReviewBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.checkpoint = self.root / CHECKPOINT_ID
        self.downloads = self.root / "Downloads"
        self.index = self.checkpoint / "EVIDENCE_PACKAGE" / "INDEX.md"
        self.zip_path = self.checkpoint / "CERA_CHECKPOINT_001_EVIDENCE.zip"
        self.first_response = self.checkpoint / "PRO_RESPONSE.md"
        self.destination = self.checkpoint / "PRO_RESPONSE_EVIDENCE_VERIFIED.md"
        self.checkpoint.mkdir(parents=True)
        self.downloads.mkdir()
        self.index.parent.mkdir()
        (self.checkpoint / "REQUEST.md").write_text(
            "# Review request\n\n"
            f"ending_checkpoint_sha: `{CHECKPOINT_SHA}`\n",
            encoding="utf-8",
        )
        self.index.write_text("# Evidence index\n", encoding="utf-8")
        (self.checkpoint / "TASK4_RESULT.md").write_text(
            f"bridge_task_sha: `{BRIDGE_SHA}`\n", encoding="utf-8"
        )
        with zipfile.ZipFile(self.zip_path, "w") as archive:
            archive.writestr("INDEX.md", "checkpoint evidence\n")
        self.zip_sha = hashlib.sha256(self.zip_path.read_bytes()).hexdigest()
        (self.checkpoint / f"{self.zip_path.name}.sha256").write_text(
            f"{self.zip_sha}  {self.zip_path.name}\n", encoding="utf-8"
        )
        self.first_response_bytes = b"# Preserved evidence-blocked response\n"
        self.first_response.write_bytes(self.first_response_bytes)
        self.expected_response_name = (
            f"CERA_PRO_RESPONSE_{CHECKPOINT_ID}_{CHECKPOINT_SHA[:12]}.md"
        )
        self.expected_response = self.downloads / self.expected_response_name

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_bridge(
        self,
        mode: str,
        *,
        check: bool = True,
        max_polls: int = 1,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            str(POWERSHELL),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(BRIDGE_SCRIPT),
            "-Mode",
            mode,
            "-CheckpointDirectory",
            str(self.checkpoint),
            "-DownloadsDirectory",
            str(self.downloads),
            "-PollSeconds",
            "1",
            "-MaxPolls",
            str(max_polls),
            "-StabilityDelayMilliseconds",
            "0",
        ]
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        if check and result.returncode != 0:
            self.fail(
                f"bridge {mode} failed ({result.returncode})\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def export(self) -> dict[str, object]:
        self.run_bridge("Export")
        return json.loads(
            (self.checkpoint / "BRIDGE_EXPORT_RECEIPT.json").read_text(
                encoding="utf-8-sig"
            )
        )

    def response_bytes(
        self,
        *,
        checkpoint_sha: str = CHECKPOINT_SHA,
        zip_sha: str | None = None,
        scope: str = "evidence_verified",
        disposition: str = "accepted",
        body: str = (
            "The actual source, tests, hashes, route metadata, and effects were "
            "inspected. The review remains advisory and does not authorize any "
            "implementation, provider call, publication, or route change."
        ),
    ) -> bytes:
        selected_zip_sha = zip_sha or self.zip_sha
        return (
            "# CERA Evidence-Verified Review\n\n"
            f"reviewed_checkpoint_id: {CHECKPOINT_ID}\n"
            f"reviewed_checkpoint_sha: {checkpoint_sha}\n"
            f"reviewed_evidence_zip_sha256: {selected_zip_sha}\n"
            f"review_scope: {scope}\n"
            f"review_disposition: {disposition}\n\n"
            "## Findings\n\n"
            f"{body}\n"
        ).encode("utf-8")

    def write_response(self, **overrides: str) -> bytes:
        content = self.response_bytes(**overrides)
        self.expected_response.write_bytes(content)
        return content

    def test_01_export_uses_exact_checkpoint_identity(self) -> None:
        receipt = self.export()
        self.assertEqual(receipt["checkpoint_id"], CHECKPOINT_ID)
        self.assertEqual(receipt["checkpoint_sha"], CHECKPOINT_SHA)
        self.assertEqual(receipt["evidence_bridge_sha"], BRIDGE_SHA)
        self.assertEqual(receipt["evidence_zip_sha256"], self.zip_sha)
        self.assertEqual(
            receipt["expected_downloaded_response_filename"],
            self.expected_response_name,
        )
        message = Path(str(receipt["upload_message_path"])).read_text(
            encoding="utf-8"
        )
        self.assertIn(self.expected_response_name, message)

    def test_02_export_rejects_wrong_evidence_hash(self) -> None:
        (self.checkpoint / f"{self.zip_path.name}.sha256").write_text(
            f"{'0' * 64}  {self.zip_path.name}\n", encoding="utf-8"
        )
        result = self.run_bridge("Export", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hash mismatch", result.stderr.casefold())
        self.assertFalse((self.checkpoint / "BRIDGE_EXPORT_RECEIPT.json").exists())

    def test_03_export_filename_is_deterministic_and_idempotent(self) -> None:
        first = self.export()
        first_bytes = Path(str(first["exported_zip_path"])).read_bytes()
        second = self.export()
        self.assertEqual(first["exported_zip_path"], second["exported_zip_path"])
        self.assertEqual(first["upload_message_path"], second["upload_message_path"])
        self.assertEqual(first_bytes, Path(str(second["exported_zip_path"])).read_bytes())

    def test_04_export_does_not_mutate_checkpoint_zip(self) -> None:
        before = self.zip_path.read_bytes()
        self.export()
        self.assertEqual(self.zip_path.read_bytes(), before)
        self.assertEqual(hashlib.sha256(before).hexdigest(), self.zip_sha)

    def test_05_wait_watches_only_expected_response_path(self) -> None:
        self.export()
        (self.downloads / "CERA_PRO_RESPONSE_decoy.md").write_bytes(
            self.response_bytes()
        )
        result = self.run_bridge("Wait")
        self.assertIn("CERA_WAITING_FOR_PRO_RESPONSE", result.stdout)
        self.assertFalse(self.destination.exists())
        self.assertFalse((self.checkpoint / "BRIDGE_IMPORT_RECEIPT.json").exists())

    def test_06_wait_has_no_provider_database_or_source_side_effect(self) -> None:
        self.export()
        script_before = hashlib.sha256(BRIDGE_SCRIPT.read_bytes()).hexdigest()
        result = self.run_bridge("Wait")
        self.assertIn("CERA_WAITING_FOR_PRO_RESPONSE", result.stdout)
        waiting = (self.checkpoint / "WAITING_FOR_PRO_RESPONSE.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("provider_calls_while_waiting: 0", waiting)
        self.assertIn("database_or_story_writes_while_waiting: 0", waiting)
        self.assertIn("repository_source_changes_while_waiting: 0", waiting)
        self.assertEqual(
            hashlib.sha256(BRIDGE_SCRIPT.read_bytes()).hexdigest(), script_before
        )
        script = BRIDGE_SCRIPT.read_text(encoding="utf-8").casefold()
        for forbidden_command in (
            "invoke-webrequest",
            "invoke-restmethod",
            "start-process",
            "system.data.sqlite",
        ):
            self.assertNotIn(forbidden_command, script)

    def test_07_import_rejects_wrong_checkpoint_sha(self) -> None:
        self.export()
        self.write_response(checkpoint_sha="1" * 40)
        result = self.run_bridge("Import", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("identity does not match", result.stderr.casefold())
        self.assertFalse(self.destination.exists())

    def test_08_import_rejects_wrong_evidence_zip_hash(self) -> None:
        self.export()
        self.write_response(zip_sha="2" * 64)
        result = self.run_bridge("Import", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("identity does not match", result.stderr.casefold())

    def test_09_import_rejects_incomplete_or_placeholder_response(self) -> None:
        self.export()
        self.expected_response.write_text(
            "# Pending\n"
            f"reviewed_checkpoint_id: {CHECKPOINT_ID}\n"
            f"reviewed_checkpoint_sha: {CHECKPOINT_SHA}\n"
            f"reviewed_evidence_zip_sha256: {self.zip_sha}\n"
            "review_scope: evidence_verified\n"
            "review_disposition: pending\n",
            encoding="utf-8",
        )
        result = self.run_bridge("Import", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.destination.exists())

    def test_10_import_preserves_response_bytes_exactly(self) -> None:
        self.export()
        supplied = self.write_response(
            body=(
                "Evidence is complete. Unicode preservation sample: Sakura — "
                "reviewed. This remains advisory and creator authorization is required."
            )
        )
        result = self.run_bridge("Import")
        self.assertIn("CERA_PRO_RESPONSE_IMPORTED", result.stdout)
        self.assertEqual(self.destination.read_bytes(), supplied)

    def test_11_import_never_overwrites_first_response(self) -> None:
        self.export()
        self.write_response()
        self.run_bridge("Import")
        self.assertEqual(self.first_response.read_bytes(), self.first_response_bytes)

    def test_12_import_is_idempotent_for_identical_response(self) -> None:
        self.export()
        supplied = self.write_response()
        first = self.run_bridge("Import")
        second = self.run_bridge("Import")
        self.assertIn("CERA_PRO_RESPONSE_IMPORTED", first.stdout)
        self.assertIn("CERA_PRO_RESPONSE_IMPORTED", second.stdout)
        self.assertEqual(self.destination.read_bytes(), supplied)

    def test_13_import_rejects_conflicting_existing_response(self) -> None:
        self.export()
        self.write_response()
        conflicting = b"# Different preserved review\n"
        self.destination.write_bytes(conflicting)
        result = self.run_bridge("Import", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicting", result.stderr.casefold())
        self.assertEqual(self.destination.read_bytes(), conflicting)

    def test_14_import_creates_privacy_safe_receipt(self) -> None:
        self.export()
        supplied = self.write_response()
        self.run_bridge("Import")
        receipt = json.loads(
            (self.checkpoint / "BRIDGE_IMPORT_RECEIPT.json").read_text(
                encoding="utf-8-sig"
            )
        )
        self.assertEqual(receipt["checkpoint_id"], CHECKPOINT_ID)
        self.assertEqual(receipt["checkpoint_sha"], CHECKPOINT_SHA)
        self.assertEqual(receipt["evidence_zip_sha256"], self.zip_sha)
        self.assertEqual(receipt["response_sha256"], hashlib.sha256(supplied).hexdigest())
        self.assertEqual(receipt["identity_validation"], "passed")
        self.assertNotIn("response_text", receipt)
        self.assertNotIn("recommendations", receipt)

    def test_15_import_does_not_execute_recommendations(self) -> None:
        self.export()
        marker = self.root / "SHOULD_NOT_EXIST.txt"
        self.write_response(
            body=f"Recommendation only: create {marker}. Do not execute it automatically."
        )
        self.run_bridge("Import")
        self.assertFalse(marker.exists())

    def test_16_status_reports_transport_without_claiming_approval(self) -> None:
        self.export()
        self.write_response()
        self.run_bridge("Import")
        status = self.run_bridge("Status").stdout
        self.assertIn(f"checkpoint ID: {CHECKPOINT_ID}", status)
        self.assertIn(f"checkpoint SHA: {CHECKPOINT_SHA}", status)
        self.assertIn(f"evidence ZIP hash: {self.zip_sha}", status)
        self.assertIn("export status: exported", status)
        self.assertIn("waiting status: not_waiting", status)
        self.assertIn("response detected: true", status)
        self.assertIn("response imported: true", status)
        self.assertIn("creator authorization status: required", status)
        self.assertNotIn("authorized", status.casefold())
        self.assertNotIn("approved", status.casefold())

    def test_17_wait_imports_exact_response_once_when_detected(self) -> None:
        self.export()
        supplied = self.write_response()
        result = self.run_bridge("Wait")
        self.assertIn("CERA_PRO_RESPONSE_IMPORTED", result.stdout)
        self.assertEqual(self.destination.read_bytes(), supplied)
        self.assertTrue((self.checkpoint / "BRIDGE_IMPORT_RECEIPT.json").is_file())


class ProReviewRepositoryCycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        self._git("init", "-q")
        self._git("config", "user.email", "cycle-tests@example.invalid")
        self._git("config", "user.name", "Cycle Tests")
        (self.root / "baseline.txt").write_text("baseline\n", encoding="utf-8")
        self._git("add", "baseline.txt")
        self._git("commit", "-q", "-m", "baseline")
        self.checkpoint_sha = self._git("rev-parse", "HEAD").strip()

        self.sources = self.root / "review-inputs"
        self.sources.mkdir()
        self.evidence = self.sources / "checkpoint-evidence.zip"
        with zipfile.ZipFile(self.evidence, "w") as archive:
            archive.writestr("INDEX.md", "immutable checkpoint evidence\n")
        self.jobs: list[Path] = []
        for index in range(1, 4):
            path = self.sources / f"job-{index}.md"
            path.write_text(
                f"# Job {index} result\n\n"
                f"task_id: `progression-{index}`\n"
                "status: completed\n\n"
                f"Verified bounded result {index}.\n",
                encoding="utf-8",
            )
            self.jobs.append(path)
        self.authority_source = self.sources / "creator-authorization.md"
        self.authority_source.write_text(
            "# Creator authorization\n\nRun the named bounded verification.\n",
            encoding="utf-8",
        )
        self.cycle_id = "cycle-001"
        self.cycle = (
            self.root / ".chatgpt" / "pro-review" / "cycles" / self.cycle_id
        )
        self.source = self.cycle / "source"
        self.source.mkdir(parents=True)
        self.spec_path = self.cycle / "CYCLE_SPEC.json"
        self.job4_task_id = "bounded-verification"
        self._write_authorization()
        self._write_spec()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.root), *arguments],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            self.fail(result.stderr)
        return result.stdout

    @staticmethod
    def _hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _write_authorization(self) -> None:
        scope = (
            "Run the named focused and broad provider-free verification without "
            "changing runtime authority."
        )
        record = {
            "schema_version": review_cycle.JOB4_AUTHORIZATION_SCHEMA,
            "cycle_id": self.cycle_id,
            "task_id": self.job4_task_id,
            "scope": scope,
            "scope_sha256": hashlib.sha256(scope.encode()).hexdigest(),
            "expected_result_relative_path": "source/JOB4_RESULT.json",
            "authority_source_path": str(self.authority_source),
            "authority_source_sha256": self._hash(self.authority_source),
            "explicit_exclusions": [
                "no provider calls",
                "no story or database writes",
                "no runtime or deployment changes",
            ],
        }
        self.authorization_record = self.source / "JOB4_AUTHORIZATION.json"
        self.authorization_record.write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )

    def _context(self, job_count: int) -> dict[str, object]:
        return {
            "creator_goal": "Prove the repository-local overlapped review cycle.",
            "starting_baseline_or_prior_checkpoint": self.checkpoint_sha,
            "selection_rationale": [f"Bounded progression {i}." for i in range(1, job_count + 1)],
            "diff_summary": "Only review tooling, tests, and governed evidence change.",
            "focused_tests": "Focused repository cycle tests pass.",
            "complete_suite": "Pending the exact pre-authorized Job 4.",
            "active_profile_before": "unchanged-test-profile",
            "active_profile_after": "unchanged-test-profile",
            "provider_calls_and_cost": "zero actual calls and zero cost",
            "retry_and_fallback": "zero retry and zero fallback",
            "story_database_and_branch_effects": "zero story/database effects",
            "user_visible_effect": "repository review transport only",
            "historical_evidence_integrity": "preserved",
            "unresolved_defects": "none known before independent review",
            "uncertainty_and_risks": "app delivery needs external evidence",
            "disagreement_with_prior_review": "none",
            "codex_advisory_next_candidates": ["review the exact package"],
            "questions_for_chatgpt_pro": ["Are any integrity gaps still material?"],
            "explicit_exclusions": ["runtime", "providers", "story", "deployment"],
        }

    def _write_spec(
        self,
        *,
        job_count: int = 3,
        sequence: int = 1,
        prior_cycle_id: str | None = None,
        git_sha: str | None = None,
        evidence_hash: str | None = None,
    ) -> None:
        scope = json.loads(self.authorization_record.read_text(encoding="utf-8"))[
            "scope"
        ]
        value = {
            "schema_version": review_cycle.SPEC_SCHEMA,
            "cycle_id": self.cycle_id,
            "cycle_sequence": sequence,
            "checkpoint": {
                "id": "checkpoint-001",
                "git_sha": git_sha or self.checkpoint_sha,
                "evidence_path": str(self.evidence),
                "evidence_sha256": evidence_hash or self._hash(self.evidence),
            },
            "jobs_1_3": [
                {
                    "task_id": f"progression-{index}",
                    "result_path": str(path),
                    "result_sha256": self._hash(path),
                }
                for index, path in enumerate(self.jobs[:job_count], start=1)
            ],
            "prior_cycle_id": prior_cycle_id,
            "job4": {
                "task_id": self.job4_task_id,
                "scope": scope,
                "authorization_record_path": str(self.authorization_record),
                "authorization_record_sha256": self._hash(self.authorization_record),
            },
            "review_context": self._context(job_count),
            "review_snapshot": {
                "baseline_git_sha": git_sha or self.checkpoint_sha,
                "include_all_nonexcluded_changes": True,
                "declared_exclusions": [
                    {
                        "path_prefix": "runtime/",
                        "reason_code": "generated_runtime_state",
                    },
                    {
                        "path_prefix": ".chatgpt/operations/",
                        "reason_code": "connector_metadata_outside_task",
                    },
                    {
                        "path_prefix": f".chatgpt/pro-review/cycles/{self.cycle_id}/",
                        "reason_code": "current_cycle_transport_state",
                    },
                ],
            },
        }
        self.spec_path.write_text(
            json.dumps(value, indent=2) + "\n", encoding="utf-8"
        )

    def publish(self) -> dict[str, object]:
        return review_cycle.publish_cycle(
            self.cycle, self.spec_path, repository_root_path=self.root
        )

    def record_trigger(self) -> dict[str, object]:
        message = self.cycle / "outbox" / "TRIGGER_MESSAGE.txt"
        target = "chatgpt-thread-id"
        return review_cycle.record_trigger(
            self.cycle,
            target_id=target,
            app_result_json=json.dumps({"threadId": target}, separators=(",", ":")),
            message_sha256=self._hash(message),
            repository_root_path=self.root,
        )

    def write_job4_result(self, *, provider_calls: int = 0) -> None:
        report = self.source / "JOB4_REPORT.md"
        report.write_text(
            "# Job 4 report\n\nAll named provider-free verification completed.\n",
            encoding="utf-8",
        )
        result = {
            "schema_version": review_cycle.JOB4_RESULT_SCHEMA,
            "cycle_id": self.cycle_id,
            "task_id": self.job4_task_id,
            "status": "completed",
            "report_relative_path": "source/JOB4_REPORT.md",
            "report_sha256": self._hash(report),
            "effects": {
                "provider_calls": provider_calls,
                "story_database_writes": 0,
                "active_route_changes": 0,
                "deployment_remote_or_push_effects": 0,
            },
            "verification": [
                {
                    "command": "python -m unittest focused",
                    "status": "passed",
                    "summary": "All focused cases passed.",
                }
            ],
        }
        (self.source / "JOB4_RESULT.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )

    def complete_job4(self, *, provider_calls: int = 0) -> dict[str, object]:
        self.write_job4_result(provider_calls=provider_calls)
        return review_cycle.complete_job4(
            self.cycle,
            stability_delay_milliseconds=0,
            repository_root_path=self.root,
        )

    def write_canary_job4_result(
        self,
        *,
        status: str,
        execution_mode: str,
        provider_calls: int,
        scripted_transport_invocations: int,
        operational_counters: ContinuousJob4OperationalCountersV1 | None = None,
        postcondition_changes: dict[str, object] | None = None,
    ) -> dict[str, object]:
        report = self.source / "JOB4_REPORT.md"
        counters = operational_counters or ContinuousJob4OperationalCountersV1(
            live_story_writes=0,
            production_database_writes=0,
            deployment_operations=0,
            remote_operations=0,
            merge_operations=0,
            push_operations=0,
            service_changes=0,
            installed_sillytavern_changes=0,
        )
        postcondition_values: dict[str, object] = {
            "execution_mode": execution_mode,
            "source_database_sha256_before": "1" * 64,
            "source_database_sha256_after": "1" * 64,
            "disposable_database_sha256_before": "2" * 64,
            "disposable_database_sha256_after": "2" * 64,
            "database_integrity_check": "ok",
            "database_foreign_key_findings": 0,
            "active_profile_sha256_before": "3" * 64,
            "active_profile_sha256_after": "3" * 64,
            "active_profile_inspection_status": "verified",
            "thread_archival": {"planner": True, "validator": True},
            "accepted_session_synchronized": True,
            "accepted_final_sequences_injected": True,
            "call_ledger_dispatches": (
                scripted_transport_invocations
                if execution_mode.startswith("provider_free_scripted_")
                else provider_calls
            ),
            "scripted_transport_invocations": scripted_transport_invocations,
        }
        postcondition_values.update(postcondition_changes or {})
        postconditions = ContinuousJob4PostconditionsV1(**postcondition_values)
        terminal = ContinuousJob4TerminalEvidenceV1.build(
            execution_status=status,
            provider_calls=provider_calls,
            operational_counters=counters,
            postconditions=postconditions,
        )
        effects = terminal.effect_evidence.canonical_effects
        detail = {
            "cycle_id": self.cycle_id,
            "status": terminal.status,
            "execution_status": status,
            "execution_mode": execution_mode,
            "provider_calls": effects["provider_calls"],
            "scripted_transport_invocations": scripted_transport_invocations,
            "story_database_writes": effects["story_database_writes"],
            "active_route_changes": effects["active_route_changes"],
            "deployment_remote_or_push_effects": effects[
                "deployment_remote_or_push_effects"
            ],
            "source_database_unchanged": (
                postconditions.source_database_sha256_before is not None
                and postconditions.source_database_sha256_before
                == postconditions.source_database_sha256_after
            ),
            "copy_database_unchanged": (
                postconditions.disposable_database_sha256_before is not None
                and postconditions.disposable_database_sha256_before
                == postconditions.disposable_database_sha256_after
            ),
            "active_route_unchanged": postconditions.active_route_changes == 0,
            "thread_archival": dict(postconditions.thread_archival),
            "terminal_evidence": terminal.to_dict(),
            "terminal_evidence_sha256": terminal.sha256,
            "calls": [],
            "turns": [],
            "failure": (
                None
                if terminal.status == "completed"
                else {"stage": "terminal_postconditions"}
            ),
        }
        report.write_text(
            build_report(detail, task_id=self.job4_task_id),
            encoding="utf-8",
        )
        value = build_canonical_job4_result(
            detail,
            task_id=self.job4_task_id,
            report_sha256=self._hash(report),
        )
        (self.source / "JOB4_TERMINAL_EVIDENCE.json").write_bytes(
            canonical_bytes(terminal.to_dict())
        )
        (self.source / "JOB4_RESULT.json").write_text(
            json.dumps(value, indent=2) + "\n", encoding="utf-8"
        )
        return value

    def response_bytes(self, **overrides: str) -> bytes:
        manifest = json.loads(
            (self.cycle / "CYCLE_MANIFEST.json").read_text(encoding="utf-8")
        )
        values = {
            "review_cycle_id": manifest["cycle_id"],
            "reviewed_checkpoint_id": manifest["checkpoint"]["id"],
            "reviewed_checkpoint_git_sha": manifest["checkpoint"]["git_sha"],
            "reviewed_evidence_sha256": manifest["checkpoint"]["evidence_sha256"],
            "reviewed_task_set_sha256": manifest["task_set_sha256"],
            "reviewed_job4_task_id": manifest["job4"]["task_id"],
            "response_nonce": manifest["response_nonce"],
            "review_scope": "repository_cycle",
            "review_disposition": "accepted",
        }
        values.update(overrides)
        findings = (
            "The actual source snapshot, manifest root, receipt chain, task identities, "
            "authorization record, state transitions, tests, and effect declarations "
            "were independently reviewed. The response remains advisory, creates no "
            "creator authority, and identifies no unreported runtime or story effect. "
            "The findings are intentionally substantive enough to prove that this is "
            "not the generated placeholder response."
        )
        return (
            "# CERA ChatGPT Pro Repository Review\n\n"
            + "\n".join(f"{key}: {value}" for key, value in values.items())
            + f"\n\n## Independent findings\n\n{findings}\n"
        ).encode("utf-8")

    def response_bytes_with_planning_sections(self, **overrides: str) -> bytes:
        return self.response_bytes(**overrides) + (
            b"\n## Required corrections\n\nNo provider-free corrections remain.\n"
            b"\n## Next three progressions\n\nNo next progression is recommended.\n"
            b"\n## Recommended next Job 4\n\nNo further Job 4 is recommended.\n"
            b"\n## Explicitly not authorized\n\nProviders, production, deployment, and story writes remain closed.\n"
        )

    def write_response(self, **overrides: str) -> bytes:
        data = self.response_bytes(**overrides)
        path = self.cycle / review_cycle.EXPECTED_RESPONSE_RELATIVE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data

    def consume(self) -> dict[str, object]:
        return review_cycle.consume_response(
            self.cycle,
            stability_delay_milliseconds=0,
            repository_root_path=self.root,
        )

    def test_18_publish_binds_source_root_and_job4_authorization_contract(self) -> None:
        state = self.publish()
        self.assertEqual(state["state"], review_cycle.STATE_JOB4_IN_PROGRESS)
        manifest = json.loads(
            (self.cycle / "CYCLE_MANIFEST.json").read_text(encoding="utf-8")
        )
        self.assertRegex(manifest["manifest_root_sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(manifest["source_snapshot"]["included_changes"])
        started = json.loads(
            (self.cycle / "receipts" / "JOB4_STARTED.json").read_text(encoding="utf-8")
        )
        self.assertTrue(started["authorization_record_contract_validated"])
        self.assertEqual(
            started["predecessor_receipt_sha256"],
            self._hash(self.cycle / "receipts" / "PUBLISHED.json"),
        )

    def test_19_supports_one_and_two_progression_packages(self) -> None:
        self._write_spec(job_count=1)
        self.publish()
        manifest = json.loads((self.cycle / "CYCLE_MANIFEST.json").read_text())
        self.assertEqual(len(manifest["jobs_1_3"]), 1)

    def test_20_rejects_external_path_and_database_artifact(self) -> None:
        external = Path(self.temporary.name) / "outside.md"
        external.write_text("outside\n", encoding="utf-8")
        spec = json.loads(self.spec_path.read_text())
        spec["jobs_1_3"][0]["result_path"] = str(external)
        spec["jobs_1_3"][0]["result_sha256"] = self._hash(external)
        self.spec_path.write_text(json.dumps(spec), encoding="utf-8")
        with self.assertRaisesRegex(review_cycle.CycleError, "escapes"):
            self.publish()

        database = self.sources / "story.sqlite"
        database.write_bytes(b"not a database")
        spec["jobs_1_3"][0]["result_path"] = str(database)
        spec["jobs_1_3"][0]["result_sha256"] = self._hash(database)
        self.spec_path.write_text(json.dumps(spec), encoding="utf-8")
        with self.assertRaisesRegex(review_cycle.CycleError, "protected file type"):
            self.publish()

    def test_21_rejects_symlink_escape_when_supported(self) -> None:
        outside = Path(self.temporary.name) / "outside-target.md"
        outside.write_text("outside\n", encoding="utf-8")
        link = self.sources / "linked.md"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symlink creation is unavailable")
        spec = json.loads(self.spec_path.read_text())
        spec["jobs_1_3"][0]["result_path"] = str(link)
        spec["jobs_1_3"][0]["result_sha256"] = self._hash(link)
        self.spec_path.write_text(json.dumps(spec), encoding="utf-8")
        with self.assertRaises(review_cycle.CycleError):
            self.publish()

    def test_22_rejects_invalid_or_nonexistent_git_object(self) -> None:
        self._write_spec(git_sha="1" * 41)
        with self.assertRaisesRegex(review_cycle.CycleError, "valid lowercase hash"):
            self.publish()
        self._write_spec(git_sha="1" * 40)
        with self.assertRaisesRegex(review_cycle.CycleError, "does not exist"):
            self.publish()

    def test_23_rejects_invalid_evidence_zip(self) -> None:
        self.evidence.write_bytes(b"not a zip")
        self._write_spec()
        with self.assertRaisesRegex(review_cycle.CycleError, "valid ZIP"):
            self.publish()

    def test_24_source_mutation_blocks_later_transition(self) -> None:
        self.publish()
        self.jobs[0].write_text("changed after publication\n", encoding="utf-8")
        self.write_job4_result()
        with self.assertRaisesRegex(review_cycle.CycleError, "drifted after publication"):
            review_cycle.complete_job4(
                self.cycle,
                stability_delay_milliseconds=0,
                repository_root_path=self.root,
            )

    def test_25_tampered_manifest_outbox_and_forged_recovery_fail_closed(self) -> None:
        self.publish()
        manifest_path = self.cycle / "CYCLE_MANIFEST.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["job4"]["task_id"] = "forged"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(review_cycle.CycleError, "root hash mismatch"):
            review_cycle.recover_cycle(self.cycle, repository_root_path=self.root)

    def test_26_outbox_tamper_blocks_completion(self) -> None:
        self.publish()
        (self.cycle / "outbox" / "JOB_1_RESULT.md").write_text("tampered\n")
        self.write_job4_result()
        with self.assertRaisesRegex(review_cycle.CycleError, "outbox artifact changed"):
            review_cycle.complete_job4(
                self.cycle,
                stability_delay_milliseconds=0,
                repository_root_path=self.root,
            )

    def test_27_trigger_attestation_binds_message_and_app_result_without_overclaim(self) -> None:
        self.publish()
        message = self.cycle / "outbox" / "TRIGGER_MESSAGE.txt"
        with self.assertRaisesRegex(review_cycle.CycleError, "exact target"):
            review_cycle.record_trigger(
                self.cycle,
                target_id="target",
                app_result_json=json.dumps({"threadId": "wrong"}),
                message_sha256=self._hash(message),
                repository_root_path=self.root,
            )
        receipt = self.record_trigger()
        self.assertTrue(receipt["app_result_attested_success"])
        self.assertFalse(receipt["independent_delivery_proof"])
        self.assertNotIn("chatgpt-thread-id", json.dumps(receipt))

    def test_28_structured_job4_effects_are_preserved_not_hard_coded(self) -> None:
        self.publish()
        self.record_trigger()
        self.complete_job4(provider_calls=2)
        receipt = json.loads(
            (self.cycle / "receipts" / "JOB4_COMPLETED.json").read_text()
        )
        self.assertEqual(receipt["effects"]["provider_calls"], 2)
        self.assertEqual(
            receipt["effect_claim_source"], "structured_job4_result_declaration"
        )

    def test_28a_completed_live_shaped_canary_result_completes_job4(self) -> None:
        self.publish()
        self.record_trigger()
        self.write_canary_job4_result(
            status="completed",
            execution_mode="live_one_shot",
            provider_calls=10,
            scripted_transport_invocations=0,
        )
        state = review_cycle.complete_job4(
            self.cycle,
            stability_delay_milliseconds=0,
            repository_root_path=self.root,
        )
        self.assertEqual(state["state"], review_cycle.STATE_RESPONSE_PENDING)

    def test_28b_failed_live_shaped_canary_result_completes_job4(self) -> None:
        self.publish()
        self.record_trigger()
        self.write_canary_job4_result(
            status="failed",
            execution_mode="live_one_shot",
            provider_calls=1,
            scripted_transport_invocations=0,
        )
        state = review_cycle.complete_job4(
            self.cycle,
            stability_delay_milliseconds=0,
            repository_root_path=self.root,
        )
        self.assertEqual(state["state"], review_cycle.STATE_RESPONSE_PENDING)

    def test_28c_failed_scripted_canary_result_completes_job4(self) -> None:
        self.publish()
        self.record_trigger()
        self.write_canary_job4_result(
            status="failed",
            execution_mode="provider_free_scripted_v8",
            provider_calls=0,
            scripted_transport_invocations=3,
        )
        state = review_cycle.complete_job4(
            self.cycle,
            stability_delay_milliseconds=0,
            repository_root_path=self.root,
        )
        self.assertEqual(state["state"], review_cycle.STATE_RESPONSE_PENDING)

    def test_28d_actual_scripted_v8_canary_completes_job4_transport(self) -> None:
        self.publish()
        self.record_trigger()
        runtime = self.root / "runtime" / "canary-republication"
        source_database = self.root / "runtime" / "source.sqlite3"
        source_database.parent.mkdir()
        connection = sqlite3.connect(source_database)
        try:
            connection.execute("CREATE TABLE qualification(value TEXT)")
            connection.execute("INSERT INTO qualification VALUES ('unchanged')")
            connection.commit()
        finally:
            connection.close()
        manifest = json.loads(
            (self.cycle / "CYCLE_MANIFEST.json").read_text(encoding="utf-8")
        )
        project_head = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        completed = subprocess.run(
            (
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "run_continuous_planner_validator_job4.py"),
                "--confirm-provider-free-scripted-v8",
                "--expected-scripted-fixture-sha256",
                SCRIPTED_JOB4_FIXTURE_SHA256,
                "--cycle-directory",
                str(self.cycle),
                "--source-database",
                str(source_database),
                "--runtime-root",
                str(runtime),
                "--expected-checkpoint-sha",
                project_head,
                "--expected-cycle-id",
                self.cycle_id,
                "--expected-task-id",
                self.job4_task_id,
                "--expected-authorization-sha256",
                manifest["job4"]["authorization_record_sha256"],
                "--maximum-provider-calls",
                "10",
            ),
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        state = review_cycle.complete_job4(
            self.cycle,
            stability_delay_milliseconds=0,
            repository_root_path=self.root,
        )
        self.assertEqual(state["state"], review_cycle.STATE_RESPONSE_PENDING)
        receipt = json.loads(
            (self.cycle / "receipts" / "JOB4_COMPLETED.json").read_text()
        )
        result = json.loads(
            (self.cycle / "artifacts" / "JOB4_RESULT.json").read_text()
        )
        self.assertEqual(receipt["effects"]["provider_calls"], 0)
        self.assertEqual(receipt["event"], "job4_completed_v2")
        self.assertEqual(
            receipt["effect_claim_source"], "typed_terminal_evidence_v2"
        )
        self.assertEqual(
            receipt["terminal_evidence_sha256"],
            result["terminal_evidence_sha256"],
        )
        terminal_artifact = (
            self.cycle / "artifacts" / "JOB4_TERMINAL_EVIDENCE.json"
        )
        self.assertEqual(self._hash(terminal_artifact), receipt["terminal_evidence_sha256"])
        self.assertNotIn("scripted_transport_invocations", receipt["effects"])
        self.assertIn(
            "Scripted transport invocations:** 10",
            (self.cycle / "artifacts" / "JOB4_REPORT.md").read_text(),
        )
        recovered = review_cycle.recover_cycle(
            self.cycle, repository_root_path=self.root
        )
        self.assertEqual(recovered["state"], review_cycle.STATE_RESPONSE_PENDING)

    def test_28e_complete_job4_rejects_legacy_canary_fields(self) -> None:
        self.publish()
        self.record_trigger()
        value = self.write_canary_job4_result(
            status="completed",
            execution_mode="provider_free_scripted_v8",
            provider_calls=0,
            scripted_transport_invocations=10,
        )
        result_path = self.source / "JOB4_RESULT.json"
        result_path.write_text(
            json.dumps({**value, "authorization_sha256": "a" * 64}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(review_cycle.CycleError, "authorization_sha256"):
            review_cycle.complete_job4(
                self.cycle,
                stability_delay_milliseconds=0,
                repository_root_path=self.root,
            )
        self.assertFalse((self.cycle / "receipts" / "JOB4_COMPLETED.json").exists())
        invalid_effects = dict(value)
        invalid_effects["effects"] = {
            **value["effects"],
            "scripted_transport_invocations": 10,
        }
        result_path.write_text(json.dumps(invalid_effects), encoding="utf-8")
        with self.assertRaisesRegex(
            review_cycle.CycleError, "scripted_transport_invocations"
        ):
            review_cycle.complete_job4(
                self.cycle,
                stability_delay_milliseconds=0,
                repository_root_path=self.root,
            )
        self.assertFalse((self.cycle / "receipts" / "JOB4_COMPLETED.json").exists())

    def test_28f_story_effect_evidence_survives_completion_copy_receipt_and_recovery(self) -> None:
        self.publish()
        self.record_trigger()
        counters = ContinuousJob4OperationalCountersV1(
            live_story_writes=1,
            production_database_writes=2,
            deployment_operations=0,
            remote_operations=0,
            merge_operations=0,
            push_operations=0,
            service_changes=0,
            installed_sillytavern_changes=0,
        )
        value = self.write_canary_job4_result(
            status="completed",
            execution_mode="live_one_shot",
            provider_calls=10,
            scripted_transport_invocations=0,
            operational_counters=counters,
        )
        self.assertEqual(value["status"], "failed")
        state = review_cycle.complete_job4(
            self.cycle,
            stability_delay_milliseconds=0,
            repository_root_path=self.root,
        )
        self.assertEqual(state["state"], review_cycle.STATE_RESPONSE_PENDING)
        copied = json.loads(
            (self.cycle / "artifacts" / "JOB4_RESULT.json").read_text()
        )
        receipt = json.loads(
            (self.cycle / "receipts" / "JOB4_COMPLETED.json").read_text()
        )
        for record in (value, copied, receipt):
            self.assertEqual(record["effects"]["story_database_writes"], 3)
        self.assertIn(
            '"story_database_writes":3',
            (self.cycle / "artifacts" / "JOB4_REPORT.md").read_text(),
        )
        recovered = review_cycle.recover_cycle(
            self.cycle, repository_root_path=self.root
        )
        self.assertEqual(recovered["state"], review_cycle.STATE_RESPONSE_PENDING)
        recovered_receipt = json.loads(
            (self.cycle / "receipts" / "JOB4_COMPLETED.json").read_text()
        )
        self.assertEqual(
            recovered_receipt["effects"]["story_database_writes"], 3
        )

    def test_28g_active_profile_failure_survives_completion_as_route_effect(self) -> None:
        self.publish()
        self.record_trigger()
        value = self.write_canary_job4_result(
            status="completed",
            execution_mode="provider_free_scripted_v8",
            provider_calls=0,
            scripted_transport_invocations=10,
            postcondition_changes={
                "active_profile_sha256_after": None,
                "active_profile_inspection_status": "failed",
            },
        )
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["effects"]["active_route_changes"], 1)
        review_cycle.complete_job4(
            self.cycle,
            stability_delay_milliseconds=0,
            repository_root_path=self.root,
        )
        receipt = json.loads(
            (self.cycle / "receipts" / "JOB4_COMPLETED.json").read_text()
        )
        self.assertEqual(receipt["effects"]["active_route_changes"], 1)
        copied = json.loads(
            (self.cycle / "artifacts" / "JOB4_RESULT.json").read_text()
        )
        self.assertEqual(copied["effects"]["active_route_changes"], 1)
        self.assertIn(
            '"active_route_changes":1',
            (self.cycle / "artifacts" / "JOB4_REPORT.md").read_text(),
        )

    def test_28h_operational_effect_evidence_survives_completion(self) -> None:
        self.publish()
        self.record_trigger()
        counters = ContinuousJob4OperationalCountersV1(
            live_story_writes=0,
            production_database_writes=0,
            deployment_operations=1,
            remote_operations=2,
            merge_operations=3,
            push_operations=4,
            service_changes=5,
            installed_sillytavern_changes=6,
        )
        value = self.write_canary_job4_result(
            status="completed",
            execution_mode="provider_free_scripted_v8",
            provider_calls=0,
            scripted_transport_invocations=10,
            operational_counters=counters,
        )
        self.assertEqual(value["status"], "failed")
        self.assertEqual(
            value["effects"]["deployment_remote_or_push_effects"], 21
        )
        review_cycle.complete_job4(
            self.cycle,
            stability_delay_milliseconds=0,
            repository_root_path=self.root,
        )
        receipt = json.loads(
            (self.cycle / "receipts" / "JOB4_COMPLETED.json").read_text()
        )
        self.assertEqual(
            receipt["effects"]["deployment_remote_or_push_effects"], 21
        )
        copied = json.loads(
            (self.cycle / "artifacts" / "JOB4_RESULT.json").read_text()
        )
        self.assertEqual(
            copied["effects"]["deployment_remote_or_push_effects"], 21
        )
        self.assertIn(
            '"deployment_remote_or_push_effects":21',
            (self.cycle / "artifacts" / "JOB4_REPORT.md").read_text(),
        )

    def test_28i_terminal_evidence_is_required_copied_and_revalidated(self) -> None:
        self.publish()
        self.record_trigger()
        self.write_canary_job4_result(
            status="completed",
            execution_mode="provider_free_scripted_v8",
            provider_calls=0,
            scripted_transport_invocations=10,
        )
        source_terminal = self.source / "JOB4_TERMINAL_EVIDENCE.json"
        exact_terminal_bytes = source_terminal.read_bytes()
        source_terminal.unlink()
        with self.assertRaisesRegex(review_cycle.CycleError, "is missing"):
            review_cycle.complete_job4(
                self.cycle,
                stability_delay_milliseconds=0,
                repository_root_path=self.root,
            )
        source_terminal.write_bytes(exact_terminal_bytes)
        review_cycle.complete_job4(
            self.cycle,
            stability_delay_milliseconds=0,
            repository_root_path=self.root,
        )
        copied_terminal = (
            self.cycle / "artifacts" / "JOB4_TERMINAL_EVIDENCE.json"
        )
        self.assertEqual(copied_terminal.read_bytes(), exact_terminal_bytes)
        copied_terminal.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(
            review_cycle.CycleError, "terminal evidence does not match"
        ):
            review_cycle.recover_cycle(
                self.cycle, repository_root_path=self.root
            )

    def test_29_placeholder_or_bodyless_response_is_rejected(self) -> None:
        self.publish()
        self.complete_job4()
        template = (self.cycle / "outbox" / "PRO_RESPONSE_TEMPLATE.md").read_bytes()
        path = self.cycle / review_cycle.EXPECTED_RESPONSE_RELATIVE_PATH
        path.parent.mkdir(parents=True)
        path.write_bytes(template.replace(b"accepted | corrections_required | blocked", b"accepted"))
        with self.assertRaises(review_cycle.CycleError):
            self.consume()

    def test_30_identity_mismatch_and_conflicting_duplicate_preserve_state(self) -> None:
        self.publish()
        self.complete_job4()
        self.write_response(reviewed_task_set_sha256="3" * 64)
        with self.assertRaisesRegex(review_cycle.CycleError, "does not match"):
            self.consume()
        self.write_response()
        accepted = self.consume()
        self.assertEqual(accepted["state"], review_cycle.STATE_REVIEW_CONSUMED)
        original = (self.cycle / review_cycle.ACCEPTED_RESPONSE_RELATIVE_PATH).read_bytes()
        self.write_response(review_disposition="corrections_required")
        with self.assertRaisesRegex(review_cycle.CycleError, "conflicts"):
            self.consume()
        self.assertEqual(
            (self.cycle / review_cycle.ACCEPTED_RESPONSE_RELATIVE_PATH).read_bytes(),
            original,
        )

    def test_31_unstable_response_is_rejected(self) -> None:
        self.publish()
        self.complete_job4()
        response_path = self.cycle / review_cycle.EXPECTED_RESPONSE_RELATIVE_PATH
        response_path.parent.mkdir(parents=True)
        response_path.write_bytes(self.response_bytes())

        def mutate(_: float) -> None:
            response_path.write_bytes(self.response_bytes() + b"\nchanged\n")

        with mock.patch.object(review_cycle_core.time, "sleep", mutate):
            with self.assertRaisesRegex(review_cycle.CycleError, "stable-read"):
                review_cycle.consume_response(
                    self.cycle,
                    stability_delay_milliseconds=10,
                    repository_root_path=self.root,
                )

    def test_32_interrupted_consumption_and_recovery_validate_exact_bytes(self) -> None:
        self.publish()
        self.complete_job4()
        supplied = self.write_response()
        accepted = self.cycle / review_cycle.ACCEPTED_RESPONSE_RELATIVE_PATH
        accepted.parent.mkdir(parents=True)
        accepted.write_bytes(supplied)
        recovered = review_cycle.recover_cycle(
            self.cycle, repository_root_path=self.root
        )
        self.assertEqual(recovered["state"], review_cycle.STATE_REVIEW_CONSUMED)
        accepted.write_bytes(b"forged accepted bytes")
        with self.assertRaises(review_cycle.CycleError):
            review_cycle.recover_cycle(self.cycle, repository_root_path=self.root)

    def test_33_wait_receipts_are_append_only_and_invent_no_work(self) -> None:
        self.publish()
        self.complete_job4()
        for _ in range(2):
            with self.assertRaises(review_cycle.ResponseNotReady):
                review_cycle.wait_and_consume(
                    self.cycle,
                    max_polls=1,
                    poll_seconds=0,
                    stability_delay_milliseconds=0,
                    repository_root_path=self.root,
                )
        receipts = sorted((self.cycle / "receipts" / "waits").glob("*.json"))
        self.assertEqual(len(receipts), 2)
        self.assertTrue(all(not json.loads(path.read_text())["unrelated_work_started"] for path in receipts))

    def test_34_concurrent_conflicting_immutable_writes_never_overwrite(self) -> None:
        target = self.source / "concurrent.txt"
        errors: list[Exception] = []

        def writer(data: bytes) -> None:
            try:
                review_cycle.immutable_write(target, data)
            except Exception as exc:  # expected for exactly one conflicting writer
                errors.append(exc)

        threads = [
            threading.Thread(target=writer, args=(b"first",)),
            threading.Thread(target=writer, args=(b"second",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(errors), 1)
        self.assertIn(target.read_bytes(), {b"first", b"second"})

    def test_35_second_cycle_proves_predecessor_receipt_chain(self) -> None:
        self.publish()
        self.complete_job4()
        self.write_response()
        self.consume()

        self.cycle_id = "cycle-002"
        self.cycle = self.root / ".chatgpt" / "pro-review" / "cycles" / self.cycle_id
        self.source = self.cycle / "source"
        self.source.mkdir(parents=True)
        self.spec_path = self.cycle / "CYCLE_SPEC.json"
        self.job4_task_id = "bounded-verification-2"
        self._write_authorization()
        self._write_spec(sequence=2, prior_cycle_id="cycle-001")
        self.publish()
        manifest = json.loads((self.cycle / "CYCLE_MANIFEST.json").read_text())
        self.assertEqual(manifest["prior_cycle"]["cycle_id"], "cycle-001")
        self.assertRegex(
            manifest["prior_cycle"]["job4_completion_receipt_sha256"],
            r"^[0-9a-f]{64}$",
        )

    def test_36_supports_two_progression_package(self) -> None:
        self._write_spec(job_count=2)
        self.publish()
        manifest = json.loads((self.cycle / "CYCLE_MANIFEST.json").read_text())
        self.assertEqual(len(manifest["jobs_1_3"]), 2)

    def test_37_snapshot_automatically_includes_unreferenced_changed_file(self) -> None:
        extra = self.sources / "unreferenced-change.md"
        extra.write_text("# Extra changed source\n", encoding="utf-8")
        self.publish()
        manifest = json.loads((self.cycle / "CYCLE_MANIFEST.json").read_text())
        paths = {item["path"] for item in manifest["source_snapshot"]["included_changes"]}
        self.assertIn("review-inputs/unreferenced-change.md", paths)

    def test_37a_snapshot_preserves_confined_historical_runtime_evidence(self) -> None:
        evidence = (
            self.root
            / ".chatgpt"
            / "pro-review"
            / "checkpoints"
            / "checkpoint-001"
            / "EVIDENCE_PACKAGE"
            / "runtime"
            / "HISTORICAL_RECEIPT_SAMPLE.json"
        )
        evidence.parent.mkdir(parents=True)
        evidence.write_text('{"historical": true}\n', encoding="utf-8")

        self.publish()
        manifest = json.loads((self.cycle / "CYCLE_MANIFEST.json").read_text())
        paths = {item["path"] for item in manifest["source_snapshot"]["included_changes"]}
        self.assertIn(
            ".chatgpt/pro-review/checkpoints/checkpoint-001/"
            "EVIDENCE_PACKAGE/runtime/HISTORICAL_RECEIPT_SAMPLE.json",
            paths,
        )
        receipt = self.record_trigger()
        self.assertTrue(receipt["app_result_attested_success"])

    def test_38_semantically_mismatched_authorization_is_rejected(self) -> None:
        record = json.loads(self.authorization_record.read_text())
        record["task_id"] = "different-task"
        self.authorization_record.write_text(json.dumps(record), encoding="utf-8")
        self._write_spec()
        with self.assertRaisesRegex(review_cycle.CycleError, "exact cycle/task/scope"):
            self.publish()

    def test_39_forged_completion_receipt_cannot_drive_recovery(self) -> None:
        self.publish()
        manifest = json.loads((self.cycle / "CYCLE_MANIFEST.json").read_text())
        artifact = self.cycle / "artifacts" / "JOB4_RESULT.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("{}\n", encoding="utf-8")
        forged = {
            "schema_version": review_cycle.RECEIPT_SCHEMA,
            "cycle_id": self.cycle_id,
            "event": "job4_completed",
            "manifest_root_sha256": manifest["manifest_root_sha256"],
            "predecessor_receipt_sha256": "0" * 64,
            "recorded_at_utc": "2026-07-31T00:00:00Z",
            "job4_task_id": self.job4_task_id,
            "job4_result_sha256": self._hash(artifact),
            "job4_result_relative_path": "artifacts/JOB4_RESULT.json",
        }
        (self.cycle / "receipts" / "JOB4_COMPLETED.json").write_text(
            json.dumps(forged), encoding="utf-8"
        )
        with self.assertRaisesRegex(review_cycle.CycleError, "receipt"):
            review_cycle.recover_cycle(self.cycle, repository_root_path=self.root)

    def test_40_request_binds_complete_context_and_valid_source_archive(self) -> None:
        self.publish()
        request = (self.cycle / "outbox" / "REVIEW_REQUEST.md").read_text()
        for heading in (
            "## Creator goal",
            "## Bound source and evidence",
            "## Current or revised progressions",
            "## Preceding Job 4 provenance",
            "## Concurrent pre-authorized Job 4",
        ):
            self.assertIn(heading, request)
        with zipfile.ZipFile(self.cycle / "outbox" / "SOURCE_SNAPSHOT.zip") as archive:
            self.assertIsNone(archive.testzip())
            self.assertTrue(archive.namelist())

    def test_41_latest_consumed_finds_validated_repository_response(self) -> None:
        self.publish()
        self.complete_job4()
        supplied = self.write_response()
        self.consume()
        latest = review_cycle.latest_consumed_cycle(repository_root_path=self.root)
        self.assertEqual(latest["cycle_id"], self.cycle_id)
        self.assertEqual(latest["accepted_response_sha256"], hashlib.sha256(supplied).hexdigest())

    def test_42_tracked_runtime_source_is_included_but_root_runtime_is_excluded(self) -> None:
        tracked_runtime = self.root / "src" / "cera" / "runtime" / "director.py"
        tracked_runtime.parent.mkdir(parents=True)
        tracked_runtime.write_text("DIRECTOR_VERSION = 1\n", encoding="utf-8")
        generated_runtime = self.root / "runtime" / "session.json"
        generated_runtime.parent.mkdir()
        generated_runtime.write_text("{}\n", encoding="utf-8")

        self.publish()
        manifest = json.loads((self.cycle / "CYCLE_MANIFEST.json").read_text())
        included = {
            item["path"] for item in manifest["source_snapshot"]["included_changes"]
        }
        excluded = {
            item["path"]
            for item in manifest["source_snapshot"]["excluded_status_changes"]
        }
        self.assertIn("src/cera/runtime/director.py", included)
        self.assertIn("runtime/session.json", excluded)

    def test_43_deletion_and_rename_are_bound_as_status_aware_changes(self) -> None:
        deleted = self.root / "deleted.txt"
        renamed = self.root / "old-name.txt"
        deleted.write_text("delete me\n", encoding="utf-8")
        renamed.write_text("rename me\n", encoding="utf-8")
        self._git("add", "deleted.txt", "old-name.txt")
        self._git("commit", "-q", "-m", "tracked snapshot fixtures")
        self._git("rm", "-q", "deleted.txt")
        self._git("mv", "old-name.txt", "new-name.txt")

        self.publish()
        manifest = json.loads((self.cycle / "CYCLE_MANIFEST.json").read_text())
        changes = manifest["source_snapshot"]["included_changes"]
        deletion = next(item for item in changes if item["path"] == "deleted.txt")
        rename = next(item for item in changes if item["path"] == "new-name.txt")
        self.assertEqual(deletion["content_state"], "deleted")
        self.assertIsNone(deletion["sha256"])
        self.assertIn("D", deletion["status"])
        self.assertEqual(rename["original_path"], "old-name.txt")
        self.assertIn("R", rename["status"])
        with zipfile.ZipFile(self.cycle / "outbox" / "SOURCE_SNAPSHOT.zip") as archive:
            self.assertNotIn("files/deleted.txt", archive.namelist())
            self.assertIn("files/new-name.txt", archive.namelist())

    def test_44_progression_document_must_match_task_identity_and_status(self) -> None:
        self.jobs[0].write_text(
            "# Wrong result\n\ntask_id: `different-task`\nstatus: completed\n",
            encoding="utf-8",
        )
        self._write_spec()
        with self.assertRaisesRegex(review_cycle.CycleError, "exact task_id"):
            self.publish()

    def test_45_partial_event_specific_receipt_is_rejected(self) -> None:
        self.publish()
        path = self.cycle / "receipts" / "PUBLISHED.json"
        receipt = json.loads(path.read_text())
        del receipt["outbox_sha256"]
        path.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(review_cycle.CycleError, "receipt fields"):
            self.record_trigger()

    def test_46_latest_consumed_skips_forged_higher_sequence_cycle(self) -> None:
        self.publish()
        self.complete_job4()
        self.write_response()
        self.consume()

        forged = self.root / ".chatgpt" / "pro-review" / "cycles" / "cycle-forged"
        (forged / "receipts").mkdir(parents=True)
        (forged / "CYCLE_MANIFEST.json").write_text(
            json.dumps(
                {
                    "schema_version": review_cycle.MANIFEST_SCHEMA,
                    "cycle_id": "cycle-forged",
                    "cycle_sequence": 999,
                    "manifest_root_sha256": "0" * 64,
                }
            ),
            encoding="utf-8",
        )
        (forged / "receipts" / "RESPONSE_CONSUMED.json").write_text(
            "{}\n", encoding="utf-8"
        )
        latest = review_cycle.latest_consumed_cycle(repository_root_path=self.root)
        self.assertEqual(latest["cycle_id"], "cycle-001")
        self.assertIn("cycle-forged", latest["skipped_invalid_cycles"])

    def test_47_v2_predecessor_requires_valid_accepted_response_chain(self) -> None:
        self.publish()
        self.complete_job4()
        self.write_response()
        self.consume()
        (self.cycle / "accepted" / "PRO_RESPONSE.md").write_text(
            "tampered\n", encoding="utf-8"
        )

        self.cycle_id = "cycle-002"
        self.cycle = self.root / ".chatgpt" / "pro-review" / "cycles" / self.cycle_id
        self.source = self.cycle / "source"
        self.source.mkdir(parents=True)
        self.spec_path = self.cycle / "CYCLE_SPEC.json"
        self.job4_task_id = "bounded-verification-2"
        self._write_authorization()
        self._write_spec(sequence=2, prior_cycle_id="cycle-001")
        with self.assertRaisesRegex(review_cycle.CycleError, "response bytes"):
            self.publish()

    def test_48_trigger_cannot_be_inserted_after_job4_completion(self) -> None:
        self.publish()
        self.complete_job4()
        with self.assertRaisesRegex(review_cycle.CycleError, "after Job 4 completion"):
            self.record_trigger()

    def test_49_missing_job4_report_blocks_response_consumption(self) -> None:
        self.publish()
        self.complete_job4()
        (self.cycle / "artifacts" / "JOB4_REPORT.md").unlink()
        self.write_response()
        with self.assertRaisesRegex(review_cycle.CycleError, "completed Job 4 report"):
            self.consume()

    def test_50_source_identity_survives_trigger_completion_and_consumption(self) -> None:
        self.publish()
        self.record_trigger()
        self.complete_job4()
        self.write_response()
        self.consume()
        manifest = json.loads((self.cycle / "CYCLE_MANIFEST.json").read_text())
        review_cycle_core.validate_snapshot_current(self.root, self.cycle, manifest)

    def test_51_snapshot_has_aggregate_file_and_byte_ceilings(self) -> None:
        with mock.patch.object(review_cycle_core, "MAX_SNAPSHOT_FILES", 1):
            with self.assertRaisesRegex(review_cycle.CycleError, "file-count ceiling"):
                self.publish()

    def test_52_status_labels_mutable_state_as_unverified_view(self) -> None:
        self.publish()
        status = review_cycle.cycle_status(
            self.cycle, repository_root_path=self.root
        )
        self.assertEqual(
            status["state_view_validation"], "not_performed_status_only"
        )

    def test_53_triggered_job4_recovers_then_completes_and_is_latest(self) -> None:
        self.publish()
        trigger = self.record_trigger()
        (self.cycle / "state" / "CYCLE_STATE.json").unlink()

        recovered = review_cycle.recover_cycle(
            self.cycle, repository_root_path=self.root
        )
        self.assertEqual(recovered["state"], review_cycle.STATE_JOB4_IN_PROGRESS)
        self.assertEqual(recovered["last_receipt"], "TRIGGER_SENT.json")
        self.assertEqual(
            recovered["last_receipt_sha256"],
            self._hash(self.cycle / "receipts" / "TRIGGER_SENT.json"),
        )
        self.assertEqual(trigger["event"], "review_trigger_sent")

        self.complete_job4()
        self.write_response()
        self.consume()
        final = review_cycle.recover_cycle(
            self.cycle, repository_root_path=self.root
        )
        self.assertEqual(final["state"], review_cycle.STATE_REVIEW_CONSUMED)
        latest = review_cycle.latest_consumed_cycle(repository_root_path=self.root)
        self.assertEqual(latest["cycle_id"], self.cycle_id)

    def test_54_sequence_seven_requires_all_advisory_planning_sections(self) -> None:
        self.publish()
        manifest = json.loads((self.cycle / "CYCLE_MANIFEST.json").read_text())
        template_hash = self._hash(self.cycle / "outbox" / "PRO_RESPONSE_TEMPLATE.md")
        with self.assertRaisesRegex(review_cycle.CycleError, "required planning section"):
            review_cycle_core.parse_response(
                self.response_bytes(),
                template_hash,
                require_planning_sections=True,
            )
        parsed = review_cycle_core.parse_response(
            self.response_bytes_with_planning_sections(),
            template_hash,
            require_planning_sections=True,
        )
        self.assertEqual(parsed["review_cycle_id"], manifest["cycle_id"])

    def test_54_trigger_retry_is_idempotent_only_for_exact_attestation(self) -> None:
        self.publish()
        first = self.record_trigger()
        second = self.record_trigger()
        self.assertEqual(first, second)

        message = self.cycle / "outbox" / "TRIGGER_MESSAGE.txt"
        with self.assertRaisesRegex(review_cycle.CycleError, "conflicting"):
            review_cycle.record_trigger(
                self.cycle,
                target_id="different-chatgpt-thread",
                app_result_json=json.dumps(
                    {"threadId": "different-chatgpt-thread"}, separators=(",", ":")
                ),
                message_sha256=self._hash(message),
                repository_root_path=self.root,
            )




if __name__ == "__main__":
    unittest.main()
