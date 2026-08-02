from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from cera.continuous.sessions import (
    CONTINUOUS_ACCEPTED_SNAPSHOT_PATH_POLICY_SHA256,
)
from cera.serialization import canonical_sha256, text_sha256
from cera.sillytavern.campaign import (
    CONTINUOUS_V3_CALL_SCHEDULE,
    CONTINUOUS_V3_RUN_IDENTITIES,
    CONTINUOUS_V3_V2_RUN_IDENTITIES,
    validate_v2_campaign_configuration,
)
from cera.sillytavern.manual_routes import (
    PROVIDER_BACKED_MANUAL_ROUTE,
    PROVIDER_FREE_MANUAL_ROUTE,
    validate_manual_profile,
)
from cera.sillytavern.models import (
    CERA_CONTINUOUS_V3_PROVIDER_MANUAL_MODEL,
    ChatMessage,
    SillyTavernChatRequest,
)
from cera.sillytavern.provider_authority import (
    CONTINUOUS_PROVIDER_ACTIVATION_SCHEMA,
    CONTINUOUS_PROVIDER_MODELS,
    validate_provider_activation,
)
from scripts import run_cera_sillytavern_continuous_manual as manual


ROOT = Path(__file__).resolve().parents[1]


def _signed_debit() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "cera.sillytavern_historical_v1_call_debit.v1",
        "campaign_id": "2026-08-02-continuous-sillytavern-two-run-v1",
        "run_id": CONTINUOUS_V3_RUN_IDENTITIES[0],
        "calls": [CONTINUOUS_V3_CALL_SCHEDULE[0]],
        "codex_family_calls": 1,
        "deepseek_calls": 0,
        "campaign_result_sha256": text_sha256("campaign-result"),
        "campaign_execution_manifest_sha256": text_sha256("campaign-manifest"),
        "run_result_sha256": text_sha256("run-result"),
        "provider_ledger_sha256": text_sha256("provider-ledger"),
        "run_execution_manifest_sha256": text_sha256("run-manifest"),
    }
    return {**value, "debit_sha256": canonical_sha256(value)}


def _configuration() -> dict[str, object]:
    profile_path = "integrations/sillytavern/continuous_v3_test_profile.json"
    profile_sha256 = text_sha256("profile")
    messages = ["one", "two", "three"]
    value: dict[str, object] = {
        "schema_version": "cera.sillytavern_continuous_v3_campaign_configuration.v2",
        "campaign_id": "2026-08-02-continuous-sillytavern-two-run-v2",
        "cycle_authority": {
            "cycle_id": "cycle-v2",
            "cycle_sequence": 23,
            "checkpoint_git_sha": "a" * 40,
            "cycle_manifest_sha256": text_sha256("cycle-manifest"),
            "manifest_root_sha256": text_sha256("manifest-root"),
            "repository_identity_sha256": text_sha256("repository"),
            "task_set_sha256": text_sha256("tasks"),
            "changed_source_manifest_sha256": text_sha256("source-manifest"),
            "published_receipt_sha256": text_sha256("published"),
            "job4_task_id": "job4-v2",
            "job4_scope_sha256": text_sha256("scope"),
            "job4_started_receipt_sha256": text_sha256("started"),
            "trigger_receipt_sha256": text_sha256("trigger"),
            "authorization_record_sha256": text_sha256("authorization"),
            "cycle_state": "job4_in_progress",
            "cycle_state_sha256": text_sha256("state"),
        },
        "run_identities": list(CONTINUOUS_V3_V2_RUN_IDENTITIES),
        "call_budget": {
            "creator_codex_family_total": 800,
            "creator_deepseek_total": 800,
            "prior_codex_family_debit": 1,
            "prior_deepseek_debit": 0,
            "remaining_codex_family_calls": 799,
            "remaining_deepseek_calls": 800,
            "campaign_total_stage_ceiling": 39,
            "per_run_stage_ceiling": 10,
        },
        "historical_v1_debit": _signed_debit(),
        "fixture": {
            "messages": messages,
            "fixture_sha256": canonical_sha256(messages),
            "call_schedule": list(CONTINUOUS_V3_CALL_SCHEDULE),
            "call_schedule_sha256": canonical_sha256(
                CONTINUOUS_V3_CALL_SCHEDULE
            ),
        },
        "route": {
            "profile_id": "cera.sillytavern.continuous_v3_test.v1",
            "profile_path": profile_path,
            "profile_sha256": profile_sha256,
            "profile": {"profile_id": "cera.sillytavern.continuous_v3_test.v1"},
            "host": "127.0.0.1",
            "port": 5113,
            "model": "cera-continuous-v3-test",
        },
        "providers": CONTINUOUS_PROVIDER_MODELS,
        "prompt_bindings": {
            "planner_prompt_version": "planner-v1",
            "planner_stable_instructions_sha256": text_sha256("planner-stable"),
            "planner_base_instructions_sha256": text_sha256("planner-base"),
            "composer_prompt_version": "composer-v1",
            "validator_prompt_version": "validator-v1",
            "validator_stable_instructions_sha256": text_sha256("validator-stable"),
            "validator_base_instructions_sha256": text_sha256("validator-base"),
        },
        "schema_bindings": {
            "planner_sequence": "planner-schema",
            "validator_package": "validator-schema",
            "http_review": "review-schema",
            "campaign_configuration": (
                "cera.sillytavern_continuous_v3_campaign_configuration.v2"
            ),
        },
        "policy_bindings": {
            "persistence_policy_sha256": text_sha256("persistence"),
            "accepted_snapshot_path_policy_sha256": (
                CONTINUOUS_ACCEPTED_SNAPSHOT_PATH_POLICY_SHA256
            ),
            "strict_accept_only": True,
            "automatic_retry": False,
            "fallback": False,
            "automatic_false_positive": False,
        },
        "source_bindings": {
            "checkpoint_sha": "a" * 40,
            "actual_head_sha": "a" * 40,
            "actual_tree_sha": "b" * 40,
            "tracked_file_count": 1,
            "tracked_files_root_sha256": canonical_sha256(
                {profile_path: profile_sha256}
            ),
            "tracked_files": {profile_path: profile_sha256},
            "source_database_sha256": text_sha256("database"),
        },
        "transport_mode": {
            "mode": "non_network_fake_ports",
            "external_provider_calls_authorized": 0,
            "provider_activation_relative_path": None,
            "provider_activation_file_sha256": None,
            "provider_activation_receipt_sha256": None,
            "fake_fixture_id": "scripted-fixture-v1",
            "fake_fixture_sha256": text_sha256("fixture-source"),
        },
    }
    return {**value, "configuration_sha256": canonical_sha256(value)}


class ContinuousV2ExecutionAuthorityTests(unittest.TestCase):
    def test_configuration_is_one_self_bound_v2_authority(self) -> None:
        value = _configuration()
        self.assertEqual(validate_v2_campaign_configuration(value), value)
        changed = dict(value)
        changed["run_identities"] = list(CONTINUOUS_V3_RUN_IDENTITIES)
        changed["configuration_sha256"] = canonical_sha256(
            {key: item for key, item in changed.items() if key != "configuration_sha256"}
        )
        with self.assertRaisesRegex(Exception, "run identities"):
            validate_v2_campaign_configuration(changed)

    def test_actual_child_cli_refuses_historical_v1_identity(self) -> None:
        command = [
            sys.executable,
            str(ROOT / "scripts" / "run_sillytavern_continuous_v3_campaign.py"),
            "--single-run",
            "--run-id",
            CONTINUOUS_V3_RUN_IDENTITIES[0],
            "--cycle-directory",
            str(ROOT / "absent-cycle"),
            "--source-database",
            str(ROOT / "absent.sqlite3"),
            "--runtime-root",
            str(ROOT / "runtime" / "absent-v1-run"),
            "--historical-v1-campaign-root",
            str(ROOT / "absent-v1-campaign"),
            "--transport-mode",
            "non_network_fake_ports",
            "--expected-checkpoint-sha",
            "a" * 40,
            "--expected-cycle-id",
            "cycle-v2",
            "--expected-cycle-sequence",
            "23",
            "--expected-job4-task-id",
            "job4-v2",
            "--expected-authorization-sha256",
            text_sha256("authorization"),
            "--execution-manifest",
            str(ROOT / "absent-manifest.json"),
            "--execution-identity-sha256",
            text_sha256("execution"),
        ]
        completed = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True, check=False
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("fresh V2 campaign run identity", completed.stderr)
        self.assertFalse((ROOT / "runtime" / "absent-v1-run").exists())

    def test_provider_activation_is_exact_and_profile_bound(self) -> None:
        unsigned = {
            "schema_version": CONTINUOUS_PROVIDER_ACTIVATION_SCHEMA,
            "cycle_id": "cycle-v2",
            "cycle_sequence": 23,
            "job4_task_id": "job4-v2",
            "job4_authorization_sha256": text_sha256("authorization"),
            "route_profile_id": PROVIDER_BACKED_MANUAL_ROUTE.profile_id,
            "provider_models": CONTINUOUS_PROVIDER_MODELS,
            "maximum_codex_family_calls": 20,
            "maximum_deepseek_calls": 10,
            "activation_nonce": "activation-v2-001",
            "authority_source_sha256": text_sha256("cycle-manifest"),
        }
        value = {**unsigned, "activation_sha256": canonical_sha256(unsigned)}
        validated = validate_provider_activation(
            value,
            expected_cycle_id="cycle-v2",
            expected_cycle_sequence=23,
            expected_job4_task_id="job4-v2",
            expected_job4_authorization_sha256=text_sha256("authorization"),
            expected_route_profile_id=PROVIDER_BACKED_MANUAL_ROUTE.profile_id,
            maximum_codex_family_calls=799,
            maximum_deepseek_calls=800,
        )
        self.assertEqual(validated, value)
        with self.assertRaisesRegex(Exception, "authority changed"):
            validate_provider_activation(
                value,
                expected_cycle_id="cycle-v2",
                expected_cycle_sequence=23,
                expected_job4_task_id="job4-v2",
                expected_job4_authorization_sha256=text_sha256("authorization"),
                expected_route_profile_id=PROVIDER_FREE_MANUAL_ROUTE.profile_id,
                maximum_codex_family_calls=799,
                maximum_deepseek_calls=800,
            )

    def test_external_manual_mode_requires_cycle_bound_activation(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError, "requires one activation receipt"
        ):
            manual.configure_manual_execution(
                PROVIDER_BACKED_MANUAL_ROUTE,
                transport_mode="external_provider",
            )
        with self.assertRaisesRegex(RuntimeError, "reject provider authority"):
            manual.configure_manual_execution(
                PROVIDER_BACKED_MANUAL_ROUTE,
                transport_mode="non_network_fake_ports",
                provider_activation=ROOT / "not-authorized.json",
            )

    def test_manual_profiles_reject_substitution_both_ways(self) -> None:
        free = json.loads(
            PROVIDER_FREE_MANUAL_ROUTE.profile_path(ROOT).read_text(encoding="utf-8")
        )
        provider = json.loads(
            PROVIDER_BACKED_MANUAL_ROUTE.profile_path(ROOT).read_text(encoding="utf-8")
        )
        self.assertEqual(
            validate_manual_profile(free, route=PROVIDER_FREE_MANUAL_ROUTE), free
        )
        self.assertEqual(
            validate_manual_profile(provider, route=PROVIDER_BACKED_MANUAL_ROUTE),
            provider,
        )
        with self.assertRaises(Exception):
            validate_manual_profile(free, route=PROVIDER_BACKED_MANUAL_ROUTE)
        with self.assertRaises(Exception):
            validate_manual_profile(provider, route=PROVIDER_FREE_MANUAL_ROUTE)

    def test_provider_manual_fake_construction_has_zero_external_calls(self) -> None:
        manual_root = (ROOT / "runtime" / "manual").resolve()
        manual_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=manual_root) as temporary:
            root = Path(temporary) / "provider-route"
            try:
                manual.configure_manual_execution(
                    PROVIDER_BACKED_MANUAL_ROUTE,
                    transport_mode="non_network_fake_ports",
                )
                manual.reset_manual_root(root, session_id="provider-route-test")
                adapter, harness, stack = manual.build_provider_backed_manual_adapter(
                    root,
                    process_instance_id="provider-route-test-process",
                )
                self.assertEqual(adapter.profile_id, PROVIDER_BACKED_MANUAL_ROUTE.profile_id)
                self.assertEqual(adapter.virtual_model, CERA_CONTINUOUS_V3_PROVIDER_MANUAL_MODEL)
                self.assertTrue(harness.scripted_provider_free)
                self.assertEqual(
                    adapter.reasoner_session_status["external_provider_calls_authorized"],
                    0,
                )
                stack.close()
            finally:
                manual.configure_manual_execution(
                    PROVIDER_FREE_MANUAL_ROUTE,
                    transport_mode="scripted_provider_free",
                )

    def test_provider_virtual_model_is_request_valid_but_not_free_profile(self) -> None:
        request = SillyTavernChatRequest(
            model=CERA_CONTINUOUS_V3_PROVIDER_MANUAL_MODEL,
            messages=(ChatMessage(role="user", content="Hana answers."),),
            stream=False,
            cera_session_id="provider-route-test",
            cera_profile_id=PROVIDER_BACKED_MANUAL_ROUTE.profile_id,
        )
        self.assertEqual(request.model, CERA_CONTINUOUS_V3_PROVIDER_MANUAL_MODEL)


if __name__ == "__main__":
    unittest.main()
