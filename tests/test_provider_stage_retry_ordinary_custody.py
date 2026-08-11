from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

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
from cera.serialization import (
    canonical_bytes,
    canonical_sha256,
    domain_sha256,
    text_sha256,
    to_primitive,
)

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
        return context, self._freeze_in(self.store, context)

    @staticmethod
    def _freeze_in(
        store: ProtectedOrdinaryStageRetryCustodyStoreV1,
        context: OrdinaryStageRetryRequestContextV1,
        *,
        payload: dict[str, object] | None = None,
    ) -> ProtectedOrdinaryCustodyReceiptV1:
        return store.freeze_request(
            normalized_request=_payload() if payload is None else payload,
            binding=context.binding,
            generation=context.generation,
            turn_input=context.turn_input,
            retrieval_snapshot=context.planner_retrieval.retrieval_snapshot,
            tool_result_bundle=context.planner_retrieval.tool_result_bundle,
        )

    @staticmethod
    def _scope(
        context: OrdinaryStageRetryRequestContextV1,
        stage: ProviderStage,
        ordinal: int,
    ) -> ProviderStageRetryOccurrenceScopeV1:
        return ProviderStageRetryOccurrenceScopeV1.create(
            world_id=context.binding.world_id,
            branch_id=context.binding.branch_id,
            request_id=context.binding.request_id,
            generation_id=context.generation_id,
            stage=stage,
            stage_ordinal=ordinal,
            accepted_state_sha256=text_sha256(f"accepted-state:{stage.value}:{ordinal}"),
            exact_input=f"frozen-packet:{stage.value}:{ordinal}".encode(),
            authority_binding={"stage": stage.value, "ordinal": ordinal},
        )

    @staticmethod
    def _bind_scope(
        store: ProtectedOrdinaryStageRetryCustodyStoreV1,
        scope: ProviderStageRetryOccurrenceScopeV1,
        *,
        context_sha256: str,
        advance_cursor: bool,
    ) -> None:
        store.bind_occurrence(scope=scope, context_sha256=context_sha256)
        store.bind_chain(
            chain_id=scope.identity.chain_id,
            request_id=scope.request_id,
            context_sha256=context_sha256,
        )
        if advance_cursor:
            store.advance_latest_chain(scope=scope, context_sha256=context_sha256)

    @staticmethod
    def _occurrence_path(
        store: ProtectedOrdinaryStageRetryCustodyStoreV1,
        scope: ProviderStageRetryOccurrenceScopeV1,
    ) -> Path:
        occurrence_key = domain_sha256(
            "cera.ordinary_stage_retry_occurrence_key.v1",
            {
                "request_id": scope.request_id,
                "generation_id": scope.generation_id,
                "stage": scope.stage.value,
                "stage_ordinal": scope.stage_ordinal,
            },
        )
        return store.occurrences_root / f"{occurrence_key}.json"

    @staticmethod
    def _read_payload(path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict)
        return payload

    @staticmethod
    def _write_payload(path: Path, payload: dict[str, Any]) -> None:
        path.write_bytes(canonical_bytes(payload))

    @classmethod
    def _rehash_occurrence(
        cls,
        path: Path,
        payload: dict[str, Any],
    ) -> Path:
        identity = payload["identity"]
        occurrence_key = domain_sha256(
            "cera.ordinary_stage_retry_occurrence_key.v1",
            identity,
        )
        payload["occurrence_key"] = occurrence_key
        unsigned = {
            key: value for key, value in payload.items() if key != "occurrence_binding_sha256"
        }
        payload["occurrence_binding_sha256"] = canonical_sha256(unsigned)
        rewritten = path.parent / f"{occurrence_key}.json"
        cls._write_payload(rewritten, payload)
        if rewritten != path:
            path.unlink()
        return rewritten

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

    def test_occurrence_counts_add_sibling_lanes_without_advancing_linear_cursor(
        self,
    ) -> None:
        context, receipt = self._freeze()
        planner = self._scope(context, ProviderStage.PLANNER, 1)
        writer = self._scope(context, ProviderStage.WRITER, 1)
        semantic = self._scope(context, ProviderStage.SEMANTIC_VALIDATOR, 1)
        reader = self._scope(context, ProviderStage.READER, 1)
        for stage_scope in (planner, writer):
            self._bind_scope(
                self.store,
                stage_scope,
                context_sha256=receipt.context_sha256,
                advance_cursor=True,
            )
        for stage_scope in (semantic, reader):
            self._bind_scope(
                self.store,
                stage_scope,
                context_sha256=receipt.context_sha256,
                advance_cursor=False,
            )

        foreign_context = _context(
            world_id="world-foreign-custody",
            branch_id="branch-foreign-custody",
        )
        foreign_receipt = self._freeze_in(self.store, foreign_context)
        foreign_planner = self._scope(foreign_context, ProviderStage.PLANNER, 1)
        self._bind_scope(
            self.store,
            foreign_planner,
            context_sha256=foreign_receipt.context_sha256,
            advance_cursor=True,
        )

        expected = {
            ProviderStage.PLANNER.value: 1,
            ProviderStage.WRITER.value: 1,
            ProviderStage.SEMANTIC_VALIDATOR.value: 1,
            ProviderStage.READER.value: 1,
            ProviderStage.RECORDER.value: 0,
        }
        self.assertEqual(
            self.store.stage_occurrence_counts(context.binding.request_id),
            expected,
        )
        self.assertEqual(
            self.store.latest_chain_for_chain(planner.identity.chain_id).chain_id,
            writer.identity.chain_id,
        )

        restarted = ProtectedOrdinaryStageRetryCustodyStoreV1(self.root)
        self.assertEqual(
            restarted.stage_occurrence_counts(context.binding.request_id),
            expected,
        )
        self.assertEqual(
            restarted.latest_chain_for_chain(planner.identity.chain_id).chain_id,
            writer.identity.chain_id,
        )

    def test_occurrence_counts_fail_closed_when_cursor_occurrence_is_missing(self) -> None:
        context, receipt = self._freeze()
        planner = self._scope(context, ProviderStage.PLANNER, 1)
        writer = self._scope(context, ProviderStage.WRITER, 1)
        for stage_scope in (planner, writer):
            self._bind_scope(
                self.store,
                stage_scope,
                context_sha256=receipt.context_sha256,
                advance_cursor=True,
            )
        self._occurrence_path(self.store, writer).unlink()

        with self.assertRaisesRegex(StateConflictError, "lost its stage occurrence"):
            self.store.stage_occurrence_counts(context.binding.request_id)

    def test_occurrence_counts_require_positive_contiguous_sibling_ordinals(self) -> None:
        context, receipt = self._freeze()
        planner = self._scope(context, ProviderStage.PLANNER, 1)
        reader_two = self._scope(context, ProviderStage.READER, 2)
        self._bind_scope(
            self.store,
            planner,
            context_sha256=receipt.context_sha256,
            advance_cursor=True,
        )
        self._bind_scope(
            self.store,
            reader_two,
            context_sha256=receipt.context_sha256,
            advance_cursor=False,
        )

        with self.assertRaisesRegex(StateConflictError, "ordinals are not contiguous"):
            self.store.stage_occurrence_counts(context.binding.request_id)

    def test_occurrence_verification_rejects_rehashed_identity_relabels(self) -> None:
        cases: tuple[tuple[str, str, object], ...] = (
            ("request", "request_id", f"request-{text_sha256('foreign-request')}"),
            ("generation", "generation_id", "generation-00000002"),
            ("stage", "stage", ProviderStage.WRITER.value),
            ("ordinal", "stage_ordinal", 2),
            ("invalid-stage", "stage", "not-a-provider-stage"),
            ("nonpositive-ordinal", "stage_ordinal", 0),
        )
        for label, field_name, replacement in cases:
            with self.subTest(label=label):
                store = ProtectedOrdinaryStageRetryCustodyStoreV1(self.root / label)
                context = _context()
                receipt = self._freeze_in(store, context)
                planner = self._scope(context, ProviderStage.PLANNER, 1)
                self._bind_scope(
                    store,
                    planner,
                    context_sha256=receipt.context_sha256,
                    advance_cursor=True,
                )
                path = self._occurrence_path(store, planner)
                payload = self._read_payload(path)
                payload["identity"][field_name] = replacement
                self._rehash_occurrence(path, payload)

                with self.assertRaises(StateConflictError):
                    store.stage_occurrence_counts(context.binding.request_id)

    def test_occurrence_verification_rejects_key_hash_context_and_chain_tampering(
        self,
    ) -> None:
        for label in ("filename", "key", "binding-hash", "context", "chain"):
            with self.subTest(label=label):
                store = ProtectedOrdinaryStageRetryCustodyStoreV1(self.root / label)
                context = _context()
                receipt = self._freeze_in(store, context)
                planner = self._scope(context, ProviderStage.PLANNER, 1)
                self._bind_scope(
                    store,
                    planner,
                    context_sha256=receipt.context_sha256,
                    advance_cursor=True,
                )
                path = self._occurrence_path(store, planner)
                payload = self._read_payload(path)
                if label == "filename":
                    path.rename(path.parent / f"{text_sha256('wrong-filename')}.json")
                elif label == "key":
                    payload["occurrence_key"] = text_sha256("wrong-occurrence-key")
                    unsigned = {
                        key: value
                        for key, value in payload.items()
                        if key != "occurrence_binding_sha256"
                    }
                    payload["occurrence_binding_sha256"] = canonical_sha256(unsigned)
                    self._write_payload(path, payload)
                elif label == "binding-hash":
                    payload["scope_sha256"] = text_sha256("changed-scope")
                    self._write_payload(path, payload)
                elif label == "context":
                    payload["context_sha256"] = text_sha256("changed-context")
                    self._rehash_occurrence(path, payload)
                else:
                    other_chain_id = _chain_id("other-bound-chain")
                    store.bind_chain(
                        chain_id=other_chain_id,
                        request_id=context.binding.request_id,
                        context_sha256=receipt.context_sha256,
                    )
                    payload["chain_id"] = other_chain_id
                    self._rehash_occurrence(path, payload)

                with self.assertRaises(StateConflictError):
                    store.stage_occurrence_counts(context.binding.request_id)

    def test_occurrence_counts_cross_check_cursor_request_hashes_and_chain(self) -> None:
        for field_name in (
            "request_sha256",
            "request_occurrence_sha256",
            "chain_id",
        ):
            with self.subTest(field_name=field_name):
                store = ProtectedOrdinaryStageRetryCustodyStoreV1(
                    self.root / f"cursor-{field_name}"
                )
                context = _context()
                receipt = self._freeze_in(store, context)
                planner = self._scope(context, ProviderStage.PLANNER, 1)
                self._bind_scope(
                    store,
                    planner,
                    context_sha256=receipt.context_sha256,
                    advance_cursor=True,
                )
                cursor_path = (
                    store.latest_chains_root
                    / f"{context.binding.request_id.removeprefix('request-')}.json"
                )
                payload = self._read_payload(cursor_path)
                if field_name == "chain_id":
                    replacement = _chain_id("other-cursor-chain")
                    store.bind_chain(
                        chain_id=replacement,
                        request_id=context.binding.request_id,
                        context_sha256=receipt.context_sha256,
                    )
                    payload["latest_chain_id"] = replacement
                else:
                    replacement = text_sha256(f"changed-{field_name}")
                payload["entries"][0][field_name] = replacement
                unsigned = {
                    key: value for key, value in payload.items() if key != "latest_chain_sha256"
                }
                payload["latest_chain_sha256"] = canonical_sha256(unsigned)
                self._write_payload(cursor_path, payload)

                with self.assertRaisesRegex(StateConflictError, "lost its stage occurrence"):
                    store.stage_occurrence_counts(context.binding.request_id)


if __name__ == "__main__":
    unittest.main()
