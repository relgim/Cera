from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_SCRIPT = PROJECT_ROOT / "tools" / "pro_review_bridge.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")
CHECKPOINT_ID = "2026-07-31-checkpoint-001"
CHECKPOINT_SHA = "248dfbc969a2961338d8f9b35c61bda4f4e6010b"
BRIDGE_SHA = "0982dabb6e548177d058b81679b8cbf1d6dd7192"


@unittest.skipUnless(POWERSHELL, "Windows PowerShell is required")
class ProReviewBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.checkpoint = self.root / CHECKPOINT_ID
        self.downloads = self.root / "Downloads"
        self.index = self.checkpoint / "EVIDENCE_PACKAGE" / "INDEX.md"
        self.zip_path = self.checkpoint / "CERA_CHECKPOINT_001_EVIDENCE.zip"
        self.first_response = self.checkpoint / "PRO_RESPONSE.md"
        self.destination = self.checkpoint / "PRO_RESPONSE_EVIDENCE_VERIFIED.md"
        self.checkpoint.mkdir(parents=True)
        self.downloads.mkdir()
        self.index.parent.mkdir()
        (self.checkpoint / "REQUEST.md").write_text(
            "# Review request\n\n"
            f"ending_checkpoint_sha: `{CHECKPOINT_SHA}`\n",
            encoding="utf-8",
        )
        self.index.write_text("# Evidence index\n", encoding="utf-8")
        (self.checkpoint / "TASK4_RESULT.md").write_text(
            f"bridge_task_sha: `{BRIDGE_SHA}`\n", encoding="utf-8"
        )
        with zipfile.ZipFile(self.zip_path, "w") as archive:
            archive.writestr("INDEX.md", "checkpoint evidence\n")
        self.zip_sha = hashlib.sha256(self.zip_path.read_bytes()).hexdigest()
        (self.checkpoint / f"{self.zip_path.name}.sha256").write_text(
            f"{self.zip_sha}  {self.zip_path.name}\n", encoding="utf-8"
        )
        self.first_response_bytes = b"# Preserved evidence-blocked response\n"
        self.first_response.write_bytes(self.first_response_bytes)
        self.expected_response_name = (
            f"CERA_PRO_RESPONSE_{CHECKPOINT_ID}_{CHECKPOINT_SHA[:12]}.md"
        )
        self.expected_response = self.downloads / self.expected_response_name

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_bridge(
        self,
        mode: str,
        *,
        check: bool = True,
        max_polls: int = 1,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            str(POWERSHELL),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(BRIDGE_SCRIPT),
            "-Mode",
            mode,
            "-CheckpointDirectory",
            str(self.checkpoint),
            "-DownloadsDirectory",
            str(self.downloads),
            "-PollSeconds",
            "1",
            "-MaxPolls",
            str(max_polls),
            "-StabilityDelayMilliseconds",
            "0",
        ]
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        if check and result.returncode != 0:
            self.fail(
                f"bridge {mode} failed ({result.returncode})\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def export(self) -> dict[str, object]:
        self.run_bridge("Export")
        return json.loads(
            (self.checkpoint / "BRIDGE_EXPORT_RECEIPT.json").read_text(
                encoding="utf-8-sig"
            )
        )

    def response_bytes(
        self,
        *,
        checkpoint_sha: str = CHECKPOINT_SHA,
        zip_sha: str | None = None,
        scope: str = "evidence_verified",
        disposition: str = "accepted",
        body: str = (
            "The actual source, tests, hashes, route metadata, and effects were "
            "inspected. The review remains advisory and does not authorize any "
            "implementation, provider call, publication, or route change."
        ),
    ) -> bytes:
        selected_zip_sha = zip_sha or self.zip_sha
        return (
            "# CERA Evidence-Verified Review\n\n"
            f"reviewed_checkpoint_id: {CHECKPOINT_ID}\n"
            f"reviewed_checkpoint_sha: {checkpoint_sha}\n"
            f"reviewed_evidence_zip_sha256: {selected_zip_sha}\n"
            f"review_scope: {scope}\n"
            f"review_disposition: {disposition}\n\n"
            "## Findings\n\n"
            f"{body}\n"
        ).encode("utf-8")

    def write_response(self, **overrides: str) -> bytes:
        content = self.response_bytes(**overrides)
        self.expected_response.write_bytes(content)
        return content

    def test_01_export_uses_exact_checkpoint_identity(self) -> None:
        receipt = self.export()
        self.assertEqual(receipt["checkpoint_id"], CHECKPOINT_ID)
        self.assertEqual(receipt["checkpoint_sha"], CHECKPOINT_SHA)
        self.assertEqual(receipt["evidence_bridge_sha"], BRIDGE_SHA)
        self.assertEqual(receipt["evidence_zip_sha256"], self.zip_sha)
        self.assertEqual(
            receipt["expected_downloaded_response_filename"],
            self.expected_response_name,
        )
        message = Path(str(receipt["upload_message_path"])).read_text(
            encoding="utf-8"
        )
        self.assertIn(self.expected_response_name, message)

    def test_02_export_rejects_wrong_evidence_hash(self) -> None:
        (self.checkpoint / f"{self.zip_path.name}.sha256").write_text(
            f"{'0' * 64}  {self.zip_path.name}\n", encoding="utf-8"
        )
        result = self.run_bridge("Export", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hash mismatch", result.stderr.casefold())
        self.assertFalse((self.checkpoint / "BRIDGE_EXPORT_RECEIPT.json").exists())

    def test_03_export_filename_is_deterministic_and_idempotent(self) -> None:
        first = self.export()
        first_bytes = Path(str(first["exported_zip_path"])).read_bytes()
        second = self.export()
        self.assertEqual(first["exported_zip_path"], second["exported_zip_path"])
        self.assertEqual(first["upload_message_path"], second["upload_message_path"])
        self.assertEqual(first_bytes, Path(str(second["exported_zip_path"])).read_bytes())

    def test_04_export_does_not_mutate_checkpoint_zip(self) -> None:
        before = self.zip_path.read_bytes()
        self.export()
        self.assertEqual(self.zip_path.read_bytes(), before)
        self.assertEqual(hashlib.sha256(before).hexdigest(), self.zip_sha)

    def test_05_wait_watches_only_expected_response_path(self) -> None:
        self.export()
        (self.downloads / "CERA_PRO_RESPONSE_decoy.md").write_bytes(
            self.response_bytes()
        )
        result = self.run_bridge("Wait")
        self.assertIn("CERA_WAITING_FOR_PRO_RESPONSE", result.stdout)
        self.assertFalse(self.destination.exists())
        self.assertFalse((self.checkpoint / "BRIDGE_IMPORT_RECEIPT.json").exists())

    def test_06_wait_has_no_provider_database_or_source_side_effect(self) -> None:
        self.export()
        script_before = hashlib.sha256(BRIDGE_SCRIPT.read_bytes()).hexdigest()
        result = self.run_bridge("Wait")
        self.assertIn("CERA_WAITING_FOR_PRO_RESPONSE", result.stdout)
        waiting = (self.checkpoint / "WAITING_FOR_PRO_RESPONSE.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("provider_calls_while_waiting: 0", waiting)
        self.assertIn("database_or_story_writes_while_waiting: 0", waiting)
        self.assertIn("repository_source_changes_while_waiting: 0", waiting)
        self.assertEqual(
            hashlib.sha256(BRIDGE_SCRIPT.read_bytes()).hexdigest(), script_before
        )
        script = BRIDGE_SCRIPT.read_text(encoding="utf-8").casefold()
        for forbidden_command in (
            "invoke-webrequest",
            "invoke-restmethod",
            "start-process",
            "system.data.sqlite",
        ):
            self.assertNotIn(forbidden_command, script)

    def test_07_import_rejects_wrong_checkpoint_sha(self) -> None:
        self.export()
        self.write_response(checkpoint_sha="1" * 40)
        result = self.run_bridge("Import", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("identity does not match", result.stderr.casefold())
        self.assertFalse(self.destination.exists())

    def test_08_import_rejects_wrong_evidence_zip_hash(self) -> None:
        self.export()
        self.write_response(zip_sha="2" * 64)
        result = self.run_bridge("Import", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("identity does not match", result.stderr.casefold())

    def test_09_import_rejects_incomplete_or_placeholder_response(self) -> None:
        self.export()
        self.expected_response.write_text(
            "# Pending\n"
            f"reviewed_checkpoint_id: {CHECKPOINT_ID}\n"
            f"reviewed_checkpoint_sha: {CHECKPOINT_SHA}\n"
            f"reviewed_evidence_zip_sha256: {self.zip_sha}\n"
            "review_scope: evidence_verified\n"
            "review_disposition: pending\n",
            encoding="utf-8",
        )
        result = self.run_bridge("Import", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.destination.exists())

    def test_10_import_preserves_response_bytes_exactly(self) -> None:
        self.export()
        supplied = self.write_response(
            body=(
                "Evidence is complete. Unicode preservation sample: Sakura — "
                "reviewed. This remains advisory and creator authorization is required."
            )
        )
        result = self.run_bridge("Import")
        self.assertIn("CERA_PRO_RESPONSE_IMPORTED", result.stdout)
        self.assertEqual(self.destination.read_bytes(), supplied)

    def test_11_import_never_overwrites_first_response(self) -> None:
        self.export()
        self.write_response()
        self.run_bridge("Import")
        self.assertEqual(self.first_response.read_bytes(), self.first_response_bytes)

    def test_12_import_is_idempotent_for_identical_response(self) -> None:
        self.export()
        supplied = self.write_response()
        first = self.run_bridge("Import")
        second = self.run_bridge("Import")
        self.assertIn("CERA_PRO_RESPONSE_IMPORTED", first.stdout)
        self.assertIn("CERA_PRO_RESPONSE_IMPORTED", second.stdout)
        self.assertEqual(self.destination.read_bytes(), supplied)

    def test_13_import_rejects_conflicting_existing_response(self) -> None:
        self.export()
        self.write_response()
        conflicting = b"# Different preserved review\n"
        self.destination.write_bytes(conflicting)
        result = self.run_bridge("Import", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicting", result.stderr.casefold())
        self.assertEqual(self.destination.read_bytes(), conflicting)

    def test_14_import_creates_privacy_safe_receipt(self) -> None:
        self.export()
        supplied = self.write_response()
        self.run_bridge("Import")
        receipt = json.loads(
            (self.checkpoint / "BRIDGE_IMPORT_RECEIPT.json").read_text(
                encoding="utf-8-sig"
            )
        )
        self.assertEqual(receipt["checkpoint_id"], CHECKPOINT_ID)
        self.assertEqual(receipt["checkpoint_sha"], CHECKPOINT_SHA)
        self.assertEqual(receipt["evidence_zip_sha256"], self.zip_sha)
        self.assertEqual(receipt["response_sha256"], hashlib.sha256(supplied).hexdigest())
        self.assertEqual(receipt["identity_validation"], "passed")
        self.assertNotIn("response_text", receipt)
        self.assertNotIn("recommendations", receipt)

    def test_15_import_does_not_execute_recommendations(self) -> None:
        self.export()
        marker = self.root / "SHOULD_NOT_EXIST.txt"
        self.write_response(
            body=f"Recommendation only: create {marker}. Do not execute it automatically."
        )
        self.run_bridge("Import")
        self.assertFalse(marker.exists())

    def test_16_status_reports_transport_without_claiming_approval(self) -> None:
        self.export()
        self.write_response()
        self.run_bridge("Import")
        status = self.run_bridge("Status").stdout
        self.assertIn(f"checkpoint ID: {CHECKPOINT_ID}", status)
        self.assertIn(f"checkpoint SHA: {CHECKPOINT_SHA}", status)
        self.assertIn(f"evidence ZIP hash: {self.zip_sha}", status)
        self.assertIn("export status: exported", status)
        self.assertIn("waiting status: not_waiting", status)
        self.assertIn("response detected: true", status)
        self.assertIn("response imported: true", status)
        self.assertIn("creator authorization status: required", status)
        self.assertNotIn("authorized", status.casefold())
        self.assertNotIn("approved", status.casefold())

    def test_17_wait_imports_exact_response_once_when_detected(self) -> None:
        self.export()
        supplied = self.write_response()
        result = self.run_bridge("Wait")
        self.assertIn("CERA_PRO_RESPONSE_IMPORTED", result.stdout)
        self.assertEqual(self.destination.read_bytes(), supplied)
        self.assertTrue((self.checkpoint / "BRIDGE_IMPORT_RECEIPT.json").is_file())


if __name__ == "__main__":
    unittest.main()
