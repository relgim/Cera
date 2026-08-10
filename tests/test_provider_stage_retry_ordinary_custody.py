from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cera.errors import StateConflictError
from cera.pi_scene.provider_stage_retry import ProviderStage
from cera.pi_scene.provider_stage_retry_ordinary import (
    OrdinaryStageRetryRequestContextV1,
)
from cera.pi_scene.provider_stage_retry_ordinary_custody import (
    ProtectedOrdinaryCustodyReceiptV1,
    ProtectedOrdinaryStageRetryCustodyStoreV1,
    ProtectedRecorderContinuationV1,
)
from cera.pi_scene.provider_stage_retry_scope import (
    ProviderStageRetryOccurrenceScopeV1,
)
from cera.serialization import canonical_sha256, text_sha256, to_primitive

from .test_provider_stage_retry_ordinary import _context, _payload


def _chain_id(label: str) -> str:
    return f"stage-retry-{text_sha256(label)}"


class ProtectedOrdinaryStageRetryCustodyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = ProtectedOrdinaryStageRetryCustodyStoreV1(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _freeze(
        self,
    ) -> tuple[
        OrdinaryStageRetryRequestContextV1,
        ProtectedOrdinaryCustodyReceiptV1,
    ]:
        context = _context()
        receipt = self.store.freeze_request(
            normalized_request=_payload(),
            binding=context.binding,
            generation=context.generation,
            turn_input=context.turn_input,
            retrieval_snapshot=context.planner_retrieval.retrieval_snapshot,
            tool_result_bundle=context.planner_retrieval.tool_result_bundle,
        )
        return context, receipt

    def test_restart_recovers_exact_request_turn_and_chain_context(self) -> None:
        context, receipt = self._freeze()
        chain_id = _chain_id("restart-chain")
        self.store.bind_chain(
            chain_id=chain_id,
            request_id=context.binding.request_id,
            context_sha256=receipt.context_sha256,
        )

        restarted = ProtectedOrdinaryStageRetryCustodyStoreV1(self.root)
        pending = restarted.load_request_for_chain(chain_id)
        self.assertEqual(pending.normalized_request, _payload())
        self.assertEqual(
            to_primitive(pending.turn_input),
            to_primitive(context.turn_input),
        )
        self.assertEqual(
            canonical_sha256(pending.turn_input),
            canonical_sha256(context.turn_input),
        )
        self.assertEqual(restarted.chain_context(chain_id).request_id, receipt.request_id)

    def test_different_prompt_is_blocked_while_branch_barrier_is_active(self) -> None:
        context, receipt = self._freeze()
        other_payload = {
            **_payload(),
            "messages": [{"role": "user", "content": "A different prompt."}],
        }
        other_context = _context(payload=other_payload)

        barrier = self.store.branch_barrier(
            world_id=context.binding.world_id,
            branch_id=context.binding.branch_id,
        )
        self.assertIsNotNone(barrier)
        assert barrier is not None
        self.assertEqual(barrier.request_id, receipt.request_id)
        with self.assertRaisesRegex(StateConflictError, "unresolved request barrier"):
            self.store.freeze_request(
                normalized_request=other_payload,
                binding=other_context.binding,
                generation=other_context.generation,
                turn_input=other_context.turn_input,
                retrieval_snapshot=other_context.planner_retrieval.retrieval_snapshot,
                tool_result_bundle=other_context.planner_retrieval.tool_result_bundle,
            )

    def test_terminal_response_remains_idempotently_retrievable_by_chain(self) -> None:
        context, receipt = self._freeze()
        chain_id = _chain_id("terminal-chain")
        self.store.bind_chain(
            chain_id=chain_id,
            request_id=context.binding.request_id,
            context_sha256=receipt.context_sha256,
        )
        response = {
            "id": "completion-ordinary-retry",
            "choices": [{"message": {"role": "assistant", "content": "Exact ending."}}],
        }
        first = self.store.bind_terminal_response(
            request_id=context.binding.request_id,
            response=response,
        )
        second = self.store.bind_terminal_response(
            request_id=context.binding.request_id,
            response=response,
        )
        self.assertEqual(first, second)
        self.assertEqual(
            self.store.bind_terminal_response_for_chain(
                chain_id=chain_id,
                response=response,
            ),
            first,
        )

        restarted = ProtectedOrdinaryStageRetryCustodyStoreV1(self.root)
        loaded, loaded_receipt = restarted.load_terminal_response_for_chain(chain_id)
        self.assertEqual(loaded, response)
        self.assertEqual(loaded_receipt, first)
        restarted.redact_request(
            context.binding.request_id,
            disposition="completed",
            terminal_evidence_sha256=first.response_sha256,
        )
        loaded_after_redaction, _ = restarted.load_terminal_response_for_chain(chain_id)
        self.assertEqual(loaded_after_redaction, response)
        self.assertIsNone(
            restarted.branch_barrier(
                world_id=context.binding.world_id,
                branch_id=context.binding.branch_id,
            )
        )

    def test_optional_terminal_response_distinguishes_absence_from_corruption(self) -> None:
        context, _ = self._freeze()
        self.assertIsNone(self.store.load_terminal_response_optional(context.binding.request_id))
        self.store.bind_terminal_response(
            request_id=context.binding.request_id,
            response={"id": "completion-before-corruption"},
        )
        response_path = (
            self.root / "responses" / f"{context.binding.request_id.removeprefix('request-')}.json"
        )
        response_path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(StateConflictError, "response shape changed"):
            self.store.load_terminal_response_optional(context.binding.request_id)

    def test_terminal_failure_barrier_requires_explicit_external_release(self) -> None:
        context, _ = self._freeze()
        terminal = self.store.redact_request(
            context.binding.request_id,
            disposition="attempts_exhausted",
            terminal_evidence_sha256=text_sha256("attempts-exhausted"),
        )
        self.assertTrue(terminal.branch_barrier_active)
        restarted = ProtectedOrdinaryStageRetryCustodyStoreV1(self.root)
        self.assertIsNotNone(
            restarted.branch_barrier(
                world_id=context.binding.world_id,
                branch_id=context.binding.branch_id,
            )
        )
        released = restarted.release_branch_barrier(
            context.binding.request_id,
            recovery_evidence_sha256=text_sha256("external-recovery"),
        )
        self.assertFalse(released.branch_barrier_active)
        self.assertIsNone(
            restarted.branch_barrier(
                world_id=context.binding.world_id,
                branch_id=context.binding.branch_id,
            )
        )

    def test_recorder_continuation_mapping_survives_restart(self) -> None:
        context, receipt = self._freeze()
        chain_id = _chain_id("recorder-chain")
        self.store.bind_chain(
            chain_id=chain_id,
            request_id=context.binding.request_id,
            context_sha256=receipt.context_sha256,
        )
        continuation = ProtectedRecorderContinuationV1(
            chain_id=chain_id,
            request_id=context.binding.request_id,
            review_id="review-accepted-ordinary",
            accepted_turn_id="turn-0001",
            accepted_receipt_sha256=text_sha256("accepted-receipt"),
        )
        self.store.bind_recorder_continuation(continuation)
        restarted = ProtectedOrdinaryStageRetryCustodyStoreV1(self.root)
        self.assertEqual(restarted.find_recorder_continuation(chain_id), continuation)

    def test_latest_chain_cursor_survives_restart_and_never_rolls_back(self) -> None:
        context, receipt = self._freeze()

        def scope(stage: ProviderStage, ordinal: int) -> ProviderStageRetryOccurrenceScopeV1:
            return ProviderStageRetryOccurrenceScopeV1.create(
                world_id=context.binding.world_id,
                branch_id=context.binding.branch_id,
                request_id=context.binding.request_id,
                generation_id=context.generation_id,
                stage=stage,
                stage_ordinal=ordinal,
                accepted_state_sha256=text_sha256(f"accepted-{stage.value}-{ordinal}"),
                exact_input=f"exact-{stage.value}-{ordinal}".encode(),
                authority_binding={"stage": stage.value, "ordinal": ordinal},
            )

        scopes = (
            scope(ProviderStage.PLANNER, 1),
            scope(ProviderStage.WRITER, 1),
            scope(ProviderStage.SEMANTIC_VALIDATOR, 1),
            scope(ProviderStage.WRITER, 2),
        )
        for stage_scope in scopes:
            self.store.bind_occurrence(
                scope=stage_scope,
                context_sha256=receipt.context_sha256,
            )
            self.store.bind_chain(
                chain_id=stage_scope.identity.chain_id,
                request_id=context.binding.request_id,
                context_sha256=receipt.context_sha256,
            )
            self.store.advance_latest_chain(
                scope=stage_scope,
                context_sha256=receipt.context_sha256,
            )

        restarted = ProtectedOrdinaryStageRetryCustodyStoreV1(self.root)
        latest = restarted.latest_chain_for_chain(scopes[0].identity.chain_id)
        self.assertEqual(latest.chain_id, scopes[-1].identity.chain_id)
        self.assertEqual(latest.request_sha256, scopes[-1].request_sha256)
        source_identity = restarted.chain_request_identity(scopes[0].identity.chain_id)
        self.assertEqual(source_identity.request_id, context.binding.request_id)
        self.assertEqual(source_identity.request_sha256, scopes[0].request_sha256)

        # Replaying an earlier cached stage repairs a missing cursor write, but
        # can never move an already advanced request back to that old chain.
        replay = restarted.advance_latest_chain(
            scope=scopes[0],
            context_sha256=receipt.context_sha256,
        )
        self.assertEqual(replay.chain_id, scopes[-1].identity.chain_id)


if __name__ == "__main__":
    unittest.main()
