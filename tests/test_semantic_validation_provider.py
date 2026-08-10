from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.continuous.operation_evidence import ProviderOperationEvidenceStoreV1
from cera.semantic_validation import (
    SemanticValidationVerdictV1,
    SemanticVerdict,
)
from cera.semantic_validation.provider import CodexLunaSemanticValidatorBackend
from cera.serialization import canonical_sha256, to_primitive

from .test_semantic_validation_contracts import _request


class _OfflineLunaTransport:
    def __init__(self, _route, *, workspace: Path, runner: object) -> None:
        del workspace, runner

    def invoke(
        self,
        _prompt: str,
        *,
        output_schema: dict[str, object],
        mcp_binding: object,
        on_worker_started,
        on_worker_preflight,
        on_transport_invoke,
    ) -> object:
        del output_schema, mcp_binding
        on_worker_started()
        on_worker_preflight()
        on_transport_invoke()
        verdict = SemanticValidationVerdictV1(
            schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
            verdict=SemanticVerdict.PASS,
            conflict=None,
        )
        payload = {"result": to_primitive(verdict)}
        return SimpleNamespace(
            output_text=json.dumps(payload, sort_keys=True),
            parsed_json=payload,
            receipt={"provider": "offline-luna"},
            operation_telemetry=None,
            tool_call_count=0,
            failed_tool_call_count=0,
            tool_names=(),
            tool_server_names=(),
        )


class SemanticValidationProviderTests(unittest.TestCase):
    def test_luna_binds_candidate_before_operation_evidence_dispatch(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            evidence = ProviderOperationEvidenceStoreV1(
                root / "operation_evidence",
                stage="semantic-validation-test",
            )
            ledger = ContinuousProviderCallLedger(root / "ledger.jsonl")
            backend = CodexLunaSemanticValidatorBackend(
                lifecycle=SimpleNamespace(external_provider_boundary=False),
                workspace=root / "workspace",
                call_ledger=ledger,
                operation_evidence=evidence,
            )
            request = _request()

            with patch(
                "cera.semantic_validation.provider.CodexSDKTransport",
                _OfflineLunaTransport,
            ):
                verdict = backend.run_validator_once(
                    thread_id="thread:luna-offline",
                    request=request,
                )

            self.assertIs(verdict.verdict, SemanticVerdict.PASS)
            self.assertEqual(ledger.dispatched_call_count, 1)
            calls = evidence.snapshot()["calls"]
            self.assertEqual(len(calls), 1)
            pre_dispatch = json.loads(
                (root / "operation_evidence" / calls[0]["call_id"] / "PRE_DISPATCH.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                pre_dispatch["turn"],
                f"semantic-validation:{canonical_sha256(request)}",
            )
            self.assertEqual(pre_dispatch["role"], "validator")
            self.assertEqual(pre_dispatch["attempt"], 1)
            self.assertTrue(calls[0]["terminal"])


if __name__ == "__main__":
    unittest.main()
