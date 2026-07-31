from __future__ import annotations

from dataclasses import replace
import json
import unittest

from cera.composer import ArtifactPublicationMode, PurePresentationRenderer, RendererProfile
from cera.errors import ErrorCode, EvidenceServiceError
from cera.evidence import EvidenceFetchRequest
from cera.ids import IdKind, deterministic_id
from cera.ingress import IntentInterpretationReceipt
from cera.reasoner import SeedDossierReceipt
from cera.registry import build_schema_registry
from cera.runtime import (
    OrdinaryApplicationFailure,
    OrdinaryApplicationRequest,
    OrdinaryApplicationRequestV2,
    IngressPublicationEvidence,
    ProviderFreeOrdinaryApplication,
)
from cera.serialization import canonical_json, domain_sha256, text_sha256, to_primitive
import tests.test_post_publication as post_support
import tests.test_real_genesis_integration as real_support


class ExplodingPort:
    def execute(self, *args, **kwargs):
        raise AssertionError("replay called an execution port")

    def render(self, *args, **kwargs):
        raise AssertionError("replay called the renderer")

    def consolidate(self, *args, **kwargs):
        raise AssertionError("replay called the consolidator")


class OrdinaryApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = post_support.PostPublicationTests("runTest")
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)

    @property
    def live(self):
        return self.harness.live

    @property
    def store(self):
        return self.live.real.sandbox.store

    @staticmethod
    def renderer():
        return PurePresentationRenderer((RendererProfile("plain", "v1"),))

    def application(self, args, *, renderer=None, consolidator=None):
        return ProviderFreeOrdinaryApplication(
            store=self.store,
            pipeline=self.live.pipeline,
            reasoner_port=args[1],
            composer_port=args[3],
            renderer=renderer,
            consolidator=consolidator,
        )

    @staticmethod
    def request(args, *, render=True, consolidate=True):
        return OrdinaryApplicationRequest(
            schema_version=OrdinaryApplicationRequest.SCHEMA_VERSION,
            reasoner_request=args[0],
            composer_plan=args[2],
            renderer_profile="plain" if render else None,
            consolidation_requested=consolidate,
        )

    def test_full_ordinary_application_commits_and_returns_registered_contracts(self) -> None:
        args = self.live.ordinary_case("phase20-full")
        consolidator = self.harness.no_change_port("phase20-full")
        request = self.request(args)
        result = self.application(
            args,
            renderer=self.renderer(),
            consolidator=consolidator,
        ).execute(request)

        self.assertTrue(result.receipt.story_state_committed)
        self.assertFalse(result.receipt.exact_replay)
        self.assertEqual(result.receipt.adapter_call_count, 2)
        self.assertEqual(result.receipt.downstream_failure_count, 0)
        self.assertEqual(result.operational_report.terminal_count, 3)
        self.assertEqual(result.operational_report.failed_count, 0)
        self.assertEqual(consolidator.invocation_count, 1)
        self.assertEqual(self.store.table_count("artifacts"), 1)
        self.assertEqual(self.store.table_count("authority_records"), 2)
        reply_record_id = deterministic_id(
            IdKind.MATERIAL,
            "cera.accepted_reply_material.v1",
            str(result.accepted_artifact.artifact_id),
        )
        snapshot = self.live.real.sandbox.open_snapshot(
            "phase20-accepted-reply-material"
        )
        reply_evidence_id = self.live.real.sandbox.evidence_id(
            snapshot,
            reply_record_id,
        )
        reply = self.live.real.sandbox.service.fetch_evidence(
            snapshot,
            EvidenceFetchRequest(
                (reply_evidence_id,),
                ("accepted_reply", "artifact_binding"),
            ),
        ).exact_records[0]
        reply_sections = json.loads(reply.sections_json)
        self.assertEqual(
            reply_sections["accepted_reply"]["prose"],
            result.accepted_artifact.accepted_prose,
        )
        self.assertEqual(
            reply_sections["accepted_reply"]["prose_sha256"],
            result.accepted_artifact.prose_sha256,
        )
        sakura_snapshot = self.live.real.sandbox.open_snapshot(
            "phase20-accepted-reply-private",
            scope=self.live.real.sandbox.character_scope("Sakura"),
        )
        with self.assertRaises(EvidenceServiceError):
            self.live.real.sandbox.service.fetch_evidence(
                sakura_snapshot,
                EvidenceFetchRequest(
                    (
                        self.live.real.sandbox.evidence_id(
                            sakura_snapshot,
                            reply_record_id,
                        ),
                    ),
                    ("accepted_reply",),
                ),
            )

        registry = build_schema_registry()
        self.assertEqual(registry.decode(to_primitive(request)), request)
        self.assertEqual(
            registry.decode(to_primitive(result.receipt)),
            result.receipt,
        )

    def test_v2_application_commits_seed_lookup_evidence_beyond_pipeline_receipts(self) -> None:
        args = list(self.live.ordinary_case("phase20-v2-ingress"))
        original_request = args[0]
        relationship = self.live.real.relationship_record("Hana")
        relationship_evidence_id = self.live.real.sandbox.evidence_id(
            original_request.prepared_turn.evidence_snapshot,
            relationship.record_id,
        )
        seed_fetch = self.live.real.sandbox.service.fetch_evidence(
            original_request.prepared_turn.evidence_snapshot,
            EvidenceFetchRequest(
                (relationship_evidence_id,),
                ("claim",),
            ),
        )
        seed_exact = seed_fetch.exact_records
        seed_receipt = SeedDossierReceipt(
            schema_version=SeedDossierReceipt.SCHEMA_VERSION,
            seed_receipt_id=deterministic_id(
                IdKind.SEED_RECEIPT,
                "cera.test.seed_receipt",
                original_request.request_sha256,
            ),
            snapshot_token=original_request.prepared_turn.evidence_snapshot.snapshot_token,
            snapshot_binding_sha256=(
                original_request.prepared_turn.evidence_snapshot.binding_sha256
            ),
            reauthorized_seed_hashes=tuple(
                domain_sha256("cera.seed_exact_evidence.v1", value)
                for value in seed_exact
            ),
            obligation_resolutions=(),
            lookup_receipt_ids=(seed_fetch.receipt.lookup_receipt_id,),
            exact_evidence_ids=tuple(value.evidence_id for value in seed_exact),
            authoritative_store_writes=0,
        )
        reasoner_request = replace(
            original_request,
            seed_dossier=replace(
                original_request.seed_dossier,
                exact_seed_evidence=seed_exact,
                seed_receipt_id=seed_receipt.seed_receipt_id,
            ),
        )
        args[0] = reasoner_request
        interpretation = IntentInterpretationReceipt(
            schema_version=IntentInterpretationReceipt.SCHEMA_VERSION,
            receipt_id=deterministic_id(
                IdKind.VALIDATION,
                "cera.test.intent_receipt",
                reasoner_request.request_sha256,
            ),
            envelope_sha256=text_sha256("phase20-v2-envelope"),
            interpretation_sha256=text_sha256("phase20-v2-interpretation"),
            adapter_version="cera.test.exact_projection.v1",
            external_provider_calls=0,
            authoritative_store_writes=0,
        )
        ingress = IngressPublicationEvidence.create(
            request_id=reasoner_request.prepared_turn.request.request_id,
            source_sha256=reasoner_request.prepared_turn.request.source_sha256,
            reasoner_request_sha256=reasoner_request.request_sha256,
            interpretation_receipt=interpretation,
            seed_receipt=seed_receipt,
            seed_lookup_receipts=(seed_fetch.receipt,),
        )
        application_request = OrdinaryApplicationRequestV2(
            schema_version=OrdinaryApplicationRequestV2.SCHEMA_VERSION,
            reasoner_request=reasoner_request,
            composer_plan=args[2],
            renderer_profile=None,
            consolidation_requested=False,
            ingress_evidence=ingress,
        )
        result = self.application(args).execute(application_request)
        self.assertIn(
            seed_fetch.receipt.lookup_receipt_id,
            result.commit_receipt.lookup_receipt_ids,
        )
        stored = self.store.get_turn_receipt_record(
            seed_fetch.receipt.lookup_receipt_id
        )
        self.assertEqual(stored.payload_json, canonical_json(seed_fetch.receipt))
        self.assertEqual(
            self.store.get_turn_receipt_record(ingress.receipt_id).payload_json,
            canonical_json(ingress),
        )

    def test_exact_application_replay_makes_zero_adapter_or_downstream_calls(self) -> None:
        args = self.live.ordinary_case("phase20-replay")
        consolidator = self.harness.no_change_port("phase20-replay")
        request = self.request(args)
        first = self.application(
            args,
            renderer=self.renderer(),
            consolidator=consolidator,
        ).execute(request)
        runner_calls = args[4].calls
        composer_calls = len(args[5].calls)

        replay_app = ProviderFreeOrdinaryApplication(
            store=self.store,
            pipeline=self.live.pipeline,
            reasoner_port=ExplodingPort(),
            composer_port=ExplodingPort(),
        )
        replay = replay_app.execute(request)
        self.assertTrue(replay.receipt.exact_replay)
        self.assertEqual(replay.receipt.adapter_call_count, 0)
        self.assertEqual(replay.accepted_artifact, first.accepted_artifact)
        self.assertEqual(replay.commit_receipt, first.commit_receipt)
        self.assertEqual(args[4].calls, runner_calls)
        self.assertEqual(len(args[5].calls), composer_calls)
        self.assertEqual(consolidator.invocation_count, 1)
        self.assertEqual(self.store.table_count("artifacts"), 1)

    def test_downstream_failure_returns_committed_story_and_safe_report(self) -> None:
        args = self.live.ordinary_case("phase20-downstream-failure")
        unavailable = self.harness.no_change_port("phase20-downstream-failure")
        unavailable.available = False
        request = self.request(args)
        result = self.application(
            args,
            renderer=post_support.FailingRenderer(),
            consolidator=unavailable,
        ).execute(request)
        self.assertTrue(result.receipt.story_state_committed)
        self.assertEqual(result.operational_report.failed_count, 2)
        self.assertEqual(result.operational_report.pending_count, 1)
        public = canonical_json(result.operational_report)
        self.assertNotIn("simulated renderer outage", public)
        self.assertNotIn("scripted consolidator is unavailable", public)
        self.assertTrue(all(not value.details for value in result.operational_report.errors))

        healthy = self.harness.no_change_port("phase20-healthy-unused")
        replay = ProviderFreeOrdinaryApplication(
            store=self.store,
            pipeline=self.live.pipeline,
            reasoner_port=ExplodingPort(),
            composer_port=ExplodingPort(),
            renderer=self.renderer(),
            consolidator=healthy,
        ).execute(request)
        self.assertTrue(replay.receipt.exact_replay)
        self.assertEqual(replay.operational_report.failed_count, 2)
        self.assertEqual(healthy.invocation_count, 0)

    def test_regeneration_application_preserves_prior_fork(self) -> None:
        first_args = self.live.ordinary_case("phase20-regeneration-first")
        first_request = self.request(first_args, render=False, consolidate=False)
        first = self.application(first_args).execute(first_request)
        child = real_support.ident(IdKind.BRANCH, "phase20-child")
        self.store.fork_branch(self.live.real.sandbox.branch_id, child)

        replacement_args = self.live.ordinary_case(
            "phase20-regeneration-replacement",
            source="Ted asks for a different continuation of the same moment.",
            expected_generation=1,
            expected_parent_artifact_id=first.accepted_artifact.artifact_id,
            publication_mode=ArtifactPublicationMode.REGENERATE,
            publication_parent_artifact_id=None,
            replaces_artifact_id=first.accepted_artifact.artifact_id,
        )
        replacement = self.application(replacement_args).execute(
            self.request(replacement_args, render=False, consolidate=False)
        )
        self.assertIsNone(replacement.accepted_artifact.parent_artifact_id)
        self.assertEqual(replacement.operational_report.work_items, ())
        self.assertEqual(
            self.store.visible_artifact_ids(self.live.real.sandbox.branch_id),
            (replacement.accepted_artifact.artifact_id,),
        )
        self.assertEqual(
            self.store.visible_artifact_ids(child),
            (first.accepted_artifact.artifact_id,),
        )

    def test_missing_requested_downstream_adapter_fails_before_reasoning(self) -> None:
        args = self.live.ordinary_case("phase20-preflight")
        request = self.request(args, render=True, consolidate=False)
        with self.assertRaises(OrdinaryApplicationFailure) as raised:
            self.application(args).execute(request)
        self.assertEqual(raised.exception.envelope.error_code, ErrorCode.RENDER_FAILED)
        self.assertEqual(args[4].calls, 0)
        self.assertEqual(len(args[5].calls), 0)
        self.assertEqual(self.store.table_count("artifacts"), 0)

    def test_pipeline_exception_is_normalized_and_does_not_commit(self) -> None:
        args = self.live.ordinary_case("phase20-normalized-failure")
        app = ProviderFreeOrdinaryApplication(
            store=self.store,
            pipeline=self.live.pipeline,
            reasoner_port=args[1],
            composer_port=ExplodingPort(),
        )
        with self.assertRaises(OrdinaryApplicationFailure) as raised:
            app.execute(self.request(args, render=False, consolidate=False))
        self.assertEqual(
            raised.exception.envelope.error_code,
            ErrorCode.COMPOSER_CONTRACT_INVALID,
        )
        self.assertFalse(raised.exception.envelope.story_state_committed)
        self.assertEqual(self.store.table_count("artifacts"), 0)
        self.assertEqual(self.store.table_count("authority_records"), 0)

    def test_reused_idempotency_key_with_different_turn_fails_before_adapters(self) -> None:
        first_args = self.live.ordinary_case("phase20-idempotency-first")
        self.application(first_args).execute(
            self.request(first_args, render=False, consolidate=False)
        )
        second_args = self.live.ordinary_case(
            "phase20-idempotency-conflict",
            source="Ted supplies a different source message.",
            expected_generation=1,
            expected_parent_artifact_id=(
                self.store.get_branch(self.live.real.sandbox.branch_id).head_artifact_id
            ),
        )
        object.__setattr__(
            second_args[0].prepared_turn.request,
            "idempotency_key",
            first_args[0].prepared_turn.request.idempotency_key,
        )
        with self.assertRaises(OrdinaryApplicationFailure) as raised:
            self.application(second_args).execute(
                self.request(second_args, render=False, consolidate=False)
            )
        self.assertEqual(raised.exception.envelope.error_code, ErrorCode.STATE_CONFLICT)
        self.assertEqual(second_args[4].calls, 0)
        self.assertEqual(len(second_args[5].calls), 0)
        self.assertEqual(self.store.table_count("artifacts"), 1)


if __name__ == "__main__":
    unittest.main()
