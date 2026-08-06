from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.continuous.world import ContinuousWorldStore
from cera.errors import ContractValidationError, StateConflictError
from cera.providers import ProviderSchemaDialect, project_provider_output_schema
from cera.schema import from_mapping
from cera.serialization import canonical_sha256, text_sha256, to_primitive
from cera.sequence_first import (
    ActiveWorldAuthorityAssembler,
    ApprovedTargetV1,
    ConflictClass,
    ControllerFailureType,
    DurableChangeKind,
    DurableChangeV1,
    EvidenceRecordV1,
    FreshCandidateValidatorFactory,
    ItemKind,
    PersistenceTargetCustodyV1,
    PersistentPlannerSession,
    PresenceChangeV1,
    PresenceDirection,
    ProtectedSourceClaimV1,
    ReaderIssueV1,
    ReaderStatus,
    ReaderVerdictV1,
    SequenceCustodyEnvelopeV1,
    SequenceDraftV1,
    SequenceFirstCoordinator,
    SequenceFirstAcceptedAuthorityV1,
    SequenceFirstTurnRequestV1,
    SequenceFirstTurnSemanticInputV1,
    SequenceItemV1,
    TargetOperationKind,
    ValidationConflictV1,
    ValidatorDecisionV1,
    ValidatorVerdict,
    Visibility,
    VoiceCueV1,
    WriterAttemptStatus,
    WriterResponseV1,
    apply_presence_changes,
    writer_retry_eligible,
)
from cera.sequence_first.prompting import (
    PLANNER_BASE_INSTRUCTIONS,
    PLANNER_PROFILE,
    VALIDATOR_BASE_INSTRUCTIONS,
    VALIDATOR_PROFILE,
    planner_turn_prompt,
)
from cera.sequence_first.provider import (
    SEQUENCE_FIRST_PLANNER_ADAPTER,
    SEQUENCE_FIRST_READER_ADAPTER,
    SEQUENCE_FIRST_VALIDATOR_ADAPTER,
    SequenceFirstPlannerCodexBackend,
    SequenceFirstReaderCodexPort,
    SequenceFirstValidatorCodexBackend,
    reader_verdict_json_schema,
    sequence_draft_json_schema,
    sequence_first_planner_route,
    sequence_first_reader_route,
    sequence_first_validator_route,
    validator_decision_json_schema,
)
from cera.sequence_first.contracts import LOCAL_KEY_JSON_PATTERN
from cera.sequence_first.world import SequenceFirstWorldTransaction
from cera.sillytavern.sequence_first_adapter import (
    AcceptedSceneStateV1,
    FrozenSillyTavernIngressV1,
    SequenceFirstSillyTavernAdapter,
)


WRITER_SCHEMA = "cera.scene_writer_draft.v1"


def item(
    *,
    key: str = "hana_answers",
    owner: str | None = "character:hana",
    kind: ItemKind = ItemKind.DIALOGUE_INTENT,
    meaning: str = "Hana answers warmly and returns the floor to Ted.",
    evidence: tuple[str, ...] = ("evidence:hana_voice",),
    planner_keys: tuple[str, ...] = (),
    change_keys: tuple[str, ...] = (),
    parent: str | None = None,
) -> SequenceItemV1:
    return SequenceItemV1(
        item_key=key,
        kind=kind,
        owner_id=owner,
        concise_meaning=meaning,
        causal_parent_item_key=parent,
        evidence_keys=evidence,
        durable_change_keys=change_keys,
        planner_item_keys=planner_keys,
    )


def intended(
    *,
    items: tuple[SequenceItemV1, ...] | None = None,
    durable_changes: tuple[DurableChangeV1, ...] = (),
    presence_changes: tuple[PresenceChangeV1, ...] = (),
) -> SequenceDraftV1:
    return SequenceDraftV1(
        items=items or (item(),),
        durable_changes=durable_changes,
        presence_changes=presence_changes,
        resulting_public_state="Hana, Mia, and Ted remain in the entry room.",
        unresolved_threads=("Ted has not chosen what to do next.",),
        stopping_boundary="Stop after an NPC returns the floor to Ted.",
    )


def realized(
    *,
    items: tuple[SequenceItemV1, ...] | None = None,
    durable_changes: tuple[DurableChangeV1, ...] = (),
    presence_changes: tuple[PresenceChangeV1, ...] = (),
) -> SequenceDraftV1:
    return SequenceDraftV1(
        items=items
        or (
            item(
                key="hana_answer_realized",
                planner_keys=("hana_answers",),
            ),
        ),
        durable_changes=durable_changes,
        presence_changes=presence_changes,
        resulting_public_state="Hana, Mia, and Ted remain in the entry room.",
        unresolved_threads=("Ted has not chosen what to do next.",),
        stopping_boundary="Stop after an NPC returns the floor to Ted.",
    )


def semantic_input(
    *,
    source: str = "Continue the scene",
    present: tuple[str, ...] = (
        "character:ted",
        "character:hana",
        "character:mia",
    ),
    remote: tuple[str, ...] = (),
    approved_targets: tuple[ApprovedTargetV1, ...] = (),
    scene_reinitialization: bool = False,
) -> SequenceFirstTurnSemanticInputV1:
    claims = (
        ProtectedSourceClaimV1(
            claim_key="current_request",
            exact_text=source,
        ),
    )
    return SequenceFirstTurnSemanticInputV1(
        exact_current_source=source,
        current_source_key="source:current",
        protected_source_claims=claims,
        known_character_ids=(
            "character:ted",
            "character:hana",
            "character:mia",
            "character:sakura",
        ),
        accepted_present_character_ids=present,
        explicitly_authorized_remote_character_ids=remote,
        current_public_scene_state="Ted, Hana, and Mia are in the entry room.",
        prior_realized_sequence=None,
        character_deltas=(),
        evidence_records=(
            EvidenceRecordV1(
                evidence_key="evidence:hana_voice",
                subject_id="character:hana",
                visibility=Visibility.PUBLIC,
                exact_content="Hana is warm, patient, and direct.",
            ),
        ),
        approved_targets=approved_targets,
        unresolved_threads=("Ted has not chosen what to do next.",),
        hard_boundaries=("Do not invent Ted's response.",),
        scene_reinitialization=scene_reinitialization,
    )


def custody(
    *,
    candidate_id: str = "candidate-one",
    turn_id: str = "turn-002",
    parent: str | None = None,
    source: str = "Continue the scene",
    accepted_head: str | None = None,
    targets: tuple[PersistenceTargetCustodyV1, ...] = (),
) -> SequenceCustodyEnvelopeV1:
    return SequenceCustodyEnvelopeV1(
        request_id=f"request-{candidate_id}",
        candidate_id=candidate_id,
        world_id="world-test",
        branch_id="branch-main",
        scene_id="scene-entry",
        turn_id=turn_id,
        parent_accepted_turn_id=parent,
        exact_source_sha256=text_sha256(source),
        accepted_head_sha256=accepted_head,
        transaction_id=f"transaction-{candidate_id}",
        persistence_targets=targets,
    )


def request(
    *,
    semantics: SequenceFirstTurnSemanticInputV1 | None = None,
    private_custody: SequenceCustodyEnvelopeV1 | None = None,
) -> SequenceFirstTurnRequestV1:
    semantics = semantics or semantic_input()
    private_custody = private_custody or custody(source=semantics.exact_current_source)
    return SequenceFirstTurnRequestV1(semantics, private_custody)


def accepted_decision(value: SequenceDraftV1 | None = None) -> ValidatorDecisionV1:
    return ValidatorDecisionV1(
        verdict=ValidatorVerdict.ACCEPT,
        realized_sequence=value or realized(),
    )


def rejected_decision(
    *,
    conflict_class: ConflictClass = ConflictClass.PROTECTED_USER_INVENTION,
    quote: str = "Ted nodded.",
) -> ValidatorDecisionV1:
    return ValidatorDecisionV1(
        verdict=ValidatorVerdict.REJECT,
        realized_sequence=None,
        conflict=ValidationConflictV1(
            conflict_class=conflict_class,
            concise_explanation="The prose contains a material conflict.",
            exact_quote=quote,
        ),
    )


def accepted_reader() -> ReaderVerdictV1:
    return ReaderVerdictV1(status=ReaderStatus.ACCEPTED)


def rejected_reader() -> ReaderVerdictV1:
    return ReaderVerdictV1(
        status=ReaderStatus.REJECTED,
        issues=(
            ReaderIssueV1(
                issue_code="severe_repetition",
                concise_explanation="The prose repeats one line throughout.",
                exact_quote="Again. Again. Again.",
            ),
        ),
    )


def inconclusive_reader() -> ReaderVerdictV1:
    return ReaderVerdictV1(
        status=ReaderStatus.INCONCLUSIVE,
        issues=(
            ReaderIssueV1(
                issue_code="review_package_ambiguous",
                concise_explanation="The review package is not sufficient to judge prose quality.",
            ),
        ),
    )


class PlannerFake:
    def __init__(self, output: SequenceDraftV1) -> None:
        self.output = output
        self.calls = 0
        self.inputs = []

    def plan(self, semantic_request) -> SequenceDraftV1:
        self.calls += 1
        self.inputs.append(semantic_request)
        return self.output


class WriterFake:
    def __init__(self, outputs: list[str | Exception]) -> None:
        self.outputs = outputs
        self.calls = 0
        self.briefs = []

    def write(self, brief, attempt_number: int) -> WriterResponseV1:
        self.calls += 1
        self.briefs.append(brief)
        output = self.outputs[self.calls - 1]
        if isinstance(output, Exception):
            raise output
        return WriterResponseV1(WRITER_SCHEMA, output)


class ValidatorSessionFake:
    def __init__(self, decision: ValidatorDecisionV1) -> None:
        self.decision = decision
        self.calls = 0
        self.archived = False

    def validate(self, validator_request) -> ValidatorDecisionV1:
        self.calls += 1
        return self.decision

    def archive_and_prove_nonresumable(self) -> None:
        self.archived = True


class ValidatorFactoryFake:
    def __init__(self, decisions: list[ValidatorDecisionV1]) -> None:
        self.decisions = decisions
        self.sessions: list[ValidatorSessionFake] = []

    def create_sequence_first_validator(self) -> ValidatorSessionFake:
        session = ValidatorSessionFake(self.decisions[len(self.sessions)])
        self.sessions.append(session)
        return session


class ReaderFake:
    def __init__(self, outputs: list[ReaderVerdictV1]) -> None:
        self.outputs = outputs
        self.calls = 0

    def read(self, reader_request) -> ReaderVerdictV1:
        output = self.outputs[self.calls]
        self.calls += 1
        return output


class VoiceCueResolverFake:
    def __init__(self) -> None:
        self.calls = []

    def resolve(self, *, responder_ids, request):
        self.calls.append((responder_ids, request))
        return tuple(
            VoiceCueV1(
                character_id,
                {
                    "character:hana": "Warm and direct.",
                    "character:mia": "Bright and concise.",
                }.get(character_id, "Concise and character-faithful."),
            )
            for character_id in responder_ids
        )


def coordinator(
    *,
    plan: SequenceDraftV1 | None = None,
    writer_outputs: list[str | Exception] | None = None,
    validator_outputs: list[ValidatorDecisionV1] | None = None,
    reader_outputs: list[ReaderVerdictV1] | None = None,
):
    writer = WriterFake(writer_outputs or ["Hana asked what Ted wanted to do next."])
    factory = ValidatorFactoryFake(validator_outputs or [accepted_decision()])
    value = SequenceFirstCoordinator(
        planner=PlannerFake(plan or intended()),
        writer=writer,
        validator_factory=factory,
        reader=ReaderFake(reader_outputs or [accepted_reader()]),
        voice_cue_resolver=VoiceCueResolverFake(),
    )
    return value, writer, factory


class SequenceFirstSemanticBoundaryTests(unittest.TestCase):
    def test_active_world_authority_is_accepted_scope_bound(self) -> None:
        with TemporaryDirectory() as temporary:
            store = ContinuousWorldStore(Path(temporary).resolve())
            active = store.initialize("world-test", "branch-main") / "ACTIVE"
            assembler = ActiveWorldAuthorityAssembler(store)
            for name, character_id in (
                ("Ted", "character:ted"),
                ("Hana", "character:hana"),
            ):
                (active / "Characters" / f"{name}.json").write_text(
                    json.dumps(
                        {
                            "schema_version": "cera.continuous_character.v1",
                            "_cera_revision": 1,
                            "character_id": character_id,
                            "reasoning_summary": f"{name} accepted summary.",
                            "latest_accepted_changes": [],
                            "turn_claims": {},
                        },
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )
            store._rebuild_index(active)
            authority = SequenceFirstAcceptedAuthorityV1(
                schema_version=SequenceFirstAcceptedAuthorityV1.SCHEMA_VERSION,
                world_id="world-test",
                branch_id="branch-main",
                known_character_ids=("character:ted", "character:hana"),
                voice_cues=(VoiceCueV1("character:hana", "Warm and direct."),),
                hard_boundaries=("Do not invent Ted's response.",),
            )
            published = assembler.publish_initial_projection(authority)
            self.assertEqual(published, active / assembler.RELATIVE_PATH)
            self.assertEqual(
                assembler.assemble(world_id="world-test", branch_id="branch-main"),
                authority,
            )
            other_active = store.initialize("world-test", "other") / "ACTIVE"
            for path in (active / "Characters").glob("*.json"):
                (other_active / "Characters" / path.name).write_bytes(path.read_bytes())
            store._rebuild_index(other_active)
            (other_active / assembler.RELATIVE_PATH).write_text(
                json.dumps(to_primitive(authority), sort_keys=True),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(StateConflictError, "changed scope"):
                assembler.assemble(world_id="world-test", branch_id="other")

    def test_persistence_surface_is_character_or_canonical_relationship_only(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "outside ACTIVE policy"):
            PersistenceTargetCustodyV1(
                target_key="target:rule",
                target_file="Rules/rule.json",
                record_class="rule",
                target_record_id="rule:test",
                target_subject_ids=("character:hana",),
                expected_file_revision=1,
                operation=TargetOperationKind.ADD,
                field_path="/observations/turn_001",
            )
        with self.assertRaisesRegex(ContractValidationError, "canonical order"):
            PersistenceTargetCustodyV1(
                target_key="target:relationship",
                target_file="Relationships/Ted_Hana.json",
                record_class="relationship",
                target_record_id="relationship:ted_hana",
                target_subject_ids=("character:ted", "character:hana"),
                expected_file_revision=1,
                operation=TargetOperationKind.ADD,
                field_path="/observations/turn_001",
            )

    def test_model_authored_objects_have_no_python_custody_fields(self) -> None:
        objects = (
            semantic_input(),
            intended(),
            realized(),
            accepted_decision(),
        )
        forbidden = {
            "request_id",
            "candidate_id",
            "world_id",
            "branch_id",
            "scene_id",
            "turn_id",
            "parent_accepted_turn_id",
            "transaction_id",
            "receipt_sha256",
            "revision_id",
            "path",
        }
        for value in objects:
            payload = to_primitive(value)
            rendered = json.dumps(payload, sort_keys=True)
            for field_name in forbidden:
                self.assertNotIn(f'"{field_name}"', rendered)

    def test_python_binds_semantics_after_decode(self) -> None:
        turn = request()
        result, _, _ = coordinator()
        generated = result.generate(turn)
        self.assertEqual(generated.candidate.custody, turn.custody)
        self.assertNotIn("world-test", json.dumps(to_primitive(intended())))

    def test_no_model_authored_background_or_duplicate_coverage_field(self) -> None:
        sequence_fields = {field.name for field in fields(SequenceDraftV1)}
        self.assertNotIn("backgrounded_character_ids", sequence_fields)
        self.assertNotIn("responding_character_ids", sequence_fields)
        self.assertNotIn("beat_coverage", sequence_fields)
        self.assertNotIn("coverage", sequence_fields)
        self.assertEqual(realized().covered_planner_item_keys, ("hana_answers",))

    def test_responder_and_background_views_derive_from_item_owners(self) -> None:
        semantics = semantic_input()
        plan = intended()
        self.assertEqual(plan.responding_character_ids, ("character:hana",))
        self.assertEqual(
            semantics.derived_backgrounded_character_ids(plan),
            ("character:mia",),
        )

    def test_protected_user_exact_quote_is_python_verified(self) -> None:
        semantics = semantic_input(source='Ted says, "Please continue."')
        exact = intended(
            items=(
                SequenceItemV1(
                    item_key="ted_supplied_words",
                    kind=ItemKind.DIALOGUE_INTENT,
                    concise_meaning="Use only Ted's supplied words.",
                    owner_id="character:ted",
                    protected_user_exact_quotes=('"Please continue."',),
                ),
            )
        )
        semantics.validate_intended(exact)
        invalid = replace(
            exact,
            items=(
                replace(
                    exact.items[0],
                    protected_user_exact_quotes=('"Invented permission."',),
                ),
            ),
        )
        with self.assertRaisesRegex(ContractValidationError, "absent from current source"):
            semantics.validate_intended(invalid)

    def test_non_ted_item_cannot_claim_protected_user_source(self) -> None:
        with self.assertRaisesRegex(
            ContractValidationError,
            "requires protected-user ownership",
        ):
            SequenceItemV1(
                item_key="hana_misclaims_ted_source",
                kind=ItemKind.DIALOGUE_INTENT,
                concise_meaning="Hana answers Ted.",
                owner_id="character:hana",
                protected_user_exact_quotes=("Ted asks Hana a question.",),
            )

    def test_prior_sequence_is_the_only_model_visible_public_state_copy(self) -> None:
        semantics = replace(semantic_input(), prior_realized_sequence=realized())
        prompt = planner_turn_prompt(semantics)
        self.assertIn('"prior_realized_sequence"', prompt)
        self.assertNotIn('"current_public_scene_state"', prompt)
        self.assertNotIn('"unresolved_threads"', prompt.split('"prior_realized_sequence"', 1)[0])
        self.assertIn("newest current-turn packet", PLANNER_BASE_INSTRUCTIONS)
        self.assertIn("supersede", PLANNER_BASE_INSTRUCTIONS)

    def test_affected_and_observing_mini_ledgers_do_not_exist(self) -> None:
        item_fields = {field.name for field in fields(SequenceItemV1)}
        self.assertNotIn("affected_character_ids", item_fields)
        self.assertNotIn("observing_character_ids", item_fields)
        self.assertNotIn("addressed_ids", item_fields)
        self.assertNotIn("referenced_ids", item_fields)

    def test_validator_is_binary_and_review_flags_are_orthogonal(self) -> None:
        decision_fields = {field.name for field in fields(ValidatorDecisionV1)}
        self.assertEqual(
            {value.value for value in ValidatorVerdict},
            {"accept", "reject"},
        )
        self.assertIn("review_flags", decision_fields)
        self.assertNotIn("concern", {value.value for value in ValidatorVerdict})

    def test_retry_is_derived_not_model_authored(self) -> None:
        conflict_fields = {field.name for field in fields(ValidationConflictV1)}
        self.assertNotIn("writer_retry_appropriate", conflict_fields)
        self.assertTrue(
            writer_retry_eligible(
                failure_type=ControllerFailureType.SEMANTIC_WRITER_CONFLICT,
                decision=rejected_decision(),
            )
        )
        self.assertFalse(
            writer_retry_eligible(
                failure_type=ControllerFailureType.CAPABILITY,
                decision=rejected_decision(),
            )
        )

    def test_deepseek_writer_response_wire_has_exactly_two_fields(self) -> None:
        self.assertEqual(
            tuple(field.name for field in fields(WriterResponseV1)),
            ("schema_version", "story_text"),
        )

    def test_invalid_target_key_fails_closed(self) -> None:
        change = DurableChangeV1(
            change_key="hana_development",
            kind=DurableChangeKind.CHARACTER_DEVELOPMENT,
            subject_ids=("character:hana",),
            concise_change="Hana becomes more willing to ask direct questions.",
            target_key="target:missing",
        )
        plan = intended(
            items=(item(change_keys=(change.change_key,)),),
            durable_changes=(change,),
        )
        with self.assertRaisesRegex(ContractValidationError, "not approved"):
            semantic_input().validate_intended(plan)

    def test_protected_user_item_requires_an_exact_current_source_claim(self) -> None:
        with self.assertRaisesRegex(
            ContractValidationError,
            "protected-user-owned item requires",
        ):
            SequenceItemV1(
                item_key="ted_action",
                kind=ItemKind.ACTION,
                owner_id="character:ted",
                concise_meaning="Ted performs an action not supplied by Ted.",
                evidence_keys=("source:current",),
            )

        supplied = SequenceItemV1(
            item_key="ted_request",
            kind=ItemKind.ACTION,
            owner_id="character:ted",
            concise_meaning="Ted asks the others to continue.",
            evidence_keys=("source:current",),
            protected_user_claim_keys=("current_request",),
        )
        semantic_input().validate_intended(intended(items=(supplied,)))

    def test_private_evidence_cannot_authorize_another_character(self) -> None:
        base = semantic_input()
        private_evidence = EvidenceRecordV1(
            evidence_key="evidence:hana_private",
            subject_id="character:hana",
            visibility=Visibility.CHARACTER_PRIVATE,
            exact_content="Hana privately worries about the locked door.",
            knowledge_owner_id="character:hana",
        )
        semantics = replace(
            base,
            evidence_records=base.evidence_records + (private_evidence,),
        )
        mia_uses_hana_private_state = item(
            key="mia_private_inference",
            owner="character:mia",
            meaning="Mia acts on Hana's unshared worry.",
            evidence=("evidence:hana_private",),
        )
        with self.assertRaisesRegex(ContractValidationError, "different assertion owner"):
            semantics.validate_intended(
                intended(items=(mia_uses_hana_private_state,))
            )


class SequenceFirstPresenceTests(unittest.TestCase):
    def test_merely_mentioning_absent_mia_does_not_make_her_present(self) -> None:
        semantics = semantic_input(
            source="Ted asks Hana whether Mia is upstairs.",
            present=("character:ted", "character:hana"),
        )
        plan = intended()
        semantics.validate_intended(plan)
        self.assertNotIn("character:mia", semantics.accepted_present_character_ids)
        invented_mia = intended(
            items=(
                item(
                    key="mia_answers",
                    owner="character:mia",
                    meaning="Mia answers from the room.",
                ),
            )
        )
        with self.assertRaisesRegex(ContractValidationError, "absent item owner"):
            semantics.validate_intended(invented_mia)

    def test_accepted_present_mia_remains_silent_without_owned_item(self) -> None:
        semantics = semantic_input()
        plan = intended()
        semantics.validate_intended(plan)
        self.assertEqual(
            semantics.derived_backgrounded_character_ids(plan),
            ("character:mia",),
        )

    def test_structural_owner_does_not_make_backgrounded_mia_a_responder(self) -> None:
        semantics = semantic_input()
        plan = intended(
            items=(
                item(),
                item(
                    key="mia_remains_backgrounded",
                    owner="character:mia",
                    kind=ItemKind.MATERIAL_CONTINUITY,
                    meaning="Mia remains present but does not enter the exchange.",
                    evidence=(),
                    parent="hana_answers",
                ),
            )
        )
        semantics.validate_intended(plan)
        self.assertEqual(plan.responding_character_ids, ("character:hana",))
        self.assertEqual(
            semantics.derived_backgrounded_character_ids(plan),
            ("character:mia",),
        )

    def test_retrieval_about_absent_mia_does_not_create_presence_or_salience(self) -> None:
        base = semantic_input(present=("character:ted", "character:hana"))
        semantics = replace(
            base,
            evidence_records=base.evidence_records
            + (
                EvidenceRecordV1(
                    evidence_key="evidence:mia_profile",
                    subject_id="character:mia",
                    visibility=Visibility.PUBLIC,
                    exact_content="Mia prefers tea to coffee.",
                ),
            ),
        )
        plan = intended()
        semantics.validate_intended(plan)
        self.assertNotIn("character:mia", plan.responding_character_ids)
        self.assertNotIn("character:mia", semantics.accepted_present_character_ids)
        self.assertEqual(
            semantics.derived_backgrounded_character_ids(plan),
            (),
        )

    def test_entry_and_exit_apply_in_exact_item_order(self) -> None:
        enters = item(
            key="mia_enters",
            owner="character:mia",
            kind=ItemKind.ACTION,
            meaning="Mia enters after being called.",
            evidence=("source:current",),
        )
        leaves = item(
            key="mia_leaves",
            owner="character:mia",
            kind=ItemKind.ACTION,
            meaning="Mia leaves to resume her task.",
            evidence=("source:current",),
            parent="mia_enters",
        )
        plan = intended(
            items=(enters, leaves),
            presence_changes=(
                PresenceChangeV1(
                    "character:mia", PresenceDirection.ENTER, "mia_enters"
                ),
                PresenceChangeV1(
                    "character:mia", PresenceDirection.LEAVE, "mia_leaves"
                ),
            ),
        )
        semantics = semantic_input(
            source="Mia enters when Ted calls, speaks briefly, then leaves.",
            present=("character:ted", "character:hana"),
        )
        semantics.validate_intended(plan)
        self.assertEqual(
            apply_presence_changes(semantics.accepted_present_character_ids, plan),
            ("character:ted", "character:hana"),
        )

    def test_ambiguous_presence_produces_no_state_change(self) -> None:
        plan = intended()
        self.assertEqual(plan.presence_changes, ())
        self.assertEqual(
            apply_presence_changes(
                ("character:ted", "character:hana"),
                plan,
            ),
            ("character:ted", "character:hana"),
        )

    def test_phone_or_offscreen_speech_does_not_imply_physical_presence(self) -> None:
        remote = item(
            key="mia_phone_reply",
            owner="character:mia",
            kind=ItemKind.REMOTE_COMMUNICATION,
            meaning="Mia replies over the phone without entering the room.",
            evidence=("source:current",),
        )
        plan = intended(items=(remote,))
        semantics = semantic_input(
            source="Mia answers Hana over the phone.",
            present=("character:ted", "character:hana"),
        )
        semantics.validate_intended(plan)
        self.assertEqual(plan.presence_changes, ())
        self.assertEqual(
            apply_presence_changes(semantics.accepted_present_character_ids, plan),
            ("character:ted", "character:hana"),
        )

    def test_scene_change_reestablishes_presence(self) -> None:
        transition = SequenceItemV1(
            item_key="establish_new_room",
            kind=ItemKind.SCENE_TRANSITION,
            owner_id=None,
            concise_meaning="The new scene begins with Ted and Hana in the kitchen.",
            evidence_keys=("source:current",),
        )
        plan = intended(
            items=(transition,),
            presence_changes=(
                PresenceChangeV1(
                    "character:ted",
                    PresenceDirection.ENTER,
                    "establish_new_room",
                ),
                PresenceChangeV1(
                    "character:hana",
                    PresenceDirection.ENTER,
                    "establish_new_room",
                ),
            ),
        )
        semantics = semantic_input(
            source="The scene moves to the kitchen with Ted and Hana.",
            present=(),
            scene_reinitialization=True,
        )
        semantics.validate_intended(plan)
        self.assertEqual(
            apply_presence_changes((), plan),
            ("character:ted", "character:hana"),
        )


class SequenceFirstPipelineTests(unittest.TestCase):
    def test_voice_cues_resolve_only_after_planner_owner_selection(self) -> None:
        resolver = VoiceCueResolverFake()
        plan = intended(
            items=(
                item(key="mia_answers", owner="character:mia"),
            )
        )
        writer = WriterFake(["Mia answers Ted."])
        runtime = SequenceFirstCoordinator(
            planner=PlannerFake(plan),
            writer=writer,
            validator_factory=ValidatorFactoryFake([accepted_decision(realized(items=(item(key="mia_realized", owner="character:mia", planner_keys=("mia_answers",)),)))]),
            reader=ReaderFake([accepted_reader()]),
            voice_cue_resolver=resolver,
        )
        runtime.generate(request())
        self.assertEqual(resolver.calls[0][0], ("character:mia",))
        self.assertEqual(
            writer.briefs[0].voice_cues,
            (VoiceCueV1("character:mia", "Bright and concise."),),
        )
        self.assertNotIn("stopping_boundary", {field.name for field in fields(type(writer.briefs[0]))})
    def test_complete_fake_pipeline_accepts_and_binds(self) -> None:
        value, writer, factory = coordinator()
        result = value.generate(request())
        self.assertTrue(result.accepted)
        self.assertEqual(writer.calls, 1)
        self.assertEqual(len(factory.sessions), 1)
        self.assertTrue(factory.sessions[0].archived)
        self.assertEqual(result.candidate.accepted_attempt_number, 1)

    def test_three_writer_attempts_are_independent_and_never_merged(self) -> None:
        value, writer, factory = coordinator(
            writer_outputs=["Ted nodded.", "Ted nodded again.", "Hana asks a question."],
            validator_outputs=[
                rejected_decision(quote="Ted nodded."),
                rejected_decision(quote="Ted nodded again."),
                accepted_decision(),
            ],
            reader_outputs=[accepted_reader()],
        )
        result = value.generate(request())
        self.assertTrue(result.accepted)
        self.assertEqual(writer.calls, 3)
        self.assertEqual(result.candidate.writer_response.story_text, writer.outputs[2])
        self.assertEqual(len(factory.sessions), 3)
        self.assertTrue(all(session.archived for session in factory.sessions))
        self.assertEqual(
            tuple(receipt.status for receipt in result.attempt_receipts),
            (
                WriterAttemptStatus.VALIDATOR_REJECTED,
                WriterAttemptStatus.VALIDATOR_REJECTED,
                WriterAttemptStatus.ACCEPTED,
            ),
        )

    def test_transport_failure_never_opens_writer_retry(self) -> None:
        value, writer, _ = coordinator(
            writer_outputs=[RuntimeError("transport failed"), "must not run"],
        )
        with self.assertRaisesRegex(RuntimeError, "transport failed"):
            value.generate(request())
        self.assertEqual(writer.calls, 1)

    def test_capability_rejection_does_not_retry(self) -> None:
        value, writer, _ = coordinator(
            writer_outputs=["Unsupported candidate.", "must not run"],
            validator_outputs=[
                rejected_decision(
                    conflict_class=ConflictClass.CAPABILITY_RESTRICTION,
                    quote="Unsupported candidate.",
                )
            ],
        )
        result = value.generate(request())
        self.assertFalse(result.accepted)
        self.assertEqual(writer.calls, 1)

    def test_rejected_quote_must_exist_in_frozen_prose(self) -> None:
        value, _, _ = coordinator(
            writer_outputs=["Hana asks a question."],
            validator_outputs=[rejected_decision(quote="Ted nodded.")],
        )
        with self.assertRaisesRegex(ContractValidationError, "absent from frozen"):
            value.generate(request())

    def test_reader_rejection_cannot_commit_and_opens_fresh_attempt(self) -> None:
        value, writer, factory = coordinator(
            writer_outputs=["Again. Again. Again.", "Hana asks a clear question."],
            validator_outputs=[accepted_decision(), accepted_decision()],
            reader_outputs=[rejected_reader(), accepted_reader()],
        )
        result = value.generate(request())
        self.assertTrue(result.accepted)
        self.assertEqual(result.candidate.writer_response.story_text, writer.outputs[1])
        self.assertEqual(len(factory.sessions), 2)

    def test_reader_inconclusive_terminates_without_any_retry_or_repair(self) -> None:
        value, writer, factory = coordinator(
            writer_outputs=["Hana asks a clear question.", "must not run"],
            validator_outputs=[accepted_decision()],
            reader_outputs=[inconclusive_reader()],
        )
        result = value.generate(request())
        self.assertFalse(result.accepted)
        self.assertEqual(writer.calls, 1)
        self.assertEqual(len(factory.sessions), 1)
        self.assertTrue(factory.sessions[0].archived)
        self.assertEqual(result.attempt_receipts, ())
        self.assertEqual(
            result.terminal_reader_verdict.status,
            ReaderStatus.INCONCLUSIVE,
        )

    def test_regeneration_candidates_are_immutable_siblings(self) -> None:
        first_request = request(private_custody=custody(candidate_id="candidate-first"))
        second_request = request(private_custody=custody(candidate_id="candidate-second"))
        first = coordinator(writer_outputs=["First prose."])[0].generate(
            first_request
        ).candidate
        second = coordinator(writer_outputs=["Second prose."])[0].generate(
            second_request
        ).candidate
        self.assertNotEqual(first.custody.candidate_id, second.custody.candidate_id)
        self.assertEqual(
            first.custody.parent_accepted_turn_id,
            second.custody.parent_accepted_turn_id,
        )
        with self.assertRaises(FrozenInstanceError):
            first.accepted_attempt_number = 3


class SequenceFirstSessionTests(unittest.TestCase):
    class StoredLifecycleFake:
        def __init__(self, base_instructions: str, prefix: str) -> None:
            self.base_instructions = base_instructions
            self.prefix = prefix
            self.started = []
            self.archived = set()

        def start_stored_thread(self) -> str:
            thread_id = f"{self.prefix}-{len(self.started) + 1}"
            self.started.append(thread_id)
            return thread_id

        def resume_stored_thread(self, thread_id: str) -> bool:
            return thread_id in self.started and thread_id not in self.archived

        def archive_stored_thread(self, thread_id: str) -> None:
            self.archived.add(thread_id)

        def stored_thread_is_selectable(self, thread_id: str) -> bool:
            return thread_id in self.started and thread_id not in self.archived

    class CodexTransportFake:
        captured_schemas = []

        def __init__(self, route, *, workspace: Path, runner) -> None:
            self.route = route
            self.workspace = workspace
            self.runner = runner

        def invoke(
            self,
            prompt: str,
            *,
            output_schema: dict,
            mcp_binding=None,
            on_worker_started,
            on_worker_preflight,
            on_transport_invoke,
        ):
            self.__class__.captured_schemas.append(
                (self.route.adapter_id, output_schema)
            )
            on_worker_started()
            on_worker_preflight()
            on_transport_invoke()
            if self.route.adapter_id == SEQUENCE_FIRST_PLANNER_ADAPTER:
                payload = to_primitive(intended())
            elif self.route.adapter_id == SEQUENCE_FIRST_VALIDATOR_ADAPTER:
                payload = to_primitive(accepted_decision())
            elif self.route.adapter_id == SEQUENCE_FIRST_READER_ADAPTER:
                payload = to_primitive(accepted_reader())
            else:
                raise AssertionError("unexpected fake provider route")
            return SimpleNamespace(
                parsed_json=payload,
                receipt={"route": self.route.route_id},
                operation_telemetry=None,
                tool_call_count=0,
                failed_tool_call_count=0,
                tool_names=(),
                tool_server_names=(),
            )

    class PlannerBackend:
        def __init__(self) -> None:
            self.starts = []
            self.prompts = []

        def start_stored_thread(self, *, base_instructions: str, profile: str) -> str:
            self.starts.append((base_instructions, profile))
            return "planner-thread"

        def run_planner_turn(self, *, thread_id: str, prompt: str) -> SequenceDraftV1:
            self.prompts.append(prompt)
            return intended()

        def is_resumable(self, thread_id: str) -> bool:
            return True

    class ValidatorBackend:
        def __init__(self) -> None:
            self.starts = []
            self.prompts = []
            self.archived = set()

        def start_fresh_thread(self, *, base_instructions: str, profile: str) -> str:
            thread_id = f"validator-{len(self.starts) + 1}"
            self.starts.append((thread_id, base_instructions, profile))
            return thread_id

        def run_validator_once(self, *, thread_id: str, prompt: str):
            self.prompts.append((thread_id, prompt))
            return accepted_decision()

        def archive(self, thread_id: str) -> None:
            self.archived.add(thread_id)

        def is_resumable(self, thread_id: str) -> bool:
            return thread_id not in self.archived

    def test_planner_stable_instructions_are_installed_once(self) -> None:
        backend = self.PlannerBackend()
        planner = PersistentPlannerSession(backend)
        planner.plan(semantic_input())
        planner.plan(semantic_input(source="Continue again"))
        self.assertEqual(backend.starts, [(PLANNER_BASE_INSTRUCTIONS, PLANNER_PROFILE)])
        self.assertEqual(len(backend.prompts), 2)
        self.assertTrue(
            all(PLANNER_BASE_INSTRUCTIONS not in prompt for prompt in backend.prompts)
        )

    def test_validator_is_fresh_compact_and_nonresumable_per_candidate(self) -> None:
        backend = self.ValidatorBackend()
        factory = FreshCandidateValidatorFactory(backend)
        for _ in range(2):
            session = factory.create_sequence_first_validator()
            session.validate(
                __import__(
                    "cera.sequence_first.contracts",
                    fromlist=["SequenceFirstValidatorInputV1"],
                ).SequenceFirstValidatorInputV1(
                    intended_sequence=intended(),
                    exact_writer_prose="Hana asks a question.",
                    prior_realized_sequence=None,
                    accepted_present_character_ids=("character:ted", "character:hana"),
                    current_public_scene_state="Ted and Hana are in the room.",
                    protected_source_claims=(),
                    hard_boundaries=("Do not invent Ted's response.",),
                )
            )
            session.archive_and_prove_nonresumable()
        self.assertEqual(len(backend.starts), 2)
        self.assertEqual(
            {value[2] for value in backend.starts},
            {VALIDATOR_PROFILE},
        )
        self.assertTrue(
            all(value[1] == VALIDATOR_BASE_INSTRUCTIONS for value in backend.starts)
        )
        self.assertTrue(
            all(VALIDATOR_BASE_INSTRUCTIONS not in prompt for _, prompt in backend.prompts)
        )
        self.assertEqual(backend.archived, {"validator-1", "validator-2"})
        self.assertNotIn("exhaustive", VALIDATOR_PROFILE)

    def test_provider_schemas_expose_semantics_only(self) -> None:
        rendered = json.dumps(
            {
                "planner": sequence_draft_json_schema(),
                "validator": validator_decision_json_schema(),
                "reader": reader_verdict_json_schema(),
            },
            sort_keys=True,
        )
        for forbidden in (
            "world_id",
            "branch_id",
            "scene_id",
            "turn_id",
            "candidate_id",
            "request_id",
            "backgrounded_character_ids",
            "responding_character_ids",
            "beat_coverage",
            "writer_retry_appropriate",
            "affected_ids",
            "observing_ids",
            "output_start",
            "output_end",
        ):
            self.assertNotIn(f'"{forbidden}"', rendered)

    def test_provider_local_key_schema_matches_closed_python_grammar(self) -> None:
        sequence_schema = sequence_draft_json_schema()
        item_schema = sequence_schema["properties"]["items"]["items"]["properties"]
        durable_schema = sequence_schema["properties"]["durable_changes"]["items"]["properties"]
        presence_schema = sequence_schema["properties"]["presence_changes"]["items"]["properties"]
        self.assertEqual(item_schema["item_key"]["pattern"], LOCAL_KEY_JSON_PATTERN)
        self.assertEqual(
            item_schema["causal_parent_item_key"]["anyOf"][0]["pattern"],
            LOCAL_KEY_JSON_PATTERN,
        )
        for name in (
            "protected_user_claim_keys",
            "durable_change_keys",
            "planner_item_keys",
        ):
            self.assertEqual(
                item_schema[name]["items"]["pattern"], LOCAL_KEY_JSON_PATTERN
            )
        self.assertEqual(item_schema["planner_item_keys"]["maxItems"], 0)
        projected = project_provider_output_schema(
            sequence_schema,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        projected_planner_keys = projected["properties"]["items"]["items"][
            "properties"
        ]["planner_item_keys"]
        self.assertEqual(projected_planner_keys["maxItems"], 0)
        self.assertEqual(
            durable_schema["change_key"]["pattern"], LOCAL_KEY_JSON_PATTERN
        )
        self.assertEqual(
            presence_schema["effective_after_item_key"]["pattern"],
            LOCAL_KEY_JSON_PATTERN,
        )

        validator = validator_decision_json_schema()["properties"]
        review_flag = validator["review_flags"]["items"]["properties"]
        conflict = validator["conflict"]["anyOf"][0]["properties"]
        realized = validator["realized_sequence"]["anyOf"][0]
        self.assertEqual(
            review_flag["flag_code"]["pattern"], LOCAL_KEY_JSON_PATTERN
        )
        self.assertEqual(
            conflict["omitted_planner_item_key"]["anyOf"][0]["pattern"],
            LOCAL_KEY_JSON_PATTERN,
        )
        self.assertEqual(
            realized["properties"]["items"]["items"]["properties"]["item_key"]["pattern"],
            LOCAL_KEY_JSON_PATTERN,
        )
        realized_planner_keys = realized["properties"]["items"]["items"][
            "properties"
        ]["planner_item_keys"]
        self.assertEqual(
            realized_planner_keys["items"]["pattern"], LOCAL_KEY_JSON_PATTERN
        )
        self.assertNotIn("maxItems", realized_planner_keys)
        reader_issue = reader_verdict_json_schema()["properties"]["issues"]["items"]["properties"]
        self.assertEqual(
            reader_issue["issue_code"]["pattern"], LOCAL_KEY_JSON_PATTERN
        )

        with self.assertRaises(ContractValidationError):
            item(key="item:hana_answers")

    def test_validator_schema_is_strict_complete_and_closed(self) -> None:
        schema = validator_decision_json_schema()
        self.assertIsInstance(schema, dict)
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            schema["required"],
            ["verdict", "realized_sequence", "review_flags", "conflict"],
        )
        properties = schema["properties"]
        self.assertEqual(properties["verdict"]["enum"], ["accept", "reject"])
        self.assertEqual(
            properties["realized_sequence"]["anyOf"][1],
            {"type": "null"},
        )
        self.assertEqual(properties["review_flags"]["type"], "array")
        conflict = properties["conflict"]["anyOf"][0]
        self.assertEqual(conflict["type"], "object")
        self.assertFalse(conflict["additionalProperties"])
        self.assertEqual(
            conflict["properties"]["conflict_class"]["enum"],
            [value.value for value in ConflictClass],
        )

    def test_validator_provider_payloads_decode_both_closed_branches(self) -> None:
        accepted = from_mapping(
            ValidatorDecisionV1,
            to_primitive(accepted_decision()),
        )
        rejected = from_mapping(
            ValidatorDecisionV1,
            to_primitive(rejected_decision()),
        )
        self.assertIs(accepted.verdict, ValidatorVerdict.ACCEPT)
        self.assertIs(rejected.verdict, ValidatorVerdict.REJECT)

    def test_validator_provider_payload_rejects_mixed_branches(self) -> None:
        accepted_with_conflict = to_primitive(accepted_decision())
        accepted_with_conflict["conflict"] = to_primitive(
            rejected_decision().conflict
        )
        with self.assertRaisesRegex(
            ContractValidationError,
            "accepted Validator branch is invalid",
        ):
            from_mapping(ValidatorDecisionV1, accepted_with_conflict)

        rejected_with_realization = to_primitive(rejected_decision())
        rejected_with_realization["realized_sequence"] = to_primitive(realized())
        with self.assertRaisesRegex(
            ContractValidationError,
            "rejected Validator branch is invalid",
        ):
            from_mapping(ValidatorDecisionV1, rejected_with_realization)

    def test_new_routes_are_explicit_unpromoted_and_have_no_fallback(self) -> None:
        planner_route = sequence_first_planner_route()
        validator_route = sequence_first_validator_route(
            model="gpt-5.6-terra",
            effort="medium",
        )
        reader_route = sequence_first_reader_route(
            model="gpt-5.6-terra",
            effort="high",
        )
        self.assertEqual(planner_route.adapter_id, SEQUENCE_FIRST_PLANNER_ADAPTER)
        self.assertEqual(
            validator_route.adapter_id,
            SEQUENCE_FIRST_VALIDATOR_ADAPTER,
        )
        self.assertEqual(reader_route.adapter_id, SEQUENCE_FIRST_READER_ADAPTER)
        for route in (planner_route, validator_route, reader_route):
            self.assertFalse(route.fallback_enabled)
            self.assertFalse(route.production_enabled)
            self.assertEqual(route.automatic_retry_count, 0)
            self.assertNotIn("exhaustive", route.adapter_id)

    def test_actual_codex_adapters_use_ledger_and_isolate_validator_reader(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            ledger = ContinuousProviderCallLedger(
                root / "provider_calls.jsonl",
                maximum_calls=3,
            )
            planner_lifecycle = self.StoredLifecycleFake(
                PLANNER_BASE_INSTRUCTIONS,
                "planner",
            )
            validator_lifecycle = self.StoredLifecycleFake(
                VALIDATOR_BASE_INSTRUCTIONS,
                "validator",
            )
            reader_lifecycle = self.StoredLifecycleFake(
                __import__(
                    "cera.sequence_first.prompting",
                    fromlist=["READER_BASE_INSTRUCTIONS"],
                ).READER_BASE_INSTRUCTIONS,
                "reader",
            )
            planner = PersistentPlannerSession(
                SequenceFirstPlannerCodexBackend(
                    lifecycle=planner_lifecycle,
                    workspace=root,
                    call_ledger=ledger,
                )
            )
            validator = FreshCandidateValidatorFactory(
                SequenceFirstValidatorCodexBackend(
                    lifecycle=validator_lifecycle,
                    workspace=root,
                    model="gpt-5.6-terra",
                    effort="medium",
                    call_ledger=ledger,
                )
            )
            reader = SequenceFirstReaderCodexPort(
                lifecycle=reader_lifecycle,
                workspace=root,
                model="gpt-5.6-terra",
                effort="high",
                call_ledger=ledger,
            )
            runtime = SequenceFirstCoordinator(
                planner=planner,
                writer=WriterFake(["Hana asks what Ted wants to do next."]),
                validator_factory=validator,
                reader=reader,
                voice_cue_resolver=VoiceCueResolverFake(),
            )
            self.CodexTransportFake.captured_schemas = []
            with patch(
                "cera.sequence_first.provider.CodexSDKTransport",
                self.CodexTransportFake,
            ):
                result = runtime.generate(request())

            self.assertTrue(result.accepted)
            self.assertEqual(ledger.dispatched_call_count, 3)
            self.assertEqual(planner_lifecycle.archived, set())
            self.assertEqual(validator_lifecycle.archived, {"validator-1"})
            self.assertEqual(reader_lifecycle.archived, {"reader-1"})
            accepted_owners = {
                event["owner"]
                for event in ledger.events
                if event["state"] == "typed_accepted"
            }
            self.assertEqual(
                accepted_owners,
                {"planner", "validator", "reader"},
            )
            validator_schemas = [
                schema
                for adapter_id, schema in self.CodexTransportFake.captured_schemas
                if adapter_id == SEQUENCE_FIRST_VALIDATOR_ADAPTER
            ]
            self.assertEqual(validator_schemas, [validator_decision_json_schema()])
            self.assertIsNotNone(validator_schemas[0])


class SequenceFirstStage6AdapterTests(unittest.TestCase):
    def state(self, *, present=("character:ted", "character:hana")):
        return AcceptedSceneStateV1(
            world_id="world-test",
            branch_id="branch-main",
            scene_id="scene-entry",
            parent_accepted_turn_id=None,
            accepted_head_sha256=None,
            known_character_ids=("character:ted", "character:hana", "character:mia"),
            accepted_present_character_ids=present,
            explicitly_authorized_remote_character_ids=(),
            current_public_scene_state="Ted and Hana are in the room.",
            prior_realized_sequence=None,
            character_deltas=(),
            evidence_records=(
                EvidenceRecordV1(
                    "evidence:hana_voice",
                    "character:hana",
                    Visibility.PUBLIC,
                    "Hana is warm and direct.",
                ),
            ),
            approved_targets=(),
            persistence_targets=(),
            unresolved_threads=("Ted has the next choice.",),
        )

    def ingress(self, *, source="Ted asks Hana whether Mia is upstairs.", scene=False):
        return FrozenSillyTavernIngressV1(
            request_id="request-stage6",
            candidate_id="candidate-stage6",
            turn_id="turn-stage6",
            transaction_id="transaction-stage6",
            exact_current_source=source,
            current_source_key="source:current",
            protected_source_claims=(
                ProtectedSourceClaimV1("current_request", source),
            ),
            hard_boundaries=("Do not invent Ted's response.",),
            scene_reinitialization=scene,
        )

    def test_current_message_names_do_not_change_accepted_presence(self) -> None:
        prepared = SequenceFirstSillyTavernAdapter.prepare_request(
            ingress=self.ingress(),
            accepted_state=self.state(),
        )
        self.assertEqual(
            prepared.semantic_input.accepted_present_character_ids,
            ("character:ted", "character:hana"),
        )
        self.assertNotIn(
            "character:mia",
            prepared.semantic_input.accepted_present_character_ids,
        )

    def test_scene_reinitialization_keeps_authorized_presence_not_prior_sequence(self) -> None:
        state = replace(
            self.state(present=("character:ted", "character:hana", "character:mia")),
            prior_realized_sequence=realized(),
        )
        prepared = SequenceFirstSillyTavernAdapter.prepare_request(
            ingress=self.ingress(source="The scene changes to the kitchen.", scene=True),
            accepted_state=state,
        )
        self.assertEqual(
            prepared.semantic_input.accepted_present_character_ids,
            ("character:ted", "character:hana", "character:mia"),
        )
        self.assertIsNone(prepared.semantic_input.prior_realized_sequence)

    def test_stage6_adapter_reaches_sequence_first_coordinator_provider_free(self) -> None:
        value, _, _ = coordinator()
        adapter = SequenceFirstSillyTavernAdapter(value)
        result = adapter.generate(
            ingress=self.ingress(source="Continue the scene"),
            accepted_state=self.state(
                present=("character:ted", "character:hana", "character:mia")
            ),
        )
        self.assertTrue(result.accepted)

    def test_new_stage6_module_has_no_old_semantic_cast_helpers(self) -> None:
        source = Path(
            "src/cera/sillytavern/sequence_first_adapter.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "_candidate_character_ids",
            "eligible_responder_ids",
            "planner_requested_character_ids",
            "default_sakura",
            "re.compile",
            "aliases",
            "active_cast_ids",
        ):
            self.assertNotIn(forbidden, source)


class SequenceFirstWorldTransactionTests(unittest.TestCase):
    def _qualified_candidate(self, store: ContinuousWorldStore, root: Path):
        active = store.initialize("world-test", "branch-main") / "ACTIVE"
        character_path = active / "Characters" / "hana.json"
        store._write_json(
            character_path,
            {
                "schema_version": "cera.character.v1",
                "_cera_revision": 1,
                "character_id": "character:hana",
                "reasoning_summary": "Hana answers gently.",
            },
        )
        store._rebuild_index(active)
        before = store.tree_sha256(active)
        prior_value = "Hana answers gently."
        target = ApprovedTargetV1(
            target_key="target:hana_reasoning",
            allowed_change_kinds=(DurableChangeKind.CHARACTER_DEVELOPMENT,),
        )
        private_target = PersistenceTargetCustodyV1(
            target_key=target.target_key,
            target_file="Characters/hana.json",
            record_class="character",
            target_record_id="character:hana",
            target_subject_ids=("character:hana",),
            expected_file_revision=1,
            operation=TargetOperationKind.REPLACE,
            field_path="/reasoning_summary",
            expected_prior_value_sha256=canonical_sha256(prior_value),
        )
        change = DurableChangeV1(
            change_key="hana_directness",
            kind=DurableChangeKind.CHARACTER_DEVELOPMENT,
            subject_ids=("character:hana",),
            concise_change="Hana now asks direct questions when uncertainty matters.",
            target_key=target.target_key,
        )
        plan = intended(
            items=(item(change_keys=(change.change_key,)),),
            durable_changes=(change,),
        )
        final = realized(
            items=(
                item(
                    key="hana_answer_realized",
                    planner_keys=("hana_answers",),
                    change_keys=(change.change_key,),
                ),
            ),
            durable_changes=(change,),
        )
        semantics = semantic_input(approved_targets=(target,))
        private = custody(
            candidate_id="candidate-world",
            turn_id="turn-001",
            source=semantics.exact_current_source,
            accepted_head=before,
            targets=(private_target,),
        )
        value, _, _ = coordinator(
            plan=plan,
            validator_outputs=[accepted_decision(final)],
        )
        result = value.generate(request(semantics=semantics, private_custody=private))
        return result, before

    def test_target_key_reaches_existing_revisioned_atomic_store(self) -> None:
        with TemporaryDirectory() as temporary:
            store = ContinuousWorldStore(Path(temporary).resolve())
            result, before = self._qualified_candidate(store, Path(temporary))
            turn_id = SequenceFirstCoordinator.commit(
                result,
                transaction=SequenceFirstWorldTransaction(store),
                expected_parent_accepted_turn_id=None,
                creator_accepted=True,
            )
            self.assertEqual(turn_id, "turn-001")
            active = store.branch_root("world-test", "branch-main") / "ACTIVE"
            document = json.loads(
                (active / "Characters" / "hana.json").read_text(encoding="utf-8")
            )
            self.assertEqual(document["_cera_revision"], 2)
            self.assertEqual(
                document["reasoning_summary"],
                "Hana now asks direct questions when uncertainty matters.",
            )
            self.assertNotEqual(store.tree_sha256(active), before)
            receipt = (
                store.branch_root("world-test", "branch-main")
                / "CANDIDATES"
                / "turn-001"
                / "PROMOTION_RECEIPT.json"
            )
            self.assertTrue(receipt.is_file())

            restarted_store = ContinuousWorldStore(Path(temporary).resolve())
            accepted_head = SequenceFirstWorldTransaction(
                restarted_store
            ).load_accepted_head(
                world_id="world-test",
                branch_id="branch-main",
            )
            self.assertEqual(accepted_head.accepted_turn_id, "turn-001")
            self.assertEqual(
                accepted_head.accepted_present_character_ids,
                ("character:ted", "character:hana", "character:mia"),
            )
            self.assertIsNotNone(accepted_head.prior_realized_sequence)
            self.assertFalse(hasattr(accepted_head, "provider_thread_id"))

    def test_branch_scoped_restart_loader_does_not_read_sibling_artifacts(self) -> None:
        with TemporaryDirectory() as temporary:
            store = ContinuousWorldStore(Path(temporary).resolve())
            result, _ = self._qualified_candidate(store, Path(temporary))
            SequenceFirstCoordinator.commit(
                result,
                transaction=SequenceFirstWorldTransaction(store),
                expected_parent_accepted_turn_id=None,
                creator_accepted=True,
            )
            store.initialize("world-test", "branch-sibling")
            sibling = SequenceFirstWorldTransaction(store).load_accepted_head(
                world_id="world-test",
                branch_id="branch-sibling",
            )
            self.assertIsNone(sibling.accepted_turn_id)
            self.assertIsNone(sibling.prior_realized_sequence)
            self.assertEqual(sibling.accepted_present_character_ids, ())

    def test_failure_before_atomic_swap_leaves_active_unchanged(self) -> None:
        def failpoint(stage: str) -> None:
            if stage == "journal_created":
                raise RuntimeError("simulated before swap")

        with TemporaryDirectory() as temporary:
            store = ContinuousWorldStore(
                Path(temporary).resolve(),
                promotion_failpoint=failpoint,
            )
            result, before = self._qualified_candidate(store, Path(temporary))
            with self.assertRaisesRegex(RuntimeError, "before swap"):
                SequenceFirstCoordinator.commit(
                    result,
                    transaction=SequenceFirstWorldTransaction(store),
                    expected_parent_accepted_turn_id=None,
                    creator_accepted=True,
                )
            active = store.branch_root("world-test", "branch-main") / "ACTIVE"
            self.assertEqual(store.tree_sha256(active), before)
            document = json.loads(
                (active / "Characters" / "hana.json").read_text(encoding="utf-8")
            )
            self.assertEqual(document["_cera_revision"], 1)

    def test_restart_finishes_receipt_after_installed_active_interruption(self) -> None:
        def failpoint(stage: str) -> None:
            if stage == "prepared_active_installed":
                raise RuntimeError("simulated after active install")

        with TemporaryDirectory() as temporary:
            runtime_root = Path(temporary).resolve()
            interrupted_store = ContinuousWorldStore(
                runtime_root,
                promotion_failpoint=failpoint,
            )
            result, before = self._qualified_candidate(
                interrupted_store,
                runtime_root,
            )
            with self.assertRaisesRegex(RuntimeError, "after active install"):
                SequenceFirstCoordinator.commit(
                    result,
                    transaction=SequenceFirstWorldTransaction(interrupted_store),
                    expected_parent_accepted_turn_id=None,
                    creator_accepted=True,
                )

            active = (
                interrupted_store.branch_root("world-test", "branch-main")
                / "ACTIVE"
            )
            self.assertNotEqual(interrupted_store.tree_sha256(active), before)

            restarted_store = ContinuousWorldStore(runtime_root)
            accepted_head = SequenceFirstWorldTransaction(
                restarted_store
            ).load_accepted_head(
                world_id="world-test",
                branch_id="branch-main",
            )
            self.assertEqual(accepted_head.accepted_turn_id, "turn-001")
            receipt = (
                restarted_store.branch_root("world-test", "branch-main")
                / "CANDIDATES"
                / "turn-001"
                / "PROMOTION_RECEIPT.json"
            )
            self.assertTrue(receipt.is_file())
            journal = next(
                restarted_store.branch_root("world-test", "branch-main").glob(
                    ".acceptance-sequence-first-*/JOURNAL.json"
                )
            )
            self.assertEqual(
                json.loads(journal.read_text(encoding="utf-8"))["state"],
                "finalized",
            )


if __name__ == "__main__":
    unittest.main()
