from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator

from cera.composer import (
    ComposerContextAssembler,
    ComposerCoordinator,
    ComposerExecutionFailure,
    DeepSeekSceneComposerPort,
)
from cera.composer.deepseek import QuoteAnchor, _resolve_anchor
from cera.providers import DeepSeekChatTransport, deepseek_composer_candidate
from cera.reasoner import (
    ReasonerDraftSemanticError,
    codex_reasoner_draft_v2_json_schema,
    validate_reasoner_draft_payload_semantics,
)
from cera.realization import (
    RealizationVerifierAdapterRole,
    RealizationVerificationStatus,
    RealizationViolationCode,
    SceneRealizationVerificationDraft,
    SceneRealizationVerifierCall,
    SceneRealizationViolationFinding,
    SceneRealizationVerifierPort,
)
from cera.runtime import LiveShapedTurnFailure, LiveShapedTurnPipeline, TurnStageAuditJournal
from cera.serialization import domain_sha256, text_sha256

import tests.test_live_shaped_pipeline as live_support
from tests.provider_fakes import OfflineDeepSeekChatTransport
from scripts.run_live_story_qualification import QualificationCase, failure_record


ROOT = Path(__file__).resolve().parents[1]


class MissingBoundaryVerifier(SceneRealizationVerifierPort):
    adapter_version = "cera.test.missing_boundary_verifier.v1"
    qualification_eligible = False

    def verify(self, request):
        return _test_call(
            self,
            SceneRealizationVerificationDraft(
                status=RealizationVerificationStatus.ACCEPTED,
                verified_beat_ids=request.expected_beat_ids,
                verified_participant_ids=request.selected_participant_ids,
                violation_codes=(),
                verified_boundary_checks=(),
            ),
        )


class InvalidOccurrenceOpener(live_support.PacketDrivenDeepSeekOpener):
    def __call__(self, request, *, timeout):
        response = super().__call__(request, timeout=timeout)
        payload = response.payload
        output = json.loads(payload["choices"][0]["message"]["content"])
        # Active v4 uses Python-derived segment offsets rather than provider
        # quote occurrences. Mutate a v4 structural reference after a complete
        # provider response so the safe provider receipt must be retained.
        output["terminal_segment_key"] = "unknown_terminal_segment"
        payload["choices"][0]["message"]["content"] = json.dumps(output)
        return live_support.FakeHTTPResponse(payload)


class AnchoredProtectedUserRejectingVerifier(SceneRealizationVerifierPort):
    adapter_version = "cera.test.anchored_protected_user_rejector.v1"
    qualification_eligible = False

    def verify(self, request):
        start = 0
        end = min(12, len(request.story_text))
        code = RealizationViolationCode.PROTECTED_USER_UNSUPPLIED_REALIZATION.value
        return _test_call(
            self,
            SceneRealizationVerificationDraft(
                status=RealizationVerificationStatus.REJECTED,
                verified_beat_ids=(),
                verified_participant_ids=(),
                violation_codes=(code,),
                violation_findings=(
                    SceneRealizationViolationFinding(
                        code=code,
                        start=start,
                        end=end,
                        text_sha256=text_sha256(request.story_text[start:end]),
                    ),
                ),
            ),
        )


def _test_call(port, draft):
    return SceneRealizationVerifierCall(
        draft=draft,
        adapter_role=RealizationVerifierAdapterRole.SCRIPTED_FAKE,
        adapter_version=port.adapter_version,
        adapter_evidence_id=port.adapter_version,
        adapter_evidence_sha256=domain_sha256(
            "cera.test.realization_verifier_call.v1",
            {"adapter": port.adapter_version, "draft": draft},
        ),
        qualification_eligible=False,
        external_provider_calls=0,
    )


class V4ReasonerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.support = live_support.LiveShapedPipelineTests("runTest")
        self.support.setUp()
        self.addCleanup(self.support.doCleanups)

    def test_each_decision_ready_required_field_has_safe_field_diagnostic(self) -> None:
        runner = self.support.ordinary_case("v4-status-diagnostics")[4]
        valid = json.loads(runner.output_text)
        mutations = {
            "route": None,
            "scene_intent": None,
            "responding_npc_ids": [],
            "floor_owner_id": None,
            "participation": [],
            "character_moves": [],
            "current_beats": [],
            "stop_before": None,
        }
        for field_name, replacement in mutations.items():
            with self.subTest(field_name=field_name):
                payload = json.loads(json.dumps(valid))
                payload[field_name] = replacement
                with self.assertRaises(ReasonerDraftSemanticError) as caught:
                    validate_reasoner_draft_payload_semantics(payload)
                self.assertIn(
                    f"{field_name}:required_for_decision_ready",
                    caught.exception.diagnostics,
                )
                self.assertNotIn(str(valid.get(field_name)), str(caught.exception))

    def test_duplicate_adult_concepts_report_path_without_values(self) -> None:
        runner = self.support.adult_case("v4-duplicate-diagnostic")[4]
        payload = json.loads(runner.output_text)
        concepts = payload["adult_craft_need"]["beat_requirements"][0][
            "channel_requirements"
        ][0]["required_concepts"]
        concepts.append(concepts[0])
        with self.assertRaises(ReasonerDraftSemanticError) as caught:
            validate_reasoner_draft_payload_semantics(payload)
        diagnostic = (
            "adult_craft_need.beat_requirements[0].channel_requirements[0]."
            "required_concepts:duplicate_items"
        )
        self.assertIn(diagnostic, caught.exception.diagnostics)
        self.assertNotIn(concepts[0], str(caught.exception))

    def test_provider_schema_teaches_uniqueness_without_unsupported_keyword(self) -> None:
        schema = codex_reasoner_draft_v2_json_schema()
        Draft202012Validator.check_schema(schema)
        serialized = json.dumps(schema)
        self.assertNotIn('"uniqueItems"', serialized)
        required_concepts = schema["properties"]["adult_craft_need"]["anyOf"][0][
            "properties"
        ]["beat_requirements"]["items"]["properties"]["channel_requirements"][
            "items"
        ]["oneOf"][1]["properties"]["required_concepts"]
        self.assertIn("distinct", required_concepts["description"])


class V4ComposerAndEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.support = live_support.LiveShapedPipelineTests("runTest")
        self.support.setUp()
        self.addCleanup(self.support.doCleanups)

    def test_python_derives_anchor_occurrence_and_rejects_ambiguity(self) -> None:
        self.assertEqual(_resolve_anchor("Hana answers once.", QuoteAnchor("answers", 0)), (5, 12))
        with self.assertRaisesRegex(Exception, "ambiguous"):
            _resolve_anchor("Hana nods; Hana nods.", QuoteAnchor("Hana", 0))
        with self.assertRaisesRegex(Exception, "zero compatibility sentinel"):
            QuoteAnchor("Hana", 1)

    def test_composer_failure_exports_all_prior_safe_receipt_handles(self) -> None:
        args = self.support.ordinary_case("v4-receipt-export")
        bad_opener = InvalidOccurrenceOpener()
        bad_composer = DeepSeekSceneComposerPort(
            OfflineDeepSeekChatTransport(
                deepseek_composer_candidate(),
                opener=bad_opener,
                environment={"DEEPSEEK_API_KEY": "dummy"},
                external_provider_boundary=False,
            )
        )
        journal = TurnStageAuditJournal(self.support.real.sandbox.store)
        pipeline = LiveShapedTurnPipeline(
            self.support.real.reasoner,
            ComposerContextAssembler(self.support.real.sandbox.service),
            ComposerCoordinator(),
            realization_verifier_port=self.support.pipeline.realization_verifier_port,
            stage_audit_journal=journal,
        )
        with self.assertRaises(ComposerExecutionFailure) as caught:
            pipeline.execute(args[0], args[1], args[2], bad_composer)
        bundle = caught.exception.failure_evidence_bundle
        self.assertIsNotNone(bundle)
        kinds = {value.evidence_kind for value in bundle.retained_evidence}
        self.assertTrue(
            {
                "scene_reasoner_receipt",
                "reasoner_provider_call_receipt",
                "mcp_bridge_receipt",
                "composer_context_assembly_receipt",
                "provider_call_receipt",
                "evidence_lookup_receipt",
            }.issubset(kinds)
        )
        restarted = self.support.real.sandbox.store.turn_failure_evidence_bundles(
            args[0].prepared_turn.request.request_id
        )
        self.assertEqual(restarted, (bundle,))
        self.assertEqual(caught.exception.retained_evidence_handles, bundle.retained_evidence)
        exported = failure_record(
            QualificationCase(
                case_id="provider-free-receipt-export",
                title="Provider-free receipt export",
                responders=("Hana",),
                source_text="Synthetic non-live source.",
            ),
            caught.exception,
            "provider-free-v4-diagnostic",
        )
        self.assertEqual(
            exported["safe_failure_evidence"]["stage_failure_bundle"][
                "failure_bundle_id"
            ],
            str(bundle.failure_bundle_id),
        )
        self.assertEqual(
            len(
                exported["safe_failure_evidence"][
                    "all_prior_stage_receipt_handles"
                ]
            ),
            len(bundle.retained_evidence),
        )

    def test_protected_user_boundary_cannot_be_accepted_without_semantic_check(self) -> None:
        args = self.support.ordinary_case("v4-protected-user-boundary")
        journal = TurnStageAuditJournal(self.support.real.sandbox.store)
        pipeline = LiveShapedTurnPipeline(
            self.support.real.reasoner,
            ComposerContextAssembler(self.support.real.sandbox.service),
            ComposerCoordinator(),
            realization_verifier_port=MissingBoundaryVerifier(),
            stage_audit_journal=journal,
        )
        with self.assertRaises(LiveShapedTurnFailure) as caught:
            pipeline.execute(*args[:4])
        self.assertIn("required semantic boundary", str(caught.exception))
        bundle = caught.exception.failure_evidence_bundle
        self.assertIsNotNone(bundle)
        self.assertFalse(bundle.story_state_committed)
        self.assertFalse(bundle.retains_story_prose)

    def test_anchored_protected_user_semantic_rejection_retains_safe_receipt(self) -> None:
        args = self.support.ordinary_case("v4-protected-user-finding")
        pipeline = LiveShapedTurnPipeline(
            self.support.real.reasoner,
            ComposerContextAssembler(self.support.real.sandbox.service),
            ComposerCoordinator(),
            realization_verifier_port=AnchoredProtectedUserRejectingVerifier(),
            stage_audit_journal=TurnStageAuditJournal(self.support.real.sandbox.store),
        )
        with self.assertRaises(LiveShapedTurnFailure) as caught:
            pipeline.execute(*args[:4])
        bundle = caught.exception.failure_evidence_bundle
        self.assertIsNotNone(bundle)
        handles = {value.evidence_kind for value in bundle.retained_evidence}
        self.assertIn("scene_realization_verification_receipt", handles)
        self.assertFalse(bundle.retains_story_prose)


if __name__ == "__main__":
    unittest.main()
