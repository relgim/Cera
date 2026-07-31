from __future__ import annotations

from dataclasses import replace
import unittest

from cera.contracts import (
    BehavioralScenePlan,
    BehavioralTurnControls,
    CausalRunwayContract,
    CharacterAutonomyMode,
    DevelopmentAtomKind,
    DevelopmentAtomProposal,
    DevelopmentAtomStrength,
    InteractionTopology,
    InteriorityLevel,
    NaturalStopReason,
    PromptHandlingMode,
    PromptTone,
    ProtectedUserAllowanceKind,
    ProtectedUserRealizationAllowance,
    SceneEventBlock,
    SceneRunwayClass,
    SceneFunction,
    SourceClaimAuthority,
    SourceClaimDecision,
    SourceClaimKind,
    SourceClaimLedger,
    WriterScaffold,
    validate_claim_authority,
)
from cera.errors import ContractValidationError
from cera.ids import IdKind
from cera.registry import build_schema_registry
from cera.serialization import text_sha256
from cera.reasoner import (
    CodexReasonerDraftV5,
    CodexReasonerDraftV6,
    DraftCausalRunwayV5,
    DraftDevelopmentAtomV6,
    DraftCharacterMoveV2,
    DraftParticipationV2,
    DraftProtectedUserAllowanceV5,
    DraftSceneEventBlockV5,
    DraftSourceClaimV5,
    DraftWriterScaffoldV5,
    ReasonerOutcomeStatus,
    compile_reasoner_draft,
)
from cera.contracts import DecisionRoute
import tests.test_reasoner as reasoner_test_support

from tests.support import HASH_A, tid


class BehavioralContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ted = tid(IdKind.CHARACTER, "ted")
        self.enne = tid(IdKind.CHARACTER, "enne")
        self.source_unit = tid(IdKind.SOURCE_UNIT, "S01")

    def claim(
        self,
        kind: SourceClaimKind,
        authority: SourceClaimAuthority,
        *,
        suffix: str,
    ) -> SourceClaimDecision:
        return SourceClaimDecision(
            claim_id=tid(IdKind.SOURCE_CLAIM, suffix),
            source_unit_id=self.source_unit,
            kind=kind,
            authority=authority,
            start=0,
            end=4,
            exact_text_sha256=text_sha256("test"),
            normalized_meaning="Bounded source meaning.",
            affected_character_ids=(self.enne,),
            rationale="Classified from an exact source span.",
        )

    def test_creator_default_protects_character_mind_and_body(self) -> None:
        controls = BehavioralTurnControls.creator_default()
        self.assertIs(controls.character_autonomy_mode, CharacterAutonomyMode.BOTH)
        self.assertIs(controls.prompt_handling_mode, PromptHandlingMode.ADJUSTMENT)
        for kind in (
            SourceClaimKind.NPC_MIND_STATE,
            SourceClaimKind.NPC_INVOLUNTARY_BODY_STATE,
        ):
            with self.assertRaises(ContractValidationError):
                validate_claim_authority(
                    controls,
                    (self.claim(kind, SourceClaimAuthority.SOURCE_AUTHORIZED, suffix=kind.value),),
                )

    def test_autonomy_keeps_observable_action_available(self) -> None:
        validate_claim_authority(
            BehavioralTurnControls.creator_default(),
            (
                self.claim(
                    SourceClaimKind.NPC_OBSERVABLE_ACTION,
                    SourceClaimAuthority.SOURCE_AUTHORIZED,
                    suffix="action",
                ),
            ),
        )

    def test_adjustment_cannot_smuggle_modification(self) -> None:
        with self.assertRaises(ContractValidationError):
            validate_claim_authority(
                BehavioralTurnControls.creator_default(),
                (
                    self.claim(
                        SourceClaimKind.PROTECTED_USER_ACTION,
                        SourceClaimAuthority.MODIFICATION_PROPOSAL,
                        suffix="mod",
                    ),
                ),
            )

    def test_scene_blocks_preserve_writer_space_and_selected_cast(self) -> None:
        claim = self.claim(
            SourceClaimKind.PROTECTED_USER_DIALOGUE,
            SourceClaimAuthority.SOURCE_AUTHORIZED,
            suffix="dialogue",
        )
        ledger = SourceClaimLedger(
            schema_version=SourceClaimLedger.SCHEMA_VERSION,
            source_sha256=HASH_A,
            controls_sha256=BehavioralTurnControls.creator_default().controls_sha256,
            claims=(claim,),
            unresolved_questions=(),
            prohibited_inferences=("Do not invent Ted's reply.",),
        )
        scaffold = WriterScaffold(
            scaffold_id=tid(IdKind.WRITER_SCAFFOLD, "W01"),
            viewpoint_character_ids=(self.enne,),
            motivation_and_subtext=("Preserve Enne's analytic reserve.",),
            voice_and_interiority=("Use selective interiority only when useful.",),
            physical_and_material_continuity=(),
            transition_obligations=("Move from appraisal to a bounded response.",),
            open_realization_space=("DeepSeek chooses exact phrasing and pacing.",),
        )
        block = SceneEventBlock(
            block_id=tid(IdKind.SCENE_BLOCK, "B01"),
            actor_ids=(self.enne,),
            purpose="Enne appraises the request before answering.",
            causal_basis="Her established literal and analytical manner.",
            event_advances=("She tests the request's exact scope.",),
            resulting_state="The boundary is explicit without resolving Ted's next choice.",
            evidence_ids=(),
            source_claim_ids=(claim.claim_id,),
            protected_user_allowance=ProtectedUserRealizationAllowance(
                allowed_kinds=(ProtectedUserAllowanceKind.EXACT_SUPPLIED_DIALOGUE,),
                source_claim_ids=(claim.claim_id,),
                explanation="The supplied question may be referenced exactly.",
            ),
            writer_scaffold=scaffold,
        )
        plan = BehavioralScenePlan(
            schema_version=BehavioralScenePlan.SCHEMA_VERSION,
            claim_ledger=ledger,
            event_blocks=(block,),
            runway=CausalRunwayContract(
                runway_class=SceneRunwayClass.DEVELOPED,
                development_obligations=("Complete appraisal and immediate consequence.",),
                continue_beyond_prompt_endpoint=True,
                stop_reason=NaturalStopReason.MEANINGFUL_USER_DECISION,
                stop_condition="Stop before an unsupplied consequential Ted choice.",
            ),
            interaction_topology=InteractionTopology.SINGLE_NPC_FLOOR,
            scene_function=SceneFunction.ORDINARY_SOCIAL,
            tone=PromptTone.NEUTRAL,
            interiority_level=InteriorityLevel.MEDIUM,
            selected_character_ids=(self.enne,),
            omitted_character_ids=(),
        )
        self.assertEqual(plan.event_blocks[0].writer_scaffold, scaffold)
        with self.assertRaises(ContractValidationError):
            replace(plan, selected_character_ids=(tid(IdKind.CHARACTER, "mia"),))

    def test_development_is_atomic_and_never_self_authorizing(self) -> None:
        atom = DevelopmentAtomProposal(
            atom_id=tid(IdKind.DEVELOPMENT, "d1"),
            owner_character_id=self.enne,
            kind=DevelopmentAtomKind.NOTICED_SIGNAL,
            strength_before=DevelopmentAtomStrength.ABSENT,
            strength_after=DevelopmentAtomStrength.TRACE,
            summary="Enne notices a small response without naming it love.",
            source_scene_block_ids=(tid(IdKind.SCENE_BLOCK, "B01"),),
            evidence_record_ids=(tid(IdKind.EVENT, "e1"),),
            predecessor_development_ids=(),
            inference_limit="Does not establish attachment, jealousy, or love.",
        )
        self.assertFalse(atom.durable_authority)
        with self.assertRaises(ContractValidationError):
            replace(
                atom,
                strength_before=DevelopmentAtomStrength.ABSENT,
                strength_after=DevelopmentAtomStrength.ESTABLISHED,
            )
        with self.assertRaises(ContractValidationError):
            replace(atom, durable_authority=True)

    def test_new_behavioral_schemas_are_registered(self) -> None:
        versions = build_schema_registry().versions
        for value in (
            BehavioralTurnControls.SCHEMA_VERSION,
            SourceClaimLedger.SCHEMA_VERSION,
            BehavioralScenePlan.SCHEMA_VERSION,
        ):
            self.assertIn(value, versions)

    def test_v6_reasoner_draft_compiles_claims_blocks_runway_and_atomic_development(self) -> None:
        support = reasoner_test_support.ReasonerTests("runTest")
        support.setUp()
        self.addCleanup(support.doCleanups)
        prepared = support.prepared("behavioral-v5", responders=(support.alpha,))
        request = support.request(prepared, aware=(support.alpha,))
        unit = request.source_view.units[0]
        draft = CodexReasonerDraftV6(
            schema_version=CodexReasonerDraftV6.SCHEMA_VERSION,
            status=ReasonerOutcomeStatus.DECISION_READY,
            route=DecisionRoute.ORDINARY,
            scene_intent="Let Alpha respond from established character logic.",
            responding_npc_ids=(support.alpha,),
            floor_owner_id=support.alpha,
            participation=(
                DraftParticipationV2(
                    character_id=support.alpha,
                    intervention_reason=None,
                    evidence_ids=(),
                ),
            ),
            character_moves=(
                DraftCharacterMoveV2(
                    character_id=support.alpha,
                    perception="Alpha receives the exact source cue.",
                    selected_intent="Answer without inventing the user.",
                    action_direction="Give one character-specific response.",
                    evidence_ids=(),
                    knowledge_constraints=("Use only supplied source authority.",),
                ),
            ),
            source_claims=(
                DraftSourceClaimV5(
                    claim_key="source_context",
                    source_unit_id=unit.source_unit_id,
                    kind=SourceClaimKind.CREATIVE_DIRECTION,
                    authority=SourceClaimAuthority.SOURCE_AUTHORIZED,
                    quote=unit.safe_text,
                    normalized_meaning="The exact synthetic cue is current.",
                    affected_character_ids=(support.alpha,),
                    rationale="The complete source unit is direct current-turn context.",
                ),
            ),
            event_blocks=(
                DraftSceneEventBlockV5(
                    block_key="alpha_response",
                    actor_ids=(support.alpha,),
                    purpose="Alpha appraises and answers the cue.",
                    causal_basis="Alpha owns the conversational floor.",
                    event_advances=("Alpha forms and gives a bounded response.",),
                    resulting_state="Alpha has responded; Ted's next choice remains open.",
                    evidence_ids=(),
                    source_claim_keys=("source_context",),
                    protected_user_allowance=DraftProtectedUserAllowanceV5(
                        allowed_kinds=(),
                        source_claim_keys=(),
                        explanation="No direct protected-user realization is required.",
                    ),
                    writer_scaffold=DraftWriterScaffoldV5(
                        viewpoint_character_ids=(support.alpha,),
                        motivation_and_subtext=("Preserve Alpha's current reserve.",),
                        voice_and_interiority=("Use selective interiority.",),
                        physical_and_material_continuity=(),
                        transition_obligations=("Move from appraisal to reply.",),
                        open_realization_space=("DeepSeek owns exact prose and pacing.",),
                    ),
                ),
            ),
            causal_runway=DraftCausalRunwayV5(
                runway_class=SceneRunwayClass.DEVELOPED,
                development_obligations=("Complete appraisal and response.",),
                continue_beyond_prompt_endpoint=True,
                stop_reason=NaturalStopReason.MEANINGFUL_USER_DECISION,
                stop_condition="Stop before Ted's next consequential choice.",
            ),
            interaction_topology=InteractionTopology.SINGLE_NPC_FLOOR,
            scene_function=SceneFunction.ORDINARY_SOCIAL,
            tone=PromptTone.NEUTRAL,
            interiority_level=InteriorityLevel.MEDIUM,
            future_segments=(),
            writer_must_preserve=("Keep the response character-specific.",),
            uncertainties=(),
            prohibited_inferences=("Do not invent Ted's private state.",),
            insufficiencies=(),
            blocker_code=None,
            adult_craft_need=None,
            protected_user_boundary_acknowledged=True,
            development_atoms=(
                DraftDevelopmentAtomV6(
                    atom_key="alpha_first_trace",
                    owner_character_id=support.alpha,
                    kind=DevelopmentAtomKind.NOTICED_SIGNAL,
                    strength_before=DevelopmentAtomStrength.ABSENT,
                    strength_after=DevelopmentAtomStrength.TRACE,
                    summary="Alpha notices one small response without explaining it.",
                    source_block_keys=("alpha_response",),
                    evidence_ids=(),
                    predecessor_development_ids=(),
                    inference_limit=(
                        "This does not establish attraction, attachment, jealousy, or love."
                    ),
                ),
            ),
        )
        outcome = compile_reasoner_draft(
            request,
            draft,
            authorized_exact_evidence=(),
        )
        self.assertIsNotNone(outcome.behavioral_scene_plan)
        self.assertEqual(
            outcome.decision.current_segment.ordered_beats[0].neutral_event,
            "Alpha appraises and answers the cue.",
        )
        self.assertTrue(
            outcome.behavioral_scene_plan.runway.continue_beyond_prompt_endpoint
        )
        self.assertEqual(
            outcome.behavioral_scene_plan.development_atoms[0].strength_after,
            DevelopmentAtomStrength.TRACE,
        )


if __name__ == "__main__":
    unittest.main()
