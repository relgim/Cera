from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from cera.continuous.call_ledger import ContinuousProviderCallLedger, ProviderCallState
from cera.continuous.operation_evidence import ProviderOperationEvidenceStoreV1
from cera.continuous.provider import DeepSeekContinuousComposerPort
from cera.errors import StateConflictError
from cera.sequence_first.provider import sequence_first_writer_route


class ProviderOperationEvidenceTests(unittest.TestCase):
    class DeepSeekTransportFake:
        external_provider_boundary = False

        def __init__(self) -> None:
            self.route = sequence_first_writer_route()

        def invoke(self, messages, *, output_mode, thinking_enabled, on_transport_invoke):
            del messages, output_mode
            assert thinking_enabled is False
            on_transport_invoke()
            return SimpleNamespace(
                output_text='{"schema_version":"cera.scene_writer_draft.v1","story_text":"Hana answers."}',
                parsed_json={
                    "schema_version": "cera.scene_writer_draft.v1",
                    "story_text": "Hana answers.",
                },
                receipt={"finish_reason": "stop", "output_tokens": 12},
                operation_telemetry=None,
                tool_call_count=0,
                failed_tool_call_count=0,
                tool_names=(),
                tool_server_names=(),
            )

    def test_sequence_first_deepseek_wire_captures_exact_stateless_call(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            evidence = ProviderOperationEvidenceStoreV1(root / "calls", stage="writer-fake")
            evidence.begin_turn("turn-writer-0001")
            ledger = ContinuousProviderCallLedger(root / "ledger.jsonl")
            composer = DeepSeekContinuousComposerPort(
                self.DeepSeekTransportFake(),
                call_ledger=ledger,
            )
            result = composer.compose(
                "Frozen Writer brief.",
                operation_evidence=evidence,
                operation_evidence_attempt=1,
                operation_evidence_prompt_version="cera.sequence_first.writer_prompt.v1",
                operation_evidence_schema_version="cera.scene_writer_draft.v1",
            )
            self.assertEqual(result.value.story_text, "Hana answers.")
            call = evidence.snapshot()["calls"][0]
            call_root = root / "calls" / call["call_id"]
            predispatch = json.loads((call_root / "PRE_DISPATCH.json").read_text())
            self.assertEqual(predispatch["role"], "writer")
            self.assertEqual(predispatch["attempt"], 1)
            self.assertEqual(predispatch["operation_workspace"], "not_applicable:stateless_https")
            self.assertIn(b"Frozen Writer brief.", (call_root / "REQUEST.bin").read_bytes())
            self.assertEqual(ledger.dispatched_call_count, 1)

    def test_atomic_call_evidence_survives_later_post_dispatch_failure(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            store = ProviderOperationEvidenceStoreV1(root / "calls", stage="fake-stage")
            ledger = ContinuousProviderCallLedger(root / "ledger.jsonl")
            store.begin_turn("turn-0001")
            request_one = store.request(
                request_bytes=b"exact first request\n",
                structured_output_schema={"type": "object"},
                prompt_version="prompt.v1",
                schema_version="schema.v1",
                operation_workspace=str(root / "workspace" / "planner-0001"),
                role="planner",
                archival_policy="persistent_per_accepted_branch",
            )
            first_raw = SimpleNamespace(
                output_text='{"answer":"first"}',
                parsed_json={"answer": "first"},
                receipt={"input_tokens": 10, "cached_input_tokens": 6},
                operation_telemetry={"latency_ms": 25},
                tool_call_count=1,
                failed_tool_call_count=0,
                tool_names=("fetch",),
                tool_server_names=("world",),
            )

            def first_dispatch(mark_invoked):
                mark_invoked()
                return first_raw

            first = ledger.execute(
                owner="planner",
                operation="fake_plan_0001",
                route="route.v1",
                model="fake-model",
                effort="medium",
                dispatch_with_invocation_marker=first_dispatch,
                finalize=lambda raw: SimpleNamespace(value=raw.parsed_json),
                stored_thread_sha256="a" * 64,
                operation_evidence=request_one,
            )
            self.assertEqual(first.value, {"answer": "first"})
            first_snapshot = store.snapshot()["calls"][0]
            first_directory_hash = first_snapshot["directory_sha256"]
            first_root = root / "calls" / first_snapshot["call_id"]
            self.assertEqual((first_root / "REQUEST.bin").read_bytes(), b"exact first request\n")
            self.assertEqual((first_root / "RAW_PROVIDER_RESULT.bin").read_bytes(), b'{"answer":"first"}')
            terminal = json.loads((first_root / "TERMINAL.json").read_text())
            self.assertEqual(terminal["ledger_event"]["state"], ProviderCallState.ACCEPTED.value)
            self.assertEqual(terminal["token_cache_latency_tool_data"]["tool_names"], ["fetch"])

            store.begin_turn("turn-0002")
            request_two = store.request(
                request_bytes=b"exact second request\n",
                structured_output_schema={"type": "object"},
                prompt_version="prompt.v1",
                schema_version="schema.v1",
                operation_workspace=str(root / "workspace" / "validator-0001"),
                role="validator",
                archival_policy="fresh_per_candidate_then_archive",
            )
            second_raw = SimpleNamespace(
                output_text='{"answer":"malformed"}',
                parsed_json={"answer": "malformed"},
                receipt={"input_tokens": 8, "cached_input_tokens": 0},
                operation_telemetry={"latency_ms": 40},
                tool_call_count=0,
                failed_tool_call_count=0,
                tool_names=(),
                tool_server_names=(),
            )

            def second_dispatch(mark_invoked):
                mark_invoked()
                return second_raw

            def fail_after_dispatch(_raw):
                raise ValueError("simulated post-dispatch decode failure")

            with self.assertRaisesRegex(ValueError, "post-dispatch"):
                ledger.execute(
                    owner="validator",
                    operation="fake_validate_0001",
                    route="route.v1",
                    model="fake-model",
                    effort="medium",
                    dispatch_with_invocation_marker=second_dispatch,
                    finalize=fail_after_dispatch,
                    stored_thread_sha256="b" * 64,
                    operation_evidence=request_two,
                )

            final_snapshot = store.snapshot()
            self.assertEqual(len(final_snapshot["calls"]), 2)
            preserved = next(call for call in final_snapshot["calls"] if call["call_id"] == first_snapshot["call_id"])
            self.assertEqual(preserved["directory_sha256"], first_directory_hash)
            failed = next(call for call in final_snapshot["calls"] if call["call_id"] != preserved["call_id"])
            failed_root = root / "calls" / failed["call_id"]
            self.assertTrue(failed["terminal"])
            self.assertEqual((failed_root / "RAW_PROVIDER_RESULT.bin").read_bytes(), b'{"answer":"malformed"}')
            self.assertTrue((failed_root / "FAILURE_RECEIPT.json").is_file())
            failed_terminal = json.loads((failed_root / "TERMINAL.json").read_text())
            self.assertEqual(failed_terminal["ledger_event"]["state"], ProviderCallState.POST_VALIDATION_FAILED.value)
            self.assertEqual(ledger.dispatched_call_count, 2)

    def test_unbound_thread_archival_does_not_create_a_provider_call(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            store = ProviderOperationEvidenceStoreV1(root / "calls", stage="fake-stage")
            store.begin_turn("turn-0001")
            thread_identity = "a" * 64

            store.record_archival(
                role="validator",
                archived=True,
                resumable=False,
                disposition="archived_before_provider_dispatch",
                thread_identity_sha256=thread_identity,
            )

            snapshot = store.snapshot()
            self.assertEqual(snapshot["calls"], [])
            self.assertEqual(len(snapshot["thread_lifecycles"]), 1)
            archival = snapshot["thread_lifecycles"][0]["value"]
            self.assertIsNone(archival["call_id"])
            self.assertEqual(archival["role"], "validator")
            self.assertTrue(archival["archived"])
            self.assertFalse(archival["resumable"])
            self.assertFalse(archival["provider_dispatched"])
            self.assertEqual(archival["thread_identity_sha256"], thread_identity)
            self.assertIn(thread_identity, snapshot["thread_lifecycles"][0]["relative_path"])

    def test_unbound_thread_archival_rejects_unclosed_role(self) -> None:
        with TemporaryDirectory() as temporary:
            store = ProviderOperationEvidenceStoreV1(
                Path(temporary).resolve() / "calls",
                stage="fake-stage",
            )
            store.begin_turn("turn-0001")
            with self.assertRaisesRegex(StateConflictError, "role is not closed"):
                store.record_archival(
                    role="../../unchecked",
                    archived=True,
                    resumable=False,
                    disposition="archived_before_provider_dispatch",
                    thread_identity_sha256="a" * 64,
                )

    def test_new_thread_does_not_attach_to_prior_role_call(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            ledger = ContinuousProviderCallLedger(root / "provider_calls.jsonl", maximum_calls=2)
            store = ProviderOperationEvidenceStoreV1(root / "calls", stage="fake-stage")
            store.begin_turn("turn-0001")
            request = store.request(
                request_bytes=b"request",
                structured_output_schema={"type": "object"},
                prompt_version="prompt-v1",
                schema_version="schema-v1",
                operation_workspace=str(root / "operation"),
                role="validator",
                archival_policy="fresh_then_archive",
            )
            first_thread = "a" * 64
            raw = SimpleNamespace(
                output_text='{"accepted":true}',
                parsed_json={"accepted": True},
                receipt={},
                operation_telemetry=None,
                tool_call_count=0,
                failed_tool_call_count=0,
                tool_names=(),
                tool_server_names=(),
            )

            def dispatch(mark_invoked):
                mark_invoked()
                return raw

            ledger.execute(
                owner="validator",
                operation="validate",
                route="fake-route",
                model="fake-model",
                effort="medium",
                dispatch_with_invocation_marker=dispatch,
                finalize=lambda value: SimpleNamespace(value=value.parsed_json),
                stored_thread_sha256=first_thread,
                operation_evidence=request,
            )
            second_thread = "b" * 64

            store.record_archival(
                role="validator",
                archived=True,
                resumable=False,
                disposition="archived_before_provider_dispatch",
                thread_identity_sha256=second_thread,
            )

            snapshot = store.snapshot()
            self.assertEqual(len(snapshot["calls"]), 1)
            self.assertNotIn(
                "ARCHIVAL.json",
                snapshot["calls"][0]["artifact_sha256_by_name"],
            )
            self.assertEqual(len(snapshot["thread_lifecycles"]), 1)
            self.assertEqual(
                snapshot["thread_lifecycles"][0]["value"]["thread_identity_sha256"],
                second_thread,
            )


if __name__ == "__main__":
    unittest.main()
