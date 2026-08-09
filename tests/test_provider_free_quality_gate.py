from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import unittest


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
        prior_key = os.environ.get("OPENAI_API_KEY")
        try:
            os.environ["OPENAI_API_KEY"] = "must-not-survive"
            package = module._configure_checkout()
            self.assertEqual(package, (ROOT / "src" / "cera").resolve())
            self.assertNotIn("OPENAI_API_KEY", os.environ)
        finally:
            sys.path[:] = prior_path
            if prior_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = prior_key

    def test_compile_gate_includes_source_tests_and_scripts(self) -> None:
        module = _load_runner()
        self.assertGreater(module._compile_python(), 350)


if __name__ == "__main__":
    unittest.main()
