from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from cera.continuous.call_ledger import (
    ContinuousProviderCallLedger,
    ProviderCallState,
)
from cera.continuous.diagnostics import ContinuousRootDiagnosticRecorder
from cera.continuous.evidence import (
    EvidenceVisibility,
    RequestEvidenceBindingRegistry,
    build_character_summary_envelope,
    validate_character_summary_envelope,
)
from cera.continuous.sessions import ContinuousSessionRole
from cera.continuous.sessions import (
    ContinuousSessionCoordinator,
    InMemoryContinuousStoredSessionPort,
)
from cera.continuous.runtime import (
    ContinuousShadowTurnCoordinator,
    ContinuousTurnRequestV1,
)
from cera.continuous.world import (
    ContinuousWorldStore,
    SceneChangeCoordinator,
    redact_secrets,
)
from cera.continuous.world_mcp import ContinuousWorldToolDispatcher
from cera.creator_review.models import CreatorReviewAction, CreatorReviewSeverity
from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_bytes, canonical_sha256, text_sha256, to_primitive

from scripts.run_continuous_planner_validator_job4 import compatibility
from tests.test_continuous_world import (
    AcceptedTurnPairV1,
    accepted_pair,
    character_summary,
    concern_assessment,
    final_sequence,
    package,
    rich_sequence,
    scene_summary_package,
    session_compatibility,
)
from cera.continuous.contracts import (
    CharacterSummaryEnvelopeV1,
    ProtectedUserAllowanceMode,
    ProtectedUserAllowanceV1,
    SceneSummaryV1,
    ValidatorSemanticStatus,
    WorldEditOperationKind,
    WorldEditOperationV1,
)
from cera.registry import build_schema_registry
from cera.continuous.provider import (
    CodexContinuousPlannerPort,
    CodexContinuousValidatorPort,
    DeepSeekContinuousComposerPort,
)


def _seed_character(store: ContinuousWorldStore, world: str = "world-test", branch: str = "main") -> Path:
    root = store.initialize(world, branch)
    path = root / "ACTIVE" / "Characters" / "Sakura.json"
    path.write_bytes(
        canonical_bytes(
            {
                "schema_version": "cera.continuous_character.v1",
                "_cera_revision": 1,
                "character_id": "character:sakura_hanezawa",
                "visibility": "character_private",
                "knowledge_owner_id": "character:sakura_hanezawa",
                "reasoning_summary": "Sakura is guarded at an unfamiliar arrival and retains threshold control.",
                "latest_accepted_changes": [],
                "turn_claims": {},
            }
        )
        + b"\n"
    )
    return root


def _summary(pair: AcceptedTurnPairV1, text: str = "Sakura closed the arrival scene.") -> SceneSummaryV1:
    return SceneSummaryV1(
        schema_version=SceneSummaryV1.SCHEMA_VERSION,
        summary_id="summary:arrival",
        completed_scene_id="scene:arrival",
        accepted_turn_ids=(pair.accepted_turn_id,),
        shortest_complete_summary=text,
        last_five_exact_pairs=(pair,),
        ending_state="The accepted arrival scene ended.",
        transition_context="A new scene may now begin.",
    )


class _QueueStage:
    def __init__(self, *values) -> None:
        self.values = list(values)

    def _next(self, prompt: str):
        value = self.values.pop(0)
        if hasattr(value, "beats"):
            import re

            keys = tuple(
                dict.fromkeys(
                    re.findall(r'"binding_key":"(binding_[a-z0-9_]+)"', prompt)
                )
            )
            value = replace(
                value,
                beats=tuple(
                    replace(beat, source_evidence_bindings=keys)
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

    def plan(self, prompt: str):
        return self._next(prompt)

    def compose(self, prompt: str):
        return self._next(prompt)

    def validate(self, prompt: str, **_kwargs):
        return self._next(prompt)


class ContinuousEvidenceCorrectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ContinuousWorldStore(Path(self.temp.name).resolve() / "worlds")
        self.root = _seed_character(self.store)

    def _registry_and_sequence(self):
        registry = RequestEvidenceBindingRegistry(
            world_id="world-test", branch_id="main", turn_id="turn-001"
        )
        current = registry.allocate_current_source(
            source_identity="current_user_source:turn-001",
            source_text="Hello, my name is Ted.",
            protected_user_allowance_scope="exact source only",
        )
        dispatcher = ContinuousWorldToolDispatcher(
            self.root,
            ContinuousSessionRole.PLANNER,
            current_turn_id="turn-001",
            evidence_registry=registry,
        )
        read = dispatcher.invoke(
            "cera_world_read", {"path": "ACTIVE/Characters/Sakura.json"}
        )
        record_key = read["evidence_binding"]["binding_key"]
        sequence = replace(
            rich_sequence(),
            beats=(
                replace(
                    rich_sequence().beats[0],
                    source_evidence_bindings=(current.binding_key, record_key),
                ),
            ),
        )
        return registry, sequence, record_key

    def test_exact_read_allocates_authoritative_request_local_binding(self) -> None:
        registry, sequence, _record_key = self._registry_and_sequence()
        registry.validate_sequence(sequence, branch_root=self.root)
        self.assertEqual(len(registry.bindings), 2)
        record = next(value for value in registry.bindings if value.relative_path is not None)
        self.assertEqual(record.record_revision, 1)
        self.assertEqual(record.knowledge_owner_id, "character:sakura_hanezawa")
        self.assertEqual(record.visibility, EvidenceVisibility.CHARACTER_PRIVATE)

    def test_character_record_without_legacy_visibility_defaults_to_its_owner(self) -> None:
        character = self.root / "ACTIVE" / "Characters" / "Sakura.json"
        payload = json.loads(character.read_text(encoding="utf-8"))
        payload.pop("visibility")
        payload.pop("knowledge_owner_id")
        payload["character_id"] = "character:sakura_hanezawa"
        character.write_bytes(canonical_bytes(payload) + b"\n")
        registry = RequestEvidenceBindingRegistry(
            world_id="world-test", branch_id="main", turn_id="turn-001"
        )
        dispatcher = ContinuousWorldToolDispatcher(
            self.root,
            ContinuousSessionRole.PLANNER,
            current_turn_id="turn-001",
            evidence_registry=registry,
        )
        result = dispatcher.invoke(
            "cera_world_read", {"path": "ACTIVE/Characters/Sakura.json"}
        )
        self.assertEqual(
            result["evidence_binding"]["visibility"], "character_private"
        )
        self.assertEqual(
            result["evidence_binding"]["knowledge_owner_id"],
            "character:sakura_hanezawa",
        )

    def test_invented_and_stale_bindings_fail(self) -> None:
        registry, sequence, _record_key = self._registry_and_sequence()
        invented = replace(
            sequence,
            beats=(
                replace(
                    sequence.beats[0],
                    source_evidence_bindings=("binding_record_invented",),
                ),
            ),
        )
        with self.assertRaisesRegex(StateConflictError, "unallocated"):
            registry.validate_sequence(invented, branch_root=self.root)
        character = self.root / "ACTIVE" / "Characters" / "Sakura.json"
        payload = json.loads(character.read_text(encoding="utf-8"))
        payload["_cera_revision"] = 2
        character.write_bytes(canonical_bytes(payload) + b"\n")
        with self.assertRaisesRegex(StateConflictError, "stale"):
            registry.validate_sequence(sequence, branch_root=self.root)

    def test_sibling_binding_and_private_knowledge_transfer_fail(self) -> None:
        sibling = RequestEvidenceBindingRegistry(
            world_id="world-test", branch_id="sibling", turn_id="turn-001"
        )
        sibling_source = sibling.allocate_current_source(
            source_identity="current_user_source:turn-001",
            source_text="Hello.",
            protected_user_allowance_scope="exact source only",
        )
        main = RequestEvidenceBindingRegistry(
            world_id="world-test", branch_id="main", turn_id="turn-001"
        )
        with self.assertRaisesRegex(StateConflictError, "another request"):
            main.import_bindings((sibling_source,))

        registry, sequence, _record_key = self._registry_and_sequence()
        transferred = replace(
            sequence,
            selected_character_ids=("character:mia_hanezawa",),
            beats=(
                replace(
                    sequence.beats[0],
                    actor_ids=("character:mia_hanezawa",),
                ),
            ),
        )
        with self.assertRaisesRegex(PermissionError, "private evidence"):
            registry.validate_sequence(transferred, branch_root=self.root)

    def test_final_sequence_and_edits_retain_beat_traceability(self) -> None:
        registry, sequence, _record_key = self._registry_and_sequence()
        registry.validate_sequence(sequence, branch_root=self.root)
        registry.validate_traceability(sequence, package())
        broken = replace(
            package(),
            complete_final_sequence=replace(
                final_sequence(),
                items=(
                    replace(
                        final_sequence().items[0], planner_beat_keys=("missing_beat",)
                    ),
                ),
            ),
        )
        with self.assertRaisesRegex(StateConflictError, "unknown Planner beat"):
            registry.validate_traceability(sequence, broken)


class ContinuousProviderFreeIntegrationTests(unittest.TestCase):
    def test_three_turn_two_scene_flow_keeps_separate_sessions_and_appends_once(self) -> None:
        with TemporaryDirectory() as directory:
            store = ContinuousWorldStore(Path(directory).resolve() / "worlds")
            root = _seed_character(store)
            port = InMemoryContinuousStoredSessionPort()
            planner_session = ContinuousSessionCoordinator(
                session_compatibility(ContinuousSessionRole.PLANNER), port
            )
            validator_session = ContinuousSessionCoordinator(
                session_compatibility(ContinuousSessionRole.VALIDATOR), port
            )
            first_pair = AcceptedTurnPairV1(
                accepted_turn_id="turn-001",
                user_message="Hello, my name is Ted.",
                complete_final_sequence=final_sequence("turn-001"),
            )
            second_pair = AcceptedTurnPairV1(
                accepted_turn_id="turn-002",
                user_message="I am the expected tenant.",
                complete_final_sequence=final_sequence("turn-002"),
            )
            new_prompt = "Several days later, Ted asks about the threshold check."
            planner = _QueueStage(
                rich_sequence(),
                replace(rich_sequence(), sequence_id="sequence:turn_002"),
                replace(
                    rich_sequence(),
                    sequence_id="sequence:turn_003",
                    scene_id="scene-002",
                ),
            )
            composer = _QueueStage(
                SimpleNamespace(story_text="Sakura requests proof."),
                SimpleNamespace(story_text="Sakura acknowledges Ted's answer."),
                SimpleNamespace(story_text="Sakura answers in the later scene."),
            )
            summary_package = scene_summary_package(first_pair, new_prompt=new_prompt)
            summary_package = replace(
                summary_package,
                optional_scene_summary=replace(
                    summary_package.optional_scene_summary,
                    accepted_turn_ids=("turn-001", "turn-002"),
                    last_five_exact_pairs=(first_pair, second_pair),
                ),
            )
            validator = _QueueStage(
                package(turn_id="turn-001", revision=1),
                package(turn_id="turn-002", revision=2),
                summary_package,
                package(turn_id="turn-003", revision=3),
            )
            coordinator = ContinuousShadowTurnCoordinator(
                world=store,
                planner_session=planner_session,
                validator_session=validator_session,
                planner=planner,
                composer=composer,
                validator=validator,
            )
            planner_thread = planner_session.ensure_session().provider_thread_id
            validator_thread = validator_session.ensure_session().provider_thread_id
            self.assertNotEqual(planner_thread, validator_thread)

            for turn_id, message in (
                ("turn-001", "Hello, my name is Ted."),
                ("turn-002", "I am the expected tenant."),
            ):
                character_path = store.branch_root("world-test", "main") / "ACTIVE" / "Characters" / "Sakura.json"
                character_record = json.loads(character_path.read_text(encoding="utf-8"))
                candidate = coordinator.prepare(
                    ContinuousTurnRequestV1(
                        world_id="world-test",
                        branch_id="main",
                        scene_id="scene-001",
                        turn_id=turn_id,
                        user_message=message,
                        current_authority_packet={"protected_user_id": "character:ted"},
                        character_summaries=(character_summary(
                            source_sha256=text_sha256(character_path.read_text(encoding="utf-8")),
                            source_revision=character_record["_cera_revision"],
                        ),),
                    )
                )
                self.assertEqual(candidate.provider_calls, 0)
                coordinator.apply_creator_action(turn_id, CreatorReviewAction.ACCEPT)

            character_path = store.branch_root("world-test", "main") / "ACTIVE" / "Characters" / "Sakura.json"
            character_record = json.loads(character_path.read_text(encoding="utf-8"))
            changed = coordinator.prepare_scene_change(
                ContinuousTurnRequestV1(
                    world_id="world-test",
                    branch_id="main",
                    scene_id="scene-002",
                    turn_id="turn-003",
                    user_message=new_prompt,
                    current_authority_packet={"protected_user_id": "character:ted"},
                    character_summaries=(character_summary(
                        source_sha256=text_sha256(character_path.read_text(encoding="utf-8")),
                        source_revision=character_record["_cera_revision"],
                    ),),
                    cera_scene_change=True,
                ),
                completed_scene_id="scene:arrival",
                accepted_turn_ids=("turn-001", "turn-002"),
            )
            self.assertEqual(changed.turn_candidate.provider_calls, 0)
            coordinator.apply_creator_action("turn-003", CreatorReviewAction.ACCEPT)

            self.assertEqual(
                planner_session.snapshot().accepted_turn_ids,
                ("turn-001", "turn-002", "turn-003"),
            )
            self.assertEqual(planner_session.unsynchronized_accepted_turn_ids, ())
            self.assertEqual(len(port.model_visible_context[planner_thread]), 3)
            self.assertEqual(
                planner_session.ensure_session().provider_thread_id, planner_thread
            )
            self.assertEqual(
                validator_session.ensure_session().provider_thread_id, validator_thread
            )
            pairs = store.accepted_turn_pairs(
                "world-test", "main", ("turn-001", "turn-002", "turn-003")
            )
            self.assertEqual(len(pairs), 3)
            summary = changed.scene_change_envelope.previous_scene_summary
            self.assertNotIn(new_prompt, summary.shortest_complete_summary)
            derived = root / "DERIVED" / "Scenes"
            self.assertEqual(len(tuple(derived.glob("*.summary.json"))), 1)


class ContinuousCallAccountingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = ContinuousProviderCallLedger(
            Path(self.temp.name).resolve() / "provider_calls.jsonl"
        )

    def test_post_dispatch_failure_matrix_is_conservatively_counted(self) -> None:
        operations = (
            "planner_decoding",
            "planner_domain_validation",
            "planner_world_mcp_reconciliation",
            "deepseek_decoding",
            "validator_decoding",
            "validator_domain_validation",
            "scene_summary_decoding",
            "scene_summary_domain_validation",
        )
        for operation in operations:
            with self.assertRaises(ContractValidationError):
                self.ledger.execute(
                    owner=operation.split("_", 1)[0],
                    operation=operation,
                    route="test-route",
                    model="fake-model",
                    effort=None,
                    stored_thread_sha256="a" * 64,
                    dispatch=lambda: SimpleNamespace(
                        receipt={"external_provider_calls": 1},
                        operation_telemetry={"stage": "returned"},
                    ),
                    finalize=lambda _raw: (_ for _ in ()).throw(
                        ContractValidationError("simulated post-dispatch failure")
                    ),
                )
        self.assertEqual(self.ledger.dispatched_call_count, len(operations))
        terminal = [
            value
            for value in self.ledger.events
            if value["state"] == ProviderCallState.POST_VALIDATION_FAILED.value
        ]
        self.assertEqual(len(terminal), len(operations))
        self.assertTrue(all(value["provider_receipt_sha256"] for value in terminal))

    def test_transport_failure_after_dispatch_counts_once(self) -> None:
        with self.assertRaises(RuntimeError):
            self.ledger.execute(
                owner="planner",
                operation="transport",
                route="test-route",
                model="fake-model",
                effort="medium",
                stored_thread_sha256="b" * 64,
                dispatch=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
                finalize=lambda raw: raw,
            )
        self.assertEqual(self.ledger.dispatched_call_count, 1)
        self.assertEqual(
            self.ledger.events[-1]["state"], ProviderCallState.PROVIDER_FAILED.value
        )

    def test_pretransport_failure_counts_zero_and_stored_thread_is_bound(self) -> None:
        with self.assertRaises(ContractValidationError):
            self.ledger.execute(
                owner="planner",
                operation="preflight",
                route="test-route",
                model="fake-model",
                effort="medium",
                stored_thread_sha256="c" * 64,
                dispatch=lambda: (_ for _ in ()).throw(
                    ContractValidationError("local schema failure")
                ),
                finalize=lambda raw: raw,
            )
        self.assertEqual(self.ledger.dispatched_call_count, 0)
        self.assertEqual(
            self.ledger.events[-1]["state"],
            ProviderCallState.PRETRANSPORT_FAILED.value,
        )
        self.assertTrue(
            all(value["stored_thread_sha256"] == "c" * 64 for value in self.ledger.events)
        )

    def test_planner_adapter_precomputes_mcp_before_ledger_and_binds_thread(self) -> None:
        route = SimpleNamespace(
            model_name="gpt-5.6-sol",
            route_id="fake-sol",
            reasoning_effort="medium",
        )

        class Transport:
            def __init__(self) -> None:
                self.route = route
                self.runner = SimpleNamespace(provider_thread_id="stored-planner-1")

            def invoke(self, *_args, **_kwargs):
                return SimpleNamespace(
                    parsed_json={}, receipt={"calls": 1}, operation_telemetry=None
                )

        class BrokenBridge:
            @property
            def runtime_binding(self):
                raise ContractValidationError("MCP binding is invalid")

        port = CodexContinuousPlannerPort(
            Transport(), world_bridge=BrokenBridge(), call_ledger=self.ledger
        )
        with self.assertRaisesRegex(ContractValidationError, "MCP binding"):
            port.plan("Plan this turn.")
        self.assertEqual(self.ledger.dispatched_call_count, 0)
        self.assertEqual(self.ledger.events, ())

        port = CodexContinuousPlannerPort(Transport(), call_ledger=self.ledger)
        with self.assertRaises(ContractValidationError):
            port.plan("Plan this turn.")
        self.assertEqual(self.ledger.dispatched_call_count, 1)
        self.assertTrue(
            all(
                value["stored_thread_sha256"] == text_sha256("stored-planner-1")
                for value in self.ledger.events
            )
        )

    def test_validator_adapter_postinvocation_decode_failure_counts_one(self) -> None:
        route = SimpleNamespace(
            model_name="gpt-5.6-terra",
            route_id="fake-terra",
            reasoning_effort="high",
        )

        class Transport:
            def __init__(self) -> None:
                self.route = route
                self.runner = SimpleNamespace(provider_thread_id="stored-validator-1")

            def invoke(self, *_args, **_kwargs):
                return SimpleNamespace(
                    parsed_json={},
                    receipt={"calls": 1},
                    operation_telemetry={"returned": True},
                    tool_call_count=0,
                    failed_tool_call_count=0,
                )

        port = CodexContinuousValidatorPort(Transport(), call_ledger=self.ledger)
        with self.assertRaises(ContractValidationError):
            port.validate("Validate this turn.")
        self.assertEqual(self.ledger.dispatched_call_count, 1)
        self.assertEqual(
            self.ledger.events[-1]["state"],
            ProviderCallState.POST_VALIDATION_FAILED.value,
        )
        self.assertTrue(
            all(
                value["stored_thread_sha256"] == text_sha256("stored-validator-1")
                for value in self.ledger.events
            )
        )

    def test_deepseek_adapter_postinvocation_decode_failure_counts_one(self) -> None:
        route = SimpleNamespace(
            model_name="deepseek-v4-flash",
            route_id="fake-deepseek",
            reasoning_effort=None,
        )

        class Transport:
            def __init__(self) -> None:
                self.route = route

            def invoke(self, *_args, **_kwargs):
                return SimpleNamespace(parsed_json={}, receipt={"calls": 1})

        port = DeepSeekContinuousComposerPort(Transport(), call_ledger=self.ledger)
        with self.assertRaises(ContractValidationError):
            port.compose("Compose this turn.")
        self.assertEqual(self.ledger.dispatched_call_count, 1)
        self.assertEqual(
            self.ledger.events[-1]["state"],
            ProviderCallState.POST_VALIDATION_FAILED.value,
        )

    def test_planner_mcp_finalization_failure_is_postinvocation(self) -> None:
        route = SimpleNamespace(
            model_name="gpt-5.6-sol",
            route_id="fake-sol",
            reasoning_effort="medium",
        )

        class Transport:
            def __init__(self) -> None:
                self.route = route
                self.runner = SimpleNamespace(provider_thread_id="stored-planner-mcp")

            def invoke(self, *_args, **_kwargs):
                return SimpleNamespace(
                    parsed_json=to_primitive(rich_sequence()),
                    receipt={"calls": 1},
                    operation_telemetry={"returned": True},
                    tool_call_count=1,
                    failed_tool_call_count=0,
                    tool_names=("cera_world_read",),
                    tool_server_names=("cera_continuous_world_v1",),
                )

        class Bridge:
            runtime_binding = None

            def finalize(self, _result):
                raise StateConflictError("MCP reconciliation failed")

        port = CodexContinuousPlannerPort(
            Transport(), world_bridge=Bridge(), call_ledger=self.ledger
        )
        with self.assertRaisesRegex(StateConflictError, "MCP reconciliation"):
            port.plan("Plan this turn.")
        self.assertEqual(self.ledger.dispatched_call_count, 1)
        self.assertEqual(
            self.ledger.events[-1]["state"],
            ProviderCallState.POST_VALIDATION_FAILED.value,
        )


class ContinuousWorldHardeningTests(unittest.TestCase):
    def test_false_positive_matches_accept_active_bytes_but_records_diagnostic(self) -> None:
        with TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            accept_store = ContinuousWorldStore(base / "accept")
            false_store = ContinuousWorldStore(base / "false")
            _seed_character(accept_store)
            _seed_character(false_store)
            accept_store.create_candidate("world-test", "main", "turn-001")
            false_store.create_candidate("world-test", "main", "turn-001")
            accept_store.apply_creator_action(
                world_id="world-test",
                branch_id="main",
                turn_id="turn-001",
                action=CreatorReviewAction.ACCEPT,
                package=package(),
                accepted_pair=accepted_pair(),
            )
            concern = replace(
                package(),
                semantic_status=ValidatorSemanticStatus.CONCERN,
                creator_review=concern_assessment(),
            )
            false_store.apply_creator_action(
                world_id="world-test",
                branch_id="main",
                turn_id="turn-001",
                action=CreatorReviewAction.FALSE_POSITIVE,
                package=concern,
                accepted_pair=accepted_pair(),
            )
            accept_active = accept_store.branch_root("world-test", "main") / "ACTIVE"
            false_root = false_store.branch_root("world-test", "main")
            self.assertEqual(
                accept_store.tree_sha256(accept_active),
                false_store.tree_sha256(false_root / "ACTIVE"),
            )
            self.assertEqual(len(tuple((false_root / "VALIDATOR_DIAGNOSTICS").glob("*.json"))), 1)

    def test_critical_false_positive_is_eligible_only_with_concern_semantics(self) -> None:
        with TemporaryDirectory() as directory:
            store = ContinuousWorldStore(Path(directory).resolve() / "worlds")
            _seed_character(store)
            critical = replace(
                package(),
                semantic_status=ValidatorSemanticStatus.CONCERN,
                creator_review=replace(
                    concern_assessment(), severity=CreatorReviewSeverity.CRITICAL
                ),
            )
            store.create_candidate("world-test", "main", "turn-001")
            receipt = store.apply_creator_action(
                world_id="world-test",
                branch_id="main",
                turn_id="turn-001",
                action=CreatorReviewAction.FALSE_POSITIVE,
                package=critical,
                accepted_pair=accepted_pair(),
            )
            self.assertTrue(receipt.accepted)
            with self.assertRaisesRegex(ContractValidationError, "severity disagree"):
                replace(critical, semantic_status=ValidatorSemanticStatus.ACCEPTED)

    def test_failed_model_injection_remains_typed_pending_and_blocks_continuation(self) -> None:
        class FailingAppendPort(InMemoryContinuousStoredSessionPort):
            def append_context(self, handle, text):
                del handle, text
                raise RuntimeError("simulated ambiguous provider injection")

        with TemporaryDirectory() as directory:
            store = ContinuousWorldStore(Path(directory).resolve() / "worlds")
            root = _seed_character(store)
            port = FailingAppendPort()
            planner_session = ContinuousSessionCoordinator(
                session_compatibility(ContinuousSessionRole.PLANNER), port
            )
            validator_session = ContinuousSessionCoordinator(
                session_compatibility(ContinuousSessionRole.VALIDATOR), port
            )
            coordinator = ContinuousShadowTurnCoordinator(
                world=store,
                planner_session=planner_session,
                validator_session=validator_session,
                planner=_QueueStage(rich_sequence()),
                composer=_QueueStage(SimpleNamespace(story_text="Sakura requests proof.")),
                validator=_QueueStage(package()),
            )
            source = root / "ACTIVE" / "Characters" / "Sakura.json"
            coordinator.prepare(
                ContinuousTurnRequestV1(
                    world_id="world-test",
                    branch_id="main",
                    scene_id="scene-001",
                    turn_id="turn-001",
                    user_message="Hello.",
                    current_authority_packet={"protected_user_id": "character:ted"},
                    character_summaries=(character_summary(
                        source_sha256=text_sha256(source.read_text(encoding="utf-8"))
                    ),),
                )
            )
            with self.assertRaisesRegex(RuntimeError, "ambiguous provider injection"):
                coordinator.apply_creator_action(
                    "turn-001", CreatorReviewAction.ACCEPT
                )
            journal = json.loads(
                (root / ".acceptance-turn-001" / "JOURNAL.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(journal["planner_ledger_state"], "appended")
            self.assertEqual(journal["model_injection_state"], "pending")
            self.assertEqual(coordinator.restore_pending_accepted_context(), ("turn-001",))

    def test_scene_summary_is_regenerable_derived_view_and_never_changes_active(self) -> None:
        with TemporaryDirectory() as directory:
            store = ContinuousWorldStore(Path(directory).resolve() / "worlds")
            root = _seed_character(store)
            pair = AcceptedTurnPairV1(
                accepted_turn_id="turn-001",
                user_message="Hello.",
                complete_final_sequence=final_sequence(),
            )
            store.write_accepted_pair("world-test", "main", pair)
            before = store.tree_sha256(root / "ACTIVE")
            first = store.save_scene_summary("world-test", "main", _summary(pair))
            self.assertTrue(first.is_relative_to(root / "DERIVED" / "Scenes"))
            self.assertFalse(tuple((root / "ACTIVE" / "Scenes").glob("*.summary.json")))
            payload = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual(payload["authority_classification"], "non_authoritative_derived_view")
            self.assertEqual(payload["source_accepted_turn_ids"], ["turn-001"])
            self.assertEqual(
                [value["accepted_turn_id"] for value in payload["source_turn_provenance"]],
                ["turn-001"],
            )
            self.assertEqual(
                payload["source_turn_provenance"][0]["authority_basis"],
                "exact_accepted_pair",
            )
            store.save_scene_summary(
                "world-test", "main", _summary(pair, "Sakura ended the same accepted scene."),
            )
            changed = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual(changed["summary_revision"], 2)
            self.assertEqual(before, store.tree_sha256(root / "ACTIVE"))

    def test_revision_bound_create_file_rejects_array_and_string(self) -> None:
        for value in ([], "text"):
            with self.assertRaisesRegex(ContractValidationError, "revisioned JSON object"):
                WorldEditOperationV1(
                    operation_key="create_invalid",
                    target_file="Rules/invalid.json",
                    expected_file_revision=None,
                    operation=WorldEditOperationKind.CREATE_FILE,
                    field_path="/",
                    value=value,
                    reason="Invalid mutable record shape.",
                    source_final_sequence_item="verify_arrival",
                )

    def test_restart_recovery_covers_every_promotion_cut_point(self) -> None:
        stages = (
            "acceptance_journal_created",
            "journal_created",
            "active_moved_to_backup",
            "prepared_active_installed",
            "prior_backup_removed",
            "committed",
        )
        for stage in stages:
            with self.subTest(stage=stage), TemporaryDirectory() as directory:
                base = Path(directory).resolve() / "worlds"

                def failpoint(value: str) -> None:
                    if value == stage:
                        raise SystemExit("simulated process loss")

                store = ContinuousWorldStore(base, promotion_failpoint=failpoint)
                root = _seed_character(store)
                before = store.tree_sha256(root / "ACTIVE")
                store.create_candidate("world-test", "main", "turn-001")
                with self.assertRaises(SystemExit):
                    store.apply_creator_action(
                        world_id="world-test",
                        branch_id="main",
                        turn_id="turn-001",
                        action=CreatorReviewAction.ACCEPT,
                        package=package(),
                        accepted_pair=accepted_pair(),
                    )
                recovered = ContinuousWorldStore(base)
                recovered_root = recovered.initialize("world-test", "main")
                after = recovered.tree_sha256(recovered_root / "ACTIVE")
                self.assertTrue((recovered_root / "ACTIVE").is_dir())
                self.assertNotEqual(after, "")
                if stage in {"acceptance_journal_created", "journal_created"}:
                    self.assertEqual(after, before)
                else:
                    self.assertNotEqual(after, before)
                journal = next(recovered_root.glob(".acceptance-*/JOURNAL.json"))
                self.assertIn(
                    json.loads(journal.read_text(encoding="utf-8"))["state"],
                    {"local_acceptance_complete", "rolled_back"},
                )

    def test_complete_acceptance_recovery_finishes_every_local_artifact(self) -> None:
        stages = (
            "promotion_receipt_written",
            "false_positive_diagnostic_written",
            "timeline_written",
        )
        for stage in stages:
            with self.subTest(stage=stage), TemporaryDirectory() as directory:
                base = Path(directory).resolve() / "worlds"

                def failpoint(value: str) -> None:
                    if value == stage:
                        raise SystemExit("simulated acceptance evidence crash")

                store = ContinuousWorldStore(base, promotion_failpoint=failpoint)
                root = _seed_character(store)
                candidate = replace(
                    package(),
                    semantic_status=ValidatorSemanticStatus.CONCERN,
                    creator_review=concern_assessment(),
                )
                pair = AcceptedTurnPairV1(
                    accepted_turn_id="turn-001",
                    user_message="Hello.",
                    complete_final_sequence=final_sequence(),
                )
                store.create_candidate("world-test", "main", "turn-001")
                with self.assertRaises(SystemExit):
                    store.apply_creator_action(
                        world_id="world-test",
                        branch_id="main",
                        turn_id="turn-001",
                        action=CreatorReviewAction.FALSE_POSITIVE,
                        package=candidate,
                        accepted_pair=pair,
                    )
                recovered = ContinuousWorldStore(base)
                recovered_root = recovered.initialize("world-test", "main")
                receipt = recovered_root / "CANDIDATES" / "turn-001" / "PROMOTION_RECEIPT.json"
                diagnostics = tuple(
                    (recovered_root / "VALIDATOR_DIAGNOSTICS").glob(
                        "*.false_positive.json"
                    )
                )
                timeline = recovered_root / "DEBUG" / "TIMELINE.jsonl"
                journal = recovered_root / ".acceptance-turn-001" / "JOURNAL.json"
                self.assertTrue(receipt.is_file())
                self.assertEqual(len(diagnostics), 1)
                self.assertEqual(len(timeline.read_text(encoding="utf-8").splitlines()), 1)
                self.assertEqual(
                    json.loads(journal.read_text(encoding="utf-8"))["state"],
                    "local_acceptance_complete",
                )
                self.assertEqual(
                    recovered.pending_acceptance_synchronization("world-test", "main"),
                    ("turn-001",),
                )


class ContinuousEvidenceAuthorityV2Tests(unittest.TestCase):
    def test_character_summary_is_exactly_derived_and_stale_or_fabricated_fails(self) -> None:
        with TemporaryDirectory() as directory:
            store = ContinuousWorldStore(Path(directory).resolve() / "worlds")
            root = _seed_character(store)
            envelope = build_character_summary_envelope(
                branch_root=root,
                source_path="ACTIVE/Characters/Sakura.json",
                character_id="character:sakura_hanezawa",
            )
            validate_character_summary_envelope(branch_root=root, envelope=envelope)
            source = root / "ACTIVE" / "Characters" / "Sakura.json"
            record = json.loads(source.read_text(encoding="utf-8"))
            record["reasoning_summary"] = "Fabricated replacement summary."
            source.write_bytes(canonical_bytes(record) + b"\n")
            with self.assertRaisesRegex(StateConflictError, "hash is stale"):
                validate_character_summary_envelope(branch_root=root, envelope=envelope)

    def test_wrong_character_and_fabricated_latest_change_fail(self) -> None:
        with TemporaryDirectory() as directory:
            store = ContinuousWorldStore(Path(directory).resolve() / "worlds")
            root = _seed_character(store)
            with self.assertRaisesRegex(StateConflictError, "another character"):
                build_character_summary_envelope(
                    branch_root=root,
                    source_path="ACTIVE/Characters/Sakura.json",
                    character_id="character:mia_hanezawa",
                )
            genuine = build_character_summary_envelope(
                branch_root=root,
                source_path="ACTIVE/Characters/Sakura.json",
                character_id="character:sakura_hanezawa",
            )
            payload = {
                "schema_version": CharacterSummaryEnvelopeV1.SCHEMA_VERSION,
                "character_id": genuine.character_id,
                "source_path_or_record_id": genuine.source_path_or_record_id,
                "source_revision": genuine.source_revision,
                "source_sha256": genuine.source_sha256,
                "source_authority_classification": genuine.source_authority_classification,
                "summary_field_path": genuine.summary_field_path,
                "latest_changes_field_path": genuine.latest_changes_field_path,
                "summary": genuine.summary,
                "latest_accepted_changes": ("Invented accepted change.",),
            }
            fabricated = CharacterSummaryEnvelopeV1(
                **payload,
                derivation_receipt_sha256=canonical_sha256(payload),
            )
            with self.assertRaisesRegex(StateConflictError, "latest changes"):
                validate_character_summary_envelope(branch_root=root, envelope=fabricated)

    def test_validator_derived_character_summary_requires_exact_package_and_active_source(self) -> None:
        with TemporaryDirectory() as directory:
            store = ContinuousWorldStore(Path(directory).resolve() / "worlds")
            root = _seed_character(store)
            candidate = store.create_candidate("world-test", "main", "turn-001")
            validator_path = store.record_candidate_package(
                "world-test", "main", candidate, package()
            )
            active_path = root / "ACTIVE" / "Characters" / "Sakura.json"
            active_text = active_path.read_text(encoding="utf-8")
            validator_text = validator_path.read_text(encoding="utf-8")
            receipt_payload = {
                "authority_classification": "validator_derived_summary_record",
                "character_id": "character:sakura_hanezawa",
                "source_record_path": "ACTIVE/Characters/Sakura.json",
                "source_record_revision": 1,
                "source_record_sha256": text_sha256(active_text),
                "validator_package_path": validator_path.relative_to(root).as_posix(),
                "validator_package_sha256": text_sha256(validator_text),
                "summary": "Sakura retains threshold control.",
                "latest_accepted_changes": (),
            }
            derived = {
                "schema_version": "cera.validator_character_summary_record.v1",
                "_cera_revision": 1,
                **receipt_payload,
                "validator_derivation_receipt_sha256": canonical_sha256(
                    receipt_payload
                ),
            }
            derived_path = (
                root / "DERIVED" / "CharacterSummaries" / "Sakura.json"
            )
            derived_path.parent.mkdir(parents=True, exist_ok=True)
            derived_path.write_bytes(canonical_bytes(derived) + b"\n")
            envelope = build_character_summary_envelope(
                branch_root=root,
                source_path="DERIVED/CharacterSummaries/Sakura.json",
                character_id="character:sakura_hanezawa",
            )
            validate_character_summary_envelope(branch_root=root, envelope=envelope)
            validator_path.write_text("{}\n", encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(StateConflictError, "Validator package is stale"):
                validate_character_summary_envelope(branch_root=root, envelope=envelope)

    def test_private_multi_actor_and_derived_only_hard_decision_fail(self) -> None:
        with TemporaryDirectory() as directory:
            store = ContinuousWorldStore(Path(directory).resolve() / "worlds")
            root = _seed_character(store)
            registry = RequestEvidenceBindingRegistry(
                world_id="world-test", branch_id="main", turn_id="turn-001"
            )
            current = registry.allocate_current_source(
                source_identity="current:turn-001",
                source_text="Hello.",
                protected_user_allowance_scope="exact supplied source",
            )
            private = registry.allocate_initial_projection(
                branch_root=root,
                relative_path="ACTIVE/Characters/Sakura.json",
                record_type="characters",
                visibility=EvidenceVisibility.CHARACTER_PRIVATE,
                knowledge_owner_id="character:sakura_hanezawa",
            )
            base = rich_sequence()
            shared = replace(
                base,
                selected_character_ids=(
                    "character:sakura_hanezawa",
                    "character:mia_hanezawa",
                ),
                beats=(replace(
                    base.beats[0],
                    actor_ids=("character:sakura_hanezawa", "character:mia_hanezawa"),
                    source_evidence_bindings=(current.binding_key, private.binding_key),
                ),),
            )
            with self.assertRaisesRegex(PermissionError, "exactly one NPC actor"):
                registry.validate_sequence(shared, branch_root=root)

            derived_path = root / "DERIVED" / "Scenes" / "arrival.json"
            derived_path.parent.mkdir(parents=True, exist_ok=True)
            derived_path.write_bytes(canonical_bytes({
                "_cera_revision": 1,
                "authority_classification": "non_authoritative_derived_view",
                "summary": "Navigation only.",
            }) + b"\n")
            derived_registry = RequestEvidenceBindingRegistry(
                world_id="world-test", branch_id="main", turn_id="turn-002"
            )
            derived = derived_registry.allocate_initial_projection(
                branch_root=root,
                relative_path="DERIVED/Scenes/arrival.json",
                record_type="scenes",
                visibility=EvidenceVisibility.PUBLIC,
                knowledge_owner_id=None,
            )
            derived_only = replace(
                base,
                beats=(replace(
                    base.beats[0], source_evidence_bindings=(derived.binding_key,)
                ),),
            )
            with self.assertRaisesRegex(StateConflictError, "ACTIVE authority"):
                derived_registry.validate_sequence(derived_only, branch_root=root)

    def test_minimal_connective_cannot_make_ted_an_actor(self) -> None:
        base = rich_sequence().beats[0]
        with self.assertRaisesRegex(ContractValidationError, "exact supplied source"):
            replace(
                base,
                actor_ids=("character:ted",),
                protected_user_allowance=ProtectedUserAllowanceV1(
                    mode=ProtectedUserAllowanceMode.MINIMAL_NONBRANCHING_CONNECTIVE,
                    source_binding_keys=("binding_mechanical_test",),
                    explanation="Nonmeaningful continuity only.",
                ),
            )

    def test_persisted_continuous_records_are_in_schema_registry(self) -> None:
        versions = build_schema_registry().versions
        self.assertIn("cera.request_evidence_binding.v2", versions)
        self.assertIn("cera.continuous_provider_call_ledger_event.v2", versions)
        self.assertIn("cera.scene_summary_derived_view.v2", versions)

    def test_embedded_secret_redaction_and_root_attribute_diagnostic(self) -> None:
        raw = {
            "message": (
                "prefix Authorization: Bearer abcdefghijkl suffix sk-abcdefghijk "
                "api_key=supersecretvalue&next=1 https://x.test/?token=querysecretvalue"
            )
        }
        rendered = json.dumps(redact_secrets(raw))
        for secret in (
            "abcdefghijkl",
            "sk-abcdefghijk",
            "supersecretvalue",
            "querysecretvalue",
        ):
            self.assertNotIn(secret, rendered)
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            recorder = ContinuousRootDiagnosticRecorder(root)
            store = ContinuousWorldStore(root / "worlds")
            with self.assertRaises(AttributeError):
                recorder.run(
                    "compatibility_creation",
                    "create_world_compatibility",
                    lambda: getattr(store, "world_directory_identity")(
                        "hanezawa-job4", "canary-main"
                    ),
                )
            payload = json.loads(recorder.path.read_text(encoding="utf-8"))
            self.assertEqual(payload["failure"]["contract_name"], "world_directory_identity")
            self.assertEqual(payload["current_stage"], "compatibility_creation")
            self.assertTrue(payload["failure"]["stack_frames"])

    def test_failed_job4_compatibility_path_is_now_provider_free_valid(self) -> None:
        with TemporaryDirectory() as directory:
            store = ContinuousWorldStore(Path(directory).resolve() / "worlds")
            _seed_character(store, "hanezawa-job4", "canary-main")
            value = compatibility(store, ContinuousSessionRole.PLANNER)
            self.assertEqual(
                value.world_directory_identity_sha256,
                store.world_identity_sha256("hanezawa-job4", "canary-main"),
            )


if __name__ == "__main__":
    unittest.main()
