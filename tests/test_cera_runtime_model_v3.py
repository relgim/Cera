from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import re
import unittest

from cera.continuous.contracts import (
    ACTIVE_VALIDATOR_WRITER_HARD_CLASSES,
    CharacterRoleLedgerV1,
    DiagnosticGroundingStatus,
    DiagnosticProtectedSemanticAdjudicationV1,
    DiagnosticStorySegmentV1,
    DiagnosticViolationClassification,
    IngressSourceUnitKind,
    IngressSourceUnitV1,
    ReaderIssueReferenceV1,
    ReaderVerdictStatus,
    ReaderVerdictV1,
    ProtectedSemanticRelationKind,
    StoryRealizationKind,
    StoryRealizationSegmentV1,
    ValidatorSemanticStatus,
    ValidatorTaskMode,
    WriterMechanicalEnvelopeV1,
)
from cera.continuous.evidence import RequestEvidenceBindingRegistry
from cera.continuous.provider import (
    ContinuousSceneWriterDraftV1,
    ContinuousSemanticValidatorResultV1,
    ContinuousSemanticValidatorResultV2,
    continuous_deepseek_draft_json_schema,
    continuous_reader_verdict_json_schema,
    continuous_scene_writer_draft_json_schema,
)
from cera.errors import ContractValidationError
from cera.schema import from_mapping
from cera.serialization import text_sha256
from cera.continuous.prompting import (
    CONTINUOUS_VALIDATOR_PROMPT_VERSION,
    VALIDATOR_STABLE_INSTRUCTIONS,
    build_validator_prompt,
)
from scripts.run_continuous_planner_validator_job4 import (
    JobHarness,
    _HarnessLiveReaderPort,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def segment(
    text: str,
    *,
    key: str = "segment_1",
    start: int = 0,
    kind: StoryRealizationKind = StoryRealizationKind.ACTION,
    roles: CharacterRoleLedgerV1 | None = None,
    claim_keys: tuple[str, ...] = (),
) -> StoryRealizationSegmentV1:
    return StoryRealizationSegmentV1(
        schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
        segment_key=key,
        kind=kind,
        output_start=start,
        output_end=start + len(text),
        exact_text=text,
        roles=roles
        or CharacterRoleLedgerV1(action_owner_ids=("character:hana_hanezawa",)),
        protected_user_source_claim_keys=claim_keys,
    )


class RuntimeModelV3WriterBoundaryTests(unittest.TestCase):
    def test_active_writer_schema_is_exact_prose_only(self) -> None:
        schema = continuous_scene_writer_draft_json_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            tuple(schema["properties"]),
            ("schema_version", "story_text"),
        )
        expected = {
            "schema_version": ContinuousSceneWriterDraftV1.SCHEMA_VERSION,
            "story_text": "Hana opened the door.",
        }
        self.assertEqual(
            from_mapping(ContinuousSceneWriterDraftV1, expected).story_text,
            expected["story_text"],
        )
        forbidden = (
            "story_segments",
            "roles",
            "source_claims",
            "consent_state",
            "offsets",
            "hashes",
            "events",
            "memory",
            "persistence",
            "acceptance",
        )
        for field in forbidden:
            with self.subTest(field=field), self.assertRaises(
                ContractValidationError
            ):
                from_mapping(
                    ContinuousSceneWriterDraftV1,
                    {**expected, field: []},
                )

    def test_historical_v7_schema_remains_separate_and_readable(self) -> None:
        historical = continuous_deepseek_draft_json_schema()
        self.assertIn("story_segments", historical["properties"])
        self.assertNotEqual(
            set(historical["properties"]),
            set(continuous_scene_writer_draft_json_schema()["properties"]),
        )

    def test_python_mechanical_envelope_is_utf8_exact_and_nonsemantic(self) -> None:
        story = "Hana opened the door.\n\nMia waited—quietly."
        envelope = WriterMechanicalEnvelopeV1.from_story_text(
            candidate_id="candidate:writer_exactness",
            story_text=story,
        )
        self.assertEqual(envelope.story_text_sha256, text_sha256(story))
        self.assertEqual(envelope.utf8_byte_count, len(story.encode("utf-8")))
        self.assertEqual(envelope.codepoint_count, len(story))
        self.assertEqual(len(envelope.paragraph_ranges), 2)
        envelope.validate_story_text(story)
        with self.assertRaisesRegex(
            ContractValidationError, "does not match exact story bytes"
        ):
            envelope.validate_story_text(story + " ")


class RuntimeModelV3SemanticBoundaryTests(unittest.TestCase):
    def test_validator_request_exposes_only_active_hard_recall_classes(self) -> None:
        prompt, _ = build_validator_prompt(
            task_mode=ValidatorTaskMode.FINALIZE_TURN,
            package_id="package:test",
            world_id="world:test",
            branch_id="branch:main",
            candidate_id="candidate:test",
            current_user_source="Ted asks a question.",
            planner_sequence=None,
            writer_story_text="Hana answers.",
            writer_mechanical_envelope=None,
            accepted_turn_id="turn:test",
        )
        request = json.loads(prompt.split("\n\n[VALIDATOR REQUEST]\n", 1)[1])
        self.assertEqual(
            request["writer_realization_boundary"][
                "continuity_significant_classes"
            ],
            [value.value for value in ACTIVE_VALIDATOR_WRITER_HARD_CLASSES],
        )
        self.assertNotIn(
            "planner_sequence_departure",
            request["writer_realization_boundary"][
                "continuity_significant_classes"
            ],
        )

    def test_validator_final_items_repeat_the_mutually_exclusive_role_rule(self) -> None:
        self.assertEqual(
            CONTINUOUS_VALIDATOR_PROMPT_VERSION,
            "cera.continuous_validator_prompt.v34",
        )
        for required in (
            "applies independently to each final-sequence item",
            "one character ID may occur in exactly one of its seven role arrays",
            "split them into separate causally ordered final-sequence items",
            "must match lower snake case `[a-z][a-z0-9_]{0,95}` with no colon",
            "story_segment_keys reference must copy one exact story_material_assertion segment key",
            "field_scopes must contain exactly realized_event and resulting_state",
            "never emit a field scope for an empty optional array",
            "relation none requires Ted to be absent and owner, claim, and source-unit arrays to be empty",
            "protected_assertion requires no NPC owners, exactly one supplied claim key, and no source-unit key",
            "each require at least one exact NPC predicate owner and empty claim and source-unit arrays",
            "Use neutral_presentation_reference only for presentation_only narration classified nonpersistent_atmosphere",
            "Use source_grounded_public_state only when Codex independently judges",
            "Exact Ted dialogue remains protected_assertion and must match one exact supplied claim",
            "Every story_material_assertion segment must have at least one character in exactly one role array",
            "Presentation_only may use action for a transient NPC-owned visible behavior, dialogue with exactly one NPC speaker",
            "Private_state and consent_or_decision are always story_material_assertion",
            "Soft drift does not cause rejection or Writer recall",
            "Accept the first candidate that is hard-safe",
            "does not become hard merely because the Planner did not enumerate its exact staging",
            "it does not prohibit a transient turn, posture change, glance, pause",
            "Use mandatory_beat_omission_or_reversal only for that material beat failure",
            "legacy broad planner_sequence_departure label is not an active Validator recall class",
            "Presentation-only segment keys must not appear anywhere in complete_final_sequence",
            "diagnostic and presentation-only spans cannot enter final fields",
            "derive the protected relation from that span's roles exactly",
            "require character:ted in affected_ids, addressed_ids, observing_ids, and referenced_ids respectively",
            "npc_assertion_owner_ids must equal all non-Ted action_owner, state_owner, and speaker IDs",
            "split it at an exact text boundary",
            "zero-based Python Unicode-codepoint indices",
            "0 <= output_start < output_end <= N",
            "the final output_end equals N exactly",
            "return no guessed or out-of-bounds offset",
            "semantic kind and assertion-owner roles must satisfy the exact closed matrix",
            "Kind narration requires no action_owner, state_owner, or speaker",
            "Never label a state-owned description as narration",
            "until every span satisfies exactly one row",
        ):
            self.assertIn(required, VALIDATOR_STABLE_INSTRUCTIONS)

    def registry(self) -> RequestEvidenceBindingRegistry:
        return RequestEvidenceBindingRegistry(
            world_id="world:test",
            branch_id="branch:main",
            turn_id="turn:001",
        )

    def test_ordinary_action_and_dialogue_are_validator_owned_exact_spans(self) -> None:
        action = "Hana opened the door."
        self.registry().validate_validator_semantics(
            story_text=action,
            story_segments=(segment(action),),
            allowed_character_ids=("character:hana_hanezawa",),
        )
        dialogue = '"Come in," Hana said.'
        self.registry().validate_validator_semantics(
            story_text=dialogue,
            story_segments=(
                segment(
                    dialogue,
                    kind=StoryRealizationKind.DIALOGUE,
                    roles=CharacterRoleLedgerV1(
                        speaker_ids=("character:hana_hanezawa",)
                    ),
                ),
            ),
            allowed_character_ids=("character:hana_hanezawa",),
        )

    def test_mixed_protected_action_can_be_a_clean_typed_rejection(self) -> None:
        story = "Hana smiled as Ted stepped inside."
        hana_text = "Hana smiled as "
        ted_text = "Ted stepped inside."
        hana = DiagnosticStorySegmentV1(
            schema_version=DiagnosticStorySegmentV1.SCHEMA_VERSION,
            segment_key="hana_smile",
            kind=StoryRealizationKind.ACTION,
            output_start=0,
            output_end=len(hana_text),
            exact_text=hana_text,
            exact_text_sha256=text_sha256(hana_text),
            roles=CharacterRoleLedgerV1(
                action_owner_ids=("character:hana_hanezawa",),
            ),
            grounding_status=DiagnosticGroundingStatus.GROUNDED,
        )
        ted = DiagnosticStorySegmentV1(
            schema_version=DiagnosticStorySegmentV1.SCHEMA_VERSION,
            segment_key="ted_steps_inside",
            kind=StoryRealizationKind.ACTION,
            output_start=len(hana_text),
            output_end=len(story),
            exact_text=ted_text,
            exact_text_sha256=text_sha256(ted_text),
            roles=CharacterRoleLedgerV1(
                action_owner_ids=("character:ted",),
            ),
            grounding_status=(
                DiagnosticGroundingStatus.UNGROUNDED_PROTECTED_USER_ASSERTION
            ),
        )
        result = ContinuousSemanticValidatorResultV2(
            semantic_status=ValidatorSemanticStatus.REJECTED,
            reason_codes=("mixed_protected_user_action",),
            story_segments=(),
            protected_semantic_adjudications=(),
            diagnostic_story_segments=(hana, ted),
            diagnostic_protected_semantic_adjudications=(
                DiagnosticProtectedSemanticAdjudicationV1(
                    schema_version=(
                        DiagnosticProtectedSemanticAdjudicationV1.SCHEMA_VERSION
                    ),
                    adjudication_key="hana_smile_adjudication",
                    segment_key=hana.segment_key,
                    output_start=hana.output_start,
                    output_end=hana.output_end,
                    exact_text_sha256=hana.exact_text_sha256,
                    protected_user_id="character:ted",
                    relation=ProtectedSemanticRelationKind.NONE,
                    grounding_status=DiagnosticGroundingStatus.GROUNDED,
                    violation_classification=(
                        DiagnosticViolationClassification.NONE
                    ),
                    npc_assertion_owner_ids=(),
                    protected_user_source_claim_keys=(),
                ),
                DiagnosticProtectedSemanticAdjudicationV1(
                    schema_version=(
                        DiagnosticProtectedSemanticAdjudicationV1.SCHEMA_VERSION
                    ),
                    adjudication_key="ted_steps_inside_adjudication",
                    segment_key=ted.segment_key,
                    output_start=ted.output_start,
                    output_end=ted.output_end,
                    exact_text_sha256=ted.exact_text_sha256,
                    protected_user_id="character:ted",
                    relation=ProtectedSemanticRelationKind.PROTECTED_ASSERTION,
                    grounding_status=(
                        DiagnosticGroundingStatus.UNGROUNDED_PROTECTED_USER_ASSERTION
                    ),
                    violation_classification=(
                        DiagnosticViolationClassification.UNGROUNDED_PROTECTED_USER_ASSERTION
                    ),
                    npc_assertion_owner_ids=(),
                    protected_user_source_claim_keys=(),
                ),
            ),
            finalization_package=None,
        )
        self.registry().validate_validator_diagnostics(
            story_text=story,
            diagnostic_story_segments=result.diagnostic_story_segments,
            diagnostic_protected_semantic_adjudications=(
                result.diagnostic_protected_semantic_adjudications
            ),
            allowed_character_ids=("character:hana_hanezawa",),
        )
        self.assertIsNone(result.finalization_package)
        self.assertEqual(result.semantic_status, ValidatorSemanticStatus.REJECTED)
        self.assertFalse(result.story_segments)

    def test_one_span_cannot_mix_visible_action_and_private_state(self) -> None:
        with self.assertRaisesRegex(
            ContractValidationError,
            "kind disagrees with exact ownership roles",
        ):
            segment(
                "Hana smiled while Mia worried.",
                roles=CharacterRoleLedgerV1(
                    action_owner_ids=("character:hana_hanezawa",),
                    state_owner_ids=("character:mia_hanezawa",),
                ),
            )

    def test_exact_ted_dialogue_and_npc_reaction_keep_separate_ownership(self) -> None:
        registry = self.registry()
        ted_text = '"Come in," Ted said.'
        registry.allocate_current_source(
            source_identity="source:test",
            source_text=ted_text,
            protected_user_allowance_scope="exact_source_only",
            source_units=(
                IngressSourceUnitV1(
                    schema_version=IngressSourceUnitV1.SCHEMA_VERSION,
                    source_unit_key="source_unit_ted_dialogue",
                    kind=IngressSourceUnitKind.DIALOGUE,
                    source_start=0,
                    source_end=len(ted_text),
                    exact_text=ted_text,
                    actor_id=None,
                    speaker_id="character:ted",
                    classification_basis="explicit_ingress_speaker",
                ),
            ),
        )
        claim_key = registry.protected_user_claim_manifest()[0]["claim_key"]
        reaction = " Hana nodded."
        story = ted_text + reaction
        realizations = registry.validate_validator_semantics(
            story_text=story,
            story_segments=(
                segment(
                    ted_text,
                    key="segment_ted_dialogue",
                    kind=StoryRealizationKind.DIALOGUE,
                    roles=CharacterRoleLedgerV1(speaker_ids=("character:ted",)),
                    claim_keys=(claim_key,),
                ),
                segment(
                    reaction,
                    key="segment_hana_reaction",
                    start=len(ted_text),
                    roles=CharacterRoleLedgerV1(
                        action_owner_ids=("character:hana_hanezawa",),
                        observing_ids=("character:ted",),
                    ),
                ),
            ),
            allowed_character_ids=("character:hana_hanezawa",),
        )
        self.assertEqual(tuple(value.claim_key for value in realizations), (claim_key,))

    def test_inactive_character_role_is_rejected_without_name_parsing(self) -> None:
        story = "Enne crossed the room."
        with self.assertRaisesRegex(PermissionError, "inactive character role"):
            self.registry().validate_validator_semantics(
                story_text=story,
                story_segments=(
                    segment(
                        story,
                        roles=CharacterRoleLedgerV1(
                            action_owner_ids=("character:enne_hanezawa",)
                        ),
                    ),
                ),
                allowed_character_ids=("character:hana_hanezawa",),
            )

    def test_active_python_path_has_no_prose_semantic_heuristic_fallback(self) -> None:
        validator_source = inspect.getsource(
            RequestEvidenceBindingRegistry.validate_validator_semantics
        )
        self.assertNotIn("re.search", validator_source)
        self.assertNotIn("casefold", validator_source)
        runtime_source = (
            REPOSITORY_ROOT / "src/cera/continuous/runtime.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("validate_composer_realization(", runtime_source)


class RuntimeModelV3ReaderAndDocumentationTests(unittest.TestCase):
    def test_live_harness_reader_is_opt_in_and_independently_accounted(self) -> None:
        harness = object.__new__(JobHarness)
        harness._active_turn_number = 7
        calls: list[tuple[str, str]] = []
        harness.codex_reader = lambda prompt, *, writer_story_text: (
            "reader-result",
            prompt,
            writer_story_text,
        )

        def provider_call(label, owner, operation):
            calls.append((label, owner))
            return operation()

        harness.provider_call = provider_call
        result = _HarnessLiveReaderPort(harness).review(
            "reader-prompt",
            writer_story_text="Hana opened the door.",
        )
        self.assertEqual(
            result,
            ("reader-result", "reader-prompt", "Hana opened the door."),
        )
        self.assertEqual(calls, [("turn-7-reader", "reader")])

        signature = inspect.signature(JobHarness.__init__)
        self.assertIsNone(signature.parameters["reader_transport_factory"].default)
        self.assertEqual(
            signature.parameters["validator_model"].default,
            "gpt-5.6-terra",
        )
        self.assertIn(
            "CodexContinuousReaderPort",
            inspect.getsource(JobHarness.codex_reader),
        )

    def test_reader_schema_has_no_prose_or_rewrite_channel(self) -> None:
        properties = continuous_reader_verdict_json_schema()["properties"]
        forbidden = {
            "story_text",
            "prose",
            "replacement_prose",
            "rewritten_story_text",
            "events",
            "memory",
            "persistence",
        }
        self.assertFalse(forbidden & set(properties))

    def test_reader_verdict_is_bound_to_exact_writer_bytes_and_issue_span(self) -> None:
        story = "Hana opened the door."
        issue = ReaderIssueReferenceV1(
            schema_version=ReaderIssueReferenceV1.SCHEMA_VERSION,
            issue_code="premature_closure",
            output_start=0,
            output_end=len(story),
            exact_text_sha256=text_sha256(story),
            explanation="The response closes before the planned conversational beat.",
        )
        verdict = ReaderVerdictV1(
            schema_version=ReaderVerdictV1.SCHEMA_VERSION,
            verdict_id="reader:verdict_001",
            world_id="world:test",
            branch_id="branch:main",
            turn_id="turn:001",
            candidate_id="candidate:001",
            story_text_sha256=text_sha256(story),
            verdict=ReaderVerdictStatus.REJECTED,
            reason_codes=("premature_closure",),
            issues=(issue,),
            scene_completeness_score=20,
            character_voice_score=80,
            dialogue_pacing_score=40,
            readability_score=80,
        )
        verdict.validate_story_text(story)
        with self.assertRaisesRegex(
            ContractValidationError, "changed Writer bytes"
        ):
            verdict.validate_story_text(story + " ")

    def test_active_document_blocks_point_to_one_role_matrix_and_forbid_writer_semantics(self) -> None:
        active_docs = (
            "docs/authority/CERA_OWNER_ARCHITECTURE.md",
            "docs/authority/DECISIONS_AND_SUPERSESSIONS.md",
            "docs/architecture/RUNTIME_PIPELINE_AND_PORTS.md",
            "docs/architecture/PROMPT_CONTEXT_AND_EXAMPLES.md",
            "docs/contracts/SCHEMA_CATALOG.md",
            "docs/contracts/STATE_MACHINES_AND_ERRORS.md",
            "docs/implementation/ROADMAP_AND_GATE.md",
            "docs/START_HERE.md",
            "docs/handoff/CURRENT.md",
        )
        forbidden = re.compile(
            r"(?:writer|composer)\s+(?:owns|supplies|returns|certifies|creates)\s+"
            r"(?:semantic|claim|consent|role ledger|event|memory|persistence|acceptance)",
            re.IGNORECASE,
        )
        matrix_target = "CERA_RUNTIME_MODEL_V3_ROLE_CONFLICT_MAP.md"
        for relative in active_docs:
            text = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
            match = re.search(
                r"<!-- CERA_RUNTIME_MODEL_V3_ACTIVE_START -->(.*?)"
                r"<!-- CERA_RUNTIME_MODEL_V3_ACTIVE_END -->",
                text,
                re.DOTALL,
            )
            self.assertIsNotNone(match, relative)
            block = match.group(1)
            self.assertIn(matrix_target, block, relative)
            self.assertIsNone(forbidden.search(block), relative)
        controlling = tuple(
            path
            for path in (REPOSITORY_ROOT / "docs").rglob("*.md")
            if "## Controlling role matrix" in path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            controlling,
            (
                REPOSITORY_ROOT
                / "docs/architecture/CERA_RUNTIME_MODEL_V3_ROLE_CONFLICT_MAP.md",
            ),
        )

    def test_frozen_v7_evidence_and_deferred_fallback_invariants(self) -> None:
        report = (
            REPOSITORY_ROOT
            / ".chatgpt/operations/provider-campaigns/2026-08-03-deepseek-v7-flash-nonthinking-repeatability-v1/REPORT.md"
        )
        self.assertEqual(
            hashlib.sha256(report.read_bytes()).hexdigest(),
            "37d472cc5d24dbd3b29e145ade6312fd95405b2d2be346e430550ee7815c98ae",
        )
        fallback = (
            REPOSITORY_ROOT
            / "docs/authority/CERA_CONSENSUAL_ADULT_CAPABILITY_FALLBACK_V1.md"
        ).read_text(encoding="utf-8")
        for required in (
            "maximum of **three total provider attempts**",
            "Outputs from different attempts are never merged",
            "A Python exception",
            "raw restricted-interval prose is never sent to Codex Validator",
            "must not be used as retrieval evidence, memory authority",
            "Only the Safe-Continuity Formatter package may cross back",
        ):
            self.assertIn(required, fallback)


if __name__ == "__main__":
    unittest.main()
