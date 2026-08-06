from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cera.continuous.world import ContinuousWorldStore
from cera.errors import ContractValidationError
from cera.serialization import text_sha256
from cera.sequence_first import (
    ConflictClass,
    EvidenceRecordV1,
    ItemKind,
    PresenceChangeV1,
    PresenceDirection,
    ProtectedSourceClaimV1,
    ReaderStatus,
    ReaderVerdictV1,
    SequenceDraftV1,
    SequenceFirstCoordinator,
    SequenceItemV1,
    ValidationConflictV1,
    ValidatorDecisionV1,
    ValidatorVerdict,
    Visibility,
    VoiceCueV1,
    WriterResponseV1,
)
from cera.sequence_first.world import SequenceFirstWorldTransaction
from cera.sillytavern.models import CERA_CONTINUOUS_V3_PROVIDER_MANUAL_MODEL
from cera.sillytavern.sequence_first_adapter import SequenceFirstSillyTavernAdapter
from cera.sillytavern.sequence_first_stage6 import (
    ExplicitSceneInitializationV1,
    SequenceFirstPresenceAuthorityError,
    SequenceFirstStage6Bridge,
    SequenceFirstStage6StateProjectionV1,
    SequenceFirstStage6TurnCustodyV1,
)


def sequence(
    *,
    owner: str = "character:hana",
    kind: ItemKind = ItemKind.DIALOGUE_INTENT,
    item_key: str = "hana_responds",
    presence_changes: tuple[PresenceChangeV1, ...] = (),
) -> SequenceDraftV1:
    return SequenceDraftV1(
        items=(
            SequenceItemV1(
                item_key=item_key,
                kind=kind,
                concise_meaning="The owner responds without controlling Ted.",
                owner_id=owner,
                evidence_keys=("source:current",),
            ),
        ),
        durable_changes=(),
        presence_changes=presence_changes,
        resulting_public_state="The exchange pauses with Ted retaining the floor.",
        unresolved_threads=("Ted retains the next choice.",),
        stopping_boundary="Stop when Ted has the next meaningful choice.",
    )


def realized_from(intended: SequenceDraftV1) -> SequenceDraftV1:
    key_map = {item.item_key: f"realized_{item.item_key}" for item in intended.items}
    return SequenceDraftV1(
        items=tuple(
            SequenceItemV1(
                item_key=key_map[item.item_key],
                kind=item.kind,
                concise_meaning=item.concise_meaning,
                owner_id=item.owner_id,
                causal_parent_item_key=(
                    None
                    if item.causal_parent_item_key is None
                    else key_map[item.causal_parent_item_key]
                ),
                evidence_keys=item.evidence_keys,
                protected_user_claim_keys=item.protected_user_claim_keys,
                durable_change_keys=item.durable_change_keys,
                planner_item_keys=(item.item_key,),
            )
            for item in intended.items
        ),
        durable_changes=intended.durable_changes,
        presence_changes=tuple(
            PresenceChangeV1(
                character_id=change.character_id,
                direction=change.direction,
                effective_after_item_key=key_map[change.effective_after_item_key],
            )
            for change in intended.presence_changes
        ),
        resulting_public_state=intended.resulting_public_state,
        unresolved_threads=intended.unresolved_threads,
        stopping_boundary=intended.stopping_boundary,
    )


class PlannerFake:
    def __init__(self, plans: list[SequenceDraftV1]) -> None:
        self.plans = plans
        self.inputs = []

    def plan(self, semantic_input):
        self.inputs.append(semantic_input)
        return self.plans[len(self.inputs) - 1]


class WriterFake:
    def __init__(self, prose: str = "Hana asks Ted what he would like to do next.") -> None:
        self.prose = prose
        self.calls = 0

    def write(self, brief, attempt_number: int) -> WriterResponseV1:
        self.calls += 1
        return WriterResponseV1("cera.scene_writer_draft.v1", self.prose)


class ValidatorSessionFake:
    def __init__(self, *, reject: bool = False, quote: str | None = None) -> None:
        self.reject = reject
        self.quote = quote
        self.archived = False

    def validate(self, request):
        if self.reject:
            return ValidatorDecisionV1(
                verdict=ValidatorVerdict.REJECT,
                realized_sequence=None,
                conflict=ValidationConflictV1(
                    conflict_class=ConflictClass.CAPABILITY_RESTRICTION,
                    concise_explanation="The candidate cannot be accepted.",
                    exact_quote=self.quote,
                ),
            )
        return ValidatorDecisionV1(
            verdict=ValidatorVerdict.ACCEPT,
            realized_sequence=realized_from(request.intended_sequence),
        )

    def archive_and_prove_nonresumable(self) -> None:
        self.archived = True


class ValidatorFactoryFake:
    def __init__(self, *, reject: bool = False, quote: str | None = None) -> None:
        self.reject = reject
        self.quote = quote
        self.sessions = []

    def create_sequence_first_validator(self):
        session = ValidatorSessionFake(reject=self.reject, quote=self.quote)
        self.sessions.append(session)
        return session


class ReaderFake:
    def __init__(self) -> None:
        self.calls = 0

    def read(self, request) -> ReaderVerdictV1:
        self.calls += 1
        return ReaderVerdictV1(ReaderStatus.ACCEPTED)


class SequenceFirstStage6BridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._identity = 0

    def raw(self, source: str, *, scene_change: bool = False) -> dict:
        return {
            "model": CERA_CONTINUOUS_V3_PROVIDER_MANUAL_MODEL,
            "messages": [
                {"role": "system", "content": "SillyTavern fixture."},
                {"role": "user", "content": source},
            ],
            "stream": False,
            "cera_scene_change": scene_change,
        }

    def custody(self, source: str) -> SequenceFirstStage6TurnCustodyV1:
        self._identity += 1
        suffix = f"{self._identity:03d}"
        return SequenceFirstStage6TurnCustodyV1(
            request_id=f"request-{suffix}",
            candidate_id=f"candidate-{suffix}",
            turn_id=f"turn-{suffix}",
            transaction_id=f"transaction-{suffix}",
            current_source_key="source:current",
            protected_source_claims=(
                ProtectedSourceClaimV1("current_request", source),
            ),
            hard_boundaries=("Do not invent Ted's response.",),
        )

    def projection(
        self,
        *,
        remote: tuple[str, ...] = (),
        include_mia_evidence: bool = False,
    ) -> SequenceFirstStage6StateProjectionV1:
        evidence = [
            EvidenceRecordV1(
                "evidence:hana_voice",
                "character:hana",
                Visibility.PUBLIC,
                "Hana is warm and direct.",
            )
        ]
        if include_mia_evidence:
            evidence.append(
                EvidenceRecordV1(
                    "evidence:mia_background",
                    "character:mia",
                    Visibility.PUBLIC,
                    "Mia is elsewhere in the house.",
                )
            )
        return SequenceFirstStage6StateProjectionV1(
            known_character_ids=(
                "character:ted",
                "character:hana",
                "character:mia",
            ),
            explicitly_authorized_remote_character_ids=remote,
            evidence_records=tuple(evidence),
        )

    def runtime(
        self,
        store: ContinuousWorldStore,
        plans: list[SequenceDraftV1],
        *,
        reject: bool = False,
        prose: str = "Hana asks Ted what he would like to do next.",
    ):
        planner = PlannerFake(plans)
        writer = WriterFake(prose)
        validator = ValidatorFactoryFake(reject=reject, quote=prose if reject else None)
        reader = ReaderFake()
        coordinator = SequenceFirstCoordinator(
            planner=planner,
            writer=writer,
            validator_factory=validator,
            reader=reader,
        )
        transaction = SequenceFirstWorldTransaction(store)
        bridge = SequenceFirstStage6Bridge(
            adapter=SequenceFirstSillyTavernAdapter(coordinator),
            transaction=transaction,
        )
        return bridge, planner, writer, validator, reader

    def initialize_and_accept(
        self,
        *,
        store: ContinuousWorldStore,
        present: tuple[str, ...] = ("character:ted", "character:hana"),
        branch_id: str = "branch-main",
        scene_id: str = "scene-entry",
    ):
        source = "Begin the explicitly initialized scene."
        bridge, *_ = self.runtime(store, [sequence()])
        prepared = bridge.prepare(
            raw_request=self.raw(source),
            world_id="world-test",
            branch_id=branch_id,
            custody=self.custody(source),
            state_projection=self.projection(),
            scene_initialization=ExplicitSceneInitializationV1(
                scene_id=scene_id,
                accepted_present_character_ids=present,
                current_public_scene_state="The explicitly initialized cast is present.",
                unresolved_threads=("Ted retains the next choice.",),
            ),
        )
        result = bridge.generate(
            prepared,
            voice_cues=(VoiceCueV1("character:hana", "Warm and direct."),),
        )
        return bridge.accept_and_reload(prepared, result, creator_accepted=True)

    def test_uninitialized_branch_fails_closed_without_explicit_authority(self) -> None:
        with TemporaryDirectory() as temporary:
            store = ContinuousWorldStore(Path(temporary).resolve())
            bridge, *_ = self.runtime(store, [sequence()])
            source = "Mia may be nearby."
            with self.assertRaises(SequenceFirstPresenceAuthorityError):
                bridge.prepare(
                    raw_request=self.raw(source),
                    world_id="world-test",
                    branch_id="branch-main",
                    custody=self.custody(source),
                    state_projection=self.projection(),
                )

    def test_raw_scene_change_control_cannot_create_presence_authority(self) -> None:
        with TemporaryDirectory() as temporary:
            store = ContinuousWorldStore(Path(temporary).resolve())
            self.initialize_and_accept(store=store)
            bridge, *_ = self.runtime(store, [sequence()])
            source = "The scene changes and Mia is there."
            with self.assertRaises(SequenceFirstPresenceAuthorityError):
                bridge.prepare(
                    raw_request=self.raw(source, scene_change=True),
                    world_id="world-test",
                    branch_id="branch-main",
                    custody=self.custody(source),
                    state_projection=self.projection(),
                )

    def test_absent_mia_mention_and_retrieval_do_not_change_presence(self) -> None:
        with TemporaryDirectory() as temporary:
            store = ContinuousWorldStore(Path(temporary).resolve())
            self.initialize_and_accept(store=store)
            bridge, planner, *_ = self.runtime(store, [sequence()])
            source = "Ted asks Hana whether Mia is upstairs."
            prepared = bridge.prepare(
                raw_request=self.raw(source),
                world_id="world-test",
                branch_id="branch-main",
                custody=self.custody(source),
                state_projection=self.projection(include_mia_evidence=True),
            )
            result = bridge.generate(
                prepared,
                voice_cues=(VoiceCueV1("character:hana", "Warm and direct."),),
            )
            self.assertTrue(result.accepted)
            self.assertEqual(
                planner.inputs[0].accepted_present_character_ids,
                ("character:ted", "character:hana"),
            )
            self.assertNotIn(
                "character:mia",
                planner.inputs[0].accepted_present_character_ids,
            )

    def test_present_mia_can_remain_silent_and_is_derived_background(self) -> None:
        with TemporaryDirectory() as temporary:
            store = ContinuousWorldStore(Path(temporary).resolve())
            self.initialize_and_accept(
                store=store,
                present=("character:ted", "character:hana", "character:mia"),
            )
            plan = sequence()
            bridge, *_ = self.runtime(store, [plan])
            source = "Continue the scene."
            prepared = bridge.prepare(
                raw_request=self.raw(source),
                world_id="world-test",
                branch_id="branch-main",
                custody=self.custody(source),
                state_projection=self.projection(),
            )
            self.assertEqual(
                prepared.request.semantic_input.derived_backgrounded_character_ids(plan),
                ("character:mia",),
            )
            result = bridge.generate(
                prepared,
                voice_cues=(VoiceCueV1("character:hana", "Warm and direct."),),
            )
            self.assertTrue(result.accepted)

    def test_ordered_entry_then_exit_updates_only_after_acceptance(self) -> None:
        with TemporaryDirectory() as temporary:
            store = ContinuousWorldStore(Path(temporary).resolve())
            baseline = self.initialize_and_accept(store=store)
            enter = sequence(
                owner="character:mia",
                kind=ItemKind.ACTION,
                item_key="mia_enters",
                presence_changes=(
                    PresenceChangeV1(
                        "character:mia",
                        PresenceDirection.ENTER,
                        "mia_enters",
                    ),
                ),
            )
            bridge, *_ = self.runtime(store, [enter])
            source = "Mia enters after Ted calls to her."
            prepared = bridge.prepare(
                raw_request=self.raw(source),
                world_id="world-test",
                branch_id="branch-main",
                custody=self.custody(source),
                state_projection=self.projection(),
            )
            result = bridge.generate(
                prepared,
                voice_cues=(VoiceCueV1("character:mia", "Bright and concise."),),
            )
            self.assertEqual(
                baseline.accepted_present_character_ids,
                ("character:ted", "character:hana"),
            )
            entered = bridge.accept_and_reload(prepared, result, creator_accepted=True)
            self.assertEqual(
                entered.accepted_present_character_ids,
                ("character:ted", "character:hana", "character:mia"),
            )

            leave = sequence(
                presence_changes=(
                    PresenceChangeV1(
                        "character:mia",
                        PresenceDirection.LEAVE,
                        "hana_responds",
                    ),
                ),
            )
            bridge, *_ = self.runtime(store, [leave])
            source = "Mia leaves while Hana continues the conversation."
            prepared = bridge.prepare(
                raw_request=self.raw(source),
                world_id="world-test",
                branch_id="branch-main",
                custody=self.custody(source),
                state_projection=self.projection(),
            )
            result = bridge.generate(
                prepared,
                voice_cues=(VoiceCueV1("character:hana", "Warm and direct."),),
            )
            left = bridge.accept_and_reload(prepared, result, creator_accepted=True)
            self.assertEqual(
                left.accepted_present_character_ids,
                ("character:ted", "character:hana"),
            )

    def test_remote_mia_can_respond_without_becoming_physically_present(self) -> None:
        with TemporaryDirectory() as temporary:
            store = ContinuousWorldStore(Path(temporary).resolve())
            self.initialize_and_accept(store=store)
            remote_plan = sequence(
                owner="character:mia",
                kind=ItemKind.REMOTE_COMMUNICATION,
                item_key="mia_phone_reply",
            )
            bridge, *_ = self.runtime(store, [remote_plan])
            source = "Mia replies over the phone."
            prepared = bridge.prepare(
                raw_request=self.raw(source),
                world_id="world-test",
                branch_id="branch-main",
                custody=self.custody(source),
                state_projection=self.projection(remote=("character:mia",)),
            )
            result = bridge.generate(
                prepared,
                voice_cues=(VoiceCueV1("character:mia", "Bright and concise."),),
            )
            head = bridge.accept_and_reload(prepared, result, creator_accepted=True)
            self.assertEqual(
                head.accepted_present_character_ids,
                ("character:ted", "character:hana"),
            )

    def test_ambiguous_presence_causes_no_update(self) -> None:
        with TemporaryDirectory() as temporary:
            store = ContinuousWorldStore(Path(temporary).resolve())
            self.initialize_and_accept(store=store)
            bridge, *_ = self.runtime(store, [sequence()])
            source = "Ted wonders if Mia might be somewhere nearby."
            prepared = bridge.prepare(
                raw_request=self.raw(source),
                world_id="world-test",
                branch_id="branch-main",
                custody=self.custody(source),
                state_projection=self.projection(),
            )
            result = bridge.generate(
                prepared,
                voice_cues=(VoiceCueV1("character:hana", "Warm and direct."),),
            )
            head = bridge.accept_and_reload(prepared, result, creator_accepted=True)
            self.assertEqual(
                head.accepted_present_character_ids,
                ("character:ted", "character:hana"),
            )

    def test_explicit_scene_reinitialization_inherits_no_prior_cast(self) -> None:
        with TemporaryDirectory() as temporary:
            store = ContinuousWorldStore(Path(temporary).resolve())
            self.initialize_and_accept(
                store=store,
                present=("character:ted", "character:hana", "character:mia"),
            )
            bridge, *_ = self.runtime(store, [sequence()])
            source = "Begin the separately authorized kitchen scene."
            prepared = bridge.prepare(
                raw_request=self.raw(source, scene_change=True),
                world_id="world-test",
                branch_id="branch-main",
                custody=self.custody(source),
                state_projection=self.projection(),
                scene_initialization=ExplicitSceneInitializationV1(
                    scene_id="scene-kitchen",
                    accepted_present_character_ids=(
                        "character:ted",
                        "character:hana",
                    ),
                    current_public_scene_state="Ted and Hana are in the kitchen.",
                ),
            )
            self.assertTrue(prepared.request.semantic_input.scene_reinitialization)
            self.assertIsNone(prepared.request.semantic_input.prior_realized_sequence)
            self.assertEqual(
                prepared.request.semantic_input.accepted_present_character_ids,
                ("character:ted", "character:hana"),
            )
            result = bridge.generate(
                prepared,
                voice_cues=(VoiceCueV1("character:hana", "Warm and direct."),),
            )
            head = bridge.accept_and_reload(prepared, result, creator_accepted=True)
            self.assertEqual(head.scene_id, "scene-kitchen")
            self.assertNotIn("character:mia", head.accepted_present_character_ids)

    def test_rejection_preserves_prior_accepted_presence(self) -> None:
        with TemporaryDirectory() as temporary:
            store = ContinuousWorldStore(Path(temporary).resolve())
            before = self.initialize_and_accept(store=store)
            prose = "Hana asks a question that cannot be accepted."
            bridge, *_ = self.runtime(store, [sequence()], reject=True, prose=prose)
            source = "Continue the scene."
            prepared = bridge.prepare(
                raw_request=self.raw(source),
                world_id="world-test",
                branch_id="branch-main",
                custody=self.custody(source),
                state_projection=self.projection(),
            )
            result = bridge.generate(
                prepared,
                voice_cues=(VoiceCueV1("character:hana", "Warm and direct."),),
            )
            self.assertFalse(result.accepted)
            after = SequenceFirstWorldTransaction(store).load_accepted_head(
                world_id="world-test",
                branch_id="branch-main",
            )
            self.assertEqual(after.active_head_sha256, before.active_head_sha256)
            self.assertEqual(
                after.accepted_present_character_ids,
                before.accepted_present_character_ids,
            )

    def test_failed_commit_restart_and_sibling_branch_preserve_presence(self) -> None:
        with TemporaryDirectory() as temporary:
            fail = {"enabled": False}

            def failpoint(stage: str) -> None:
                if fail["enabled"] and stage == "journal_created":
                    raise RuntimeError("simulated pre-swap failure")

            root = Path(temporary).resolve()
            store = ContinuousWorldStore(root, promotion_failpoint=failpoint)
            before = self.initialize_and_accept(
                store=store,
                present=("character:ted", "character:hana", "character:mia"),
            )
            store.materialize_branch_from_checkpoint(
                world_id="world-test",
                parent_branch_id="branch-main",
                child_branch_id="branch-sibling",
                accepted_checkpoint_turn_id=before.accepted_turn_id,
                ordered_accepted_turn_ids=(before.accepted_turn_id,),
                accepted_ancestry_sha256=text_sha256("accepted ancestry"),
                parent_provider_thread_sha256=text_sha256("planner thread"),
                parent_world_directory_identity_sha256=(
                    store.branch_directory_identity_sha256(
                        "world-test",
                        "branch-main",
                    )
                ),
                child_world_directory_identity_sha256=(
                    store.branch_directory_identity_sha256(
                        "world-test",
                        "branch-sibling",
                    )
                ),
                authority_policy_version="authority-v1",
                privacy_policy_version="privacy-v1",
                protected_user_policy_version="protected-user-v1",
                session_policy_version="session-v1",
                persistence_policy_sha256=text_sha256("persistence policy"),
            )
            sibling = SequenceFirstWorldTransaction(store).load_accepted_head(
                world_id="world-test",
                branch_id="branch-sibling",
            )
            leave = sequence(
                presence_changes=(
                    PresenceChangeV1(
                        "character:mia",
                        PresenceDirection.LEAVE,
                        "hana_responds",
                    ),
                ),
            )
            bridge, *_ = self.runtime(store, [leave])
            source = "Mia leaves."
            prepared = bridge.prepare(
                raw_request=self.raw(source),
                world_id="world-test",
                branch_id="branch-main",
                custody=self.custody(source),
                state_projection=self.projection(),
            )
            result = bridge.generate(
                prepared,
                voice_cues=(VoiceCueV1("character:hana", "Warm and direct."),),
            )
            fail["enabled"] = True
            with self.assertRaisesRegex(RuntimeError, "pre-swap failure"):
                bridge.accept_and_reload(prepared, result, creator_accepted=True)

            restarted = SequenceFirstWorldTransaction(ContinuousWorldStore(root))
            main_after = restarted.load_accepted_head(
                world_id="world-test",
                branch_id="branch-main",
            )
            sibling_after = restarted.load_accepted_head(
                world_id="world-test",
                branch_id="branch-sibling",
            )
            self.assertEqual(main_after.active_head_sha256, before.active_head_sha256)
            self.assertEqual(
                main_after.accepted_present_character_ids,
                ("character:ted", "character:hana", "character:mia"),
            )
            self.assertEqual(
                sibling_after.accepted_present_character_ids,
                sibling.accepted_present_character_ids,
            )

    def test_stage6_route_source_contains_no_historical_semantic_helpers(self) -> None:
        source = "\n".join(
            Path(path).read_text(encoding="utf-8")
            for path in (
                "src/cera/sillytavern/sequence_first_stage6.py",
                "src/cera/sillytavern/sequence_first_adapter.py",
            )
        )
        for forbidden in (
            "_candidate_character_ids",
            "current_scene_character_ids",
            "eligible_responder_ids",
            "planner_requested_character_ids",
            "active_cast_ids",
            "default_sakura",
            "re.compile",
            "aliases",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
