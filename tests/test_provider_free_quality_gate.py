from __future__ import annotations

import importlib.util
import os
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


if __name__ == "__main__":
    unittest.main()
