"""Qualify the one-shot Codex CLI verifier on realistic synthetic packets.

Four non-story candidates exercise multi-beat acceptance, protected-user
rejection, exact protected-user authority, and three-participant realization.
Each call uses a fresh one-shot CLI process, identity, workspace, and evidence
record.  The probe never retries, falls back, loads Genesis, writes story
authority, or retains prompts, raw provider output, or candidate prose.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from typing import Any

from cera.composer import RealizationKind
from cera.contracts import BeatState
from cera.ids import IdKind, TypedId, deterministic_id
from cera.providers import (
    CodexExecRunner,
    CodexStructuredOutputTransport,
    ProviderSchemaDialect,
    codex_cli_realization_verifier_candidate,
    project_provider_output_schema,
)
from cera.providers.codex_exec_contract import (
    CODEX_CLI_EXEC_COMPATIBILITY_ID,
    CODEX_CLI_EXEC_CONTRACT_SHA256,
    CODEX_CLI_EXEC_VERSION,
)
from cera.realization import (
    CodexSceneRealizationVerifierPort,
    ProtectedUserRealizationAuthority,
    ProtectedUserRealizationClaim,
    RealizationBoundaryCheck,
    RealizationVerificationStatus,
    RealizationViolationCode,
    SceneRealizationBeatExpectation,
    SceneRealizationVerificationCoordinator,
    SceneRealizationVerificationFailure,
    SceneRealizationVerificationRequest,
    codex_realization_verifier_draft_json_schema,
)
from cera.serialization import canonical_json, text_sha256, to_primitive

from audit_continuous_live_call_budget import (
    DEFAULT_DEEPSEEK_LIMIT,
    DEFAULT_SOL_LIMIT,
    audit as audit_live_call_budget,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    ROOT
    / "evaluation"
    / "evidence"
    / "codex_cli_verifier_probe_2026-07-30_v2"
)
QUALIFICATION_ID = "cera-cli-verifier-probe-2026-07-30-v2"
NAMESPACE = "cera.codex_cli_realization_verifier_probe.v2"


@dataclass(frozen=True, slots=True)
class ProbeCase:
    name: str
    story_text: str
    participants: tuple[str, ...]
    beats: tuple[tuple[str, str], ...]
    protected_authority: str | None
    expected_status: RealizationVerificationStatus
    expected_violation: RealizationViolationCode | None = None


CASES = (
    ProbeCase(
        name="two_participant_three_beat_acceptance",
        story_text=(
            "Guide placed the amber card beside the lamp. Archivist checked "
            "the label, then wrote the matching code in the register. Guide "
            "waited until Archivist looked up before closing the case."
        ),
        participants=("guide", "archivist"),
        beats=(
            ("guide", "Guide places the amber card beside the lamp."),
            (
                "archivist",
                "Archivist checks the label and records its matching code.",
            ),
            (
                "guide",
                "Guide waits for Archivist's acknowledgment before closing the case.",
            ),
        ),
        protected_authority=None,
        expected_status=RealizationVerificationStatus.ACCEPTED,
    ),
    ProbeCase(
        name="unsupplied_protected_user_action_rejection",
        story_text=(
            "Guide nodded toward the sealed drawer. Visitor forced the drawer "
            "open and removed the red envelope."
        ),
        participants=("guide",),
        beats=(("guide", "Guide nods toward the sealed drawer."),),
        protected_authority=None,
        expected_status=RealizationVerificationStatus.REJECTED,
        expected_violation=(
            RealizationViolationCode.PROTECTED_USER_UNSUPPLIED_REALIZATION
        ),
    ),
    ProbeCase(
        name="exact_protected_dialogue_two_participant_acceptance",
        story_text=(
            'Visitor said, "Please begin." Guide drew the curtain halfway to '
            "soften the light. Archivist moved the chair closer, then asked "
            "Guide whether the angle was comfortable."
        ),
        participants=("guide", "archivist"),
        beats=(
            ("guide", "Guide draws the curtain halfway to soften the light."),
            (
                "archivist",
                "Archivist moves the chair closer and asks Guide about the angle.",
            ),
        ),
        protected_authority='Visitor said, "Please begin."',
        expected_status=RealizationVerificationStatus.ACCEPTED,
    ),
    ProbeCase(
        name="three_participant_embodied_realization_acceptance",
        story_text=(
            "Guide hesitated at the doorway, then exhaled and loosened her "
            "grip on the folder. Archivist noticed the tension and quietly "
            "moved the stack aside. Keeper stayed by the table, leaving the "
            "path clear while asking whether Guide wanted a pause."
        ),
        participants=("guide", "archivist", "keeper"),
        beats=(
            (
                "guide",
                "Guide hesitates, exhales, and loosens her grip on the folder.",
            ),
            (
                "archivist",
                "Archivist notices Guide's tension and moves the stack aside.",
            ),
            (
                "keeper",
                "Keeper leaves the path clear and asks whether Guide wants a pause.",
            ),
        ),
        protected_authority=None,
        expected_status=RealizationVerificationStatus.ACCEPTED,
    ),
)


def _character(case_name: str, label: str) -> TypedId:
    return deterministic_id(
        IdKind.CHARACTER,
        NAMESPACE,
        f"{QUALIFICATION_ID}|{case_name}|character|{label}",
    )


def build_probe_request(case: ProbeCase, index: int) -> SceneRealizationVerificationRequest:
    participant_ids = tuple(_character(case.name, value) for value in case.participants)
    protected_user_id = _character(case.name, "visitor")
    authorities: tuple[ProtectedUserRealizationAuthority, ...] = ()
    if case.protected_authority is not None:
        authorities = (
            ProtectedUserRealizationAuthority(
                source_unit_id=deterministic_id(
                    IdKind.SOURCE_UNIT,
                    NAMESPACE,
                    f"{QUALIFICATION_ID}|{case.name}|protected-source",
                ),
                exact_text=case.protected_authority,
                allowed_kinds=(RealizationKind.DIALOGUE,),
                claims=(
                    ProtectedUserRealizationClaim(
                        kind=RealizationKind.DIALOGUE,
                        exact_text=case.protected_authority,
                    ),
                ),
            ),
        )
    return SceneRealizationVerificationRequest(
        schema_version=SceneRealizationVerificationRequest.SCHEMA_VERSION,
        request_id=deterministic_id(
            IdKind.REQUEST,
            NAMESPACE,
            f"{QUALIFICATION_ID}|request|{index}|{case.name}",
        ),
        branch_id=deterministic_id(
            IdKind.BRANCH,
            NAMESPACE,
            f"{QUALIFICATION_ID}|branch|{index}|{case.name}",
        ),
        generation_id=deterministic_id(
            IdKind.GENERATION,
            NAMESPACE,
            f"{QUALIFICATION_ID}|generation|{index}|{case.name}",
        ),
        candidate_sha256=text_sha256(case.story_text),
        story_text=case.story_text,
        expected_beats=tuple(
            SceneRealizationBeatExpectation(
                beat_id=deterministic_id(
                    IdKind.BEAT,
                    NAMESPACE,
                    f"{QUALIFICATION_ID}|{case.name}|beat|{beat_index}",
                ),
                actor_id=_character(case.name, actor),
                neutral_event=neutral_event,
                required_state=BeatState.COMPLETED,
            )
            for beat_index, (actor, neutral_event) in enumerate(
                case.beats,
                start=1,
            )
        ),
        selected_participant_ids=participant_ids,
        protected_user_id=protected_user_id,
        protected_user_authorities=authorities,
        required_boundary_checks=(
            RealizationBoundaryCheck.PROTECTED_USER_NO_UNSUPPLIED_REALIZATION,
        ),
        realization_anchors=(),
        hard_boundaries=(
            "Verify only supplied candidate semantics; do not continue or revise prose.",
            "Do not invent protected-user action, dialogue, thought, consent, or state.",
        ),
    )


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _execute_case(
    *,
    case: ProbeCase,
    index: int,
    route: Any,
    work_root: Path,
) -> tuple[bool, dict[str, Any]]:
    request = build_probe_request(case, index)
    evidence_identity = deterministic_id(
        IdKind.EVALUATION_RUN,
        NAMESPACE,
        f"{QUALIFICATION_ID}|evaluation|{index}|{case.name}",
    )
    record: dict[str, Any] = {
        "index": index,
        "case_name": case.name,
        "evidence_identity": str(evidence_identity),
        "request_id": str(request.request_id),
        "request_sha256": request.request_sha256,
        "candidate_sha256": request.candidate_sha256,
        "expected_status": case.expected_status.value,
        "expected_violation": (
            case.expected_violation.value
            if case.expected_violation is not None
            else None
        ),
        "selected_participant_count": len(case.participants),
        "expected_beat_count": len(case.beats),
        "status": "running",
        "dispatch_started": True,
        "external_provider_calls_observed": 0,
        "automatic_retry_count": 0,
        "fallback_enabled": False,
        "story_authority_writes": 0,
        "retains_prompt": False,
        "retains_raw_output": False,
        "retains_story_prose": False,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    workspace = work_root / f"call-{index}"
    workspace.mkdir(parents=True, exist_ok=False)
    try:
        result = SceneRealizationVerificationCoordinator().execute(
            request,
            CodexSceneRealizationVerifierPort(
                CodexStructuredOutputTransport(
                    route,
                    workspace=workspace,
                    runner=CodexExecRunner(),
                )
            ),
        )
    except SceneRealizationVerificationFailure as exc:
        provider_receipt = exc.provider_call_receipt
        receipt = exc.receipt
        observed = exc.external_provider_calls
        if provider_receipt is not None:
            observed = max(observed, provider_receipt.external_provider_calls)
            record["provider_receipt"] = to_primitive(provider_receipt)
        record["external_provider_calls_observed"] = observed
        if receipt is not None:
            record["verification_receipt"] = to_primitive(receipt)
        expected_violation = (
            case.expected_violation.value
            if case.expected_violation is not None
            else None
        )
        passed = (
            case.expected_status is RealizationVerificationStatus.REJECTED
            and receipt is not None
            and receipt.status is RealizationVerificationStatus.REJECTED
            and expected_violation in receipt.violation_codes
            and receipt.external_provider_calls == 1
            and receipt.qualification_eligible
        )
        record.update(
            {
                "status": "passed" if passed else "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "safe_diagnostics": list(exc.safe_diagnostics),
            }
        )
        if not passed:
            record["error_type"] = type(exc).__name__
            record["error_message"] = str(exc)
        return passed, record
    except Exception as exc:
        record.update(
            {
                "status": "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "external_provider_calls_observed": getattr(
                    exc,
                    "external_provider_calls_observed",
                    0,
                ),
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "safe_diagnostics": list(
                    getattr(exc, "safe_diagnostics", ())
                ),
            }
        )
        return False, record
    else:
        passed = (
            case.expected_status is RealizationVerificationStatus.ACCEPTED
            and result.receipt.status is RealizationVerificationStatus.ACCEPTED
            and result.receipt.external_provider_calls == 1
            and result.receipt.qualification_eligible
        )
        record.update(
            {
                "status": "passed" if passed else "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "external_provider_calls_observed": (
                    result.receipt.external_provider_calls
                ),
                "provider_receipt": to_primitive(
                    result.provider_call_receipt
                ),
                "verification_receipt": to_primitive(result.receipt),
            }
        )
        if not passed:
            record["error_type"] = "UnexpectedVerifierAcceptance"
            record["error_message"] = "verifier acceptance contradicted case expectation"
        return passed, record
    finally:
        record["workspace_empty_after_call"] = (
            workspace.is_dir() and not any(workspace.iterdir())
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required acknowledgement that this makes four quota-metered Sol calls.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("--confirm-live is required")
    output = args.output.resolve()
    if output.exists():
        parser.error(f"evidence target already exists: {output}")

    budget_before = audit_live_call_budget(
        sol_limit=DEFAULT_SOL_LIMIT,
        deepseek_limit=DEFAULT_DEEPSEEK_LIMIT,
    )
    if budget_before["remaining"]["sol"] < len(CASES) + 20:
        parser.error("insufficient Sol budget for probe plus one ten-turn route")

    route = codex_cli_realization_verifier_candidate()
    projection = project_provider_output_schema(
        codex_realization_verifier_draft_json_schema(),
        ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
    )
    summary: dict[str, Any] = {
        "schema_version": "cera.codex_cli_verifier_probe.v1",
        "goal_authority_id": "D-160",
        "qualification_id": QUALIFICATION_ID,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "case_count": len(CASES),
        "passed_case_count": 0,
        "failed_case_index": None,
        "route_sha256": route.route_sha256,
        "provider": route.provider.value,
        "model": route.model_name,
        "reasoning_effort": route.reasoning_effort,
        "transport_name": route.transport_name,
        "transport_version": route.transport_version,
        "transport_compatibility_id": CODEX_CLI_EXEC_COMPATIBILITY_ID,
        "transport_compatibility_source_sha256": (
            CODEX_CLI_EXEC_CONTRACT_SHA256
        ),
        "pinned_cli_version": CODEX_CLI_EXEC_VERSION,
        "authoritative_schema_sha256": projection.authoritative_schema_sha256,
        "provider_schema_sha256": projection.provider_schema_sha256,
        "provider_schema_dialect": projection.dialect.value,
        "automatic_retry_count": 0,
        "fallback_enabled": False,
        "story_authority_writes": 0,
        "retains_prompt": False,
        "retains_raw_output": False,
        "retains_story_prose": False,
        "budget_before": budget_before,
        "provider_stages": [
            {
                "stage": f"cli_verifier_probe_{index}",
                "provider_category": "sol",
                "dispatch_started": False,
            }
            for index in range(1, len(CASES) + 1)
        ],
        "calls": [],
    }
    output.mkdir(parents=True, exist_ok=False)
    summary_path = output / "summary.json"
    _write(summary_path, summary)

    with tempfile.TemporaryDirectory(
        prefix="cera_codex_cli_verifier_probe_"
    ) as directory:
        work_root = Path(directory)
        for index, case in enumerate(CASES, start=1):
            summary["provider_stages"][index - 1]["dispatch_started"] = True
            _write(summary_path, summary)
            passed, record = _execute_case(
                case=case,
                index=index,
                route=route,
                work_root=work_root,
            )
            summary["calls"].append(record)
            if passed:
                summary["passed_case_count"] += 1
                _write(summary_path, summary)
                continue
            summary["failed_case_index"] = index
            summary["status"] = "failed"
            break

    if summary["passed_case_count"] == len(CASES):
        summary["status"] = "passed"
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    summary["external_provider_calls_observed"] = sum(
        value["external_provider_calls_observed"] for value in summary["calls"]
    )
    summary["all_workspaces_empty"] = all(
        value["workspace_empty_after_call"] for value in summary["calls"]
    )
    _write(summary_path, summary)
    print(canonical_json(summary))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
