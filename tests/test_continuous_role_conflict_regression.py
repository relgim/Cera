from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
import subprocess
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cera.continuous.call_ledger import (
    ContinuousProviderCallLedger,
    ProviderCallState,
)
from cera.continuous.contracts import RichPlannerSequenceV1
from cera.continuous.ingress import build_default_prepared_classifier_registry
from cera.continuous.prompting import PLANNER_STABLE_INSTRUCTIONS
from cera.continuous.provider import (
    CodexContinuousPlannerPort,
    continuous_planner_route,
    rich_planner_sequence_json_schema,
)
from cera.continuous.record_policy import PERSISTENCE_POLICY_SHA256
from cera.continuous.sessions import (
    ContinuousSessionCompatibilityV1,
    ContinuousSessionCoordinator,
    ContinuousSessionRole,
    InMemoryContinuousStoredSessionPort,
)
from cera.continuous.thread_lineage import ContinuousThreadLineageLedger
from cera.errors import ContractValidationError, StateConflictError
from cera.schema import from_mapping
from cera.serialization import (
    canonical_bytes,
    canonical_sha256,
    text_sha256,
)
from cera.sillytavern.campaign import (
    CONTINUOUS_V3_CALL_SCHEDULE,
    CONTINUOUS_V3_RUN_IDENTITIES,
    CONTINUOUS_V3_V2_RUN_IDENTITIES,
    ContinuousV3TwoRunCampaign,
)
from scripts.run_continuous_planner_validator_job4 import (
    _terminalize_known_thread_sessions,
)
from scripts.run_sillytavern_continuous_v3_campaign import (
    _record_primary_or_additive_failure,
    _validate_child_reconciliation,
    recover_prior_campaign,
    reconcile_child_run,
)
from scripts import run_sillytavern_continuous_v3_campaign as campaign_script


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "continuous_v1_run001_role_conflict_v1.json"
)


class _FixturePlannerTransport:
    def __init__(self, payload: dict[str, object]) -> None:
        self.route = continuous_planner_route(effort="medium")
        self.runner = SimpleNamespace(provider_thread_id="fixture-planner-thread")
        self.payload = payload
        self.invocations = 0

    def invoke(
        self,
        _prompt: str,
        *,
        on_worker_started=None,
        on_worker_preflight=None,
        on_transport_invoke=None,
        **_kwargs,
    ):
        self.invocations += 1
        for callback in (
            on_worker_started,
            on_worker_preflight,
            on_transport_invoke,
        ):
            if callback is not None:
                callback()
        return SimpleNamespace(
            parsed_json=self.payload,
            receipt={"fixture": "privacy-safe-provider-receipt"},
            operation_telemetry={"fixture": "privacy-safe-operation-telemetry"},
            tool_call_count=0,
            failed_tool_call_count=0,
            tool_names=(),
            tool_server_names=(),
        )


class _TransportState:
    def __init__(self) -> None:
        self.alive = True
        self.events: list[str] = []


class _TransportLiveSessionPort(InMemoryContinuousStoredSessionPort):
    def __init__(self, state: _TransportState) -> None:
        super().__init__()
        self.state = state

    def _require_live(self, operation: str) -> None:
        if not self.state.alive:
            raise AssertionError(f"{operation} ran after transport close")
        self.state.events.append(operation)

    def archive(self, handle, reason):
        self._require_live("archive")
        return super().archive(handle, reason)

    def resume(self, handle):
        self._require_live("resume_probe")
        return super().resume(handle)

    def selectable_as_active_or_accepted_ancestry(self, handle):
        self._require_live("backend_selection_probe")
        return super().selectable_as_active_or_accepted_ancestry(handle)


def _compatibility(
    role: ContinuousSessionRole,
    *,
    branch_id: str = "branch:fixture_main",
) -> ContinuousSessionCompatibilityV1:
    return ContinuousSessionCompatibilityV1(
        schema_version=ContinuousSessionCompatibilityV1.SCHEMA_VERSION,
        world_id="world:fixture_role_conflict",
        branch_id=branch_id,
        role=role,
        provider="openai_codex",
        model=(
            "gpt-5.6-sol"
            if role is ContinuousSessionRole.PLANNER
            else "gpt-5.6-terra"
        ),
        reasoning_effort=(
            "medium" if role is ContinuousSessionRole.PLANNER else "high"
        ),
        prompt_version=f"cera.continuous_{role.value}_prompt.v1",
        output_schema_version=f"cera.continuous_{role.value}_output.v1",
        world_directory_identity_sha256=text_sha256(
            f"fixture-role-conflict/{branch_id}"
        ),
        authority_policy_version="cera.owner_architecture.v2",
        privacy_policy_version="cera.privacy.v1",
        protected_user_policy_version="cera.continuous_protected_user_policy.v8",
        session_policy_version="cera.continuous_session_policy.v9_d200",
        ingress_classifier_registry_sha256=(
            build_default_prepared_classifier_registry().registry_sha256
        ),
        persistence_policy_sha256=PERSISTENCE_POLICY_SHA256,
    )


class ContinuousRoleConflictRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_bytes = FIXTURE_PATH.read_bytes()
        self.fixture = json.loads(self.fixture_bytes)

    def test_fixture_is_privacy_safe_and_binds_immutable_run001_hashes(self) -> None:
        self.assertEqual(
            self.fixture["schema_version"],
            "cera.continuous_role_conflict_regression_fixture.v1",
        )
        self.assertEqual(
            self.fixture["source_binding"]["run_result_sha256"],
            "f1617de44f31c9e0408eb3d75c422dc7a9ef33447cca2fe63fa26915e0950c22",
        )
        self.assertEqual(
            self.fixture["source_binding"]["provider_ledger_sha256"],
            "dfd2572e208104614fa9e5f2ed0e42246a7d4d445b201e906bbcac6842fc54fd",
        )
        self.assertEqual(
            self.fixture["source_binding"]["safe_provider_receipt_sha256"],
            "12ef7be094320c70f4aca07db1c925eefe3d32f28ca8f8aa00c0427db54b8f55",
        )
        self.assertEqual(
            self.fixture["source_binding"]["operation_telemetry_sha256"],
            "3fc5a87fbc3d91efae1ec2fef893d64bb1303121a5d21d56281107f1cc468b80",
        )
        self.assertEqual(
            self.fixture["source_binding"]["validation_schema_path"],
            "RichPlannerSequenceV1.beats[].roles",
        )
        for field in (
            "raw_provider_output_retained",
            "raw_private_provider_state_retained",
            "secrets_retained",
        ):
            self.assertFalse(self.fixture["privacy"][field])
        self.assertTrue(self.fixture["privacy"]["synthetic_character_ids_only"])
        rendered = self.fixture_bytes.decode("utf-8")
        self.assertNotIn("hanezawa", rendered.casefold())
        self.assertNotIn("character:ted", rendered.casefold())

    def test_invalid_post_provider_fixture_fails_once_without_repair_or_acceptance(self) -> None:
        invalid = self.fixture["invalid_sequence"]
        before = canonical_bytes(invalid)
        with TemporaryDirectory(prefix="cera-role-conflict-") as temporary:
            ledger = ContinuousProviderCallLedger(
                Path(temporary) / "PROVIDER_CALL_LEDGER.jsonl",
                maximum_calls=1,
            )
            transport = _FixturePlannerTransport(invalid)
            port = CodexContinuousPlannerPort(transport, call_ledger=ledger)
            with self.assertRaisesRegex(
                ContractValidationError,
                "one character cannot hold multiple roles in one scoped assertion",
            ):
                port.plan("Use the exact privacy-safe regression fixture.")
            self.assertEqual(transport.invocations, 1)
            self.assertEqual(ledger.dispatched_call_count, 1)
            self.assertEqual(
                tuple(event["state"] for event in ledger.events),
                tuple(self.fixture["source_binding"]["provider_state_sequence"]),
            )
            self.assertEqual(
                ledger.events[-1]["state"],
                ProviderCallState.POST_VALIDATION_FAILED.value,
            )
            self.assertFalse(
                any(
                    event["state"] == ProviderCallState.ACCEPTED.value
                    for event in ledger.events
                )
            )
        self.assertEqual(canonical_bytes(invalid), before)
        disposition = self.fixture["expected_disposition"]
        self.assertEqual(disposition["automatic_retry_count"], 0)
        self.assertEqual(disposition["fallback_count"], 0)
        self.assertTrue(
            all(
                disposition[field] is False
                for field in (
                    "accepted",
                    "accept_eligible",
                    "coalesced_roles",
                    "deleted_assertions",
                    "guessed_ownership",
                    "changed_role_meaning",
                    "normalized_into_validity",
                )
            )
        )

    def test_all_four_source_conflict_shapes_remain_exact(self) -> None:
        invalid_beats = self.fixture["invalid_sequence"]["beats"]
        for conflict in self.fixture["invalid_conflict_groups"]:
            roles = invalid_beats[conflict["beat_index"]]["roles"]
            character_id = conflict["synthetic_character_id"]
            self.assertTrue(
                all(character_id in roles[field] for field in conflict["role_fields"])
            )
        with self.assertRaisesRegex(ContractValidationError, "multiple roles"):
            from_mapping(RichPlannerSequenceV1, self.fixture["invalid_sequence"])

    def test_corrected_prompt_schema_and_split_sequence_pass_strict_path(self) -> None:
        role_schema = rich_planner_sequence_json_schema()["properties"]["beats"][
            "items"
        ]["properties"]["roles"]
        self.assertFalse(role_schema["additionalProperties"])
        self.assertEqual(
            set(role_schema["required"]),
            {
                "schema_version",
                "action_owner_ids",
                "state_owner_ids",
                "speaker_ids",
                "affected_ids",
                "addressed_ids",
                "observing_ids",
                "referenced_ids",
            },
        )
        self.assertIn(
            "one character ID must appear in exactly one of the seven role arrays",
            PLANNER_STABLE_INSTRUCTIONS,
        )
        self.assertIn(
            "split those assertions into separate causally ordered beats",
            PLANNER_STABLE_INSTRUCTIONS,
        )
        corrected = self.fixture["corrected_sequence"]
        with TemporaryDirectory(prefix="cera-role-corrected-") as temporary:
            ledger = ContinuousProviderCallLedger(
                Path(temporary) / "PROVIDER_CALL_LEDGER.jsonl",
                maximum_calls=1,
            )
            transport = _FixturePlannerTransport(corrected)
            result = CodexContinuousPlannerPort(
                transport, call_ledger=ledger
            ).plan("Use the corrected split-beat regression fixture.")
            self.assertEqual(transport.invocations, 1)
            self.assertEqual(ledger.dispatched_call_count, 1)
            self.assertEqual(
                ledger.events[-1]["state"], ProviderCallState.ACCEPTED.value
            )
        self.assertTrue(result.value.provisional)
        self.assertEqual(len(result.value.beats), 5)
        role_fields = (
            "action_owner_ids",
            "state_owner_ids",
            "speaker_ids",
            "affected_ids",
            "addressed_ids",
            "observing_ids",
            "referenced_ids",
        )
        for beat in result.value.beats:
            all_ids = tuple(
                character_id
                for field in role_fields
                for character_id in getattr(beat.roles, field)
            )
            self.assertEqual(len(all_ids), len(set(all_ids)))
        gamma_roles = tuple(
            field
            for beat in result.value.beats
            for field in ("action_owner_ids", "state_owner_ids", "speaker_ids")
            if "character:fixture_gamma" in getattr(beat.roles, field)
        )
        self.assertEqual(
            gamma_roles,
            ("action_owner_ids", "state_owner_ids", "speaker_ids"),
        )

    def test_every_physical_thread_terminalizes_before_transport_close(self) -> None:
        state = _TransportState()
        port = _TransportLiveSessionPort(state)
        lineage = ContinuousThreadLineageLedger()
        planner = ContinuousSessionCoordinator(
            _compatibility(ContinuousSessionRole.PLANNER), port
        )
        validator = ContinuousSessionCoordinator(
            _compatibility(ContinuousSessionRole.VALIDATOR), port
        )
        planner.attach_thread_lineage(lineage, purpose="primary_planner")
        validator.attach_thread_lineage(lineage, purpose="primary_validator")
        planner_handle = planner.ensure_session()
        planner.ensure_session()
        validator.ensure_session()
        child_compatibility = _compatibility(
            ContinuousSessionRole.PLANNER,
            branch_id="branch:fixture_child",
        )
        child_handle = port.fork_branch(planner_handle, child_compatibility)
        child = ContinuousSessionCoordinator(
            child_compatibility,
            port,
            handle=child_handle,
        )
        child.attach_thread_lineage(
            lineage,
            purpose="accepted_checkpoint_fork_child",
            creation_operation="fork",
            parent_provider_thread_sha256=planner_handle.provider_thread_id_sha256,
        )
        child_evidence = child.archive_and_verify_terminal("fixture_child_complete")
        self.assertTrue(child_evidence.verified)
        archived, evidence, receipt = _terminalize_known_thread_sessions(
            thread_lineage=lineage,
            planner_session=planner,
            validator_session=validator,
            reason="fixture_run_complete",
        )
        self.assertTrue(state.alive)
        self.assertEqual(archived, {"planner": True, "validator": True})
        self.assertTrue(all(value["verified"] for value in evidence.values()))
        self.assertEqual(receipt.status, "verified")
        self.assertEqual(len(receipt.entries), 3)
        self.assertTrue(
            all(entry.terminal_disposition == "verified_archived" for entry in receipt.entries)
        )
        self.assertFalse(port.resume(planner_handle))
        self.assertFalse(port.selectable_as_active_or_accepted_ancestry(planner_handle))
        with self.assertRaisesRegex(StateConflictError, "terminally archived"):
            planner.ensure_session()
        state.events.append("provider_context_close")
        state.alive = False
        self.assertLess(
            max(index for index, event in enumerate(state.events) if event == "archive"),
            state.events.index("provider_context_close"),
        )

    def test_cleanup_failure_is_additive_to_original_failure_and_call_count(self) -> None:
        result = {"status": "running", "provider_calls": 1}
        _record_primary_or_additive_failure(
            result,
            ContractValidationError(
                "one character cannot hold multiple roles in one scoped assertion"
            ),
            stage="planner_post_validation",
        )
        _record_primary_or_additive_failure(
            result,
            RuntimeError("transport cleanup failed"),
            stage="thread_terminalization_cleanup",
        )
        self.assertEqual(result["error_type"], "ContractValidationError")
        self.assertEqual(
            result["error_message"],
            "one character cannot hold multiple roles in one scoped assertion",
        )
        self.assertEqual(result["provider_calls"], 1)
        self.assertEqual(
            result["thread_terminalization_cleanup_error_type"], "RuntimeError"
        )

    def test_failure_path_also_closes_terminal_lineage_while_transport_is_live(self) -> None:
        state = _TransportState()
        port = _TransportLiveSessionPort(state)
        lineage = ContinuousThreadLineageLedger()
        planner = ContinuousSessionCoordinator(
            _compatibility(ContinuousSessionRole.PLANNER), port
        )
        validator = ContinuousSessionCoordinator(
            _compatibility(ContinuousSessionRole.VALIDATOR), port
        )
        planner.attach_thread_lineage(lineage, purpose="primary_planner")
        validator.attach_thread_lineage(lineage, purpose="primary_validator")
        planner.ensure_session()
        validator.ensure_session()
        result = {"status": "running", "provider_calls": 1}
        _record_primary_or_additive_failure(
            result,
            ContractValidationError(
                "one character cannot hold multiple roles in one scoped assertion"
            ),
            stage="planner_post_validation",
        )
        archived, _evidence, receipt = _terminalize_known_thread_sessions(
            thread_lineage=lineage,
            planner_session=planner,
            validator_session=validator,
            reason="fixture_run_failed",
        )
        self.assertTrue(state.alive)
        self.assertEqual(archived, {"planner": True, "validator": True})
        self.assertEqual(receipt.status, "verified")
        self.assertEqual(result["error_type"], "ContractValidationError")
        self.assertEqual(result["provider_calls"], 1)
        state.events.append("provider_context_close")
        state.alive = False

    def test_partial_child_result_recovery_is_exact_and_uses_next_identity(self) -> None:
        with TemporaryDirectory(prefix="cera-child-reconciliation-") as temporary:
            root = Path(temporary)
            run_id = CONTINUOUS_V3_RUN_IDENTITIES[0]
            run_root = root / "runs" / run_id
            run_root.mkdir(parents=True)
            ledger_path = run_root / "PROVIDER_CALL_LEDGER.jsonl"
            ledger_path.write_bytes(
                canonical_bytes(
                    {
                        "event_index": 1,
                        "call_id": "fixture-call-1",
                        "state": "transport_invoked",
                        "owner": "planner",
                    }
                )
                + b"\n"
            )
            old_identity = text_sha256("fixture old execution")
            reconciliation = reconcile_child_run(
                run_root=run_root,
                run_id=run_id,
                execution_identity_sha256=old_identity,
                process_returncode=1,
            )
            self.assertEqual(reconciliation["calls"], [CONTINUOUS_V3_CALL_SCHEDULE[0]])
            self.assertEqual(reconciliation["provider_calls"], 1)
            self.assertEqual(reconciliation["codex_family_calls"], 1)
            self.assertEqual(reconciliation["deepseek_calls"], 0)
            self.assertFalse(reconciliation["passed"])
            with self.assertRaises(FileExistsError):
                reconcile_child_run(
                    run_root=run_root,
                    run_id=run_id,
                    execution_identity_sha256=old_identity,
                    process_returncode=1,
                )

            campaign = ContinuousV3TwoRunCampaign(old_identity)
            campaign.begin_run(run_id, execution_identity_sha256=old_identity)
            campaign.record_dispatch(CONTINUOUS_V3_CALL_SCHEDULE[0])
            campaign.terminalize(passed=False, reason="ChildResultMissing")
            (root / "EXECUTION_MANIFEST.json").write_bytes(
                canonical_bytes({"execution_identity_sha256": old_identity}) + b"\n"
            )
            (root / "CAMPAIGN_RESULT.json").write_bytes(
                canonical_bytes(
                    {
                        "campaign_id": "2026-08-02-continuous-sillytavern-two-run-v1",
                        "execution_identity_sha256": old_identity,
                        "campaign": campaign.to_dict(),
                    }
                )
                + b"\n"
            )
            immutable_before = {
                path: path.read_bytes()
                for path in (
                    ledger_path,
                    run_root / "PARENT_RUN_RECONCILIATION.json",
                    root / "EXECUTION_MANIFEST.json",
                    root / "CAMPAIGN_RESULT.json",
                )
            }
            new_identity = text_sha256("fixture repaired execution")
            recovered, receipt = recover_prior_campaign(
                root,
                execution_identity_sha256=new_identity,
            )
            self.assertEqual(recovered.total_provider_calls, 1)
            self.assertEqual(recovered.codex_family_calls, 1)
            self.assertEqual(recovered.deepseek_calls, 0)
            self.assertEqual(receipt["recovered_codex_family_calls"], 1)
            self.assertEqual(receipt["recovered_deepseek_calls"], 0)
            recovered.begin_run(
                CONTINUOUS_V3_RUN_IDENTITIES[1],
                execution_identity_sha256=new_identity,
            )
            self.assertEqual(
                recovered.current.run_id,
                CONTINUOUS_V3_RUN_IDENTITIES[1],
            )
            self.assertTrue(
                all(path.read_bytes() == content for path, content in immutable_before.items())
            )

    def test_pre_root_child_failure_freezes_zero_call_receipt(self) -> None:
        with TemporaryDirectory(prefix="cera-child-pre-root-") as temporary:
            run_root = Path(temporary) / "run"
            reconciliation = reconcile_child_run(
                run_root=run_root,
                run_id=CONTINUOUS_V3_RUN_IDENTITIES[0],
                execution_identity_sha256=text_sha256("fixture execution"),
                process_returncode=None,
                process_error=RuntimeError("child launch failed"),
            )
            self.assertEqual(reconciliation["provider_calls"], 0)
            self.assertEqual(reconciliation["calls"], [])
            self.assertEqual(reconciliation["terminal_reason"], "RuntimeError")
            self.assertEqual(reconciliation["failure_source"], "parent_process_launch")
            self.assertTrue((run_root / "PARENT_RUN_RECONCILIATION.json").is_file())
            _validate_child_reconciliation(
                reconciliation,
                run_root=run_root,
                expected_run_id=CONTINUOUS_V3_RUN_IDENTITIES[0],
            )

    def test_same_execution_recovery_preserves_one_pass_and_restart_boundary(self) -> None:
        with TemporaryDirectory(prefix="cera-same-execution-recovery-") as temporary:
            root = Path(temporary)
            run_id = CONTINUOUS_V3_RUN_IDENTITIES[0]
            run_root = root / "runs" / run_id
            run_root.mkdir(parents=True)
            identity = text_sha256("same execution fixture")
            campaign = ContinuousV3TwoRunCampaign(identity)
            campaign.begin_run(run_id, execution_identity_sha256=identity)
            for label in CONTINUOUS_V3_CALL_SCHEDULE:
                campaign.record_dispatch(label)
            campaign.terminalize(passed=True, reason="qualified")
            (root / "EXECUTION_MANIFEST.json").write_bytes(
                canonical_bytes({"execution_identity_sha256": identity}) + b"\n"
            )
            (root / "CAMPAIGN_RESULT.json").write_bytes(
                canonical_bytes(
                    {
                        "campaign_id": "2026-08-02-continuous-sillytavern-two-run-v1",
                        "execution_identity_sha256": identity,
                        "campaign": campaign.to_dict(),
                    }
                )
                + b"\n"
            )
            (run_root / "RUN_RESULT.json").write_bytes(
                canonical_bytes(
                    {
                        "run_id": run_id,
                        "status": "passed",
                        "provider_calls": len(CONTINUOUS_V3_CALL_SCHEDULE),
                    }
                )
                + b"\n"
            )
            (run_root / "EXECUTION_MANIFEST.json").write_bytes(
                canonical_bytes({"execution_identity_sha256": identity}) + b"\n"
            )
            (run_root / "PROVIDER_CALL_LEDGER.jsonl").write_bytes(
                b"".join(
                    canonical_bytes(
                        {
                            "event_index": index,
                            "call_id": f"same-execution-call-{index}",
                            "state": "transport_invoked",
                            "owner": (
                                "composer"
                                if "deepseek" in label
                                else "validator"
                                if "validator" in label
                                else "planner"
                            ),
                        }
                    )
                    + b"\n"
                    for index, label in enumerate(
                        CONTINUOUS_V3_CALL_SCHEDULE, start=1
                    )
                )
            )

            resumed, receipt = recover_prior_campaign(
                root,
                execution_identity_sha256=identity,
            )
            self.assertEqual(resumed.consecutive_passes, 1)
            self.assertTrue(resumed.restart_after_last_pass)
            self.assertFalse(receipt["execution_identity_changed"])
            self.assertEqual(receipt["recovered_consecutive_passes"], 1)
            self.assertTrue(receipt["restart_boundary_satisfied_by_new_process"])
            resumed.begin_run(
                CONTINUOUS_V3_RUN_IDENTITIES[1],
                execution_identity_sha256=identity,
            )

            changed_identity = text_sha256("changed execution fixture")
            reset, reset_receipt = recover_prior_campaign(
                root,
                execution_identity_sha256=changed_identity,
            )
            self.assertEqual(reset.consecutive_passes, 0)
            self.assertFalse(reset.restart_after_last_pass)
            self.assertTrue(reset_receipt["execution_identity_changed"])
            self.assertTrue(
                reset_receipt["execution_change_resets_consecutive_passes"]
            )

    def test_unresolved_prepared_call_is_consumed_but_proven_pretransport_is_not(self) -> None:
        with TemporaryDirectory(prefix="cera-child-uncertain-call-") as temporary:
            root = Path(temporary)
            unresolved_root = root / "unresolved"
            unresolved_root.mkdir()
            (unresolved_root / "PROVIDER_CALL_LEDGER.jsonl").write_bytes(
                canonical_bytes(
                    {
                        "event_index": 1,
                        "call_id": "uncertain-call-1",
                        "state": "worker_preflight_not_invoked",
                        "owner": "planner",
                    }
                )
                + b"\n"
            )
            unresolved = reconcile_child_run(
                run_root=unresolved_root,
                run_id=CONTINUOUS_V3_RUN_IDENTITIES[0],
                execution_identity_sha256=text_sha256("unresolved execution"),
                process_returncode=1,
            )
            self.assertEqual(unresolved["provider_calls"], 1)
            self.assertEqual(
                unresolved["calls"], [CONTINUOUS_V3_CALL_SCHEDULE[0]]
            )

            pretransport_root = root / "pretransport"
            pretransport_root.mkdir()
            (pretransport_root / "PROVIDER_CALL_LEDGER.jsonl").write_bytes(
                b"".join(
                    canonical_bytes(event) + b"\n"
                    for event in (
                        {
                            "event_index": 1,
                            "call_id": "local-failure-call-1",
                            "state": "prepared_not_invoked",
                            "owner": "planner",
                        },
                        {
                            "event_index": 2,
                            "call_id": "local-failure-call-1",
                            "state": "pretransport_failed",
                            "owner": "planner",
                        },
                    )
                )
            )
            (pretransport_root / "RUN_RESULT.json").write_bytes(
                canonical_bytes(
                    {
                        "run_id": CONTINUOUS_V3_RUN_IDENTITIES[0],
                        "status": "failed",
                        "provider_calls": 0,
                        "calls": [
                            {
                                "label": CONTINUOUS_V3_CALL_SCHEDULE[0],
                                "status": "failed",
                                "external_provider_calls_observed": 0,
                            }
                        ],
                        "error_type": "LocalPretransportFailure",
                    }
                )
                + b"\n"
            )
            pretransport = reconcile_child_run(
                run_root=pretransport_root,
                run_id=CONTINUOUS_V3_RUN_IDENTITIES[0],
                execution_identity_sha256=text_sha256("pretransport execution"),
                process_returncode=1,
            )
            self.assertEqual(pretransport["provider_calls"], 0)
            self.assertEqual(pretransport["calls"], [])
            self.assertEqual(
                pretransport["call_source"], "child_result_and_provider_ledger"
            )
            self.assertEqual(
                pretransport["terminal_reason"], "LocalPretransportFailure"
            )

    def test_child_pass_with_incomplete_schedule_freezes_as_failure(self) -> None:
        with TemporaryDirectory(prefix="cera-child-incomplete-pass-") as temporary:
            run_root = Path(temporary)
            run_id = CONTINUOUS_V3_RUN_IDENTITIES[0]
            (run_root / "PROVIDER_CALL_LEDGER.jsonl").write_bytes(
                canonical_bytes(
                    {
                        "event_index": 1,
                        "call_id": "incomplete-pass-call-1",
                        "state": "transport_invoked",
                        "owner": "planner",
                    }
                )
                + b"\n"
            )
            (run_root / "RUN_RESULT.json").write_bytes(
                canonical_bytes(
                    {
                        "run_id": run_id,
                        "status": "passed",
                        "provider_calls": 1,
                        "calls": [{"label": CONTINUOUS_V3_CALL_SCHEDULE[0]}],
                    }
                )
                + b"\n"
            )
            reconciliation = reconcile_child_run(
                run_root=run_root,
                run_id=run_id,
                execution_identity_sha256=text_sha256("incomplete pass execution"),
                process_returncode=0,
            )
            self.assertFalse(reconciliation["passed"])
            self.assertEqual(
                reconciliation["terminal_reason"], "IncompleteProviderSchedule"
            )
            self.assertEqual(reconciliation["failure_source"], "child_result")
            self.assertEqual(reconciliation["provider_calls"], 1)

    def test_parent_campaign_terminalizes_partial_child_without_result_file(self) -> None:
        with TemporaryDirectory(prefix="cera-parent-partial-child-") as temporary:
            root = Path(temporary)
            source_db = root / "source.sqlite3"
            source_db.write_bytes(b"provider-free fixture")
            campaign_root = root / "campaign"
            unsigned_manifest = {
                "schema_version": "test.execution_manifest.v1",
                "authority": {"manifest_root_sha256": text_sha256("fixture-root")},
            }
            manifest = {
                **unsigned_manifest,
                "execution_identity_sha256": canonical_sha256(unsigned_manifest),
            }
            arguments = Namespace(
                cycle_directory=root / "cycle",
                source_database=source_db,
                runtime_root=campaign_root,
                expected_checkpoint_sha="a" * 40,
                expected_authorization_sha256=text_sha256("authorization"),
                expected_cycle_id="cycle-test",
                expected_cycle_sequence=22,
                expected_job4_task_id="provider-free-audit",
                historical_v1_campaign_root=root / "historical-v1",
                prior_v2_campaign_root=None,
                transport_mode="non_network_fake_ports",
                provider_activation=None,
            )
            configuration = {
                "configuration_sha256": text_sha256("configuration"),
                "historical_v1_debit": {
                    "debit_sha256": text_sha256("debit"),
                    "codex_family_calls": 1,
                    "deepseek_calls": 0,
                },
            }

            def partial_child(command, **_kwargs):
                run_root = Path(command[command.index("--runtime-root") + 1])
                run_root.mkdir(parents=True)
                (run_root / "PROVIDER_CALL_LEDGER.jsonl").write_bytes(
                    canonical_bytes(
                        {
                            "event_index": 1,
                            "call_id": "parent-fixture-call-1",
                            "state": "transport_invoked",
                            "owner": "planner",
                        }
                    )
                    + b"\n"
                )
                return subprocess.CompletedProcess(command, 1)

            with (
                patch.object(
                    campaign_script,
                    "execution_manifest",
                    side_effect=(manifest, dict(manifest)),
                ),
                patch.object(
                    campaign_script.subprocess,
                    "run",
                    side_effect=partial_child,
                ),
                patch.object(
                    campaign_script,
                    "_manifest_configuration",
                    return_value=configuration,
                ),
            ):
                return_code = campaign_script.run_campaign(arguments)
            self.assertEqual(return_code, 1)
            result = json.loads(
                (campaign_root / "CAMPAIGN_RESULT.json").read_text(encoding="utf-8")
            )
            self.assertEqual(result["campaign"]["total_provider_calls"], 1)
            self.assertEqual(result["campaign"]["codex_family_calls"], 1)
            self.assertEqual(result["campaign"]["deepseek_calls"], 0)
            self.assertEqual(result["campaign"]["runs"][0]["state"], "failed")
            run_root = (
                campaign_root / "runs" / CONTINUOUS_V3_V2_RUN_IDENTITIES[0]
            )
            self.assertFalse((run_root / "RUN_RESULT.json").exists())
            reconciliation = json.loads(
                (run_root / "PARENT_RUN_RECONCILIATION.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(reconciliation["provider_calls"], 1)
            self.assertEqual(
                reconciliation["calls"], [CONTINUOUS_V3_CALL_SCHEDULE[0]]
            )
            self.assertEqual(
                result["schema_version"],
                "cera.sillytavern_continuous_v3_campaign_result.v3",
            )
            self.assertEqual(len(result["run_reconciliations"]), 1)
            self.assertEqual(
                result["run_reconciliations_sha256"],
                canonical_sha256(result["run_reconciliations"]),
            )
            self.assertEqual(
                reconciliation["terminal_reason"],
                "ChildCampaignConfigurationMissing",
            )
            self.assertEqual(reconciliation["external_provider_calls"], 0)


if __name__ == "__main__":
    unittest.main()
