from __future__ import annotations

from dataclasses import replace
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from cera.continuous import (
    AcceptedTurnPairV1,
    CharacterSummaryEnvelopeV1,
    CreatedFieldLogEntryV1,
    EventRecordCandidateV1,
    FinalSequenceItemV1,
    FinalSequenceV1,
    SceneSummaryV1,
    ValidatorFinalizationPackageV1,
    ValidatorSemanticStatus,
    ValidatorTaskMode,
    WorldEditOperationKind,
    WorldEditOperationV1,
    ProtectedUserAllowanceMode,
    ProtectedUserAllowanceV1,
    RichPlannerSequenceV1,
    RichSequenceBeatV1,
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


def final_sequence(turn_id: str = "turn-001") -> FinalSequenceV1:
    return FinalSequenceV1(
        schema_version=FinalSequenceV1.SCHEMA_VERSION,
        sequence_id=f"sequence:{turn_id}",
        accepted_turn_id=turn_id,
        items=(
            FinalSequenceItemV1(
                item_key="verify_arrival",
                planner_beat_keys=("verify_arrival",),
                realized_event="Sakura keeps control of the threshold and requests identifying proof.",
                valid_deepseek_additions=("A brief measured pause supports her established caution.",),
                omitted_or_contradicted_details=(),
                private_state_owner_ids=("character:sakura_hanezawa",),
                knowledge_changes=("Sakura now knows the visitor claims to be the expected tenant.",),
                material_changes=(),
                resulting_state="The visitor remains outside awaiting verification.",
            ),
        ),
        final_stop_state="Ted must supply the requested identifying detail.",
    )


def accepted_pair(turn_id: str = "turn-001") -> AcceptedTurnPairV1:
    return AcceptedTurnPairV1(
        accepted_turn_id=turn_id,
        user_message="Hello.",
        complete_final_sequence=final_sequence(turn_id),
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


def package(*, turn_id: str = "turn-001", revision: int = 1) -> ValidatorFinalizationPackageV1:
    return ValidatorFinalizationPackageV1(
        schema_version=ValidatorFinalizationPackageV1.SCHEMA_VERSION,
        package_id=f"package:{turn_id}",
        world_id="world-test",
        branch_id="main",
        task_mode=ValidatorTaskMode.FINALIZE_TURN,
        semantic_status=ValidatorSemanticStatus.ACCEPTED,
        complete_final_sequence=final_sequence(turn_id),
        creator_review=good_assessment(),
        world_edit_operations=(
            WorldEditOperationV1(
                operation_key="record_tenant_claim",
                target_file="Characters/Sakura.json",
                expected_file_revision=revision,
                operation=WorldEditOperationKind.ADD,
                field_path=f"/turn_claims/{turn_id}",
                value="Ted claimed to be the expected tenant.",
                reason="The accepted visible exchange changed Sakura's direct knowledge.",
                source_final_sequence_item="verify_arrival",
            ),
        ),
        created_field_log=(
            CreatedFieldLogEntryV1(
                target_file="Characters/Sakura.json",
                field_path=f"/turn_claims/{turn_id}",
                value_type="string",
                value="Ted claimed to be the expected tenant.",
                reason="The accepted visible exchange changed Sakura's direct knowledge.",
                source_final_sequence_item="verify_arrival",
            ),
        ),
        event_record=EventRecordCandidateV1(
            event_id=f"event:{turn_id}",
            accepted_turn_id=turn_id,
            scene_id="scene-001",
            participant_ids=("character:sakura_hanezawa", "character:ted"),
            summary="Sakura requested proof after Ted identified himself as the expected tenant.",
            final_sequence_item_keys=("verify_arrival",),
        ),
        optional_scene_summary=None,
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
                actor_ids=("character:sakura_hanezawa",),
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
        protected_user_policy_version="test-protected-v1",
        session_policy_version="test-session-v1",
    )


class _FakeStage:
    def __init__(self, value, method: str) -> None:
        self.value = value
        self.method = method

    def plan(self, prompt):
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


class _FakeQueueStage:
    def __init__(self, *values) -> None:
        self.values = list(values)

    def validate(self, _prompt, **_kwargs):
        return SimpleNamespace(
            value=self.values.pop(0),
            provider_receipt=None,
            operation_telemetry=None,
            tool_call_count=0,
            failed_tool_call_count=0,
        )


class _FailStage:
    def plan(self, _prompt):
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
        view = self.store.create_candidate("world-test", "main", "turn-001")
        self.store.record_candidate_package("world-test", "main", view, package())
        self.assertEqual(before, self.store.tree_sha256(self.root / "ACTIVE"))
        receipt = self.store.apply_creator_action(
            world_id="world-test",
            branch_id="main",
            turn_id="turn-001",
            action=CreatorReviewAction.ACCEPT,
            package=package(),
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
        self.store.create_candidate("world-test", "main", "turn-001")
        receipt = self.store.apply_creator_action(
            world_id="world-test",
            branch_id="main",
            turn_id="turn-001",
            action=CreatorReviewAction.FALSE_POSITIVE,
            package=candidate,
            accepted_pair=accepted_pair(),
        )
        self.assertTrue(receipt.accepted)
        diagnostic = tuple((self.root / "VALIDATOR_DIAGNOSTICS").glob("*.json"))
        self.assertEqual(len(diagnostic), 1)
        self.assertFalse(json.loads(diagnostic[0].read_text())["creates_story_constraint"])

    def test_false_positive_rejects_good_assessment(self) -> None:
        self.store.create_candidate("world-test", "main", "turn-001")
        with self.assertRaisesRegex(StateConflictError, "does not permit"):
            self.store.apply_creator_action(
                world_id="world-test",
                branch_id="main",
                turn_id="turn-001",
                action=CreatorReviewAction.FALSE_POSITIVE,
                package=package(),
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
            self.store.create_candidate("world-test", "main", turn)
            before = self.store.tree_sha256(self.root / "ACTIVE")
            receipt = self.store.apply_creator_action(
                world_id="world-test",
                branch_id="main",
                turn_id=turn,
                action=action,
                package=candidate,
            )
            self.assertFalse(receipt.accepted)
            self.assertEqual(before, self.store.tree_sha256(self.root / "ACTIVE"))

    def test_revision_conflict_rejects_whole_package(self) -> None:
        self.store.create_candidate("world-test", "main", "turn-001")
        before = self.store.tree_sha256(self.root / "ACTIVE")
        with self.assertRaisesRegex(StateConflictError, "revision precondition"):
            self.store.apply_creator_action(
                world_id="world-test",
                branch_id="main",
                turn_id="turn-001",
                action=CreatorReviewAction.ACCEPT,
                package=package(revision=9),
                accepted_pair=accepted_pair(),
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
            value="Sakura retained control of the threshold.",
            reason="The accepted sequence changed the current relationship evidence.",
            source_final_sequence_item="verify_arrival",
        )
        candidate = replace(
            package(),
            world_edit_operations=(first, second),
            created_field_log=(
                package().created_field_log[0],
                CreatedFieldLogEntryV1(
                    target_file=second.target_file,
                    field_path=second.field_path,
                    value_type="string",
                    value=second.value,
                    reason=second.reason,
                    source_final_sequence_item=second.source_final_sequence_item,
                ),
            ),
        )
        self.store.create_candidate("world-test", "main", "turn-001")
        receipt = self.store.apply_creator_action(
            world_id="world-test",
            branch_id="main",
            turn_id="turn-001",
            action=CreatorReviewAction.ACCEPT,
            package=candidate,
            accepted_pair=accepted_pair(),
        )
        self.assertEqual(
            receipt.changed_files,
            ("Characters/Sakura.json", "Relationships/Sakura_Ted.json"),
        )
        self.assertEqual(
            json.loads(relationship.read_text(encoding="utf-8"))["_cera_revision"],
            2,
        )

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
        candidate = replace(
            package(),
            world_edit_operations=(create,),
            created_field_log=(
                CreatedFieldLogEntryV1(
                    target_file=create.target_file,
                    field_path="/",
                    value_type="object",
                    value=create.value,
                    reason=create.reason,
                    source_final_sequence_item=create.source_final_sequence_item,
                ),
            ),
        )
        self.store.create_candidate("world-test", "main", "turn-001")
        self.store.apply_creator_action(
            world_id="world-test",
            branch_id="main",
            turn_id="turn-001",
            action=CreatorReviewAction.ACCEPT,
            package=candidate,
            accepted_pair=accepted_pair(),
        )
        created = json.loads((self.root / "ACTIVE" / "Rules" / "arrival_rule.json").read_text())
        self.assertEqual(created["_cera_revision"], 1)
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
                SimpleNamespace(story_text="Sakura keeps the threshold and requests proof."),
                "compose",
            ),
            validator=_FakeStage(package(), "validate"),
        )
        candidate = coordinator.prepare(
            ContinuousTurnRequestV1(
                world_id="world-test",
                branch_id="main",
                scene_id="scene-001",
                turn_id="turn-001",
                user_message="Hello, my name is Ted. Is this the Hanezawa residence?",
                current_authority_packet={"protected_user_id": "character:ted"},
                character_summaries=(character_summary(source_sha256=text_sha256(
                    (self.root / "ACTIVE" / "Characters" / "Sakura.json").read_text(encoding="utf-8")
                )),),
            )
        )
        self.assertEqual(candidate.provider_calls, 0)
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

    def test_provider_failure_leaves_complete_secret_free_debug_skeleton(self) -> None:
        port = InMemoryContinuousStoredSessionPort()
        coordinator = ContinuousShadowTurnCoordinator(
            world=self.store,
            planner_session=ContinuousSessionCoordinator(
                session_compatibility(ContinuousSessionRole.PLANNER), port
            ),
            validator_session=ContinuousSessionCoordinator(
                session_compatibility(ContinuousSessionRole.VALIDATOR), port
            ),
            planner=_FailStage(),
            composer=_FakeStage(SimpleNamespace(story_text="unused"), "compose"),
            validator=_FakeStage(package(), "validate"),
        )
        with self.assertRaises(RuntimeError):
            coordinator.prepare(
                ContinuousTurnRequestV1(
                    world_id="world-test",
                    branch_id="main",
                    scene_id="scene-001",
                    turn_id="turn-001",
                    user_message="Hello.",
                    current_authority_packet={"protected_user_id": "character:ted"},
                )
            )
        debug = ContinuousDebugRecorder(self.root, "scene-001", "turn-001")
        self.assertEqual(debug.validate_complete(), ())
        error_text = (debug.root / "errors.json").read_text(encoding="utf-8")
        self.assertIn('"stage":"planner"', error_text)
        self.assertNotIn("private-test-token", error_text)

    def test_scene_change_uses_same_sessions_and_calls_validator_summary_before_planner(self) -> None:
        port = InMemoryContinuousStoredSessionPort()
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
        new_prompt = "Several days later, Ted asks Mia about Sakura."
        coordinator = ContinuousShadowTurnCoordinator(
            world=self.store,
            planner_session=planner_session,
            validator_session=validator_session,
            planner=_FakeStage(
                replace(rich_sequence(), scene_id="scene-002"), "plan"
            ),
            composer=_FakeStage(
                SimpleNamespace(story_text="Mia answers cautiously in the kitchen."),
                "compose",
            ),
            validator=_FakeQueueStage(
                scene_summary_package(pair, new_prompt=new_prompt),
                replace(
                    package(turn_id="turn-002"),
                    event_record=replace(
                        package(turn_id="turn-002").event_record,
                        scene_id="scene-002",
                    ),
                ),
            ),
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
                current_authority_packet={"protected_user_id": "character:ted"},
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
