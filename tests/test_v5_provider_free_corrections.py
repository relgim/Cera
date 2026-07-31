from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator

from cera.adult_craft import (
    AdultCraftAxis,
    AdultCraftConcept,
    AdultCraftFamily,
    AdultCraftMode,
    AdultCraftNeedV3,
    BeatCraftRequirementV3,
    CharacterChannelCraftNeedV3,
    OutcomeAuthorityState,
    OutcomeScope,
    SceneChannelCraftNeedV3,
    SegmentCommitment,
    SpecificityRegister,
    RealizationChannel,
)
from cera.adult_craft.catalog import AdultCraftCatalog
from cera.adult_craft.selector import AdultCraftSelector
from cera.composer import (
    ComposerContextAssembler,
    ComposerCoordinator,
    ComposerExecutionFailure,
    DeepSeekCompositionDraftV3,
    DeepSeekSceneComposerPort,
    deepseek_composition_draft_v3_json_schema,
)
from cera.errors import ContractValidationError, ErrorCode
from cera.ids import IdKind, deterministic_id
from cera.providers import DeepSeekChatTransport, deepseek_composer_candidate
from cera.realization import (
    InMemoryRejectedCandidateReviewStore,
    SceneRealizationVerificationCoordinator,
)
from cera.reasoner import (
    CodexReasonerDraftV3,
    codex_reasoner_draft_v3_json_schema,
    validate_reasoner_draft_payload_semantics,
)
from cera.runtime import (
    LiveShapedTurnFailure,
    LiveShapedTurnPipeline,
    TurnFailureEvidenceBundleV2,
    TurnFailureEvidenceBundleV3,
    TurnStageAuditJournal,
)
from cera.schema import from_mapping
from cera.serialization import canonical_json, domain_sha256

import tests.test_composer as composer_support
import tests.test_deepseek_scene_composer as deepseek_support
import tests.test_live_shaped_pipeline as live_support
from tests.test_v4_structural_correction import AnchoredProtectedUserRejectingVerifier
from scripts.run_live_story_qualification import QualificationRejectedCandidateReviewStore


ROOT = Path(__file__).resolve().parents[1]
CATALOG_ROOT = ROOT / "adult" / "catalog" / "adult_craft_v1"


class V5AdultCraftOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = AdultCraftCatalog.load(CATALOG_ROOT)
        self.support = composer_support.ComposerTests("runTest")
        self.support.setUp()
        self.addCleanup(self.support.doCleanups)

    def _need(self, lexical_concepts=()):
        request = self.support.request("v5-adult-craft", adult=True)
        decision = request.reasoner_outcome.decision
        assert decision is not None
        beat = decision.current_segment.ordered_beats[0]
        need = AdultCraftNeedV3(
            schema_version=AdultCraftNeedV3.SCHEMA_VERSION,
            craft_need_id=deterministic_id(
                IdKind.ADULT_CRAFT_NEED,
                "test.v5.adult_craft_need",
                "|".join(value.value for value in lexical_concepts) or "semantic-only",
            ),
            request_id=request.prepared_turn.request.request_id,
            decision_id=decision.decision_id,
            sequence_plan_sha256=domain_sha256(
                "cera.sequence_plan.v1",
                (decision.current_segment, decision.future_segments),
            ),
            mode=AdultCraftMode.ON,
            families=(AdultCraftFamily.GENERAL,),
            subfamilies=(),
            beat_requirements=(
                BeatCraftRequirementV3(
                    beat_id=beat.beat_id,
                    actor_id=beat.actor_id,
                    action_family="validated_current_beat",
                    object_concepts=(AdultCraftConcept.ANATOMY,),
                    axes=(AdultCraftAxis.DIRECT_VOCABULARY,),
                    channel_requirements=(
                        SceneChannelCraftNeedV3(
                            channel=RealizationChannel.NARRATION,
                            minimum_register=SpecificityRegister.DIRECT,
                            semantic_concepts=(AdultCraftConcept.ANATOMY,),
                            lexical_concepts=lexical_concepts,
                        ),
                    ),
                    priority=3,
                ),
            ),
            climax=OutcomeScope(
                OutcomeAuthorityState.ALLOWED,
                SegmentCommitment.NOT_SELECTED,
            ),
            aftermath=OutcomeScope(
                OutcomeAuthorityState.ALLOWED,
                SegmentCommitment.NOT_SELECTED,
            ),
            character_card_sections=(),
        )
        return request, decision, need

    def test_semantic_concept_selects_craft_without_creating_word_quota(self) -> None:
        request, decision, need = self._need()
        selected = AdultCraftSelector(self.catalog).select(
            need,
            decision,
            request_id=request.prepared_turn.request.request_id,
        )
        self.assertIn("concept:anatomy", selected.receipt.covered_requirements)
        self.assertNotIn("lexicon:anatomy", selected.receipt.covered_requirements)
        self.assertEqual(selected.specificity_contract.beat_requirements[0].terms, ())

    def test_only_explicit_lexical_subset_creates_word_quota(self) -> None:
        request, decision, need = self._need((AdultCraftConcept.ANATOMY,))
        selected = AdultCraftSelector(self.catalog).select(
            need,
            decision,
            request_id=request.prepared_turn.request.request_id,
        )
        self.assertIn("lexicon:anatomy", selected.receipt.covered_requirements)
        self.assertEqual(
            selected.specificity_contract.beat_requirements[0].terms[0].concept,
            AdultCraftConcept.ANATOMY,
        )

    def test_lexical_concepts_must_be_semantic_subset(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "subset"):
            SceneChannelCraftNeedV3(
                channel=RealizationChannel.PHYSIOLOGY,
                minimum_register=SpecificityRegister.DIRECT,
                semantic_concepts=(AdultCraftConcept.ANATOMY,),
                lexical_concepts=(AdultCraftConcept.FECES,),
            )


class V5ProviderContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.live = live_support.LiveShapedPipelineTests("runTest")
        self.live.setUp()
        self.addCleanup(self.live.doCleanups)

    def test_active_reasoner_schema_and_domain_enforce_lexical_subset(self) -> None:
        payload = json.loads(self.live.adult_case("v5-reasoner-v3")[4].output_text)
        payload["schema_version"] = CodexReasonerDraftV3.SCHEMA_VERSION
        for beat in payload["adult_craft_need"]["beat_requirements"]:
            for channel in beat["channel_requirements"]:
                channel["semantic_concepts"] = channel.pop("required_concepts")
                channel["lexical_concepts"] = []
        schema = codex_reasoner_draft_v3_json_schema()
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(payload)
        from_mapping(CodexReasonerDraftV3, payload)

        invalid = json.loads(json.dumps(payload))
        channel = invalid["adult_craft_need"]["beat_requirements"][0][
            "channel_requirements"
        ][0]
        channel["lexical_concepts"] = ["feces"]
        with self.assertRaisesRegex(
            ContractValidationError,
            "not_subset_of_semantic_concepts",
        ):
            validate_reasoner_draft_payload_semantics(invalid)
        with self.assertRaisesRegex(ContractValidationError, "subset"):
            from_mapping(CodexReasonerDraftV3, invalid)

    def test_legacy_composer_v3_remains_decodable_but_cannot_bypass_active_v6(self) -> None:
        support = deepseek_support.DeepSeekSceneComposerTests("runTest")
        support.setUp()
        self.addCleanup(support.doCleanups)
        request = support.request_with_context("v5-segments")
        old = support.response_for(request, "v5-segments")
        story_text = old["story_segments"][0]["text"]
        owner_by_authority = {
            str(value.beat_id): str(value.actor_id)
            for value in request.reasoner_outcome.decision.current_segment.ordered_beats
        }
        owner_by_authority.update(
            {
                str(value.source_unit_id): str(
                    request.prepared_turn.request.protected_user_id
                )
                for value in request.source_packet.units
            }
        )
        response = {
            "schema_version": DeepSeekCompositionDraftV3.SCHEMA_VERSION,
            "story_segments": [
                {"segment_key": "opening", "text": story_text},
            ],
            "source_coverage_segments": [
                {
                    "source_unit_id": value["source_unit_id"],
                    "segment_key": "opening",
                }
                for value in old["source_coverage"]
            ],
            "realization_segments": [
                {
                    "owner_id": owner_by_authority[value["authority_id"]],
                    "kind": value["kind"],
                    "segment_key": "opening",
                    "source_unit_id": (
                        value["authority_id"]
                        if value["authority_id"].startswith("source_unit:")
                        else None
                    ),
                    "beat_id": (
                        value["authority_id"]
                        if value["authority_id"].startswith("beat:")
                        else None
                    ),
                }
                for value in old["realization_segments"]
            ],
            "terminal_segment_key": "opening",
        }
        Draft202012Validator(deepseek_composition_draft_v3_json_schema()).validate(response)
        from_mapping(DeepSeekCompositionDraftV3, response)
        opener = deepseek_support.RecordingOpener(response)
        port = DeepSeekSceneComposerPort(
            DeepSeekChatTransport(
                deepseek_composer_candidate(),
                opener=opener,
                environment={"DEEPSEEK_API_KEY": "dummy"},
            )
        )
        with self.assertRaises(ComposerExecutionFailure) as caught:
            ComposerCoordinator().execute(request, port)
        self.assertEqual(
            caught.exception.safe_diagnostics,
            ("TYPED_DECODING_FAILED",),
        )
        self.assertEqual(len(opener.calls), 1)

    def test_unknown_segment_reference_fails_before_domain_acceptance(self) -> None:
        payload = {
            "schema_version": DeepSeekCompositionDraftV3.SCHEMA_VERSION,
            "story_segments": [{"segment_key": "only", "text": "Hana answers."}],
            "source_coverage_segments": [],
            "realization_segments": [
                {
                    "owner_id": "character:hana",
                    "kind": "dialogue",
                    "segment_key": "missing",
                    "source_unit_id": None,
                    "beat_id": "beat:current",
                }
            ],
            "terminal_segment_key": "only",
        }
        with self.assertRaisesRegex(ContractValidationError, "unknown story segment"):
            from_mapping(DeepSeekCompositionDraftV3, payload)


class V5FailureEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.support = live_support.LiveShapedPipelineTests("runTest")
        self.support.setUp()
        self.addCleanup(self.support.doCleanups)

    def test_adult_selection_failure_exports_reasoner_receipt_payload(self) -> None:
        args = self.support.adult_case("v5-adult-selection-evidence")
        pipeline = LiveShapedTurnPipeline(
            self.support.real.reasoner,
            ComposerContextAssembler(self.support.real.sandbox.service),
            ComposerCoordinator(),
            realization_verifier_port=self.support.pipeline.realization_verifier_port,
            stage_audit_journal=TurnStageAuditJournal(self.support.real.sandbox.store),
        )
        with self.assertRaises(LiveShapedTurnFailure) as caught:
            pipeline.execute(*args[:4])
        bundle = caught.exception.failure_evidence_bundle
        self.assertIsInstance(bundle, TurnFailureEvidenceBundleV3)
        self.assertEqual(bundle.safe_diagnostic_codes, ())
        payloads = {value.evidence_kind: value for value in bundle.retained_receipt_payloads}
        self.assertIn("scene_reasoner_receipt", payloads)
        self.assertNotIn("story_text", payloads["scene_reasoner_receipt"].payload_json)
        self.assertEqual(
            caught.exception.privacy_safe_receipt_payloads,
            bundle.retained_receipt_payloads,
        )
        retained = bundle.retained_receipt_payloads[0]
        malicious = json.loads(retained.payload_json)
        malicious["story_text"] = "must never enter operational evidence"
        with self.assertRaisesRegex(ContractValidationError, "protected content"):
            replace(
                retained,
                payload_json=canonical_json(malicious),
                payload_sha256=domain_sha256(
                    "cera.privacy_safe_receipt_payload.v1", malicious
                ),
            )

    def test_value_free_composer_diagnostics_survive_restart(self) -> None:
        args = self.support.ordinary_case("v5-safe-diagnostic-restart")
        prepared = args[0].prepared_turn
        journal = TurnStageAuditJournal(
            self.support.real.sandbox.store
        )
        bundle = journal.failed(
            prepared,
            "scene_composer",
            ErrorCode.COMPOSER_CONTRACT_INVALID,
            "scene composer failed before typed acceptance",
            input_sha256=args[0].request_sha256,
            output_sha256=None,
            external_provider_calls_observed=1,
            safe_diagnostic_codes=(
                "SOURCE_COVERAGE_SEQUENCE_MISMATCH",
                "BEAT_AUTHORITY_RELATION_MISMATCH",
            ),
        )
        reloaded = self.support.real.sandbox.store.get_turn_failure_evidence(
            bundle.failure_bundle_id
        )
        self.assertIsInstance(reloaded, TurnFailureEvidenceBundleV3)
        self.assertEqual(
            reloaded.safe_diagnostic_codes,
            bundle.safe_diagnostic_codes,
        )
        restart = journal.inspect_restart(prepared.request.request_id)
        self.assertFalse(restart.automatic_resume_permitted)
        self.assertEqual(
            restart.entries[-1].failure_bundle_id,
            bundle.failure_bundle_id,
        )
        with self.assertRaisesRegex(
            ContractValidationError,
            "value-free tokens",
        ):
            replace(
                bundle,
                safe_diagnostic_codes=("Mia private evidence",),
            )

    def test_rejected_candidate_review_is_separate_from_runtime_receipts(self) -> None:
        args = self.support.ordinary_case("v5-rejected-review")
        review_store = InMemoryRejectedCandidateReviewStore()
        pipeline = LiveShapedTurnPipeline(
            self.support.real.reasoner,
            ComposerContextAssembler(self.support.real.sandbox.service),
            ComposerCoordinator(),
            realization_verification_coordinator=(
                SceneRealizationVerificationCoordinator(review_store)
            ),
            realization_verifier_port=AnchoredProtectedUserRejectingVerifier(),
            stage_audit_journal=TurnStageAuditJournal(self.support.real.sandbox.store),
        )
        with self.assertRaises(LiveShapedTurnFailure) as caught:
            pipeline.execute(*args[:4])
        review_id, review_sha256 = caught.exception.rejected_candidate_review_handle
        review = review_store.get(review_id)
        self.assertEqual(review.review_sha256, review_sha256)
        self.assertTrue(review.excerpts)
        with tempfile.TemporaryDirectory() as directory:
            qualification_store = QualificationRejectedCandidateReviewStore(
                Path(directory) / "rejected_candidate_reviews"
            )
            qualification_store.retain(review)
            export_path = (
                Path(directory)
                / "rejected_candidate_reviews"
                / f"{review.review_sha256}.json"
            )
            exported = json.loads(export_path.read_text(encoding="utf-8"))
            self.assertEqual(
                exported["schema_version"],
                "cera.qualification_rejected_candidate_review_export.v1",
            )
            self.assertEqual(exported["review_sha256"], review.review_sha256)
            self.assertEqual(
                exported["access_scope"], "creator_local_qualification_review"
            )
            self.assertTrue(exported["runtime_receipt_forbidden"])
            self.assertFalse(exported["story_state_committed"])
            with self.assertRaises(FileExistsError):
                qualification_store.retain(review)
        bundle = caught.exception.failure_evidence_bundle
        self.assertFalse(bundle.retains_story_prose)
        self.assertIn(
            "rejected_candidate_review_artifact",
            {value.evidence_kind for value in bundle.retained_evidence},
        )
        self.assertNotIn(
            "rejected_candidate_review_artifact",
            {value.evidence_kind for value in bundle.retained_receipt_payloads},
        )
        self.assertTrue(
            all(
                '"story_text":' not in value.payload_json
                for value in bundle.retained_receipt_payloads
            )
        )


if __name__ == "__main__":
    unittest.main()
