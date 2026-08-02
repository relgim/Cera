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
    AcceptedSessionProjectionV1,
    EvidenceVisibility,
    RequestEvidenceBindingRegistry,
    build_character_summary_envelope,
    validate_character_summary_envelope,
    project_final_sequence_facts,
)
from cera.continuous.sessions import ContinuousSessionRole
from cera.continuous.sessions import (
    ContinuousSessionSnapshotStore,
    ContinuousSessionCoordinator,
    InMemoryContinuousStoredSessionPort,
)
from cera.continuous.runtime import (
    ContinuousShadowTurnCoordinator,
    ContinuousTurnRequestV1,
)
from cera.continuous.ingress import ContinuousIngressAuthorityStore
from cera.continuous.record_policy import PERSISTENCE_POLICY_SHA256
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
    ingress_fixture,
    ingress_reference,
    ingress_units,
    make_ingress_authority,
    composer_draft,
    stage_candidate,
    staged_authority_kwargs,
    package,
    protected_semantic_adjudication,
    rich_sequence,
    scene_summary_package,
    session_compatibility,
)
from cera.continuous.contracts import (
    AcceptedFinalSequenceEnvelopeV1,
    CharacterRoleLedgerV1,
    CharacterSummaryEnvelopeV1,
    FrozenContinuousIngressFixtureV1,
    IngressSourceUnitKind,
    IngressSourceUnitV1,
    ProtectedUserAllowanceMode,
    ProtectedUserAllowanceV1,
    ProtectedUserRealizationSpanV1,
    ProtectedUserSourceClaimKind,
    ProtectedSemanticRelationKind,
    ProtectedSemanticAdjudicationV1,
    PersistenceDirectiveV1,
    PersistenceRecordClass,
    StoryRealizationKind,
    StoryRealizationSegmentV1,
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
from cera.continuous.prompting import PLANNER_STABLE_INSTRUCTIONS


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


def _owned_unit(
    text: str,
    *,
    start: int = 0,
    end: int | None = None,
    key: str = "source_unit_owned",
    kind: IngressSourceUnitKind = IngressSourceUnitKind.DIALOGUE,
) -> IngressSourceUnitV1:
    end = len(text) if end is None else end
    return IngressSourceUnitV1(
        schema_version=IngressSourceUnitV1.SCHEMA_VERSION,
        source_unit_key=key,
        kind=kind,
        source_start=start,
        source_end=end,
        exact_text=text[start:end],
        actor_id=(
            "character:ted"
            if kind in {IngressSourceUnitKind.ACTION, IngressSourceUnitKind.STATE}
            else None
        ),
        speaker_id=(
            "character:ted" if kind is IngressSourceUnitKind.DIALOGUE else None
        ),
        classification_basis=(
            "explicit_ingress_speaker"
            if kind is IngressSourceUnitKind.DIALOGUE
            else "explicit_ingress_actor"
        ),
    )


class _QueueStage:
    def __init__(self, *values) -> None:
        self.values = list(values)
        self.prompts: list[str] = []

    def _next(self, prompt: str):
        self.prompts.append(prompt)
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
            source_units=ingress_units("Hello, my name is Ted."),
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
            source_units=ingress_units("Hello."),
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
                    roles=CharacterRoleLedgerV1(
                        action_owner_ids=("character:mia_hanezawa",),
                    ),
                ),
            ),
        )
        with self.assertRaisesRegex(PermissionError, "private evidence"):
            registry.validate_sequence(transferred, branch_root=self.root)

    def test_final_sequence_and_edits_retain_beat_traceability(self) -> None:
        registry, sequence, _record_key = self._registry_and_sequence()
        registry.validate_sequence(sequence, branch_root=self.root)
        draft = composer_draft("Sakura requests proof.")
        registry.validate_composer_realization(
            story_text=draft.story_text,
            realizations=draft.protected_user_realizations,
            story_segments=draft.story_segments,
        )
        registry.validate_traceability(sequence, package(), branch_root=self.root)
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
            registry.validate_traceability(
                sequence, broken, branch_root=self.root
            )
        ownership_item = replace(
            final_sequence().items[0],
            roles=CharacterRoleLedgerV1(
                action_owner_ids=("character:mia_hanezawa",),
            ),
            private_state_owner_ids=("character:mia_hanezawa",),
            field_scopes=tuple(
                replace(
                    scope,
                    roles=CharacterRoleLedgerV1(
                        action_owner_ids=("character:mia_hanezawa",),
                    ),
                    knowledge_owner_id=(
                        "character:mia_hanezawa"
                        if scope.knowledge_owner_id is not None
                        else None
                    ),
                )
                for scope in final_sequence().items[0].field_scopes
            ),
        )
        ownership_broken = replace(
            package(),
            complete_final_sequence=replace(
                final_sequence(), items=(ownership_item,)
            ),
            event_record=replace(
                package().event_record,
                participant_ids=("character:mia_hanezawa",),
                item_role_ledgers=(
                    replace(
                        package().event_record.item_role_ledgers[0],
                        roles=ownership_item.roles,
                    ),
                ),
            ),
        )
        with self.assertRaisesRegex(StateConflictError, "role or claim ownership"):
            registry.validate_traceability(
                sequence, ownership_broken, branch_root=self.root
            )

    def _package_for_semantic_segment(
        self,
        segment: StoryRealizationSegmentV1,
        adjudication: ProtectedSemanticAdjudicationV1,
    ):
        base = package()
        base_item = base.complete_final_sequence.items[0]
        scopes = tuple(
            replace(
                scope,
                story_segment_keys=(segment.segment_key,),
                roles=segment.roles,
                protected_user_source_claim_keys=(),
                persistence_directives=(),
            )
            for scope in base_item.field_scopes
            if scope.field_name in {"realized_event", "resulting_state"}
        )
        item = replace(
            base_item,
            story_segment_keys=(segment.segment_key,),
            realized_event=segment.exact_text,
            valid_deepseek_additions=(),
            private_state_owner_ids=(),
            knowledge_changes=(),
            material_changes=(),
            resulting_state=segment.exact_text,
            roles=segment.roles,
            protected_user_source_claim_keys=(),
            field_scopes=scopes,
        )
        sequence = replace(
            base.complete_final_sequence,
            items=(item,),
            final_stop_state=segment.exact_text,
        )
        return replace(
            base,
            complete_final_sequence=sequence,
            world_edit_operations=(),
            created_field_log=(),
            event_record=replace(
                base.event_record,
                participant_ids=segment.roles.involved_ids,
                item_role_ledgers=(
                    replace(
                        base.event_record.item_role_ledgers[0],
                        roles=segment.roles,
                    ),
                ),
                summary=segment.exact_text,
            ),
            protected_semantic_adjudications=(adjudication,),
        )

    def test_independent_semantics_rejects_explicit_and_pronoun_laundering(self) -> None:
        for index, text in enumerate(
            ("Sakura watches as Ted steps inside.", "Sakura watches as he steps inside."),
            1,
        ):
            with self.subTest(text=text):
                registry, sequence, _ = self._registry_and_sequence()
                segment = StoryRealizationSegmentV1(
                    schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
                    segment_key=f"segment_laundered_{index}",
                    kind=StoryRealizationKind.ACTION,
                    output_start=0,
                    output_end=len(text),
                    exact_text=text,
                    roles=CharacterRoleLedgerV1(
                        action_owner_ids=("character:sakura_hanezawa",),
                        referenced_ids=("character:ted",),
                    ),
                )
                registry.validate_composer_realization(
                    story_text=text,
                    realizations=(),
                    story_segments=(segment,),
                )
                adjudication = ProtectedSemanticAdjudicationV1(
                    schema_version=ProtectedSemanticAdjudicationV1.SCHEMA_VERSION,
                    adjudication_key=f"adjudicate_laundered_{index}",
                    segment_key=segment.segment_key,
                    output_start=0,
                    output_end=len(text),
                    exact_text_sha256=text_sha256(text),
                    protected_user_id="character:ted",
                    relation=ProtectedSemanticRelationKind.PROTECTED_ASSERTION,
                    npc_assertion_owner_ids=(),
                    protected_user_source_claim_keys=("claim_unsupplied",),
                )
                with self.assertRaisesRegex(
                    StateConflictError, "laundered through Composer roles"
                ):
                    registry.validate_traceability(
                        sequence,
                        self._package_for_semantic_segment(
                            segment, adjudication
                        ),
                    )

    def test_independent_semantics_allows_npc_action_toward_ted_without_reaction(self) -> None:
        text = "Sakura closes the door in front of Ted."
        registry, sequence, _ = self._registry_and_sequence()
        segment = StoryRealizationSegmentV1(
            schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
            segment_key="segment_npc_affects_ted",
            kind=StoryRealizationKind.ACTION,
            output_start=0,
            output_end=len(text),
            exact_text=text,
            roles=CharacterRoleLedgerV1(
                action_owner_ids=("character:sakura_hanezawa",),
                affected_ids=("character:ted",),
            ),
        )
        registry.validate_composer_realization(
            story_text=text,
            realizations=(),
            story_segments=(segment,),
        )
        adjudication = ProtectedSemanticAdjudicationV1(
            schema_version=ProtectedSemanticAdjudicationV1.SCHEMA_VERSION,
            adjudication_key="adjudicate_npc_affects_ted",
            segment_key=segment.segment_key,
            output_start=0,
            output_end=len(text),
            exact_text_sha256=text_sha256(text),
            protected_user_id="character:ted",
            relation=ProtectedSemanticRelationKind.AFFECTED_BY_NPC,
            npc_assertion_owner_ids=("character:sakura_hanezawa",),
            protected_user_source_claim_keys=(),
        )
        registry.validate_traceability(
            sequence,
            self._package_for_semantic_segment(segment, adjudication),
        )

    def test_independent_semantics_rejects_missing_conflicting_and_incomplete_adjudication(self) -> None:
        text = "Sakura closes the door in front of Ted."
        registry, sequence, _ = self._registry_and_sequence()
        segment = StoryRealizationSegmentV1(
            schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
            segment_key="segment_v8_matrix",
            kind=StoryRealizationKind.ACTION,
            output_start=0,
            output_end=len(text),
            exact_text=text,
            roles=CharacterRoleLedgerV1(
                action_owner_ids=("character:sakura_hanezawa",),
                affected_ids=("character:ted",),
            ),
        )
        registry.validate_composer_realization(
            story_text=text,
            realizations=(),
            story_segments=(segment,),
        )
        valid = ProtectedSemanticAdjudicationV1(
            schema_version=ProtectedSemanticAdjudicationV1.SCHEMA_VERSION,
            adjudication_key="adjudicate_v8_matrix",
            segment_key=segment.segment_key,
            output_start=0,
            output_end=len(text),
            exact_text_sha256=text_sha256(text),
            protected_user_id="character:ted",
            relation=ProtectedSemanticRelationKind.AFFECTED_BY_NPC,
            npc_assertion_owner_ids=("character:sakura_hanezawa",),
            protected_user_source_claim_keys=(),
        )
        valid_package = self._package_for_semantic_segment(segment, valid)
        with self.assertRaisesRegex(
            ContractValidationError, "lacks independent protected semantics"
        ):
            replace(valid_package, protected_semantic_adjudications=())
        with self.assertRaisesRegex(StateConflictError, "exact NPC-owned predicate"):
            registry.validate_traceability(
                sequence,
                replace(
                    valid_package,
                    protected_semantic_adjudications=(
                        replace(
                            valid,
                            relation=ProtectedSemanticRelationKind.ADDRESSED_BY_NPC,
                        ),
                    ),
                ),
            )
        with self.assertRaisesRegex(StateConflictError, "exact Composer span"):
            registry.validate_traceability(
                sequence,
                replace(
                    valid_package,
                    protected_semantic_adjudications=(
                        replace(valid, output_end=len(text) - 1),
                    ),
                ),
            )

        raw = to_primitive(valid_package)
        raw["protected_semantic_adjudications"][0]["relation"] = "invented_relation"
        with self.assertRaises(ContractValidationError):
            build_schema_registry().decode(raw)

    def test_protected_user_claims_bind_exact_spans_across_all_beat_text(self) -> None:
        registry = RequestEvidenceBindingRegistry(
            world_id="world-test", branch_id="main", turn_id="turn-001"
        )
        current = registry.allocate_current_source(
            source_identity="current_user_source:turn-001",
            source_text='Ted says, "Hello, my name is Ted."',
            protected_user_allowance_scope="exact supplied source only",
            source_units=(
                _owned_unit(
                    'Ted says, "Hello, my name is Ted."',
                    start=len('Ted says, "'),
                    end=len('Ted says, "Hello, my name is Ted.'),
                    kind=IngressSourceUnitKind.DIALOGUE,
                ),
            ),
        )
        claim = next(
            value
            for value in registry.protected_user_claim_manifest()
            if value["kind"] == "dialogue"
        )
        base = rich_sequence()
        exact = replace(
            base,
            beats=(
                replace(
                    base.beats[0],
                    roles=CharacterRoleLedgerV1(
                        speaker_ids=("character:ted",),
                    ),
                    observable_action_or_dialogue_direction=claim["exact_text"],
                    protected_user_allowance=ProtectedUserAllowanceV1(
                        mode=ProtectedUserAllowanceMode.EXACT_SOURCE_ONLY,
                        source_binding_keys=(current.binding_key,),
                        source_claim_keys=(claim["claim_key"],),
                        explanation="Use only the exact supplied dialogue span.",
                    ),
                    source_evidence_bindings=(current.binding_key,),
                ),
            ),
        )
        registry.validate_sequence(exact, branch_root=self.root)

        active = registry.allocate_initial_projection(
            branch_root=self.root,
            relative_path="ACTIVE/Characters/Sakura.json",
            record_type="characters",
            visibility=EvidenceVisibility.CHARACTER_PRIVATE,
            knowledge_owner_id="character:sakura_hanezawa",
        )

        with self.assertRaisesRegex(PermissionError, "must equal"):
            registry.validate_sequence(
                replace(
                    exact,
                    beats=(replace(
                        exact.beats[0],
                        observable_action_or_dialogue_direction=(
                            "Ted enters the house without a supplied action."
                        ),
                    ),),
                ),
                branch_root=self.root,
            )

    def test_derived_character_summary_mcp_read_is_private_and_owner_bound(self) -> None:
        path = self.root / "DERIVED" / "CharacterSummaries" / "Sakura.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            canonical_bytes(
                {
                    "_cera_revision": 1,
                    "character_id": "character:sakura_hanezawa",
                    "summary": "Historical derived navigation only.",
                }
            )
            + b"\n"
        )
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
            "cera_world_read",
            {"path": "DERIVED/CharacterSummaries/Sakura.json"},
        )
        self.assertEqual(result["evidence_binding"]["record_type"], "character_summaries")
        self.assertEqual(result["evidence_binding"]["visibility"], "character_private")
        self.assertEqual(
            result["evidence_binding"]["knowledge_owner_id"],
            "character:sakura_hanezawa",
        )


class ContinuousProviderFreeIntegrationTests(unittest.TestCase):
    def test_three_turn_two_scene_flow_keeps_separate_sessions_and_appends_once(self) -> None:
        with TemporaryDirectory() as directory:
            store = ContinuousWorldStore(Path(directory).resolve() / "worlds")
            root = _seed_character(store)
            new_prompt = "Several days later, Ted asks about the threshold check."
            ingress_authority = make_ingress_authority(
                Path(directory).resolve() / "ingress_authority",
                ("Hello, my name is Ted.", "turn-001"),
                ("I am the expected tenant.", "turn-002"),
                (new_prompt, "turn-003"),
            )
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
                complete_final_sequence=final_sequence(
                    "turn-002", revision=2
                ),
            )
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
                composer_draft("Sakura requests proof."),
                composer_draft("Sakura acknowledges the visitor's answer."),
                composer_draft("Sakura answers in the later scene."),
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
                package(
                    turn_id="turn-001",
                    revision=1,
                    story_text="Sakura requests proof.",
                ),
                package(
                    turn_id="turn-002",
                    revision=2,
                    story_text="Sakura acknowledges the visitor's answer.",
                ),
                summary_package,
                replace(
                    package(
                        turn_id="turn-003",
                        revision=3,
                        story_text="Sakura answers in the later scene.",
                    ),
                    event_record=replace(
                        package(
                            turn_id="turn-003",
                            revision=3,
                            story_text="Sakura answers in the later scene.",
                        ).event_record,
                        scene_id="scene-002",
                    ),
                ),
            )
            coordinator = ContinuousShadowTurnCoordinator(
                world=store,
                planner_session=planner_session,
                validator_session=validator_session,
                planner=planner,
                composer=composer,
                validator=validator,
                ingress_authority=ingress_authority,
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
                summaries = (
                    (
                        character_summary(
                            source_sha256=text_sha256(
                                character_path.read_text(encoding="utf-8")
                            ),
                            source_revision=character_record["_cera_revision"],
                        ),
                    )
                    if turn_id == "turn-001"
                    else ()
                )
                candidate = coordinator.prepare(
                    ContinuousTurnRequestV1(
                        world_id="world-test",
                        branch_id="main",
                        scene_id="scene-001",
                        turn_id=turn_id,
                        user_message=message,
                        current_authority_packet={"protected_user_id": "character:ted"},
                        **ingress_reference(ingress_authority, message, turn_id),
                        character_summaries=summaries,
                    )
                )
                self.assertEqual(candidate.provider_calls, 0)
                coordinator.apply_creator_action(turn_id, CreatorReviewAction.ACCEPT)

            self.assertIn('"kind":"accepted_session_envelope"', planner.prompts[1])
            self.assertIn('"character_summary_bindings":[]', planner.prompts[1])
            self.assertNotIn(PLANNER_STABLE_INSTRUCTIONS, planner.prompts[1])
            self.assertNotIn(
                "Sakura keeps control of the threshold and requests identifying proof.",
                planner.prompts[1],
            )
            self.assertIn('"facts":[]', planner.prompts[1])
            self.assertIn(
                "[OWNER-SCOPED ACCEPTED-SESSION PROJECTIONS]\n[]",
                composer.prompts[1],
            )
            self.assertNotIn(
                '"accepted_session_projections"', validator.prompts[1]
            )
            self.assertIn(
                '"cited_accepted_evidence":[', validator.prompts[1]
            )
            self.assertIn(
                '"field_value":"The visitor remains outside awaiting verification."',
                validator.prompts[1],
            )
            cited_debug = json.loads(
                (
                    candidate.debug_root
                    / "validator_cited_accepted_evidence.json"
                ).read_text(encoding="utf-8")
            )
            replay = json.loads(
                (candidate.debug_root / "replay_input.json").read_text(
                    encoding="utf-8"
                )
            )
            validator_request = json.loads(
                validator.prompts[1].split("[VALIDATOR REQUEST]\n", 1)[1]
            )
            self.assertEqual(
                validator_request["cited_accepted_evidence"], cited_debug
            )
            self.assertEqual(
                replay["validator_cited_accepted_evidence"], cited_debug
            )
            self.assertEqual(
                canonical_sha256(cited_debug),
                candidate.validator_cited_accepted_evidence_sha256,
            )
            self.assertTrue(cited_debug)
            self.assertNotIn(
                "cera.validator_cited_accepted_evidence.v1",
                composer.prompts[1],
            )
            self.assertEqual(validator_request["accepted_pairs"], [])

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
                    **ingress_reference(ingress_authority, new_prompt, "turn-003"),
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

            # The provider-free fake route exercises the exact shared
            # coordinator schedule used by the ten-stage live shape:
            # 3 Planner + 3 Composer + 3 finalizer + 1 scene-summary call.
            self.assertEqual(
                len(planner.prompts) + len(composer.prompts) + len(validator.prompts),
                10,
            )

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
            journal = json.loads(
                (
                    store.branch_root("world-test", "main")
                    / ".acceptance-turn-003"
                    / "JOURNAL.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(journal["model_injection_state"], "synchronized")
            self.assertEqual(journal["planner_snapshot_state"], "persisted")
            for key in (
                "accepted_final_envelope_sha256",
                "planner_provider_thread_sha256",
                "injection_operation_receipt_sha256",
                "planner_session_snapshot_sha256",
                "synchronization_receipt_sha256",
                "compact_accepted_head_receipt_sha256",
                "stable_reference_set_sha256",
            ):
                self.assertEqual(len(journal[key]), 64)
            self.assertEqual(journal["stable_reference_state"], "persisted")
            self.assertTrue(
                journal["stable_reference_relative_path"].startswith(
                    "PLANNER_SESSION/ACCEPTED_REFERENCES/"
                )
            )
            self.assertEqual(
                journal["planner_session_snapshot_receipt"]["accepted_turn_id"],
                "turn-003",
            )
            self.assertTrue(
                journal["planner_session_snapshot_relative_path"].startswith(
                    "PLANNER_SESSION/ACCEPTED/"
                )
            )

    def test_acceptance_synchronization_crash_points_remain_pending_without_replay(self) -> None:
        stages = (
            "after_in_memory_ledger_append",
            "after_provider_injection_returned",
            "after_world_injection_update",
            "before_snapshot_replace",
            "after_snapshot_replace",
            "after_snapshot_persisted",
            "after_stable_references_persisted",
        )
        for stage in stages:
            with self.subTest(stage=stage), TemporaryDirectory() as directory:
                store = ContinuousWorldStore(Path(directory).resolve() / "worlds")
                root = _seed_character(store)
                ingress_authority = make_ingress_authority(
                    Path(directory).resolve() / "ingress_authority",
                    ("Hello.", "turn-001"),
                )
                session_port = InMemoryContinuousStoredSessionPort()
                planner_session = ContinuousSessionCoordinator(
                    session_compatibility(ContinuousSessionRole.PLANNER), session_port
                )
                validator_session = ContinuousSessionCoordinator(
                    session_compatibility(ContinuousSessionRole.VALIDATOR), session_port
                )

                def failpoint(value: str) -> None:
                    if value == stage:
                        raise SystemExit("simulated synchronization crash")

                coordinator = ContinuousShadowTurnCoordinator(
                    world=store,
                    planner_session=planner_session,
                    validator_session=validator_session,
                    planner=_QueueStage(rich_sequence()),
                    composer=_QueueStage(composer_draft("Sakura requests proof.")),
                    validator=_QueueStage(package()),
                    ingress_authority=ingress_authority,
                    acceptance_sync_failpoint=failpoint,
                )
                source = root / "ACTIVE" / "Characters" / "Sakura.json"
                record = json.loads(source.read_text(encoding="utf-8"))
                coordinator.prepare(
                    ContinuousTurnRequestV1(
                        world_id="world-test",
                        branch_id="main",
                        scene_id="scene-001",
                        turn_id="turn-001",
                        user_message="Hello.",
                        current_authority_packet={"protected_user_id": "character:ted"},
                        **ingress_reference(ingress_authority, "Hello.", "turn-001"),
                        character_summaries=(
                            character_summary(
                                source_sha256=text_sha256(
                                    source.read_text(encoding="utf-8")
                                ),
                                source_revision=record["_cera_revision"],
                            ),
                        ),
                    )
                )
                with self.assertRaises(SystemExit):
                    coordinator.apply_creator_action(
                        "turn-001", CreatorReviewAction.ACCEPT
                    )
                self.assertEqual(
                    store.pending_acceptance_synchronization("world-test", "main"),
                    ("turn-001",),
                )
                journal = json.loads(
                    (
                        root / ".acceptance-turn-001" / "JOURNAL.json"
                    ).read_text(encoding="utf-8")
                )
                self.assertNotEqual(journal["model_injection_state"], "synchronized")
                if stage == "after_stable_references_persisted":
                    self.assertEqual(journal["stable_reference_state"], "persisted")


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

    def test_receipt_overrides_optional_zero_and_unresolved_prepared_consumes_slot(self) -> None:
        class CompletedFailure(RuntimeError):
            external_provider_calls_observed = 0
            provider_call_receipt = {"external_provider_calls": 1}

        with self.assertRaises(CompletedFailure):
            self.ledger.execute(
                owner="planner",
                operation="receipt_beats_zero",
                route="test-route",
                model="fake-model",
                effort="medium",
                stored_thread_sha256="d" * 64,
                dispatch=lambda: (_ for _ in ()).throw(CompletedFailure("failed")),
                finalize=lambda raw: raw,
            )
        self.assertEqual(self.ledger.dispatched_call_count, 1)

        stranded = ContinuousProviderCallLedger(
            Path(self.temp.name).resolve() / "stranded.jsonl", maximum_calls=1
        )
        stranded._record(
            "call_stranded",
            "planner",
            "transport",
            ProviderCallState.PREPARED,
            "test-route",
            "fake-model",
            "medium",
            stored_thread_sha256="e" * 64,
        )
        self.assertEqual(stranded.unresolved_prepared_call_ids, ("call_stranded",))
        self.assertEqual(stranded.dispatched_call_count, 1)
        with self.assertRaisesRegex(StateConflictError, "ceiling"):
            stranded.execute(
                owner="planner",
                operation="second",
                route="test-route",
                model="fake-model",
                effort="medium",
                stored_thread_sha256="e" * 64,
                dispatch=lambda: object(),
                finalize=lambda raw: raw,
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

    def test_true_submission_marker_distinguishes_preflight_from_thread_run(self) -> None:
        def preflight_only(markers):
            markers.mark_worker_started()
            markers.mark_worker_preflight()
            raise ContractValidationError("thread was never run")

        with self.assertRaises(ContractValidationError):
            self.ledger.execute(
                owner="planner",
                operation="pre_thread_run",
                route="test-route",
                model="fake-model",
                effort="medium",
                stored_thread_sha256="f" * 64,
                dispatch_with_stage_markers=preflight_only,
                finalize=lambda raw: raw,
            )
        self.assertEqual(self.ledger.dispatched_call_count, 0)
        self.assertEqual(
            self.ledger.events[-1]["state"],
            ProviderCallState.PRETRANSPORT_FAILED.value,
        )

        def submitted(markers):
            markers.mark_worker_started()
            markers.mark_worker_preflight()
            markers.mark_transport_invoked()
            raise ContractValidationError("failed after thread_run")

        with self.assertRaises(ContractValidationError):
            self.ledger.execute(
                owner="planner",
                operation="at_thread_run",
                route="test-route",
                model="fake-model",
                effort="medium",
                stored_thread_sha256="f" * 64,
                dispatch_with_stage_markers=submitted,
                finalize=lambda raw: raw,
            )
        self.assertEqual(self.ledger.dispatched_call_count, 1)
        self.assertEqual(
            self.ledger.events[-1]["state"], ProviderCallState.PROVIDER_FAILED.value
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
    def test_candidate_authority_manifest_is_required_and_immutable(self) -> None:
        with TemporaryDirectory() as directory:
            store = ContinuousWorldStore(Path(directory).resolve() / "worlds")
            root = _seed_character(store)
            stage_candidate(store, package())
            authority = (
                root / "CANDIDATES" / "turn-001" / "CANDIDATE_AUTHORITY.json"
            )
            payload = json.loads(authority.read_text(encoding="utf-8"))
            payload["authority_context_sha256"] = "f" * 64
            authority.write_bytes(canonical_bytes(payload) + b"\n")
            with self.assertRaisesRegex(StateConflictError, "authority changed"):
                store.apply_creator_action(
                    world_id="world-test",
                    branch_id="main",
                    turn_id="turn-001",
                    action=CreatorReviewAction.ACCEPT,
                    package=package(),
                    candidate_sha256="0" * 64,
                    authority_context_sha256="0" * 64,
                    accepted_pair=accepted_pair(),
                )

    def test_false_positive_matches_accept_active_bytes_but_records_diagnostic(self) -> None:
        with TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            accept_store = ContinuousWorldStore(base / "accept")
            false_store = ContinuousWorldStore(base / "false")
            _seed_character(accept_store)
            _seed_character(false_store)
            stage_candidate(accept_store, package())
            accept_store.apply_creator_action(
                world_id="world-test",
                branch_id="main",
                turn_id="turn-001",
                action=CreatorReviewAction.ACCEPT,
                package=package(),
                **staged_authority_kwargs(accept_store, package()),
                accepted_pair=accepted_pair(),
            )
            concern = replace(
                package(),
                semantic_status=ValidatorSemanticStatus.CONCERN,
                creator_review=concern_assessment(),
            )
            stage_candidate(false_store, concern)
            false_store.apply_creator_action(
                world_id="world-test",
                branch_id="main",
                turn_id="turn-001",
                action=CreatorReviewAction.FALSE_POSITIVE,
                package=concern,
                **staged_authority_kwargs(false_store, concern),
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
            stage_candidate(store, critical)
            receipt = store.apply_creator_action(
                world_id="world-test",
                branch_id="main",
                turn_id="turn-001",
                action=CreatorReviewAction.FALSE_POSITIVE,
                package=critical,
                **staged_authority_kwargs(store, critical),
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
            ingress_authority = make_ingress_authority(
                Path(directory).resolve() / "ingress_authority",
                ("Hello.", "turn-001"),
            )
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
                composer=_QueueStage(composer_draft("Sakura requests proof.")),
                validator=_QueueStage(package()),
                ingress_authority=ingress_authority,
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
                    **ingress_reference(ingress_authority, "Hello.", "turn-001"),
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
                stage_candidate(store, package())
                with self.assertRaises(SystemExit):
                    store.apply_creator_action(
                        world_id="world-test",
                        branch_id="main",
                        turn_id="turn-001",
                        action=CreatorReviewAction.ACCEPT,
                        package=package(),
                        **staged_authority_kwargs(store, package()),
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
                stage_candidate(store, candidate)
                with self.assertRaises(SystemExit):
                    store.apply_creator_action(
                        world_id="world-test",
                        branch_id="main",
                        turn_id="turn-001",
                        action=CreatorReviewAction.FALSE_POSITIVE,
                        package=candidate,
                        **staged_authority_kwargs(store, candidate),
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

    def test_validator_derived_character_summary_is_not_an_authority_source(self) -> None:
        with TemporaryDirectory() as directory:
            store = ContinuousWorldStore(Path(directory).resolve() / "worlds")
            root = _seed_character(store)
            candidate = store.create_candidate("world-test", "main", "turn-001")
            validator_path = store.record_candidate_package(
                "world-test",
                "main",
                candidate,
                package(),
                candidate_sha256="1" * 64,
                authority_context_sha256="2" * 64,
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
            with self.assertRaisesRegex(
                ContractValidationError, "ACTIVE authoritative record"
            ):
                build_character_summary_envelope(
                    branch_root=root,
                    source_path="DERIVED/CharacterSummaries/Sakura.json",
                    character_id="character:sakura_hanezawa",
                )

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
                source_units=ingress_units("Hello."),
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
                    roles=CharacterRoleLedgerV1(
                        action_owner_ids=(
                            "character:sakura_hanezawa",
                            "character:mia_hanezawa",
                        ),
                    ),
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
            with self.assertRaisesRegex(StateConflictError, "owner-bound accepted-session"):
                derived_registry.validate_sequence(derived_only, branch_root=root)

    def test_minimal_connective_cannot_make_ted_an_actor(self) -> None:
        base = rich_sequence().beats[0]
        with self.assertRaisesRegex(ContractValidationError, "exact supplied source"):
            replace(
                base,
                roles=CharacterRoleLedgerV1(
                    action_owner_ids=("character:ted",),
                ),
                protected_user_allowance=ProtectedUserAllowanceV1(
                    mode=ProtectedUserAllowanceMode.MINIMAL_NONBRANCHING_CONNECTIVE,
                    source_binding_keys=("binding_mechanical_test",),
                    explanation="Nonmeaningful continuity only.",
                ),
            )

    def test_persisted_continuous_records_are_in_schema_registry(self) -> None:
        versions = build_schema_registry().versions
        self.assertIn("cera.request_evidence_binding.v4", versions)
        self.assertIn("cera.protected_user_source_claim.v3", versions)
        self.assertIn("cera.continuous_ingress_source_unit.v1", versions)
        self.assertIn("cera.story_realization_segment.v3", versions)
        self.assertIn("cera.protected_user_realization_span.v1", versions)
        self.assertIn("cera.accepted_session_projection.v5", versions)
        self.assertIn("cera.accepted_session_fact.v2", versions)
        self.assertIn("cera.character_role_ledger.v1", versions)
        self.assertIn("cera.event_item_role_ledger.v1", versions)
        self.assertIn("cera.continuous_context_injection_receipt.v1", versions)
        self.assertIn("cera.continuous_session_snapshot_receipt.v1", versions)
        self.assertIn("cera.continuous_provider_call_ledger_event.v3", versions)
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


class ContinuousAuthorityV5Tests(unittest.TestCase):
    def test_ingress_owned_source_units_replace_text_heuristics(self) -> None:
        text = "Hello, my name is Ted. Is this the Hanezawa residence?"
        split = text.index(" Is this")
        registry = RequestEvidenceBindingRegistry(
            world_id="world-test", branch_id="main", turn_id="turn-001"
        )
        registry.allocate_current_source(
            source_identity="current_user_source:turn-001",
            source_text=text,
            protected_user_allowance_scope="exact source only",
            source_units=(
                _owned_unit(text, start=0, end=split, key="source_unit_intro"),
                IngressSourceUnitV1(
                    schema_version=IngressSourceUnitV1.SCHEMA_VERSION,
                    source_unit_key="source_unit_separator",
                    kind=IngressSourceUnitKind.NARRATION,
                    source_start=split,
                    source_end=split + 1,
                    exact_text=text[split : split + 1],
                    actor_id=None,
                    speaker_id=None,
                    classification_basis="explicit_ingress_narration",
                ),
                _owned_unit(
                    text,
                    start=split + 1,
                    key="source_unit_question",
                ),
            ),
        )
        claims = registry.protected_user_claim_manifest()
        self.assertEqual(tuple(value["exact_text"] for value in claims), (
            "Hello, my name is Ted.",
            "Is this the Hanezawa residence?",
        ))
        self.assertTrue(all(
            value["deterministic_projection_rule"] == "explicit_ingress_source_unit"
            for value in claims
        ))

        npc_text = 'Sakura says, "I am worried."'
        npc_registry = RequestEvidenceBindingRegistry(
            world_id="world-test", branch_id="main", turn_id="turn-002"
        )
        npc_registry.allocate_current_source(
            source_identity="current_user_source:turn-002",
            source_text=npc_text,
            protected_user_allowance_scope="exact source only",
            source_units=ingress_units(npc_text),
        )
        self.assertEqual(npc_registry.protected_user_claim_manifest(), ())

    def test_continuous_request_rejects_unclassified_source_gaps(self) -> None:
        text = "Prefix Hello."
        with self.assertRaisesRegex(ContractValidationError, "gap-free"):
            FrozenContinuousIngressFixtureV1(
                schema_version=FrozenContinuousIngressFixtureV1.SCHEMA_VERSION,
                fixture_id="cera.fixture.continuous_authority.gap",
                fixture_schema_id="cera.fixture_registry.gap_test.v1",
                world_id="world-test",
                branch_id="main",
                session_id="session:test",
                request_id="request:turn-001",
                turn_id="turn-001",
                idempotency_key_sha256=text_sha256("gap-test"),
                raw_source=text,
                protected_user_id="character:ted",
                source_units=(
                    _owned_unit(
                        text,
                        start=len("Prefix "),
                        key="source_unit_incomplete",
                    ),
                ),
            )

    def test_composer_must_annotate_every_exact_protected_source_occurrence(self) -> None:
        registry = RequestEvidenceBindingRegistry(
            world_id="world-test", branch_id="main", turn_id="turn-001"
        )
        registry.allocate_current_source(
            source_identity="current_user_source:turn-001",
            source_text='Ted says, "Please wait."',
            protected_user_allowance_scope="exact source only",
            source_units=(
                _owned_unit(
                    'Ted says, "Please wait."',
                    start=len('Ted says, "'),
                    end=len('Ted says, "Please wait.'),
                ),
            ),
        )
        claim = next(
            value
            for value in registry.protected_user_claim_manifest()
            if value["kind"] == "dialogue"
        )
        story = "Please wait. Sakura pauses."
        realization = ProtectedUserRealizationSpanV1(
            schema_version=ProtectedUserRealizationSpanV1.SCHEMA_VERSION,
            claim_key=claim["claim_key"],
            kind=ProtectedUserSourceClaimKind.DIALOGUE,
            output_start=0,
            output_end=len("Please wait."),
            exact_text="Please wait.",
        )
        story_segments = (
            StoryRealizationSegmentV1(
                schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
                segment_key="segment_ted_dialogue",
                kind=StoryRealizationKind.DIALOGUE,
                output_start=0,
                output_end=len("Please wait."),
                exact_text="Please wait.",
                roles=CharacterRoleLedgerV1(
                    speaker_ids=("character:ted",),
                ),
                protected_user_source_claim_keys=(claim["claim_key"],),
            ),
            StoryRealizationSegmentV1(
                schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
                segment_key="segment_sakura_pause",
                kind=StoryRealizationKind.NARRATION,
                output_start=len("Please wait."),
                output_end=len(story),
                exact_text=story[len("Please wait."):],
                roles=CharacterRoleLedgerV1(
                    referenced_ids=("character:sakura_hanezawa",),
                ),
            ),
        )
        registry.validate_composer_realization(
            story_text=story,
            realizations=(realization,),
            story_segments=story_segments,
        )
        with self.assertRaisesRegex(PermissionError, "ledgers disagree"):
            registry.validate_composer_realization(
                story_text=story, realizations=(), story_segments=story_segments
            )
        with self.assertRaisesRegex(PermissionError, "changed exact output bytes"):
            registry.validate_composer_realization(
                story_text="X" + story,
                realizations=(realization,),
                story_segments=story_segments,
            )

    def test_composer_rejects_paraphrased_or_hidden_ted_ownership(self) -> None:
        text = "Please wait."
        registry = RequestEvidenceBindingRegistry(
            world_id="world-test", branch_id="main", turn_id="turn-001"
        )
        registry.allocate_current_source(
            source_identity="current_user_source:turn-001",
            source_text=text,
            protected_user_allowance_scope="exact source only",
            source_units=(_owned_unit(text),),
        )
        claim = registry.protected_user_claim_manifest()[0]
        paraphrase = "Wait here."
        segment = StoryRealizationSegmentV1(
            schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
            segment_key="segment_ted_paraphrase",
            kind=StoryRealizationKind.DIALOGUE,
            output_start=0,
            output_end=len(paraphrase),
            exact_text=paraphrase,
            roles=CharacterRoleLedgerV1(
                speaker_ids=("character:ted",),
            ),
            protected_user_source_claim_keys=(claim["claim_key"],),
        )
        with self.assertRaisesRegex(PermissionError, "invented or paraphrased"):
            registry.validate_composer_realization(
                story_text=paraphrase,
                realizations=(),
                story_segments=(segment,),
            )

        hidden = "Sakura looks at Ted."
        hidden_segment = StoryRealizationSegmentV1(
            schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
            segment_key="segment_hidden_ted",
            kind=StoryRealizationKind.ACTION,
            output_start=0,
            output_end=len(hidden),
            exact_text=hidden,
            roles=CharacterRoleLedgerV1(
                action_owner_ids=("character:sakura_hanezawa",),
                referenced_ids=("character:ted",),
            ),
        )
        registry.validate_composer_realization(
            story_text=hidden,
            realizations=(),
            story_segments=(hidden_segment,),
        )

    def test_final_event_and_edit_cannot_extend_one_valid_ted_claim(self) -> None:
        text = "Please wait."
        registry = RequestEvidenceBindingRegistry(
            world_id="world-test", branch_id="main", turn_id="turn-001"
        )
        current = registry.allocate_current_source(
            source_identity="current_user_source:turn-001",
            source_text=text,
            protected_user_allowance_scope="exact source only",
            source_units=(_owned_unit(text),),
        )
        claim = registry.protected_user_claim_manifest()[0]
        segment = StoryRealizationSegmentV1(
            schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
            segment_key="segment_ted_exact",
            kind=StoryRealizationKind.DIALOGUE,
            output_start=0,
            output_end=len(text),
            exact_text=text,
            roles=CharacterRoleLedgerV1(
                speaker_ids=("character:ted",),
            ),
            protected_user_source_claim_keys=(claim["claim_key"],),
        )
        realization = ProtectedUserRealizationSpanV1(
            schema_version=ProtectedUserRealizationSpanV1.SCHEMA_VERSION,
            claim_key=claim["claim_key"],
            kind=ProtectedUserSourceClaimKind.DIALOGUE,
            output_start=0,
            output_end=len(text),
            exact_text=text,
        )
        registry.validate_composer_realization(
            story_text=text,
            realizations=(realization,),
            story_segments=(segment,),
        )
        base_sequence = rich_sequence()
        sequence = replace(
            base_sequence,
            beats=(
                replace(
                    base_sequence.beats[0],
                    roles=CharacterRoleLedgerV1(
                        speaker_ids=("character:ted",),
                    ),
                    source_evidence_bindings=(current.binding_key,),
                    protected_user_allowance=ProtectedUserAllowanceV1(
                        mode=ProtectedUserAllowanceMode.EXACT_SOURCE_ONLY,
                        source_binding_keys=(current.binding_key,),
                        source_claim_keys=(claim["claim_key"],),
                        explanation="Exact supplied dialogue only.",
                    ),
                ),
            ),
        )
        base_item = final_sequence().items[0]
        exact_scopes = tuple(
            replace(
                scope,
                story_segment_keys=(segment.segment_key,),
                roles=CharacterRoleLedgerV1(
                    speaker_ids=("character:ted",),
                ),
                protected_user_source_claim_keys=(claim["claim_key"],),
            )
            for scope in base_item.field_scopes
            if scope.field_name in {"realized_event", "resulting_state"}
        )
        exact_item = replace(
            base_item,
            story_segment_keys=(segment.segment_key,),
            realized_event=text,
            valid_deepseek_additions=(),
            private_state_owner_ids=(),
            knowledge_changes=(),
            material_changes=(),
            resulting_state=text,
            protected_user_source_claim_keys=(claim["claim_key"],),
            roles=CharacterRoleLedgerV1(
                speaker_ids=("character:ted",),
            ),
            field_scopes=exact_scopes,
        )
        exact_final = replace(
            final_sequence(), items=(exact_item,), final_stop_state=text
        )
        valid = replace(
            package(),
            complete_final_sequence=exact_final,
            world_edit_operations=(),
            created_field_log=(),
            event_record=replace(
                package().event_record,
                participant_ids=("character:ted",),
                item_role_ledgers=(
                    replace(
                        package().event_record.item_role_ledgers[0],
                        roles=exact_item.roles,
                    ),
                ),
                summary=text,
                protected_user_source_claim_keys=(claim["claim_key"],),
            ),
            protected_semantic_adjudications=(
                replace(
                    protected_semantic_adjudication(text),
                    adjudication_key="adjudicate_segment_ted_exact",
                    segment_key=segment.segment_key,
                    relation=ProtectedSemanticRelationKind.PROTECTED_ASSERTION,
                    npc_assertion_owner_ids=(),
                    protected_user_source_claim_keys=(claim["claim_key"],),
                ),
            ),
        )
        registry.validate_traceability(sequence, valid)

        invented = text + " Ted steps inside."
        invented_item = replace(
            exact_item,
            realized_event=invented,
            resulting_state=text,
        )
        invented_final = replace(exact_final, items=(invented_item,))
        with self.assertRaisesRegex(StateConflictError, "invented or paraphrased"):
            registry.validate_traceability(
                sequence,
                replace(valid, complete_final_sequence=invented_final),
            )

        with self.assertRaisesRegex(StateConflictError, "event summary"):
            registry.validate_traceability(
                sequence,
                replace(
                    valid,
                    event_record=replace(valid.event_record, summary=invented),
                ),
            )

        invented_edit = WorldEditOperationV1(
            operation_key="persist_invented_movement",
            target_file="Characters/Sakura.json",
            expected_file_revision=1,
            operation=WorldEditOperationKind.REPLACE,
            field_path="/reasoning_summary",
            value=invented,
            reason="Persist accepted final field realized_event.",
            source_final_sequence_item=exact_item.item_key,
            source_final_field_name="realized_event",
            protected_user_source_claim_keys=(claim["claim_key"],),
        )
        with self.assertRaisesRegex(
            ContractValidationError, "persistable final fields"
        ):
            replace(valid, world_edit_operations=(invented_edit,))


class ContinuousAuthorityV6Tests(unittest.TestCase):
    def test_v7_persistence_rejects_record_classes_without_typed_subject_schemas(self) -> None:
        for record_class, target_file in (
            (PersistenceRecordClass.RULE, "Rules/arrival.json"),
            (PersistenceRecordClass.LOCATION, "Locations/entry.json"),
            (PersistenceRecordClass.EVENT, "Events/arrival.json"),
            (PersistenceRecordClass.SCENE, "Scenes/arrival.json"),
        ):
            with self.subTest(record_class=record_class), self.assertRaisesRegex(
                ContractValidationError, "only character or relationship"
            ):
                PersistenceDirectiveV1(
                    schema_version=PersistenceDirectiveV1.SCHEMA_VERSION,
                    directive_key=f"persist_{record_class.value}",
                    target_file=target_file,
                    target_record_class=record_class,
                    persistence_policy_sha256=PERSISTENCE_POLICY_SHA256,
                    target_record_id=f"{record_class.value}:arrival",
                    target_subject_ids=("character:sakura_hanezawa",),
                    expected_file_revision=1,
                    operation=WorldEditOperationKind.ADD,
                    field_path="/accepted_facts/turn-001",
                    expected_prior_value_sha256=None,
                    source_value_index=0,
                )

    def test_ingress_receipt_custody_and_all_request_identities_are_enforced(self) -> None:
        with TemporaryDirectory() as directory:
            store = ContinuousWorldStore(Path(directory).resolve() / "worlds")
            _seed_character(store)
            text = "Hello."
            idempotency_key = "authority-v6-turn-001"
            fixture = ingress_fixture(text, "turn-001")
            fixture = replace(
                fixture,
                fixture_id="cera.fixture.continuous_authority.v6",
                idempotency_key_sha256=text_sha256(idempotency_key),
            )
            authority = ContinuousIngressAuthorityStore(
                Path(directory).resolve() / "ingress_authority",
                fixture_registry=(fixture,),
            )
            receipt = authority.issue_frozen_fixture(
                fixture_id="cera.fixture.continuous_authority.v6",
                idempotency_key=idempotency_key,
            )
            port = InMemoryContinuousStoredSessionPort()
            coordinator = ContinuousShadowTurnCoordinator(
                world=store,
                planner_session=ContinuousSessionCoordinator(
                    session_compatibility(ContinuousSessionRole.PLANNER), port
                ),
                validator_session=ContinuousSessionCoordinator(
                    session_compatibility(ContinuousSessionRole.VALIDATOR), port
                ),
                planner=_QueueStage(rich_sequence()),
                composer=_QueueStage(composer_draft("Sakura requests proof.")),
                validator=_QueueStage(package()),
                ingress_authority=authority,
            )
            request = ContinuousTurnRequestV1(
                world_id="world-test",
                branch_id="main",
                session_id=receipt.session_id,
                request_id=receipt.request_id,
                idempotency_key_sha256=text_sha256(idempotency_key),
                scene_id="scene-001",
                turn_id="turn-001",
                user_message=text,
                current_authority_packet={"protected_user_id": "character:ted"},
                ingress_receipt_id=receipt.receipt_id,
                ingress_receipt_sha256=receipt.receipt_sha256,
                character_summaries=(
                    character_summary(
                        source_sha256=text_sha256(
                            (
                                store.branch_root("world-test", "main")
                                / "ACTIVE"
                                / "Characters"
                                / "Sakura.json"
                            ).read_text(encoding="utf-8")
                        )
                    ),
                ),
            )
            for field, value in (
                ("session_id", "session:substituted"),
                ("request_id", "request:substituted"),
                ("idempotency_key_sha256", "f" * 64),
                ("user_message", "Changed."),
            ):
                with self.subTest(field=field), self.assertRaisesRegex(
                    StateConflictError, "ingress receipt changed"
                ):
                    coordinator.prepare(replace(request, **{field: value}))
            with self.assertRaisesRegex(StateConflictError, "unknown or changed"):
                coordinator.prepare(
                    replace(request, ingress_receipt_sha256="e" * 64)
                )
            candidate = coordinator.prepare(request)
            changed_reference = replace(
                request, ingress_receipt_sha256="d" * 64
            )
            self.assertNotEqual(
                candidate.authority_context_sha256,
                replace(candidate, request=changed_reference).authority_context_sha256,
            )
            self.assertNotEqual(
                candidate.candidate_sha256,
                replace(candidate, request=changed_reference).candidate_sha256,
            )
            changed_semantics = replace(
                candidate,
                protected_semantic_adjudication_ledger_sha256="c" * 64,
            )
            self.assertNotEqual(
                candidate.authority_context_sha256,
                changed_semantics.authority_context_sha256,
            )
            self.assertNotEqual(
                candidate.candidate_sha256,
                changed_semantics.candidate_sha256,
            )

    def test_protected_assertion_roles_require_claims_but_npc_address_does_not(self) -> None:
        variants = (
            (
                StoryRealizationKind.ACTION,
                CharacterRoleLedgerV1(action_owner_ids=("character:ted",)),
                "He steps inside.",
            ),
            (
                StoryRealizationKind.PRIVATE_STATE,
                CharacterRoleLedgerV1(state_owner_ids=("character:ted",)),
                "He feels relieved.",
            ),
            (
                StoryRealizationKind.CONSENT_OR_DECISION,
                CharacterRoleLedgerV1(state_owner_ids=("character:ted",)),
                "He agrees.",
            ),
            (
                StoryRealizationKind.DIALOGUE,
                CharacterRoleLedgerV1(speaker_ids=("character:ted",)),
                "Yes.",
            ),
        )
        for index, (kind, roles, exact_text) in enumerate(variants, start=1):
            with self.subTest(kind=kind), self.assertRaisesRegex(
                ContractValidationError, "exact supplied claim"
            ):
                StoryRealizationSegmentV1(
                    schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
                    segment_key=f"segment_protected_{index}",
                    kind=kind,
                    output_start=0,
                    output_end=len(exact_text),
                    exact_text=exact_text,
                    roles=roles,
                )

        npc_text = "Sakura looks at Ted."
        segment = StoryRealizationSegmentV1(
            schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
            segment_key="segment_npc_addresses_ted",
            kind=StoryRealizationKind.ACTION,
            output_start=0,
            output_end=len(npc_text),
            exact_text=npc_text,
            roles=CharacterRoleLedgerV1(
                action_owner_ids=("character:sakura_hanezawa",),
                addressed_ids=("character:ted",),
            ),
        )
        self.assertEqual(segment.roles.assertion_owner_ids, ("character:sakura_hanezawa",))
        self.assertEqual(segment.roles.addressed_ids, ("character:ted",))
        with self.assertRaisesRegex(ContractValidationError, "multiple roles"):
            CharacterRoleLedgerV1(
                action_owner_ids=("character:ted",),
                affected_ids=("character:ted",),
            )

    def test_unprotected_world_edits_must_equal_the_cited_final_field(self) -> None:
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
                source_units=ingress_units("Hello."),
            )
            active = registry.allocate_initial_projection(
                branch_root=root,
                relative_path="ACTIVE/Characters/Sakura.json",
                record_type="characters",
                visibility=EvidenceVisibility.CHARACTER_PRIVATE,
                knowledge_owner_id="character:sakura_hanezawa",
            )
            sequence = replace(
                rich_sequence(),
                beats=(
                    replace(
                        rich_sequence().beats[0],
                        source_evidence_bindings=(
                            current.binding_key,
                            active.binding_key,
                        ),
                    ),
                ),
            )
            registry.validate_sequence(sequence, branch_root=root)
            draft = composer_draft("Sakura requests proof.")
            registry.validate_composer_realization(
                story_text=draft.story_text,
                realizations=draft.protected_user_realizations,
                story_segments=draft.story_segments,
            )
            valid = package()
            registry.validate_traceability(sequence, valid, branch_root=root)
            changed = replace(
                valid.world_edit_operations[0],
                value="Sakura now trusts Ted completely.",
            )
            changed_created = replace(
                valid.created_field_log[0],
                value="Sakura now trusts Ted completely.",
            )
            with self.assertRaisesRegex(
                ContractValidationError, "field-level persistence directive"
            ):
                replace(
                    valid,
                    world_edit_operations=(changed,),
                    created_field_log=(changed_created,),
                )
            with self.assertRaisesRegex(
                ContractValidationError, "changed the proposed value"
            ):
                replace(valid, created_field_log=(changed_created,))

            mia = root / "ACTIVE" / "Characters" / "Mia.json"
            mia.write_bytes(
                canonical_bytes(
                    {
                        "schema_version": "cera.continuous_character.v1",
                        "_cera_revision": 1,
                        "character_id": "character:mia_hanezawa",
                        "turn_claims": {},
                    }
                )
                + b"\n"
            )
            item = valid.complete_final_sequence.items[0]
            wrong_scopes = tuple(
                replace(
                    scope,
                    persistence_directives=tuple(
                        replace(
                            directive,
                            target_file="Characters/Mia.json",
                            target_record_id="character:mia_hanezawa",
                            target_subject_ids=("character:mia_hanezawa",),
                        )
                        for directive in scope.persistence_directives
                    ),
                )
                for scope in item.field_scopes
            )
            wrong_target = replace(
                valid,
                complete_final_sequence=replace(
                    valid.complete_final_sequence,
                    items=(replace(item, field_scopes=wrong_scopes),),
                ),
                world_edit_operations=(
                    replace(
                        valid.world_edit_operations[0],
                        target_file="Characters/Mia.json",
                    ),
                ),
                created_field_log=(
                    replace(
                        valid.created_field_log[0],
                        target_file="Characters/Mia.json",
                    ),
                ),
            )
            with self.assertRaisesRegex(
                StateConflictError, "wrong character"
            ):
                registry.validate_traceability(
                    sequence, wrong_target, branch_root=root
                )

    def test_accepted_projection_rejects_public_or_cross_owner_private_state(self) -> None:
        private_item = final_sequence().items[0]
        private_facts = tuple(
            value
            for value in project_final_sequence_facts(private_item)
            if value.knowledge_owner_id is not None
        )
        common = dict(
            schema_version=AcceptedSessionProjectionV1.SCHEMA_VERSION,
            projection_key="projection_session_test",
            world_id="world-test",
            branch_id="main",
            request_turn_id="turn-002",
            accepted_turn_id="turn-001",
            scene_id="scene-001",
            facts=private_facts,
            accepted_pair_sha256="1" * 64,
            accepted_event_sha256="2" * 64,
            accepted_envelope_sha256="3" * 64,
            acceptance_receipt_sha256="4" * 64,
            provider_thread_sha256="5" * 64,
            session_snapshot_sha256="6" * 64,
            synchronization_receipt_sha256="7" * 64,
        )
        with self.assertRaisesRegex(ContractValidationError, "public.*private"):
            AcceptedSessionProjectionV1(knowledge_owner_id=None, **common)
        with self.assertRaisesRegex(ContractValidationError, "another owner's"):
            AcceptedSessionProjectionV1(
                knowledge_owner_id="character:mia_hanezawa", **common
            )
        public_facts = tuple(
            value
            for value in project_final_sequence_facts(private_item)
            if value.knowledge_owner_id is None
        )
        with self.assertRaisesRegex(ContractValidationError, "no fact owned"):
            AcceptedSessionProjectionV1(
                knowledge_owner_id="character:mia_hanezawa",
                **{**common, "facts": public_facts},
            )
        accepted = AcceptedSessionProjectionV1(
            knowledge_owner_id="character:sakura_hanezawa", **common
        )
        self.assertEqual(
            accepted.facts[0].knowledge_owner_id,
            "character:sakura_hanezawa",
        )

    def test_acceptance_snapshot_is_immutable_after_current_pointer_advances(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            port = InMemoryContinuousStoredSessionPort()
            coordinator = ContinuousSessionCoordinator(
                session_compatibility(ContinuousSessionRole.PLANNER), port
            )
            envelope = AcceptedFinalSequenceEnvelopeV1(
                schema_version=AcceptedFinalSequenceEnvelopeV1.SCHEMA_VERSION,
                accepted_turn_id="turn-001",
                user_message="Hello.",
                complete_final_sequence=final_sequence(),
                acceptance_receipt_sha256="8" * 64,
            )
            coordinator.record_planner_provisional(
                "turn-001", rich_sequence().sequence_sha256
            )
            coordinator.append_accepted_final_sequence(envelope)
            injection = coordinator.synchronize_accepted_final_sequence_with_receipt(
                envelope
            )
            self.assertIsNotNone(injection)
            store = ContinuousSessionSnapshotStore(root)
            receipt = store.save_for_acceptance(
                coordinator.snapshot(),
                accepted_turn_id="turn-001",
                accepted_envelope_sha256=envelope.envelope_sha256,
                injection_receipt=injection,
            )
            coordinator.record_planner_provisional("turn-002", "9" * 64)
            store.save(coordinator.snapshot())
            immutable = store.load_immutable(receipt)
            self.assertEqual(immutable.accepted_turn_ids, ("turn-001",))
            path = root / receipt.immutable_relative_path
            path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaisesRegex(StateConflictError, "bytes changed"):
                store.load_immutable(receipt)


if __name__ == "__main__":
    unittest.main()
