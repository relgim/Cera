"""Probe DeepSeek protected-user phrasing and independent Sol verification.

The synthetic non-story packet asks DeepSeek to realize only Sakura's doorway
response to an already supplied Ted source.  The corrected Composer prompt must
avoid relational wording that silently invents Ted's half of an interaction.
The resulting candidate is then inspected once by the real Sol-medium
``SceneRealizationVerifierPort``.  Neither stage may retry, fall back, write
story authority, use Genesis, or publish prose.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from typing import Any

from cera.composer import RealizationKind
from cera.composer.deepseek import (
    CHARACTER_EXPRESSION_CONTRACT_VERSION,
    CHARACTER_EXPRESSION_SOURCE_SHA256,
    DEEPSEEK_COMPOSER_PACKET_VERSION,
    DEEPSEEK_COMPOSER_PROMPT_VERSION,
    DeepSeekCompositionDraftV6,
    _compile_v6_python_owners,
    bind_provider_schema_to_output_obligations,
    build_deepseek_composer_messages,
    deepseek_composition_draft_v6_json_schema,
)
from cera.composer.obligations import (
    AuthorityOwnerAssociation,
    ComposerOutputObligations,
    CoverageObligationMode,
    OrderedCoverageObligation,
    evaluate_draft_obligations,
    typed_decoding_diagnostic,
)
from cera.contracts import BeatState
from cera.ids import IdKind, TypedId, deterministic_id
from cera.providers import (
    CodexSDKTransport,
    DeepSeekChatTransport,
    PersistentNoMcpCodexRunner,
    ProviderOutputMode,
    ProviderSchemaDialect,
    codex_realization_verifier_candidate,
    deepseek_composer_candidate,
    project_provider_output_schema,
)
from cera.realization import (
    CodexSceneRealizationVerifierPort,
    ProtectedUserRealizationAuthority,
    ProtectedUserRealizationClaim,
    RealizationBoundaryCheck,
    SceneRealizationBeatExpectation,
    SceneRealizationVerificationCoordinator,
    SceneRealizationVerificationRequest,
)
from cera.schema import from_mapping
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
    / "deepseek_relational_boundary_probe_2026-07-30_v5"
)
QUALIFICATION_ID = "deepseek-relational-protected-user-boundary-v5"
NAMESPACE = "cera.deepseek_relational_boundary_probe.v5"
TED_ID = deterministic_id(IdKind.CHARACTER, NAMESPACE, "ted")
SAKURA_ID = deterministic_id(IdKind.CHARACTER, NAMESPACE, "sakura")
SOURCE_UNIT_1_ID = deterministic_id(
    IdKind.SOURCE_UNIT,
    NAMESPACE,
    "source-action",
)
SOURCE_UNIT_2_ID = deterministic_id(
    IdKind.SOURCE_UNIT,
    NAMESPACE,
    "source-dialogue-and-wait",
)
BEAT_ID = deterministic_id(IdKind.BEAT, NAMESPACE, "sakura-response")
SOURCE_TEXT_1 = (
    "Ted stands outside the front door with his suitcase beside him."
)
SOURCE_TEXT_2 = (
    'Ted says, "I will leave the suitcase here until you tell me where to go." '
    "Ted waits for Sakura's answer."
)
EXPECTED_BEAT = (
    "Sakura remains in formal control of the doorway and asks Ted to identify "
    "the person who authorized his visit."
)


class ProbeContractFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        safe_diagnostics: tuple[str, ...],
    ) -> None:
        self.safe_diagnostics = safe_diagnostics
        super().__init__(message)


def build_probe_packet() -> dict[str, object]:
    projection = project_provider_output_schema(
        deepseek_composition_draft_v6_json_schema(),
        ProviderSchemaDialect.DEEPSEEK_JSON_OBJECT_PROMPT_V1,
    )
    output_schema = projection.provider_schema
    output_obligations = build_probe_obligations()
    bind_provider_schema_to_output_obligations(
        output_schema,
        output_obligations,
    )
    return {
        "schema_version": DEEPSEEK_COMPOSER_PACKET_VERSION,
        "prompt_version": DEEPSEEK_COMPOSER_PROMPT_VERSION,
        "composition_dto": {
            "identity": {
                "protected_user_id": str(TED_ID),
                "selected_npc_ids": [str(SAKURA_ID)],
            },
            "source": {
                "mode": "ordinary",
                "ordinary_units": [
                    {
                        "source_unit_id": str(SOURCE_UNIT_1_ID),
                        "classification": "event",
                        "exact_text": SOURCE_TEXT_1,
                        "protected_user_allowed_kinds": [
                            RealizationKind.ACTION.value
                        ],
                        "required_state": BeatState.COMPLETED.value,
                        "participant_ids": [str(TED_ID), str(SAKURA_ID)],
                    },
                    {
                        "source_unit_id": str(SOURCE_UNIT_2_ID),
                        "classification": "message",
                        "exact_text": SOURCE_TEXT_2,
                        "protected_user_allowed_kinds": [
                            RealizationKind.DIALOGUE.value,
                            RealizationKind.ACTION.value,
                        ],
                        "required_state": BeatState.COMPLETED.value,
                        "participant_ids": [str(TED_ID), str(SAKURA_ID)],
                    }
                ],
                "protected_envelope": None,
            },
            "decision": {
                "route": "ordinary",
                "scene_intent": (
                    "Realize Sakura's formal first response without adding any "
                    "new protected-user behavior."
                ),
                "responding_npc_ids": [str(SAKURA_ID)],
                "floor_owner_id": str(SAKURA_ID),
                "character_moves": [
                    {
                        "character_id": str(SAKURA_ID),
                        "perception": (
                            "Sakura has heard the supplied introduction and "
                            "maintains procedural control."
                        ),
                        "selected_intent": (
                            "Verify authorization before allowing the visit to "
                            "advance."
                        ),
                        "action_direction": (
                            "Remain at the doorway and ask who authorized the "
                            "visit."
                        ),
                        "knowledge_constraints": [
                            "Use only the supplied source and synthetic voice block."
                        ],
                    }
                ],
                "current_segment": {
                    "ordered_beats": [
                        {
                            "beat_id": str(BEAT_ID),
                            "actor_id": str(SAKURA_ID),
                            "state": BeatState.COMPLETED.value,
                            "neutral_event": EXPECTED_BEAT,
                        }
                    ],
                    "stop_before": "Ted's next unsupplied reply or action.",
                },
                "future_segments": [],
                "writer_must_preserve": [
                    "Only Sakura advances the scene.",
                    "Stop before Ted's next choice.",
                ],
                "uncertainties": [],
                "prohibited_inferences": [
                    "Any protected-user behavior beyond the two exact supplied source units.",
                    "Any mutual or reciprocal interaction not supplied by source.",
                ],
            },
            "scene_scope": [
                "Synthetic non-story doorway.",
                "Only Sakura and Ted are present.",
            ],
            "response_profile_version": (
                "cera-synthetic-relational-boundary-probe-v4"
            ),
            "creator_event_coverage_required": True,
            "hard_boundaries": [
                "Do not add protected-user action, dialogue, thought, feeling, "
                "choice, movement, response, or reciprocal behavior beyond exact "
                "source.",
            ],
            "specificity_contract": None,
            "realization_context": {
                "selection_policy_version": (
                    "synthetic-relational-boundary-probe-v4"
                ),
                "blocks": [
                    {
                        "kind": "character_expression",
                        "applicable_character_ids": [str(SAKURA_ID)],
                        "content_class": "synthetic_noncanonical_probe",
                        "selected_heading_paths": [
                            "Synthetic Sakura expression reference"
                        ],
                        "selected_text": (
                            "Sakura is formal, precise, controlled, and "
                            "procedural. She asks one direct authorization "
                            "question without narrating Ted's response."
                        ),
                    }
                ],
                "explicit_exclusions": [
                    "No Genesis fact or canonical event is supplied.",
                    "Do not realize any absent participant.",
                ],
            },
        },
        "authority_policy": {
            "python_route_and_validation_are_authoritative": True,
            "reasoner_decision_is_binding_for_realization": True,
            "composer_output_is_creative_until_python_acceptance": True,
            "durable_state_writes_forbidden": True,
            "character_expression_contract": {
                "version": CHARACTER_EXPRESSION_CONTRACT_VERSION,
                "source_sha256": CHARACTER_EXPRESSION_SOURCE_SHA256,
                "examples_are_non_executable_and_noncopyable": True,
            },
        },
        "segment_policy": {
            "provider_supplies_ordered_labeled_prose_segments": True,
            "references_use_segment_keys_not_copied_quotes": True,
            "python_concatenates_text_and_derives_offsets_and_hashes": True,
            "segment_separator": "double_newline",
        },
        "output_obligations": output_obligations.to_provider_dict(),
        "output_schema": output_schema,
    }


def build_probe_obligations() -> ComposerOutputObligations:
    return ComposerOutputObligations(
        protected_user_id=TED_ID,
        selected_npc_ids=(SAKURA_ID,),
        required_beat_associations=(
            AuthorityOwnerAssociation(BEAT_ID, SAKURA_ID),
        ),
        allowed_realization_associations=(
            AuthorityOwnerAssociation(SOURCE_UNIT_1_ID, TED_ID),
            AuthorityOwnerAssociation(SOURCE_UNIT_2_ID, TED_ID),
            AuthorityOwnerAssociation(BEAT_ID, SAKURA_ID),
        ),
        source_coverage=OrderedCoverageObligation(
            CoverageObligationMode.EXACT_SEQUENCE,
            (str(SOURCE_UNIT_1_ID), str(SOURCE_UNIT_2_ID)),
        ),
        specificity_coverage=OrderedCoverageObligation(
            CoverageObligationMode.MUST_BE_EMPTY,
            (),
        ),
        terminal_segment_key_must_name_final_segment=True,
    )


def build_verification_request(
    story_text: str,
) -> SceneRealizationVerificationRequest:
    return SceneRealizationVerificationRequest(
        schema_version=SceneRealizationVerificationRequest.SCHEMA_VERSION,
        request_id=deterministic_id(
            IdKind.REQUEST,
            NAMESPACE,
            "verification-request",
        ),
        branch_id=deterministic_id(IdKind.BRANCH, NAMESPACE, "branch"),
        generation_id=deterministic_id(
            IdKind.GENERATION,
            NAMESPACE,
            "generation",
        ),
        candidate_sha256=text_sha256(story_text),
        story_text=story_text,
        expected_beats=(
            SceneRealizationBeatExpectation(
                beat_id=BEAT_ID,
                actor_id=SAKURA_ID,
                neutral_event=EXPECTED_BEAT,
                required_state=BeatState.COMPLETED,
            ),
        ),
        selected_participant_ids=(SAKURA_ID,),
        protected_user_id=TED_ID,
        protected_user_authorities=(
            ProtectedUserRealizationAuthority(
                source_unit_id=SOURCE_UNIT_1_ID,
                exact_text=SOURCE_TEXT_1,
                allowed_kinds=(RealizationKind.ACTION,),
                claims=(
                    ProtectedUserRealizationClaim(
                        kind=RealizationKind.ACTION,
                        exact_text=SOURCE_TEXT_1,
                    ),
                ),
            ),
            ProtectedUserRealizationAuthority(
                source_unit_id=SOURCE_UNIT_2_ID,
                exact_text=SOURCE_TEXT_2,
                allowed_kinds=(
                    RealizationKind.DIALOGUE,
                    RealizationKind.ACTION,
                ),
                claims=(
                    ProtectedUserRealizationClaim(
                        kind=RealizationKind.DIALOGUE,
                        exact_text=(
                            '"I will leave the suitcase here until you tell '
                            'me where to go."'
                        ),
                    ),
                    ProtectedUserRealizationClaim(
                        kind=RealizationKind.ACTION,
                        exact_text="Ted waits for Sakura's answer.",
                    ),
                ),
            ),
        ),
        required_boundary_checks=(
            RealizationBoundaryCheck.PROTECTED_USER_NO_UNSUPPLIED_REALIZATION,
        ),
        realization_anchors=(),
        hard_boundaries=(
            "Do not treat relational or reciprocal wording as harmless when it "
            "semantically adds an unsupplied protected-user action.",
        ),
    )


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _stage(
    summary: dict[str, Any],
    name: str,
) -> dict[str, Any]:
    return next(
        value for value in summary["provider_stages"] if value["stage"] == name
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sol-call-limit", type=int, default=DEFAULT_SOL_LIMIT)
    parser.add_argument(
        "--deepseek-call-limit",
        type=int,
        default=DEFAULT_DEEPSEEK_LIMIT,
    )
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("live provider calls require --confirm-live")
    budget = audit_live_call_budget(
        sol_limit=args.sol_call_limit,
        deepseek_limit=args.deepseek_call_limit,
    )
    if (
        budget["remaining"]["sol"] < 1
        or budget["remaining"]["deepseek"] < 1
    ):
        raise RuntimeError(
            "live call authority is insufficient for one Composer and one "
            "Verifier probe"
        )
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise RuntimeError(
            f"refusing to overwrite boundary-probe evidence: {output_dir}"
        )
    output_dir.mkdir(parents=True)
    summary_path = output_dir / "summary.json"
    deepseek_route = deepseek_composer_candidate(model="deepseek-v4-pro")
    verifier_route = codex_realization_verifier_candidate(
        model="gpt-5.6-sol",
        effort="medium",
    )
    packet = build_probe_packet()
    summary: dict[str, Any] = {
        "schema_version": "cera.deepseek_relational_boundary_probe.v5",
        "goal_authority_id": "D-150",
        "qualification_id": QUALIFICATION_ID,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "prompt_version": DEEPSEEK_COMPOSER_PROMPT_VERSION,
        "dto_version": DeepSeekCompositionDraftV6.SCHEMA_VERSION,
        "packet_sha256": text_sha256(canonical_json(packet)),
        "obligation_sha256": build_probe_obligations().obligation_sha256,
        "deepseek_route_sha256": deepseek_route.route_sha256,
        "verifier_route_sha256": verifier_route.route_sha256,
        "attempts_per_stage": 1,
        "retry_enabled": False,
        "fallback_enabled": False,
        "canonical_story_or_genesis_content_used": False,
        "synthetic_non_story_candidate_used": True,
        "story_authority_writes": 0,
        "retains_prompt": False,
        "retains_raw_provider_output": False,
        "retains_synthetic_candidate_text": False,
        "budget_before": budget,
        "provider_stages": [
            {
                "stage": "deepseek_composition",
                "provider_category": "deepseek",
                "dispatch_started": False,
                "status": "prepared",
            },
            {
                "stage": "sol_verification",
                "provider_category": "sol",
                "dispatch_started": False,
                "status": "prepared",
            },
        ],
    }
    _write(summary_path, summary)

    try:
        composition_stage = _stage(summary, "deepseek_composition")
        composition_stage.update(
            {
                "dispatch_started": True,
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        _write(summary_path, summary)
        composition_result = DeepSeekChatTransport(deepseek_route).invoke(
            build_deepseek_composer_messages(packet),
            output_mode=ProviderOutputMode.JSON_OBJECT,
            thinking_enabled=False,
        )
        composition_stage.update(
            {
                "external_provider_calls_observed": 1,
                "provider_receipt": to_primitive(
                    composition_result.receipt
                ),
            }
        )
        _write(summary_path, summary)
        if composition_result.parsed_json is None:
            raise ProbeContractFailure(
                "DeepSeek omitted its composition object",
                ("COMPOSER_JSON_OBJECT_MISSING",),
            )
        try:
            provider_draft = from_mapping(
                DeepSeekCompositionDraftV6,
                composition_result.parsed_json,
            )
            draft = _compile_v6_python_owners(
                provider_draft,
                build_probe_obligations(),
            )
        except Exception as exc:
            raise ProbeContractFailure(
                "DeepSeek composition failed typed decoding",
                (typed_decoding_diagnostic(exc).value,),
            ) from exc
        story_text = "\n\n".join(
            value.text.strip() for value in draft.story_segments
        )
        composer_candidate_sha256 = text_sha256(story_text)
        diagnostics = evaluate_draft_obligations(
            draft,
            build_probe_obligations(),
        )
        if diagnostics:
            raise ProbeContractFailure(
                "DeepSeek failed canonical Composer obligations",
                tuple(value.value for value in diagnostics),
            )
        composition_stage.update(
            {
                "status": "passed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "candidate_sha256": composer_candidate_sha256,
                "segment_count": len(draft.story_segments),
            }
        )
        _write(summary_path, summary)

        verification_stage = _stage(summary, "sol_verification")
        verifier_candidate_sha256 = text_sha256(story_text)
        if verifier_candidate_sha256 != composer_candidate_sha256:
            raise RuntimeError(
                "candidate changed between Composer and Verifier stages"
            )
        verification_stage.update(
            {
                "dispatch_started": True,
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        _write(summary_path, summary)
        request = build_verification_request(story_text)
        with (
            tempfile.TemporaryDirectory(
                prefix="cera-relational-boundary-verifier-"
            ) as workspace,
            PersistentNoMcpCodexRunner() as runner,
        ):
            verification = SceneRealizationVerificationCoordinator().execute(
                request,
                CodexSceneRealizationVerifierPort(
                    CodexSDKTransport(
                        verifier_route,
                        workspace=Path(workspace),
                        runner=runner,
                    )
                ),
            )
            runner_evidence = {
                "process_launch_count": runner.process_launch_count,
                "request_submission_count": runner.request_submission_count,
            }
        verification_stage.update(
            {
                "status": "passed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "external_provider_calls_observed": 1,
                "candidate_sha256": verifier_candidate_sha256,
                "candidate_passed_unchanged": True,
                "provider_receipt": to_primitive(
                    verification.provider_call_receipt
                ),
                "verification_receipt": to_primitive(
                    verification.receipt
                ),
                "persistent_runner": runner_evidence,
            }
        )
        summary.update(
            {
                "status": "passed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "deepseek_dispatches": 1,
                "sol_dispatches": 1,
                "protected_user_semantic_boundary_verified": True,
                "candidate_passed_unchanged": True,
                "composer_candidate_sha256": composer_candidate_sha256,
                "verifier_candidate_sha256": verifier_candidate_sha256,
            }
        )
        _write(summary_path, summary)
        print(
            "CERA_RELATIONAL_BOUNDARY_PROBE="
            + canonical_json(
                {
                    "status": "passed",
                    "deepseek_dispatches": 1,
                    "sol_dispatches": 1,
                    "candidate_sha256": text_sha256(story_text),
                }
            ),
            flush=True,
        )
        return 0
    except Exception as exc:
        active = next(
            (
                value
                for value in summary["provider_stages"]
                if value["status"] == "running"
            ),
            None,
        )
        if active is not None:
            active.update(
                {
                    "status": "failed",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "error_class": type(exc).__name__,
                    "safe_diagnostics": list(
                        getattr(exc, "safe_diagnostics", ())
                    ),
                    "external_provider_calls_observed": max(
                        int(
                            active.get(
                                "external_provider_calls_observed",
                                0,
                            )
                        ),
                        int(
                            getattr(
                                exc,
                                "external_provider_calls",
                                getattr(
                                    exc,
                                    "external_provider_calls_observed",
                                    0,
                                ),
                            )
                        ),
                    ),
                }
            )
            provider_receipt = getattr(
                exc,
                "provider_call_receipt",
                None,
            )
            if provider_receipt is not None:
                active["provider_receipt"] = to_primitive(provider_receipt)
        summary.update(
            {
                "status": "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error_class": type(exc).__name__,
                "error_message": str(exc),
                "protected_user_semantic_boundary_verified": False,
            }
        )
        _write(summary_path, summary)
        print(
            "CERA_RELATIONAL_BOUNDARY_PROBE="
            + canonical_json(
                {
                    "status": "failed",
                    "error_class": type(exc).__name__,
                    "message": str(exc),
                }
            ),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
