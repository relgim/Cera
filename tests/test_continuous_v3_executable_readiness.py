from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

from cera.creator_review import CreatorReviewAction
from cera.ids import IdKind, TypedId
from cera.serialization import canonical_sha256
from cera.sillytavern.campaign import (
    CONTINUOUS_V3_CALL_SCHEDULE,
    CONTINUOUS_V3_V2_RUN_IDENTITIES,
)
from cera.sillytavern.manual_routes import (
    PROVIDER_BACKED_MANUAL_ROUTE,
    PROVIDER_FREE_MANUAL_ROUTE,
)
from cera.sillytavern.models import SillyTavernChatRequest
from scripts import run_cera_sillytavern_continuous_manual as manual
from cera.sillytavern.campaign import (
    PROVIDER_FREE_PARTIAL_FAILURE_FIXTURE_SHA256,
)
from tools import pro_review_cycle_core as review_cycle


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_V1_ROOT = (
    ROOT / "runtime" / "evaluation" / "2026-08-02-continuous-sillytavern-two-run-v1"
)
SOURCE_DATABASE = ROOT / "runtime" / "development" / "hanezawa_human_test_v1_2.sqlite3"
QUALIFICATION_CYCLE_ID = "continuous-v3-executable-readiness-local-cycle"
QUALIFICATION_TASK_ID = "continuous-v3-executable-readiness-local-job4"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def _export_readiness_evidence(
    name: str,
    *,
    summary: dict,
    roots: tuple[tuple[str, Path], ...],
) -> None:
    raw_directory = os.environ.get("CERA_V3_READINESS_EVIDENCE_DIRECTORY")
    if raw_directory is None:
        return
    directory = Path(raw_directory).resolve()
    expected = (
        ROOT
        / ".chatgpt"
        / "pro-review"
        / "checkpoints"
        / "2026-08-02-continuous-sillytavern-overnight-v3-001"
        / "PROGRESSION_3_READINESS"
    ).resolve()
    if directory != expected:
        raise RuntimeError("readiness evidence directory is outside the checkpoint")
    directory.mkdir(parents=True, exist_ok=True)
    archive_path = directory / f"{name}.zip"
    result_path = directory / f"{name}.json"
    if archive_path.exists() or result_path.exists():
        raise FileExistsError("refusing to overwrite frozen readiness evidence")
    files: dict[str, str] = {}
    with ZipFile(archive_path, "x", compression=ZIP_DEFLATED) as archive:
        for prefix, root in roots:
            for path in sorted(item for item in root.rglob("*") if item.is_file()):
                if path.suffix.casefold() not in {
                    ".json",
                    ".jsonl",
                    ".md",
                    ".txt",
                    ".log",
                }:
                    continue
                relative = f"{prefix}/{path.relative_to(root).as_posix()}"
                archive.write(path, arcname=relative)
                files[relative] = _sha256(path)
    result = {
        "schema_version": "cera.continuous_v3_readiness_evidence.v1",
        "name": name,
        "provider_free": True,
        "external_provider_calls": 0,
        "summary": summary,
        "files": files,
        "files_sha256": canonical_sha256(files),
        "archive_relative_path": archive_path.name,
        "archive_sha256": _sha256(archive_path),
    }
    _write_json(result_path, result)


class ContinuousV3ExecutableReadinessTests(unittest.TestCase):
    maxDiff = None

    def _clone_environment(self, clone: Path) -> dict[str, str]:
        environment = dict(os.environ)
        entries = (str(clone / "src"), str(clone))
        existing = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = os.pathsep.join(
            (*entries, *((existing,) if existing else ()))
        )
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return environment

    def _publish_local_cycle(self, clone: Path, checkpoint_sha: str) -> tuple[Path, dict]:
        input_root = clone / "evaluation" / "local-v3-executable-readiness"
        input_root.mkdir(parents=True)
        jobs: list[Path] = []
        for index in range(1, 4):
            task_id = f"local-readiness-progression-{index}"
            path = input_root / f"PROGRESSION_{index}_RESULT.md"
            path.write_text(
                f"# Local readiness progression {index}\n\n"
                f"task_id: `{task_id}`\n"
                "status: `completed`\n",
                encoding="utf-8",
            )
            jobs.append(path)
        evidence = input_root / "LOCAL_READINESS_EVIDENCE.zip"
        with ZipFile(evidence, "x", compression=ZIP_DEFLATED) as archive:
            archive.writestr(
                "README.txt",
                "Disposable provider-free executable-readiness authority.\n",
            )

        cycle = (
            clone
            / ".chatgpt"
            / "pro-review"
            / "cycles"
            / QUALIFICATION_CYCLE_ID
        )
        source = cycle / "source"
        source.mkdir(parents=True)
        scope = (
            "Run only the disposable local fake-transport V2 executable and manual "
            "readiness qualification with zero external provider calls."
        )
        authority_source = clone / "docs" / "START_HERE.md"
        authorization = {
            "schema_version": review_cycle.JOB4_AUTHORIZATION_SCHEMA,
            "cycle_id": QUALIFICATION_CYCLE_ID,
            "task_id": QUALIFICATION_TASK_ID,
            "scope": scope,
            "scope_sha256": hashlib.sha256(scope.encode()).hexdigest(),
            "expected_result_relative_path": "source/JOB4_RESULT.json",
            "authority_source_path": str(authority_source),
            "authority_source_sha256": _sha256(authority_source),
            "explicit_exclusions": [
                "no external provider calls",
                "no story or persistent database writes",
                "no route, service, deployment, remote, merge, or push changes",
            ],
        }
        authorization_path = source / "JOB4_AUTHORIZATION.json"
        _write_json(authorization_path, authorization)
        spec = {
            "schema_version": review_cycle.SPEC_SCHEMA,
            "cycle_id": QUALIFICATION_CYCLE_ID,
            "cycle_sequence": 1,
            "checkpoint": {
                "id": "local-v3-executable-readiness-checkpoint",
                "git_sha": checkpoint_sha,
                "evidence_path": str(evidence),
                "evidence_sha256": _sha256(evidence),
            },
            "jobs_1_3": [
                {
                    "task_id": f"local-readiness-progression-{index}",
                    "result_path": str(path),
                    "result_sha256": _sha256(path),
                }
                for index, path in enumerate(jobs, 1)
            ],
            "prior_cycle_id": None,
            "job4": {
                "task_id": QUALIFICATION_TASK_ID,
                "scope": scope,
                "authorization_record_path": str(authorization_path),
                "authorization_record_sha256": _sha256(authorization_path),
            },
            "review_context": {
                "creator_goal": "Prove the final provider-free V3 executable path.",
                "starting_baseline_or_prior_checkpoint": checkpoint_sha,
                "selection_rationale": [
                    "Bind the disposable repository cycle.",
                    "Exercise the actual parent and child process path.",
                    "Exercise recovery and exact accounting.",
                ],
                "diff_summary": "Disposable clone and runtime evidence only.",
                "focused_tests": "This local fixture is the focused executable gate.",
                "complete_suite": "Owned by the authoritative parent checkpoint.",
                "active_profile_before": "unchanged",
                "active_profile_after": "unchanged",
                "provider_calls_and_cost": "zero external calls and zero cost",
                "retry_and_fallback": "zero retries and zero fallbacks",
                "story_database_and_branch_effects": "zero persistent effects",
                "user_visible_effect": "none",
                "historical_evidence_integrity": "verified before and after",
                "unresolved_defects": "none introduced by the fixture",
                "uncertainty_and_risks": "disposable local qualification only",
                "disagreement_with_prior_review": "none",
                "codex_advisory_next_candidates": ["Run the frozen complete suite."],
                "questions_for_chatgpt_pro": ["Are any readiness gaps material?"],
                "explicit_exclusions": ["providers", "story", "deployment"],
            },
            "review_snapshot": {
                "baseline_git_sha": checkpoint_sha,
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
                        "path_prefix": ".chatgpt/pro-review/sequence-authority/",
                        "reason_code": "sequence_authority_transport_state",
                    },
                    {
                        "path_prefix": (
                            f".chatgpt/pro-review/cycles/{QUALIFICATION_CYCLE_ID}/"
                        ),
                        "reason_code": "current_cycle_transport_state",
                    },
                ],
            },
        }
        spec_path = cycle / "CYCLE_SPEC.json"
        _write_json(spec_path, spec)
        review_cycle.publish_cycle(cycle, spec_path, repository_root_path=clone)
        message = cycle / "outbox" / "TRIGGER_MESSAGE.txt"
        target = "local-provider-free-readiness-fixture"
        review_cycle.record_trigger(
            cycle,
            target_id=target,
            app_result_json=json.dumps({"threadId": target}, separators=(",", ":")),
            message_sha256=_sha256(message),
            repository_root_path=clone,
        )
        manifest = json.loads((cycle / "CYCLE_MANIFEST.json").read_text())
        return cycle, manifest

    def _campaign_command(
        self,
        *,
        clone: Path,
        cycle: Path,
        manifest: dict,
        source_database: Path,
        runtime_root: Path,
        prior_root: Path | None = None,
    ) -> list[str]:
        command = [
            sys.executable,
            str(clone / "scripts" / "run_sillytavern_continuous_v3_campaign.py"),
            "--confirm-v2-campaign",
            "--cycle-directory",
            str(cycle),
            "--source-database",
            str(source_database),
            "--runtime-root",
            str(runtime_root),
            "--historical-v1-campaign-root",
            str(HISTORICAL_V1_ROOT),
            "--transport-mode",
            "non_network_fake_ports",
            "--expected-checkpoint-sha",
            manifest["checkpoint"]["git_sha"],
            "--expected-cycle-id",
            manifest["cycle_id"],
            "--expected-cycle-sequence",
            str(manifest["cycle_sequence"]),
            "--expected-job4-task-id",
            manifest["job4"]["task_id"],
            "--expected-authorization-sha256",
            manifest["job4"]["authorization_record_sha256"],
            "--provider-free-failure-run-id",
            CONTINUOUS_V3_V2_RUN_IDENTITIES[0],
            "--confirm-provider-free-partial-failure-fixture-sha256",
            PROVIDER_FREE_PARTIAL_FAILURE_FIXTURE_SHA256,
        ]
        if prior_root is not None:
            command.extend(("--prior-v2-campaign-root", str(prior_root)))
        return command

    def test_actual_v2_parent_child_failure_recovery_and_long_root(self) -> None:
        self.assertTrue(HISTORICAL_V1_ROOT.is_dir())
        self.assertTrue(SOURCE_DATABASE.is_file())
        historical_before = _tree_hashes(HISTORICAL_V1_ROOT)
        with TemporaryDirectory(prefix="c3-ready-", dir="D:\\") as temporary:
            temporary_root = Path(temporary)
            clone = temporary_root / "r"
            cloned = subprocess.run(
                [
                    "git",
                    "clone",
                    "--quiet",
                    "--local",
                    "--no-hardlinks",
                    "--no-tags",
                    str(ROOT),
                    str(clone),
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=120,
            )
            self.assertEqual(cloned.returncode, 0, cloned.stdout + cloned.stderr)
            checkpoint_sha = subprocess.run(
                ["git", "-C", str(clone), "rev-parse", "HEAD"],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(
                checkpoint_sha,
                subprocess.run(
                    ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                    text=True,
                    capture_output=True,
                    check=True,
                ).stdout.strip(),
            )
            cycle, manifest = self._publish_local_cycle(clone, checkpoint_sha)
            source_database = clone / "runtime" / "source" / SOURCE_DATABASE.name
            source_database.parent.mkdir(parents=True)
            shutil.copy2(SOURCE_DATABASE, source_database)
            source_hash = _sha256(source_database)
            evidence_parent = clone / "runtime"
            first_root = evidence_parent / "campaign-001"
            second_root = evidence_parent / "campaign-002"
            expected_branch_root = (
                second_root
                / "runs"
                / CONTINUOUS_V3_V2_RUN_IDENTITIES[1]
                / "worlds"
                / "hanezawa-job4"
                / "canary-main"
            )
            self.assertGreaterEqual(len(str(expected_branch_root.resolve())), 134)
            environment = self._clone_environment(clone)

            first = subprocess.run(
                self._campaign_command(
                    clone=clone,
                    cycle=cycle,
                    manifest=manifest,
                    source_database=source_database,
                    runtime_root=first_root,
                ),
                cwd=clone,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
                timeout=180,
            )
            self.assertEqual(first.returncode, 1, first.stdout + first.stderr)
            self.assertTrue(
                (first_root / "CAMPAIGN_RESULT.json").is_file(),
                first.stdout + first.stderr,
            )
            first_result = json.loads(
                (first_root / "CAMPAIGN_RESULT.json").read_text(encoding="utf-8")
            )
            first_run = first_result["campaign"]["runs"]
            self.assertEqual(len(first_run), 1)
            self.assertEqual(first_run[0]["state"], "failed")
            self.assertEqual(first_run[0]["calls"], list(CONTINUOUS_V3_CALL_SCHEDULE[:3]))
            self.assertEqual(first_result["external_provider_calls"], 0)
            immutable_first = _tree_hashes(first_root)

            second = subprocess.run(
                self._campaign_command(
                    clone=clone,
                    cycle=cycle,
                    manifest=manifest,
                    source_database=source_database,
                    runtime_root=second_root,
                    prior_root=first_root,
                ),
                cwd=clone,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
                timeout=240,
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            result = json.loads(
                (second_root / "CAMPAIGN_RESULT.json").read_text(encoding="utf-8")
            )
            campaign = result["campaign"]
            self.assertEqual(result["status"], "completed_two_consecutive_runs_passed")
            self.assertEqual(campaign["controlled_restart_count"], 1)
            self.assertEqual(campaign["consecutive_passes"], 2)
            self.assertEqual([run["state"] for run in campaign["runs"]], ["failed", "passed", "passed"])
            passing_calls = [
                label
                for run in campaign["runs"]
                if run["state"] == "passed"
                for label in run["calls"]
            ]
            self.assertEqual(len(passing_calls), 20)
            self.assertEqual(sum("deepseek" not in label for label in passing_calls), 14)
            self.assertEqual(sum("deepseek" in label for label in passing_calls), 6)
            self.assertEqual(result["external_provider_calls"], 0)
            configuration = json.loads(
                (second_root / "CAMPAIGN_CONFIGURATION.json").read_text()
            )
            self.assertEqual(
                result["historical_v1_debit_sha256"],
                configuration["historical_v1_debit"]["debit_sha256"],
            )
            self.assertEqual(_tree_hashes(first_root), immutable_first)
            self.assertEqual(_sha256(source_database), source_hash)
            self.assertFalse(_port_open(5113))
            snapshot_paths = tuple(second_root.rglob("SESSION_SNAPSHOT.json"))
            self.assertTrue(snapshot_paths)
            self.assertLessEqual(max(len(str(path.resolve())) for path in snapshot_paths), 248)
            _export_readiness_evidence(
                "V2_EXECUTABLE_RECOVERY",
                summary={
                    "checkpoint_git_sha": checkpoint_sha,
                    "cycle_manifest_sha256": _sha256(
                        cycle / "CYCLE_MANIFEST.json"
                    ),
                    "failed_run_id": campaign["runs"][0]["run_id"],
                    "failed_stage_invocations": len(
                        campaign["runs"][0]["calls"]
                    ),
                    "passing_run_ids": [
                        run["run_id"]
                        for run in campaign["runs"]
                        if run["state"] == "passed"
                    ],
                    "passing_codex_family_stage_invocations": 14,
                    "passing_deepseek_stage_invocations": 6,
                    "controlled_restart_count": campaign[
                        "controlled_restart_count"
                    ],
                    "historical_v1_codex_debit": 1,
                    "long_branch_root_characters": len(
                        str(expected_branch_root.resolve())
                    ),
                    "maximum_snapshot_path_characters": max(
                        len(str(path.resolve())) for path in snapshot_paths
                    ),
                    "source_database_sha256": source_hash,
                    "failed_root_immutable_after_recovery": True,
                },
                roots=(
                    ("local_cycle", cycle),
                    ("failed_campaign", first_root),
                    ("recovered_campaign", second_root),
                ),
            )
        self.assertEqual(_tree_hashes(HISTORICAL_V1_ROOT), historical_before)

    def test_provider_backed_manual_actual_process_fake_ports(self) -> None:
        launcher = ROOT / "scripts" / "run_cera_sillytavern_continuous_provider_manual.py"
        with TemporaryDirectory(
            prefix="provider-backed-manual-",
            dir=ROOT / "runtime" / "manual",
        ) as temporary:
            manual_root = Path(temporary) / "route"

            def invoke(*arguments: str, check: bool = True) -> dict:
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(launcher),
                        "--transport-mode",
                        "non_network_fake_ports",
                        *arguments,
                        "--root",
                        str(manual_root),
                    ],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=90,
                )
                if check:
                    self.assertEqual(
                        completed.returncode,
                        0,
                        completed.stdout + completed.stderr,
                    )
                self.assertTrue(completed.stdout.strip(), completed.stderr)
                return json.loads(completed.stdout)

            try:
                invoke(
                    "reset",
                    "--session-id",
                    PROVIDER_BACKED_MANUAL_ROUTE.default_session_id,
                    "--confirm-reset",
                )
                started = invoke("start")
                self.assertEqual(started["status"], "running")
                first = invoke(
                    "submit",
                    "--message",
                    "Ted knocks and asks Sakura whether this is the Hanezawa residence.",
                )
                self.assertEqual(first["http_status"], 200, first)
                first_id = first["body"]["cera"]["provisional_review_id"]
                first_review = invoke("review", "--review-id", first_id)["body"]
                self.assertEqual(first_review["state"], "review_ready")
                for field in (
                    "candidate_sha256",
                    "candidate_text_sha256",
                    "sequence_plan_sha256",
                    "validator_package_sha256",
                    "review_binding_sha256",
                ):
                    self.assertRegex(first_review[field], r"^[0-9a-f]{64}$")
                first_accept = invoke(
                    "decide",
                    "--review-id",
                    first_id,
                    "--action",
                    "accept",
                )
                self.assertEqual(first_accept["body"]["status"], "accepted")
                invoke("stop")
                restarted = invoke("start")
                self.assertNotEqual(
                    restarted["process_instance_sha256"],
                    started["process_instance_sha256"],
                )
                pending = invoke(
                    "submit",
                    "--message",
                    "Continue the doorway conversation with only the current character.",
                )
                self.assertEqual(pending["http_status"], 200, pending)
                pending_id = pending["body"]["cera"]["provisional_review_id"]
                invoke("stop")
                invoke("start")
                recovered = invoke("review", "--review-id", pending_id)["body"]
                self.assertTrue(recovered["recovered_after_restart"])
                self.assertFalse(recovered["accept_enabled"])
                self.assertTrue(recovered["decline_enabled"])
                self.assertEqual(
                    recovered["active_character_ids"],
                    first_review["active_character_ids"],
                )
                declined = invoke(
                    "decide",
                    "--review-id",
                    pending_id,
                    "--action",
                    "decline",
                )
                self.assertEqual(declined["body"]["status"], "rejected")
                changed = invoke(
                    "submit",
                    "--message",
                    "The next morning, Ted is in the kitchen with Mia and asks about Sakura.",
                    "--scene-change",
                )
                self.assertEqual(changed["http_status"], 200, changed)
                changed_id = changed["body"]["cera"]["provisional_review_id"]
                accepted = invoke(
                    "decide",
                    "--review-id",
                    changed_id,
                    "--action",
                    "accept",
                )
                self.assertEqual(accepted["body"]["status"], "accepted")
                stopped = invoke("stop")
                self.assertFalse(stopped["port_open"])
                isolation = invoke("verify-isolation")
                self.assertTrue(isolation["passed"], isolation)
                self.assertFalse(_port_open(PROVIDER_BACKED_MANUAL_ROUTE.port))
                _export_readiness_evidence(
                    "PROVIDER_BACKED_MANUAL_FAKE_ROUTE",
                    summary={
                        "profile_id": PROVIDER_BACKED_MANUAL_ROUTE.profile_id,
                        "model": PROVIDER_BACKED_MANUAL_ROUTE.model,
                        "port": PROVIDER_BACKED_MANUAL_ROUTE.port,
                        "accepted_turns": 2,
                        "declined_recovered_turns": 1,
                        "scene_changes": 1,
                        "process_restarts": 2,
                        "exact_validator_hash_projection": True,
                        "durable_active_cast": True,
                        "thread_terminalization_verified": True,
                        "isolation_passed": True,
                    },
                    roots=(("manual_root", manual_root),),
                )
            finally:
                if _port_open(PROVIDER_BACKED_MANUAL_ROUTE.port):
                    invoke("stop", check=False)

    def test_provider_backed_pending_decision_recovery_fails_closed(self) -> None:
        manual_parent = ROOT / "runtime" / "manual"
        manual_parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=manual_parent) as temporary:
            root = Path(temporary) / "pending-decision"
            try:
                manual.configure_manual_execution(
                    PROVIDER_BACKED_MANUAL_ROUTE,
                    transport_mode="non_network_fake_ports",
                )
                manual.reset_manual_root(
                    root,
                    session_id=PROVIDER_BACKED_MANUAL_ROUTE.default_session_id,
                )
                adapter, harness, stack = manual.build_provider_backed_manual_adapter(
                    root,
                    process_instance_id="provider-backed-pending-one",
                )
                reply = adapter.complete(
                    SillyTavernChatRequest.from_mapping(
                        {
                            "model": PROVIDER_BACKED_MANUAL_ROUTE.model,
                            "stream": False,
                            "cera_session_id": (
                                PROVIDER_BACKED_MANUAL_ROUTE.default_session_id
                            ),
                            "cera_profile_id": PROVIDER_BACKED_MANUAL_ROUTE.profile_id,
                            "cera_scene_change": False,
                            "messages": [
                                {"role": "user", "content": "Sakura answers."}
                            ],
                        }
                    )
                )
                review_id = TypedId.parse(
                    str(reply.provisional_review_id), IdKind.REVIEW_PACKET
                )
                record = adapter.get_review(review_id)
                adapter.state_store.begin_decision(
                    record,
                    CreatorReviewAction.ACCEPT,
                    process_instance_id=adapter.process_instance_id,
                )
                payload = adapter.review_payload(record)
                self.assertTrue(payload["decision_pending"])
                self.assertFalse(payload["accept_enabled"])
                self.assertFalse(payload["decline_enabled"])
                stack.close()
                with self.assertRaisesRegex(Exception, "cannot be proven"):
                    manual.build_provider_backed_manual_adapter(
                        root,
                        process_instance_id="provider-backed-pending-two",
                    )
                self.assertEqual(harness.provider_calls, 0)
            finally:
                manual.configure_manual_execution(
                    PROVIDER_FREE_MANUAL_ROUTE,
                    transport_mode="scripted_provider_free",
                )


if __name__ == "__main__":
    unittest.main()
