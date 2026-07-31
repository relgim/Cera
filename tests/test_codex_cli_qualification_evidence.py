from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from cera.errors import ContractValidationError
from cera.providers import (
    ACTIVE_CODEX_CLI_VERIFIER_SUMMARY_SHA256,
    codex_cli_realization_verifier_candidate,
    load_codex_cli_verifier_qualification,
)
from cera.realization import codex_realization_verifier_draft_json_schema
from cera.providers import (
    ProviderSchemaDialect,
    project_provider_output_schema,
)
from cera.serialization import bytes_sha256


ROOT = Path(__file__).resolve().parents[1]
SUMMARY = (
    ROOT
    / "evaluation"
    / "evidence"
    / "codex_cli_verifier_probe_2026-07-29_v1"
    / "summary.json"
)


class CodexCliQualificationEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.route = codex_cli_realization_verifier_candidate()
        cls.schema_sha256 = project_provider_output_schema(
            codex_realization_verifier_draft_json_schema(),
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema_sha256
        cls.payload = json.loads(SUMMARY.read_text(encoding="utf-8"))
        cls.evidence_route_sha256 = cls.payload["route_sha256"]
        cls.evidence_schema_sha256 = cls.payload["provider_schema_sha256"]

    def _load(self, path: Path, digest: str):
        return load_codex_cli_verifier_qualification(
            path,
            expected_summary_sha256=digest,
            expected_route_sha256=self.evidence_route_sha256,
            expected_provider_schema_sha256=self.evidence_schema_sha256,
        )

    def test_historical_evidence_binds_four_realistic_one_shot_calls(self) -> None:
        evidence = self._load(
            SUMMARY,
            ACTIVE_CODEX_CLI_VERIFIER_SUMMARY_SHA256,
        )
        self.assertEqual(evidence.verifier_calls, 4)
        self.assertEqual(evidence.accepted_cases, 3)
        self.assertEqual(evidence.rejected_cases, 1)
        self.assertEqual(evidence.route_sha256, self.evidence_route_sha256)
        self.assertNotEqual(evidence.route_sha256, self.route.route_sha256)
        self.assertNotEqual(self.evidence_schema_sha256, self.schema_sha256)
        self.assertEqual(evidence.transport_name, "codex_cli_exec")

    def test_hash_mismatch_fails_before_semantic_acceptance(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "hash"):
            self._load(SUMMARY, "0" * 64)

    def test_rehashed_mutations_fail_closed(self) -> None:
        mutations = {
            "status": lambda value: value.update(status="failed"),
            "hidden retry": lambda value: value["calls"][0].update(
                automatic_retry_count=1
            ),
            "transport drift": lambda value: value.update(
                transport_name="codex_python_sdk_app_server"
            ),
            "workspace mutation": lambda value: value["calls"][3].update(
                workspace_empty_after_call=False
            ),
            "dispatch omitted": lambda value: value["provider_stages"][2].update(
                dispatch_started=False
            ),
            "duplicate provider request": lambda value: value["calls"][3][
                "provider_receipt"
            ].update(
                provider_request_id_sha256=value["calls"][0][
                    "provider_receipt"
                ]["provider_request_id_sha256"]
            ),
            "accepted violation": lambda value: value["calls"][0][
                "verification_receipt"
            ].update(
                violation_codes=["required_beat_not_realized"]
            ),
            "rejection unanchored": lambda value: value["calls"][1][
                "verification_receipt"
            ].update(
                violation_finding_sha256s=[]
            ),
            "participant omission": lambda value: value["calls"][3][
                "verification_receipt"
            ].update(
                verified_participant_ids=value["calls"][3][
                    "verification_receipt"
                ]["verified_participant_ids"][:-1]
            ),
            "receipt retention": lambda value: value["calls"][2][
                "provider_receipt"
            ].update(
                retains_story_prose=True
            ),
            "route drift": lambda value: value.update(route_sha256="0" * 64),
        }
        for label, mutate in mutations.items():
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as directory,
            ):
                payload = deepcopy(self.payload)
                mutate(payload)
                raw = (
                    json.dumps(payload, ensure_ascii=False, sort_keys=True)
                    + "\n"
                ).encode("utf-8")
                path = Path(directory) / "summary.json"
                path.write_bytes(raw)
                with self.assertRaises(ContractValidationError):
                    self._load(path, bytes_sha256(raw))


if __name__ == "__main__":
    unittest.main()
