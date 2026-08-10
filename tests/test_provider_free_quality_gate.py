from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_provider_free_quality_gate.py"


def _load_runner():
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
        self.assertEqual(module._LINT_TARGETS, module._FORMAT_TARGETS)

    def test_generated_provider_stage_contracts_are_gate_bound(self) -> None:
        module = _load_runner()
        self.assertIn(module._GENERATED_CONTRACT_CHECK, module._FORMAT_TARGETS)
        self.assertIn(module._GENERATED_CONTRACT_CHECK, module._TYPE_TARGETS)
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
