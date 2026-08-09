from __future__ import annotations

import json
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from cera.active_runtime import ACTIVE_RUNTIME_PROFILE
from cera.active_runtime_validation import (
    active_runtime_status,
    validate_active_runtime_bindings,
)

ROOT = Path(__file__).resolve().parents[1]


class ActiveRuntimeProfileTests(unittest.TestCase):
    def test_all_active_source_bindings_match_the_canonical_profile(self) -> None:
        self.assertEqual(validate_active_runtime_bindings(), ())
        status = active_runtime_status()
        self.assertTrue(status["valid"])
        self.assertEqual(status["profile_id"], ACTIVE_RUNTIME_PROFILE.profile_id)
        self.assertEqual(
            status["profile_sha256"],
            ACTIVE_RUNTIME_PROFILE.profile_sha256,
        )

    def test_mutated_profile_is_detected_and_has_a_different_hash(self) -> None:
        mutated = replace(
            ACTIVE_RUNTIME_PROFILE,
            reasoner=replace(
                ACTIVE_RUNTIME_PROFILE.reasoner,
                prompt_version="cera.codex_scene_reasoner_prompt.stale",
            ),
        )
        self.assertNotEqual(
            mutated.profile_sha256,
            ACTIVE_RUNTIME_PROFILE.profile_sha256,
        )
        self.assertTrue(
            any(
                "reasoner prompt" in mismatch
                for mismatch in validate_active_runtime_bindings(mutated)
            )
        )

    def test_profile_command_is_machine_readable_and_validated(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "show_active_runtime_profile.py"),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["valid"])
        self.assertEqual(
            payload["reasoner"]["prompt_version"],
            "cera.codex_scene_reasoner_prompt.v25",
        )
        self.assertEqual(
            payload["reasoner"]["tool_contract_version"],
            "cera.reasoner_evidence_mcp.v7",
        )
        self.assertFalse(payload["composer"]["thinking_enabled"])


if __name__ == "__main__":
    unittest.main()
