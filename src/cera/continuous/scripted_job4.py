"""Closed provider-free fixtures for executable Continuous Job 4 qualification.

This module is imported only behind an exact scripted command-line
confirmation.  Its transports cross the real adapter and call-ledger seams but
cannot dispatch an external request.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cera.creator_review.models import (
    CreatorReviewAssessment,
    CreatorReviewSeverity,
    PublicationEligibility,
    ReviewIssueOwner,
)
from cera.serialization import bytes_sha256, text_sha256, to_primitive

from .contracts import (
    CharacterRoleLedgerV1,
    FinalFieldScopeV1,
    FinalInformationVisibility,
    FinalSequenceItemV1,
    FinalSequenceV1,
    ProtectedSemanticAdjudicationV1,
    ProtectedSemanticRelationKind,
    ProtectedUserAllowanceMode,
    ProtectedUserAllowanceV1,
    RichPlannerSequenceV1,
    RichSequenceBeatV1,
    StoryRealizationKind,
    StoryRealizationSegmentV1,
    ValidatorSemanticStatus,
    ValidatorTaskMode,
)
from .provider import (
    ContinuousSceneWriterDraftV1,
    ContinuousSemanticValidatorDraftV4,
    ContinuousSemanticValidatorDraftV11,
    ProviderEventRecordDraftV1,
    ProviderFinalSequenceDraftV2,
    ProviderSceneSummaryDraftV1,
    continuous_deepseek_route,
    continuous_planner_route,
    continuous_validator_route,
)


SCRIPTED_JOB4_FIXTURE_ID = "cera.continuous_job4_scripted_fixture.v11_validator_identity"
_STORIES = {
    "turn-001": "Sakura requests bounded proof.",
    "turn-002": "Sakura keeps the threshold controlled.",
    "turn-003": "Mia answers cautiously in the later scene.",
}
SCRIPTED_JOB4_FIXTURE_SHA256 = bytes_sha256(Path(__file__).read_bytes())


@dataclass(frozen=True, slots=True)
class _ScriptedReceipt:
    requested_model: str
    external_provider_calls: int = 0


@dataclass(frozen=True, slots=True)
class _ScriptedTelemetry:
    provider_thread_id_sha256: str
    model: str
    reasoning_effort: str
    fast_mode_enabled: bool = False


class ScriptedCodexTransport:
    """Provider-shaped transport that only returns a frozen local DTO."""

    def __init__(self, route, thread_id: str, produce) -> None:
        self.route = route
        self.runner = SimpleNamespace(provider_thread_id=thread_id)
        self._thread_id = thread_id
        self._produce = produce

    def invoke(
        self,
        prompt: str,
        *,
        on_worker_started=None,
        on_worker_preflight=None,
        on_transport_invoke=None,
        **_kwargs,
    ):
        for callback in (
            on_worker_started,
            on_worker_preflight,
            on_transport_invoke,
        ):
            if callback is not None:
                callback()
        value = self._produce(prompt)
        return SimpleNamespace(
            parsed_json=to_primitive(value),
            receipt=_ScriptedReceipt(requested_model=self.route.model_name),
            operation_telemetry=_ScriptedTelemetry(
                provider_thread_id_sha256=text_sha256(self._thread_id),
                model=self.route.model_name,
                reasoning_effort=self.route.reasoning_effort,
            ),
            tool_call_count=0,
            failed_tool_call_count=0,
            tool_names=(),
            tool_server_names=(),
        )


class ScriptedDeepSeekTransport:
    """DeepSeek-shaped transport with no network or provider client."""

    def __init__(self, produce) -> None:
        self.route = continuous_deepseek_route()
        self._produce = produce

    def invoke(self, messages, *, on_transport_invoke=None, **_kwargs):
        if on_transport_invoke is not None:
            on_transport_invoke()
        value = self._produce(messages[-1].content)
        return SimpleNamespace(
            parsed_json=to_primitive(value),
            receipt=_ScriptedReceipt(requested_model=self.route.model_name),
            tool_names=(),
            tool_server_names=(),
        )


class ScriptedJob4FixtureRuntime:
    """Produces the exact ten deterministic stage results for one harness."""

    def __init__(self, *, world_id: str, branch_id: str) -> None:
        self.world_id = world_id
        self.branch_id = branch_id
        self.harness: Any | None = None

    def bind(self, harness: Any) -> None:
        if self.harness is not None:
            raise RuntimeError("scripted Job 4 fixture is already bound")
        self.harness = harness

    def planner_transport(self, _workspace, thread_id: str):
        return ScriptedCodexTransport(
            continuous_planner_route(effort="medium"),
            thread_id,
            self._planner_value,
        )

    def validator_transport(self, _workspace, thread_id: str):
        return ScriptedCodexTransport(
            continuous_validator_route(model="gpt-5.6-terra", effort="high"),
            thread_id,
            self._validator_value,
        )

    def composer_transport(self):
        return ScriptedDeepSeekTransport(self._composer_value)

    def _current(self):
        if self.harness is None:
            raise RuntimeError("scripted Job 4 fixture is not bound")
        return self.harness

    @staticmethod
    def _npc_for(turn_id: str) -> str:
        return (
            "character:mia_hanezawa"
            if turn_id == "turn-003"
            else "character:sakura_hanezawa"
        )

    @staticmethod
    def _scene_for(turn_id: str) -> str:
        return "scene-002" if turn_id == "turn-003" else "scene-001"

    def _roles(self, turn_id: str) -> CharacterRoleLedgerV1:
        return CharacterRoleLedgerV1(
            action_owner_ids=(self._npc_for(turn_id),),
            addressed_ids=("character:ted",),
        )

    def _planner_value(self, prompt: str) -> RichPlannerSequenceV1:
        harness = self._current()
        turn_id = harness._active_turn_id
        beat_key = f"beat_{turn_id.replace('-', '_')}"
        marker = "[CURRENT AUTHORITATIVE TURN PACKET]\n"
        packet = json.loads(prompt.rsplit(marker, 1)[1])
        npc = self._npc_for(turn_id)
        bindings = tuple(
            dict.fromkeys(
                str(value["binding_key"])
                for value in packet["request_local_evidence_bindings"]
                if value.get("visibility") != "character_private"
                or value.get("knowledge_owner_id") == npc
            )
        )
        return RichPlannerSequenceV1(
            schema_version=RichPlannerSequenceV1.SCHEMA_VERSION,
            sequence_id=f"sequence:{turn_id.replace('-', '_')}",
            world_id=self.world_id,
            branch_id=self.branch_id,
            accepted_turn_id=None,
            scene_id=self._scene_for(turn_id),
            selected_character_ids=(self._npc_for(turn_id),),
            omitted_character_ids=(),
            beats=(
                RichSequenceBeatV1(
                    beat_key=beat_key,
                    roles=self._roles(turn_id),
                    evidence_grounded_perception=(
                        "The trusted ingress and exact authorized records define the current exchange."
                    ),
                    immediate_goal="Answer without inventing Ted's next response.",
                    relevant_character_pressures=(
                        "Preserve the NPC's current stance and the accepted scene state.",
                    ),
                    competing_obligation_or_constraint=(
                        "Ted's unsupplied response remains protected."
                    ),
                    selected_tactic="Use one bounded NPC-owned response.",
                    causal_explanation=(
                        "The response advances the exchange while leaving the next user choice open."
                    ),
                    observable_action_or_dialogue_direction=_STORIES[turn_id],
                    private_state_guidance="Do not invent private history.",
                    physical_material_continuity="Preserve the current scene boundary.",
                    resulting_state="The exchange awaits Ted's next choice.",
                    deepseek_realization_space=(
                        "Choose exact wording and natural pacing within the bounded response.",
                    ),
                    protected_user_allowance=ProtectedUserAllowanceV1(
                        mode=ProtectedUserAllowanceMode.NONE,
                        source_binding_keys=(),
                        explanation="Do not create or restate Ted's response.",
                    ),
                    source_evidence_bindings=bindings,
                ),
            ),
            final_stop_state="The exchange awaits Ted's next choice.",
            unresolved_threads=("Ted's next choice remains open.",),
            provisional=True,
        )

    def _composer_value(self, _prompt: str) -> ContinuousSceneWriterDraftV1:
        turn_id = self._current()._active_turn_id
        story = _STORIES[turn_id]
        return ContinuousSceneWriterDraftV1(
            schema_version=ContinuousSceneWriterDraftV1.SCHEMA_VERSION,
            story_text=story,
        )

    @staticmethod
    def _good_assessment() -> CreatorReviewAssessment:
        return CreatorReviewAssessment(
            schema_version=CreatorReviewAssessment.SCHEMA_VERSION,
            severity=CreatorReviewSeverity.GOOD,
            publication_eligibility=PublicationEligibility.ACCEPT_ALLOWED,
            issue_owner=ReviewIssueOwner.NONE,
            reason_codes=(),
            creator_reason="The scripted candidate preserves all hard boundaries.",
            verifier_status="accepted",
        )

    def _validator_value(self, prompt: str) -> ContinuousSemanticValidatorDraftV11:
        harness = self._current()
        request = json.loads(prompt.rsplit("[VALIDATOR REQUEST]\n", 1)[1])
        package_id = request["package_id"]
        world_id = request["world_id"]
        branch_id = request["branch_id"]
        if harness._active_validator_label == "scene-1-validator-summary":
            accepted_ids = tuple(
                value.accepted_turn_id for value in harness.accepted_pairs
            )
            return ContinuousSemanticValidatorDraftV11.from_v4(ContinuousSemanticValidatorDraftV4(
                schema_version=ContinuousSemanticValidatorDraftV4.SCHEMA_VERSION,
                package_id=package_id,
                world_id=world_id,
                branch_id=branch_id,
                task_mode=ValidatorTaskMode.SCENE_SUMMARY,
                semantic_status=ValidatorSemanticStatus.ACCEPTED,
                reason_codes=(),
                story_segments=(),
                complete_final_sequence=None,
                creator_review=None,
                protected_semantic_adjudications=(),
                event_record=None,
                optional_scene_summary=ProviderSceneSummaryDraftV1(
                    summary_id="summary:scene_001",
                    completed_scene_id="scene-001",
                    accepted_turn_ids=accepted_ids,
                    shortest_complete_summary=(
                        "Sakura kept the arrival exchange bounded across the accepted turns."
                    ),
                    ending_state="The arrival scene ended with Ted's next choice open.",
                    transition_context="The next accepted prompt begins a later kitchen scene.",
                ),
            ))

        turn_id = harness._active_turn_id
        story = _STORIES[turn_id]
        beat_key = f"beat_{turn_id.replace('-', '_')}"
        item_key = f"item_{turn_id.replace('-', '_')}"
        roles = self._roles(turn_id)
        scopes = tuple(
            FinalFieldScopeV1(
                field_name=field_name,
                visibility=FinalInformationVisibility.PUBLIC,
                knowledge_owner_id=None,
                story_segment_keys=("segment_entire_story",),
                roles=roles,
                persistence_directives=(),
            )
            for field_name in ("realized_event", "resulting_state")
        ) + (
            FinalFieldScopeV1(
                field_name="knowledge_changes",
                visibility=FinalInformationVisibility.CHARACTER_PRIVATE,
                knowledge_owner_id=self._npc_for(turn_id),
                story_segment_keys=("segment_entire_story",),
                roles=roles,
                persistence_directives=(),
            ),
        )
        sequence = FinalSequenceV1(
            schema_version=FinalSequenceV1.SCHEMA_VERSION,
            sequence_id=f"final:{turn_id}",
            accepted_turn_id=turn_id,
            items=(
                FinalSequenceItemV1(
                    item_key=item_key,
                    planner_beat_keys=(beat_key,),
                    story_segment_keys=("segment_entire_story",),
                    realized_event=story,
                    valid_deepseek_additions=(),
                    omitted_or_contradicted_details=(),
                    private_state_owner_ids=(self._npc_for(turn_id),),
                    knowledge_changes=(
                        f"{self._npc_for(turn_id)} retains the accepted exchange context.",
                    ),
                    material_changes=(),
                    resulting_state="The exchange awaits Ted's next choice.",
                    roles=roles,
                    field_scopes=scopes,
                ),
            ),
            final_stop_state="The exchange awaits Ted's next choice.",
        )
        return ContinuousSemanticValidatorDraftV11.from_v4(ContinuousSemanticValidatorDraftV4(
            schema_version=ContinuousSemanticValidatorDraftV4.SCHEMA_VERSION,
            package_id=package_id,
            world_id=world_id,
            branch_id=branch_id,
            task_mode=ValidatorTaskMode.FINALIZE_TURN,
            semantic_status=ValidatorSemanticStatus.ACCEPTED,
            reason_codes=(),
            story_segments=(
                StoryRealizationSegmentV1(
                    schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
                    segment_key="segment_entire_story",
                    kind=StoryRealizationKind.ACTION,
                    output_start=0,
                    output_end=len(story),
                    exact_text=story,
                    roles=roles,
                    protected_user_source_claim_keys=(),
                ),
            ),
            complete_final_sequence=ProviderFinalSequenceDraftV2.from_final_sequence(
                sequence
            ),
            creator_review=self._good_assessment(),
            protected_semantic_adjudications=(
                ProtectedSemanticAdjudicationV1(
                    schema_version=ProtectedSemanticAdjudicationV1.SCHEMA_VERSION,
                    adjudication_key=f"adjudication_{turn_id.replace('-', '_')}",
                    segment_key="segment_entire_story",
                    output_start=0,
                    output_end=len(story),
                    exact_text_sha256=text_sha256(story),
                    protected_user_id="character:ted",
                    relation=ProtectedSemanticRelationKind.ADDRESSED_BY_NPC,
                    npc_assertion_owner_ids=(self._npc_for(turn_id),),
                    protected_user_source_claim_keys=(),
                ),
            ),
            event_record=ProviderEventRecordDraftV1(
                event_id=f"event:{turn_id}",
                accepted_turn_id=turn_id,
                scene_id=self._scene_for(turn_id),
                summary=story,
                final_sequence_item_keys=(item_key,),
                protected_user_source_claim_keys=(),
            ),
            optional_scene_summary=None,
        ))
