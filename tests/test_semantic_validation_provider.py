from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from cera.continuous.call_ledger import ContinuousProviderCallLedger, ProviderCallState
from cera.continuous.operation_evidence import ProviderOperationEvidenceStoreV1
from cera.errors import ContractValidationError, ErrorCode
from cera.providers.models import (
    ProviderRetryableFailureCategory,
    ProviderTransportError,
)
from cera.semantic_validation import (
    SemanticConflictClass,
    SemanticConflictV1,
    SemanticValidationVerdictV1,
    SemanticVerdict,
)
from cera.semantic_validation.prompting import (
    LUNA_VALIDATOR_BASE_INSTRUCTIONS,
    LUNA_VALIDATOR_PROFILE,
)
from cera.semantic_validation.provider import (
    FULL_MODEL_QUALIFICATION_LUNA_MAXIMUM_OUTPUT_TOKENS,
    FULL_MODEL_QUALIFICATION_LUNA_ROUTE_ID,
    LUNA_VALIDATOR_ADAPTER,
    LUNA_VALIDATOR_PROMPT,
    CodexLunaSemanticValidatorBackend,
    full_model_qualification_luna_validator_route,
    luna_validator_route,
)
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


class _InvalidCompletedLunaTransport(_OfflineLunaTransport):
    def invoke(self, *args, **kwargs) -> object:
        result = super().invoke(*args, **kwargs)
        payload = {
            "result": {
                "schema_version": "cera.semantic_validation.verdict.invalid",
                "verdict": "pass",
                "conflict": None,
            }
        }
        result.output_text = json.dumps(payload, sort_keys=True)
        result.parsed_json = payload
        result.receipt = {"provider": "offline-luna-invalid"}
        return result


class _RequestBoundInvalidLunaTransport(_OfflineLunaTransport):
    def invoke(self, *args, **kwargs) -> object:
        result = super().invoke(*args, **kwargs)
        verdict = SemanticValidationVerdictV1(
            schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
            verdict=SemanticVerdict.REJECT,
            conflict=SemanticConflictV1(
                conflict_class=SemanticConflictClass.STOPPING_BOUNDARY,
                concise_explanation="The candidate crosses its required stopping boundary.",
                exact_quote="This quote is absent from the exact candidate prose.",
                decision_key="sakura_door_response",
            ),
        )
        payload = {"result": to_primitive(verdict)}
        result.output_text = json.dumps(payload, sort_keys=True)
        result.parsed_json = payload
        result.receipt = {"provider": "offline-luna-bound-invalid"}
        return result


class SemanticValidationProviderTests(unittest.TestCase):
    def test_luna_route_binds_the_hardened_runtime_identity(self) -> None:
        route = luna_validator_route()
        self.assertEqual(
            LUNA_VALIDATOR_ADAPTER,
            "cera.semantic_validation.luna_adapter.v6",
        )
        self.assertEqual(LUNA_VALIDATOR_PROMPT, "cera.semantic_validation.luna_prompt.v7")
        self.assertEqual(LUNA_VALIDATOR_PROFILE, "cera.semantic_validator.luna_xhigh.v7")
        self.assertEqual(route.route_id, "cera_semantic_validator_luna_xhigh_v10")
        self.assertEqual(route.adapter_id, LUNA_VALIDATOR_ADAPTER)
        self.assertEqual(route.prompt_version, LUNA_VALIDATOR_PROMPT)
        self.assertEqual(route.timeout_seconds, 180)
        self.assertEqual(route.maximum_output_tokens, 4_096)
        self.assertIn("verbatim contiguous substring", LUNA_VALIDATOR_BASE_INSTRUCTIONS)
        self.assertIn("never paraphrase", LUNA_VALIDATOR_BASE_INSTRUCTIONS)
        self.assertIn("add ellipses", LUNA_VALIDATOR_BASE_INSTRUCTIONS)
        precedence = (
            "protected_user_dialogue",
            "protected_user_private_state",
            "knowledge_violation",
            "unauthorized_consequence",
            "authority_ambiguity",
            "severe_incompleteness",
            "locked_fact_conflict",
            "contradicted_decision",
            "omitted_decision",
            "presence_violation",
            "stopping_boundary",
            "capability_restriction",
        )
        hard_order = LUNA_VALIDATOR_BASE_INSTRUCTIONS.split("hard-first order:", 1)[1]
        offsets = tuple(hard_order.index(value) for value in precedence)
        self.assertEqual(offsets, tuple(sorted(offsets)))
        self.assertIn(
            "Never let a soft conflict hide a coexisting hard conflict",
            LUNA_VALIDATOR_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "Ted has exactly two ordinary hard protections", LUNA_VALIDATOR_BASE_INSTRUCTIONS
        )
        self.assertIn(
            "only when the candidate expressly attributes the speech or private state to Ted",
            LUNA_VALIDATOR_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "Public biography, employment, duty assignment, stationing, role, affiliation",
            LUNA_VALIDATOR_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "Never classify such a public detail as protected_user_private_state",
            LUNA_VALIDATOR_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "visibly present, independently participating, retains his own choice",
            LUNA_VALIDATOR_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "only when the prose invents an unobservable reason, motive, thought",
            LUNA_VALIDATOR_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "soft presence_violation or capability_restriction",
            LUNA_VALIDATOR_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "Never infer that an anonymous or differently named claimant",
            LUNA_VALIDATOR_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "is not protected_user_dialogue or protected_user_private_state",
            LUNA_VALIDATOR_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "established identity, adult age, parent-child relationship",
            LUNA_VALIDATOR_BASE_INSTRUCTIONS,
        )
        self.assertIn(
            "may remember an accepted event, be uncertain, or fail to recall it",
            LUNA_VALIDATOR_BASE_INSTRUCTIONS,
        )
        self.assertIn("An extra Sakura line", LUNA_VALIDATOR_BASE_INSTRUCTIONS)
        self.assertIn("never unauthorized_consequence", LUNA_VALIDATOR_BASE_INSTRUCTIONS)
        self.assertIn(
            "only when the output is unusable, off-topic, incoherent",
            LUNA_VALIDATOR_BASE_INSTRUCTIONS,
        )

    def test_full_model_qualification_route_changes_only_identity_and_output_budget(self) -> None:
        production_route = luna_validator_route()
        qualification_route = full_model_qualification_luna_validator_route()

        self.assertEqual(FULL_MODEL_QUALIFICATION_LUNA_MAXIMUM_OUTPUT_TOKENS, 128_000)
        self.assertEqual(
            qualification_route.route_id,
            "cera_full_model_qualification_semantic_validator_luna_xhigh_v8",
        )
        self.assertEqual(
            qualification_route,
            replace(
                production_route,
                route_id=FULL_MODEL_QUALIFICATION_LUNA_ROUTE_ID,
                maximum_output_tokens=128_000,
            ),
        )
        self.assertNotEqual(qualification_route.route_sha256, production_route.route_sha256)

    def test_luna_backend_accepts_only_the_two_exact_route_identities(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            ledger = ContinuousProviderCallLedger(root / "ledger.jsonl")
            qualification_route = full_model_qualification_luna_validator_route()
            backend = CodexLunaSemanticValidatorBackend(
                lifecycle=SimpleNamespace(external_provider_boundary=False),
                workspace=root / "workspace",
                call_ledger=ledger,
                route=qualification_route,
            )
            self.assertEqual(backend.route, qualification_route)

            with self.assertRaisesRegex(
                ContractValidationError,
                "not an approved exact identity",
            ):
                CodexLunaSemanticValidatorBackend(
                    lifecycle=SimpleNamespace(external_provider_boundary=False),
                    workspace=root / "other-workspace",
                    call_ledger=ledger,
                    route=replace(qualification_route, maximum_output_tokens=128_001),
                )

    def test_completed_invalid_verdict_is_typed_retryable_provider_output(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            ledger = ContinuousProviderCallLedger(root / "ledger.jsonl")
            backend = CodexLunaSemanticValidatorBackend(
                lifecycle=SimpleNamespace(external_provider_boundary=False),
                workspace=root / "workspace",
                call_ledger=ledger,
            )
            with (
                patch(
                    "cera.semantic_validation.provider.CodexSDKTransport",
                    _InvalidCompletedLunaTransport,
                ),
                self.assertRaises(ProviderTransportError) as caught,
            ):
                request = _request()
                backend.run_validator_once(
                    thread_id="thread:luna-invalid",
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
                {"provider": "offline-luna-invalid"},
            )
            self.assertEqual(
                failure.safe_diagnostics,
                ("provider_output:luna_verdict_contract_invalid",),
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

    def test_request_bound_invalid_verdict_is_typed_retryable_provider_output(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            ledger = ContinuousProviderCallLedger(root / "ledger.jsonl")
            backend = CodexLunaSemanticValidatorBackend(
                lifecycle=SimpleNamespace(external_provider_boundary=False),
                workspace=root / "workspace",
                call_ledger=ledger,
            )
            request = _request()
            with (
                patch(
                    "cera.semantic_validation.provider.CodexSDKTransport",
                    _RequestBoundInvalidLunaTransport,
                ),
                self.assertRaises(ProviderTransportError) as caught,
            ):
                backend.run_validator_once(
                    thread_id="thread:luna-bound-invalid",
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
                {"provider": "offline-luna-bound-invalid"},
            )
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
