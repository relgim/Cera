from __future__ import annotations

import unittest
from dataclasses import replace

from cera.cognition import (
    AutonomyApplicationV1,
    CharacterAutonomyMode,
    CognitionPlanV1,
    CognitionValidationContextV1,
    DecisionItemLinkV1,
    DecisionRecordV1,
    KnowledgeCertainty,
    LogicRoute,
    MaterialPressureV1,
    ObserverFrameV1,
    PerceivedFactV1,
    PressureLevel,
    ProvisionalDependencyV1,
    ProvisionalTruthValue,
    ResponseLayersV1,
    UserDirectionDisposition,
    validate_cognition_plan,
)
from cera.errors import ContractValidationError
from cera.schema import from_mapping
from cera.sequence_first.contracts import (
    ItemKind,
    ProtectedSourceClaimV1,
    SequenceDraftV1,
    SequenceFirstTurnSemanticInputV1,
    SequenceItemV1,
)
from cera.serialization import to_primitive

SAKURA = "character:sakura_hanezawa"


def _turn() -> SequenceFirstTurnSemanticInputV1:
    return SequenceFirstTurnSemanticInputV1(
        exact_current_source="Ted asks Sakura to open the door.",
        current_source_key="source:current",
        protected_source_claims=(
            ProtectedSourceClaimV1(
                claim_key="current_request",
                exact_text="Ted asks Sakura to open the door.",
            ),
        ),
        known_character_ids=("character:ted", SAKURA),
        accepted_present_character_ids=("character:ted", SAKURA),
        explicitly_authorized_remote_character_ids=(),
        current_public_scene_state="Ted and Sakura are inside by the closed door.",
        prior_realized_sequence=None,
        character_deltas=(),
        evidence_records=(),
        approved_targets=(),
        unresolved_threads=(),
        hard_boundaries=(),
    )


def _sequence() -> SequenceDraftV1:
    return SequenceDraftV1(
        items=(
            SequenceItemV1(
                item_key="sakura_checks_door",
                kind=ItemKind.ACTION,
                concise_meaning="Sakura pauses and verifies who is outside.",
                owner_response_semantics="She checks before changing access.",
                owner_id=SAKURA,
                evidence_keys=("source:current",),
            ),
            SequenceItemV1(
                item_key="natural_stop",
                kind=ItemKind.STOPPING_BOUNDARY,
                concise_meaning="Stop after Sakura asks who is outside.",
            ),
        ),
        durable_changes=(),
        presence_changes=(),
        resulting_public_state="The door remains closed while Sakura verifies.",
        unresolved_threads=("Identity outside remains unverified.",),
        stopping_boundary="Sakura's verification question returns the floor.",
    )


def _application(mode: CharacterAutonomyMode) -> AutonomyApplicationV1:
    return AutonomyApplicationV1(
        mind_precedence_applied=mode.mind_precedence,
        body_precedence_applied=mode.body_precedence,
        user_direction_disposition=UserDirectionDisposition.PROPOSED_OUTCOME,
        overwhelming_pressure_kind=None,
        concise_effect="Sakura's logic decides whether the proposed opening occurs.",
    )


def _decision(mode: CharacterAutonomyMode) -> DecisionRecordV1:
    return DecisionRecordV1(
        decision_key="sakura_door_response",
        owner_id=SAKURA,
        causal_trigger_refs=("source:current",),
        observer_frame=ObserverFrameV1(
            directly_perceived=(
                PerceivedFactV1(
                    source_ref="source:current",
                    concise_perception="Ted asked her to open the door.",
                    certainty=KnowledgeCertainty.ESTABLISHED,
                ),
            ),
            inferred_meanings=("Ted wants the access boundary changed.",),
            unavailable_or_ambiguous=("The person outside is not verified.",),
        ),
        perceived_event_meaning="A request would change the household boundary.",
        knowledge_certainty=KnowledgeCertainty.HIGH,
        personal_and_social_meaning="Protecting the household outweighs convenience.",
        response_layers=ResponseLayersV1(
            immediate_involuntary_reaction="Her attention tightens toward the door.",
            conscious_interpretation="She recognizes an unverified access request.",
            subconscious_pressure="Habit urges her to verify before opening.",
            considered_judgment="A question is safer than immediate compliance.",
        ),
        selected_intent="Verify the visitor without opening the door.",
        concise_decision_basis="Uncertainty and household duty outweigh the request.",
        decisive_factor_refs=("source:current",),
        material_pressures=(
            MaterialPressureV1(
                kind="household_protection",
                level=PressureLevel.HIGH,
                direction="verify",
                evidence_refs=("source:current",),
            ),
        ),
        autonomy_application=_application(mode),
        anticipated_immediate_effect="The threshold stays closed during verification.",
        close_alternative=None,
        uncertainty=KnowledgeCertainty.MODERATE,
    )


def _plan(mode: CharacterAutonomyMode = CharacterAutonomyMode.BOTH) -> CognitionPlanV1:
    return CognitionPlanV1(
        sequence=_sequence(),
        decision_records=(_decision(mode),),
        decision_item_links=(
            DecisionItemLinkV1(
                item_key="sakura_checks_door",
                decision_key="sakura_door_response",
            ),
        ),
        provisional_dependencies=(),
    )


def _context(
    mode: CharacterAutonomyMode = CharacterAutonomyMode.BOTH,
    *,
    provisional: tuple[str, ...] = (),
) -> CognitionValidationContextV1:
    return CognitionValidationContextV1(
        autonomy_mode=mode,
        logic_route=LogicRoute.ORDINARY,
        available_evidence_refs=("source:current",),
        available_provisional_record_ids=provisional,
    )


class CognitionContractTests(unittest.TestCase):
    def test_closed_round_trip_and_historical_sequence_shape(self) -> None:
        plan = _plan()
        decoded = from_mapping(CognitionPlanV1, to_primitive(plan))
        self.assertEqual(decoded, plan)
        legacy = to_primitive(plan.sequence)
        self.assertNotIn("decision_records", legacy)
        self.assertNotIn("decision_key", legacy["items"][0])
        self.assertEqual(from_mapping(SequenceDraftV1, legacy), plan.sequence)

    def test_valid_material_decision_bundle(self) -> None:
        validate_cognition_plan(_plan(), turn=_turn(), context=_context())

    def test_material_item_requires_exactly_one_decision_link(self) -> None:
        plan = replace(_plan(), decision_item_links=())
        with self.assertRaisesRegex(ContractValidationError, "lack decision links"):
            validate_cognition_plan(plan, turn=_turn(), context=_context())

    def test_link_owner_must_match_item_owner(self) -> None:
        wrong = replace(_decision(CharacterAutonomyMode.BOTH), owner_id="character:hana")
        plan = replace(_plan(), decision_records=(wrong,))
        with self.assertRaisesRegex(ContractValidationError, "not a known character"):
            validate_cognition_plan(plan, turn=_turn(), context=_context())

    def test_autonomy_mode_is_mechanically_bound(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "wrong mind precedence"):
            validate_cognition_plan(
                _plan(CharacterAutonomyMode.OFF),
                turn=_turn(),
                context=_context(CharacterAutonomyMode.BOTH),
            )

    def test_overriding_pressure_must_be_overwhelming(self) -> None:
        decision = _decision(CharacterAutonomyMode.BOTH)
        application = replace(
            decision.autonomy_application,
            overwhelming_pressure_kind="household_protection",
        )
        with self.assertRaisesRegex(ContractValidationError, "overwhelming"):
            replace(decision, autonomy_application=application)

    def test_provisional_dependency_is_pinned_true_or_false(self) -> None:
        dependency = ProvisionalDependencyV1(
            provisional_record_id="provisional:door_claim",
            assumed_value=ProvisionalTruthValue.FALSE,
            concise_dependency="The scene proceeds with the claim treated as false.",
        )
        plan = replace(_plan(), provisional_dependencies=(dependency,))
        validate_cognition_plan(
            plan,
            turn=_turn(),
            context=_context(provisional=("provisional:door_claim",)),
        )
        with self.assertRaisesRegex(ContractValidationError, "unavailable provisional"):
            validate_cognition_plan(plan, turn=_turn(), context=_context())

    def test_protected_user_cannot_own_decision_record(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "protected user"):
            replace(_decision(CharacterAutonomyMode.BOTH), owner_id="character:ted")

    def test_decoder_rejects_coerced_boolean_and_unknown_field(self) -> None:
        payload = to_primitive(_plan())
        payload["decision_records"][0]["autonomy_application"]["mind_precedence_applied"] = "true"
        with self.assertRaisesRegex(ContractValidationError, "must be boolean"):
            from_mapping(CognitionPlanV1, payload)

        payload = to_primitive(_plan())
        payload["unknown"] = "forbidden"
        with self.assertRaisesRegex(ContractValidationError, "unknown fields"):
            from_mapping(CognitionPlanV1, payload)


if __name__ == "__main__":
    unittest.main()
