from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_codex_reasoner_model_ladder.py"


def load_script():
    spec = importlib.util.spec_from_file_location("cera_model_ladder", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CodexReasonerModelLadderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_script()

    def test_matrix_covers_requested_practical_range(self) -> None:
        actual = tuple(
            (value.model, value.effort) for value in self.module.CANDIDATES
        )
        self.assertEqual(
            actual,
            (
                ("gpt-5.6-luna", "high"),
                ("gpt-5.6-luna", "xhigh"),
                ("gpt-5.6-terra", "low"),
                ("gpt-5.6-terra", "medium"),
                ("gpt-5.6-terra", "high"),
                ("gpt-5.6-terra", "xhigh"),
                ("gpt-5.6-sol", "low"),
                ("gpt-5.6-sol", "medium"),
            ),
        )

    def test_stage_ledger_is_unique_and_counts_every_planned_dispatch(self) -> None:
        stages = self.module.planned_provider_stages()
        names = [value["stage"] for value in stages]
        self.assertEqual(len(stages), 48)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(
            sum(value["provider_category"] == "sol" for value in stages),
            32,
        )
        self.assertEqual(
            sum(value["provider_category"] == "deepseek" for value in stages),
            16,
        )
        self.assertFalse(any(value["dispatch_started"] for value in stages))

    def test_cache_namespace_is_route_specific_and_semantically_external(self) -> None:
        class FakeTransport:
            route = object()

            def __init__(self) -> None:
                self.prompt = None
                self.kwargs = None

            def invoke(self, prompt, **kwargs):
                self.prompt = prompt
                self.kwargs = kwargs
                return "result"

        fake = FakeTransport()
        wrapped = self.module.CacheNamespacedCodexTransport(fake, "benchmark:luna")
        result = wrapped.invoke("CERA typed Scene Reasoner invocation.", answer=1)
        self.assertEqual(result, "result")
        self.assertEqual(wrapped.route, fake.route)
        self.assertEqual(
            fake.prompt,
            "CERA non-semantic cache namespace: benchmark:luna\n"
            "CERA typed Scene Reasoner invocation.",
        )
        self.assertEqual(fake.kwargs, {"answer": 1})


if __name__ == "__main__":
    unittest.main()
