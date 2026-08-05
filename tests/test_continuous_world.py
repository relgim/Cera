from __future__ import annotations

from dataclasses import replace
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cera.continuous import (
    AcceptedTurnPairV1,
    CharacterRoleLedgerV1,
    CharacterSummaryEnvelopeV1,
    CreatedFieldLogEntryV1,
    EventRecordCandidateV1,
    EventItemRoleLedgerV1,
    FinalFieldScopeV1,
    FinalInformationVisibility,
    FinalSequenceItemV1,
    FinalSequenceV1,
    FrozenContinuousIngressFixtureV1,
    SceneSummaryV1,
    ValidatorFinalizationPackageV1,
    ValidatorSemanticStatus,
    ValidatorTaskMode,
    WorldEditOperationKind,
    WorldEditOperationV1,
    ProtectedUserAllowanceMode,
    ProtectedUserAllowanceV1,
    ProhibitedWriterDetailClass,
    RichPlannerSequenceV1,
    RichSequenceBeatV1,
    IngressSourceUnitKind,
    IngressSourceUnitV1,
    ContinuousIngressAuthorityStore,
    PersistenceDirectiveV1,
    PersistenceRecordClass,
    PERSISTENCE_POLICY_SHA256,
    ProtectedSemanticAdjudicationV1,
    ProtectedSemanticRelationKind,
    StoryRealizationKind,
    StoryRealizationSegmentV1,
)
from cera.continuous.world import (
    ContinuousDebugRecorder,
    ContinuousWorldStore,
    SceneChangeCoordinator,
    redact_secrets,
)
from cera.continuous.runtime import (
    ContinuousShadowTurnCoordinator,
    ContinuousTurnRequestV1,
)
from cera.continuous.sessions import (
    ContinuousSessionCompatibilityV1,
    ContinuousSessionCoordinator,
    ContinuousSessionRole,
    InMemoryContinuousStoredSessionPort,
)
from cera.continuous.ingress import build_default_prepared_classifier_registry
from cera.continuous.contracts import (
    DiagnosticGroundingStatus,
    DiagnosticViolationClassification,
    ReaderIssueReferenceV1,
    ReaderVerdictStatus,
    ReaderVerdictV1,
)
from cera.continuous.provider import (
    ContinuousSemanticValidatorDraftV12,
    ContinuousSemanticValidatorResultV1,
    ProviderDiagnosticProtectedSemanticAdjudicationDraftV1,
    ProviderDiagnosticStorySegmentDraftV1,
    ProviderRejectedSemanticStatus,
    ProviderRejectedTurnDecisionDraftV3,
    ProviderRejectedTurnDecisionDraftV4,
    ProviderRejectedViolationDraftV1,
    ProviderWriterRecallEligibility,
)
from cera.continuous.world_mcp import (
    ContinuousWorldMcpBridge,
    ContinuousWorldToolDispatcher,
    WORLD_MCP_MAXIMUM_CALLS,
)
from cera.creator_review.models import (
    CreatorReviewAction,
    CreatorReviewAssessment,
    CreatorReviewSeverity,
    PublicationEligibility,
    ReviewIssueOwner,
)
from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_bytes, canonical_sha256, to_primitive
from cera.serialization import text_sha256


def final_sequence(turn_id: str = "turn-001", *, revision: int = 1) -> FinalSequenceV1:
    return FinalSequenceV1(
        schema_version=FinalSequenceV1.SCHEMA_VERSION,
        sequence_id=f"sequence:{turn_id}",
        accepted_turn_id=turn_id,
        items=(
            FinalSequenceItemV1(
                item_key="verify_arrival",
                planner_beat_keys=("verify_arrival",),
                story_segment_keys=("segment_entire_story",),
                realized_event="Sakura keeps control of the threshold and requests identifying proof.",
                valid_deepseek_additions=("A brief measured pause supports her established caution.",),
                omitted_or_contradicted_details=(),
                private_state_owner_ids=("character:sakura_hanezawa",),
                knowledge_changes=("Sakura now knows the visitor claims to be the expected tenant.",),
                material_changes=(),
                resulting_state="The visitor remains outside awaiting verification.",
                roles=CharacterRoleLedgerV1(
                    action_owner_ids=("character:sakura_hanezawa",),
                    addressed_ids=("character:ted",),
                ),
                field_scopes=(
                    FinalFieldScopeV1(
                        field_name="realized_event",
                        visibility=FinalInformationVisibility.PUBLIC,
                        knowledge_owner_id=None,
                        story_segment_keys=("segment_entire_story",),
                        roles=CharacterRoleLedgerV1(
                            action_owner_ids=("character:sakura_hanezawa",),
                            addressed_ids=("character:ted",),
                        ),
                    ),
                    FinalFieldScopeV1(
                        field_name="valid_deepseek_additions",
                        visibility=FinalInformationVisibility.PUBLIC,
                        knowledge_owner_id=None,
                        story_segment_keys=("segment_entire_story",),
                        roles=CharacterRoleLedgerV1(
                            action_owner_ids=("character:sakura_hanezawa",),
                            addressed_ids=("character:ted",),
                        ),
                    ),
                    FinalFieldScopeV1(
                        field_name="knowledge_changes",
                        visibility=FinalInformationVisibility.CHARACTER_PRIVATE,
                        knowledge_owner_id="character:sakura_hanezawa",
                        story_segment_keys=("segment_entire_story",),
                        roles=CharacterRoleLedgerV1(
                            action_owner_ids=("character:sakura_hanezawa",),
                            addressed_ids=("character:ted",),
                        ),
                        persistence_directives=(
                            PersistenceDirectiveV1(
                                schema_version=PersistenceDirectiveV1.SCHEMA_VERSION,
                                directive_key="record_tenant_claim",
                                target_file="Characters/Sakura.json",
                                target_record_class=PersistenceRecordClass.CHARACTER,
                                persistence_policy_sha256=PERSISTENCE_POLICY_SHA256,
                                target_record_id="character:sakura_hanezawa",
                                target_subject_ids=("character:sakura_hanezawa",),
                                expected_file_revision=revision,
                                operation=WorldEditOperationKind.ADD,
                                field_path=f"/turn_claims/{turn_id}",
                                expected_prior_value_sha256=None,
                                source_value_index=0,
                            ),
                        ),
                    ),
                    FinalFieldScopeV1(
                        field_name="resulting_state",
                        visibility=FinalInformationVisibility.PUBLIC,
                        knowledge_owner_id=None,
                        story_segment_keys=("segment_entire_story",),
                        roles=CharacterRoleLedgerV1(
                            action_owner_ids=("character:sakura_hanezawa",),
                            addressed_ids=("character:ted",),
                        ),
                    ),
                ),
            ),
        ),
        final_stop_state="The visitor remains outside awaiting verification.",
    )


def accepted_pair(
    turn_id: str = "turn-001", *, revision: int = 1
) -> AcceptedTurnPairV1:
    return AcceptedTurnPairV1(
        accepted_turn_id=turn_id,
        user_message="Hello.",
        complete_final_sequence=final_sequence(turn_id, revision=revision),
    )


def good_assessment() -> CreatorReviewAssessment:
    return CreatorReviewAssessment(
        schema_version=CreatorReviewAssessment.SCHEMA_VERSION,
        severity=CreatorReviewSeverity.GOOD,
        publication_eligibility=PublicationEligibility.ACCEPT_ALLOWED,
        issue_owner=ReviewIssueOwner.NONE,
        reason_codes=(),
        creator_reason="The realization preserves the Planner sequence and all hard boundaries.",
        verifier_status="accepted",
    )


def concern_assessment() -> CreatorReviewAssessment:
    return CreatorReviewAssessment(
        schema_version=CreatorReviewAssessment.SCHEMA_VERSION,
        severity=CreatorReviewSeverity.CONCERN,
        publication_eligibility=PublicationEligibility.ACCEPT_ALLOWED,
        issue_owner=ReviewIssueOwner.VERIFIER,
        reason_codes=("possible_invention",),
        creator_reason="The Validator identified a publication-eligible concern.",
        verifier_status="concern",
    )


def character_summary(
    *, source_sha256: str | None = None, source_revision: int = 1
) -> CharacterSummaryEnvelopeV1:
    source_path = "ACTIVE/Characters/Sakura.json"
    summary_text = "Sakura is guarded at an unfamiliar arrival and retains threshold control."
    source_hash = source_sha256 or "0" * 64
    receipt = canonical_sha256(
        {
            "schema_version": CharacterSummaryEnvelopeV1.SCHEMA_VERSION,
            "character_id": "character:sakura_hanezawa",
            "source_path_or_record_id": source_path,
            "source_revision": source_revision,
            "source_sha256": source_hash,
            "source_authority_classification": "active_authoritative_record_fields",
            "summary_field_path": "/reasoning_summary",
            "latest_changes_field_path": "/latest_accepted_changes",
            "summary": summary_text,
            "latest_accepted_changes": (),
        }
    )
    return CharacterSummaryEnvelopeV1(
        schema_version=CharacterSummaryEnvelopeV1.SCHEMA_VERSION,
        character_id="character:sakura_hanezawa",
        source_path_or_record_id=source_path,
        source_revision=source_revision,
        source_sha256=source_hash,
        source_authority_classification="active_authoritative_record_fields",
        summary_field_path="/reasoning_summary",
        latest_changes_field_path="/latest_accepted_changes",
        latest_accepted_changes=(),
        summary=summary_text,
        derivation_receipt_sha256=receipt,
        incomplete=True,
        more_information_available=True,
    )


def package(
    *,
    turn_id: str = "turn-001",
    revision: int = 1,
    story_text: str = "Sakura requests proof.",
) -> ValidatorFinalizationPackageV1:
    return ValidatorFinalizationPackageV1(
        schema_version=ValidatorFinalizationPackageV1.SCHEMA_VERSION,
        package_id=f"package:{turn_id}",
        world_id="world-test",
        branch_id="main",
        task_mode=ValidatorTaskMode.FINALIZE_TURN,
        semantic_status=ValidatorSemanticStatus.ACCEPTED,
        complete_final_sequence=final_sequence(turn_id, revision=revision),
        creator_review=good_assessment(),
        world_edit_operations=(
            WorldEditOperationV1(
                operation_key="record_tenant_claim",
                target_file="Characters/Sakura.json",
                expected_file_revision=revision,
                operation=WorldEditOperationKind.ADD,
                field_path=f"/turn_claims/{turn_id}",
                value="Sakura now knows the visitor claims to be the expected tenant.",
                reason="Persist accepted final field knowledge_changes.",
                source_final_sequence_item="verify_arrival",
                source_final_field_name="knowledge_changes",
                persistence_directive_key="record_tenant_claim",
            ),
        ),
        created_field_log=(
            CreatedFieldLogEntryV1(
                target_file="Characters/Sakura.json",
                field_path=f"/turn_claims/{turn_id}",
                value_type="string",
                value="Sakura now knows the visitor claims to be the expected tenant.",
                reason="Persist accepted final field knowledge_changes.",
                source_final_sequence_item="verify_arrival",
                source_final_field_name="knowledge_changes",
                persistence_directive_key="record_tenant_claim",
            ),
        ),
        event_record=EventRecordCandidateV1(
            event_id=f"event:{turn_id}",
            accepted_turn_id=turn_id,
            scene_id="scene-001",
            participant_ids=("character:sakura_hanezawa", "character:ted"),
            item_role_ledgers=(
                EventItemRoleLedgerV1(
                    schema_version=EventItemRoleLedgerV1.SCHEMA_VERSION,
                    final_sequence_item_key="verify_arrival",
                    roles=final_sequence(turn_id).items[0].roles,
                ),
            ),
            summary="Sakura keeps control of the threshold and requests identifying proof.",
            final_sequence_item_keys=("verify_arrival",),
        ),
        optional_scene_summary=None,
        protected_semantic_adjudications=(
            protected_semantic_adjudication(story_text),
        ),
    )


def protected_semantic_adjudication(
    story_text: str,
) -> ProtectedSemanticAdjudicationV1:
    return ProtectedSemanticAdjudicationV1(
        schema_version=ProtectedSemanticAdjudicationV1.SCHEMA_VERSION,
        adjudication_key="adjudicate_segment_entire_story",
        segment_key="segment_entire_story",
        output_start=0,
        output_end=len(story_text),
        exact_text_sha256=text_sha256(story_text),
        protected_user_id="character:ted",
        relation=ProtectedSemanticRelationKind.ADDRESSED_BY_NPC,
        npc_assertion_owner_ids=("character:sakura_hanezawa",),
        protected_user_source_claim_keys=(),
    )


def scene_summary_package(
    pair: AcceptedTurnPairV1,
    *,
    new_prompt: str,
) -> ValidatorFinalizationPackageV1:
    return ValidatorFinalizationPackageV1(
        schema_version=ValidatorFinalizationPackageV1.SCHEMA_VERSION,
        package_id="package:scene-arrival-summary",
        world_id="world-test",
        branch_id="main",
        task_mode=ValidatorTaskMode.SCENE_SUMMARY,
        semantic_status=ValidatorSemanticStatus.ACCEPTED,
        complete_final_sequence=None,
        creator_review=None,
        world_edit_operations=(),
        created_field_log=(),
        event_record=None,
        optional_scene_summary=SceneSummaryV1(
            schema_version=SceneSummaryV1.SCHEMA_VERSION,
            summary_id="summary:scene-arrival",
            completed_scene_id="scene:arrival",
            accepted_turn_ids=(pair.accepted_turn_id,),
            shortest_complete_summary=(
                "Sakura kept control of the threshold and requested identifying proof."
            ),
            last_five_exact_pairs=(pair,),
            ending_state="Ted remained at the threshold awaiting verification.",
            transition_context="The next accepted prompt begins a later kitchen scene.",
        ),
    )


def rich_sequence() -> RichPlannerSequenceV1:
    return RichPlannerSequenceV1(
        schema_version=RichPlannerSequenceV1.SCHEMA_VERSION,
        sequence_id="sequence:turn_001",
        world_id="world-test",
        branch_id="main",
        accepted_turn_id=None,
        scene_id="scene-001",
        selected_character_ids=("character:sakura_hanezawa",),
        omitted_character_ids=(),
        beats=(
            RichSequenceBeatV1(
                beat_key="verify_arrival",
                roles=CharacterRoleLedgerV1(
                    action_owner_ids=("character:sakura_hanezawa",),
                    addressed_ids=("character:ted",),
                ),
                evidence_grounded_perception="The supplied arrival claim matches the expected-arrival context.",
                immediate_goal="Verify the claim before relaxing the household threshold.",
                relevant_character_pressures=("Protect the household while fulfilling the arrival arrangement.",),
                competing_obligation_or_constraint="The expected arrival is plausible but not yet proven.",
                selected_tactic="Ask one narrow arrangement-specific verification question.",
                causal_explanation="Bounded verification resolves the immediate uncertainty without inventing hostility.",
                observable_action_or_dialogue_direction="Sakura acknowledges the address and requests proof in her formal voice.",
                private_state_guidance="Keep caution owner-private and avoid invented history.",
                physical_material_continuity="Sakura retains control of the doorway and does not invite entry.",
                resulting_state="The threshold remains closed with one clear verification request unanswered.",
                deepseek_realization_space=("Choose exact wording, pacing, and a natural threshold gesture.",),
                protected_user_allowance=ProtectedUserAllowanceV1(
                    mode=ProtectedUserAllowanceMode.NONE,
                    source_binding_keys=(),
                    explanation="Do not invent Ted's answer or movement.",
                ),
                source_evidence_bindings=("source_current_user", "evidence_sakura_guarded"),
            ),
        ),
        final_stop_state="Stop when Ted's unsupplied verification answer is genuinely required.",
        unresolved_threads=("Ted's identity remains pending verification.",),
        provisional=True,
    )


def ingress_units(text: str) -> tuple[IngressSourceUnitV1, ...]:
    return (
        IngressSourceUnitV1(
            schema_version=IngressSourceUnitV1.SCHEMA_VERSION,
            source_unit_key="source_unit_entire_message",
            kind=IngressSourceUnitKind.NARRATION,
            source_start=0,
            source_end=len(text),
            exact_text=text,
            actor_id=None,
            speaker_id=None,
            classification_basis="explicit_ingress_narration",
        ),
    )


def ingress_fixture(text: str, turn_id: str) -> FrozenContinuousIngressFixtureV1:
    idempotency_key = f"continuous-world-{turn_id}"
    return FrozenContinuousIngressFixtureV1(
        schema_version=FrozenContinuousIngressFixtureV1.SCHEMA_VERSION,
        fixture_id=f"cera.fixture.continuous_world.{turn_id}",
        fixture_schema_id="cera.fixture_registry.continuous_world_tests.v1",
        world_id="world-test",
        branch_id="main",
        session_id="session:continuous_world",
        request_id=f"request:{turn_id}",
        turn_id=turn_id,
        idempotency_key_sha256=text_sha256(idempotency_key),
        raw_source=text,
        protected_user_id="character:ted",
        source_units=ingress_units(text),
    )


def make_ingress_authority(
    root: Path, *fixtures: tuple[str, str]
) -> ContinuousIngressAuthorityStore:
    return ContinuousIngressAuthorityStore(
        root,
        fixture_registry=tuple(
            ingress_fixture(text, turn_id) for text, turn_id in fixtures
        ),
    )


def ingress_reference(
    authority: ContinuousIngressAuthorityStore,
    text: str,
    turn_id: str,
) -> dict[str, str]:
    idempotency_key = f"continuous-world-{turn_id}"
    receipt = authority.issue_frozen_fixture(
        fixture_id=f"cera.fixture.continuous_world.{turn_id}",
        idempotency_key=idempotency_key,
    )
    return {
        "session_id": receipt.session_id,
        "request_id": receipt.request_id,
        "idempotency_key_sha256": text_sha256(idempotency_key),
        "ingress_receipt_id": receipt.receipt_id,
        "ingress_receipt_sha256": receipt.receipt_sha256,
    }


def composer_draft(story_text: str):
    return SimpleNamespace(
        story_text=story_text,
        protected_user_realizations=(),
        story_segments=(
            StoryRealizationSegmentV1(
                schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
                segment_key="segment_entire_story",
                kind=StoryRealizationKind.ACTION,
                output_start=0,
                output_end=len(story_text),
                exact_text=story_text,
                roles=CharacterRoleLedgerV1(
                    action_owner_ids=("character:sakura_hanezawa",),
                    addressed_ids=("character:ted",),
                ),
            ),
        ),
    )


def stage_candidate(store: ContinuousWorldStore, candidate_package):
    view = store.create_candidate(
        candidate_package.world_id,
        candidate_package.branch_id,
        candidate_package.complete_final_sequence.accepted_turn_id,
    )
    candidate_sha256 = canonical_sha256(
        {"test_candidate": candidate_package.package_sha256}
    )
    authority_context_sha256 = canonical_sha256(
        {"test_authority": candidate_package.package_sha256}
    )
    store.record_candidate_package(
        candidate_package.world_id,
        candidate_package.branch_id,
        view,
        candidate_package,
        candidate_sha256=candidate_sha256,
        authority_context_sha256=authority_context_sha256,
    )
    return view


def staged_authority_kwargs(
    store: ContinuousWorldStore,
    candidate_package: ValidatorFinalizationPackageV1,
) -> dict[str, str]:
    payload = json.loads(
        (
            store.branch_root(candidate_package.world_id, candidate_package.branch_id)
            / "CANDIDATES"
            / candidate_package.complete_final_sequence.accepted_turn_id
            / "CANDIDATE_AUTHORITY.json"
        ).read_text(encoding="utf-8")
    )
    return {
        "candidate_sha256": payload["candidate_sha256"],
        "authority_context_sha256": payload["authority_context_sha256"],
    }


def session_compatibility(role: ContinuousSessionRole) -> ContinuousSessionCompatibilityV1:
    return ContinuousSessionCompatibilityV1(
        schema_version=ContinuousSessionCompatibilityV1.SCHEMA_VERSION,
        world_id="world-test",
        branch_id="main",
        role=role,
        provider="fake",
        model="fake-planner" if role is ContinuousSessionRole.PLANNER else "fake-validator",
        reasoning_effort="test",
        prompt_version=f"fake-{role.value}-v1",
        output_schema_version=f"fake-{role.value}-output-v1",
        world_directory_identity_sha256=text_sha256("world-test/main"),
        authority_policy_version="test-authority-v1",
        privacy_policy_version="test-privacy-v1",
        protected_user_policy_version="cera.continuous_protected_user_policy.v8",
        session_policy_version="cera.continuous_session_policy.v9_d200",
        ingress_classifier_registry_sha256=(
            build_default_prepared_classifier_registry().registry_sha256
        ),
        persistence_policy_sha256=PERSISTENCE_POLICY_SHA256,
    )


class _FakeStage:
    def __init__(self, value, method: str) -> None:
        self.value = value
        self.method = method

    def plan(self, prompt, **_kwargs):
        value = self.value
        if isinstance(value, RichPlannerSequenceV1):
            keys = tuple(dict.fromkeys(re.findall(r'"binding_key":"(binding_[a-z0-9_]+)"', prompt)))
            value = replace(
                value,
                beats=tuple(
                    replace(beat, source_evidence_bindings=keys[:2])
                    for beat in value.beats
                ),
            )
        if self.method == "validate" and isinstance(
            value, ValidatorFinalizationPackageV1
        ):
            if value.task_mode is ValidatorTaskMode.FINALIZE_TURN:
                request = json.loads(prompt.rsplit("[VALIDATOR REQUEST]\n", 1)[1])
                segments = composer_draft(request["writer_story_text"]).story_segments
            else:
                segments = ()
            value = ContinuousSemanticValidatorResultV1.from_finalization_package(
                finalization_package=value,
                story_segments=segments,
            )
        return SimpleNamespace(
            value=value,
            provider_receipt=None,
            operation_telemetry=None,
            tool_call_count=0,
            failed_tool_call_count=0,
            world_tool_debug=None,
        )

    compose = plan
    validate = plan


class _CountingPlannerStage(_FakeStage):
    def __init__(self, value) -> None:
        super().__init__(value, "plan")
        self.calls = 0

    def plan(self, prompt, **kwargs):
        self.calls += 1
        return super().plan(prompt, **kwargs)


class _QueueComposerStage:
    def __init__(self, *story_texts: str) -> None:
        self.story_texts = list(story_texts)
        self.calls = 0
        self.prompts: list[str] = []

    def compose(self, prompt: str):
        self.calls += 1
        self.prompts.append(prompt)
        value = composer_draft(self.story_texts.pop(0))
        return SimpleNamespace(
            value=value,
            provider_receipt=None,
            operation_telemetry=None,
            tool_call_count=0,
            failed_tool_call_count=0,
            world_tool_debug=None,
        )


class _FakeQueueStage:
    def __init__(self, *values) -> None:
        self.values = list(values)

    def validate(self, prompt, **_kwargs):
        value = self.values.pop(0)
        if value.task_mode is ValidatorTaskMode.FINALIZE_TURN:
            request = json.loads(prompt.rsplit("[VALIDATOR REQUEST]\n", 1)[1])
            segments = composer_draft(request["writer_story_text"]).story_segments
        else:
            segments = ()
        return SimpleNamespace(
            value=ContinuousSemanticValidatorResultV1.from_finalization_package(
                finalization_package=value,
                story_segments=segments,
            ),
            provider_receipt=None,
            operation_telemetry=None,
            tool_call_count=0,
            failed_tool_call_count=0,
        )


class _AcceptingReaderStage:
    def __init__(self) -> None:
        self._counter = 0

    def review(self, prompt, *, writer_story_text):
        self._counter += 1
        request = json.loads(prompt.rsplit("[READER REQUEST]\n", 1)[1])
        verdict = ReaderVerdictV1(
            schema_version=ReaderVerdictV1.SCHEMA_VERSION,
            verdict_id=f"reader:verdict_{self._counter}",
            world_id=request["world_id"],
            branch_id=request["branch_id"],
            turn_id=request["turn_id"],
            candidate_id=request["candidate_id"],
            story_text_sha256=request["writer_mechanical_envelope"][
                "story_text_sha256"
            ],
            verdict=ReaderVerdictStatus.ACCEPTED,
            reason_codes=(),
            issues=(),
            scene_completeness_score=90,
            character_voice_score=90,
            dialogue_pacing_score=90,
            readability_score=90,
        )
        return SimpleNamespace(
            value=verdict,
            provider_receipt=None,
            operation_telemetry=None,
            tool_call_count=0,
            failed_tool_call_count=0,
            world_tool_debug=None,
            physical_session_sha256=text_sha256(
                f"reader:{self._counter}:{request['candidate_id']}"
            ),
        )


class _CountingReaderStage:
    def __init__(self) -> None:
        self.calls = 0

    def review(self, _prompt):
        self.calls += 1
        raise AssertionError("Reader must not run after Validator rejection")


class _RejectingValidatorStage:
    def validate(self, prompt, **_kwargs):
        request = json.loads(prompt.rsplit("[VALIDATOR REQUEST]\n", 1)[1])
        story_text = request["writer_story_text"]
        protected_start = story_text.index("Ted")
        diagnostic_segments = (
            ProviderDiagnosticStorySegmentDraftV1(
                schema_version=(
                    ProviderDiagnosticStorySegmentDraftV1.SCHEMA_VERSION
                ),
                segment_key="sakura_boundary",
                kind=StoryRealizationKind.ACTION,
                output_start=0,
                output_end=protected_start,
                roles=CharacterRoleLedgerV1(
                    action_owner_ids=("character:sakura_hanezawa",),
                ),
                grounding_status=DiagnosticGroundingStatus.GROUNDED,
                protected_user_source_claim_keys=(),
            ),
            ProviderDiagnosticStorySegmentDraftV1(
                schema_version=(
                    ProviderDiagnosticStorySegmentDraftV1.SCHEMA_VERSION
                ),
                segment_key="ungrounded_ted_entry",
                kind=StoryRealizationKind.ACTION,
                output_start=protected_start,
                output_end=len(story_text),
                roles=CharacterRoleLedgerV1(
                    action_owner_ids=("character:ted",),
                ),
                grounding_status=(
                    DiagnosticGroundingStatus.UNGROUNDED_PROTECTED_USER_ASSERTION
                ),
                protected_user_source_claim_keys=(),
            ),
        )
        diagnostic_adjudications = tuple(
            ProviderDiagnosticProtectedSemanticAdjudicationDraftV1(
                schema_version=(
                    ProviderDiagnosticProtectedSemanticAdjudicationDraftV1.SCHEMA_VERSION
                ),
                adjudication_key=f"{segment.segment_key}_adjudication",
                segment_key=segment.segment_key,
                output_start=segment.output_start,
                output_end=segment.output_end,
                protected_user_id="character:ted",
                relation=(
                    ProtectedSemanticRelationKind.PROTECTED_ASSERTION
                    if segment.segment_key == "ungrounded_ted_entry"
                    else ProtectedSemanticRelationKind.NONE
                ),
                grounding_status=segment.grounding_status,
                violation_classification=(
                    DiagnosticViolationClassification.UNGROUNDED_PROTECTED_USER_ASSERTION
                    if segment.segment_key == "ungrounded_ted_entry"
                    else DiagnosticViolationClassification.NONE
                ),
                npc_assertion_owner_ids=(),
                protected_user_source_claim_keys=(),
            )
            for segment in diagnostic_segments
        )
        return SimpleNamespace(
            value=ProviderRejectedTurnDecisionDraftV3(
                schema_version=ProviderRejectedTurnDecisionDraftV3.SCHEMA_VERSION,
                semantic_status=ProviderRejectedSemanticStatus.REJECTED,
                primary_reason_code="mixed_protected_user_action",
                additional_reason_codes=(),
                diagnostic_story_segments=diagnostic_segments,
                diagnostic_protected_semantic_adjudications=(
                    diagnostic_adjudications
                ),
            ).compile(writer_story_text=story_text),
            provider_receipt=None,
            operation_telemetry=None,
            tool_call_count=0,
            failed_tool_call_count=0,
            world_tool_debug=None,
        )


class _RecallThenAcceptValidatorStage:
    def __init__(self) -> None:
        self.calls = 0

    def validate(self, prompt, **_kwargs):
        self.calls += 1
        request = json.loads(prompt.rsplit("[VALIDATOR REQUEST]\n", 1)[1])
        story_text = request["writer_story_text"]
        if self.calls == 1:
            diagnostic_segment = ProviderDiagnosticStorySegmentDraftV1(
                schema_version=(
                    ProviderDiagnosticStorySegmentDraftV1.SCHEMA_VERSION
                ),
                segment_key="unsupported_private_fact",
                kind=StoryRealizationKind.NARRATION,
                output_start=0,
                output_end=len(story_text),
                roles=CharacterRoleLedgerV1(
                    referenced_ids=("character:sakura_hanezawa",),
                ),
                grounding_status=DiagnosticGroundingStatus.GROUNDED,
                protected_user_source_claim_keys=(),
            )
            diagnostic_adjudication = (
                ProviderDiagnosticProtectedSemanticAdjudicationDraftV1(
                    schema_version=(
                        ProviderDiagnosticProtectedSemanticAdjudicationDraftV1.SCHEMA_VERSION
                    ),
                    adjudication_key="unsupported_private_fact_adjudication",
                    segment_key=diagnostic_segment.segment_key,
                    output_start=0,
                    output_end=len(story_text),
                    protected_user_id="character:ted",
                    relation=ProtectedSemanticRelationKind.NONE,
                    grounding_status=DiagnosticGroundingStatus.GROUNDED,
                    violation_classification=(
                        DiagnosticViolationClassification.NONE
                    ),
                    npc_assertion_owner_ids=(),
                    protected_user_source_claim_keys=(),
                )
            )
            decision = ProviderRejectedTurnDecisionDraftV4(
                schema_version=ProviderRejectedTurnDecisionDraftV4.SCHEMA_VERSION,
                semantic_status=ProviderRejectedSemanticStatus.REJECTED,
                primary_reason_code="unauthorized_private_fact",
                additional_reason_codes=(),
                diagnostic_story_segments=(diagnostic_segment,),
                diagnostic_protected_semantic_adjudications=(
                    diagnostic_adjudication,
                ),
                writer_recall_eligibility=(
                    ProviderWriterRecallEligibility.ELIGIBLE
                ),
                writer_recall_violations=(
                    ProviderRejectedViolationDraftV1(
                        schema_version=(
                            ProviderRejectedViolationDraftV1.SCHEMA_VERSION
                        ),
                        segment_key=diagnostic_segment.segment_key,
                        prohibited_detail_classes=(
                            ProhibitedWriterDetailClass.UNAUTHORIZED_PRIVATE_FACT,
                        ),
                    ),
                ),
            )
            value = ContinuousSemanticValidatorDraftV12(
                schema_version=ContinuousSemanticValidatorDraftV12.SCHEMA_VERSION,
                package_id=request["package_id"],
                world_id=request["world_id"],
                branch_id=request["branch_id"],
                decision=decision,
            ).compile(writer_story_text=story_text)
        else:
            value = ContinuousSemanticValidatorResultV1.from_finalization_package(
                finalization_package=package(story_text=story_text),
                story_segments=composer_draft(story_text).story_segments,
            )
        return SimpleNamespace(
            value=value,
            provider_receipt=None,
            operation_telemetry=None,
            tool_call_count=0,
            failed_tool_call_count=0,
            world_tool_debug=None,
        )


class _RejectingReaderStage:
    def __init__(self) -> None:
        self.calls = 0

    def review(self, prompt, *, writer_story_text):
        self.calls += 1
        request = json.loads(prompt.rsplit("[READER REQUEST]\n", 1)[1])
        story_text = request["writer_story_text"]
        verdict = ReaderVerdictV1(
            schema_version=ReaderVerdictV1.SCHEMA_VERSION,
            verdict_id=f"reader:rejected_{self.calls}",
            world_id=request["world_id"],
            branch_id=request["branch_id"],
            turn_id=request["turn_id"],
            candidate_id=request["candidate_id"],
            story_text_sha256=text_sha256(story_text),
            verdict=ReaderVerdictStatus.REJECTED,
            reason_codes=("premature_closure",),
            issues=(
                ReaderIssueReferenceV1(
                    schema_version=ReaderIssueReferenceV1.SCHEMA_VERSION,
                    issue_code="premature_closure",
                    output_start=0,
                    output_end=len(story_text),
                    exact_text_sha256=text_sha256(story_text),
                    explanation="The response stops before the planned beat resolves.",
                ),
            ),
            scene_completeness_score=20,
            character_voice_score=80,
            dialogue_pacing_score=40,
            readability_score=80,
        )
        return SimpleNamespace(
            value=verdict,
            provider_receipt=None,
            operation_telemetry=None,
            tool_call_count=0,
            failed_tool_call_count=0,
            world_tool_debug=None,
            physical_session_sha256=text_sha256(
                f"reader:rejected:{self.calls}:{request['candidate_id']}"
            ),
        )


class _FailStage:
    def plan(self, _prompt, **_kwargs):
        raise RuntimeError("Bearer private-test-token")


class ContinuousWorldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = ContinuousWorldStore(Path(self.temporary.name).resolve() / "continuous_worlds")
        self.root = self.store.initialize("world-test", "main")
        character = self.root / "ACTIVE" / "Characters" / "Sakura.json"
        character.write_bytes(
            canonical_bytes(
                {
                    "schema_version": "cera.continuous_character.v1",
                    "_cera_revision": 1,
                    "character_id": "character:sakura_hanezawa",
                    "reasoning_summary": "Sakura is guarded at an unfamiliar arrival and retains threshold control.",
                    "latest_accepted_changes": [],
                    "turn_claims": {},
                }
            )
            + b"\n"
        )

    def test_layout_and_stable_internal_revision(self) -> None:
        self.assertTrue((self.root / "ACTIVE" / "WORLD_INDEX.jsonl").is_file())
        for name in ("Characters", "Relationships", "Rules", "Locations", "Events", "Scenes"):
            self.assertTrue((self.root / "ACTIVE" / name).is_dir())
        self.assertEqual(
            json.loads((self.root / "ACTIVE" / "Characters" / "Sakura.json").read_text())["_cera_revision"],
            1,
        )

    def test_candidate_isolation_and_atomic_accept(self) -> None:
        before = self.store.tree_sha256(self.root / "ACTIVE")
        view = stage_candidate(self.store, package())
        self.assertEqual(before, self.store.tree_sha256(self.root / "ACTIVE"))
        receipt = self.store.apply_creator_action(
            world_id="world-test",
            branch_id="main",
            turn_id="turn-001",
            action=CreatorReviewAction.ACCEPT,
            package=package(),
            **staged_authority_kwargs(self.store, package()),
            accepted_pair=accepted_pair(),
        )
        self.assertTrue(receipt.accepted)
        self.assertTrue(receipt.planner_append_required)
        payload = json.loads((self.root / "ACTIVE" / "Characters" / "Sakura.json").read_text())
        self.assertEqual(payload["_cera_revision"], 2)
        self.assertIn("turn-001", payload["turn_claims"])
        event_paths = tuple((self.root / "ACTIVE" / "Events").glob("*.json"))
        self.assertTrue(
            any(
                json.loads(path.read_text(encoding="utf-8")).get("event_id")
                == "event:turn-001"
                for path in event_paths
            )
        )

    def test_false_positive_accepts_eligible_concern_and_records_diagnostic(self) -> None:
        candidate = replace(
            package(),
            semantic_status=ValidatorSemanticStatus.CONCERN,
            creator_review=concern_assessment(),
        )
        stage_candidate(self.store, candidate)
        receipt = self.store.apply_creator_action(
            world_id="world-test",
            branch_id="main",
            turn_id="turn-001",
            action=CreatorReviewAction.FALSE_POSITIVE,
            package=candidate,
            **staged_authority_kwargs(self.store, candidate),
            accepted_pair=accepted_pair(),
        )
        self.assertTrue(receipt.accepted)
        diagnostic = tuple((self.root / "VALIDATOR_DIAGNOSTICS").glob("*.json"))
        self.assertEqual(len(diagnostic), 1)
        self.assertFalse(json.loads(diagnostic[0].read_text())["creates_story_constraint"])

    def test_v3_semantic_concern_remains_reviewable_without_becoming_acceptance(self) -> None:
        candidate = replace(
            package(),
            semantic_status=ValidatorSemanticStatus.CONCERN,
            creator_review=concern_assessment(),
        )
        result = ContinuousSemanticValidatorResultV1.from_finalization_package(
            finalization_package=candidate,
            story_segments=composer_draft("Sakura requests proof.").story_segments,
        )
        self.assertEqual(result.semantic_status, ValidatorSemanticStatus.CONCERN)
        self.assertEqual(result.reason_codes, ("possible_invention",))
        self.assertIs(result.finalization_package, candidate)

    def test_false_positive_rejects_good_assessment(self) -> None:
        stage_candidate(self.store, package())
        with self.assertRaisesRegex(StateConflictError, "does not permit"):
            self.store.apply_creator_action(
                world_id="world-test",
                branch_id="main",
                turn_id="turn-001",
                action=CreatorReviewAction.FALSE_POSITIVE,
                package=package(),
                **staged_authority_kwargs(self.store, package()),
                accepted_pair=accepted_pair(),
            )

    def test_nonaccepting_actions_never_change_active(self) -> None:
        for index, action in enumerate(
            (
                CreatorReviewAction.DEEPSEEK_REWRITE,
                CreatorReviewAction.CODEX_REPLAN,
                CreatorReviewAction.CORRECTION_ADJUSTMENT,
                CreatorReviewAction.DECLINE,
            ),
            1,
        ):
            turn = f"turn-{index:03d}"
            candidate = package(turn_id=turn)
            stage_candidate(self.store, candidate)
            before = self.store.tree_sha256(self.root / "ACTIVE")
            receipt = self.store.apply_creator_action(
                world_id="world-test",
                branch_id="main",
                turn_id=turn,
                action=action,
                package=candidate,
                **staged_authority_kwargs(self.store, candidate),
            )
            self.assertFalse(receipt.accepted)
            self.assertEqual(before, self.store.tree_sha256(self.root / "ACTIVE"))

    def test_revision_conflict_rejects_whole_package(self) -> None:
        stage_candidate(self.store, package(revision=9))
        before = self.store.tree_sha256(self.root / "ACTIVE")
        with self.assertRaisesRegex(StateConflictError, "revision precondition"):
            self.store.apply_creator_action(
                world_id="world-test",
                branch_id="main",
                turn_id="turn-001",
                action=CreatorReviewAction.ACCEPT,
                package=package(revision=9),
                **staged_authority_kwargs(self.store, package(revision=9)),
                accepted_pair=accepted_pair(revision=9),
            )
        self.assertEqual(before, self.store.tree_sha256(self.root / "ACTIVE"))

    def test_atomic_multi_file_apply_updates_all_records_together(self) -> None:
        relationship = self.root / "ACTIVE" / "Relationships" / "Sakura_Ted.json"
        relationship.write_bytes(
            canonical_bytes(
                {
                    "schema_version": "cera.continuous_relationship.v1",
                    "_cera_revision": 1,
                    "relationship_id": "relationship:sakura_ted",
                    "participant_ids": [
                        "character:sakura_hanezawa",
                        "character:ted",
                    ],
                    "observations": {},
                }
            )
            + b"\n"
        )
        first = package().world_edit_operations[0]
        second = WorldEditOperationV1(
            operation_key="record_threshold_observation",
            target_file="Relationships/Sakura_Ted.json",
            expected_file_revision=1,
            operation=WorldEditOperationKind.ADD,
            field_path="/observations/turn-001",
            value="The visitor remains outside awaiting verification.",
            reason="Persist accepted final field resulting_state.",
            source_final_sequence_item="verify_arrival",
            source_final_field_name="resulting_state",
            persistence_directive_key="record_threshold_observation",
        )
        base = package()
        base_item = base.complete_final_sequence.items[0]
        revised_scopes = tuple(
            replace(
                scope,
                persistence_directives=(
                    *scope.persistence_directives,
                    PersistenceDirectiveV1(
                        schema_version=PersistenceDirectiveV1.SCHEMA_VERSION,
                        directive_key="record_threshold_observation",
                        target_file="Relationships/Sakura_Ted.json",
                        target_record_class=PersistenceRecordClass.RELATIONSHIP,
                        persistence_policy_sha256=PERSISTENCE_POLICY_SHA256,
                        target_record_id="relationship:sakura_ted",
                        target_subject_ids=(
                            "character:sakura_hanezawa",
                            "character:ted",
                        ),
                        expected_file_revision=1,
                        operation=WorldEditOperationKind.ADD,
                        field_path="/observations/turn-001",
                        expected_prior_value_sha256=None,
                        source_value_index=0,
                    ),
                ),
            )
            if scope.field_name == "resulting_state"
            else scope
            for scope in base_item.field_scopes
        )
        candidate = replace(
            base,
            complete_final_sequence=replace(
                base.complete_final_sequence,
                items=(replace(base_item, field_scopes=revised_scopes),),
            ),
            world_edit_operations=(first, second),
            created_field_log=(
                base.created_field_log[0],
                CreatedFieldLogEntryV1(
                    target_file=second.target_file,
                    field_path=second.field_path,
                    value_type="string",
                    value=second.value,
                    reason=second.reason,
                    source_final_sequence_item=second.source_final_sequence_item,
                    source_final_field_name="resulting_state",
                    persistence_directive_key="record_threshold_observation",
                ),
            ),
        )
        stage_candidate(self.store, candidate)
        receipt = self.store.apply_creator_action(
            world_id="world-test",
            branch_id="main",
            turn_id="turn-001",
            action=CreatorReviewAction.ACCEPT,
            package=candidate,
            **staged_authority_kwargs(self.store, candidate),
            accepted_pair=replace(
                accepted_pair(),
                complete_final_sequence=candidate.complete_final_sequence,
            ),
        )
        self.assertEqual(
            receipt.changed_files,
            ("Characters/Sakura.json", "Relationships/Sakura_Ted.json"),
        )
        self.assertEqual(
            json.loads(relationship.read_text(encoding="utf-8"))["_cera_revision"],
            2,
        )

    def test_v8_persistence_policy_rejects_metadata_and_pointer_escapes(self) -> None:
        character = final_sequence().items[0].field_scopes[2].persistence_directives[0]
        for path in (
            "/character_id",
            "/schema_version",
            "/_cera_revision",
            "/visibility",
            "/knowledge_owner_id",
            "/source_path",
            "/source_sha256",
            "/genesis_records",
            "/provenance",
            "/index_metadata",
            "/turn_claims/~1character_id",
            "/turn_claims/..",
            "/turn_claims/~2escape",
        ):
            with self.subTest(record_class="character", path=path), self.assertRaises(
                ContractValidationError
            ):
                replace(character, field_path=path)

        relationship = replace(
            character,
            target_file="Relationships/Sakura_Ted.json",
            target_record_class=PersistenceRecordClass.RELATIONSHIP,
            target_record_id="relationship:sakura_ted",
            target_subject_ids=("character:sakura_hanezawa", "character:ted"),
            field_path="/observations/turn-001",
        )
        for path in (
            "/relationship_id",
            "/participant_ids",
            "/schema_version",
            "/_cera_revision",
            "/source_path",
            "/source_sha256",
            "/genesis_records",
            "/provenance",
            "/index_metadata",
            "/observations/~1participant_ids",
        ):
            with self.subTest(record_class="relationship", path=path), self.assertRaises(
                ContractValidationError
            ):
                replace(relationship, field_path=path)

    def test_v8_relationship_target_must_be_justified_by_final_field_roles(self) -> None:
        relationship_path = self.root / "ACTIVE" / "Relationships" / "Mia_Ted.json"
        relationship_path.write_bytes(
            canonical_bytes(
                {
                    "schema_version": "cera.continuous_relationship.v1",
                    "_cera_revision": 1,
                    "relationship_id": "relationship:mia_ted",
                    "participant_ids": ["character:mia_hanezawa", "character:ted"],
                    "observations": {},
                }
            )
            + b"\n"
        )
        base = package()
        item = base.complete_final_sequence.items[0]
        directive = PersistenceDirectiveV1(
            schema_version=PersistenceDirectiveV1.SCHEMA_VERSION,
            directive_key="record_unrelated_relationship",
            target_file="Relationships/Mia_Ted.json",
            target_record_class=PersistenceRecordClass.RELATIONSHIP,
            persistence_policy_sha256=PERSISTENCE_POLICY_SHA256,
            target_record_id="relationship:mia_ted",
            target_subject_ids=("character:mia_hanezawa", "character:ted"),
            expected_file_revision=1,
            operation=WorldEditOperationKind.ADD,
            field_path="/observations/turn-001",
            expected_prior_value_sha256=None,
            source_value_index=0,
        )
        scopes = tuple(
            replace(scope, persistence_directives=(directive,))
            if scope.field_name == "resulting_state"
            else scope
            for scope in item.field_scopes
        )
        sequence = replace(
            base.complete_final_sequence,
            items=(replace(item, field_scopes=scopes),),
        )
        operation = WorldEditOperationV1(
            operation_key=directive.directive_key,
            target_file=directive.target_file,
            expected_file_revision=1,
            operation=WorldEditOperationKind.ADD,
            field_path=directive.field_path,
            value=item.resulting_state,
            reason="Persist accepted final field resulting_state.",
            source_final_sequence_item=item.item_key,
            source_final_field_name="resulting_state",
            persistence_directive_key=directive.directive_key,
        )
        candidate = replace(
            base,
            complete_final_sequence=sequence,
            world_edit_operations=(base.world_edit_operations[0], operation),
            created_field_log=(
                base.created_field_log[0],
                CreatedFieldLogEntryV1(
                    target_file=operation.target_file,
                    field_path=operation.field_path,
                    value_type="string",
                    value=operation.value,
                    reason=operation.reason,
                    source_final_sequence_item=item.item_key,
                    source_final_field_name="resulting_state",
                    persistence_directive_key=directive.directive_key,
                ),
            ),
        )
        stage_candidate(self.store, candidate)
        before = self.store.tree_sha256(self.root / "ACTIVE")
        with self.assertRaisesRegex(StateConflictError, "not justified"):
            self.store.apply_creator_action(
                world_id="world-test",
                branch_id="main",
                turn_id="turn-001",
                action=CreatorReviewAction.ACCEPT,
                package=candidate,
                **staged_authority_kwargs(self.store, candidate),
                accepted_pair=replace(
                    accepted_pair(), complete_final_sequence=sequence
                ),
            )
        self.assertEqual(before, self.store.tree_sha256(self.root / "ACTIVE"))

    def test_v8_post_edit_validation_rejects_json_valid_record_invalidity(self) -> None:
        base = package()
        item = base.complete_final_sequence.items[0]
        knowledge_scope = next(
            scope for scope in item.field_scopes if scope.field_name == "knowledge_changes"
        )
        directive = replace(
            knowledge_scope.persistence_directives[0],
            directive_key="replace_changes_with_scalar",
            operation=WorldEditOperationKind.REPLACE,
            field_path="/latest_accepted_changes",
            expected_prior_value_sha256=canonical_sha256([]),
        )
        scopes = tuple(
            replace(scope, persistence_directives=(directive,))
            if scope.field_name == "knowledge_changes"
            else scope
            for scope in item.field_scopes
        )
        sequence = replace(
            base.complete_final_sequence,
            items=(replace(item, field_scopes=scopes),),
        )
        value = item.knowledge_changes[0]
        operation = WorldEditOperationV1(
            operation_key=directive.directive_key,
            target_file=directive.target_file,
            expected_file_revision=1,
            operation=WorldEditOperationKind.REPLACE,
            field_path=directive.field_path,
            value=value,
            reason="Persist accepted final field knowledge_changes.",
            source_final_sequence_item=item.item_key,
            source_final_field_name="knowledge_changes",
            persistence_directive_key=directive.directive_key,
        )
        candidate = replace(
            base,
            complete_final_sequence=sequence,
            world_edit_operations=(operation,),
            created_field_log=(),
        )
        stage_candidate(self.store, candidate)
        before = self.store.tree_sha256(self.root / "ACTIVE")
        with self.assertRaisesRegex(StateConflictError, "semantic field type"):
            self.store.apply_creator_action(
                world_id="world-test",
                branch_id="main",
                turn_id="turn-001",
                action=CreatorReviewAction.ACCEPT,
                package=candidate,
                **staged_authority_kwargs(self.store, candidate),
                accepted_pair=replace(
                    accepted_pair(), complete_final_sequence=sequence
                ),
            )
        self.assertEqual(before, self.store.tree_sha256(self.root / "ACTIVE"))

    def test_v8_approved_character_replace_remains_atomic(self) -> None:
        base = package()
        item = base.complete_final_sequence.items[0]
        prior = json.loads(
            (self.root / "ACTIVE" / "Characters" / "Sakura.json").read_text(
                encoding="utf-8"
            )
        )["reasoning_summary"]
        directive = replace(
            item.field_scopes[2].persistence_directives[0],
            directive_key="replace_reasoning_summary",
            operation=WorldEditOperationKind.REPLACE,
            field_path="/reasoning_summary",
            expected_prior_value_sha256=canonical_sha256(prior),
        )
        scopes = tuple(
            replace(scope, persistence_directives=(directive,))
            if scope.field_name == "realized_event"
            else replace(scope, persistence_directives=())
            if scope.field_name == "knowledge_changes"
            else scope
            for scope in item.field_scopes
        )
        sequence = replace(
            base.complete_final_sequence,
            items=(replace(item, field_scopes=scopes),),
        )
        operation = WorldEditOperationV1(
            operation_key=directive.directive_key,
            target_file=directive.target_file,
            expected_file_revision=1,
            operation=WorldEditOperationKind.REPLACE,
            field_path=directive.field_path,
            value=item.realized_event,
            reason="Persist accepted final field realized_event.",
            source_final_sequence_item=item.item_key,
            source_final_field_name="realized_event",
            persistence_directive_key=directive.directive_key,
        )
        candidate = replace(
            base,
            complete_final_sequence=sequence,
            world_edit_operations=(operation,),
            created_field_log=(),
        )
        stage_candidate(self.store, candidate)
        receipt = self.store.apply_creator_action(
            world_id="world-test",
            branch_id="main",
            turn_id="turn-001",
            action=CreatorReviewAction.ACCEPT,
            package=candidate,
            **staged_authority_kwargs(self.store, candidate),
            accepted_pair=replace(
                accepted_pair(), complete_final_sequence=sequence
            ),
        )
        self.assertTrue(receipt.accepted)
        record = json.loads(
            (self.root / "ACTIVE" / "Characters" / "Sakura.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(record["reasoning_summary"], item.realized_event)
        self.assertEqual(record["_cera_revision"], 2)

    def test_create_file_and_operation_ceiling(self) -> None:
        create = WorldEditOperationV1(
            operation_key="create_thread",
            target_file="Rules/arrival_rule.json",
            expected_file_revision=None,
            operation=WorldEditOperationKind.CREATE_FILE,
            field_path="/",
            value={"schema_version": "cera.rule.v1", "rule_id": "rule:arrival"},
            reason="An accepted creator-approved rule candidate requires a stable file.",
            source_final_sequence_item="verify_arrival",
        )
        with self.assertRaisesRegex(
            ContractValidationError, "untyped persistence transform"
        ):
            replace(
                package(),
                world_edit_operations=(create,),
                created_field_log=(),
            )
        with self.assertRaisesRegex(ContractValidationError, "ceiling"):
            replace(package(), world_edit_operations=tuple(create for _ in range(101)))

    def test_scene_change_uses_allow_list_tail_and_excludes_new_prompt(self) -> None:
        pairs = []
        for index in range(1, 7):
            turn = f"turn-{index:03d}"
            pair = AcceptedTurnPairV1(
                accepted_turn_id=turn,
                user_message=f"accepted old prompt {index}",
                complete_final_sequence=final_sequence(turn),
            )
            self.store.write_accepted_pair("world-test", "main", pair)
            pairs.append(pair)

        new_prompt = "Several days later, Ted asks Mia about Sakura."

        def summarize(values):
            return SceneSummaryV1(
                schema_version=SceneSummaryV1.SCHEMA_VERSION,
                summary_id="summary:arrival",
                completed_scene_id="scene:arrival",
                accepted_turn_ids=tuple(value.accepted_turn_id for value in values),
                shortest_complete_summary="Sakura verified the arriving tenant while preserving the household threshold.",
                last_five_exact_pairs=values[-5:],
                ending_state="Ted's identity was accepted and the arrival scene closed.",
                transition_context="The next prompt begins several days later in the kitchen.",
            )

        envelope = SceneChangeCoordinator(self.store).process(
            world_id="world-test",
            branch_id="main",
            completed_scene_id="scene:arrival",
            first_new_scene_prompt=new_prompt,
            accepted_turn_ids=tuple(value.accepted_turn_id for value in pairs),
            summarize=summarize,
        )
        self.assertEqual(
            tuple(value.accepted_turn_id for value in envelope.previous_scene_summary.last_five_exact_pairs),
            tuple(value.accepted_turn_id for value in pairs[-5:]),
        )
        self.assertNotIn(new_prompt, envelope.previous_scene_summary.shortest_complete_summary)
        self.assertIn(new_prompt, envelope.render_for_planner())

    def test_debug_artifacts_are_complete_replayable_and_secret_free(self) -> None:
        recorder = ContinuousDebugRecorder(self.root, "scene-001", "turn-001")
        for name in recorder.REQUIRED_ARTIFACTS:
            if name == "planner_raw_prompt.txt":
                recorder.write_text(name, "Bearer secret-value")
            else:
                recorder.write_json(name, {"api_key": "secret", "value": name})
        self.assertEqual(recorder.validate_complete(), ())
        self.assertEqual(recorder.replay_input()["api_key"], "[REDACTED]")
        for path in recorder.root.iterdir():
            self.assertNotIn("secret-value", path.read_text(encoding="utf-8"))
            self.assertNotIn('"secret"', path.read_text(encoding="utf-8"))

    def test_branch_isolation(self) -> None:
        sibling = self.store.initialize("world-test", "sibling")
        self.store.create_candidate("world-test", "main", "turn-001")
        self.assertFalse((sibling / "CANDIDATES" / "turn-001").exists())

    def test_world_lookup_dispatcher_is_role_scoped_and_ceiling_bound(self) -> None:
        planner = ContinuousWorldToolDispatcher(
            self.root, ContinuousSessionRole.PLANNER, maximum_calls=3
        )
        listed = planner.invoke("cera_world_list", {"prefix": "ACTIVE/Characters", "limit": 10})
        self.assertEqual(len(listed["records"]), 1)
        searched = planner.invoke(
            "cera_world_search",
            {"terms": ["sakura_hanezawa"], "record_types": ["characters"], "limit": 10},
        )
        self.assertEqual(len(searched["records"]), 1)
        exact = planner.invoke(
            "cera_world_read", {"path": searched["records"][0]["path"]}
        )
        self.assertEqual(exact["content"]["character_id"], "character:sakura_hanezawa")
        with self.assertRaisesRegex(StateConflictError, "runaway ceiling"):
            planner.invoke("cera_world_list", {"prefix": "", "limit": 1})

        self.store.create_candidate("world-test", "main", "turn-001")
        denied = (
            "CANDIDATES/turn-001/ACTIVE_VIEW/Characters/Sakura.json"
        )
        with self.assertRaises(PermissionError):
            ContinuousWorldToolDispatcher(
                self.root, ContinuousSessionRole.PLANNER
            ).invoke("cera_world_read", {"path": denied})
        validator = ContinuousWorldToolDispatcher(
            self.root,
            ContinuousSessionRole.VALIDATOR,
            current_turn_id="turn-001",
        )
        self.assertEqual(
            validator.invoke("cera_world_read", {"path": denied})["content"]["character_id"],
            "character:sakura_hanezawa",
        )

    def test_world_mcp_binding_uses_highest_supported_runaway_ceiling(self) -> None:
        dispatcher = ContinuousWorldToolDispatcher(
            self.root, ContinuousSessionRole.PLANNER
        )
        with ContinuousWorldMcpBridge(dispatcher) as bridge:
            binding = bridge.runtime_binding
            self.assertEqual(binding.maximum_tool_calls, WORLD_MCP_MAXIMUM_CALLS)
            self.assertEqual(binding.minimum_tool_calls, 0)
            self.assertEqual(binding.enabled_tools, (
                "cera_world_list",
                "cera_world_search",
                "cera_world_read",
            ))

    def test_provider_free_turn_coordinator_prepares_then_accepts_once(self) -> None:
        port = InMemoryContinuousStoredSessionPort()
        message = "Hello, my name is Ted. Is this the Hanezawa residence?"
        ingress_authority = make_ingress_authority(
            self.root.parent / "ingress_authority_turn_1",
            (message, "turn-001"),
        )
        planner_session = ContinuousSessionCoordinator(
            session_compatibility(ContinuousSessionRole.PLANNER), port
        )
        validator_session = ContinuousSessionCoordinator(
            session_compatibility(ContinuousSessionRole.VALIDATOR), port
        )
        coordinator = ContinuousShadowTurnCoordinator(
            world=self.store,
            planner_session=planner_session,
            validator_session=validator_session,
            planner=_FakeStage(rich_sequence(), "plan"),
            composer=_FakeStage(
                composer_draft("Sakura keeps the threshold and requests proof."),
                "compose",
            ),
            validator=_FakeStage(
                package(
                    story_text="Sakura keeps the threshold and requests proof."
                ),
                "validate",
            ),
            reader=_AcceptingReaderStage(),
            ingress_authority=ingress_authority,
        )
        candidate = coordinator.prepare(
            ContinuousTurnRequestV1(
                world_id="world-test",
                branch_id="main",
                scene_id="scene-001",
                turn_id="turn-001",
                user_message=message,
                **ingress_reference(ingress_authority, message, "turn-001"),
                character_summaries=(character_summary(source_sha256=text_sha256(
                    (self.root / "ACTIVE" / "Characters" / "Sakura.json").read_text(encoding="utf-8")
                )),),
            )
        )
        self.assertEqual(candidate.provider_calls, 0)
        candidate.writer_mechanical_envelope.validate_story_text(
            candidate.deepseek_story_text
        )
        role_session_hashes = {
            planner_session.ensure_session().provider_thread_id_sha256,
            validator_session.ensure_session().provider_thread_id_sha256,
            candidate.reader_session_sha256,
        }
        self.assertEqual(len(role_session_hashes), 3)
        original_candidate_sha256 = candidate.candidate_sha256
        changed_ingress = replace(
            candidate,
            request=replace(candidate.request, ingress_receipt_sha256="f" * 64),
        )
        self.assertNotEqual(
            original_candidate_sha256,
            changed_ingress.candidate_sha256,
        )
        with patch(
            "cera.continuous.runtime.PERSISTENCE_POLICY_SHA256",
            "e" * 64,
        ):
            self.assertNotEqual(
                original_candidate_sha256,
                candidate.candidate_sha256,
            )
        self.assertEqual(
            planner_session.ensure_session().provider_thread_id,
            planner_session.ensure_session().provider_thread_id,
        )
        receipt = coordinator.apply_creator_action("turn-001", CreatorReviewAction.ACCEPT)
        self.assertTrue(receipt.accepted)
        self.assertEqual(planner_session.snapshot().accepted_turn_ids, ("turn-001",))
        self.assertEqual(planner_session.unsynchronized_accepted_turn_ids, ())
        self.assertEqual(
            len(
                port.model_visible_context[
                    planner_session.ensure_session().provider_thread_id
                ]
            ),
            1,
        )
        restored = self.store.accepted_final_envelope(
            "world-test", "main", "turn-001"
        )
        self.assertEqual(
            restored.envelope_sha256,
            next(
                value.payload_sha256
                for value in planner_session.snapshot().context_events
                if value.event_type == "accepted_final_sequence"
            ),
        )
        debug = ContinuousDebugRecorder(self.root, "scene-001", "turn-001")
        self.assertEqual(debug.validate_complete(), ())
        self.assertTrue(
            json.loads((debug.root / "exact_diff.json").read_text(encoding="utf-8"))
        )

    def test_validator_rejection_stops_before_reader_candidate_and_story_mutation(self) -> None:
        port = InMemoryContinuousStoredSessionPort()
        message = "Hello."
        ingress_authority = make_ingress_authority(
            self.root.parent / "ingress_authority_validator_reject",
            (message, "turn-001"),
        )
        reader = _CountingReaderStage()
        coordinator = ContinuousShadowTurnCoordinator(
            world=self.store,
            planner_session=ContinuousSessionCoordinator(
                session_compatibility(ContinuousSessionRole.PLANNER), port
            ),
            validator_session=ContinuousSessionCoordinator(
                session_compatibility(ContinuousSessionRole.VALIDATOR), port
            ),
            planner=_FakeStage(rich_sequence(), "plan"),
            composer=_FakeStage(
                composer_draft("Sakura watches as Ted steps inside."), "compose"
            ),
            validator=_RejectingValidatorStage(),
            reader=reader,
            ingress_authority=ingress_authority,
        )
        before = self.store.tree_sha256(self.root / "ACTIVE")
        with self.assertRaisesRegex(PermissionError, "mixed_protected_user_action"):
            coordinator.prepare(
                ContinuousTurnRequestV1(
                    world_id="world-test",
                    branch_id="main",
                    scene_id="scene-001",
                    turn_id="turn-001",
                    user_message=message,
                    **ingress_reference(
                        ingress_authority, message, "turn-001"
                    ),
                    character_summaries=(
                        character_summary(
                            source_sha256=text_sha256(
                                (
                                    self.root
                                    / "ACTIVE"
                                    / "Characters"
                                    / "Sakura.json"
                                ).read_text(encoding="utf-8")
                            )
                        ),
                    ),
                )
            )
        self.assertEqual(reader.calls, 0)
        self.assertEqual(coordinator._candidates, {})
        self.assertEqual(before, self.store.tree_sha256(self.root / "ACTIVE"))
        self.assertEqual(coordinator.planner_session.snapshot().accepted_turn_ids, ())

    def test_reader_rejection_exhausts_bounded_recall_without_story_authority(self) -> None:
        port = InMemoryContinuousStoredSessionPort()
        message = "Hello."
        ingress_authority = make_ingress_authority(
            self.root.parent / "ingress_authority_reader_reject",
            (message, "turn-001"),
        )
        reader = _RejectingReaderStage()
        coordinator = ContinuousShadowTurnCoordinator(
            world=self.store,
            planner_session=ContinuousSessionCoordinator(
                session_compatibility(ContinuousSessionRole.PLANNER), port
            ),
            validator_session=ContinuousSessionCoordinator(
                session_compatibility(ContinuousSessionRole.VALIDATOR), port
            ),
            planner=_FakeStage(rich_sequence(), "plan"),
            composer=_FakeStage(
                composer_draft("Sakura keeps the threshold."), "compose"
            ),
            validator=_FakeStage(
                package(story_text="Sakura keeps the threshold."), "validate"
            ),
            reader=reader,
            ingress_authority=ingress_authority,
        )
        before = self.store.tree_sha256(self.root / "ACTIVE")
        with self.assertRaisesRegex(PermissionError, "Reader rejected"):
            coordinator.prepare(
                ContinuousTurnRequestV1(
                    world_id="world-test",
                    branch_id="main",
                    scene_id="scene-001",
                    turn_id="turn-001",
                    user_message=message,
                    **ingress_reference(
                        ingress_authority, message, "turn-001"
                    ),
                    character_summaries=(
                        character_summary(
                            source_sha256=text_sha256(
                                (
                                    self.root
                                    / "ACTIVE"
                                    / "Characters"
                                    / "Sakura.json"
                                ).read_text(encoding="utf-8")
                            )
                        ),
                    ),
                )
            )
        self.assertEqual(reader.calls, 3)
        self.assertEqual(coordinator._candidates, {})
        self.assertEqual(before, self.store.tree_sha256(self.root / "ACTIVE"))
        self.assertEqual(coordinator.planner_session.snapshot().accepted_turn_ids, ())
        debug = ContinuousDebugRecorder(self.root, "scene-001", "turn-001")
        self.assertTrue(
            (debug.root / "WRITER_ATTEMPTS" / "attempt-002").is_dir()
        )
        self.assertTrue(
            (debug.root / "WRITER_ATTEMPTS" / "attempt-003").is_dir()
        )

    def test_validator_recall_reuses_one_planner_and_accepts_first_safe_attempt(
        self,
    ) -> None:
        port = InMemoryContinuousStoredSessionPort()
        message = "Hello."
        ingress_authority = make_ingress_authority(
            self.root.parent / "ingress_authority_validator_recall",
            (message, "turn-001"),
        )
        planner = _CountingPlannerStage(rich_sequence())
        composer = _QueueComposerStage(
            "Sakura privately decided what Ted must be feeling.",
            "Sakura keeps the threshold and requests proof.",
        )
        validator = _RecallThenAcceptValidatorStage()
        reader = _AcceptingReaderStage()
        coordinator = ContinuousShadowTurnCoordinator(
            world=self.store,
            planner_session=ContinuousSessionCoordinator(
                session_compatibility(ContinuousSessionRole.PLANNER), port
            ),
            validator_session=ContinuousSessionCoordinator(
                session_compatibility(ContinuousSessionRole.VALIDATOR), port
            ),
            planner=planner,
            composer=composer,
            validator=validator,
            reader=reader,
            ingress_authority=ingress_authority,
        )
        before = self.store.tree_sha256(self.root / "ACTIVE")
        candidate = coordinator.prepare(
            ContinuousTurnRequestV1(
                world_id="world-test",
                branch_id="main",
                scene_id="scene-001",
                turn_id="turn-001",
                user_message=message,
                **ingress_reference(ingress_authority, message, "turn-001"),
                character_summaries=(
                    character_summary(
                        source_sha256=text_sha256(
                            (
                                self.root
                                / "ACTIVE"
                                / "Characters"
                                / "Sakura.json"
                            ).read_text(encoding="utf-8")
                        )
                    ),
                ),
            )
        )
        self.assertEqual(planner.calls, 1)
        self.assertEqual(composer.calls, 2)
        self.assertEqual(validator.calls, 2)
        self.assertEqual(reader._counter, 1)
        self.assertEqual(
            candidate.deepseek_story_text,
            "Sakura keeps the threshold and requests proof.",
        )
        self.assertEqual(before, self.store.tree_sha256(self.root / "ACTIVE"))
        self.assertEqual(coordinator.planner_session.snapshot().accepted_turn_ids, ())

        debug = ContinuousDebugRecorder(self.root, "scene-001", "turn-001")
        attempt_two = debug.root / "WRITER_ATTEMPTS" / "attempt-002"
        self.assertTrue(attempt_two.is_dir())
        self.assertFalse(
            (debug.root / "WRITER_ATTEMPTS" / "attempt-003").exists()
        )
        first_directive = json.loads(
            (debug.root / "writer_recall_directive.json").read_text(
                encoding="utf-8"
            )
        )
        second_input = json.loads(
            (attempt_two / "writer_recall_input.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(first_directive, second_input)
        self.assertEqual(first_directive["source_attempt_number"], 1)
        self.assertEqual(first_directive["next_attempt_number"], 2)
        self.assertEqual(
            (debug.root / "planner_output.json").read_bytes(),
            (attempt_two / "planner_output.json").read_bytes(),
        )
        self.assertNotEqual(
            (debug.root / "deepseek_output.json").read_bytes(),
            (attempt_two / "deepseek_output.json").read_bytes(),
        )

    def test_provider_failure_leaves_complete_secret_free_debug_skeleton(self) -> None:
        port = InMemoryContinuousStoredSessionPort()
        ingress_authority = make_ingress_authority(
            self.root.parent / "ingress_authority_failure",
            ("Hello.", "turn-001"),
        )
        coordinator = ContinuousShadowTurnCoordinator(
            world=self.store,
            planner_session=ContinuousSessionCoordinator(
                session_compatibility(ContinuousSessionRole.PLANNER), port
            ),
            validator_session=ContinuousSessionCoordinator(
                session_compatibility(ContinuousSessionRole.VALIDATOR), port
            ),
            planner=_FailStage(),
            composer=_FakeStage(composer_draft("unused"), "compose"),
            validator=_FakeStage(package(), "validate"),
            reader=_AcceptingReaderStage(),
            ingress_authority=ingress_authority,
        )
        with self.assertRaises(RuntimeError):
            coordinator.prepare(
                ContinuousTurnRequestV1(
                    world_id="world-test",
                    branch_id="main",
                    scene_id="scene-001",
                    turn_id="turn-001",
                    user_message="Hello.",
                    **ingress_reference(ingress_authority, "Hello.", "turn-001"),
                )
            )
        debug = ContinuousDebugRecorder(self.root, "scene-001", "turn-001")
        self.assertEqual(debug.validate_complete(), ())
        error_text = (debug.root / "errors.json").read_text(encoding="utf-8")
        self.assertIn('"stage":"planner"', error_text)
        self.assertNotIn("private-test-token", error_text)

    def test_scene_change_uses_same_sessions_and_calls_validator_summary_before_planner(self) -> None:
        port = InMemoryContinuousStoredSessionPort()
        new_prompt = "Several days later, Ted asks Mia about Sakura."
        ingress_authority = make_ingress_authority(
            self.root.parent / "ingress_authority_scene_change",
            (new_prompt, "turn-002"),
        )
        planner_session = ContinuousSessionCoordinator(
            session_compatibility(ContinuousSessionRole.PLANNER), port
        )
        validator_session = ContinuousSessionCoordinator(
            session_compatibility(ContinuousSessionRole.VALIDATOR), port
        )
        pair = AcceptedTurnPairV1(
            accepted_turn_id="turn-001",
            user_message="Hello, my name is Ted. Is this the Hanezawa residence?",
            complete_final_sequence=final_sequence("turn-001"),
        )
        self.store.write_accepted_pair("world-test", "main", pair)
        coordinator = ContinuousShadowTurnCoordinator(
            world=self.store,
            planner_session=planner_session,
            validator_session=validator_session,
            planner=_FakeStage(
                replace(rich_sequence(), scene_id="scene-002"), "plan"
            ),
            composer=_FakeStage(
                composer_draft("Mia answers cautiously in the kitchen."),
                "compose",
            ),
            validator=_FakeQueueStage(
                scene_summary_package(pair, new_prompt=new_prompt),
                replace(
                    package(
                        turn_id="turn-002",
                        story_text="Mia answers cautiously in the kitchen.",
                    ),
                    event_record=replace(
                        package(
                            turn_id="turn-002",
                            story_text="Mia answers cautiously in the kitchen.",
                        ).event_record,
                        scene_id="scene-002",
                    ),
                ),
            ),
            reader=_AcceptingReaderStage(),
            ingress_authority=ingress_authority,
        )
        planner_thread = planner_session.ensure_session().provider_thread_id
        validator_thread = validator_session.ensure_session().provider_thread_id
        result = coordinator.prepare_scene_change(
            ContinuousTurnRequestV1(
                world_id="world-test",
                branch_id="main",
                scene_id="scene-002",
                turn_id="turn-002",
                user_message=new_prompt,
                **ingress_reference(ingress_authority, new_prompt, "turn-002"),
                character_summaries=(character_summary(source_sha256=text_sha256(
                    (self.root / "ACTIVE" / "Characters" / "Sakura.json").read_text(encoding="utf-8")
                )),),
                cera_scene_change=True,
            ),
            completed_scene_id="scene:arrival",
            accepted_turn_ids=("turn-001",),
        )
        self.assertEqual(
            planner_session.ensure_session().provider_thread_id, planner_thread
        )
        self.assertEqual(
            validator_session.ensure_session().provider_thread_id, validator_thread
        )
        self.assertEqual(result.summary_provider_calls, 0)
        self.assertNotIn(new_prompt, result.scene_change_envelope.previous_scene_summary.shortest_complete_summary)
        self.assertEqual(
            result.scene_change_envelope.previous_scene_summary.last_five_exact_pairs,
            (pair,),
        )


if __name__ == "__main__":
    unittest.main()
