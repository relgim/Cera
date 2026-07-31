"""Deterministic Phase 10 evaluation, review, and promotion assessments.

This module produces evidence and recommendations only. It cannot mutate story
authority, promote a route, or authorize deployment.
"""

from __future__ import annotations

from collections import Counter

from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId, deterministic_id
from cera.serialization import domain_sha256, text_sha256

from .models import (
    AssessmentStatus,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationFinding,
    EvaluationPartition,
    EvaluationRunReport,
    EvaluationSuiteManifest,
    EvidenceClass,
    FindingSeverity,
    HumanPreference,
    HumanReviewBallot,
    HumanReviewKey,
    HumanReviewPacket,
    HumanReviewSummary,
    PromotionAssessment,
    PromotionPolicy,
    RouteIdentity,
    RouteKind,
    TelemetryEvent,
)


def evaluation_result_sha256(result: EvaluationCaseResult) -> str:
    return domain_sha256("cera.evaluation_case_result.v1", result)


def evaluation_report_sha256(report: EvaluationRunReport) -> str:
    return domain_sha256("cera.evaluation_run_report.v1", _report_payload(report))


def human_review_summary_sha256(summary: HumanReviewSummary) -> str:
    return domain_sha256("cera.human_review_summary.v1", _summary_payload(summary))


def evaluate_run(
    manifest: EvaluationSuiteManifest,
    route: RouteIdentity,
    partition: EvaluationPartition,
    results: tuple[EvaluationCaseResult, ...],
    telemetry: tuple[TelemetryEvent, ...],
) -> EvaluationRunReport:
    """Validate a role/partition run and produce deterministic findings.

    The caller executes the route. This function only validates the sealed
    inputs and supplied observations. Provider-free evidence can never be
    mislabeled as live qualification.
    """

    selected = tuple(
        case
        for case in manifest.cases
        if case.role is route.role and case.partition is partition
    )
    if not selected:
        raise ContractValidationError("evaluation manifest has no cases for route and partition")
    _require_exact_case_results(selected, results)
    if len(telemetry) != len(selected):
        raise ContractValidationError("evaluation requires one telemetry event per case")

    case_by_id = {str(case.case_id): case for case in selected}
    findings: list[EvaluationFinding] = []
    passed_cases = 0
    route_hash = route.route_sha256

    for result in results:
        case = case_by_id[str(result.case_id)]
        if result.route_sha256 != route_hash:
            raise ContractValidationError("evaluation result route binding mismatch")
        expected_criteria = {criterion.criterion_id: criterion for criterion in case.criteria}
        observed = {observation.criterion_id: observation for observation in result.observations}
        if set(observed) != set(expected_criteria):
            raise ContractValidationError("evaluation observations do not match case criteria")
        _validate_evidence_class(route, result)
        failed = 0
        for criterion in case.criteria:
            observation = observed[criterion.criterion_id]
            if observation.passed:
                continue
            failed += 1
            finding_id = deterministic_id(
                IdKind.EVALUATION_FINDING,
                "phase10-evaluation-finding",
                f"{case.case_id}|{criterion.criterion_id}|{route_hash}|{partition.value}",
            )
            findings.append(
                EvaluationFinding(
                    finding_id=finding_id,
                    case_id=case.case_id,
                    criterion_id=criterion.criterion_id,
                    category=criterion.category,
                    severity=criterion.severity,
                    evidence_sha256=observation.evidence_sha256,
                    note=observation.note,
                )
            )
        if failed == 0:
            passed_cases += 1

    _validate_telemetry(route, results, telemetry)
    ordered_results = tuple(sorted(results, key=lambda value: str(value.case_id)))
    case_ids = tuple(value.case_id for value in ordered_results)
    result_hashes = tuple(evaluation_result_sha256(value) for value in ordered_results)
    report_key = domain_sha256(
        "cera.evaluation_run_key.v1",
        {
            "manifest_sha256": manifest.manifest_sha256,
            "route_sha256": route_hash,
            "partition": partition.value,
            "result_sha256": result_hashes,
        },
    )
    run_id = deterministic_id(IdKind.EVALUATION_RUN, "phase10-evaluation-run", report_key)
    base = {
        "schema_version": EvaluationRunReport.SCHEMA_VERSION,
        "run_id": run_id,
        "manifest_sha256": manifest.manifest_sha256,
        "route_sha256": route_hash,
        "role": route.role,
        "partition": partition,
        "case_ids": case_ids,
        "result_sha256": result_hashes,
        "findings": tuple(findings),
        "hard_failure_count": sum(
            finding.severity is FindingSeverity.HARD for finding in findings
        ),
        "passed_case_count": passed_cases,
        "external_provider_calls": sum(value.external_provider_calls for value in results),
        "evidence_only": route.route_kind is not RouteKind.LIVE_PROVIDER,
        "story_authority_writes": 0,
    }
    report_hash = domain_sha256("cera.evaluation_run_report.v1", base)
    return EvaluationRunReport(**base, report_sha256=report_hash)


def build_blinded_review_packet(
    case: EvaluationCase,
    evaluation_context: str,
    route_left: RouteIdentity,
    text_left: str,
    route_right: RouteIdentity,
    text_right: str,
    order_nonce: str,
) -> tuple[HumanReviewPacket, HumanReviewKey]:
    """Create a blinded A/B packet and a separately held unblinding key."""

    if route_left.role is not case.role or route_right.role is not case.role:
        raise ContractValidationError("review candidates must match the evaluation case role")
    if route_left.route_sha256 == route_right.route_sha256:
        raise ContractValidationError("matched review requires distinct routes")
    for value, label in (
        (evaluation_context, "evaluation context"),
        (text_left, "left candidate"),
        (text_right, "right candidate"),
        (order_nonce, "order nonce"),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ContractValidationError(f"{label} must be non-empty")

    nonce_hash = text_sha256(order_nonce)
    order_seed = domain_sha256(
        "cera.human_review_order.v1",
        {
            "case_id": case.case_id,
            "left_route": route_left.route_sha256,
            "right_route": route_right.route_sha256,
            "nonce_sha256": nonce_hash,
        },
    )
    if int(order_seed[-1], 16) % 2 == 0:
        route_a, text_a = route_left, text_left
        route_b, text_b = route_right, text_right
    else:
        route_a, text_a = route_right, text_right
        route_b, text_b = route_left, text_left

    commitment = domain_sha256(
        "cera.human_review_order_commitment.v1",
        {
            "case_id": case.case_id,
            "route_a_sha256": route_a.route_sha256,
            "route_b_sha256": route_b.route_sha256,
            "candidate_a_sha256": text_sha256(text_a),
            "candidate_b_sha256": text_sha256(text_b),
            "order_nonce_sha256": nonce_hash,
        },
    )
    packet_id = deterministic_id(
        IdKind.REVIEW_PACKET,
        "phase10-human-review",
        f"{case.case_id}|{commitment}",
    )
    packet = HumanReviewPacket(
        schema_version=HumanReviewPacket.SCHEMA_VERSION,
        packet_id=packet_id,
        case_id=case.case_id,
        evaluation_context=evaluation_context,
        evaluation_context_sha256=text_sha256(evaluation_context),
        candidate_a_text=text_a,
        candidate_a_sha256=text_sha256(text_a),
        candidate_b_text=text_b,
        candidate_b_sha256=text_sha256(text_b),
        order_commitment_sha256=commitment,
        provider_identity_exposed=False,
        private_evidence_included=False,
    )
    key = HumanReviewKey(
        packet_id=packet_id,
        route_a_sha256=route_a.route_sha256,
        route_b_sha256=route_b.route_sha256,
        order_nonce_sha256=nonce_hash,
    )
    return packet, key


def summarize_human_review(
    keys: tuple[HumanReviewKey, ...],
    ballots: tuple[HumanReviewBallot, ...],
    route_sha256: str,
) -> HumanReviewSummary:
    """Summarize blinded ballots from the perspective of one candidate route."""

    if not keys or not ballots:
        raise ContractValidationError("human review summary requires keys and ballots")
    key_by_packet = {str(key.packet_id): key for key in keys}
    if len(key_by_packet) != len(keys):
        raise ContractValidationError("human review keys contain duplicate packets")
    ballot_ids = {str(ballot.ballot_id) for ballot in ballots}
    if len(ballot_ids) != len(ballots):
        raise ContractValidationError("human review ballots contain duplicate IDs")
    ballot_packets = Counter(str(ballot.packet_id) for ballot in ballots)
    if set(ballot_packets) != set(key_by_packet):
        raise ContractValidationError("every review packet requires at least one ballot")
    if any(
        route_sha256 not in (key.route_a_sha256, key.route_b_sha256)
        for key in keys
    ):
        raise ContractValidationError("review route is absent from a matched packet")

    wins = losses = ties = reject_both = hard_defects = 0
    for ballot in ballots:
        key = key_by_packet[str(ballot.packet_id)]
        if ballot.preference is HumanPreference.TIE:
            ties += 1
        elif ballot.preference is HumanPreference.REJECT_BOTH:
            reject_both += 1
        else:
            preferred_route = (
                key.route_a_sha256
                if ballot.preference is HumanPreference.A
                else key.route_b_sha256
            )
            if preferred_route == route_sha256:
                wins += 1
            else:
                losses += 1
        hard_defects += len(ballot.hard_defect_categories)

    base = {
        "route_sha256": route_sha256,
        "matched_case_count": len(keys),
        "ballot_count": len(ballots),
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "reject_both": reject_both,
        "hard_defect_count": hard_defects,
        "blinded": True,
    }
    summary_hash = domain_sha256("cera.human_review_summary.v1", base)
    return HumanReviewSummary(**base, summary_sha256=summary_hash)


def assess_promotion(
    policy: PromotionPolicy,
    manifest: EvaluationSuiteManifest,
    route: RouteIdentity,
    reports: tuple[EvaluationRunReport, ...],
    human_review: HumanReviewSummary | None,
) -> PromotionAssessment:
    """Determine eligibility for creator review; never promote a route."""

    if policy.role is not route.role:
        raise ContractValidationError("promotion policy role does not match route")
    if not reports:
        raise ContractValidationError("promotion assessment requires run reports")
    for report in reports:
        if report.route_sha256 != route.route_sha256 or report.role is not route.role:
            raise ContractValidationError("promotion report route or role mismatch")
        if report.manifest_sha256 != manifest.manifest_sha256:
            raise ContractValidationError("promotion report manifest mismatch")
        if evaluation_report_sha256(report) != report.report_sha256:
            raise ContractValidationError("promotion report integrity mismatch")
    if human_review is not None:
        if human_review.route_sha256 != route.route_sha256:
            raise ContractValidationError("human review route mismatch")
        if human_review_summary_sha256(human_review) != human_review.summary_sha256:
            raise ContractValidationError("human review summary integrity mismatch")

    all_case_ids = [str(case_id) for report in reports for case_id in report.case_ids]
    if len(all_case_ids) != len(set(all_case_ids)):
        raise ContractValidationError("promotion reports overlap evaluation cases")
    evaluated_ids = set(all_case_ids)
    case_by_id = {str(case.case_id): case for case in manifest.cases}
    if not evaluated_ids.issubset(case_by_id):
        raise ContractValidationError("promotion report references an unknown evaluation case")
    evaluated_cases = tuple(case_by_id[value] for value in evaluated_ids)
    if any(case.role is not route.role for case in evaluated_cases):
        raise ContractValidationError("promotion report includes a different evaluation role")

    evaluated_count = len(evaluated_cases)
    holdout_count = sum(case.partition is EvaluationPartition.HOLDOUT for case in evaluated_cases)
    passed_count = sum(report.passed_case_count for report in reports)
    hard_count = sum(report.hard_failure_count for report in reports)
    pass_rate = (passed_count * 10_000 // evaluated_count) if evaluated_count else 0
    covered_tags = {tag for case in evaluated_cases for tag in case.risk_tags}
    blockers: list[str] = []

    if hard_count > policy.maximum_hard_failures:
        blockers.append("hard authority or safety defect recorded")
    if pass_rate < policy.minimum_pass_rate_basis_points:
        blockers.append("pass rate is below policy minimum")
    if evaluated_count < policy.minimum_case_count:
        blockers.append("evaluated case count is below policy minimum")
    if holdout_count < policy.minimum_holdout_case_count:
        blockers.append("holdout case count is below policy minimum")
    missing_tags = sorted(set(policy.required_risk_tags).difference(covered_tags))
    if missing_tags:
        blockers.append("required risk tags are missing: " + ", ".join(missing_tags))
    if human_review is None:
        blockers.append("matched blinded human review is missing")
    else:
        if human_review.matched_case_count < policy.minimum_human_case_count:
            blockers.append("human reviewed-case count is below policy minimum")
        if human_review.ballot_count < policy.minimum_human_ballots:
            blockers.append("human ballot count is below policy minimum")
        if human_review.hard_defect_count:
            blockers.append("human review recorded a hard defect")
        decisive_ballots = human_review.wins + human_review.losses
        human_win_rate = (
            human_review.wins * 10_000 // decisive_ballots if decisive_ballots else 0
        )
        if human_win_rate < policy.minimum_human_win_rate_basis_points:
            blockers.append("matched human preference is below policy minimum")

    live_proven = (
        route.route_kind is RouteKind.LIVE_PROVIDER
        and all(not report.evidence_only for report in reports)
    )
    if not live_proven:
        blockers.append("live-provider qualification has not been proven")

    human_quality_failed = False
    if human_review is not None:
        decisive_ballots = human_review.wins + human_review.losses
        human_win_rate = (
            human_review.wins * 10_000 // decisive_ballots if decisive_ballots else 0
        )
        human_quality_failed = (
            decisive_ballots == 0
            or human_win_rate < policy.minimum_human_win_rate_basis_points
        )
    if hard_count > 0 or (human_review is not None and human_review.hard_defect_count > 0):
        status = AssessmentStatus.NOT_ELIGIBLE
    elif pass_rate < policy.minimum_pass_rate_basis_points or human_quality_failed:
        status = AssessmentStatus.NOT_ELIGIBLE
    elif not live_proven:
        status = AssessmentStatus.OFFLINE_EVIDENCE_ONLY
    elif blockers:
        status = AssessmentStatus.PENDING_EVIDENCE
    else:
        status = AssessmentStatus.ELIGIBLE_FOR_CREATOR_REVIEW

    report_hashes = tuple(sorted(report.report_sha256 for report in reports))
    assessment_key = domain_sha256(
        "cera.promotion_assessment_key.v1",
        {
            "policy_id": policy.policy_id,
            "route_sha256": route.route_sha256,
            "run_report_sha256": report_hashes,
            "human_review_summary_sha256": (
                human_review.summary_sha256 if human_review is not None else None
            ),
        },
    )
    assessment_id = deterministic_id(
        IdKind.PROMOTION_ASSESSMENT,
        "phase10-promotion-assessment",
        assessment_key,
    )
    return PromotionAssessment(
        schema_version=PromotionAssessment.SCHEMA_VERSION,
        assessment_id=assessment_id,
        policy_id=policy.policy_id,
        route_sha256=route.route_sha256,
        run_report_sha256=report_hashes,
        human_review_summary_sha256=(
            human_review.summary_sha256 if human_review is not None else None
        ),
        status=status,
        blockers=tuple(blockers),
        evaluated_case_count=evaluated_count,
        holdout_case_count=holdout_count,
        hard_failure_count=hard_count,
        pass_rate_basis_points=pass_rate,
        live_provider_qualification_proven=live_proven,
        creator_approval_required=True,
        route_promoted=False,
    )


def _require_exact_case_results(
    cases: tuple[EvaluationCase, ...],
    results: tuple[EvaluationCaseResult, ...],
) -> None:
    expected = {str(case.case_id) for case in cases}
    actual = [str(result.case_id) for result in results]
    if len(actual) != len(set(actual)):
        raise ContractValidationError("evaluation results contain duplicate case IDs")
    if set(actual) != expected:
        raise ContractValidationError("evaluation results do not exactly cover selected cases")


def _validate_evidence_class(route: RouteIdentity, result: EvaluationCaseResult) -> None:
    if route.route_kind is RouteKind.LIVE_PROVIDER:
        if result.evidence_class is not EvidenceClass.LIVE_PROVIDER:
            raise ContractValidationError("live route result lacks live-provider evidence")
        if result.external_provider_calls < 1:
            raise ContractValidationError("live route result records no provider call")
    else:
        if result.evidence_class is EvidenceClass.LIVE_PROVIDER:
            raise ContractValidationError("offline route cannot claim live-provider evidence")
        if result.external_provider_calls != 0:
            raise ContractValidationError("offline route cannot record provider calls")


def _validate_telemetry(
    route: RouteIdentity,
    results: tuple[EvaluationCaseResult, ...],
    telemetry: tuple[TelemetryEvent, ...],
) -> None:
    if len({str(event.telemetry_event_id) for event in telemetry}) != len(telemetry):
        raise ContractValidationError("evaluation telemetry contains duplicate IDs")
    if any(event.route_sha256 != route.route_sha256 for event in telemetry):
        raise ContractValidationError("evaluation telemetry route mismatch")
    metric_pairs = (
        (sum(event.provider_calls for event in telemetry), sum(result.external_provider_calls for result in results)),
        (sum(event.duration_ms for event in telemetry), sum(result.latency_ms for result in results)),
        (sum(event.input_tokens for event in telemetry), sum(result.input_tokens for result in results)),
        (sum(event.output_tokens for event in telemetry), sum(result.output_tokens for result in results)),
        (sum(event.cost_microusd for event in telemetry), sum(result.cost_microusd for result in results)),
    )
    if any(left != right for left, right in metric_pairs):
        raise ContractValidationError("evaluation telemetry counters do not bind to results")


def _report_payload(report: EvaluationRunReport) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "run_id": report.run_id,
        "manifest_sha256": report.manifest_sha256,
        "route_sha256": report.route_sha256,
        "role": report.role,
        "partition": report.partition,
        "case_ids": report.case_ids,
        "result_sha256": report.result_sha256,
        "findings": report.findings,
        "hard_failure_count": report.hard_failure_count,
        "passed_case_count": report.passed_case_count,
        "external_provider_calls": report.external_provider_calls,
        "evidence_only": report.evidence_only,
        "story_authority_writes": report.story_authority_writes,
    }


def _summary_payload(summary: HumanReviewSummary) -> dict[str, object]:
    return {
        "route_sha256": summary.route_sha256,
        "matched_case_count": summary.matched_case_count,
        "ballot_count": summary.ballot_count,
        "wins": summary.wins,
        "losses": summary.losses,
        "ties": summary.ties,
        "reject_both": summary.reject_both,
        "hard_defect_count": summary.hard_defect_count,
        "blinded": summary.blinded,
    }
