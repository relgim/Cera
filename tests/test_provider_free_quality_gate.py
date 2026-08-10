from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_provider_free_quality_gate.py"


def _load_runner() -> ModuleType:
    specification = importlib.util.spec_from_file_location("cera_quality_gate", SCRIPT)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class ProviderFreeQualityGateTests(unittest.TestCase):
    def test_checkout_import_is_exact_and_credentials_are_removed(self) -> None:
        module = _load_runner()
        prior_path = list(sys.path)
        prior_environment = dict(os.environ)
        prior_dont_write_bytecode = sys.dont_write_bytecode
        try:
            os.environ["OPENAI_API_KEY"] = "must-not-survive"
            package = module._configure_checkout()
            self.assertEqual(package, (ROOT / "src" / "cera").resolve())
            self.assertNotIn("OPENAI_API_KEY", os.environ)
            self.assertEqual(os.environ["CERA_PROVIDER_DISPATCH_DISABLED"], "1")
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

    def test_sillytavern_retry_sources_and_tests_are_gate_bound(self) -> None:
        module = _load_runner()
        checked = set(module._SILLYTAVERN_RETRY_NODE_CHECK_TARGETS)
        tested = set(module._SILLYTAVERN_RETRY_NODE_TEST_TARGETS)
        self.assertLessEqual(tested, checked)
        self.assertEqual(
            tested,
            {
                "integrations/sillytavern/cera-review-proxy-plugin/test.mjs",
                "integrations/sillytavern/creator-review-extension/metadata-panel.test.mjs",
            },
        )
        self.assertTrue(all((ROOT / target).is_file() for target in checked))

    def test_only_installed_byte_equivalence_is_deferred_before_qualification(self) -> None:
        module = _load_runner()
        self.assertEqual(
            module._DEFERRED_PREQUALIFICATION_TESTS,
            {
                "tests.test_sillytavern_installation_contract."
                "SillyTavernInstallationContractTests."
                "test_installed_creator_review_extension_matches_repository_source",
                "tests.test_sillytavern_installation_contract."
                "SillyTavernInstallationContractTests."
                "test_installed_loopback_relay_matches_repository_source",
            },
        )

    def test_quality_subprocesses_reassert_provider_free_environment(self) -> None:
        module = _load_runner()
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "must-not-reach-child",
                "CERA_PROVIDER_DISPATCH_DISABLED": "0",
            },
            clear=False,
        ):
            with patch.object(module.subprocess, "run") as run:
                run.return_value.returncode = 0
                module._run_checked((sys.executable, "--version"), label="probe")
            environment = run.call_args.kwargs["env"]
            self.assertNotIn("OPENAI_API_KEY", environment)
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


if __name__ == "__main__":
    unittest.main()
