from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from cera.errors import ContractValidationError
from cera.evaluation import (
    AssessmentStatus,
    DeploymentReadinessInput,
    DeploymentReadinessStatus,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationCriterion,
    EvaluationPartition,
    EvaluationRole,
    EvaluationSuiteManifest,
    EvidenceClass,
    FindingCategory,
    FindingSeverity,
    HumanDimensionScore,
    HumanPreference,
    HumanReviewBallot,
    PromotionPolicy,
    RealGenesisSandbox,
    RouteIdentity,
    RouteKind,
    TelemetryEvent,
    assess_deployment_readiness,
    assess_promotion,
    build_blinded_review_packet,
    evaluate_run,
    summarize_human_review,
)
from cera.ids import IdKind, TypedId
from cera.serialization import canonical_json, text_sha256
from cera.registry import build_schema_registry


ROOT = Path(__file__).resolve().parents[1]


class EvaluationFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calibration = self._case("calibration", EvaluationPartition.CALIBRATION)
        self.holdout = self._case("holdout", EvaluationPartition.HOLDOUT)
        self.manifest = self._manifest((self.calibration, self.holdout))
        self.offline_route = self._route(RouteKind.SCRIPTED_FAKE, "offline")
        self.live_route = self._route(RouteKind.LIVE_PROVIDER, "live")

    def _case(self, suffix: str, partition: EvaluationPartition) -> EvaluationCase:
        return EvaluationCase(
            schema_version=EvaluationCase.SCHEMA_VERSION,
            case_id=TypedId(IdKind.EVALUATION_CASE, suffix),
            suite_id="reasoner-gate",
            suite_version="1",
            role=EvaluationRole.SCENE_REASONER,
            partition=partition,
            input_sha256=text_sha256(f"input-{suffix}"),
            expected_contract_sha256=text_sha256(f"expected-{suffix}"),
            risk_tags=("authority", "quality"),
            criteria=(
                EvaluationCriterion(
                    criterion_id="authority",
                    category=FindingCategory.AUTHORITY,
                    severity=FindingSeverity.HARD,
                    description="Does not cross creator authority.",
                ),
                EvaluationCriterion(
                    criterion_id="quality",
                    category=FindingCategory.QUALITY,
                    severity=FindingSeverity.MAJOR,
                    description="Produces the expected decision contract.",
                ),
            ),
            provenance_sha256=(text_sha256(f"provenance-{suffix}"),),
            sealed=partition is EvaluationPartition.HOLDOUT,
        )

    def _manifest(self, cases: tuple[EvaluationCase, ...]) -> EvaluationSuiteManifest:
        return EvaluationSuiteManifest(
            schema_version=EvaluationSuiteManifest.SCHEMA_VERSION,
            suite_id="reasoner-gate",
            suite_version="1",
            cases=cases,
            excluded_craft_asset_sha256=(text_sha256("adult-ex-craft-asset"),),
            required_roles=(EvaluationRole.SCENE_REASONER,),
            required_risk_tags=("authority", "quality"),
            minimum_holdout_cases=1,
            provider_free_authoring=True,
        )

    def _route(self, kind: RouteKind, suffix: str) -> RouteIdentity:
        live = kind is RouteKind.LIVE_PROVIDER
        return RouteIdentity(
            role=EvaluationRole.SCENE_REASONER,
            route_kind=kind,
            adapter_id=f"adapter-{suffix}",
            model_name="provider-model" if live else None,
            model_revision="2026-07" if live else None,
            prompt_version="reasoner-v1",
            transport_version="port-v1",
            configuration_sha256=text_sha256(f"config-{suffix}"),
        )

    def _result(
        self,
        case: EvaluationCase,
        route: RouteIdentity,
        *,
        authority_passed: bool = True,
    ) -> EvaluationCaseResult:
        live = route.route_kind is RouteKind.LIVE_PROVIDER
        return EvaluationCaseResult(
            case_id=case.case_id,
            route_sha256=route.route_sha256,
            output_sha256=text_sha256(f"output-{case.case_id}-{route.adapter_id}"),
            observations=tuple(
                self._observation(criterion.criterion_id, authority_passed)
                if criterion.criterion_id == "authority"
                else self._observation(criterion.criterion_id, True)
                for criterion in case.criteria
            ),
            evidence_class=(
                EvidenceClass.LIVE_PROVIDER if live else EvidenceClass.SYNTHETIC_CONTRACT
            ),
            external_provider_calls=1 if live else 0,
            latency_ms=50,
            input_tokens=100 if live else 0,
            output_tokens=50 if live else 0,
            cost_microusd=25 if live else 0,
            authority_store_writes=0,
        )

    @staticmethod
    def _observation(criterion_id: str, passed: bool):
        from cera.evaluation import CriterionObservation

        return CriterionObservation(
            criterion_id=criterion_id,
            passed=passed,
            evidence_sha256=text_sha256(f"evidence-{criterion_id}-{passed}"),
            note="criterion passed" if passed else "boundary defect",
        )

    def _telemetry(
        self,
        case: EvaluationCase,
        route: RouteIdentity,
    ) -> TelemetryEvent:
        live = route.route_kind is RouteKind.LIVE_PROVIDER
        return TelemetryEvent(
            schema_version=TelemetryEvent.SCHEMA_VERSION,
            telemetry_event_id=TypedId(IdKind.TELEMETRY_EVENT, f"telemetry-{case.case_id.value}"),
            trace_id=TypedId(IdKind.TRACE, f"trace-{case.case_id.value}"),
            stage="scene_reasoner_evaluation",
            route_sha256=route.route_sha256,
            outcome="completed",
            duration_ms=50,
            provider_calls=1 if live else 0,
            input_tokens=100 if live else 0,
            output_tokens=50 if live else 0,
            cost_microusd=25 if live else 0,
            error_code=None,
            raw_source_included=False,
            story_prose_included=False,
            private_evidence_included=False,
            prompt_included=False,
            secret_included=False,
        )

    def _report(
        self,
        case: EvaluationCase,
        route: RouteIdentity,
        *,
        authority_passed: bool = True,
    ):
        return evaluate_run(
            self.manifest,
            route,
            case.partition,
            (self._result(case, route, authority_passed=authority_passed),),
            (self._telemetry(case, route),),
        )

    def _policy(self) -> PromotionPolicy:
        return PromotionPolicy(
            schema_version=PromotionPolicy.SCHEMA_VERSION,
            policy_id="scene-reasoner-v1",
            role=EvaluationRole.SCENE_REASONER,
            minimum_case_count=2,
            minimum_holdout_case_count=1,
            minimum_human_case_count=1,
            minimum_human_ballots=1,
            minimum_human_win_rate_basis_points=5_000,
            minimum_pass_rate_basis_points=10_000,
            required_risk_tags=("authority", "quality"),
            maximum_hard_failures=0,
        )

    def _review_summary(
        self,
        route: RouteIdentity,
        rival: RouteIdentity,
        *,
        route_wins: bool = True,
    ):
        _, key = build_blinded_review_packet(
            self.holdout,
            "Presentation-neutral evaluation context.",
            route,
            "Candidate one.",
            rival,
            "Candidate two.",
            "sealed-order-nonce",
        )
        route_preference = (
            HumanPreference.A if key.route_a_sha256 == route.route_sha256 else HumanPreference.B
        )
        preference = (
            route_preference
            if route_wins
            else (HumanPreference.B if route_preference is HumanPreference.A else HumanPreference.A)
        )
        ballot = HumanReviewBallot(
            schema_version=HumanReviewBallot.SCHEMA_VERSION,
            ballot_id=TypedId(IdKind.REVIEW_BALLOT, "ballot-1"),
            packet_id=key.packet_id,
            reviewer_pseudonym_sha256=text_sha256("reviewer-1"),
            preference=preference,
            scores=(HumanDimensionScore("character fidelity", 4, 3),),
            hard_defect_categories=(),
            provider_identity_seen=False,
        )
        return summarize_human_review((key,), (ballot,), route.route_sha256)

    def test_holdout_must_be_sealed(self) -> None:
        with self.assertRaises(ContractValidationError):
            replace(self.holdout, sealed=False)

    def test_evaluation_schemas_are_in_default_registry(self) -> None:
        registry = build_schema_registry()
        for version in (
            EvaluationCase.SCHEMA_VERSION,
            EvaluationSuiteManifest.SCHEMA_VERSION,
            TelemetryEvent.SCHEMA_VERSION,
            PromotionPolicy.SCHEMA_VERSION,
        ):
            self.assertIn(version, registry.versions)

    def test_manifest_rejects_craft_asset_contamination(self) -> None:
        contaminated = replace(
            self.holdout,
            provenance_sha256=(text_sha256("adult-ex-craft-asset"),),
        )
        with self.assertRaises(ContractValidationError):
            self._manifest((self.calibration, contaminated))

    def test_evaluation_run_is_reproducible_and_offline_evidence_only(self) -> None:
        first = self._report(self.calibration, self.offline_route)
        second = self._report(self.calibration, self.offline_route)
        self.assertEqual(first, second)
        self.assertTrue(first.evidence_only)
        self.assertEqual(first.external_provider_calls, 0)
        self.assertEqual(first.story_authority_writes, 0)

    def test_evaluation_rejects_missing_observation(self) -> None:
        result = self._result(self.calibration, self.offline_route)
        with self.assertRaises(ContractValidationError):
            evaluate_run(
                self.manifest,
                self.offline_route,
                EvaluationPartition.CALIBRATION,
                (replace(result, observations=result.observations[:1]),),
                (self._telemetry(self.calibration, self.offline_route),),
            )

    def test_offline_route_cannot_claim_live_evidence(self) -> None:
        result = replace(
            self._result(self.calibration, self.offline_route),
            evidence_class=EvidenceClass.LIVE_PROVIDER,
        )
        with self.assertRaises(ContractValidationError):
            evaluate_run(
                self.manifest,
                self.offline_route,
                EvaluationPartition.CALIBRATION,
                (result,),
                (self._telemetry(self.calibration, self.offline_route),),
            )

    def test_live_route_requires_live_call_receipt(self) -> None:
        result = replace(
            self._result(self.calibration, self.live_route),
            external_provider_calls=0,
        )
        with self.assertRaises(ContractValidationError):
            evaluate_run(
                self.manifest,
                self.live_route,
                EvaluationPartition.CALIBRATION,
                (result,),
                (self._telemetry(self.calibration, self.live_route),),
            )

    def test_telemetry_rejects_protected_content(self) -> None:
        event = self._telemetry(self.calibration, self.offline_route)
        with self.assertRaises(ContractValidationError):
            replace(event, private_evidence_included=True)

    def test_blinded_review_packet_exposes_no_route_identity(self) -> None:
        rival = self._route(RouteKind.SCRIPTED_FAKE, "rival")
        packet, key = build_blinded_review_packet(
            self.holdout,
            "Safe shared context.",
            self.offline_route,
            "Alpha prose.",
            rival,
            "Beta prose.",
            "nonce",
        )
        public_payload = canonical_json(packet)
        self.assertNotIn(self.offline_route.adapter_id, public_payload)
        self.assertNotIn(rival.adapter_id, public_payload)
        self.assertNotIn(self.offline_route.route_sha256, public_payload)
        self.assertFalse(packet.provider_identity_exposed)
        self.assertEqual(key.packet_id, packet.packet_id)

    def test_human_review_summary_unblinds_only_against_separate_key(self) -> None:
        rival = self._route(RouteKind.SCRIPTED_FAKE, "rival")
        summary = self._review_summary(self.offline_route, rival)
        self.assertEqual(summary.wins, 1)
        self.assertEqual(summary.losses, 0)
        self.assertTrue(summary.blinded)

    def test_hard_defect_cannot_average_away(self) -> None:
        rival = self._route(RouteKind.SCRIPTED_FAKE, "rival")
        assessment = assess_promotion(
            self._policy(),
            self.manifest,
            self.offline_route,
            (
                self._report(self.calibration, self.offline_route),
                self._report(self.holdout, self.offline_route, authority_passed=False),
            ),
            self._review_summary(self.offline_route, rival),
        )
        self.assertEqual(assessment.status, AssessmentStatus.NOT_ELIGIBLE)
        self.assertEqual(assessment.hard_failure_count, 1)
        self.assertFalse(assessment.route_promoted)

    def test_all_pass_offline_route_remains_evidence_only(self) -> None:
        rival = self._route(RouteKind.SCRIPTED_FAKE, "rival")
        assessment = assess_promotion(
            self._policy(),
            self.manifest,
            self.offline_route,
            (
                self._report(self.calibration, self.offline_route),
                self._report(self.holdout, self.offline_route),
            ),
            self._review_summary(self.offline_route, rival),
        )
        self.assertEqual(assessment.status, AssessmentStatus.OFFLINE_EVIDENCE_ONLY)
        self.assertFalse(assessment.live_provider_qualification_proven)
        self.assertTrue(assessment.creator_approval_required)

    def test_live_route_can_only_become_eligible_for_creator_review(self) -> None:
        rival = self._route(RouteKind.LIVE_PROVIDER, "live-rival")
        assessment = assess_promotion(
            self._policy(),
            self.manifest,
            self.live_route,
            (
                self._report(self.calibration, self.live_route),
                self._report(self.holdout, self.live_route),
            ),
            self._review_summary(self.live_route, rival),
        )
        self.assertEqual(assessment.status, AssessmentStatus.ELIGIBLE_FOR_CREATOR_REVIEW)
        self.assertFalse(assessment.route_promoted)

    def test_live_route_that_loses_human_review_is_not_eligible(self) -> None:
        rival = self._route(RouteKind.LIVE_PROVIDER, "live-rival")
        assessment = assess_promotion(
            self._policy(),
            self.manifest,
            self.live_route,
            (
                self._report(self.calibration, self.live_route),
                self._report(self.holdout, self.live_route),
            ),
            self._review_summary(self.live_route, rival, route_wins=False),
        )
        self.assertEqual(assessment.status, AssessmentStatus.NOT_ELIGIBLE)
        self.assertIn("matched human preference is below policy minimum", assessment.blockers)

    def test_tampered_report_cannot_support_promotion(self) -> None:
        report = self._report(self.calibration, self.offline_route)
        with self.assertRaises(ContractValidationError):
            assess_promotion(
                self._policy(),
                self.manifest,
                self.offline_route,
                (replace(report, report_sha256="0" * 64),),
                None,
            )

    def test_current_deployment_readiness_is_blocked(self) -> None:
        value = DeploymentReadinessInput(
            promotion_assessment_ids=(TypedId(IdKind.PROMOTION_ASSESSMENT, "reasoner"),),
            required_roles=(EvaluationRole.SCENE_REASONER, EvaluationRole.SCENE_COMPOSER),
            qualified_roles=(),
            matched_human_review_complete=False,
            telemetry_privacy_review_complete=True,
            production_world_authorized=False,
            sillytavern_integration_authorized=False,
            credential_storage_approved=False,
            creator_fresh_chat_accepted=False,
            deployment_authorized=False,
        )
        report = assess_deployment_readiness(value)
        self.assertEqual(report.status, DeploymentReadinessStatus.BLOCKED)
        self.assertFalse(report.deployment_executed)
        self.assertIn("deployment is not authorized", report.blockers)

    def test_all_readiness_gates_still_require_creator_decision(self) -> None:
        roles = (EvaluationRole.SCENE_REASONER, EvaluationRole.SCENE_COMPOSER)
        value = DeploymentReadinessInput(
            promotion_assessment_ids=(TypedId(IdKind.PROMOTION_ASSESSMENT, "all-routes"),),
            required_roles=roles,
            qualified_roles=roles,
            matched_human_review_complete=True,
            telemetry_privacy_review_complete=True,
            production_world_authorized=True,
            sillytavern_integration_authorized=True,
            credential_storage_approved=True,
            creator_fresh_chat_accepted=True,
            deployment_authorized=True,
        )
        report = assess_deployment_readiness(value)
        self.assertEqual(
            report.status,
            DeploymentReadinessStatus.ELIGIBLE_FOR_CREATOR_DECISION,
        )
        self.assertTrue(report.creator_decision_required)
        self.assertFalse(report.deployment_executed)

    def test_real_genesis_provenance_is_usable_without_persistent_database(self) -> None:
        with RealGenesisSandbox.create(ROOT) as sandbox:
            database_path = sandbox.database_path
            case = replace(
                self.calibration,
                provenance_sha256=(
                    sandbox.compiled.manifest_sha256,
                    sandbox.compiled.bundle_sha256,
                ),
            )
            self.assertEqual(len(case.provenance_sha256), 2)
            self.assertTrue(database_path.exists())
            self.assertEqual(sandbox.installed.receipt.outcome, "committed")
        self.assertFalse(database_path.exists())


if __name__ == "__main__":
    unittest.main()
