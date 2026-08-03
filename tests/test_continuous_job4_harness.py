from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, replace
import json
import re
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cera.continuous.job4_transaction as job4_transaction
from cera.continuous.prompting import PLANNER_STABLE_INSTRUCTIONS
from scripts.run_continuous_planner_validator_job4 import (
    BRANCH_ID,
    JobHarness,
    build_canonical_job4_result,
    validate_job4_identity,
    StablePrefixTransport,
    WORLD_ID,
    build_report,
    compatibility,
    _freeze_terminal_publication,
    source_character_summary,
    seed_world,
    validate_declared_unittest_ids,
    git_head,
)
from cera.continuous.call_ledger import (
    ContinuousProviderCallLedger,
    ProviderCallState,
)
from cera.continuous.job4_terminal import (
    ContinuousJob4CapabilityBoundaryEvidenceV1,
    ContinuousJob4CapabilityContainerV1,
    ContinuousJob4CapabilityCustody,
    ContinuousJob4CapabilityLedgerV1,
    ContinuousJob4OperationalCountersV1,
    ContinuousJob4PostconditionsV1,
    ContinuousJob4TerminalEvidenceV1,
    ContinuousJob4TerminalEvidenceV2,
    ContinuousJob4TerminalEvidenceV3,
    ContinuousJob4TerminalEvidenceV4,
    ContinuousJob4TerminalEvidenceV5,
    decode_continuous_job4_terminal_evidence,
)
from cera.continuous.job4_transaction import (
    ContinuousJob4TerminalTransactionV1,
)
from cera.continuous.contracts import (
    CharacterRoleLedgerV1,
    StoryRealizationKind,
    StoryRealizationSegmentV1,
)
from cera.continuous.provider import (
    ContinuousDeepSeekAssertionKind,
    ContinuousDeepSeekNonOwningRelationKind,
    ContinuousDeepSeekNonOwningRoleDraftV1,
    ContinuousDeepSeekStorySegmentDraftV1,
    ContinuousDeepSeekWireDraftV1,
    ContinuousSceneWriterDraftV1,
    ContinuousSemanticValidatorDraftV1,
    ContinuousSemanticValidatorResultV1,
    ContinuousValidatorDraftV1,
    ProviderEventRecordDraftV1,
    ProviderSceneSummaryDraftV1,
    _transport_stored_thread_sha256,
    continuous_deepseek_route,
    continuous_planner_route,
    continuous_validator_route,
)
from cera.continuous.sessions import (
    CONTINUOUS_ACCEPTED_SNAPSHOT_MAX_RESOLVED_CHARS,
    ContinuousSessionCoordinator,
    ContinuousSessionRole,
    ContinuousThreadArchiveEvidenceV1,
    InMemoryContinuousStoredSessionPort,
)
from cera.continuous.scripted_job4 import SCRIPTED_JOB4_FIXTURE_SHA256
from cera.continuous.world import ContinuousWorldStore
from cera.providers import (
    CodexMcpRuntimeBinding,
    CodexSDKTransport,
    DeepSeekChatTransport,
    DeepSeekMessage,
    ProviderOutputMode,
    ProviderTransportError,
)
from cera.providers.codex import CodexWorkerResult
from cera.providers.codex import _SubprocessCodexRunner
from cera.serialization import canonical_sha256, text_sha256, to_primitive
from tests.test_continuous_world import (
    composer_draft,
    package,
    rich_sequence,
    scene_summary_package,
)


ROOT = Path(__file__).resolve().parents[1]


def terminalized_detail(
    *,
    cycle_id: str,
    execution_status: str,
    execution_mode: str,
    provider_calls: int,
    scripted_transport_invocations: int,
    operational_counters: ContinuousJob4OperationalCountersV1 | None = None,
    postcondition_changes: dict[str, object] | None = None,
) -> dict[str, object]:
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
    base = {
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
    base.update(postcondition_changes or {})
    postconditions = ContinuousJob4PostconditionsV1(**base)
    terminal = ContinuousJob4TerminalEvidenceV1.build(
        execution_status=execution_status,
        provider_calls=provider_calls,
        operational_counters=counters,
        postconditions=postconditions,
    )
    effects = terminal.effect_evidence.canonical_effects
    return {
        "cycle_id": cycle_id,
        "status": terminal.status,
        "execution_status": execution_status,
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
        "failure": None if terminal.status == "completed" else {
            "stage": "terminal_postconditions"
        },
    }


class _Transport:
    route = SimpleNamespace()

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def invoke(self, prompt: str, **kwargs):
        self.prompts.append(prompt)
        return SimpleNamespace(prompt=prompt)


@dataclass(frozen=True, slots=True)
class _ScriptedReceipt:
    requested_model: str
    external_provider_calls: int = 0


@dataclass(frozen=True, slots=True)
class _ScriptedTelemetry:
    provider_thread_id_sha256: str
    model: str
    reasoning_effort: str
    fast_mode_enabled: bool = False


class _ScriptedCodexTransport:
    def __init__(self, route, thread_id: str, produce) -> None:
        self.route = route
        self.runner = SimpleNamespace(provider_thread_id=thread_id)
        self._thread_id = thread_id
        self._produce = produce

    def invoke(
        self,
        prompt: str,
        *,
        on_worker_started=None,
        on_worker_preflight=None,
        on_transport_invoke=None,
        **_kwargs,
    ):
        for callback in (
            on_worker_started,
            on_worker_preflight,
            on_transport_invoke,
        ):
            if callback is not None:
                callback()
        value = self._produce(prompt)
        return SimpleNamespace(
            parsed_json=to_primitive(value),
            receipt=_ScriptedReceipt(
                requested_model=self.route.model_name,
            ),
            operation_telemetry=_ScriptedTelemetry(
                provider_thread_id_sha256=text_sha256(self._thread_id),
                model=self.route.model_name,
                reasoning_effort=self.route.reasoning_effort,
            ),
            tool_call_count=0,
            failed_tool_call_count=0,
            tool_names=(),
            tool_server_names=(),
        )


class _ScriptedDeepSeekTransport:
    def __init__(self, produce) -> None:
        self.route = continuous_deepseek_route()
        self._produce = produce

    def invoke(self, messages, *, on_transport_invoke=None, **_kwargs):
        if on_transport_invoke is not None:
            on_transport_invoke()
        prompt = messages[-1].content
        return SimpleNamespace(
            parsed_json=to_primitive(self._produce(prompt)),
            receipt=_ScriptedReceipt(
                requested_model=self.route.model_name,
            ),
            tool_names=(),
            tool_server_names=(),
        )


class ContinuousJob4HarnessTests(unittest.TestCase):
    def test_actual_cli_completes_closed_provider_free_scripted_v7_mode(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cycle = root / "cycle"
            (cycle / "receipts").mkdir(parents=True)
            authorization = "a" * 64
            cycle_id = "cycle:scripted-cli-v7"
            task_id = "task:scripted-cli-v7"
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
            try:
                connection.execute("CREATE TABLE qualification(value TEXT)")
                connection.execute("INSERT INTO qualification VALUES ('unchanged')")
                connection.commit()
            finally:
                connection.close()
            runtime_root = root / "runtime"
            completed = subprocess.run(
                (
                    sys.executable,
                    str(ROOT / "scripts" / "run_continuous_planner_validator_job4.py"),
                    "--confirm-provider-free-scripted-v7",
                    "--expected-scripted-fixture-sha256",
                    SCRIPTED_JOB4_FIXTURE_SHA256,
                    "--cycle-directory",
                    str(cycle),
                    "--source-database",
                    str(source_database),
                    "--runtime-root",
                    str(runtime_root),
                    "--expected-checkpoint-sha",
                    git_head(ROOT),
                    "--expected-cycle-id",
                    cycle_id,
                    "--expected-task-id",
                    task_id,
                    "--expected-authorization-sha256",
                    authorization,
                    "--maximum-provider-calls",
                    "10",
                ),
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=120,
                check=False,
            )
            detail_path = runtime_root / "JOB4_DETAIL.json"
            failure_detail = (
                detail_path.read_text(encoding="utf-8")
                if detail_path.is_file()
                else ""
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr + completed.stdout + failure_detail,
            )
            detail = json.loads(detail_path.read_text())
            result = json.loads((cycle / "source" / "JOB4_RESULT.json").read_text())
            self.assertEqual(detail["status"], "completed")
            self.assertEqual(detail["execution_mode"], "provider_free_scripted_v7")
            self.assertEqual(detail["provider_calls"], 0)
            self.assertEqual(detail["scripted_transport_invocations"], 10)
            self.assertEqual(len(detail["calls"]), 10)
            self.assertEqual(detail["thread_archival"], {"planner": True, "validator": True})
            self.assertEqual(
                set(detail["thread_archival_evidence"]),
                {"planner", "validator"},
            )
            self.assertTrue(
                all(
                    evidence["archive_request_completed"]
                    and evidence["resume_succeeded_after_archive"] is False
                    and evidence["backend_selectable_after_archive"] is False
                    and evidence[
                        "coordinator_selectable_as_accepted_ancestry"
                    ]
                    is False
                    and evidence["verified"]
                    for evidence in detail["thread_archival_evidence"].values()
                )
            )
            self.assertTrue(detail["source_database_unchanged"])
            self.assertTrue(detail["copy_database_unchanged"])
            self.assertTrue(detail["active_route_unchanged"])
            terminal_profile = detail["terminal_evidence"]["postconditions"]
            self.assertEqual(
                terminal_profile["active_profile_sha256_before"],
                detail["active_runtime_before"]["profile_sha256"],
            )
            self.assertEqual(
                terminal_profile["active_profile_sha256_after"],
                detail["active_runtime_after"]["profile_sha256"],
            )
            self.assertEqual(result["effects"]["provider_calls"], 0)
            self.assertNotIn("authorization_sha256", result)
            self.assertNotIn("scripted_transport_invocations", result["effects"])
            self.assertIn(
                "scripted_transport_invocations=10",
                result["verification"][0]["summary"],
            )
            self.assertEqual(
                result["schema_version"], "cera.pro_review_job4_result.v2"
            )
            terminal_path = cycle / "source" / "JOB4_TERMINAL_EVIDENCE.json"
            terminal = decode_continuous_job4_terminal_evidence(
                json.loads(terminal_path.read_text(encoding="utf-8"))
            )
            self.assertIsInstance(terminal, ContinuousJob4TerminalEvidenceV5)
            self.assertEqual(result["terminal_evidence_sha256"], terminal.sha256)
            self.assertEqual(
                result["terminal_evidence_sha256"],
                text_sha256(terminal_path.read_text(encoding="utf-8")),
            )
            self.assertTrue(
                terminal.capability_ledger.all_zero_effects_structurally_denied
            )
            self.assertTrue(terminal.capability_boundary_evidence.enforced)
            self.assertEqual(
                set(terminal.thread_archival_evidence),
                {"planner", "validator"},
            )
            self.assertEqual(
                terminal.postconditions.thread_archival,
                {
                    role: evidence.verified
                    for role, evidence in terminal.thread_archival_evidence.items()
                },
            )

    def test_terminal_archive_invalidates_resume_and_accepted_ancestry(self) -> None:
        with TemporaryDirectory() as directory:
            world = ContinuousWorldStore(Path(directory).resolve() / "worlds")
            seed_world(world, ROOT)
            port = InMemoryContinuousStoredSessionPort()
            coordinator = ContinuousSessionCoordinator(
                compatibility(world, ContinuousSessionRole.PLANNER),
                port,
            )
            handle = coordinator.ensure_session()
            evidence = coordinator.archive_and_verify_terminal("job4_complete")
            self.assertTrue(evidence.verified)
            self.assertEqual(
                ContinuousThreadArchiveEvidenceV1.from_dict(evidence.to_dict()),
                evidence,
            )
            contradictory = evidence.to_dict()
            contradictory["verified"] = False
            with self.assertRaisesRegex(Exception, "contradictory"):
                ContinuousThreadArchiveEvidenceV1.from_dict(contradictory)
            self.assertFalse(port.resume(handle))
            self.assertFalse(port.selectable_as_active_or_accepted_ancestry(handle))
            with self.assertRaisesRegex(Exception, "terminally archived"):
                coordinator.ensure_session()

    def test_terminal_v3_owns_complete_archival_dtos_and_derived_status(self) -> None:
        verified = {
            role.value: ContinuousThreadArchiveEvidenceV1(
                role=role,
                provider_thread_id_sha256=text_sha256(
                    f"terminal-v3-{role.value}"
                ),
                archive_reason_sha256=text_sha256("terminal-v3-complete"),
                archive_request_completed=True,
                resume_succeeded_after_archive=False,
                backend_selectable_after_archive=False,
                coordinator_selectable_as_accepted_ancestry=False,
            )
            for role in (
                ContinuousSessionRole.PLANNER,
                ContinuousSessionRole.VALIDATOR,
            )
        }

        def postconditions(
            archival: dict[str, bool],
        ) -> ContinuousJob4PostconditionsV1:
            return ContinuousJob4PostconditionsV1(
                execution_mode="provider_free_scripted_v8",
                source_database_sha256_before="1" * 64,
                source_database_sha256_after="1" * 64,
                disposable_database_sha256_before="2" * 64,
                disposable_database_sha256_after="2" * 64,
                database_integrity_check="ok",
                database_foreign_key_findings=0,
                active_profile_sha256_before="3" * 64,
                active_profile_sha256_after="3" * 64,
                active_profile_inspection_status="verified",
                thread_archival=archival,
                accepted_session_synchronized=True,
                accepted_final_sequences_injected=True,
                call_ledger_dispatches=10,
                scripted_transport_invocations=10,
            )

        terminal = ContinuousJob4TerminalEvidenceV3.build(
            execution_status="completed",
            provider_calls=0,
            capability_ledger=ContinuousJob4CapabilityCustody().evidence,
            postconditions=postconditions(
                {role: value.verified for role, value in verified.items()}
            ),
            thread_archival_evidence=verified,
        )
        self.assertEqual(terminal.status, "completed")
        self.assertEqual(
            decode_continuous_job4_terminal_evidence(terminal.to_dict()),
            terminal,
        )

        variants = {
            "resume_success": replace(
                verified["planner"], resume_succeeded_after_archive=True
            ),
            "backend_selectable": replace(
                verified["planner"], backend_selectable_after_archive=True
            ),
            "unknown_resume": replace(
                verified["planner"], resume_succeeded_after_archive=None
            ),
            "verification_error": replace(
                verified["planner"], selection_error_type="RuntimeError"
            ),
        }
        for label, invalid in variants.items():
            with self.subTest(label=label):
                evidence = {**verified, "planner": invalid}
                failed = ContinuousJob4TerminalEvidenceV3.build(
                    execution_status="completed",
                    provider_calls=0,
                    capability_ledger=ContinuousJob4CapabilityCustody().evidence,
                    postconditions=postconditions(
                        {
                            role: value.verified
                            for role, value in evidence.items()
                        }
                    ),
                    thread_archival_evidence=evidence,
                )
                self.assertEqual(failed.status, "failed")
                self.assertIn("thread_archival_incomplete", failed.failure_codes)

        with self.assertRaisesRegex(ValueError, "fields differ"):
            ContinuousJob4TerminalEvidenceV3.build(
                execution_status="completed",
                provider_calls=0,
                capability_ledger=ContinuousJob4CapabilityCustody().evidence,
                postconditions=postconditions(
                    {"planner": True, "validator": True}
                ),
                thread_archival_evidence={"planner": verified["planner"]},
            )
        with self.assertRaisesRegex(ValueError, "role changed"):
            ContinuousJob4TerminalEvidenceV3.build(
                execution_status="completed",
                provider_calls=0,
                capability_ledger=ContinuousJob4CapabilityCustody().evidence,
                postconditions=postconditions(
                    {"planner": True, "validator": True}
                ),
                thread_archival_evidence={
                    "planner": verified["validator"],
                    "validator": verified["validator"],
                },
            )
        with self.assertRaisesRegex(ValueError, "not derived"):
            invalid = {
                **verified,
                "planner": variants["resume_success"],
            }
            ContinuousJob4TerminalEvidenceV3.build(
                execution_status="completed",
                provider_calls=0,
                capability_ledger=ContinuousJob4CapabilityCustody().evidence,
                postconditions=postconditions(
                    {"planner": True, "validator": True}
                ),
                thread_archival_evidence=invalid,
            )

    def test_actual_cli_completes_closed_provider_free_scripted_v10_lean_v2_mode(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cycle = root / "cycle"
            (cycle / "receipts").mkdir(parents=True)
            authorization = "b" * 64
            cycle_id = "cycle:scripted-cli-v10-lean-v2"
            task_id = "task:scripted-cli-v10-lean-v2"
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
            try:
                connection.execute("CREATE TABLE qualification(value TEXT)")
                connection.execute("INSERT INTO qualification VALUES ('unchanged')")
                connection.commit()
            finally:
                connection.close()
            runtime_root = root / "runtime"
            expected_branch_root = (
                runtime_root / "worlds" / WORLD_ID / BRANCH_ID
            )
            while len(str(expected_branch_root)) < 133:
                remaining = 133 - len(str(expected_branch_root))
                runtime_root /= "r" * max(1, remaining - 1)
                expected_branch_root = (
                    runtime_root / "worlds" / WORLD_ID / BRANCH_ID
                )
            self.assertGreaterEqual(len(str(expected_branch_root)), 133)
            self.assertLessEqual(len(str(expected_branch_root)), 134)
            completed = subprocess.run(
                (
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
                    str(runtime_root),
                    "--expected-checkpoint-sha",
                    git_head(ROOT),
                    "--expected-cycle-id",
                    cycle_id,
                    "--expected-task-id",
                    task_id,
                    "--expected-authorization-sha256",
                    authorization,
                    "--maximum-provider-calls",
                    "10",
                ),
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=120,
                check=False,
            )
            detail_path = runtime_root / "JOB4_DETAIL.json"
            failure_detail = (
                detail_path.read_text(encoding="utf-8")
                if detail_path.is_file()
                else ""
            )
            diagnostic_path = runtime_root / "ROOT_DIAGNOSTIC.json"
            failure_diagnostic = (
                diagnostic_path.read_text(encoding="utf-8")
                if diagnostic_path.is_file()
                else ""
            )
            self.assertEqual(
                completed.returncode,
                0,
                (
                    completed.stderr
                    + completed.stdout
                    + failure_detail
                    + failure_diagnostic
                ),
            )
            detail = json.loads(detail_path.read_text())
            result = json.loads((cycle / "source" / "JOB4_RESULT.json").read_text())
            self.assertEqual(detail["status"], "completed")
            self.assertEqual(detail["execution_mode"], "provider_free_scripted_v10")
            self.assertEqual(detail["provider_calls"], 0)
            self.assertEqual(detail["scripted_transport_invocations"], 10)
            self.assertEqual(len(detail["calls"]), 10)
            self.assertEqual(
                detail["terminal_evidence"]["schema_version"],
                ContinuousJob4TerminalEvidenceV5.SCHEMA_VERSION,
            )
            journal = json.loads(
                (
                    expected_branch_root
                    / ".acceptance-turn-001"
                    / "JOURNAL.json"
                ).read_text(encoding="utf-8")
            )
            snapshot_receipt = journal["planner_session_snapshot_receipt"]
            self.assertEqual(
                snapshot_receipt["schema_version"],
                "cera.continuous_session_snapshot_receipt.v3",
            )
            self.assertTrue(path_plan := snapshot_receipt["path_plan"])
            self.assertTrue(
                path_plan["current_custody"]["all_existing_components_no_follow"]
            )
            self.assertLessEqual(
                max(
                    path_plan["current_resolved_path_characters"],
                    path_plan["current_temporary_resolved_path_characters"],
                    path_plan["immutable_resolved_path_characters"],
                    path_plan["immutable_temporary_resolved_path_characters"],
                ),
                CONTINUOUS_ACCEPTED_SNAPSHOT_MAX_RESOLVED_CHARS,
            )
            immutable_path = (
                expected_branch_root
                / snapshot_receipt["immutable_relative_path"]
            )
            self.assertTrue(immutable_path.is_file())
            self.assertEqual(
                len(snapshot_receipt["accepted_turn_id_sha256"]), 64
            )
            self.assertEqual(
                len(snapshot_receipt["encoded_snapshot_file_sha256"]), 64
            )
            self.assertEqual(
                snapshot_receipt["injection_receipt"][
                    "operation_receipt_sha256"
                ],
                snapshot_receipt["injection_operation_receipt_sha256"],
            )
            self.assertEqual(detail["provider_fork"]["status"], "passed")
            self.assertEqual(
                detail["provider_fork"]["initialization_packet_kind"],
                "accepted_checkpoint_fork_initialization",
            )
            self.assertEqual(
                detail["provider_fork"]["child_first_lean_packet_kind"],
                "lean_continuous_continuation",
            )
            self.assertNotEqual(
                detail["provider_fork"]["parent_planner_thread_sha256"],
                detail["provider_fork"]["child_planner_thread_sha256"],
            )
            self.assertTrue(detail["provider_fork"]["parent_key_rejected"])
            self.assertTrue(detail["provider_fork"]["sibling_key_rejected"])
            self.assertEqual(detail["reconstruction"]["status"], "passed")
            self.assertTrue(
                detail["lean_context_verification"][
                    "turn_2_prohibited_components_absent"
                ]
            )
            for turn in detail["turns"]:
                for owner in ("planner", "composer", "validator", "reader"):
                    submitted = turn["actual_submitted_prompts"][owner]
                    self.assertGreater(submitted["byte_count"], 0)
                    self.assertEqual(
                        submitted["estimated_tokens"],
                        (submitted["byte_count"] + 3) // 4,
                    )
                injected = turn["accepted_context_injection"]
                self.assertGreater(injected["injected_context_bytes"], 0)
                self.assertEqual(
                    injected["injected_context_estimated_tokens"],
                    (injected["injected_context_bytes"] + 3) // 4,
                )
            initialization = detail["turns"][0]["actual_submitted_prompts"]
            self.assertEqual(
                set(initialization),
                {"planner", "composer", "validator", "reader"},
            )
            reconstruction = detail["reconstruction"]["initialization_receipt"]
            self.assertGreater(reconstruction["reconstruction_bytes"], 0)
            self.assertEqual(
                reconstruction["reconstruction_estimated_tokens"],
                (reconstruction["reconstruction_bytes"] + 3) // 4,
            )
            self.assertEqual(
                detail["prepared_shadow_ingress"]["status"], "passed"
            )
            self.assertEqual(
                detail["prepared_shadow_ingress"]["authority_kind"],
                "prepared_ingress",
            )
            self.assertEqual(
                detail["thread_archival"], {"planner": True, "validator": True}
            )
            self.assertTrue(detail["source_database_unchanged"])
            self.assertTrue(detail["copy_database_unchanged"])
            self.assertTrue(detail["active_route_unchanged"])
            self.assertEqual(result["effects"]["provider_calls"], 0)
            self.assertNotIn("authorization_sha256", result)
            self.assertNotIn("scripted_transport_invocations", result["effects"])
            self.assertIn(
                "scripted_transport_invocations=10",
                result["verification"][0]["summary"],
            )

    def test_missing_source_database_terminalizes_before_any_provider_work(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cycle = root / "cycle"
            (cycle / "receipts").mkdir(parents=True)
            authorization = "c" * 64
            cycle_id = "cycle:missing-source-terminalization"
            task_id = "task:missing-source-terminalization"
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
            runtime_root = root / "runtime"
            completed = subprocess.run(
                (
                    sys.executable,
                    str(ROOT / "scripts" / "run_continuous_planner_validator_job4.py"),
                    "--confirm-provider-free-scripted-v8",
                    "--expected-scripted-fixture-sha256",
                    SCRIPTED_JOB4_FIXTURE_SHA256,
                    "--cycle-directory",
                    str(cycle),
                    "--source-database",
                    str(root / "missing.sqlite3"),
                    "--runtime-root",
                    str(runtime_root),
                    "--expected-checkpoint-sha",
                    git_head(ROOT),
                    "--expected-cycle-id",
                    cycle_id,
                    "--expected-task-id",
                    task_id,
                    "--expected-authorization-sha256",
                    authorization,
                    "--maximum-provider-calls",
                    "10",
                ),
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(completed.returncode, 1, completed.stderr)
            result = json.loads(
                (cycle / "source" / "JOB4_RESULT.json").read_text(encoding="utf-8")
            )
            detail = json.loads(
                (runtime_root / "JOB4_DETAIL.json").read_text(encoding="utf-8")
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["effects"]["provider_calls"], 0)
            self.assertEqual(detail["failure"]["stage"], "pre_provider")
            self.assertIn(
                "source_database_unverified_or_changed",
                detail["terminal_evidence"]["failure_codes"],
            )
            terminal = decode_continuous_job4_terminal_evidence(
                detail["terminal_evidence"]
            )
            self.assertIsInstance(terminal, ContinuousJob4TerminalEvidenceV5)
            self.assertEqual(
                set(terminal.thread_archival_evidence),
                {"planner", "validator"},
            )
            self.assertFalse(
                any(
                    evidence.verified
                    for evidence in terminal.thread_archival_evidence.values()
                )
            )
            self.assertTrue(
                (
                    cycle
                    / "transaction"
                    / "job4"
                    / "JOB4_PUBLICATION_COMMITTED.json"
                ).is_file()
            )
            self.assertTrue((cycle / "source" / "JOB4_REPORT.md").is_file())

    def test_frozen_terminal_bytes_republish_without_semantic_reentry(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cycle = root / "cycle"
            cycle.mkdir()
            transaction = ContinuousJob4TerminalTransactionV1.begin(
                cycle_directory=cycle,
                cycle_id="cycle:frozen-recovery",
                task_id="task:frozen-recovery",
                authorization_sha256="d" * 64,
                runtime_root=root / "runtime",
            )
            transaction.freeze(
                detail_bytes=b'{"status":"failed"}\n',
                terminal_evidence_bytes=b'{"status":"failed"}',
                report_bytes=b"# Frozen terminal report\n",
                result_bytes=b'{"status":"failed"}\n',
                recovery_terminalization=False,
            )
            restarted = ContinuousJob4TerminalTransactionV1.begin(
                cycle_directory=cycle,
                cycle_id="cycle:frozen-recovery",
                task_id="task:frozen-recovery",
                authorization_sha256="d" * 64,
                runtime_root=root / "runtime",
            )
            self.assertFalse(restarted.is_new)
            self.assertEqual(restarted.state, "frozen")
            committed = restarted.publish_frozen()
            self.assertFalse(committed["semantic_work_repeated"])
            self.assertEqual(
                (cycle / "source" / "JOB4_RESULT.json").read_bytes(),
                b'{"status":"failed"}\n',
            )
            self.assertEqual(restarted.state, "committed")

    def test_started_transaction_restart_terminalizes_instead_of_rerunning(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cycle = root / "cycle"
            (cycle / "receipts").mkdir(parents=True)
            authorization = "e" * 64
            cycle_id = "cycle:started-recovery"
            task_id = "task:started-recovery"
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
            connection.commit()
            connection.close()
            runtime_root = root / "runtime"
            ContinuousJob4TerminalTransactionV1.begin(
                cycle_directory=cycle,
                cycle_id=cycle_id,
                task_id=task_id,
                authorization_sha256=authorization,
                runtime_root=runtime_root,
            )
            command = (
                sys.executable,
                str(ROOT / "scripts" / "run_continuous_planner_validator_job4.py"),
                "--confirm-provider-free-scripted-v8",
                "--expected-scripted-fixture-sha256",
                SCRIPTED_JOB4_FIXTURE_SHA256,
                "--cycle-directory",
                str(cycle),
                "--source-database",
                str(source_database),
                "--runtime-root",
                str(runtime_root),
                "--expected-checkpoint-sha",
                git_head(ROOT),
                "--expected-cycle-id",
                cycle_id,
                "--expected-task-id",
                task_id,
                "--expected-authorization-sha256",
                authorization,
                "--maximum-provider-calls",
                "10",
            )
            recovered = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(recovered.returncode, 1, recovered.stderr)
            detail = json.loads(
                (runtime_root / "JOB4_DETAIL.json").read_text(encoding="utf-8")
            )
            self.assertEqual(detail["failure"]["stage"], "restart_recovery")
            self.assertTrue(
                detail["root_terminal_transaction"]["recovery_terminalization"]
            )
            self.assertEqual(detail["provider_calls"], 0)
            self.assertEqual(detail["scripted_transport_invocations"], 0)
            repeated = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
            self.assertNotEqual(repeated.returncode, 0)
            self.assertIn(
                "already committed", (repeated.stderr + repeated.stdout).lower()
            )

    def test_report_construction_failure_still_commits_failed_terminal_result(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cycle = root / "cycle"
            cycle.mkdir()
            transaction = ContinuousJob4TerminalTransactionV1.begin(
                cycle_directory=cycle,
                cycle_id="cycle:report-construction-failure",
                task_id="task:report-construction-failure",
                authorization_sha256="f" * 64,
                runtime_root=root / "runtime",
            )
            detail = terminalized_detail(
                cycle_id="cycle:report-construction-failure",
                execution_status="completed",
                execution_mode="provider_free_scripted_v8",
                provider_calls=0,
                scripted_transport_invocations=10,
            )
            with patch(
                "scripts.run_continuous_planner_validator_job4.build_report",
                side_effect=RuntimeError("bounded report failure"),
            ):
                canonical = _freeze_terminal_publication(
                    transaction,
                    detail,
                    task_id="task:report-construction-failure",
                    recovery_terminalization=False,
                )
            self.assertEqual(canonical["status"], "failed")
            self.assertEqual(canonical["effects"]["provider_calls"], 0)
            self.assertEqual(
                detail["terminal_publication_failure"]["error_type"],
                "RuntimeError",
            )
            self.assertEqual(transaction.state, "committed")
            self.assertIn(
                "bounded finalization failure",
                (cycle / "source" / "JOB4_REPORT.md").read_text(encoding="utf-8"),
            )

    def test_publication_cut_recovers_exact_frozen_bytes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cycle = root / "cycle"
            cycle.mkdir()
            transaction = ContinuousJob4TerminalTransactionV1.begin(
                cycle_directory=cycle,
                cycle_id="cycle:publication-cut",
                task_id="task:publication-cut",
                authorization_sha256="1" * 64,
                runtime_root=root / "runtime",
            )
            result_bytes = b'{"status":"failed"}\n'
            report_bytes = b"# Publication cut\n"
            transaction.freeze(
                detail_bytes=b'{"status":"failed"}\n',
                terminal_evidence_bytes=b'{"status":"failed"}',
                report_bytes=report_bytes,
                result_bytes=result_bytes,
                recovery_terminalization=False,
            )
            original_write = job4_transaction._atomic_immutable_write

            def fail_result_publication(path: Path, data: bytes) -> None:
                if path == cycle / "source" / "JOB4_RESULT.json":
                    raise OSError("simulated publication cut")
                original_write(path, data)

            with patch.object(
                job4_transaction,
                "_atomic_immutable_write",
                side_effect=fail_result_publication,
            ):
                with self.assertRaisesRegex(OSError, "publication cut"):
                    transaction.publish_frozen()
            self.assertEqual(transaction.state, "frozen")
            restarted = ContinuousJob4TerminalTransactionV1.begin(
                cycle_directory=cycle,
                cycle_id="cycle:publication-cut",
                task_id="task:publication-cut",
                authorization_sha256="1" * 64,
                runtime_root=root / "runtime",
            )
            restarted.publish_frozen()
            self.assertEqual(restarted.state, "committed")
            self.assertEqual(
                (cycle / "source" / "JOB4_RESULT.json").read_bytes(), result_bytes
            )
            self.assertEqual(
                (cycle / "source" / "JOB4_REPORT.md").read_bytes(), report_bytes
            )

    def test_live_and_scripted_terminal_results_match_strict_cycle_schema(self) -> None:
        from tools.pro_review_cycle_core import validate_job4_result_contract

        manifest = {
            "cycle_id": "cycle:canary-result-differential",
            "job4": {"task_id": "task:canary-result-differential"},
        }
        for mode, status, provider_calls, scripted_calls in (
            ("live_one_shot", "completed", 10, 0),
            ("live_one_shot", "failed", 1, 0),
            ("provider_free_scripted_v8", "completed", 0, 10),
            ("provider_free_scripted_v8", "failed", 0, 3),
        ):
            with self.subTest(mode=mode, status=status):
                detail = terminalized_detail(
                    cycle_id=manifest["cycle_id"],
                    execution_status=status,
                    execution_mode=mode,
                    provider_calls=provider_calls,
                    scripted_transport_invocations=scripted_calls,
                )
                value = build_canonical_job4_result(
                    detail,
                    task_id=manifest["job4"]["task_id"],
                    report_sha256="a" * 64,
                )
                self.assertEqual(validate_job4_result_contract(value, manifest), value)
                self.assertNotIn("authorization_sha256", value)
                self.assertNotIn("scripted_transport_invocations", value["effects"])

    def test_strict_cycle_schema_rejects_legacy_canary_result_fields(self) -> None:
        from tools.pro_review_cycle_core import CycleError, validate_job4_result_contract

        manifest = {
            "cycle_id": "cycle:canary-result-legacy",
            "job4": {"task_id": "task:canary-result-legacy"},
        }
        value = build_canonical_job4_result(
            terminalized_detail(
                cycle_id=manifest["cycle_id"],
                execution_status="completed",
                execution_mode="provider_free_scripted_v8",
                provider_calls=0,
                scripted_transport_invocations=10,
            ),
            task_id=manifest["job4"]["task_id"],
            report_sha256="b" * 64,
        )
        with self.assertRaisesRegex(CycleError, "authorization_sha256"):
            validate_job4_result_contract(
                {**value, "authorization_sha256": "c" * 64}, manifest
            )
        invalid_effects = dict(value)
        invalid_effects["effects"] = {
            **value["effects"],
            "scripted_transport_invocations": 10,
        }
        with self.assertRaisesRegex(CycleError, "scripted_transport_invocations"):
            validate_job4_result_contract(invalid_effects, manifest)

    def test_terminal_effect_evidence_preserves_each_nonzero_canonical_effect(self) -> None:
        from tools.pro_review_cycle_core import validate_job4_result_contract

        manifest = {
            "cycle_id": "cycle:terminal-effects",
            "job4": {"task_id": "task:terminal-effects"},
        }
        cases = (
            (
                "provider_calls",
                terminalized_detail(
                    cycle_id=manifest["cycle_id"],
                    execution_status="completed",
                    execution_mode="live_one_shot",
                    provider_calls=4,
                    scripted_transport_invocations=0,
                ),
                4,
            ),
            (
                "story_database_writes",
                terminalized_detail(
                    cycle_id=manifest["cycle_id"],
                    execution_status="completed",
                    execution_mode="live_one_shot",
                    provider_calls=0,
                    scripted_transport_invocations=0,
                    operational_counters=ContinuousJob4OperationalCountersV1(
                        live_story_writes=1,
                        production_database_writes=2,
                        deployment_operations=0,
                        remote_operations=0,
                        merge_operations=0,
                        push_operations=0,
                        service_changes=0,
                        installed_sillytavern_changes=0,
                    ),
                ),
                3,
            ),
            (
                "active_route_changes",
                terminalized_detail(
                    cycle_id=manifest["cycle_id"],
                    execution_status="completed",
                    execution_mode="live_one_shot",
                    provider_calls=0,
                    scripted_transport_invocations=0,
                    postcondition_changes={
                        "active_profile_sha256_after": "4" * 64
                    },
                ),
                1,
            ),
            (
                "deployment_remote_or_push_effects",
                terminalized_detail(
                    cycle_id=manifest["cycle_id"],
                    execution_status="completed",
                    execution_mode="live_one_shot",
                    provider_calls=0,
                    scripted_transport_invocations=0,
                    operational_counters=ContinuousJob4OperationalCountersV1(
                        live_story_writes=0,
                        production_database_writes=0,
                        deployment_operations=1,
                        remote_operations=1,
                        merge_operations=1,
                        push_operations=1,
                        service_changes=1,
                        installed_sillytavern_changes=1,
                    ),
                ),
                6,
            ),
        )
        for field, detail, expected in cases:
            with self.subTest(field=field):
                value = build_canonical_job4_result(
                    detail,
                    task_id=manifest["job4"]["task_id"],
                    report_sha256="5" * 64,
                )
                self.assertEqual(value["effects"][field], expected)
                self.assertEqual(
                    validate_job4_result_contract(value, manifest), value
                )
                if field != "provider_calls":
                    self.assertEqual(value["status"], "failed")

    def test_capability_custody_structurally_denies_every_effect_port(self) -> None:
        custody = ContinuousJob4CapabilityCustody()
        evidence = custody.evidence
        self.assertTrue(evidence.all_zero_effects_structurally_denied)
        self.assertEqual(
            ContinuousJob4CapabilityLedgerV1.from_dict(evidence.to_dict()), evidence
        )
        self.assertEqual(evidence.operational_counters.story_database_writes, 0)
        self.assertEqual(evidence.active_route_mutations, 0)
        for capability in evidence.capabilities:
            with self.subTest(capability=capability), self.assertRaisesRegex(
                PermissionError, "structurally unavailable"
            ):
                custody.record(capability)
        tampered = json.loads(json.dumps(evidence.to_dict()))
        tampered["capabilities"]["live_story_write"]["count"] = 1
        with self.assertRaisesRegex(ValueError, "contradictory"):
            ContinuousJob4CapabilityLedgerV1.from_dict(tampered)

    def test_closed_capability_container_rejects_every_bypass_before_effect(self) -> None:
        container = ContinuousJob4CapabilityContainerV1.restricted(
            entrypoint_id="continuous_planner_validator_job4",
            entrypoint_path=ROOT / "scripts" / "run_continuous_planner_validator_job4.py",
        )
        boundary = container.boundary_evidence
        self.assertTrue(boundary.enforced)
        self.assertEqual(
            ContinuousJob4CapabilityBoundaryEvidenceV1.from_dict(
                boundary.to_dict()
            ),
            boundary,
        )
        self.assertEqual(set(boundary.capability_ports), set(container.evidence.capabilities))
        self.assertEqual(
            len(
                {
                    value["port_id_sha256"]
                    for value in boundary.capability_ports.values()
                }
            ),
            len(boundary.capability_ports),
        )
        effects: list[str] = []
        for capability in boundary.capability_ports:
            with self.subTest(capability=capability), self.assertRaisesRegex(
                PermissionError, "structurally unavailable"
            ):
                container.port(capability).invoke(effects.append, capability)
        self.assertEqual(effects, [])

    def test_unwrapped_surface_invalidates_denial_before_semantic_work(self) -> None:
        container = ContinuousJob4CapabilityContainerV1(
            entrypoint_id="continuous_planner_validator_job4",
            entrypoint_path=ROOT / "scripts" / "run_continuous_planner_validator_job4.py",
            additional_unwrapped_surfaces=(
                "live_story_write:test_unwrapped_adapter",
            ),
        )
        boundary = container.boundary_evidence
        self.assertEqual(boundary.status, "failed")
        self.assertEqual(
            boundary.capability_ports["live_story_write"][
                "unwrapped_surface_count"
            ],
            1,
        )
        with self.assertRaisesRegex(PermissionError, "not enforced"):
            container.require_enforced()

    def test_entrypoint_inventory_rejects_direct_product_mutation_import(self) -> None:
        with TemporaryDirectory() as directory:
            entrypoint = (
                Path(directory)
                / "scripts"
                / "run_continuous_planner_validator_job4.py"
            )
            entrypoint.parent.mkdir(parents=True)
            entrypoint.write_text(
                "from cera.runtime import commit as story_commit\n"
                "from subprocess import Popen as launch\n"
                "launch(['git', 'push'])\n",
                encoding="utf-8",
            )
            container = ContinuousJob4CapabilityContainerV1.restricted(
                entrypoint_id="continuous_planner_validator_job4",
                entrypoint_path=entrypoint,
            )
            self.assertIn(
                "live_story_write:direct_import:cera.runtime.commit:L1",
                container.boundary_evidence.unwrapped_surfaces,
            )
            self.assertIn(
                "remote_operation:unwrapped_call:subprocess.Popen:L3",
                container.boundary_evidence.unwrapped_surfaces,
            )
            with self.assertRaisesRegex(PermissionError, "not enforced"):
                container.require_enforced()

    def test_observed_bypass_is_counted_and_forces_terminal_v4_failure(self) -> None:
        container = ContinuousJob4CapabilityContainerV1(
            entrypoint_id="continuous_planner_validator_job4",
            entrypoint_path=ROOT / "scripts" / "run_continuous_planner_validator_job4.py",
            counted_capabilities=("production_database_write",),
            additional_unwrapped_surfaces=(
                "production_database_write:test_direct_bypass",
            ),
        )
        state: list[str] = []
        container.observe_unwrapped_effect(
            "production_database_write",
            probe=lambda: tuple(state),
            operation=lambda: state.append("effect-created"),
        )
        ledger = container.evidence
        boundary = container.boundary_evidence
        self.assertEqual(
            ledger.capabilities["production_database_write"]["count"], 1
        )
        self.assertFalse(boundary.enforced)
        archival = {
            role.value: ContinuousThreadArchiveEvidenceV1(
                role=role,
                provider_thread_id_sha256=text_sha256(
                    f"capability-boundary-{role.value}"
                ),
                archive_reason_sha256=text_sha256("capability-boundary-complete"),
                archive_request_completed=True,
                resume_succeeded_after_archive=False,
                backend_selectable_after_archive=False,
                coordinator_selectable_as_accepted_ancestry=False,
            )
            for role in (
                ContinuousSessionRole.PLANNER,
                ContinuousSessionRole.VALIDATOR,
            )
        }
        postconditions = ContinuousJob4PostconditionsV1(
            execution_mode="provider_free_scripted_v8",
            source_database_sha256_before="1" * 64,
            source_database_sha256_after="1" * 64,
            disposable_database_sha256_before="2" * 64,
            disposable_database_sha256_after="2" * 64,
            database_integrity_check="ok",
            database_foreign_key_findings=0,
            active_profile_sha256_before="3" * 64,
            active_profile_sha256_after="3" * 64,
            active_profile_inspection_status="verified",
            thread_archival={"planner": True, "validator": True},
            accepted_session_synchronized=True,
            accepted_final_sequences_injected=True,
            call_ledger_dispatches=0,
            scripted_transport_invocations=0,
        )
        terminal = ContinuousJob4TerminalEvidenceV4.build(
            execution_status="completed",
            provider_calls=0,
            capability_ledger=ledger,
            capability_boundary_evidence=boundary,
            postconditions=postconditions,
            thread_archival_evidence=archival,
        )
        self.assertEqual(terminal.status, "failed")
        self.assertEqual(
            terminal.effect_evidence.canonical_effects["story_database_writes"],
            1,
        )
        self.assertIn("capability_boundary_not_enforced", terminal.failure_codes)
        self.assertIn("story_database_effect_detected", terminal.failure_codes)
        self.assertEqual(
            decode_continuous_job4_terminal_evidence(terminal.to_dict()), terminal
        )

    def test_counted_capability_ports_preserve_nonzero_terminal_effects(self) -> None:
        base = terminalized_detail(
            cycle_id="cycle:counted-capability-effects",
            execution_status="completed",
            execution_mode="live_one_shot",
            provider_calls=0,
            scripted_transport_invocations=0,
        )
        postconditions = ContinuousJob4PostconditionsV1.from_dict(
            base["terminal_evidence"]["postconditions"]
        )
        custody = ContinuousJob4CapabilityCustody(
            counted_capabilities=(
                "live_story_write",
                "active_route_mutation",
                "deployment",
            )
        )
        custody.record("live_story_write")
        custody.record("live_story_write")
        custody.record("active_route_mutation")
        custody.record("deployment")
        terminal = ContinuousJob4TerminalEvidenceV2.build(
            execution_status="completed",
            provider_calls=0,
            capability_ledger=custody.evidence,
            postconditions=postconditions,
        )
        rebuilt = decode_continuous_job4_terminal_evidence(terminal.to_dict())
        self.assertEqual(rebuilt, terminal)
        self.assertEqual(terminal.status, "failed")
        self.assertEqual(
            terminal.effect_evidence.canonical_effects,
            {
                "provider_calls": 0,
                "story_database_writes": 2,
                "active_route_changes": 1,
                "deployment_remote_or_push_effects": 1,
            },
        )

    def test_terminal_effect_evidence_rejects_missing_boolean_negative_and_contradictory_values(self) -> None:
        detail = terminalized_detail(
            cycle_id="cycle:terminal-rejection",
            execution_status="completed",
            execution_mode="provider_free_scripted_v8",
            provider_calls=0,
            scripted_transport_invocations=10,
        )
        mutations: list[tuple[str, dict[str, object]]] = []
        missing = json.loads(json.dumps(detail))
        del missing["terminal_evidence"]["effect_evidence"]["provider_calls"]
        mutations.append(("missing", missing))
        boolean = json.loads(json.dumps(detail))
        boolean["terminal_evidence"]["effect_evidence"]["provider_calls"] = True
        mutations.append(("boolean", boolean))
        negative = json.loads(json.dumps(detail))
        negative["terminal_evidence"]["effect_evidence"]["provider_calls"] = -1
        mutations.append(("negative", negative))
        malformed = json.loads(json.dumps(detail))
        malformed["terminal_evidence"]["effect_evidence"][
            "operational_counters"
        ]["remote_operations"] = "zero"
        mutations.append(("malformed", malformed))
        contradictory = json.loads(json.dumps(detail))
        contradictory["provider_calls"] = 1
        mutations.append(("contradictory", contradictory))
        for name, value in mutations:
            with self.subTest(name=name), self.assertRaises(ValueError):
                build_canonical_job4_result(
                    value,
                    task_id="task:terminal-rejection",
                    report_sha256="6" * 64,
                )

    def test_terminal_postconditions_force_failure_for_every_mandatory_class(self) -> None:
        cases = {
            "active_mismatch": {"active_profile_sha256_after": "4" * 64},
            "active_inspection": {
                "active_profile_sha256_after": None,
                "active_profile_inspection_status": "failed",
            },
            "source_drift": {"source_database_sha256_after": "4" * 64},
            "copy_drift": {"disposable_database_sha256_after": "4" * 64},
            "integrity": {"database_integrity_check": "corrupt"},
            "foreign_keys": {"database_foreign_key_findings": 1},
            "archival": {
                "thread_archival": {"planner": True, "validator": False}
            },
            "synchronization": {"accepted_session_synchronized": False},
            "injection": {"accepted_final_sequences_injected": False},
            "ledger": {"call_ledger_dispatches": 9},
        }
        for name, changes in cases.items():
            with self.subTest(name=name):
                detail = terminalized_detail(
                    cycle_id="cycle:mandatory-postconditions",
                    execution_status="completed",
                    execution_mode="provider_free_scripted_v8",
                    provider_calls=0,
                    scripted_transport_invocations=10,
                    postcondition_changes=changes,
                )
                value = build_canonical_job4_result(
                    detail,
                    task_id="task:mandatory-postconditions",
                    report_sha256="7" * 64,
                )
                self.assertEqual(value["status"], "failed")
                self.assertEqual(value["verification"][1]["status"], "failed")
                if name in {"active_mismatch", "active_inspection"}:
                    self.assertGreater(value["effects"]["active_route_changes"], 0)

    def test_completed_terminal_result_cannot_contain_a_failed_mandatory_verification(self) -> None:
        detail = terminalized_detail(
            cycle_id="cycle:completed-terminal",
            execution_status="completed",
            execution_mode="provider_free_scripted_v8",
            provider_calls=0,
            scripted_transport_invocations=10,
        )
        value = build_canonical_job4_result(
            detail,
            task_id="task:completed-terminal",
            report_sha256="8" * 64,
        )
        self.assertEqual(value["status"], "completed")
        self.assertTrue(
            all(item["status"] == "passed" for item in value["verification"])
        )
        detail["terminal_evidence"]["postconditions"][
            "accepted_session_synchronized"
        ] = False
        with self.assertRaises(ValueError):
            build_canonical_job4_result(
                detail,
                task_id="task:completed-terminal",
                report_sha256="8" * 64,
            )

    def test_declared_unittest_ids_must_resolve_before_publication(self) -> None:
        valid = (
            "tests.test_continuous_planner_validator.ContinuousSessionTests."
                "test_restart_rejects_pre_v7_policy_compatibility",
        )
        self.assertEqual(validate_declared_unittest_ids(valid), {valid[0]: 1})
        with self.assertRaisesRegex(ValueError, "did not resolve"):
            validate_declared_unittest_ids(
                (
                    "tests.test_continuous_planner_validator."
                    "ContinuousSessionLifecycleTests."
                    "test_restart_rejects_pre_v7_policy_compatibility",
                )
            )

    def test_parameterized_canary_identity_rejects_historical_reuse(self) -> None:
        cycle_id = "cycle:new"
        task_id = "task:new"
        authorization = "a" * 64
        manifest = {
            "cycle_id": cycle_id,
            "job4": {
                "task_id": task_id,
                "authorization_record_sha256": authorization,
            },
        }
        validate_job4_identity(
            manifest,
            expected_cycle_id=cycle_id,
            expected_task_id=task_id,
            expected_authorization_sha256=authorization,
            maximum_provider_calls=10,
        )
        historical_cycle = "2026-08-01-continuous-planner-validator-v1-cycle-001"
        historical_task = "continuous-planner-validator-three-turn-scene-change-canary-v1"
        with self.assertRaisesRegex(ValueError, "historical failed"):
            validate_job4_identity(
                {
                    "cycle_id": historical_cycle,
                    "job4": {
                        "task_id": historical_task,
                        "authorization_record_sha256": authorization,
                    },
                },
                expected_cycle_id=historical_cycle,
                expected_task_id=historical_task,
                expected_authorization_sha256=authorization,
                maximum_provider_calls=10,
            )
        with self.assertRaisesRegex(ValueError, "ceiling"):
            validate_job4_identity(
                manifest,
                expected_cycle_id=cycle_id,
                expected_task_id=task_id,
                expected_authorization_sha256=authorization,
                maximum_provider_calls=11,
            )

    def test_stable_prefix_is_not_resent_in_the_stored_turn(self) -> None:
        inner = _Transport()
        transport = StablePrefixTransport(inner, PLANNER_STABLE_INSTRUCTIONS)
        result = transport.invoke(
            PLANNER_STABLE_INSTRUCTIONS + "\n\n[CURRENT AUTHORITATIVE TURN PACKET]\n{}"
        )
        self.assertEqual(result.prompt, "[CURRENT AUTHORITATIVE TURN PACKET]\n{}")
        self.assertNotIn(PLANNER_STABLE_INSTRUCTIONS, inner.prompts[0])

    def test_stable_prefix_recursively_exposes_stored_thread_identity(self) -> None:
        inner = SimpleNamespace(
            route=SimpleNamespace(),
            runner=SimpleNamespace(provider_thread_id="stored-planner-thread"),
        )
        wrapped = StablePrefixTransport(inner, PLANNER_STABLE_INSTRUCTIONS)
        self.assertEqual(
            _transport_stored_thread_sha256(wrapped),
            text_sha256("stored-planner-thread"),
        )

    def test_actual_codex_and_deepseek_transports_mark_submission_boundary(self) -> None:
        class Runner:
            def __init__(self, output: str) -> None:
                self.output = output

            def run(self, *, route, **_kwargs):
                return CodexWorkerResult(
                    output_text=self.output,
                    provider_request_id="fake-request",
                    returned_model=route.model_name,
                    duration_ms=1,
                    input_tokens=1,
                    cached_input_tokens=0,
                    output_tokens=1,
                    reasoning_output_tokens=0,
                    transport_version=route.transport_version,
                    pre_registered_turn_count=1,
                )

        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        }
        for output in ("not-json", "[]"):
            with self.subTest(output=output), TemporaryDirectory() as directory:
                workspace = Path(directory).resolve()
                markers: list[str] = []
                transport = CodexSDKTransport(
                    continuous_planner_route(),
                    workspace=workspace,
                    runner=Runner(output),
                )
                with self.assertRaises(ProviderTransportError):
                    transport.invoke(
                        "test prompt",
                        output_schema=schema,
                        on_transport_invoke=lambda: markers.append("invoked"),
                    )
                self.assertEqual(markers, ["invoked"])

        with TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            markers = []
            transport = CodexSDKTransport(
                continuous_planner_route(), workspace=workspace, runner=Runner('{"ok":true}')
            )
            binding = CodexMcpRuntimeBinding(
                server_name="cera_continuous_world_v1",
                url="http://127.0.0.1:43123/mcp",
                bearer_token_environment_variable="CERA_REQUEST_EVIDENCE_TOKEN",
                bearer_token="fake-secret",
                enabled_tools=("cera_world_read",),
                binding_sha256=canonical_sha256({"fake": "binding"}),
                minimum_tool_calls=1,
                maximum_tool_calls=1,
            )
            with self.assertRaises(ProviderTransportError):
                transport.invoke(
                    "test prompt",
                    output_schema=schema,
                    mcp_binding=binding,
                    on_transport_invoke=lambda: markers.append("invoked"),
                )
            self.assertEqual(markers, ["invoked"])

        markers = []

        def timed_out(*_args, **_kwargs):
            raise TimeoutError("fake timeout")

        deepseek = DeepSeekChatTransport(
            continuous_deepseek_route(),
            opener=timed_out,
            environment={"DEEPSEEK_API_KEY": "fake-key"},
        )
        with self.assertRaises(ProviderTransportError):
            deepseek.invoke(
                (DeepSeekMessage(role="user", content="test"),),
                output_mode=ProviderOutputMode.JSON_OBJECT,
                on_transport_invoke=lambda: markers.append("invoked"),
            )
        self.assertEqual(markers, ["invoked"])

    def test_actual_subprocess_sidecar_marks_post_submit_failure(self) -> None:
        markers: list[str] = []
        with TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            with self.assertRaises(ProviderTransportError) as raised:
                _SubprocessCodexRunner(
                    worker_module="tests.fixtures.codex_progress_worker"
                ).run(
                    route=continuous_planner_route(),
                    prompt="provider-free fixture",
                    output_schema={
                        "type": "object",
                        "properties": {"ok": {"type": "boolean"}},
                        "required": ["ok"],
                        "additionalProperties": False,
                    },
                    workspace=workspace,
                    mcp_binding=None,
                    on_worker_started=lambda: markers.append("worker_started"),
                    on_worker_preflight=lambda: markers.append("preflight"),
                    on_provider_submit=lambda: markers.append("thread_run"),
                )
        self.assertEqual(raised.exception.external_provider_calls_observed, 1)
        self.assertIn("worker_stage:thread_run", raised.exception.safe_diagnostics)
        self.assertEqual(markers.count("worker_started"), 1)
        self.assertGreaterEqual(markers.count("preflight"), 1)
        self.assertGreaterEqual(markers.count("thread_run"), 1)

    def test_subprocess_sidecar_stage_matrix_and_stranded_accounting(self) -> None:
        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        }

        def invoke(control: dict[str, str], *, timeout_seconds: int = 5):
            directory = TemporaryDirectory()
            self.addCleanup(directory.cleanup)
            workspace = Path(directory.name).resolve()
            route = replace(
                continuous_planner_route(), timeout_seconds=timeout_seconds
            )
            return _SubprocessCodexRunner(
                worker_module="tests.fixtures.codex_stage_matrix_worker",
                provider_thread_id="fixture-thread",
            ).run(
                route=route,
                prompt=json.dumps(control, sort_keys=True),
                output_schema=schema,
                workspace=workspace,
                mcp_binding=None,
            )

        for stage, expected_calls in (
            ("worker_launch", 0),
            ("sdk_import", 0),
            ("account_check", 0),
            ("thread_resume", 0),
            ("thread_run", 1),
            ("thread_read", 1),
        ):
            with self.subTest(stage=stage), self.assertRaises(
                ProviderTransportError
            ) as raised:
                invoke({"mode": "fail", "stage": stage})
            self.assertEqual(
                raised.exception.external_provider_calls_observed,
                expected_calls,
            )
            self.assertIn(
                f"worker_stage:{stage}", raised.exception.safe_diagnostics
            )

        success = invoke({"mode": "success"})
        self.assertEqual(success.output_text, '{"ok":true}')
        with self.assertRaises(ProviderTransportError) as malformed:
            invoke({"mode": "malformed"})
        self.assertEqual(malformed.exception.external_provider_calls_observed, 1)
        self.assertIn(
            "transport:invalid_worker_envelope",
            malformed.exception.safe_diagnostics,
        )
        for stage, expected_calls in (("sdk_import", 0), ("thread_run", 1)):
            with self.subTest(timeout_stage=stage), self.assertRaises(
                ProviderTransportError
            ) as timeout:
                invoke(
                    {"mode": "timeout", "stage": stage}, timeout_seconds=1
                )
            self.assertEqual(
                timeout.exception.external_provider_calls_observed,
                expected_calls,
            )
            self.assertIn("transport:timeout", timeout.exception.safe_diagnostics)

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            ledger = ContinuousProviderCallLedger(root / "calls.jsonl")
            route = replace(continuous_planner_route(), timeout_seconds=1)
            runner = _SubprocessCodexRunner(
                worker_module="tests.fixtures.codex_stage_matrix_worker",
                provider_thread_id="fixture-thread",
            )
            with self.assertRaises(ProviderTransportError):
                ledger.execute(
                    owner="planner",
                    operation="stranded_thread_run",
                    route=route.route_id,
                    model=route.model_name,
                    effort=route.reasoning_effort,
                    dispatch_with_stage_markers=lambda markers: runner.run(
                        route=route,
                        prompt=json.dumps(
                            {"mode": "timeout", "stage": "thread_run"},
                            sort_keys=True,
                        ),
                        output_schema=schema,
                        workspace=workspace,
                        mcp_binding=None,
                        on_worker_started=markers.mark_worker_started,
                        on_worker_preflight=markers.mark_worker_preflight,
                        on_provider_submit=markers.mark_transport_invoked,
                    ),
                    finalize=lambda value: value,
                    stored_thread_sha256=text_sha256("fixture-thread"),
                )
            self.assertEqual(ledger.dispatched_call_count, 1)
            self.assertEqual(ledger.unresolved_prepared_call_ids, ())
            call_id = ledger.events[0]["call_id"]
            self.assertEqual(
                ledger.terminal_state(call_id), ProviderCallState.PROVIDER_FAILED
            )

    def test_exact_job_harness_summary_path_reaches_first_provider_boundary(self) -> None:
        class FirstProviderBoundary(RuntimeError):
            pass

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            world = ContinuousWorldStore(root / "worlds")
            seed_world(world, ROOT)
            session_port = InMemoryContinuousStoredSessionPort()
            planner_session = ContinuousSessionCoordinator(
                compatibility(world, ContinuousSessionRole.PLANNER), session_port
            )
            validator_session = ContinuousSessionCoordinator(
                compatibility(world, ContinuousSessionRole.VALIDATOR), session_port
            )
            planner_session.install_base_instructions(
                PLANNER_STABLE_INSTRUCTIONS
            )
            planner_handle = planner_session.ensure_session().provider_thread_id
            validator_handle = validator_session.ensure_session().provider_thread_id
            lifecycle = root / "lifecycle"
            lifecycle.mkdir()
            harness = JobHarness(
                source_root=ROOT,
                cycle=root / "cycle",
                world=world,
                planner_session=planner_session,
                validator_session=validator_session,
                planner_handle=planner_handle,
                validator_handle=validator_handle,
                lifecycle_root=lifecycle,
                call_ledger=ContinuousProviderCallLedger(root / "calls.jsonl"),
            )
            harness.provider_call = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                FirstProviderBoundary("provider boundary reached")
            )
            summary = source_character_summary(
                ROOT, "sakura", world=world, world_file_revision=1
            )
            with self.assertRaisesRegex(FirstProviderBoundary, "boundary reached"):
                harness.run_turn(
                    turn_number=1,
                    scene_id="scene-arrival",
                    summaries=(summary,),
                )
            self.assertFalse(
                (world.branch_root(WORLD_ID, BRANCH_ID) / "ACTIVE" / "ACTIVE").exists()
            )

    def test_exact_job_harness_completes_all_ten_provider_free_stages(self) -> None:
        class ProviderFreeHarness(JobHarness):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.planner_prompts: list[str] = []

            @staticmethod
            def _result(value):
                return SimpleNamespace(
                    value=value,
                    provider_receipt=None,
                    operation_telemetry=None,
                    tool_call_count=0,
                    failed_tool_call_count=0,
                    world_tool_debug=None,
                )

            def provider_call(self, label, owner, operation):
                result = operation()
                self.call_records.append(
                    {
                        "index": len(self.call_records) + 1,
                        "label": label,
                        "owner": owner,
                        "status": "provider_free_passed",
                    }
                )
                return result

            def codex_planner(self, prompt, turn_id):
                self.planner_prompts.append(prompt)
                packet = json.loads(
                    prompt.rsplit("[CURRENT AUTHORITATIVE TURN PACKET]\n", 1)[1]
                )
                npc = (
                    "character:mia_hanezawa"
                    if turn_id == "turn-003"
                    else "character:sakura_hanezawa"
                )
                bindings = tuple(
                    dict.fromkeys(
                        str(value["binding_key"])
                        for value in packet["request_local_evidence_bindings"]
                        if value.get("visibility") != "character_private"
                        or value.get("knowledge_owner_id") == npc
                    )
                )
                value = rich_sequence()
                value = replace(
                    value,
                    sequence_id=f"sequence:{turn_id.replace('-', '_')}",
                    world_id=WORLD_ID,
                    branch_id=BRANCH_ID,
                    scene_id=("scene-002" if turn_id == "turn-003" else "scene-001"),
                    selected_character_ids=(
                        ("character:mia_hanezawa",)
                        if turn_id == "turn-003"
                        else value.selected_character_ids
                    ),
                    beats=tuple(
                        replace(
                            beat,
                            roles=(
                                CharacterRoleLedgerV1(
                                    action_owner_ids=("character:mia_hanezawa",),
                                    addressed_ids=("character:ted",),
                                )
                                if turn_id == "turn-003"
                                else beat.roles
                            ),
                            source_evidence_bindings=bindings,
                        )
                        for beat in value.beats
                    ),
                )
                return self._result(value)

            def deepseek(self, _prompt):
                if self._active_turn_id == "turn-003":
                    draft = composer_draft("Mia answers cautiously.")
                    draft = SimpleNamespace(
                        story_text=draft.story_text,
                        protected_user_realizations=(),
                        story_segments=(
                            replace(
                                draft.story_segments[0],
                                roles=CharacterRoleLedgerV1(
                                    action_owner_ids=("character:mia_hanezawa",),
                                    addressed_ids=("character:ted",),
                                ),
                            ),
                        ),
                    )
                    return self._result(draft)
                return self._result(composer_draft("Sakura requests bounded proof."))

            def codex_validator(self, _prompt, turn_id, *, accepted_pairs=()):
                if accepted_pairs:
                    result = scene_summary_package(
                        accepted_pairs[0], new_prompt="unused"
                    )
                    result = replace(
                        result,
                        world_id=WORLD_ID,
                        branch_id=BRANCH_ID,
                        optional_scene_summary=replace(
                            result.optional_scene_summary,
                            completed_scene_id="scene-001",
                            accepted_turn_ids=tuple(
                                value.accepted_turn_id for value in accepted_pairs
                            ),
                            last_five_exact_pairs=tuple(accepted_pairs),
                        ),
                    )
                    return self._result(
                        ContinuousSemanticValidatorResultV1.from_finalization_package(
                            finalization_package=result,
                            story_segments=(),
                        )
                    )
                revision = int(turn_id.rsplit("-", 1)[1])
                story_text = (
                    "Mia answers cautiously."
                    if turn_id == "turn-003"
                    else "Sakura requests bounded proof."
                )
                result = package(
                    turn_id=turn_id,
                    revision=revision,
                    story_text=story_text,
                )
                if turn_id == "turn-003":
                    item = result.complete_final_sequence.items[0]
                    item = replace(
                        item,
                        realized_event="Mia answers cautiously in the later scene.",
                        private_state_owner_ids=("character:mia_hanezawa",),
                        roles=CharacterRoleLedgerV1(
                            action_owner_ids=("character:mia_hanezawa",),
                            addressed_ids=("character:ted",),
                        ),
                        field_scopes=tuple(
                            replace(
                                scope,
                                knowledge_owner_id=(
                                    "character:mia_hanezawa"
                                    if scope.knowledge_owner_id is not None
                                    else None
                                ),
                                roles=CharacterRoleLedgerV1(
                                    action_owner_ids=("character:mia_hanezawa",),
                                    addressed_ids=("character:ted",),
                                ),
                            )
                            for scope in item.field_scopes
                        ),
                    )
                    result = replace(
                        result,
                        complete_final_sequence=replace(
                            result.complete_final_sequence, items=(item,)
                        ),
                        event_record=replace(
                            result.event_record,
                            participant_ids=(
                                "character:mia_hanezawa",
                                "character:ted",
                            ),
                            item_role_ledgers=(
                                replace(
                                    result.event_record.item_role_ledgers[0],
                                    roles=item.roles,
                                ),
                            ),
                            summary="Mia answers cautiously in the later scene.",
                        ),
                        protected_semantic_adjudications=(
                            replace(
                                result.protected_semantic_adjudications[0],
                                npc_assertion_owner_ids=(
                                    "character:mia_hanezawa",
                                ),
                            ),
                        ),
                    )
                stripped_items = tuple(
                    replace(
                        item,
                        field_scopes=tuple(
                            replace(scope, persistence_directives=())
                            for scope in item.field_scopes
                        ),
                    )
                    for item in result.complete_final_sequence.items
                )
                result = replace(
                    result,
                    world_id=WORLD_ID,
                    branch_id=BRANCH_ID,
                    complete_final_sequence=replace(
                        result.complete_final_sequence,
                        items=stripped_items,
                    ),
                    world_edit_operations=(),
                    created_field_log=(),
                    event_record=replace(
                        result.event_record,
                        scene_id=(
                            "scene-002" if turn_id == "turn-003" else "scene-001"
                        ),
                    ),
                )
                return self._result(
                    ContinuousSemanticValidatorResultV1.from_finalization_package(
                        finalization_package=result,
                        story_segments=(
                            StoryRealizationSegmentV1(
                                schema_version=(
                                    StoryRealizationSegmentV1.SCHEMA_VERSION
                                ),
                                segment_key="segment_entire_story",
                                kind=StoryRealizationKind.ACTION,
                                output_start=0,
                                output_end=len(story_text),
                                exact_text=story_text,
                                roles=result.complete_final_sequence.items[0].roles,
                                protected_user_source_claim_keys=(),
                            ),
                        ),
                    )
                )

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            world = ContinuousWorldStore(root / "worlds")
            seed_world(world, ROOT)
            session_port = InMemoryContinuousStoredSessionPort()
            planner_session = ContinuousSessionCoordinator(
                compatibility(world, ContinuousSessionRole.PLANNER), session_port
            )
            validator_session = ContinuousSessionCoordinator(
                compatibility(world, ContinuousSessionRole.VALIDATOR), session_port
            )
            planner_session.install_base_instructions(
                PLANNER_STABLE_INSTRUCTIONS
            )
            lifecycle = root / "lifecycle"
            lifecycle.mkdir()
            harness = ProviderFreeHarness(
                source_root=ROOT,
                cycle=root / "cycle",
                world=world,
                planner_session=planner_session,
                validator_session=validator_session,
                planner_handle=planner_session.ensure_session().provider_thread_id,
                validator_handle=validator_session.ensure_session().provider_thread_id,
                lifecycle_root=lifecycle,
                call_ledger=ContinuousProviderCallLedger(root / "calls.jsonl"),
            )
            sakura = source_character_summary(
                ROOT, "sakura", world=world, world_file_revision=1
            )
            harness.run_turn(
                turn_number=1, scene_id="scene-001", summaries=(sakura,)
            )
            harness.run_turn(turn_number=2, scene_id="scene-001", summaries=())
            harness.summarize_scene()
            mia = source_character_summary(
                ROOT, "mia", world=world, world_file_revision=1
            )
            harness.run_turn(
                turn_number=3,
                scene_id="scene-002",
                summaries=(mia,),
                scene_change_context={"validated": True},
            )
            self.assertEqual(
                tuple(value["label"] for value in harness.call_records),
                (
                    "turn-1-planner",
                    "turn-1-deepseek",
                    "turn-1-validator",
                    "turn-2-planner",
                    "turn-2-deepseek",
                    "turn-2-validator",
                    "scene-1-validator-summary",
                    "turn-3-planner",
                    "turn-3-deepseek",
                    "turn-3-validator",
                ),
            )
            self.assertEqual(harness.provider_calls, 0)
            self.assertIn('"character_summary_bindings":[]', harness.planner_prompts[1])

    def test_complete_job_harness_uses_actual_ports_with_scripted_transports(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            world = ContinuousWorldStore(root / "worlds")
            seed_world(world, ROOT)
            session_port = InMemoryContinuousStoredSessionPort()
            planner_session = ContinuousSessionCoordinator(
                compatibility(world, ContinuousSessionRole.PLANNER), session_port
            )
            validator_session = ContinuousSessionCoordinator(
                compatibility(world, ContinuousSessionRole.VALIDATOR), session_port
            )
            planner_session.install_base_instructions(
                PLANNER_STABLE_INSTRUCTIONS
            )
            holder: dict[str, JobHarness] = {}

            def planner_value(prompt: str):
                harness = holder["harness"]
                turn_id = harness._active_turn_id
                packet = json.loads(
                    prompt.rsplit(
                        "[CURRENT AUTHORITATIVE TURN PACKET]\n", 1
                    )[1]
                )
                npc = (
                    "character:mia_hanezawa"
                    if turn_id == "turn-003"
                    else "character:sakura_hanezawa"
                )
                bindings = tuple(
                    dict.fromkeys(
                        str(value["binding_key"])
                        for value in packet[
                            "request_local_evidence_bindings"
                        ]
                        if value.get("visibility")
                        != "character_private"
                        or value.get("knowledge_owner_id") == npc
                    )
                )
                value = rich_sequence()
                return replace(
                    value,
                    sequence_id=f"sequence:{turn_id.replace('-', '_')}",
                    world_id=WORLD_ID,
                    branch_id=BRANCH_ID,
                    scene_id=("scene-002" if turn_id == "turn-003" else "scene-001"),
                    selected_character_ids=(
                        ("character:mia_hanezawa",)
                        if turn_id == "turn-003"
                        else value.selected_character_ids
                    ),
                    beats=tuple(
                        replace(
                            beat,
                            roles=(
                                CharacterRoleLedgerV1(
                                    action_owner_ids=("character:mia_hanezawa",),
                                    addressed_ids=("character:ted",),
                                )
                                if turn_id == "turn-003"
                                else beat.roles
                            ),
                            source_evidence_bindings=bindings,
                        )
                        for beat in value.beats
                    ),
                )

            def composer_value(_prompt: str):
                turn_id = holder["harness"]._active_turn_id
                story = (
                    "Mia answers cautiously in the later scene."
                    if turn_id == "turn-003"
                    else "Sakura requests bounded proof."
                )
                return ContinuousSceneWriterDraftV1(
                    schema_version=ContinuousSceneWriterDraftV1.SCHEMA_VERSION,
                    story_text=story,
                )

            def validator_value(_prompt: str):
                harness = holder["harness"]
                if harness._active_validator_label == "scene-1-validator-summary":
                    summary = scene_summary_package(
                        harness.accepted_pairs[0], new_prompt="unused"
                    ).optional_scene_summary
                    summary = replace(
                        summary,
                        completed_scene_id="scene-001",
                        accepted_turn_ids=tuple(
                            value.accepted_turn_id for value in harness.accepted_pairs
                        ),
                        last_five_exact_pairs=tuple(harness.accepted_pairs),
                    )
                    return ContinuousSemanticValidatorDraftV1(
                        schema_version=(
                            ContinuousSemanticValidatorDraftV1.SCHEMA_VERSION
                        ),
                        package_id="package:scene_summary",
                        world_id=WORLD_ID,
                        branch_id=BRANCH_ID,
                        task_mode=scene_summary_package(
                            harness.accepted_pairs[0], new_prompt="unused"
                        ).task_mode,
                        semantic_status=scene_summary_package(
                            harness.accepted_pairs[0], new_prompt="unused"
                        ).semantic_status,
                        reason_codes=(),
                        story_segments=(),
                        complete_final_sequence=None,
                        creator_review=None,
                        protected_semantic_adjudications=(),
                        event_record=None,
                        optional_scene_summary=ProviderSceneSummaryDraftV1(
                            summary_id=summary.summary_id,
                            completed_scene_id=summary.completed_scene_id,
                            accepted_turn_ids=summary.accepted_turn_ids,
                            shortest_complete_summary=summary.shortest_complete_summary,
                            ending_state=summary.ending_state,
                            transition_context=summary.transition_context,
                        ),
                    )

                turn_id = harness._active_turn_id
                story_text = (
                    "Mia answers cautiously in the later scene."
                    if turn_id == "turn-003"
                    else "Sakura requests bounded proof."
                )
                result = package(
                    turn_id=turn_id,
                    revision=int(turn_id.rsplit("-", 1)[1]),
                    story_text=story_text,
                )
                if turn_id == "turn-003":
                    item = replace(
                        result.complete_final_sequence.items[0],
                        realized_event="Mia answers cautiously in the later scene.",
                        private_state_owner_ids=("character:mia_hanezawa",),
                        roles=CharacterRoleLedgerV1(
                            action_owner_ids=("character:mia_hanezawa",),
                            addressed_ids=("character:ted",),
                        ),
                        field_scopes=tuple(
                            replace(
                                scope,
                                knowledge_owner_id=(
                                    "character:mia_hanezawa"
                                    if scope.knowledge_owner_id is not None
                                    else None
                                ),
                                roles=CharacterRoleLedgerV1(
                                    action_owner_ids=("character:mia_hanezawa",),
                                    addressed_ids=("character:ted",),
                                ),
                            )
                            for scope in result.complete_final_sequence.items[0].field_scopes
                        ),
                    )
                    result = replace(
                        result,
                        complete_final_sequence=replace(
                            result.complete_final_sequence, items=(item,)
                        ),
                        event_record=replace(
                            result.event_record,
                            participant_ids=(
                                "character:mia_hanezawa",
                                "character:ted",
                            ),
                            item_role_ledgers=(
                                replace(
                                    result.event_record.item_role_ledgers[0],
                                    roles=item.roles,
                                ),
                            ),
                            summary=item.realized_event,
                        ),
                        protected_semantic_adjudications=(
                            replace(
                                result.protected_semantic_adjudications[0],
                                npc_assertion_owner_ids=(
                                    "character:mia_hanezawa",
                                ),
                            ),
                        ),
                    )
                stripped_items = tuple(
                    replace(
                        item,
                        field_scopes=tuple(
                            replace(scope, persistence_directives=())
                            for scope in item.field_scopes
                        ),
                    )
                    for item in result.complete_final_sequence.items
                )
                result = replace(
                    result,
                    world_id=WORLD_ID,
                    branch_id=BRANCH_ID,
                    complete_final_sequence=replace(
                        result.complete_final_sequence,
                        items=stripped_items,
                    ),
                    world_edit_operations=(),
                    created_field_log=(),
                    event_record=replace(
                        result.event_record,
                        scene_id=(
                            "scene-002" if turn_id == "turn-003" else "scene-001"
                        ),
                    ),
                )
                event = result.event_record
                return ContinuousSemanticValidatorDraftV1(
                    schema_version=(
                        ContinuousSemanticValidatorDraftV1.SCHEMA_VERSION
                    ),
                    package_id=result.package_id,
                    world_id=result.world_id,
                    branch_id=result.branch_id,
                    task_mode=result.task_mode,
                    semantic_status=result.semantic_status,
                    reason_codes=(),
                    story_segments=(
                        StoryRealizationSegmentV1(
                            schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
                            segment_key="segment_entire_story",
                            kind=StoryRealizationKind.ACTION,
                            output_start=0,
                            output_end=len(story_text),
                            exact_text=story_text,
                            roles=result.complete_final_sequence.items[0].roles,
                            protected_user_source_claim_keys=(),
                        ),
                    ),
                    complete_final_sequence=result.complete_final_sequence,
                    creator_review=result.creator_review,
                    protected_semantic_adjudications=(
                        result.protected_semantic_adjudications
                    ),
                    event_record=ProviderEventRecordDraftV1(
                        event_id=event.event_id,
                        accepted_turn_id=event.accepted_turn_id,
                        scene_id=event.scene_id,
                        summary=event.summary,
                        final_sequence_item_keys=event.final_sequence_item_keys,
                        protected_user_source_claim_keys=(
                            event.protected_user_source_claim_keys
                        ),
                    ),
                    optional_scene_summary=None,
                )

            planner_handle = planner_session.ensure_session().provider_thread_id
            validator_handle = validator_session.ensure_session().provider_thread_id
            lifecycle_root = root / "lifecycle"
            lifecycle_root.mkdir()
            harness = JobHarness(
                source_root=ROOT,
                cycle=root / "cycle",
                world=world,
                planner_session=planner_session,
                validator_session=validator_session,
                planner_handle=planner_handle,
                validator_handle=validator_handle,
                lifecycle_root=lifecycle_root,
                call_ledger=ContinuousProviderCallLedger(
                    root / "calls.jsonl", maximum_calls=10
                ),
                planner_transport_factory=lambda _workspace, thread_id: _ScriptedCodexTransport(
                    continuous_planner_route(effort="medium"),
                    thread_id,
                    planner_value,
                ),
                validator_transport_factory=lambda _workspace, thread_id: _ScriptedCodexTransport(
                    continuous_validator_route(
                        model="gpt-5.6-terra", effort="high"
                    ),
                    thread_id,
                    validator_value,
                ),
                composer_transport_factory=lambda: _ScriptedDeepSeekTransport(
                    composer_value
                ),
                scripted_provider_free=True,
            )
            holder["harness"] = harness
            sakura = source_character_summary(
                ROOT, "sakura", world=world, world_file_revision=1
            )
            harness.run_turn(
                turn_number=1, scene_id="scene-001", summaries=(sakura,)
            )
            harness.run_turn(turn_number=2, scene_id="scene-001", summaries=())
            harness.summarize_scene()
            mia = source_character_summary(
                ROOT, "mia", world=world, world_file_revision=1
            )
            harness.run_turn(
                turn_number=3,
                scene_id="scene-002",
                summaries=(mia,),
                scene_change_context={"validated": True},
            )
            self.assertEqual(len(harness.call_records), 10)
            self.assertTrue(
                all(
                    value["status"] == "scripted_provider_free_passed"
                    for value in harness.call_records
                )
            )
            self.assertEqual(harness.provider_calls, 0)
            self.assertEqual(harness.scripted_transport_invocations, 10)
            self.assertEqual(harness.call_ledger.dispatched_call_count, 10)
            self.assertEqual(len(harness.poll_records), 20)
            self.assertEqual(
                planner_session.snapshot().accepted_turn_ids,
                ("turn-001", "turn-002", "turn-003"),
            )
            planner_handle_value = planner_session.ensure_session()
            validator_handle_value = validator_session.ensure_session()
            session_port.archive(
                planner_handle_value, "provider_free_job4_complete"
            )
            session_port.archive(
                validator_handle_value, "provider_free_job4_complete"
            )
            self.assertFalse(session_port.resume(planner_handle_value))
            self.assertFalse(session_port.resume(validator_handle_value))
            self.assertEqual(
                sum(operation == "archive" for operation, _ in session_port.operations),
                2,
            )

    def test_hanezawa_canary_summaries_use_real_genesis_sections(self) -> None:
        with TemporaryDirectory() as directory:
            world = ContinuousWorldStore(Path(directory).resolve() / "worlds")
            seed_world(world, ROOT)
            for character in ("sakura", "mia"):
                summary = source_character_summary(
                    ROOT, character, world=world, world_file_revision=1
                )
                self.assertTrue(summary.incomplete)
                self.assertTrue(summary.more_information_available)
                self.assertIn("Characters/", summary.source_path_or_record_id)
                self.assertGreater(len(summary.summary), 100)

    def test_report_contains_terminal_route_and_effect_accounting(self) -> None:
        detail = terminalized_detail(
            cycle_id="cycle:report-terminal",
            execution_status="failed",
            execution_mode="live_one_shot",
            provider_calls=2,
            scripted_transport_invocations=0,
        )
        detail["failure"] = {"stage": "turn-1-validator"}
        report = build_report(detail)
        self.assertIn("Provider calls observed:** 2 / 10", report)
        self.assertIn("deepseek-v4-flash", report)
        self.assertIn("turn-1-validator", report)


if __name__ == "__main__":
    unittest.main()
