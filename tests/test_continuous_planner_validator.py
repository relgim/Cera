from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cera.continuous import (
    AcceptedFinalSequenceEnvelopeV1,
    CharacterRoleLedgerV1,
    CharacterSummaryEnvelopeV1,
    FinalFieldScopeV1,
    FinalInformationVisibility,
    FinalSequenceItemV1,
    FinalSequenceV1,
    ProtectedUserAllowanceMode,
    ProtectedUserAllowanceV1,
    RichPlannerSequenceV1,
    RichSequenceBeatV1,
    validator_route_for,
)
from cera.continuous.prompting import build_planner_turn_prompt, character_summary_share
from cera.continuous.packets import build_continuous_planner_turn_packet
from cera.continuous.evidence import EvidenceVisibility, RequestEvidenceBindingRegistry
from cera.continuous.provider import (
    ContinuousValidatorDraftV1,
    ProviderSceneSummaryDraftV1,
    continuous_validator_draft_json_schema,
    continuous_deepseek_draft_json_schema,
    rich_planner_sequence_json_schema,
)
from cera.continuous import AcceptedTurnPairV1, ValidatorSemanticStatus, ValidatorTaskMode
from cera.continuous.sessions import (
    ContinuousBranchForkReceiptV1,
    ContinuousSessionCompatibilityV1,
    ContinuousSessionCoordinator,
    ContinuousSessionRole,
    InMemoryContinuousStoredSessionPort,
    ContinuousSessionSnapshotStore,
    WorldPathAccessPolicyV1,
    assert_separate_role_sessions,
)
from cera.continuous.ingress import build_default_prepared_classifier_registry
from cera.continuous.record_policy import PERSISTENCE_POLICY_SHA256
from cera.continuous.codex_stored import CodexContinuousStoredSessionPort
from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, text_sha256
from cera.providers import ProviderSchemaDialect, project_provider_output_schema
from cera.registry import build_schema_registry


def beat(key: str = "verify_arrival", actor: str = "character:sakura_hanezawa") -> RichSequenceBeatV1:
    return RichSequenceBeatV1(
        beat_key=key,
        roles=CharacterRoleLedgerV1(
            action_owner_ids=(actor,),
            addressed_ids=(("character:ted",) if actor != "character:ted" else ()),
        ),
        evidence_grounded_perception="An unfamiliar adult voice identifies the expected household arrangement.",
        immediate_goal="Confirm the visitor without surrendering control of the threshold.",
        relevant_character_pressures=(
            "Protect the household from an unverified entrant.",
            "Handle the authorized arrival without unnecessary hostility.",
        ),
        competing_obligation_or_constraint="The household expects a tenant, but expectation is not identity proof.",
        selected_tactic="Ask for one arrangement-specific identifying fact while retaining the doorway boundary.",
        causal_explanation="A narrow verification question satisfies both household duty and Sakura's evidence-based caution.",
        observable_action_or_dialogue_direction="Sakura gives a formal acknowledgement and requests bounded verification.",
        private_state_guidance="Keep her vigilance owner-private; do not turn it into biography or certainty about Ted.",
        physical_material_continuity="Sakura remains at the closed or narrowly controlled threshold and does not invite entry.",
        resulting_state="Ted is recognized as a plausible arrival but must provide the requested verification.",
        deepseek_realization_space=(
            "Choose exact formal wording and pacing.",
            "Add character-natural gesture and threshold detail without inventing history.",
        ),
        protected_user_allowance=ProtectedUserAllowanceV1(
            mode=ProtectedUserAllowanceMode.NONE,
            source_binding_keys=(),
            explanation="Do not invent Ted's answer, thought, or movement.",
        ),
        source_evidence_bindings=("source_current_user", "evidence_sakura_guarded"),
    )


def sequence(*, provisional: bool = True, turn_id: str | None = None) -> RichPlannerSequenceV1:
    return RichPlannerSequenceV1(
        schema_version=RichPlannerSequenceV1.SCHEMA_VERSION,
        sequence_id="sequence:turn_001",
        world_id="world:hanezawa_test",
        branch_id="branch:main",
        accepted_turn_id=turn_id,
        scene_id="scene:arrival",
        selected_character_ids=("character:sakura_hanezawa",),
        omitted_character_ids=("character:mia_hanezawa",),
        beats=(beat(),),
        final_stop_state="Stop after a developed verification unit when Ted's unsupplied identifying answer is necessary.",
        unresolved_threads=("The visitor's identity remains unverified.",),
        provisional=provisional,
    )


def accepted_sequence(turn_id: str = "turn:001") -> FinalSequenceV1:
    return FinalSequenceV1(
        schema_version=FinalSequenceV1.SCHEMA_VERSION,
        sequence_id="sequence:accepted_001",
        accepted_turn_id=turn_id,
        items=(
            FinalSequenceItemV1(
                item_key="verify_arrival",
                planner_beat_keys=("verify_arrival",),
                story_segment_keys=("segment_entire_story",),
                realized_event="Sakura requested bounded identity verification while keeping control of the threshold.",
                valid_deepseek_additions=("A measured pause supported the established tactic.",),
                omitted_or_contradicted_details=(),
                private_state_owner_ids=("character:sakura_hanezawa",),
                knowledge_changes=("Sakura heard Ted claim the expected tenant identity.",),
                material_changes=(),
                resulting_state="Ted remains outside awaiting verification.",
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
        final_stop_state="Ted remains outside awaiting verification.",
    )


def compatibility(role: ContinuousSessionRole, branch: str = "branch:main") -> ContinuousSessionCompatibilityV1:
    return ContinuousSessionCompatibilityV1(
        schema_version=ContinuousSessionCompatibilityV1.SCHEMA_VERSION,
        world_id="world:hanezawa_test",
        branch_id=branch,
        role=role,
        provider="openai_codex",
        model="gpt-5.6-sol" if role is ContinuousSessionRole.PLANNER else "gpt-5.6-terra",
        reasoning_effort="medium" if role is ContinuousSessionRole.PLANNER else "high",
        prompt_version=f"cera.continuous_{role.value}_prompt.v1",
        output_schema_version=f"cera.continuous_{role.value}_output.v1",
        world_directory_identity_sha256=text_sha256(f"hanezawa/{branch}"),
        authority_policy_version="cera.owner_architecture.v2",
        privacy_policy_version="cera.privacy.v1",
        protected_user_policy_version="cera.continuous_protected_user_policy.v8",
        session_policy_version="cera.continuous_session_policy.v9_d200",
        ingress_classifier_registry_sha256=(
            build_default_prepared_classifier_registry().registry_sha256
        ),
        persistence_policy_sha256=PERSISTENCE_POLICY_SHA256,
    )


def branch_receipt(
    planner: ContinuousSessionCoordinator,
    child_branch: str,
) -> ContinuousBranchForkReceiptV1:
    snapshot = planner.snapshot()
    return planner.build_branch_fork_receipt(
        replace(
            snapshot.compatibility,
            branch_id=child_branch,
            world_directory_identity_sha256=text_sha256(
                f"{snapshot.compatibility.world_id}/{child_branch}"
            ),
        )
    )


def first_turn_prompt_packet(
    summary: CharacterSummaryEnvelopeV1 | None = None,
):
    registry = RequestEvidenceBindingRegistry(
        world_id="world:hanezawa_test",
        branch_id="branch:main",
        turn_id="turn:003",
    )
    source = registry.allocate_current_source(
        source_identity="current_user_source:turn:003",
        source_text="Continue.",
        protected_user_allowance_scope="exact supplied source",
        source_units=(),
    )
    mechanical = registry.allocate_mechanical_connective_allowance()
    summary_bindings = ()
    if summary is not None:
        binding = registry.allocate_world_record(
            relative_path=summary.source_path_or_record_id,
            source_sha256=summary.source_sha256,
            record_revision=summary.source_revision,
            record_type="characters",
            visibility=EvidenceVisibility.CHARACTER_PRIVATE,
            knowledge_owner_id=summary.character_id,
            exact_read_operation_sha256=text_sha256("summary read"),
        )
        summary_bindings = (
            {
                "character_id": summary.character_id,
                "binding_key": binding.binding_key,
                "source_path": binding.relative_path,
                "source_revision": binding.record_revision,
                "source_sha256": binding.source_sha256,
            },
        )
    return build_continuous_planner_turn_packet(
        world_id="world:hanezawa_test",
        branch_id="branch:main",
        session_id="session:one",
        request_id="request:three",
        scene_id="scene:arrival",
        turn_id="turn:003",
        context_mode="lean_continuous",
        current_user_message="Continue.",
        request_local_evidence_bindings=registry.prompt_manifest(),
        current_source_binding_key=source.binding_key,
        mechanical_connective_binding_key=mechanical.binding_key,
        protected_user_source_claims=(),
        ingress_source_units=(),
        ingress_custody={
            "receipt_id": "ingress_receipt:three",
            "receipt_sha256": text_sha256("receipt:three"),
            "raw_source_sha256": text_sha256("Continue."),
            "protected_user_id": "character:ted",
            "source_unit_keys": (),
        },
        character_summary_bindings=summary_bindings,
        compact_accepted_head_receipt=None,
        stable_accepted_reference_keys=(),
        projection_assisted_trigger=None,
        projection_reference_keys=(),
        projection_facts=(),
        scene_change_envelope_sha256=None,
    )


class RichPlannerContractTests(unittest.TestCase):
    def test_rich_sequence_is_valid_without_a_beat_quota(self) -> None:
        self.assertEqual(len(sequence().beats), 1)
        multi = replace(
            sequence(),
            selected_character_ids=("character:sakura_hanezawa", "character:tomi_hanezawa"),
            omitted_character_ids=(),
            beats=(
                beat(),
                beat("tomi_interrupts", "character:tomi_hanezawa"),
            ),
        )
        self.assertEqual(len(multi.beats), 2)

    def test_shallow_sequence_is_rejected_by_behavior_class(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "structurally shallow"):
            replace(
                beat(),
                evidence_grounded_perception="Sakura opens door.",
                immediate_goal="Sakura answers Ted.",
                selected_tactic="Sakura questions Ted.",
                causal_explanation="Sakura answers Ted.",
                resulting_state="Sakura questions Ted.",
            )

    def test_protected_user_requires_exact_allowance(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "protected user"):
            beat(actor="character:ted")

    def test_unselected_actor_and_private_knowledge_leak_are_rejected(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "unselected"):
            replace(sequence(), beats=(beat(actor="character:enne_hanezawa"),))

    def test_realization_space_is_explicitly_preserved(self) -> None:
        self.assertIn("Choose exact formal wording and pacing.", beat().deepseek_realization_space)

    def test_character_summary_is_incomplete_hash_bound_and_measured(self) -> None:
        payload = {
            "schema_version": CharacterSummaryEnvelopeV1.SCHEMA_VERSION,
            "character_id": "character:mia_hanezawa",
            "source_path_or_record_id": "ACTIVE/Characters/Mia.json",
            "source_revision": 4,
            "source_sha256": "1" * 64,
            "source_authority_classification": "active_authoritative_record_fields",
            "summary_field_path": "/reasoning_summary",
            "latest_changes_field_path": "/latest_accepted_changes",
            "summary": "Warm and observant; current branch relationship remains cautious acquaintance.",
            "latest_accepted_changes": ("Mia now recognizes Ted as the verified tenant.",),
        }
        summary = CharacterSummaryEnvelopeV1(
            **payload,
            derivation_receipt_sha256=canonical_sha256(payload),
        )
        _, usage = build_planner_turn_prompt(
            current_packet=first_turn_prompt_packet(summary),
            character_summaries=(summary,),
        )
        self.assertGreater(character_summary_share(usage), 0)
        self.assertEqual(summary.source_revision, 4)

    def test_provider_schemas_project_recursively_without_weakening_domain_validation(self) -> None:
        for schema in (
            rich_planner_sequence_json_schema(),
            continuous_validator_draft_json_schema(),
            continuous_deepseek_draft_json_schema(),
        ):
            projected = project_provider_output_schema(
                schema,
                ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
            )
            self.assertFalse(projected.provider_schema["additionalProperties"])
            self.assertEqual(
                set(projected.provider_schema["properties"]),
                set(projected.provider_schema["required"]),
            )
        validator_schema = canonical_sha256(
            continuous_validator_draft_json_schema()
        )
        self.assertEqual(len(validator_schema), 64)
        validator_text = str(continuous_validator_draft_json_schema())
        composer_text = str(continuous_deepseek_draft_json_schema())
        for field in (
            "story_segment_keys",
            "action_owner_ids",
            "state_owner_ids",
            "addressed_ids",
            "field_scopes",
            "protected_user_source_claim_keys",
        ):
            self.assertIn(field, validator_text)
        for field in (
            "story_segments",
            "action_owner_ids",
            "state_owner_ids",
            "speaker_ids",
            "protected_user_source_claim_keys",
        ):
            self.assertIn(field, composer_text)

    def test_scene_summary_exact_tail_is_python_derived_not_model_echoed(self) -> None:
        pair = AcceptedTurnPairV1(
            accepted_turn_id="turn:001",
            user_message="Hello.",
            complete_final_sequence=accepted_sequence(),
        )
        draft = ContinuousValidatorDraftV1(
            schema_version=ContinuousValidatorDraftV1.SCHEMA_VERSION,
            package_id="package:summary",
            world_id="world:hanezawa_test",
            branch_id="branch:main",
            task_mode=ValidatorTaskMode.SCENE_SUMMARY,
            semantic_status=ValidatorSemanticStatus.ACCEPTED,
            complete_final_sequence=None,
            creator_review=None,
            protected_semantic_adjudications=(),
            event_record=None,
            optional_scene_summary=ProviderSceneSummaryDraftV1(
                summary_id="summary:arrival",
                completed_scene_id="scene:arrival",
                accepted_turn_ids=("turn:001",),
                shortest_complete_summary="Sakura retained the threshold and requested proof.",
                ending_state="Ted remained outside awaiting verification.",
                transition_context="The next scene occurs later in the kitchen.",
            ),
        )
        compiled = draft.compile(accepted_pairs=(pair,))
        self.assertEqual(compiled.optional_scene_summary.last_five_exact_pairs, (pair,))

    def test_shadow_schemas_are_registered_without_replacing_active_versions(self) -> None:
        versions = build_schema_registry().versions
        self.assertIn(RichPlannerSequenceV1.SCHEMA_VERSION, versions)
        self.assertIn(CharacterSummaryEnvelopeV1.SCHEMA_VERSION, versions)


class ContinuousSessionTests(unittest.TestCase):
    def test_continuous_adapter_reuses_existing_stored_backend_lifecycle(self) -> None:
        class Backend:
            session_epoch_id = "epoch-1"

            def __init__(self):
                self.counter = 0
                self.valid = set()

            def start_stored_thread(self):
                self.counter += 1
                value = f"stored-{self.counter}"
                self.valid.add(value)
                return value

            def fork_stored_thread(self, parent_thread_id):
                self.assert_valid(parent_thread_id)
                return self.start_stored_thread()

            def resume_stored_thread(self, thread_id):
                return thread_id in self.valid

            def archive_stored_thread(self, thread_id):
                self.valid.discard(thread_id)

            def append_model_visible_context(self, thread_id, text):
                self.assert_valid(thread_id)

            archive_stored_leaf = archive_stored_thread

            def assert_valid(self, thread_id):
                if thread_id not in self.valid:
                    raise RuntimeError("missing")

        port = CodexContinuousStoredSessionPort(Backend())
        planner = ContinuousSessionCoordinator(
            compatibility(ContinuousSessionRole.PLANNER), port
        )
        handle = planner.ensure_session()
        self.assertTrue(port.resume(handle))
        envelope = AcceptedFinalSequenceEnvelopeV1(
            schema_version=AcceptedFinalSequenceEnvelopeV1.SCHEMA_VERSION,
            accepted_turn_id="turn:001",
            user_message="Hello.",
            complete_final_sequence=accepted_sequence(),
            acceptance_receipt_sha256=text_sha256("accepted-turn-001"),
        )
        planner.append_accepted_final_sequence(envelope)
        planner.synchronize_accepted_final_sequence(envelope)
        child = planner.fork_for_branch(
            compatibility(ContinuousSessionRole.PLANNER, "branch:child"),
            branch_receipt=branch_receipt(planner, "branch:child"),
        )
        self.assertNotEqual(
            handle.provider_thread_id,
            child.ensure_session().provider_thread_id,
        )
        self.assertEqual(port.provider_calls, 0)

    def test_planner_and_validator_are_separate_continuous_threads(self) -> None:
        port = InMemoryContinuousStoredSessionPort()
        planner = ContinuousSessionCoordinator(compatibility(ContinuousSessionRole.PLANNER), port)
        validator = ContinuousSessionCoordinator(compatibility(ContinuousSessionRole.VALIDATOR), port)
        assert_separate_role_sessions(planner, validator)
        planner_id = planner.ensure_session().provider_thread_id
        planner.record_planner_provisional("turn:001", sequence().sequence_sha256)
        self.assertEqual(planner.ensure_session().provider_thread_id, planner_id)
        self.assertEqual(port.provider_calls, 0)

    def test_accepted_final_sequence_appends_exactly_once_and_supersedes_provisional(self) -> None:
        port = InMemoryContinuousStoredSessionPort()
        planner = ContinuousSessionCoordinator(compatibility(ContinuousSessionRole.PLANNER), port)
        provisional = sequence()
        planner.record_planner_provisional("turn:001", provisional.sequence_sha256)
        envelope = AcceptedFinalSequenceEnvelopeV1(
            schema_version=AcceptedFinalSequenceEnvelopeV1.SCHEMA_VERSION,
            accepted_turn_id="turn:001",
            user_message="Hello, my name is Ted. Is this the Hanezawa residence?",
            complete_final_sequence=accepted_sequence(),
            acceptance_receipt_sha256=text_sha256("accepted-turn-001"),
        )
        self.assertTrue(planner.append_accepted_final_sequence(envelope))
        self.assertFalse(planner.append_accepted_final_sequence(envelope))
        event = planner.snapshot().context_events[-1]
        self.assertEqual(event.supersedes_payload_sha256, provisional.sequence_sha256)
        self.assertEqual(planner.snapshot().accepted_turn_ids, ("turn:001",))

    def test_accepted_final_envelope_is_injected_immediately_only_once(self) -> None:
        port = InMemoryContinuousStoredSessionPort()
        planner = ContinuousSessionCoordinator(
            compatibility(ContinuousSessionRole.PLANNER), port
        )
        envelope = AcceptedFinalSequenceEnvelopeV1(
            schema_version=AcceptedFinalSequenceEnvelopeV1.SCHEMA_VERSION,
            accepted_turn_id="turn:001",
            user_message="Hello, my name is Ted.",
            complete_final_sequence=accepted_sequence(),
            acceptance_receipt_sha256=text_sha256("accepted-turn-001"),
        )
        planner.append_accepted_final_sequence(envelope)
        self.assertEqual(planner.unsynchronized_accepted_turn_ids, ("turn:001",))
        self.assertTrue(planner.synchronize_accepted_final_sequence(envelope))
        self.assertFalse(planner.synchronize_accepted_final_sequence(envelope))
        self.assertEqual(planner.unsynchronized_accepted_turn_ids, ())
        self.assertEqual(
            port.model_visible_context[planner.ensure_session().provider_thread_id],
            [envelope.render_for_planner()],
        )
        self.assertEqual(
            sum(
                value.event_type == "accepted_final_sequence_synchronized"
                for value in planner.snapshot().context_events
            ),
            1,
        )

    def test_restart_resumes_same_compatible_physical_thread(self) -> None:
        port = InMemoryContinuousStoredSessionPort()
        planner = ContinuousSessionCoordinator(compatibility(ContinuousSessionRole.PLANNER), port)
        snapshot = planner.snapshot()
        restored = ContinuousSessionCoordinator.resume_compatible(
            snapshot,
            port,
            expected_compatibility=compatibility(ContinuousSessionRole.PLANNER),
        )
        self.assertEqual(
            restored.ensure_session().provider_thread_id,
            planner.ensure_session().provider_thread_id,
        )

    def test_restart_rejects_pre_v5_policy_compatibility(self) -> None:
        port = InMemoryContinuousStoredSessionPort()
        current = compatibility(ContinuousSessionRole.PLANNER)
        old = replace(
            current,
            protected_user_policy_version="cera.protected_user.v1",
            session_policy_version="cera.continuous_session.v1",
        )
        snapshot = ContinuousSessionCoordinator(old, port).snapshot()
        with self.assertRaisesRegex(StateConflictError, "incompatible"):
            ContinuousSessionCoordinator.resume_compatible(
                snapshot,
                port,
                expected_compatibility=current,
            )

    def test_restart_rejects_pre_v7_policy_compatibility(self) -> None:
        port = InMemoryContinuousStoredSessionPort()
        current = compatibility(ContinuousSessionRole.PLANNER)
        pre_v7 = replace(
            current,
            protected_user_policy_version="cera.continuous_protected_user_policy.v6",
            session_policy_version="cera.continuous_session_policy.v6",
        )
        snapshot = ContinuousSessionCoordinator(pre_v7, port).snapshot()
        with self.assertRaisesRegex(StateConflictError, "incompatible"):
            ContinuousSessionCoordinator.resume_compatible(
                snapshot,
                port,
                expected_compatibility=current,
            )

    def test_restart_rejects_pre_v8_classifier_and_write_policy_compatibility(self) -> None:
        port = InMemoryContinuousStoredSessionPort()
        current = compatibility(ContinuousSessionRole.PLANNER)
        stale = replace(
            current,
            ingress_classifier_registry_sha256=text_sha256("pre-v8-classifier"),
            persistence_policy_sha256=text_sha256("pre-v8-write-policy"),
        )
        snapshot = ContinuousSessionCoordinator(stale, port).snapshot()
        with self.assertRaisesRegex(StateConflictError, "incompatible"):
            ContinuousSessionCoordinator.resume_compatible(
                snapshot,
                port,
                expected_compatibility=current,
            )

    def test_restart_loads_hash_bound_snapshot_from_role_directory(self) -> None:
        port = InMemoryContinuousStoredSessionPort()
        planner = ContinuousSessionCoordinator(
            compatibility(ContinuousSessionRole.PLANNER), port
        )
        planner.record_planner_provisional("turn:001", sequence().sequence_sha256)
        with TemporaryDirectory() as temporary:
            store = ContinuousSessionSnapshotStore(Path(temporary))
            saved = planner.checkpoint(store)
            self.assertIn("PLANNER_SESSION", saved.parts)
            restored = ContinuousSessionCoordinator.resume_compatible(
                store.load(ContinuousSessionRole.PLANNER),
                port,
                expected_compatibility=compatibility(
                    ContinuousSessionRole.PLANNER
                ),
            )
        self.assertEqual(restored.snapshot(), planner.snapshot())

    def test_sibling_branch_forks_a_distinct_thread(self) -> None:
        port = InMemoryContinuousStoredSessionPort()
        planner = ContinuousSessionCoordinator(compatibility(ContinuousSessionRole.PLANNER), port)
        envelope = AcceptedFinalSequenceEnvelopeV1(
            schema_version=AcceptedFinalSequenceEnvelopeV1.SCHEMA_VERSION,
            accepted_turn_id="turn:001",
            user_message="Hello.",
            complete_final_sequence=accepted_sequence(),
            acceptance_receipt_sha256=text_sha256("accepted-turn-001"),
        )
        planner.append_accepted_final_sequence(envelope)
        planner.synchronize_accepted_final_sequence(envelope)
        child = planner.fork_for_branch(
            compatibility(ContinuousSessionRole.PLANNER, "branch:sibling"),
            branch_receipt=branch_receipt(planner, "branch:sibling"),
        )
        self.assertNotEqual(child.ensure_session().provider_thread_id, planner.ensure_session().provider_thread_id)
        self.assertEqual(child.snapshot().accepted_turn_ids, ("turn:001",))

    def test_role_path_policy_denies_cross_session_and_sibling_access(self) -> None:
        policy = WorldPathAccessPolicyV1("D:/runtime/world/main")
        self.assertTrue(
            policy.authorize(
                ContinuousSessionRole.PLANNER,
                "D:/runtime/world/main/ACTIVE/Characters/Sakura.json",
            ).endswith("Sakura.json")
        )
        for path in (
            "D:/runtime/world/main/VALIDATOR_SESSION/context.json",
            "D:/runtime/world/main/CANDIDATES/turn-1/output.json",
            "D:/runtime/world/sibling/ACTIVE/Characters/Sakura.json",
        ):
            with self.assertRaises(PermissionError):
                policy.authorize(ContinuousSessionRole.PLANNER, path)

    def test_validator_route_mapping_is_closed(self) -> None:
        self.assertEqual(validator_route_for("gpt-5.6-sol", "xhigh").validator_model, "gpt-5.6-sol")
        self.assertEqual(validator_route_for("gpt-5.6-sol", "medium").validator_model, "gpt-5.6-terra")
        with self.assertRaisesRegex(ContractValidationError, "unsupported"):
            validator_route_for("gpt-5.6-sol", "high")


if __name__ == "__main__":
    unittest.main()
