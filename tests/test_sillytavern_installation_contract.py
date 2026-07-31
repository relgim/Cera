from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SILLYTAVERN_ROOT = Path(
    os.environ.get("CERA_SILLYTAVERN_ROOT", r"E:\AIChatBot\SillyTavern")
)


class SillyTavernInstallationContractTests(unittest.TestCase):
    def test_installed_creator_review_extension_matches_repository_source(self) -> None:
        source_root = (
            REPOSITORY_ROOT
            / "integrations"
            / "sillytavern"
            / "creator-review-extension"
        )
        installed_root = (
            SILLYTAVERN_ROOT
            / "public"
            / "scripts"
            / "extensions"
            / "third-party"
            / "cera-creator-review"
        )
        for name in ("index.js", "style.css", "manifest.json"):
            source = source_root / name
            installed = installed_root / name
            self.assertTrue(installed.is_file(), f"missing installed extension: {installed}")
            self.assertEqual(source.read_bytes(), installed.read_bytes())

    def test_creator_review_uses_authenticated_same_origin_relay(self) -> None:
        extension = (
            REPOSITORY_ROOT
            / "integrations"
            / "sillytavern"
            / "creator-review-extension"
            / "index.js"
        ).read_text(encoding="utf-8")
        self.assertIn("const API_ROOT = '/api/plugins/cera-review'", extension)
        self.assertIn("getRequestHeaders", extension)
        self.assertNotIn("const API_ROOT = 'http://127.0.0.1:5101'", extension)

    def test_installed_loopback_relay_matches_repository_source(self) -> None:
        source_root = (
            REPOSITORY_ROOT
            / "integrations"
            / "sillytavern"
            / "cera-review-proxy-plugin"
        )
        installed_root = SILLYTAVERN_ROOT / "plugins" / "cera-review-proxy"
        for name in ("index.js", "package.json"):
            source = source_root / name
            installed = installed_root / name
            self.assertTrue(installed.is_file(), f"missing installed plugin: {installed}")
            self.assertEqual(source.read_bytes(), installed.read_bytes())

    def test_sillytavern_enables_local_plugins_without_auto_update(self) -> None:
        config = (SILLYTAVERN_ROOT / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("enableServerPlugins: true", config)
        self.assertIn("enableServerPluginsAutoUpdate: false", config)

    def test_loopback_relay_node_contract(self) -> None:
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for the installed SillyTavern contract")
        result = subprocess.run(
            [
                str(node),
                "--test",
                str(
                    REPOSITORY_ROOT
                    / "integrations"
                    / "sillytavern"
                    / "cera-review-proxy-plugin"
                    / "test.mjs"
                ),
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_review_metadata_handoff_is_bounded_and_render_aware(self) -> None:
        extension = (
            REPOSITORY_ROOT
            / "integrations"
            / "sillytavern"
            / "creator-review-extension"
            / "index.js"
        ).read_text(encoding="utf-8")
        self.assertIn("window.ceraCompletionMetadataQueue", extension)
        self.assertIn("event_types.MESSAGE_RECEIVED", extension)
        self.assertIn("event_types.CHARACTER_MESSAGE_RENDERED", extension)
        self.assertIn("event_types.GENERATION_ENDED", extension)
        self.assertIn("attachPendingMetadata(messageId, { resume: false })", extension)
        self.assertIn("await resumeReview(messageId)", extension)

    def test_creator_review_presentation_contract_is_explicit(self) -> None:
        source_root = (
            REPOSITORY_ROOT
            / "integrations"
            / "sillytavern"
            / "creator-review-extension"
        )
        extension = (source_root / "index.js").read_text(encoding="utf-8")
        styles = (source_root / "style.css").read_text(encoding="utf-8")
        self.assertIn("actionButton('Adjustment'", extension)
        self.assertNotIn("Correction / Adjustment", extension)
        self.assertIn("'False Positive'", extension)
        self.assertIn("controlSelect('Sol'", extension)
        self.assertIn("['medium', 'M'], ['high', 'H'], ['xhigh', 'Ex']", extension)
        self.assertIn("'false_positive'", extension)
        self.assertIn("renderStoredSpeakerMarks", extension)
        self.assertIn("vera_cast_readability", extension)
        for severity in ("good", "concern", "critical", "error"):
            self.assertIn(f".cera-review-severity-{severity}", styles)
        for speaker in ("hana", "sakura", "mia", "enne", "tomi", "aoi", "yuuni"):
            self.assertIn(f".cera-speaker-{speaker}", styles)

    def test_review_fetch_failure_is_not_mislabeled_as_codex_failure(self) -> None:
        extension = (
            REPOSITORY_ROOT
            / "integrations"
            / "sillytavern"
            / "creator-review-extension"
            / "index.js"
        ).read_text(encoding="utf-8")
        self.assertNotIn("Error - Codex isn't working", extension)
        self.assertIn("Local CERA connection failed", extension)
        self.assertIn("This does not establish that Codex failed", extension)
        self.assertIn("Check CERA status", extension)
        self.assertIn("refreshReviewStatus(messageId, reviewId)", extension)
        self.assertIn("CeraReviewRequestError", extension)

    def test_openai_bridge_queues_only_provisional_cera_metadata(self) -> None:
        openai = (SILLYTAVERN_ROOT / "public" / "scripts" / "openai.js").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "if (data?.cera?.provisional && data.cera.provisional_review_id)",
            openai,
        )
        self.assertIn("window.ceraCompletionMetadataQueue", openai)
        self.assertIn("if (queue.length > 8)", openai)
        self.assertIn("new CustomEvent('cera:completion-metadata'", openai)

    def test_cera_controls_cross_the_server_bridge(self) -> None:
        backend = (
            SILLYTAVERN_ROOT
            / "src"
            / "endpoints"
            / "backends"
            / "chat-completions.js"
        ).read_text(encoding="utf-8")
        for marker in (
            "request.body.model === 'cera-alpha'",
            "requestBody.cera_session_id",
            "requestBody.cera_scene_depth",
            "requestBody.cera_character_autonomy",
            "requestBody.cera_prompt_handling",
            "requestBody.cera_reasoning_effort",
            "requestBody.cera_regeneration_key",
        ):
            self.assertIn(marker, backend)

    def test_provisional_messages_are_excluded_from_exports(self) -> None:
        chats = (SILLYTAVERN_ROOT / "src" / "endpoints" / "chats.js").read_text(
            encoding="utf-8"
        )
        self.assertGreaterEqual(
            chats.count("extra?.cera_creator_review?.provisional"),
            2,
        )


if __name__ == "__main__":
    unittest.main()
