from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_provider_free_quality_gate.py"
_RUNNER_ISOLATION_DIRECTORIES: list[TemporaryDirectory[str]] = []


def _load_runner() -> ModuleType:
    specification = importlib.util.spec_from_file_location("cera_quality_gate", SCRIPT)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    _RUNNER_ISOLATION_DIRECTORIES.append(module._GATE_ISOLATION_DIRECTORY)
    return module


def tearDownModule() -> None:
    while _RUNNER_ISOLATION_DIRECTORIES:
        _RUNNER_ISOLATION_DIRECTORIES.pop().cleanup()


class ProviderFreeQualityGateTests(unittest.TestCase):
    def test_checkout_import_is_exact_and_credentials_are_removed(self) -> None:
        module = _load_runner()
        prior_path = list(sys.path)
        prior_environment = dict(os.environ)
        prior_dont_write_bytecode = sys.dont_write_bytecode
        try:
            os.environ["OPENAI_API_KEY"] = "must-not-survive"
            os.environ["CERA_PI_SCENE_TOKEN"] = "must-not-survive"
            os.environ["CERA_V3_READINESS_EVIDENCE_DIRECTORY"] = "must-not-survive"
            os.environ["CERA_SILLYTAVERN_ROOT"] = r"E:\must-not-be-read"
            package = module._configure_checkout()
            self.assertEqual(package, (ROOT / "src" / "cera").resolve())
            self.assertNotIn("OPENAI_API_KEY", os.environ)
            self.assertNotIn("CERA_PI_SCENE_TOKEN", os.environ)
            self.assertNotIn("CERA_V3_READINESS_EVIDENCE_DIRECTORY", os.environ)
            self.assertEqual(os.environ["CERA_PROVIDER_DISPATCH_DISABLED"], "1")
            self.assertEqual(
                os.environ["CERA_SILLYTAVERN_ROOT"],
                str(module._UNAVAILABLE_SILLYTAVERN_ROOT),
            )
            self.assertEqual(
                module._UNAVAILABLE_SILLYTAVERN_ROOT.parent,
                module._GATE_ISOLATION_ROOT,
            )
            self.assertTrue(module._GATE_ISOLATION_ROOT.is_dir())
            self.assertFalse(module._UNAVAILABLE_SILLYTAVERN_ROOT.exists())
            self.assertFalse(module._UNAVAILABLE_SILLYTAVERN_ROOT.is_symlink())
        finally:
            sys.path[:] = prior_path
            os.environ.clear()
            os.environ.update(prior_environment)
            sys.dont_write_bytecode = prior_dont_write_bytecode

    def test_compile_gate_includes_source_tests_and_scripts(self) -> None:
        module = _load_runner()
        self.assertGreater(module._compile_python(), 350)

    def test_latency_qualification_source_is_format_and_lint_checked(self) -> None:
        module = _load_runner()
        self.assertIn("src/cera/pi_scene/qualification.py", module._FORMAT_TARGETS)
        self.assertIn("src/cera/pi_scene/qualification.py", module._TYPE_TARGETS)
        self.assertIn(
            "tests/test_pi_scene_full_model_qualification.py",
            module._FORMAT_TARGETS,
        )
        self.assertLessEqual(set(module._FORMAT_TARGETS), set(module._LINT_TARGETS))

    def test_every_invoked_mypy_target_is_unique(self) -> None:
        module = _load_runner()
        self.assertEqual(len(module._TYPE_TARGETS), len(set(module._TYPE_TARGETS)))
        self.assertEqual(
            len(module._PROVIDER_STAGE_RETRY_LEGACY_TYPE_TARGETS),
            len(set(module._PROVIDER_STAGE_RETRY_LEGACY_TYPE_TARGETS)),
        )

    def test_provider_stage_retry_python_targets_are_gate_bound(self) -> None:
        module = _load_runner()
        formatted = set(module._PROVIDER_STAGE_RETRY_FORMAT_TARGETS)
        legacy = set(module._PROVIDER_STAGE_RETRY_LEGACY_SOURCE_TARGETS)
        typed = set(module._PROVIDER_STAGE_RETRY_TYPE_TARGETS)
        legacy_typed = set(module._PROVIDER_STAGE_RETRY_LEGACY_TYPE_TARGETS)
        compiled = set(module._PROVIDER_STAGE_RETRY_COMPILE_TARGETS)
        tested = set(module._PROVIDER_STAGE_RETRY_TEST_MODULES)

        self.assertLessEqual(
            {
                "scripts/run_pi_scene_full_model_qualification.py",
                "src/cera/pi_scene/adult_stage_retry_integration.py",
                "src/cera/pi_scene/provider_stage_retry_adapters.py",
                "src/cera/pi_scene/provider_stage_retry_adult_actions.py",
                "src/cera/pi_scene/provider_stage_retry_assembly.py",
                "src/cera/pi_scene/provider_stage_retry_http.py",
                "src/cera/pi_scene/provider_stage_retry_ordinary.py",
                "src/cera/pi_scene/provider_stage_retry_ordinary_custody.py",
                "src/cera/pi_scene/provider_stage_retry_ordinary_retrieval.py",
                "src/cera/pi_scene/qualification_isolation.py",
                "tests/test_pi_scene_adult_stage_retry_integration.py",
                "tests/test_provider_stage_retry_adapters.py",
                "tests/test_provider_stage_retry_assembly.py",
                "tests/test_provider_stage_retry_http.py",
                "tests/test_provider_stage_retry_ordinary.py",
                "tests/test_provider_stage_retry_ordinary_custody.py",
                "tests/test_provider_stage_retry_ordinary_retrieval.py",
            },
            formatted,
        )
        self.assertIn(
            "src/cera/pi_scene/provider_stage_retry_adapters.py",
            typed,
        )
        self.assertIn("tests.test_provider_stage_retry_adapters", tested)
        self.assertIn("tests.test_provider_stage_retry_assembly", tested)
        self.assertIn("tests.test_provider_stage_retry_http", tested)
        self.assertIn("tests.test_pi_scene_full_model_launcher", tested)
        self.assertIn("tests.test_pi_scene_http_session_review", tested)
        self.assertIn("tests.test_pi_scene_lean_v1", tested)
        self.assertIn("tests.test_pi_scene_adult_stage_retry_integration", tested)
        self.assertIn("tests.test_provider_stage_retry_ordinary", tested)

        self.assertLessEqual(formatted, set(module._FORMAT_TARGETS))
        self.assertLessEqual(formatted | legacy, set(module._LINT_TARGETS))
        self.assertEqual(legacy, legacy_typed)
        self.assertLessEqual(typed, set(module._TYPE_TARGETS))
        self.assertLessEqual(typed | legacy_typed, compiled)
        self.assertEqual(
            {
                target.removesuffix(".py").replace("/", ".")
                for target in compiled
                if target.startswith("tests/")
            },
            tested,
        )
        self.assertTrue(
            all(
                (ROOT / target).is_file() for target in module._PROVIDER_STAGE_RETRY_COMPILE_TARGETS
            )
        )

    def test_reader_and_ordinary_review_python_targets_are_gate_bound(self) -> None:
        module = _load_runner()
        reader = set(module._READER_SOURCE_TARGETS)
        lifecycle = set(module._ORDINARY_REVIEW_LIFECYCLE_SOURCE_TARGETS)
        formatted = set(module._ORDINARY_REVIEW_FORMAT_TARGETS)
        linted = set(module._ORDINARY_REVIEW_LINT_TARGETS)
        typed = set(module._ORDINARY_REVIEW_TYPE_TARGETS)
        legacy_type = set(module._ORDINARY_REVIEW_LEGACY_TYPE_TARGETS)
        compiled = set(module._ORDINARY_REVIEW_COMPILE_TARGETS)
        tested = set(module._ORDINARY_REVIEW_TEST_MODULES)

        self.assertEqual(
            reader,
            {
                path.relative_to(ROOT).as_posix()
                for path in (ROOT / "src" / "cera" / "reader_validation").glob("*.py")
            },
        )
        self.assertLessEqual(
            {
                "src/cera/pi_scene/http_contracts.py",
                "src/cera/pi_scene/ordinary_http.py",
                "src/cera/pi_scene/ordinary_rejection_policy.py",
                "src/cera/pi_scene/request_binding.py",
                "src/cera/pi_scene/review_lifecycle.py",
                "src/cera/pi_scene/review_store.py",
                "src/cera/pi_scene/runtime.py",
                "src/cera/pi_scene/store.py",
            },
            lifecycle,
        )
        self.assertLessEqual(
            {
                "tests.test_ordinary_review_schema_generation",
                "tests.test_ordinary_review_schema_generation_v3",
                "tests.test_pi_scene_provisional_review_lifecycle",
                "tests.test_pi_scene_reader_validation",
                "tests.test_provider_stage_retry_assembly",
                "tests.test_provider_stage_retry_http",
                "tests.test_provider_stage_retry_ordinary",
                "tests.test_provider_stage_retry_runtime",
            },
            tested,
        )
        self.assertLessEqual(reader, formatted)
        self.assertLessEqual(reader | lifecycle, linted)
        self.assertEqual(legacy_type, {"src/cera/pi_scene/store.py"})
        self.assertLessEqual(reader | (lifecycle - legacy_type), typed)
        self.assertTrue(legacy_type.isdisjoint(typed))
        self.assertLessEqual(reader | lifecycle, compiled)
        behavior_bindings = module._ORDINARY_REVIEW_LEGACY_TYPE_BEHAVIOR_BINDINGS
        self.assertEqual(set(behavior_bindings), legacy_type)
        self.assertTrue(all(set(modules) <= tested for modules in behavior_bindings.values()))
        self.assertEqual(
            {
                target.removesuffix(".py").replace("/", ".")
                for target in compiled
                if target.startswith("tests/")
            },
            tested,
        )
        self.assertTrue(all((ROOT / target).is_file() for target in compiled))

    def test_sillytavern_retry_sources_and_tests_are_gate_bound(self) -> None:
        module = _load_runner()
        checked = set(module._SILLYTAVERN_NODE_CHECK_TARGETS)
        retry_checked = set(module._SILLYTAVERN_RETRY_NODE_CHECK_TARGETS)
        review_checked = set(module._SILLYTAVERN_ORDINARY_REVIEW_NODE_CHECK_TARGETS)
        tested = set(module._SILLYTAVERN_RETRY_NODE_TEST_TARGETS)
        self.assertLessEqual(retry_checked | review_checked, checked)
        self.assertLessEqual(tested, checked)
        self.assertEqual(
            tested,
            {
                "integrations/sillytavern/cera-review-proxy-plugin/test.mjs",
                "integrations/sillytavern/creator-review-extension/metadata-panel.test.mjs",
            },
        )
        self.assertTrue(all((ROOT / target).is_file() for target in checked))
        self.assertTrue(
            all(
                (ROOT / target).is_file()
                for target in module._SILLYTAVERN_ORDINARY_REVIEW_ASSET_TARGETS
            )
        )

    def test_exact_external_custody_manifest_is_deferred_before_qualification(
        self,
    ) -> None:
        module = _load_runner()

        def ids(
            module_name: str,
            class_name: str,
            *method_names: str,
        ) -> frozenset[str]:
            return frozenset(
                f"{module_name}.{class_name}.{method_name}" for method_name in method_names
            )

        expected = frozenset().union(
            ids(
                "tests.test_sillytavern_installation_contract",
                "SillyTavernInstallationContractTests",
                "test_installed_creator_review_extension_matches_repository_source",
                "test_installed_loopback_relay_matches_repository_source",
                "test_sillytavern_enables_local_plugins_without_auto_update",
                "test_openai_bridge_routes_all_cera_metadata_through_closed_projection",
                "test_cera_controls_cross_the_server_bridge",
                "test_cera_controls_cross_the_client_bridge",
                "test_provisional_messages_are_excluded_from_exports",
            ),
            ids(
                "tests.test_sillytavern_continuous_manual",
                "ContinuousManualLifecycleTests",
                "test_real_process_start_health_restart_and_isolation",
                "test_unresolved_review_restart_requires_exact_recovered_decline",
                "test_cli_review_lookup_rejects_a_different_stopped_root",
                "test_restart_reconciles_exact_accept_after_manual_terminalization_cut",
                "test_unproven_decision_intent_disables_actions_and_restart_fails_closed",
                "test_restart_finishes_terminal_review_record_after_state_write_cut",
                "test_restart_reconciles_exact_decline_after_manual_terminalization_cut",
                "test_dead_process_and_stop_request_recovery_is_identity_bound",
                "test_stop_terminalization_failure_is_frozen_without_raw_error_text",
                "test_execution_manifest_and_process_tampering_fail_closed",
                "test_overlong_windows_root_fails_before_state_creation",
            ),
            ids(
                "tests.test_sillytavern_continuous_manual",
                "ContinuousManualReadinessTests",
                "test_manifest_preserves_run001_debit_and_fresh_v2_identities",
                "test_production_shaped_two_run_route_passes_locally_and_resets",
            ),
            ids(
                "tests.test_continuous_v2_execution_authority",
                "ContinuousV2ExecutionAuthorityTests",
                "test_provider_manual_fake_construction_has_zero_external_calls",
            ),
            ids(
                "tests.test_continuous_v3_executable_readiness",
                "ContinuousV3ExecutableReadinessTests",
                "test_actual_v2_parent_child_failure_recovery_and_long_root",
                "test_provider_backed_manual_actual_process_fake_ports",
                "test_provider_backed_pending_decision_recovery_fails_closed",
            ),
            ids(
                "tests.test_queue0056_frozen_validator_regression",
                "Queue0056FrozenValidatorRegressionTests",
                "test_candidate_one_reciprocal_gaze_remains_a_hard_ted_assertion",
                "test_candidate_two_relational_color_is_soft_and_noncanonical",
                "test_candidate_three_silence_is_source_grounded_public_state",
            ),
            ids(
                "tests.test_validator_simplification_source_grounded_state",
                "ValidatorSimplificationSourceGroundedStateTests",
                "test_optional_incidental_prop_offer_is_soft_and_nonpersistent",
                "test_exact_frozen_candidate_exercises_both_corrected_classes",
            ),
            ids(
                "tests.test_cera_runtime_model_v3",
                "RuntimeModelV3ReaderAndDocumentationTests",
                "test_frozen_v7_evidence_and_deferred_fallback_invariants",
            ),
            ids(
                "tests.test_runtime_model_v3_stage4_harness_identity_contract",
                "RuntimeModelV3Stage4HarnessIdentityContractTests",
                "test_historical_runner_remains_immutable_evidence",
            ),
            ids(
                "tests.test_runtime_model_v3_reader_fixtures",
                "RuntimeModelV3ReaderFixtureAuthorityTests",
                "test_historical_failed_fixture_source_is_not_rewritten",
            ),
            ids(
                "tests.test_runtime_model_v3_stage4_harness_contract",
                "RuntimeModelV3Stage4HarnessContractTests",
                "test_historical_runner_is_preserved_and_identifies_drift",
            ),
        )
        self.assertEqual(
            module._DEFERRED_PREQUALIFICATION_TESTS,
            expected,
        )
        self.assertEqual(len(expected), 33)
        self.assertEqual(
            tuple(
                len(group)
                for group in (
                    module._DIRECT_INSTALLED_ROOT_TESTS,
                    module._CONTINUOUS_MANUAL_LIFECYCLE_TESTS,
                    module._CONTINUOUS_MANUAL_READINESS_TESTS,
                    module._CONTINUOUS_V2_EXTERNAL_TESTS,
                    module._CONTINUOUS_V3_EXTERNAL_TESTS,
                    module._FROZEN_EXTERNAL_EVIDENCE_TESTS,
                )
            ),
            (7, 11, 2, 1, 3, 9),
        )
        self.assertEqual(
            set(module._AUDITED_EXTERNAL_CUSTODY_SOURCE_SHA256),
            {
                "scripts/run_cera_sillytavern_continuous_manual.py",
                "scripts/run_cera_sillytavern_continuous_manual_readiness.py",
                "tests/test_sillytavern_installation_contract.py",
                "tests/test_sillytavern_continuous_manual.py",
                "tests/test_continuous_v2_execution_authority.py",
                "tests/test_continuous_v3_executable_readiness.py",
                "tests/test_queue0056_frozen_validator_regression.py",
                "tests/test_validator_simplification_source_grounded_state.py",
                "tests/test_cera_runtime_model_v3.py",
                "tests/test_runtime_model_v3_stage4_harness_identity_contract.py",
                "tests/test_runtime_model_v3_reader_fixtures.py",
                "tests/test_runtime_model_v3_stage4_harness_contract.py",
            },
        )
        module._assert_audited_hazard_manifest()
        self.assertEqual(
            module._direct_hazard_test_ids() - module._DEFERRED_PREQUALIFICATION_TESTS,
            frozenset(),
        )

    def test_explicit_test_selection_cannot_bypass_external_custody_deferral(
        self,
    ) -> None:
        module = _load_runner()
        selected = str(next(iter(sorted(module._DIRECT_INSTALLED_ROOT_TESTS))))
        selected_class = selected.rsplit(".", 1)[0]
        selected_module = selected_class.rsplit(".", 1)[0]
        with patch.object(
            module.unittest.defaultTestLoader,
            "loadTestsFromNames",
        ) as load:
            for selector in (selected, selected_class, selected_module):
                with self.subTest(selector=selector):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "cannot load external-custody selectors",
                    ):
                        module._suite((selector,))
            load.assert_not_called()

        class DeferredCase(unittest.TestCase):
            def id(self) -> str:
                return selected

        with patch.object(
            module.unittest.defaultTestLoader,
            "loadTestsFromNames",
            return_value=unittest.TestSuite((DeferredCase(),)),
        ) as load:
            with self.assertRaisesRegex(
                RuntimeError,
                "cannot select external-custody tests",
            ):
                module._suite(("safe.alias",))
            load.assert_called_once_with(("safe.alias",))

    def test_existing_sillytavern_sentinel_fails_closed(self) -> None:
        module = _load_runner()
        with patch.object(
            module,
            "_UNAVAILABLE_SILLYTAVERN_ROOT",
            module._GATE_ISOLATION_ROOT,
        ):
            with self.assertRaisesRegex(RuntimeError, "sentinel unexpectedly exists"):
                module._assert_isolation_sentinel_absent()

    def test_quality_subprocesses_reassert_provider_free_environment(self) -> None:
        module = _load_runner()
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "must-not-reach-child",
                "CERA_PI_SCENE_TOKEN": "must-not-reach-child",
                "CERA_V3_READINESS_EVIDENCE_DIRECTORY": "must-not-reach-child",
                "CERA_SILLYTAVERN_ROOT": r"E:\must-not-be-read",
                "CERA_PROVIDER_DISPATCH_DISABLED": "0",
            },
            clear=False,
        ):
            with patch.object(module.subprocess, "run") as run:
                run.return_value.returncode = 0
                module._run_checked((sys.executable, "--version"), label="probe")
            environment = run.call_args.kwargs["env"]
            self.assertNotIn("OPENAI_API_KEY", environment)
            self.assertNotIn("CERA_PI_SCENE_TOKEN", environment)
            self.assertNotIn("CERA_V3_READINESS_EVIDENCE_DIRECTORY", environment)
            self.assertEqual(
                environment["CERA_SILLYTAVERN_ROOT"],
                str(module._UNAVAILABLE_SILLYTAVERN_ROOT),
            )
            self.assertFalse(module._UNAVAILABLE_SILLYTAVERN_ROOT.exists())
            self.assertEqual(environment["CERA_PROVIDER_DISPATCH_DISABLED"], "1")

    def test_generated_provider_stage_contracts_are_gate_bound(self) -> None:
        module = _load_runner()
        self.assertIn(module._GENERATED_CONTRACT_CHECK, module._FORMAT_TARGETS)
        self.assertIn(module._GENERATED_CONTRACT_CHECK, module._TYPE_TARGETS)
        self.assertEqual(
            set(module._PROVIDER_STAGE_RETRY_SCHEMA_TARGETS),
            {
                path.relative_to(ROOT).as_posix()
                for path in (ROOT / "schemas" / "provider_stage_retry" / "v1").glob("*.schema.json")
            },
        )
        completed = subprocess.run(
            (
                sys.executable,
                str(ROOT / module._GENERATED_CONTRACT_CHECK),
                "--check",
            ),
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_generated_ordinary_review_contracts_are_gate_bound(self) -> None:
        module = _load_runner()
        generators = {
            module._ORDINARY_REVIEW_GENERATED_CONTRACT_CHECK,
            module._ORDINARY_REVIEW_V3_GENERATED_CONTRACT_CHECK,
        }
        self.assertLessEqual(generators, set(module._FORMAT_TARGETS))
        self.assertLessEqual(generators, set(module._TYPE_TARGETS))
        self.assertEqual(
            set(module._ORDINARY_REVIEW_SCHEMA_TARGETS),
            {
                path.relative_to(ROOT).as_posix()
                for version in ("v2", "v3")
                for path in (ROOT / "schemas" / "pi_scene" / "ordinary_review" / version).glob(
                    "*.schema.json"
                )
            },
        )
        self.assertEqual(
            set(module._ORDINARY_REVIEW_GENERATED_TARGETS),
            {
                "docs/generated/ORDINARY_REVIEW_CONTRACTS_V2.md",
                "integrations/sillytavern/generated/ordinary-review-contracts-v2.mjs",
                "integrations/sillytavern/cera-review-proxy-plugin/generated/ordinary-review-contracts-v2.mjs",
                "integrations/sillytavern/creator-review-extension/generated/ordinary-review-contracts-v2.mjs",
                "src/cera/generated/ordinary_review_contracts_v2.py",
                "tests/fixtures/generated/ordinary_review_v2_negative.json",
                "tests/fixtures/generated/ordinary_review_v2_positive.json",
                "docs/generated/ORDINARY_REVIEW_CONTRACTS_V3.md",
                "integrations/sillytavern/generated/ordinary-review-contracts-v3.mjs",
                "integrations/sillytavern/cera-review-proxy-plugin/generated/ordinary-review-contracts-v3.mjs",
                "integrations/sillytavern/creator-review-extension/generated/ordinary-review-contracts-v3.mjs",
                "integrations/sillytavern/creator-review-extension/generated/ordinary_review_v3_positive.json",
                "src/cera/generated/ordinary_review_contracts_v3.py",
                "tests/fixtures/generated/ordinary_review_v3_negative.json",
                "tests/fixtures/generated/ordinary_review_v3_positive.json",
            },
        )
        self.assertTrue(
            all((ROOT / target).is_file() for target in module._ORDINARY_REVIEW_GENERATED_TARGETS)
        )
        for generator in generators:
            with self.subTest(generator=generator):
                completed = subprocess.run(
                    (sys.executable, str(ROOT / generator), "--check"),
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_quality_tool_phase_invokes_both_generated_contract_checks(self) -> None:
        module = _load_runner()
        with patch.object(module, "_assert_quality_tool_versions"):
            with patch.object(module, "_run_checked") as run_checked:
                module._run_quality_tools()
        commands = {call.args[0] for call in run_checked.call_args_list}
        self.assertLessEqual(
            {
                (sys.executable, str(ROOT / target), "--check")
                for _, target in module._GENERATED_CONTRACT_CHECKS
            },
            commands,
        )

    def test_exact_seven_stage_closure_and_reader_medium_profile(self) -> None:
        module = _load_runner()
        prior_path = list(sys.path)
        prior_environment = dict(os.environ)
        prior_dont_write_bytecode = sys.dont_write_bytecode
        try:
            module._configure_checkout()
            module._assert_provider_stage_closure()
            module._assert_reader_medium_profile()
            self.assertEqual(len(module._EXPECTED_PROVIDER_STAGES), 7)
            self.assertIn("reader", module._EXPECTED_PROVIDER_STAGES)
        finally:
            sys.path[:] = prior_path
            os.environ.clear()
            os.environ.update(prior_environment)
            sys.dont_write_bytecode = prior_dont_write_bytecode


if __name__ == "__main__":
    unittest.main()
