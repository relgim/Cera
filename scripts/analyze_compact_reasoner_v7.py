#!/usr/bin/env python3
"""Provider-free anatomy report for the frozen compact-v7 continuation case."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cera.contracts import (
    BehavioralTurnControls,
    DecisionRoute,
    InteractionTopology,
    InteriorityLevel,
    NaturalStopReason,
    PromptTone,
    SceneDepthMode,
    SceneFunction,
    SceneRunwayClass,
    SourceClaimAuthority,
    SourceClaimKind,
)
from cera.ids import IdKind, TypedId
from cera.providers import ProviderSchemaDialect, project_provider_output_schema
from cera.reasoner import (
    CodexReasonerDraftV6,
    CodexReasonerDraftV7Compact,
    CompactBeatV7,
    CompactResponderV7,
    DraftSourceClaimV5,
    InterventionReason,
    ReasonerOutcomeStatus,
    build_codex_reasoner_packet,
    build_codex_reasoner_prompt,
    codex_reasoner_draft_v6_json_schema,
    codex_reasoner_draft_v7_compact_json_schema,
    compile_compact_v7_to_v6,
)
from cera.runtime import (
    DevelopmentOrdinaryTurnPreparer,
    DevelopmentTurnSpec,
    HanezawaHumanTestWorld,
)
from cera.serialization import bytes_sha256, canonical_json, text_sha256, to_primitive
from cera.sillytavern.adapter import (
    continuity_seeds,
    select_scoped_candidate_cast_shadow,
    verifier_continuity_context,
)


DEFAULT_DATABASE = ROOT / "runtime" / "development" / "hanezawa_human_test_v1_2.sqlite3"
DEFAULT_BRANCH = "branch:e6d8da85-744a-5cde-a168-431f65d1c3a7"
DEFAULT_MESSAGE = "Continue the scene with only the current characters as they talk to eachother."


def _backup_database(source: Path, destination: Path) -> None:
    source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_connection:
        with sqlite3.connect(destination) as destination_connection:
            source_connection.backup(destination_connection)


def _prepare(
    world: HanezawaHumanTestWorld,
    *,
    branch_id: TypedId,
    message: str,
    scoped: bool,
):
    scope = select_scoped_candidate_cast_shadow(
        message,
        world,
        branch_id=branch_id,
    )
    if scoped:
        present = scope.physically_present_character_ids
        eligible = scope.eligible_responder_ids
    else:
        household = tuple(world.relationship_record_ids)
        present = (world.protected_user_id, *household)
        eligible = household
    continuity = continuity_seeds(world, branch_id=branch_id)
    spec = DevelopmentTurnSpec(
        schema_version=DevelopmentTurnSpec.SCHEMA_VERSION,
        turn_key=("compact-v7-anatomy-scoped" if scoped else "compact-v7-anatomy-broad"),
        raw_message=message,
        session_id=TypedId(IdKind.SESSION, "compact-v7-anatomy"),
        branch_id=branch_id,
        present_character_ids=present,
        eligible_responder_ids=eligible,
        scene_anchors=(
            "This is the CERA V1.2 human-test world.",
            "This analysis is provider-free and cannot publish story authority.",
            "Continue from the exact accepted branch head.",
        ),
        established_scene_context=verifier_continuity_context(
            world,
            branch_id=branch_id,
        ),
        additional_seed_records=continuity,
        response_profile_version="cera-compact-v7-shadow-anatomy-v1",
        scene_depth_mode=SceneDepthMode.MEDIUM,
        behavioral_controls=BehavioralTurnControls.creator_default(),
    )
    prepared = DevelopmentOrdinaryTurnPreparer(
        store=world.store,
        evidence_service=world.service,
        world_id=world.world_id,
        genesis_revision_id=world.revision_id,
        protected_user_id=world.protected_user_id,
        access_scope=world.system_scope,
        relationship_record_ids=world.relationship_record_ids,
        character_baseline_record_ids=world.character_baseline_record_ids,
    ).prepare(spec)
    return prepared, scope


def _variant(
    name: str,
    request,
    *,
    compact_input: bool,
    draft_schema_version: str,
    scope,
) -> dict[str, object]:
    packet = build_codex_reasoner_packet(
        request,
        evidence_tools_available=True,
        compact_input=compact_input,
        scene_cast_scope=scope if compact_input else None,
        draft_schema_version=draft_schema_version,
    )
    aliases = tuple(
        TypedId(IdKind.EVIDENCE, f"seed_{index:03d}")
        for index, _ in enumerate(request.seed_dossier.exact_seed_evidence, start=1)
    )
    schema = (
        codex_reasoner_draft_v7_compact_json_schema(
            allowed_evidence_ids=aliases,
            adult_route=False,
        )
        if draft_schema_version == CodexReasonerDraftV7Compact.SCHEMA_VERSION
        else codex_reasoner_draft_v6_json_schema(allowed_evidence_ids=aliases)
    )
    provider_schema = project_provider_output_schema(
        schema,
        ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
    ).provider_schema
    prompt = build_codex_reasoner_prompt(
        packet,
        draft_schema_version=draft_schema_version,
    )
    marker = "\nThe complete authoritative packet follows as canonical JSON:\n"
    stable = prompt.split(marker, 1)[0] if marker in prompt else prompt.rsplit("\n", 1)[0]
    packet_text = canonical_json(packet)
    schema_text = canonical_json(provider_schema)
    prompt_text = prompt
    return {
        "name": name,
        "draft_schema_version": draft_schema_version,
        "packet_schema_version": packet["schema_version"],
        "prompt_version": packet["prompt_version"],
        "seed_record_count": len(request.seed_dossier.exact_seed_evidence),
        "aware_character_ids": [str(value) for value in request.seed_dossier.aware_character_ids],
        "present_character_ids": [str(value) for value in request.prepared_turn.present_character_ids],
        "eligible_responder_ids": [str(value) for value in request.prepared_turn.eligible_responding_npc_ids],
        "stable_instruction_bytes": len(stable.encode("utf-8")),
        "packet_bytes": len(packet_text.encode("utf-8")),
        "provider_schema_bytes": len(schema_text.encode("utf-8")),
        "complete_prompt_bytes": len(prompt_text.encode("utf-8")),
        "estimated_complete_prompt_tokens_at_4_bytes": round(
            len(prompt_text.encode("utf-8")) / 4
        ),
        "estimated_schema_tokens_at_4_bytes": round(len(schema_text.encode("utf-8")) / 4),
        "stable_instruction_sha256": text_sha256(stable),
        "packet_sha256": text_sha256(packet_text),
        "provider_schema_sha256": text_sha256(schema_text),
        "complete_prompt_sha256": text_sha256(prompt_text),
    }


def _illustrative_output_anatomy(request) -> dict[str, object]:
    hana, mia = request.prepared_turn.eligible_responding_npc_ids
    source = request.source_view.units[0]
    responders = (
        CompactResponderV7(
            hana,
            None,
            "Hana and Mia are the accepted active conversational cast.",
            "Continue the welcome and leave room for Mia's contribution.",
            "Open one bounded exchange, then yield naturally.",
            (),
            ("Do not invent Ted's response.",),
        ),
        CompactResponderV7(
            mia,
            InterventionReason.CURRENT_SOURCE_ADDRESS,
            "Mia is already present in the accepted scene.",
            "Add a distinct low-pressure follow-through.",
            "Respond after Hana without introducing another person.",
            (),
            ("Use only Mia-owned knowledge.",),
        ),
    )
    beats = (
        CompactBeatV7(
            "hana_floor",
            (hana,),
            "Hana advances the current conversation.",
            "She owns the accepted conversational floor.",
            ("Hana supplies one warm opening.",),
            "The floor can pass to Mia without Ted acting.",
            (),
            ("continue",),
            (),
            (),
            ("Hana and Mia remain in the entrance area.",),
            (),
            ("DeepSeek chooses exact wording and pacing.",),
        ),
        CompactBeatV7(
            "mia_followthrough",
            (mia,),
            "Mia contributes a distinct response.",
            "She is already present and receives Hana's opening.",
            ("Mia adds a character-specific conversational follow-through.",),
            "The exchange reaches a natural Ted-facing handoff.",
            (),
            ("continue",),
            (),
            (),
            ("No absent family member enters.",),
            (),
            ("DeepSeek owns Mia's exact prose and micro-action.",),
        ),
    )
    compact = CodexReasonerDraftV7Compact(
        schema_version=CodexReasonerDraftV7Compact.SCHEMA_VERSION,
        status=ReasonerOutcomeStatus.DECISION_READY,
        route=DecisionRoute.ORDINARY,
        scene_goal="Continue the accepted Hana-Mia exchange without inventing Ted.",
        reason_code="develop_current_floor_then_distinct_followthrough",
        responders=responders,
        floor_owner_id=hana,
        source_claims=(
            DraftSourceClaimV5(
                "continue",
                source.source_unit_id,
                SourceClaimKind.CREATIVE_DIRECTION,
                SourceClaimAuthority.SOURCE_AUTHORIZED,
                source.safe_text,
                "Continue only the current characters' conversation.",
                (hana, mia),
                "The exact current source controls cast and continuation scope.",
            ),
        ),
        beats=beats,
        runway_class=SceneRunwayClass.DEVELOPED,
        continue_beyond_prompt_endpoint=True,
        stop_reason=NaturalStopReason.MEANINGFUL_USER_DECISION,
        stop_condition="Stop before Ted's next unsupplied meaningful choice.",
        interaction_topology=InteractionTopology.NPC_TO_NPC_EXCHANGE,
        scene_function=SceneFunction.ORDINARY_SOCIAL,
        tone=PromptTone.WARM,
        interiority_level=InteriorityLevel.LOW,
        essential_continuity=("Only Hana and Mia are currently active.",),
        future_segments=(),
        uncertainties=("Ted's next response remains unknown.",),
        prohibited_inferences=("Do not add an absent family member.",),
        insufficiencies=(),
        blocker_code=None,
        adult_craft_need=None,
        development_atoms=(),
        protected_user_boundary_acknowledged=True,
    )
    expanded = compile_compact_v7_to_v6(compact)
    compact_text = canonical_json(to_primitive(compact))
    expanded_text = canonical_json(to_primitive(expanded))
    return {
        "kind": "deterministic illustrative semantic-equivalent fixture",
        "provider_reported": False,
        "compact_v7_bytes": len(compact_text.encode("utf-8")),
        "expanded_v6_bytes": len(expanded_text.encode("utf-8")),
        "compact_v7_estimated_tokens_at_4_bytes": round(
            len(compact_text.encode("utf-8")) / 4
        ),
        "expanded_v6_estimated_tokens_at_4_bytes": round(
            len(expanded_text.encode("utf-8")) / 4
        ),
        "compact_v7_sha256": text_sha256(compact_text),
        "expanded_v6_sha256": text_sha256(expanded_text),
        "beat_count_before": len(compact.beats),
        "beat_count_after": len(expanded.event_blocks),
    }


def analyze(database: Path, branch_id: TypedId, message: str) -> dict[str, object]:
    with TemporaryDirectory(prefix="cera-compact-v7-anatomy-") as temporary:
        copied_database = Path(temporary) / "world.sqlite3"
        _backup_database(database, copied_database)
        world = HanezawaHumanTestWorld.open(ROOT, copied_database)
        broad, broad_scope = _prepare(
            world,
            branch_id=branch_id,
            message=message,
            scoped=False,
        )
        scoped, scoped_scope = _prepare(
            world,
            branch_id=branch_id,
            message=message,
            scoped=True,
        )
        variants = (
            _variant(
                "broad_v6",
                broad.reasoner_request,
                compact_input=False,
                draft_schema_version=CodexReasonerDraftV6.SCHEMA_VERSION,
                scope=broad_scope,
            ),
            _variant(
                "scoped_v6",
                scoped.reasoner_request,
                compact_input=True,
                draft_schema_version=CodexReasonerDraftV6.SCHEMA_VERSION,
                scope=scoped_scope,
            ),
            _variant(
                "scoped_v7",
                scoped.reasoner_request,
                compact_input=True,
                draft_schema_version=CodexReasonerDraftV7Compact.SCHEMA_VERSION,
                scope=scoped_scope,
            ),
        )
        return {
            "schema_version": "cera.compact_reasoner_v7_anatomy.v1",
            "provider_calls": 0,
            "story_database_mutated": False,
            "source_database_sha256": bytes_sha256(database.read_bytes()),
            "branch_id": str(branch_id),
            "generation": scoped.reasoner_request.prepared_turn.evidence_snapshot.generation,
            "accepted_head_artifact_id": str(
                scoped.reasoner_request.prepared_turn.request.parent_artifact_id
            ),
            "message_sha256": text_sha256(message),
            "scope": {
                "world_known": [str(value) for value in scoped_scope.world_known_character_ids],
                "physically_present": [str(value) for value in scoped_scope.physically_present_character_ids],
                "scene_reachable": [str(value) for value in scoped_scope.scene_reachable_character_ids],
                "currently_active": [str(value) for value in scoped_scope.currently_active_character_ids],
                "exact_source_responders": [str(value) for value in scoped_scope.exact_source_responder_ids],
                "eligible": [str(value) for value in scoped_scope.eligible_responder_ids],
                "selected": [],
                "reasons": list(scoped_scope.derivation_reasons),
            },
            "variants": variants,
            "output_anatomy": _illustrative_output_anatomy(
                scoped.reasoner_request
            ),
            "estimates_are_provider_reported": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--branch-id", default=DEFAULT_BRANCH)
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = analyze(
        args.database.resolve(),
        TypedId.parse(args.branch_id, IdKind.BRANCH),
        args.message,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8", newline="\n")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
