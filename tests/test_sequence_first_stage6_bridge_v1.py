from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import replace
import json
import socket
from threading import Thread
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from cera.continuous.world import ContinuousWorldStore
from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import text_sha256
from cera.sequence_first import (
    ConflictClass,
    EvidenceRecordV1,
    ItemKind,
    PresenceChangeV1,
    PresenceDirection,
    ReaderStatus,
    ReaderVerdictV1,
    SequenceDraftV1,
    SequenceFirstCoordinator,
    SequenceFirstAcceptedAuthorityV1,
    StaticAcceptedWorldAuthorityAssembler,
    SequenceItemV1,
    ValidationConflictV1,
    ValidatorDecisionV1,
    ValidatorVerdict,
    Visibility,
    VoiceCueV1,
    WriterResponseV1,
)
from cera.sequence_first.world import SequenceFirstWorldTransaction
from cera.sillytavern.models import CERA_SEQUENCE_FIRST_STAGE6_MODEL
from cera.sillytavern.sequence_first_adapter import SequenceFirstSillyTavernAdapter
from cera.sillytavern.sequence_first_http import (
    SEQUENCE_FIRST_STAGE6_PROFILE,
    sequence_first_stage6_server_config,
    sequence_first_stage6_profile_path,
    SequenceFirstStage6HttpAdapter,
    validate_sequence_first_stage6_profile,
)
from cera.sillytavern.server import CeraSillyTavernServerConfig, build_server
from cera.sillytavern.sequence_first_stage6 import (
    ExplicitSceneInitializationV1,
    SequenceFirstPresenceAuthorityError,
    SequenceFirstStage6Bridge,
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
    external_provider_boundary = False

    def __init__(self, plans: list[SequenceDraftV1]) -> None:
        self.plans = plans
        self.inputs = []

    def plan(self, semantic_input):
        self.inputs.append(semantic_input)
        return self.plans[len(self.inputs) - 1]


class WriterFake:
    external_provider_boundary = False

    def __init__(self, prose: str = "Hana asks Ted what he would like to do next.") -> None:
        self.prose = prose
        self.calls = 0

    def write(self, brief, attempt_number: int, retry_feedback=()) -> WriterResponseV1:
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
    external_provider_boundary = False

    def __init__(self, *, reject: bool = False, quote: str | None = None) -> None:
        self.reject = reject
        self.quote = quote
        self.sessions = []

    def create_sequence_first_validator(self):
        session = ValidatorSessionFake(reject=self.reject, quote=self.quote)
        self.sessions.append(session)
        return session


class RetryingValidatorSessionFake:
    def __init__(self) -> None:
        self.archived = False

    def validate(self, request):
        return ValidatorDecisionV1(
            verdict=ValidatorVerdict.REJECT,
            realized_sequence=None,
            conflict=ValidationConflictV1(
                conflict_class=ConflictClass.MATERIAL_ADDITION,
                concise_explanation="The candidate adds an unauthorized consequence.",
                exact_quote=request.exact_writer_prose,
            ),
        )

    def archive_and_prove_nonresumable(self) -> None:
        self.archived = True


class RetryingValidatorFactoryFake:
    external_provider_boundary = False

    def __init__(self) -> None:
        self.sessions = []

    def create_sequence_first_validator(self):
        session = RetryingValidatorSessionFake()
        self.sessions.append(session)
        return session


class ReaderFake:
    external_provider_boundary = False

    def __init__(self) -> None:
        self.calls = 0

    def read(self, request) -> ReaderVerdictV1:
        self.calls += 1
        return ReaderVerdictV1(ReaderStatus.ACCEPTED)


class SequenceFirstStage6BridgeTests(unittest.TestCase):
    def test_stage6_route_profile_is_explicit_and_non_authorizing(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        profile_path = sequence_first_stage6_profile_path(project_root)
        profile = json.loads(profile_path.read_text(encoding="utf-8"))

        self.assertEqual(validate_sequence_first_stage6_profile(profile), profile)
        self.assertEqual(profile["external_provider_calls_authorized_by_profile"], 0)
        self.assertFalse(profile["production"])
        self.assertTrue(profile["loopback_only"])
        self.assertEqual(profile["validator"]["model"], "gpt-5.6-sol")
        self.assertEqual(profile["reader"]["model"], "gpt-5.6-sol")

    def setUp(self) -> None:
        self._identity = 0

    def raw(self, source: str, *, scene_change: bool = False) -> dict:
        return {
            "model": CERA_SEQUENCE_FIRST_STAGE6_MODEL,
            "messages": [
                {"role": "system", "content": "SillyTavern fixture."},
                {"role": "user", "content": source},
            ],
            "stream": False,
            "cera_scene_change": scene_change,
        }

    @staticmethod
    def _post_json(url: str, payload: dict) -> dict:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as value:
            value.bind(("127.0.0.1", 0))
            return int(value.getsockname()[1])

    @staticmethod
    def _get_json(url: str) -> dict:
        with urlopen(url, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def custody(self, source: str) -> SequenceFirstStage6TurnCustodyV1:
        self._identity += 1
        suffix = f"{self._identity:03d}"
        return SequenceFirstStage6TurnCustodyV1(
            request_id=f"request-{suffix}",
            candidate_id=f"candidate-{suffix}",
            turn_id=f"turn-{suffix}",
            transaction_id=f"transaction-{suffix}",
            current_source_key="source:current",
        )

    def projection(
        self,
        *,
        remote: tuple[str, ...] = (),
        include_mia_evidence: bool = False,
        branch_id: str = "branch-main",
    ) -> SequenceFirstAcceptedAuthorityV1:
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
        return SequenceFirstAcceptedAuthorityV1(
            schema_version=SequenceFirstAcceptedAuthorityV1.SCHEMA_VERSION,
            world_id="world-test",
            branch_id=branch_id,
            known_character_ids=(
                "character:ted",
                "character:hana",
                "character:mia",
            ),
            explicitly_authorized_remote_character_ids=remote,
            evidence_records=tuple(evidence),
            voice_cues=(
                VoiceCueV1("character:hana", "Warm and direct."),
                VoiceCueV1("character:mia", "Bright and concise."),
            ),
            hard_boundaries=(
                "Do not invent Ted speech or dialogue.",
                "Do not invent Ted thoughts or feelings.",
            ),
        )

    def runtime(
        self,
        store: ContinuousWorldStore,
        plans: list[SequenceDraftV1],
        *,
        reject: bool = False,
        prose: str = "Hana asks Ted what he would like to do next.",
        authority: SequenceFirstAcceptedAuthorityV1 | None = None,
        validator_factory=None,
    ):
        planner = PlannerFake(plans)
        writer = WriterFake(prose)
        validator = validator_factory or ValidatorFactoryFake(
            reject=reject,
            quote=prose if reject else None,
        )
        reader = ReaderFake()
        assembler = StaticAcceptedWorldAuthorityAssembler(
            authority or self.projection()
        )
        coordinator = SequenceFirstCoordinator(
            planner=planner,
            writer=writer,
            validator_factory=validator,
            reader=reader,
            voice_cue_resolver=assembler,
        )
        transaction = SequenceFirstWorldTransaction(store)
        bridge = SequenceFirstStage6Bridge(
            adapter=SequenceFirstSillyTavernAdapter(coordinator),
            transaction=transaction,
            authority_assembler=assembler,
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
        bridge, *_ = self.runtime(
            store,
            [sequence()],
            authority=self.projection(branch_id=branch_id),
        )
        prepared = bridge.prepare(
            raw_request=self.raw(source),
            world_id="world-test",
            branch_id=branch_id,
            custody=self.custody(source),
            scene_initialization=ExplicitSceneInitializationV1(
                scene_id=scene_id,
                accepted_present_character_ids=present,
                current_public_scene_state="The explicitly initialized cast is present.",
                unresolved_threads=("Ted retains the next choice.",),
            ),
        )
        result = bridge.generate(prepared)
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
                )

    def test_absent_mia_mention_and_retrieval_do_not_change_presence(self) -> None:
        with TemporaryDirectory() as temporary:
            store = ContinuousWorldStore(Path(temporary).resolve())
            self.initialize_and_accept(store=store)
            bridge, planner, *_ = self.runtime(
                store,
                [sequence()],
                authority=self.projection(include_mia_evidence=True),
            )
            source = "Ted asks Hana whether Mia is upstairs."
            prepared = bridge.prepare(
                raw_request=self.raw(source),
                world_id="world-test",
                branch_id="branch-main",
                custody=self.custody(source),
            )
            result = bridge.generate(prepared)
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
            )
            self.assertEqual(prepared.ingress.protected_source_claims, ())
            self.assertEqual(
                prepared.request.semantic_input.derived_backgrounded_character_ids(plan),
                ("character:mia",),
            )
            result = bridge.generate(prepared)
            self.assertTrue(result.accepted)

    def test_failed_provisional_plan_cannot_override_next_accepted_packet(self) -> None:
        with TemporaryDirectory() as temporary:
            store = ContinuousWorldStore(Path(temporary).resolve())
            accepted = self.initialize_and_accept(store=store)
            invalid = sequence(owner="character:mia")
            valid = sequence(owner="character:hana")
            bridge, planner, *_ = self.runtime(store, [invalid, valid])
            first_source = "Continue while Mia remains absent."
            first = bridge.prepare(
                raw_request=self.raw(first_source),
                world_id="world-test",
                branch_id="branch-main",
                custody=self.custody(first_source),
            )
            with self.assertRaisesRegex(ContractValidationError, "absent item owner"):
                bridge.generate(first)
            second_source = "Continue the scene."
            second = bridge.prepare(
                raw_request=self.raw(second_source),
                world_id="world-test",
                branch_id="branch-main",
                custody=self.custody(second_source),
            )
            result = bridge.generate(second)
            self.assertTrue(result.accepted)
            self.assertEqual(
                planner.inputs[1].prior_realized_sequence,
                accepted.prior_realized_sequence,
            )
            self.assertEqual(
                planner.inputs[1].accepted_present_character_ids,
                accepted.accepted_present_character_ids,
            )

    def test_actual_loopback_http_review_accept_is_atomic(self) -> None:
        with TemporaryDirectory() as temporary:
            store = ContinuousWorldStore(Path(temporary).resolve())
            bridge, *_ = self.runtime(store, [sequence()])
            adapter = SequenceFirstStage6HttpAdapter(
                bridge=bridge,
                world_id="world-test",
                branch_id="branch-main",
                session_id="sequence-test",
                initial_scene=ExplicitSceneInitializationV1(
                    scene_id="scene-entry",
                    accepted_present_character_ids=("character:ted", "character:hana"),
                    current_public_scene_state="Ted and Hana are in the entry room.",
                ),
            )
            port = self._free_port()
            server = build_server(
                adapter,
                CeraSillyTavernServerConfig(
                    host="127.0.0.1",
                    port=port,
                    model=CERA_SEQUENCE_FIRST_STAGE6_MODEL,
                    service="sequence-first-stage6-test",
                ),
            )
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                health = self._get_json(f"http://127.0.0.1:{port}/health")
                self.assertEqual(health["reasoner_session"]["mode"], "sequence_first_stage6")
                self.assertEqual(
                    health["active_runtime"]["profile_id"],
                    SEQUENCE_FIRST_STAGE6_PROFILE,
                )
                models = self._get_json(f"http://127.0.0.1:{port}/v1/models")
                self.assertEqual(models["data"][0]["id"], CERA_SEQUENCE_FIRST_STAGE6_MODEL)
                active = store.initialize("world-test", "branch-main") / "ACTIVE"
                before = store.tree_sha256(active)
                endpoint = f"http://127.0.0.1:{port}/v1/chat/completions"
                payload = {
                    "model": CERA_SEQUENCE_FIRST_STAGE6_MODEL,
                    "messages": [{"role": "user", "content": "Continue the scene."}],
                    "stream": False,
                    "cera_session_id": "sequence-test",
                    "cera_profile_id": SEQUENCE_FIRST_STAGE6_PROFILE,
                }
                for field_name, invalid_value in (
                    ("model", "cera-alpha"),
                    ("cera_profile_id", "cera.sequence_first.stage6.other"),
                    ("cera_session_id", "sequence-other"),
                ):
                    invalid = dict(payload)
                    invalid[field_name] = invalid_value
                    with self.assertRaises(HTTPError) as failure:
                        self._post_json(endpoint, invalid)
                    self.assertEqual(failure.exception.code, 400)
                    self.assertEqual(store.tree_sha256(active), before)
                response = self._post_json(endpoint, payload)
                review_id = response["cera"]["provisional_review_id"]
                self.assertEqual(store.tree_sha256(active), before)
                unresolved = dict(payload)
                unresolved["messages"] = [
                    {"role": "user", "content": "A second unresolved request."}
                ]
                with self.assertRaises(HTTPError) as failure:
                    self._post_json(endpoint, unresolved)
                self.assertEqual(failure.exception.code, 400)
                self.assertEqual(store.tree_sha256(active), before)
                review = self._get_json(
                    f"http://127.0.0.1:{port}/v1/cera/reviews/{review_id}"
                )
                evidence = review["operation_evidence"]
                self.assertEqual(evidence["virtual_model"], CERA_SEQUENCE_FIRST_STAGE6_MODEL)
                self.assertEqual(evidence["protected_source_claim_count"], 0)
                self.assertEqual(evidence["provider_calls"], 0)
                self.assertEqual(len(evidence["writer_prose_sha256"]), 64)
                decision = self._post_json(
                    f"http://127.0.0.1:{port}/v1/cera/reviews/{review_id}/decision",
                    {"action": "accept"},
                )
                self.assertEqual(decision["status"], "accepted")
                self.assertNotEqual(store.tree_sha256(active), before)
                self.assertEqual(
                    store.initialize("world-test", "branch-main").joinpath(
                        "ACTIVE", "WORLD_STATE.json"
                    ).is_file(),
                    True,
                )
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)

    def test_sequence_first_launcher_config_is_explicit_loopback_5116(self) -> None:
        config = sequence_first_stage6_server_config()
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 5116)
        self.assertEqual(config.model, CERA_SEQUENCE_FIRST_STAGE6_MODEL)
        with self.assertRaisesRegex(ValueError, "loopback-only"):
            CeraSillyTavernServerConfig(
                host="0.0.0.0",
                port=5116,
                model=CERA_SEQUENCE_FIRST_STAGE6_MODEL,
            )

    def test_actual_loopback_http_decline_has_no_world_mutation(self) -> None:
        with TemporaryDirectory() as temporary:
            store = ContinuousWorldStore(Path(temporary).resolve())
            bridge, *_ = self.runtime(store, [sequence()])
            adapter = SequenceFirstStage6HttpAdapter(
                bridge=bridge,
                world_id="world-test",
                branch_id="branch-main",
                session_id="sequence-decline",
                initial_scene=ExplicitSceneInitializationV1(
                    scene_id="scene-entry",
                    accepted_present_character_ids=("character:ted", "character:hana"),
                    current_public_scene_state="Ted and Hana are in the entry room.",
                ),
            )
            port = self._free_port()
            server = build_server(
                adapter,
                CeraSillyTavernServerConfig(
                    host="127.0.0.1",
                    port=port,
                    model=CERA_SEQUENCE_FIRST_STAGE6_MODEL,
                    service="sequence-first-stage6-decline-test",
                ),
            )
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                active = store.initialize("world-test", "branch-main") / "ACTIVE"
                before = store.tree_sha256(active)
                response = self._post_json(
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    {
                        "model": CERA_SEQUENCE_FIRST_STAGE6_MODEL,
                        "messages": [{"role": "user", "content": "Continue the scene."}],
                        "stream": False,
                        "cera_session_id": "sequence-decline",
                        "cera_profile_id": SEQUENCE_FIRST_STAGE6_PROFILE,
                    },
                )
                review_id = response["cera"]["provisional_review_id"]
                decision = self._post_json(
                    f"http://127.0.0.1:{port}/v1/cera/reviews/{review_id}/decision",
                    {"action": "decline"},
                )
                self.assertEqual(decision["status"], "rejected")
                self.assertEqual(store.tree_sha256(active), before)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)

    def test_exhausted_http_run_retains_restart_readable_planned_terminal_only(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            store = ContinuousWorldStore(root)
            validator = RetryingValidatorFactoryFake()
            bridge, _, writer, _, reader = self.runtime(
                store,
                [sequence()],
                prose="The candidate adds an unauthorized consequence.",
                validator_factory=validator,
            )
            adapter = SequenceFirstStage6HttpAdapter(
                bridge=bridge,
                world_id="world-test",
                branch_id="branch-main",
                session_id="sequence-exhausted",
                initial_scene=ExplicitSceneInitializationV1(
                    scene_id="scene-entry",
                    accepted_present_character_ids=("character:ted", "character:hana"),
                    current_public_scene_state="Ted and Hana are in the entry room.",
                ),
            )
            port = self._free_port()
            server = build_server(
                adapter,
                CeraSillyTavernServerConfig(
                    host="127.0.0.1",
                    port=port,
                    model=CERA_SEQUENCE_FIRST_STAGE6_MODEL,
                    service="sequence-first-stage6-exhausted-test",
                ),
            )
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            source = "Continue the scene."
            try:
                branch_root = store.initialize("world-test", "branch-main")
                active = branch_root / "ACTIVE"
                active_before = store.tree_sha256(active)
                with self.assertRaises(HTTPError) as failure:
                    self._post_json(
                        f"http://127.0.0.1:{port}/v1/chat/completions",
                        {
                            "model": CERA_SEQUENCE_FIRST_STAGE6_MODEL,
                            "messages": [{"role": "user", "content": source}],
                            "stream": False,
                            "cera_session_id": "sequence-exhausted",
                            "cera_profile_id": SEQUENCE_FIRST_STAGE6_PROFILE,
                        },
                    )
                self.assertEqual(failure.exception.code, 400)
                self.assertEqual(writer.calls, 3)
                self.assertEqual(len(validator.sessions), 3)
                self.assertTrue(all(value.archived for value in validator.sessions))
                self.assertEqual(reader.calls, 0)
                self.assertEqual(store.tree_sha256(active), active_before)
                self.assertFalse(adapter.reasoner_session_status["creator_review_unresolved"])

                turn_id = f"turn-0001-{text_sha256(source)[:12]}"
                restarted = SequenceFirstWorldTransaction(
                    ContinuousWorldStore(root)
                )
                artifact = restarted.load_planned_terminal(
                    world_id="world-test",
                    branch_id="branch-main",
                    turn_id=turn_id,
                )
                self.assertEqual(artifact.primary_sequence_status.value, "planned")
                self.assertEqual(artifact.request.custody.turn_id, turn_id)
                self.assertEqual(
                    artifact.request.custody.exact_source_sha256,
                    text_sha256(source),
                )
                self.assertEqual(len(artifact.attempt_receipts), 3)
                self.assertIsNotNone(artifact.terminal_validator_decision)
                self.assertIsNone(artifact.terminal_reader_verdict)
                self.assertEqual(artifact.applied_story_effect_ids, ())
                self.assertEqual(artifact.applied_presence_effect_ids, ())
                self.assertEqual(artifact.applied_durable_effect_ids, ())
                self.assertFalse(artifact.promotion_receipt_created)
                self.assertFalse(artifact.unresolved_creator_review_created)
                self.assertEqual(tuple((active / "Events").glob("*.json")), ())
                self.assertFalse(
                    (
                        branch_root
                        / "CANDIDATES"
                        / turn_id
                        / "PROMOTION_RECEIPT.json"
                    ).exists()
                )

                # Identical bytes are idempotent; conflicting replay fails closed.
                restarted.write_planned_terminal(artifact)
                with self.assertRaisesRegex(
                    StateConflictError,
                    "planned terminal artifact changed during acceptance recovery",
                ):
                    restarted.write_planned_terminal(
                        replace(artifact, provider_calls=artifact.provider_calls + 1)
                    )
                self.assertEqual(store.tree_sha256(active), active_before)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)

    def test_actual_loopback_http_restart_reloads_accepted_lineage(self) -> None:
        with TemporaryDirectory() as temporary:
            store = ContinuousWorldStore(Path(temporary).resolve())
            self.initialize_and_accept(store=store)
            bridge, *_ = self.runtime(store, [sequence()])
            adapter = SequenceFirstStage6HttpAdapter(
                bridge=bridge,
                world_id="world-test",
                branch_id="branch-main",
                session_id="sequence-restart",
            )
            port = self._free_port()
            server = build_server(
                adapter,
                CeraSillyTavernServerConfig(
                    host="127.0.0.1",
                    port=port,
                    model=CERA_SEQUENCE_FIRST_STAGE6_MODEL,
                    service="sequence-first-stage6-restart-test",
                ),
            )
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                response = self._post_json(
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    {
                        "model": CERA_SEQUENCE_FIRST_STAGE6_MODEL,
                        "messages": [{"role": "user", "content": "Continue the scene."}],
                        "stream": False,
                        "cera_session_id": "sequence-restart",
                        "cera_profile_id": SEQUENCE_FIRST_STAGE6_PROFILE,
                    },
                )
                self.assertEqual(response["cera"]["generation"], 2)
                decision = self._post_json(
                    "http://127.0.0.1:"
                    f"{port}/v1/cera/reviews/"
                    f"{response['cera']['provisional_review_id']}/decision",
                    {"action": "accept"},
                )
                self.assertEqual(decision["generation"], 2)
                self.assertEqual(
                    len(
                        json.loads(
                            (
                                store.initialize("world-test", "branch-main")
                                / "ACTIVE"
                                / "WORLD_STATE.json"
                            ).read_text(encoding="utf-8")
                        )["accepted_turn_ids"]
                    ),
                    2,
                )
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)

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
            )
            result = bridge.generate(prepared)
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
            )
            result = bridge.generate(prepared)
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
            bridge, *_ = self.runtime(
                store,
                [remote_plan],
                authority=self.projection(remote=("character:mia",)),
            )
            source = "Mia replies over the phone."
            prepared = bridge.prepare(
                raw_request=self.raw(source),
                world_id="world-test",
                branch_id="branch-main",
                custody=self.custody(source),
            )
            result = bridge.generate(prepared)
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
            )
            result = bridge.generate(prepared)
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
            result = bridge.generate(prepared)
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
            )
            result = bridge.generate(prepared)
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
            )
            result = bridge.generate(prepared)
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
