from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

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
        for name in (
            "index.js",
            "completion-metadata.js",
            "creator-trace-panel.js",
            "review-actions.js",
            "style.css",
            "manifest.json",
        ):
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

    def test_full_model_completion_metadata_source_contract_is_closed_and_trace_aware(
        self,
    ) -> None:
        source_root = REPOSITORY_ROOT / "integrations" / "sillytavern"
        extension = (
            source_root / "creator-review-extension" / "index.js"
        ).read_text(encoding="utf-8")
        metadata = (
            source_root / "creator-review-extension" / "completion-metadata.js"
        ).read_text(encoding="utf-8")
        panel = (
            source_root / "creator-review-extension" / "creator-trace-panel.js"
        ).read_text(encoding="utf-8")
        styles = (
            source_root / "creator-review-extension" / "style.css"
        ).read_text(encoding="utf-8")
        bridge = (
            source_root / "CERA_FULL_MODEL_COMPLETION_METADATA_BRIDGE.md"
        ).read_text(encoding="utf-8")

        for marker in (
            "window.ceraCaptureCompletionMetadata",
            "window.ceraCaptureTransportFailure",
            "normalizeCompletionMetadata",
            "normalizeCreatorTrace",
            "validReviewId",
            "renderStoredCompletionMetadata",
        ):
            self.assertIn(marker, extension)
        for marker in (
            "CERA decision and processing details",
            "Relevant decisions",
            "Autonomy application",
            "Route transition",
            "Validation",
            "Provider operations",
            "Readable debug log",
        ):
            self.assertIn(marker, panel)
        self.assertIn("/^review-[a-f0-9]{28}$/", metadata)
        self.assertIn("so no action was fabricated", panel)
        self.assertNotIn("exact_story_prose:", metadata)
        self.assertIn(".cera-trace-details", styles)
        self.assertIn("data?.cera", bridge)
        self.assertIn("window.ceraCaptureCompletionMetadata(data.cera)", bridge)
        self.assertIn("must not clone, log, reshape, or persist", bridge)
        self.assertIn("Retry transport", bridge)
        self.assertIn("cera.pi_scene.transport_retry.v1", bridge)

    def test_full_model_metadata_panel_node_contract(self) -> None:
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for the CERA metadata panel")
        test_path = (
            REPOSITORY_ROOT
            / "integrations"
            / "sillytavern"
            / "creator-review-extension"
            / "metadata-panel.test.mjs"
        )
        result = subprocess.run(
            [str(node), "--test", str(test_path)],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_creator_review_extension_node_syntax(self) -> None:
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for the CERA extension")
        extension_root = (
            REPOSITORY_ROOT
            / "integrations"
            / "sillytavern"
            / "creator-review-extension"
        )
        for name in (
            "index.js",
            "completion-metadata.js",
            "creator-trace-panel.js",
            "review-actions.js",
        ):
            result = subprocess.run(
                [str(node), "--check", str(extension_root / name)],
                cwd=REPOSITORY_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_creator_review_presentation_contract_is_explicit(self) -> None:
        source_root = (
            REPOSITORY_ROOT
            / "integrations"
            / "sillytavern"
            / "creator-review-extension"
        )
        extension = (source_root / "index.js").read_text(encoding="utf-8")
        styles = (source_root / "style.css").read_text(encoding="utf-8")
        self.assertIn("renderPiSceneReview", extension)
        self.assertIn("CODEX SEQUENCE REALIZATION READY FOR CREATOR REVIEW", extension)
        self.assertIn("'Regenerate'", extension)
        self.assertIn("'Replan'", extension)
        self.assertIn("'Accept as Provisional'", extension)
        self.assertIn("provisionalAcceptEnabled(review)", extension)
        self.assertIn("No story state was accepted or committed.", extension)
        self.assertIn("if (!feedback && action !== 'replan')", extension)
        self.assertIn("CERA - PROVISIONAL CANON", (
            source_root / "creator-trace-panel.js"
        ).read_text(encoding="utf-8"))
        self.assertIn("'Repair Recording'", extension)
        self.assertIn("'Decline'", extension)
        self.assertIn("'declined'", extension)
        self.assertIn("reconcileDecisionAfterError", extension)
        self.assertNotIn("Sol reviewing...", extension)
        self.assertIn("controlSelect('Sol'", extension)
        self.assertIn("['medium', 'M'], ['high', 'H'], ['xhigh', 'Ex']", extension)
        self.assertIn("controlSelect('Adult'", extension)
        self.assertIn("['off', 'Off'], ['on', 'On'], ['ex', 'Ex']", extension)
        self.assertIn("adult_craft_mode", extension)
        self.assertIn("renderStoredSpeakerMarks", extension)
        self.assertIn("vera_cast_readability", extension)
        for severity in ("good", "concern", "critical", "error"):
            self.assertIn(f".cera-review-severity-{severity}", styles)
        for speaker in ("hana", "sakura", "mia", "enne", "tomi", "aoi", "yuuni"):
            self.assertIn(f".cera-speaker-{speaker}", styles)

    def test_scene_change_control_is_shadow_only_and_one_shot(self) -> None:
        shadow = (
            REPOSITORY_ROOT
            / "integrations"
            / "sillytavern"
            / "continuous-shadow"
            / "scene-change-control.js"
        ).read_text(encoding="utf-8")
        self.assertIn("Scene Change", shadow)
        self.assertIn("cera_scene_change", shadow)
        self.assertIn("checkbox.checked = false", shadow)
        active_manifest = (
            REPOSITORY_ROOT
            / "integrations"
            / "sillytavern"
            / "creator-review-extension"
            / "manifest.json"
        ).read_text(encoding="utf-8")
        self.assertNotIn("continuous-shadow", active_manifest)

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

    def test_pi_scene_review_identity_and_local_credential_cross_the_relay(self) -> None:
        extension = (
            REPOSITORY_ROOT
            / "integrations"
            / "sillytavern"
            / "creator-review-extension"
            / "index.js"
        ).read_text(encoding="utf-8")
        proxy = (
            REPOSITORY_ROOT
            / "integrations"
            / "sillytavern"
            / "cera-review-proxy-plugin"
            / "index.js"
        ).read_text(encoding="utf-8")
        self.assertIn("import { oai_settings }", extension)
        self.assertIn("oai_settings.custom_include_headers", extension)
        self.assertIn("headers['X-Cera-Authorization'] = ceraAuthorizationHeader()", extension)
        self.assertIn("review-[a-f0-9]{28}", proxy)
        self.assertIn("const normalizedAuthorization = normalizeAuthorization(authorization)", proxy)
        self.assertIn("Authorization: normalizedAuthorization", proxy)
        self.assertIn("authorization: request.get('X-Cera-Authorization')", proxy)
        self.assertIn("'accept_provisional'", proxy)
        self.assertIn("/v1/cera/transport-retries/:retryId", proxy)
        self.assertIn("normalizeTransportRetryBody", proxy)

    def test_openai_bridge_routes_all_cera_metadata_through_closed_projection(self) -> None:
        openai = (SILLYTAVERN_ROOT / "public" / "scripts" / "openai.js").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "if (data?.cera && typeof window.ceraCaptureCompletionMetadata === 'function')",
            openai,
        )
        self.assertIn("window.ceraCaptureCompletionMetadata(data.cera)", openai)
        self.assertEqual(openai.count("window.ceraCaptureTransportFailure(data)"), 1)
        self.assertNotIn(
            "if (data?.cera?.provisional && data.cera.provisional_review_id)",
            openai,
        )

    def test_cera_controls_cross_the_server_bridge(self) -> None:
        backend = (
            SILLYTAVERN_ROOT
            / "src"
            / "endpoints"
            / "backends"
            / "chat-completions.js"
        ).read_text(encoding="utf-8")
        for marker in (
            "['cera-alpha', 'cera-pi-scene-ordinary', 'cera-pi-scene-adult']",
            "requestBody.cera_profile_id",
            "requestBody.cera_session_id",
            "requestBody.cera_scene_depth",
            "requestBody.cera_character_autonomy",
            "requestBody.cera_prompt_handling",
            "requestBody.cera_reasoning_effort",
            "requestBody.cera_adult_craft_mode",
            "requestBody.cera_regeneration_key",
        ):
            self.assertIn(marker, backend)
        self.assertIn(
            "Retrieval breadth only; it never selects ordinary/adult routing.",
            backend,
        )

    def test_cera_controls_cross_the_client_bridge(self) -> None:
        openai = (SILLYTAVERN_ROOT / "public" / "scripts" / "openai.js").read_text(
            encoding="utf-8"
        )
        for marker in (
            "['cera-alpha', 'cera-pi-scene-ordinary', 'cera-pi-scene-adult']",
            "'cera_profile_id': isCeraCustomModel ? 'cera.pi_scene.lean.v1'",
            "'cera_session_id': isCeraCustomModel",
            "'cera_scene_depth': isCeraCustomModel",
            "'cera_character_autonomy': isCeraCustomModel",
            "'cera_prompt_handling': isCeraCustomModel",
            "'cera_reasoning_effort': isCeraCustomModel",
            "'cera_adult_craft_mode': isCeraCustomModel",
            "'cera_regeneration_key': ceraRegenerationKey",
        ):
            self.assertIn(marker, openai)
        self.assertIn(
            "Craft breadth is route-neutral. Python remains the sole route owner.",
            openai,
        )

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
