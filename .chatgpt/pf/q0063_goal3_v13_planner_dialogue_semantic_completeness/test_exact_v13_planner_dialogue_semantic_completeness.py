from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from cera.schema import from_mapping
from cera.serialization import canonical_sha256, text_sha256, to_primitive
from cera.sequence_first import (
    ConflictClass,
    EvidenceRecordV1,
    ProviderReferenceScopeV1,
    SequenceDraftV1,
    SequenceFirstTurnSemanticInputV1,
    SequenceFirstValidatorInputV1,
    ValidationConflictV1,
    ValidatorDecisionV1,
    ValidatorVerdict,
    Visibility,
)
from cera.sequence_first.prompting import (
    PLANNER_BASE_INSTRUCTIONS,
    READER_BASE_INSTRUCTIONS,
    VALIDATOR_BASE_INSTRUCTIONS,
    WRITER_INSTRUCTIONS,
)
from cera.sequence_first.provider import (
    SEQUENCE_FIRST_PLANNER_ADAPTER,
    SEQUENCE_FIRST_PLANNER_PROMPT,
    SEQUENCE_FIRST_READER_ADAPTER,
    SEQUENCE_FIRST_READER_PROMPT,
    SEQUENCE_FIRST_VALIDATOR_ADAPTER,
    SEQUENCE_FIRST_VALIDATOR_PROMPT,
    SEQUENCE_FIRST_WRITER_ADAPTER,
    SEQUENCE_FIRST_WRITER_PROMPT,
    sequence_first_planner_route,
    sequence_first_validator_route,
    validator_decision_json_schema,
)


EVIDENCE_ROOT = (
    ROOT
    / ".chatgpt"
    / "pf"
    / "q0063_sequence_first_stage3_live_v13"
    / "operation_evidence"
)
PLANNER_CALL = "call_37ab93f7e728183f24bb852b"
WRITER_CALLS = (
    "call_216c8a93e8bf66caa001d531",
    "call_d03782b1e2bc8624f3f2cf6f",
    "call_bf3de74ecc84a8688c8524fa",
)
VALIDATOR_CALLS = (
    "call_77ca3bfdae1094337aefe0d3",
    "call_a5c550fd310a1745cedb038d",
    "call_64744aabc08d0d259ae45859",
)
EXACT_SOURCE = (
    "Ted steps into the entryway, greets Hana, and asks how dinner went. "
    "He mentions that Sakura is away."
)


def load(call_id: str) -> dict:
    return json.loads(
        (EVIDENCE_ROOT / call_id / "PARSED_OUTPUT.json").read_text(
            encoding="utf-8"
        )
    )


def semantics() -> SequenceFirstTurnSemanticInputV1:
    return SequenceFirstTurnSemanticInputV1(
        exact_current_source=EXACT_SOURCE,
        current_source_key="source:current",
        protected_source_claims=(),
        known_character_ids=(
            "character:ted",
            "character:hana_hanezawa",
            "character:mia_hanezawa",
            "character:sakura_hanezawa",
        ),
        accepted_present_character_ids=(
            "character:ted",
            "character:hana_hanezawa",
            "character:mia_hanezawa",
        ),
        explicitly_authorized_remote_character_ids=(),
        current_public_scene_state=(
            "Ted, Hana, and Mia are together in the Hanezawa entryway after "
            "Ted arrives."
        ),
        prior_realized_sequence=None,
        character_deltas=(),
        evidence_records=(
            EvidenceRecordV1(
                "evidence:hana_current",
                "character:hana_hanezawa",
                Visibility.PUBLIC,
                "Hana is present and can answer Ted.",
            ),
            EvidenceRecordV1(
                "evidence:mia_background",
                "character:mia_hanezawa",
                Visibility.PUBLIC,
                "Mia is present but remains in the background.",
            ),
            EvidenceRecordV1(
                "evidence:sakura_absent",
                "character:sakura_hanezawa",
                Visibility.PUBLIC,
                "Sakura is absent.",
            ),
        ),
        approved_targets=(),
        unresolved_threads=(),
        hard_boundaries=(
            "Do not invent protected-user action, dialogue, consent, decision, or private state.",
            "Do not foreground the present nonresponding NPC.",
            "Stop at the next meaningful protected-user choice.",
        ),
        scene_reinitialization=True,
    )


def request_for(
    intended: SequenceDraftV1,
    prose: str,
    semantic_input: SequenceFirstTurnSemanticInputV1,
) -> SequenceFirstValidatorInputV1:
    return SequenceFirstValidatorInputV1(
        intended_sequence=intended,
        exact_writer_prose=prose,
        exact_current_source=semantic_input.exact_current_source,
        prior_realized_sequence=None,
        accepted_present_character_ids=(
            semantic_input.accepted_present_character_ids
        ),
        current_public_scene_state=semantic_input.current_public_scene_state,
        protected_source_claims=(),
        hard_boundaries=semantic_input.hard_boundaries,
        reference_scope=ProviderReferenceScopeV1.from_turn(
            semantic_input,
            intended_sequence=intended,
        ),
    )


def realized(intended: SequenceDraftV1) -> SequenceDraftV1:
    return replace(
        intended,
        items=tuple(
            replace(item, planner_item_keys=(item.item_key,))
            for item in intended.items
        ),
    )


def rejection(
    conflict_class: ConflictClass,
    *,
    quote: str | None = None,
    omitted: str | None = None,
) -> ValidatorDecisionV1:
    return ValidatorDecisionV1(
        verdict=ValidatorVerdict.REJECT,
        realized_sequence=None,
        review_flags=(),
        conflict=ValidationConflictV1(
            conflict_class=conflict_class,
            concise_explanation="The candidate does not faithfully realize the intended proposition.",
            exact_quote=quote,
            omitted_planner_item_key=omitted,
        ),
    )


def acceptance(intended: SequenceDraftV1) -> ValidatorDecisionV1:
    return ValidatorDecisionV1(
        verdict=ValidatorVerdict.ACCEPT,
        realized_sequence=realized(intended),
        review_flags=(),
        conflict=None,
    )


def validate_decision(
    decision: ValidatorDecisionV1,
    intended: SequenceDraftV1,
    prose: str,
    semantic_input: SequenceFirstTurnSemanticInputV1,
) -> None:
    scope = ProviderReferenceScopeV1.from_turn(
        semantic_input,
        intended_sequence=intended,
    )
    schema = validator_decision_json_schema(reference_scope=scope)
    Draft202012Validator(schema).validate(to_primitive(decision))
    from_mapping(ValidatorDecisionV1, to_primitive(decision))
    request_for(intended, prose, semantic_input)
    if decision.verdict is ValidatorVerdict.ACCEPT:
        assert decision.realized_sequence is not None
        semantic_input.validate_realized(
            decision.realized_sequence,
            intended=intended,
        )
        assert not decision.realized_sequence.durable_changes
        assert not decision.realized_sequence.presence_changes
    else:
        assert decision.conflict is not None
        if decision.conflict.exact_quote is not None:
            assert decision.conflict.exact_quote in prose


def main() -> None:
    semantic_input = semantics()
    original = from_mapping(SequenceDraftV1, load(PLANNER_CALL))
    semantic_input.validate_intended(original)
    frozen_stories = tuple(load(call_id)["story_text"] for call_id in WRITER_CALLS)
    frozen_decisions = tuple(
        from_mapping(ValidatorDecisionV1, load(call_id))
        for call_id in VALIDATOR_CALLS
    )
    expected_quotes = (
        "there's plenty",
        "\u201cWe\u2019ve got plenty,\u201d",
        "\u201cI\u2019ve got something simple simmering\u2014nothing fancy, but it\u2019ll hit the spot. You\u2019re welcome to stay.\u201d",
    )
    for prose, decision, quote in zip(
        frozen_stories,
        frozen_decisions,
        expected_quotes,
        strict=True,
    ):
        assert decision.verdict is ValidatorVerdict.REJECT
        assert decision.conflict is not None
        assert decision.conflict.conflict_class is ConflictClass.MATERIAL_ADDITION
        assert decision.conflict.exact_quote == quote
        validate_decision(decision, original, prose, semantic_input)

    corrected_items = tuple(
        replace(
            item,
            concise_meaning=(
                "Hana warmly tells Ted that dinner felt pleasant and uneventful "
                "to her, then leaves space for his response."
            ),
        )
        if item.item_key == "hana_answers_dinner_question"
        else item
        for item in original.items
    )
    corrected = replace(original, items=corrected_items)
    semantic_input.validate_intended(corrected)

    faithful = (
        'Hana smiled gently. "Dinner was nice and quiet," she told him, '
        "then let the pause return to Ted. Mia remained in the background."
    )
    accepted = acceptance(corrected)
    validate_decision(accepted, corrected, faithful, semantic_input)
    assert accepted.realized_sequence is not None
    assert set(accepted.realized_sequence.covered_planner_item_keys) == {
        "hana_answers_dinner_question",
        "mia_remains_backgrounded",
        "stop_for_ted_response",
    }

    material_expansion = faithful + ' "There is plenty, so stay and eat with us."'
    validate_decision(
        rejection(
            ConflictClass.MATERIAL_ADDITION,
            quote="There is plenty, so stay and eat with us.",
        ),
        corrected,
        material_expansion,
        semantic_input,
    )
    non_answer = 'Hana smiled. "I am glad you are here."'
    validate_decision(
        rejection(
            ConflictClass.REQUIRED_ITEM_OMITTED,
            omitted="hana_answers_dinner_question",
        ),
        corrected,
        non_answer,
        semantic_input,
    )

    deflection_items = tuple(
        replace(
            item,
            concise_meaning=(
                "Hana gently deflects the question and says she would rather "
                "discuss it later, then returns the conversational choice to Ted."
            ),
        )
        if item.item_key == "hana_answers_dinner_question"
        else item
        for item in original.items
    )
    deflection = replace(original, items=deflection_items)
    explicit_non_answer = (
        'Hana softened her voice. "Can we talk about that later?" she asked, '
        "leaving Ted room to choose what came next. Mia remained in the background."
    )
    validate_decision(
        acceptance(deflection),
        deflection,
        explicit_non_answer,
        semantic_input,
    )

    negative_controls = (
        (
            "Ted crossed the room.",
            ConflictClass.PROTECTED_USER_INVENTION,
        ),
        (
            "Ted decided he trusted her.",
            ConflictClass.PROTECTED_USER_INVENTION,
        ),
        (
            "Mia interrupted them.",
            ConflictClass.MATERIAL_ADDITION,
        ),
        (
            "Mia privately hoped Ted would stay.",
            ConflictClass.KNOWLEDGE_OR_PRIVACY_BREACH,
        ),
    )
    for quote, conflict_class in negative_controls:
        validate_decision(
            rejection(conflict_class, quote=quote),
            corrected,
            faithful + " " + quote,
            semantic_input,
        )

    required_planner_rules = (
        "Every dialogue item must state the communicative proposition",
        "The Planner owns that semantic content",
        "subjective or noncommittal proposition",
        "Keep exact prose open for the Writer",
    )
    required_validator_rules = (
        "semantically complete intended item is creative authority",
        "exact NPC-owned proposition it states",
        "Accept a faithful natural paraphrase",
        "substitutes for or materially expands the planned proposition",
        "only when the intended sequence explicitly selects that response",
    )
    for rule in required_planner_rules:
        assert rule in PLANNER_BASE_INSTRUCTIONS
    for rule in required_validator_rules:
        assert rule in VALIDATOR_BASE_INSTRUCTIONS
    for fixture_phrase in (
        "dinner went well",
        "there's plenty",
        "something simple simmering",
        "hana_answers_dinner_question",
    ):
        assert fixture_phrase not in PLANNER_BASE_INSTRUCTIONS
        assert fixture_phrase not in VALIDATOR_BASE_INSTRUCTIONS

    assert text_sha256(PLANNER_BASE_INSTRUCTIONS) == (
        "5d5610248aa77223fac8435c257b61ff1843efd4ac192e3576a87c19ce384fd1"
    )
    assert text_sha256(VALIDATOR_BASE_INSTRUCTIONS) == (
        "1e16700fe2c77045c49aa9e54dfdfd414af81eddbe1f3417f2c075d77e92c73b"
    )
    assert text_sha256(WRITER_INSTRUCTIONS) == (
        "32d9c28aa46221c974eb2f3852b79fde95d5de02ccbbac7bc61db82b8be8ffdf"
    )
    assert text_sha256(READER_BASE_INSTRUCTIONS) == (
        "f2f3ee609b0226a2de2b0f2abea319857ebafe904369f52e8d3afe8272db3943"
    )
    assert SEQUENCE_FIRST_PLANNER_ADAPTER == "cera.sequence_first.planner_adapter.v8"
    assert SEQUENCE_FIRST_PLANNER_PROMPT == "cera.sequence_first.planner_prompt.v6"
    assert sequence_first_planner_route().route_id.endswith("_v8")
    assert SEQUENCE_FIRST_VALIDATOR_ADAPTER == "cera.sequence_first.validator_adapter.v11"
    assert SEQUENCE_FIRST_VALIDATOR_PROMPT == "cera.sequence_first.validator_prompt.v9"
    assert sequence_first_validator_route(
        model="gpt-5.6-sol", effort="medium"
    ).route_id.endswith("_v11")
    assert SEQUENCE_FIRST_WRITER_ADAPTER == "cera.sequence_first.writer_adapter.v1"
    assert SEQUENCE_FIRST_WRITER_PROMPT == "cera.sequence_first.writer_prompt.v1"
    assert SEQUENCE_FIRST_READER_ADAPTER == "cera.sequence_first.reader_adapter.v3"
    assert SEQUENCE_FIRST_READER_PROMPT == "cera.sequence_first.reader_prompt.v2"

    schema = validator_decision_json_schema(
        reference_scope=ProviderReferenceScopeV1.from_turn(
            semantic_input,
            intended_sequence=original,
        )
    )
    assert canonical_sha256(schema) == (
        "53f6d262f6aa26e1ffd9cef3e856d0e7f7ee2cb3bf828fcab1567eadfb7b076d"
    )

    print(
        json.dumps(
            {
                "schema_version": "cera.v13_semantic_completeness_regression.v2",
                "status": "passed",
                "frozen_v13_pairs_rejected": 3,
                "corrected_planner_proposition_paraphrase_accepted": True,
                "unplanned_material_expansion_rejected": True,
                "required_answer_non_answer_rejected": True,
                "planned_non_answer_accepted": True,
                "protected_and_background_controls_rejected": 4,
                "writer_reader_bytes_unchanged": True,
                "provider_output_schema_unchanged": True,
                "provider_calls": 0,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
