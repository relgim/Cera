from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from cera.continuous.call_ledger import (
    ContinuousProviderCallLedger,
    ProviderCallState,
)
from cera.errors import ErrorCode
from cera.providers.models import (
    ProviderRetryableFailureCategory,
    ProviderTransportError,
)
from cera.reader_validation import build_reader_validation_input
from cera.reader_validation.prompting import SOL_READER_PROFILE
from cera.reader_validation.provider import (
    SOL_READER_ADAPTER,
    SOL_READER_PROMPT,
    CodexSolReaderBackend,
    sol_reader_route,
)

from .test_pi_scene_reader_validation import _qualified_candidate, _turn_input


class _InvalidCompletedReaderTransport:
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
        payload = {
            "verdict": {
                "status": "accepted",
                "issues": [],
                "unexpected": "closed DTO violation",
            }
        }
        return SimpleNamespace(
            output_text=json.dumps(payload, sort_keys=True),
            parsed_json=payload,
            receipt={"provider": "offline-reader-invalid"},
            operation_telemetry=None,
            tool_call_count=0,
            failed_tool_call_count=0,
            tool_names=(),
            tool_server_names=(),
        )


class ReaderValidationProviderTests(unittest.TestCase):
    def test_reader_route_binds_the_hardened_runtime_identity(self) -> None:
        route = sol_reader_route()
        self.assertEqual(
            SOL_READER_ADAPTER,
            "cera.reader_validation.sol_adapter.v2",
        )
        self.assertEqual(SOL_READER_PROMPT, "cera.reader_validation.sol_prompt.v1")
        self.assertEqual(SOL_READER_PROFILE, "cera.reader_validation.sol_medium.v1")
        self.assertEqual(route.route_id, "cera_reader_validation_sol_medium_v2")
        self.assertEqual(route.adapter_id, SOL_READER_ADAPTER)
        self.assertEqual(route.prompt_version, SOL_READER_PROMPT)

    def test_completed_invalid_verdict_is_typed_retryable_provider_output(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            ledger = ContinuousProviderCallLedger(root / "ledger.jsonl")
            backend = CodexSolReaderBackend(
                lifecycle=SimpleNamespace(external_provider_boundary=False),
                workspace=root / "workspace",
                call_ledger=ledger,
            )
            request, _ = build_reader_validation_input(
                candidate=_qualified_candidate(),
                turn_input=_turn_input(),
            )
            with (
                patch(
                    "cera.reader_validation.provider.CodexSDKTransport",
                    _InvalidCompletedReaderTransport,
                ),
                self.assertRaises(ProviderTransportError) as caught,
            ):
                backend.run_reader_once(
                    thread_id="thread:reader-invalid",
                    request=request,
                )

            failure = caught.exception
            self.assertEqual(failure.code, ErrorCode.REASONER_CONTRACT_INVALID)
            self.assertEqual(
                failure.retryable_failure_category,
                ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
            )
            self.assertEqual(failure.external_provider_calls_observed, 1)
            self.assertEqual(
                failure.provider_call_receipt,
                {"provider": "offline-reader-invalid"},
            )
            self.assertEqual(
                failure.safe_diagnostics,
                ("provider_output:reader_verdict_contract_invalid",),
            )
            self.assertIsNone(failure.__cause__)
            self.assertEqual(ledger.dispatched_call_count, 1)
            self.assertEqual(
                tuple(value["state"] for value in ledger.events[-2:]),
                (
                    ProviderCallState.PROVIDER_COMPLETED.value,
                    ProviderCallState.POST_VALIDATION_FAILED.value,
                ),
            )
            self.assertFalse(
                any(value["state"] == ProviderCallState.ACCEPTED.value for value in ledger.events)
            )


if __name__ == "__main__":
    unittest.main()
