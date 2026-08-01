from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from cera.continuous.prompting import PLANNER_STABLE_INSTRUCTIONS
from scripts.run_continuous_planner_validator_job4 import (
    StablePrefixTransport,
    build_report,
    source_character_summary,
    seed_world,
)
from cera.continuous.world import ContinuousWorldStore


ROOT = Path(__file__).resolve().parents[1]


class _Transport:
    route = SimpleNamespace()

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def invoke(self, prompt: str, **kwargs):
        self.prompts.append(prompt)
        return SimpleNamespace(prompt=prompt)


class ContinuousJob4HarnessTests(unittest.TestCase):
    def test_stable_prefix_is_not_resent_in_the_stored_turn(self) -> None:
        inner = _Transport()
        transport = StablePrefixTransport(inner, PLANNER_STABLE_INSTRUCTIONS)
        result = transport.invoke(
            PLANNER_STABLE_INSTRUCTIONS + "\n\n[CURRENT AUTHORITATIVE TURN PACKET]\n{}"
        )
        self.assertEqual(result.prompt, "[CURRENT AUTHORITATIVE TURN PACKET]\n{}")
        self.assertNotIn(PLANNER_STABLE_INSTRUCTIONS, inner.prompts[0])

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
        report = build_report(
            {
                "status": "failed",
                "provider_calls": 2,
                "calls": [],
                "turns": [],
                "failure": {"stage": "turn-1-validator"},
                "story_database_writes": 0,
                "active_route_unchanged": True,
            }
        )
        self.assertIn("Provider calls observed:** 2 / 10", report)
        self.assertIn("deepseek-v4-flash", report)
        self.assertIn("turn-1-validator", report)


if __name__ == "__main__":
    unittest.main()
