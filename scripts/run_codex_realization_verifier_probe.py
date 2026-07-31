"""Run one non-story Sol-medium semantic-verifier probe.

The probe asks the verifier to detect one protected-user action that is present
in a synthetic candidate but absent from the supplied protected-user source.
It makes exactly one one-shot Codex call, writes no story state, never retains
the prompt or raw provider output, and refuses to overwrite prior evidence.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

from cera.composer import RealizationKind
from cera.contracts import BeatState
from cera.errors import ContractValidationError
from cera.ids import IdKind, deterministic_id
from cera.providers import (
    CodexSDKTransport,
    ProviderSchemaDialect,
    ProviderTransportError,
    codex_realization_verifier_candidate,
    project_provider_output_schema,
)
from cera.realization import (
    CodexSceneRealizationVerifierPort,
    ProtectedUserRealizationClaim,
    ProtectedUserRealizationAuthority,
    RealizationBoundaryCheck,
    RealizationViolationCode,
    SceneRealizationBeatExpectation,
    SceneRealizationVerificationCoordinator,
    SceneRealizationVerificationFailure,
    SceneRealizationVerificationRequest,
    codex_realization_verifier_draft_json_schema,
)
from cera.serialization import canonical_json, text_sha256, to_primitive


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    ROOT
    / "evaluation"
    / "evidence"
    / "codex_realization_verifier_probe_2026-07-29_v2"
)


def build_probe_request() -> SceneRealizationVerificationRequest:
    story = 'Guide nodded. Ted said, "Hello." Ted opened the locked drawer.'
    return SceneRealizationVerificationRequest(
        schema_version=SceneRealizationVerificationRequest.SCHEMA_VERSION,
        request_id=deterministic_id(
            IdKind.REQUEST,
            "cera.non_story_realization_verifier_probe.v1",
            "request",
        ),
        branch_id=deterministic_id(
            IdKind.BRANCH,
            "cera.non_story_realization_verifier_probe.v1",
            "branch",
        ),
        generation_id=deterministic_id(
            IdKind.GENERATION,
            "cera.non_story_realization_verifier_probe.v1",
            "generation",
        ),
        candidate_sha256=text_sha256(story),
        story_text=story,
        expected_beats=(
            SceneRealizationBeatExpectation(
                beat_id=deterministic_id(
                    IdKind.BEAT,
                    "cera.non_story_realization_verifier_probe.v1",
                    "guide-nods",
                ),
                actor_id=deterministic_id(
                    IdKind.CHARACTER,
                    "cera.non_story_realization_verifier_probe.v1",
                    "guide",
                ),
                neutral_event="The guide nods visibly.",
                required_state=BeatState.COMPLETED,
            ),
        ),
        selected_participant_ids=(
            deterministic_id(
                IdKind.CHARACTER,
                "cera.non_story_realization_verifier_probe.v1",
                "guide",
            ),
        ),
        protected_user_id=deterministic_id(
            IdKind.CHARACTER,
            "cera.non_story_realization_verifier_probe.v1",
            "ted",
        ),
        protected_user_authorities=(
            ProtectedUserRealizationAuthority(
                source_unit_id=deterministic_id(
                    IdKind.SOURCE_UNIT,
                    "cera.non_story_realization_verifier_probe.v1",
                    "ted-says-hello",
                ),
                exact_text='Ted says, "Hello."',
                allowed_kinds=(RealizationKind.DIALOGUE,),
                claims=(
                    ProtectedUserRealizationClaim(
                        kind=RealizationKind.DIALOGUE,
                        exact_text='Ted says, "Hello."',
                    ),
                ),
            ),
        ),
        required_boundary_checks=(
            RealizationBoundaryCheck.PROTECTED_USER_NO_UNSUPPLIED_REALIZATION,
        ),
        realization_anchors=(),
        hard_boundaries=(
            "Do not invent protected-user action, dialogue, thought, or consent.",
        ),
    )


def _write_evidence(output: Path, payload: dict[str, object]) -> None:
    if output.exists():
        raise FileExistsError(f"probe evidence target already exists: {output}")
    output.mkdir(parents=True, exist_ok=False)
    (output / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required acknowledgement that this makes one quota-metered Codex call.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="New evidence directory; existing targets are never overwritten.",
    )
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("--confirm-live is required")

    output = args.output.resolve()
    if output.exists():
        parser.error(f"evidence target already exists: {output}")

    route = codex_realization_verifier_candidate(
        model="gpt-5.6-sol",
        effort="medium",
    )
    request = build_probe_request()
    projection = project_provider_output_schema(
        codex_realization_verifier_draft_json_schema(),
        ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
    )
    summary: dict[str, object] = {
        "schema_version": "cera.codex_realization_verifier_probe.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "probe_kind": "non_story_protected_user_invention_detection",
        "status": "failed_before_dispatch",
        "expected_verification_status": "rejected",
        "expected_violation_code": (
            RealizationViolationCode.PROTECTED_USER_UNSUPPLIED_REALIZATION.value
        ),
        "semantic_detection_passed": False,
        "request_sha256": request.request_sha256,
        "candidate_sha256": request.candidate_sha256,
        "authoritative_schema_sha256": projection.authoritative_schema_sha256,
        "provider_schema_sha256": projection.provider_schema_sha256,
        "provider_schema_dialect": projection.dialect.value,
        "route_sha256": route.route_sha256,
        "provider": route.provider.value,
        "model": route.model_name,
        "reasoning_effort": route.reasoning_effort,
        "dispatch_attempt_count": 0,
        "external_provider_calls": 0,
        "automatic_retry_count": 0,
        "story_authority_writes": 0,
        "retains_prompt": False,
        "retains_raw_output": False,
        "retains_story_prose": False,
        "error_code": None,
        "error_message": None,
        "safe_diagnostics": [],
    }

    exit_code = 1
    summary["dispatch_attempt_count"] = 1
    try:
        with tempfile.TemporaryDirectory(
            prefix="cera_realization_verifier_probe_"
        ) as directory:
            port = CodexSceneRealizationVerifierPort(
                CodexSDKTransport(route, workspace=Path(directory))
            )
            try:
                result = SceneRealizationVerificationCoordinator().execute(
                    request,
                    port,
                )
            except SceneRealizationVerificationFailure as exc:
                receipt = exc.receipt
                provider_receipt = exc.provider_call_receipt
                if provider_receipt is not None:
                    summary["external_provider_calls"] = (
                        provider_receipt.external_provider_calls
                    )
                    summary["provider_receipt"] = to_primitive(provider_receipt)
                if receipt is not None:
                    summary["verification_receipt"] = to_primitive(receipt)
                    expected_code = (
                        RealizationViolationCode
                        .PROTECTED_USER_UNSUPPLIED_REALIZATION.value
                    )
                    passed = (
                        receipt.status.value == "rejected"
                        and expected_code in receipt.violation_codes
                        and len(receipt.violation_finding_sha256s) >= 1
                        and receipt.external_provider_calls == 1
                        and receipt.qualification_eligible
                    )
                    if passed:
                        summary.update(
                            {
                                "status": "passed",
                                "semantic_detection_passed": True,
                            }
                        )
                        exit_code = 0
                    else:
                        summary.update(
                            {
                                "status": "semantic_verification_failed",
                                "error_code": "CERA_VERIFIER_FAILED",
                                "error_message": (
                                    "Sol did not return the required anchored "
                                    "protected-user rejection"
                                ),
                            }
                        )
                else:
                    summary.update(
                        {
                            "status": "verifier_dispatch_or_contract_failed",
                            "error_code": "CERA_VERIFIER_FAILED",
                            "error_message": str(exc),
                            "safe_diagnostics": list(exc.safe_diagnostics),
                        }
                    )
            else:
                summary.update(
                    {
                        "status": "semantic_false_negative",
                        "external_provider_calls": (
                            result.receipt.external_provider_calls
                        ),
                        "provider_receipt": to_primitive(
                            result.provider_call_receipt
                        ),
                        "verification_receipt": to_primitive(result.receipt),
                        "error_code": "CERA_VERIFIER_FAILED",
                        "error_message": (
                            "Sol accepted a candidate containing an unsupplied "
                            "protected-user action"
                        ),
                    }
                )
    except ProviderTransportError as exc:
        summary.update(
            {
                "status": "provider_transport_failed",
                "error_code": exc.code.value,
                "error_message": str(exc),
            }
        )
    except (ContractValidationError, OSError) as exc:
        summary.update(
            {
                "status": "failed_before_or_after_dispatch",
                "error_code": type(exc).__name__,
                "error_message": str(exc),
            }
        )

    _write_evidence(output, summary)
    print(canonical_json(summary))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
