from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cera.errors import ContractValidationError, StateConflictError
from cera.pi_scene.codex_planner import RetainedCodexPlannerAdapter
from cera.pi_scene.context import (
    AcceptedBranchContextProvider,
    PiSceneContextSeedV1,
    initial_hanezawa_doorway_seed,
)
from cera.pi_scene.contracts import (
    PiWriterReceiptV1,
    RecordingStatus,
    SceneRoute,
    advisory_ted_warnings,
)
from cera.pi_scene.http import (
    PI_SCENE_ORDINARY_MODEL,
    PI_SCENE_PROFILE,
    PiSceneHttpAdapter,
    PiSceneServerConfigV1,
    build_pi_scene_server,
)
from cera.pi_scene.pi_adapter import (
    ADULT_RECORDER_SYSTEM_PROMPT,
    ADULT_WRITER_SYSTEM_PROMPT,
    ORDINARY_RECORDER_SYSTEM_PROMPT,
    ORDINARY_WRITER_SYSTEM_PROMPT,
    PiSceneAdapter,
    PiSceneInvocationResultV1,
    PiSceneInvocationV1,
    _ProcessResult,
    _parse_writer_output,
)
from cera.pi_scene.operation_ledger import PiProviderOperationLedger
from cera.pi_scene.runtime import (
    LeanPiSceneCoordinator,
    LeanReviewState,
    LeanSceneTurnInputV1,
    PlannerTurnInputV1,
    PlannerTurnOutputV1,
    _parse_recorder_payload,
    repair_latest_ordinary_recording_from_output,
)
from cera.pi_scene.sillytavern_isolation import (
    isolated_sillytavern_command,
    stage_isolated_sillytavern,
    verify_isolated_sillytavern,
)
from cera.pi_scene.store import LeanSceneStore
from cera.pi_scene.writer_view import (
    WriterViewInputV1,
    WriterViewMaterializer,
    resolve_confined_path,
    verify_writer_view,
)
from cera.pi_scene.readable_debug import ReadablePiSceneDebugLog
from cera.serialization import canonical_json, canonical_sha256, text_sha256
from cera.sequence_first.contracts import (
    ItemKind,
    SequenceDraftV1,
    SequenceItemV1,
)
from scripts.run_pi_scene_lean_server import (
    _initialize_live_runtime_roots,
    _seed_live_runtime_state,
)


def sequence(label: str = "hana_answers") -> dict[str, object]:
    return {
        "items": [
            {
                "item_key": label,
                "owner_id": "character:hana",
                "kind": "dialogue_intent",
                "summary": "Hana answers while preserving the conversational floor.",
            }
        ],
        "durable_changes": [],
        "presence_changes": [],
        "resulting_public_state": "Hana has answered and the conversation remains open.",
        "unresolved_threads": ["Ted may respond."],
        "stopping_boundary": "Stop with the floor returned to Ted.",
    }


def adult_handoff(label: str = "adult_choice") -> dict[str, object]:
    return {
        "schema_version": "cera.pi_scene.adult_handoff.v1",
        "handoff_key": label,
        "all_participants_adults": True,
        "consent_boundary": "The supplied scene remains consensual and capacity-valid.",
        "causal_direction": "The adults make one mutual choice and stop at the next user floor.",
    }


class FakePlanner:
    def __init__(self) -> None:
        self.calls: list[PlannerTurnInputV1] = []

    def plan(self, request: PlannerTurnInputV1) -> PlannerTurnOutputV1:
        self.calls.append(request)
        return PlannerTurnOutputV1(
            sequence=sequence(f"hana_answers_{len(self.calls)}"),
            provider_operations=1,
        )


class FakeSequencePlannerSession:
    def __init__(self) -> None:
        self.calls = []

    def plan(self, semantic_input):
        self.calls.append(semantic_input)
        return SequenceDraftV1(
            items=(
                SequenceItemV1(
                    item_key="hana_answers",
                    kind=ItemKind.DIALOGUE_INTENT,
                    concise_meaning="Hana answers and returns the floor to Ted.",
                    owner_response_semantics="Hana answers and returns the floor to Ted.",
                    owner_id="character:hana",
                    evidence_keys=("source:current",),
                ),
            ),
            durable_changes=(),
            presence_changes=(),
            resulting_public_state="Ted and Hana remain in the kitchen.",
            unresolved_threads=("Ted may respond.",),
            stopping_boundary="Stop after Hana returns the floor to Ted.",
        )


class FakeOperationEvidence:
    def __init__(self) -> None:
        self.turns: list[str] = []

    def begin_turn(self, turn: str) -> None:
        self.turns.append(turn)


class FakePi:
    def __init__(self) -> None:
        self.calls: list[PiSceneInvocationV1] = []
        self.writer_outputs: list[str] = []
        self.recorder_outputs: list[str] = []
        self.fail_next_recorder = False

    def invoke(self, request: PiSceneInvocationV1) -> PiSceneInvocationResultV1:
        self.calls.append(request)
        if request.purpose == "recorder" and self.fail_next_recorder:
            self.fail_next_recorder = False
            raise StateConflictError("injected Recorder transport failure")
        session_id = f"pi-session-{len(self.calls):04d}"
        if request.purpose == "writer":
            output = (
                self.writer_outputs.pop(0)
                if self.writer_outputs
                else "Hana answers with calm warmth, then leaves the response to Ted."
            )
        else:
            if self.recorder_outputs:
                output = self.recorder_outputs.pop(0)
            elif request.route is SceneRoute.ORDINARY:
                output = json.dumps(
                    {
                        "secondary_canon": [],
                        "resulting_public_state": (
                            "Hana has answered and the conversation remains open."
                        ),
                        "relationship_changes": [],
                        "knowledge_changes": [],
                        "durable_changes": [],
                        "unresolved_threads": ["Ted may respond."],
                    },
                    separators=(",", ":"),
                )
            else:
                output = json.dumps(
                    {
                        "full_record": {
                            "decision_path": ["Both adults make the authorized mutual choice."],
                            "events": [
                                {
                                    "event_key": "event-mutual-choice",
                                    "summary": "The authorized adult choice occurs.",
                                    "motive": "Mutual interest within the handoff boundary.",
                                    "alternatives_considered": ["Pause and return the floor."],
                                    "consent_or_boundary_transition": "Mutual consent remains active.",
                                    "thoughts_and_feelings": ["Both remain attentive to each other."],
                                    "durable_effects": ["The shared experience may affect later trust."],
                                    "knowledge_scope": ["Only the present adults know the details."],
                                }
                            ],
                        },
                        "codex_projection": {
                            "decision_path_summary": ["The adults made a mutual authorized choice."],
                            "items": [
                                {
                                    "event_key": "event-mutual-choice",
                                    "non_explicit_summary": "A consensual adult interaction occurred.",
                                    "lasting_story_meaning": "Their shared trust may develop from it.",
                                }
                            ],
                            "resulting_public_state": "The adults remain together afterward.",
                            "unresolved_threads": ["Their next conversation remains open."],
                        },
                    },
                    separators=(",", ":"),
                )
        parent_hash = (
            None
            if request.accepted_parent_session is None or request.force_rehydrate
            else request.accepted_parent_session.session_id_sha256
        )
        receipt = PiWriterReceiptV1(
            schema_version=PiWriterReceiptV1.SCHEMA_VERSION,
            route=request.route,
            provider="deepseek",
            model="deepseek-v4-flash",
            pi_version="test",
            session_id_sha256=text_sha256(session_id),
            parent_session_id_sha256=parent_hash,
            request_sha256=text_sha256(request.prompt),
            output_sha256=text_sha256(output),
            provider_operations=1,
            tool_call_count=2,
            failed_tool_call_count=0,
            input_tokens=100,
            cached_input_tokens=(40 if parent_hash else 0),
            output_tokens=30,
            reasoning_tokens=0,
            duration_ms=5,
            finish_status="stop",
            rehydrated=(request.accepted_parent_session is None or request.force_rehydrate),
        )
        return PiSceneInvocationResultV1(
            output_text=output,
            session_id=session_id,
            session_dir=request.session_dir,
            writer_receipt=receipt,
            raw_event_count=6,
        )


class FailOncePromotionStore(LeanSceneStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.failures_remaining = 1

    def promote_pi_session(self, *args, **kwargs):
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise StateConflictError("injected accepted-session promotion failure")
        return super().promote_pi_session(*args, **kwargs)


def turn(*, source: str = "Continue the scene.", adult: bool = False) -> LeanSceneTurnInputV1:
    return LeanSceneTurnInputV1(
        world_id="world-test",
        branch_id="branch-main",
        scene_id="scene-kitchen",
        exact_user_source=source,
        current_state={
            "accepted_present_character_ids": ["character:ted", "character:hana"],
            "public_scene_state": "Ted and Hana are talking in the kitchen.",
        },
        characters={
            "character:hana": {
                "name": "Hana",
                "age": 38,
                "voice": "Warm and non-confrontational.",
            }
        },
        relationships={"ted-hana": {"summary": "They are still establishing trust."}},
        recent_prose=("Hana welcomed Ted into the conversation.",),
        relevant_memories={"hana-arrival": {"summary": "Ted recently arrived."}},
        voice_examples={"hana": "\"Please, take your time,\" Hana said."},
        craft_index={"entries": ["dialogue-pacing"]},
        adult_handoff=(adult_handoff() if adult else None),
    )


class PiSceneLeanTests(unittest.TestCase):
    def test_live_runtime_initializes_both_codex_workspace_roots(self) -> None:
        with TemporaryDirectory() as temporary:
            requested = Path(temporary) / "runtime"
            root, lifecycle, operations = _initialize_live_runtime_roots(requested)
            self.assertEqual(root, requested.resolve())
            self.assertTrue(lifecycle.is_dir())
            self.assertTrue(operations.is_dir())

    def test_live_runtime_seed_copy_preserves_source(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            (source / "accepted_world").mkdir(parents=True)
            (source / "pi_sessions").mkdir()
            (source / "accepted_world" / "receipt.json").write_text(
                "accepted", encoding="utf-8"
            )
            (source / "pi_sessions" / "session.jsonl").write_text(
                "session", encoding="utf-8"
            )
            target.mkdir()

            _seed_live_runtime_state(target, source)

            self.assertEqual(
                (target / "accepted_world" / "receipt.json").read_text(encoding="utf-8"),
                "accepted",
            )
            self.assertEqual(
                (source / "pi_sessions" / "session.jsonl").read_text(encoding="utf-8"),
                "session",
            )

    def make_runtime(self, root: Path, *, fault=None):
        planner = FakePlanner()
        pi = FakePi()
        store = LeanSceneStore(root / "world")
        coordinator = LeanPiSceneCoordinator(
            store=store,
            planner=planner,
            writer_views=WriterViewMaterializer(root / "views"),
            pi=pi,  # type: ignore[arg-type]
            session_root=root / "sessions",
            recording_fault_injector=fault,
        )
        return coordinator, planner, pi, store

    def test_retained_codex_adapter_projects_only_bounded_accepted_evidence(self) -> None:
        session = FakeSequencePlannerSession()
        operation_evidence = FakeOperationEvidence()
        adapter = RetainedCodexPlannerAdapter(
            session,
            operation_evidence=operation_evidence,  # type: ignore[arg-type]
        )
        accepted = {
            "receipt": {
                "accepted_turn_id": "accepted-0001",
                "generation": 1,
                "route": "adult",
                "exact_user_source": "The prior adult turn was accepted.",
                "exact_accepted_prose": "Accepted private prose. " * 600,
                "primary_authority_json": json.dumps(adult_handoff(), separators=(",", ":")),
            },
            "adult_projection": {
                "items": [
                    {
                        "event_key": "event-mutual-choice",
                        "non_explicit_summary": "A consensual adult interaction occurred.",
                        "lasting_story_meaning": "Their shared trust may develop from it.",
                    }
                ]
            },
        }
        result = adapter.plan(
            PlannerTurnInputV1(
                world_id="world-test",
                branch_id="branch-main",
                scene_id="scene-kitchen",
                exact_user_source="Continue the scene.",
                current_state={
                    "accepted_present_character_ids": ["character:ted", "character:hana"],
                    "public_scene_state": "Ted and Hana remain in the kitchen.",
                    "unresolved_threads": ["Ted may respond."],
                },
                characters={"character:hana": {"name": "Hana", "age": 38}},
                relationships={"ted-hana": {"summary": "Their trust is developing."}},
                relevant_memories={"recent": {"summary": "The prior turn was accepted."}},
                accepted_records=(accepted,),
            )
        )
        self.assertEqual(result.provider_operations, 1)
        self.assertEqual(len(operation_evidence.turns), 1)
        self.assertTrue(
            operation_evidence.turns[0].startswith(
                "world-test:branch-main:planner-0001:"
            )
        )
        self.assertEqual(result.sequence["items"][0]["item_key"], "hana_answers")
        semantic = session.calls[0]
        self.assertIsNone(semantic.prior_realized_sequence)
        self.assertEqual(
            semantic.accepted_present_character_ids,
            ("character:ted", "character:hana"),
        )
        accepted_evidence = next(
            value
            for value in semantic.evidence_records
            if value.evidence_key.startswith("evidence:accepted:")
        )
        self.assertIn("adult_projection", accepted_evidence.exact_content)
        self.assertNotIn("adult_full_record", accepted_evidence.exact_content)
        self.assertNotIn("The accepted scene ends", accepted_evidence.exact_content)
        self.assertNotIn("primary_authority_json", accepted_evidence.exact_content)
        self.assertLess(len(accepted_evidence.exact_content), 4_000)
        self.assertEqual(
            semantic.hard_boundaries[:2],
            (
                "Do not invent Ted speech or dialogue.",
                "Do not invent Ted thoughts or feelings.",
            ),
        )

    def test_retained_planner_rejects_internal_only_sequence_before_writer(self) -> None:
        class InternalOnlyPlannerSession:
            def plan(self, semantic_input):
                return SequenceDraftV1(
                    items=(
                        SequenceItemV1(
                            item_key="hana_private_choice",
                            kind=ItemKind.PRIVATE_STATE,
                            concise_meaning="Hana privately chooses how to answer.",
                            owner_response_semantics=(
                                "Hana privately chooses how to answer."
                            ),
                            owner_id="character:hana",
                            evidence_keys=("source:current",),
                        ),
                        SequenceItemV1(
                            item_key="return_floor",
                            kind=ItemKind.STOPPING_BOUNDARY,
                            concise_meaning="Return the floor to Ted.",
                            causal_parent_item_key="hana_private_choice",
                            evidence_keys=("source:current",),
                        ),
                    ),
                    durable_changes=(),
                    presence_changes=(),
                    resulting_public_state="Ted and Hana remain in the room.",
                    unresolved_threads=("Ted may respond.",),
                    stopping_boundary="Stop before Ted's next response.",
                )

        adapter = RetainedCodexPlannerAdapter(InternalOnlyPlannerSession())
        with self.assertRaisesRegex(
            ContractValidationError,
            "surface-realizable item",
        ):
            adapter.plan(
                PlannerTurnInputV1(
                    world_id="world-test",
                    branch_id="branch-main",
                    scene_id="scene-room",
                    exact_user_source="Continue the scene.",
                    current_state={
                        "accepted_present_character_ids": [
                            "character:ted",
                            "character:hana",
                        ],
                        "public_scene_state": "Ted and Hana remain in the room.",
                        "unresolved_threads": ["Ted may respond."],
                    },
                    characters={"character:hana": {"name": "Hana", "age": 38}},
                    relationships={},
                    relevant_memories={},
                    accepted_records=(),
                )
            )

    def test_human_readable_debug_log_keeps_labeled_inputs_and_outputs_together(self) -> None:
        with TemporaryDirectory() as temporary:
            debug = ReadablePiSceneDebugLog(Path(temporary) / "readable")
            entry = debug.write(
                stage="codex-planner",
                identity="turn-0001",
                sections={
                    "Exact user input": "Continue the scene.",
                    "Codex Planner structured output": {"items": ["hana_answers"]},
                },
            )
            self.assertIsNotNone(entry)
            assert entry is not None
            self.assertTrue(entry.is_file())
            self.assertEqual((debug.root / "LATEST.md").read_bytes(), entry.read_bytes())
            text = entry.read_text(encoding="utf-8")
            self.assertIn("## Exact user input", text)
            self.assertIn("Continue the scene.", text)
            self.assertIn("## Codex Planner structured output", text)
            self.assertIn("hana_answers", text)
            self.assertIn(entry.name, (debug.root / "INDEX.md").read_text(encoding="utf-8"))

    def test_human_readable_debug_can_be_disabled_bounded_and_redacts_secrets(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            disabled = ReadablePiSceneDebugLog(root / "disabled", enabled=False)
            self.assertIsNone(
                disabled.write(stage="planner", identity="turn-1", sections={"Input": "x"})
            )
            self.assertFalse(disabled.root.exists())

            debug = ReadablePiSceneDebugLog(root / "bounded", max_entries=2)
            for number in range(3):
                debug.write(
                    stage="writer",
                    identity=f"candidate-{number}",
                    sections={
                        "Request": {
                            "Authorization": "Bearer private-value",
                            "api_key": "private-value",
                            "visible_prose": f"scene {number}",
                        }
                    },
                )
            entries = [
                path
                for path in debug.root.glob("*.md")
                if path.name not in {"README.md", "INDEX.md", "LATEST.md"}
            ]
            self.assertEqual(len(entries), 2)
            latest = (debug.root / "LATEST.md").read_text(encoding="utf-8")
            self.assertNotIn("private-value", latest)
            self.assertEqual(latest.count("[REDACTED]"), 2)
            self.assertIn("scene 2", latest)

    def test_observed_pronoun_attributed_ted_dialogue_is_warn_only(self) -> None:
        story = (
            "Ted stood just inside the threshold, looking toward Hana.\n\n"
            '\"Sorry,\" he said, \"I meant to ask whether this is the residence.\"'
        )
        warnings = advisory_ted_warnings(story)
        self.assertEqual(
            [value.warning_code for value in warnings],
            ["possible_invented_ted_dialogue"],
        )

    def test_writer_prompts_do_not_restate_protected_user_contributions(self) -> None:
        for prompt in (ORDINARY_WRITER_SYSTEM_PROMPT, ADULT_WRITER_SYSTEM_PROMPT):
            self.assertIn("tool named context directly exactly once", prompt)
            self.assertIn("do not call a tool named invoke", prompt)
            self.assertIn("zz_CURRENT_TURN_AUTHORITY.json", prompt)
            self.assertIn("Start from the NPC or world response", prompt)
            self.assertIn("Do not invent Ted dialogue", prompt)
            self.assertIn("Freely add compatible transient", prompt)
            self.assertIn("fact_scope", prompt)
            self.assertIn("Return complete visible prose only", prompt)
            self.assertNotIn("opening sentence", prompt.lower())
        self.assertIn("current turn wins", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("RESPONSE_SEQUENCE.json", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn(
            "outside the Writer root",
            ORDINARY_WRITER_SYSTEM_PROMPT,
        )
        self.assertIn(
            "completed_source_anchor_key",
            ORDINARY_WRITER_SYSTEM_PROMPT,
        )
        self.assertIn("selected surface response", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("internal_causal_guidance", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("surface_realization_items", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("guides_surface_item_key", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("response_start_contract", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("completed cause implicit", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("Transient staging may begin only after", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("FINAL RESPONSE START GATE", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("one deletion test", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("not a prose checklist", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("Fully realize causal_direction", ADULT_WRITER_SYSTEM_PROMPT)
        self.assertIn("consent_and_capacity", ADULT_WRITER_SYSTEM_PROMPT)
        self.assertIn(
            "scene-local, reversible, non-identifying, non-causal",
            ADULT_WRITER_SYSTEM_PROMPT,
        )

    def test_recorder_prompts_require_direct_tool_and_closed_json_shape(self) -> None:
        for prompt in (ORDINARY_RECORDER_SYSTEM_PROMPT, ADULT_RECORDER_SYSTEM_PROMPT):
            self.assertIn("tool named context directly exactly once", prompt)
            self.assertIn("never call a tool named invoke", prompt)
            self.assertIn("must begin with { and end with }", prompt)
            self.assertIn("no analysis", prompt)
        self.assertIn("knowledge_scope must each be a JSON array", ADULT_RECORDER_SYSTEM_PROMPT)

    def test_creator_test_seed_matches_visible_doorway_and_does_not_surface_mia(self) -> None:
        seed = initial_hanezawa_doorway_seed()
        self.assertEqual(seed.scene_id, "scene-hanezawa-entryway")
        self.assertEqual(seed.accepted_present_character_ids, ("character:sakura",))
        self.assertIn("outside the closed front door", seed.public_scene_state)
        self.assertIn("Mia is in the common room", seed.public_scene_state)
        self.assertIn("none of them is in the current", seed.public_scene_state)

    def test_writer_view_is_minimal_manifest_bound_and_confined(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            view = WriterViewMaterializer(root / "views").materialize(
                WriterViewInputV1(
                    world_id="world-test",
                    branch_id="branch-main",
                    scene_id="scene-kitchen",
                    turn_id="turn-0001",
                    candidate_id="candidate-one",
                    route=SceneRoute.ORDINARY,
                    user_prompt="Continue.",
                    primary_authority=sequence(),
                    current_state={"public_scene_state": "Hana is present."},
                    characters={"hana": {"name": "Hana", "age": 38}},
                    relationships={"ted-hana": {"summary": "New acquaintances."}},
                    recent_prose=("Hana greeted Ted.",),
                    relevant_memories={"arrival": {"summary": "Ted arrived."}},
                    voice_examples={"hana": "A warm voice example."},
                    craft_index={"entries": ["dialogue"]},
                    accepted_records=(),
                )
            )
            self.assertEqual(view, verify_writer_view(view.root))
            self.assertEqual(view.purpose, "writer")
            self.assertFalse((view.root / "PRIMARY_SEQUENCE.json").exists())
            self.assertFalse((view.root / "ADULT_HANDOFF.json").exists())
            self.assertTrue(
                (view.root.parent / "custody" / "CANONICAL_SEQUENCE.json").is_file()
            )
            authority_order = json.loads(
                (view.root / "zz_CURRENT_TURN_AUTHORITY.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                authority_order["schema_version"],
                "cera.pi_scene.writer_authority_order.v6",
            )
            self.assertEqual(authority_order["current_route"], "ordinary")
            self.assertEqual(
                authority_order["current_primary_authority_path"],
                "RESPONSE_SEQUENCE.json",
            )
            self.assertEqual(
                authority_order["canonical_primary_sequence_custody"],
                "python_and_post_accept_recorder_only",
            )
            self.assertEqual(
                authority_order["realization_scope"],
                {
                    "render_user_prompt": False,
                    "postcondition_authority_path": (
                        "RESPONSE_SEQUENCE.json#postconditions"
                    ),
                    "internal_guidance_item_keys": [],
                    "response_authority_path": "RESPONSE_SEQUENCE.json",
                    "response_item_keys": ["hana_answers"],
                    "surface_realization_item_keys": ["hana_answers"],
                    "response_start_item_key": "hana_answers",
                    "response_start_contract_path": (
                        "RESPONSE_SEQUENCE.json#response_start_contract"
                    ),
                    "source_contribution_status": "already_supplied_context_only",
                },
            )
            self.assertEqual(
                authority_order["presentation_contract"]["first_visible_beat"],
                "response_start_item",
            )
            self.assertEqual(
                authority_order["presentation_contract"][
                    "response_start_contract_path"
                ],
                "RESPONSE_SEQUENCE.json#response_start_contract",
            )
            start_gate = json.loads(
                (view.root / "zz_RESPONSE_START_GATE.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                start_gate["schema_version"],
                "cera.pi_scene.response_start_gate.v3",
            )
            self.assertEqual(
                start_gate["authority_class"],
                "derived_noncanonical_execution_focus",
            )
            self.assertEqual(
                start_gate["canonical_authority_path"],
                "RESPONSE_SEQUENCE.json",
            )
            self.assertEqual(
                start_gate["response_start_item_key"],
                "hana_answers",
            )
            self.assertEqual(start_gate["response_start_owner_id"], "character:hana")
            self.assertEqual(start_gate["response_start_scope"], "character")
            self.assertEqual(start_gate["response_start_kind"], "dialogue_intent")
            self.assertEqual(start_gate["item_projection_role"], "surface_realization")
            self.assertEqual(
                start_gate["owner_response_semantics"],
                "Hana answers while preserving the conversational floor.",
            )
            self.assertEqual(
                start_gate["realization_mode"],
                "surface_response_after_completed_source",
            )
            self.assertEqual(
                start_gate["completed_source_rendering"], "implicit_cause_only"
            )
            self.assertEqual(start_gate["pre_response_narration"], "forbidden")
            self.assertIn(
                "physical or private condition",
                start_gate["detail_boundary"]["future_reliance_test"],
            )
            self.assertEqual(
                sorted(path.name for path in view.root.iterdir())[-1],
                "zz_RESPONSE_START_GATE.json",
            )
            with self.assertRaises(ContractValidationError):
                resolve_confined_path(view.root, "../outside.txt")
            with self.assertRaises(ContractValidationError):
                resolve_confined_path(
                    view.root,
                    "../custody/CANONICAL_SEQUENCE.json",
                )
            with self.assertRaises(ContractValidationError):
                resolve_confined_path(view.root, str((root / "outside.txt").resolve()))
            outside = root / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            link = view.root / "linked-outside.txt"
            try:
                os.symlink(outside, link)
            except OSError:
                pass
            else:
                with self.assertRaisesRegex(ContractValidationError, "symbolic link"):
                    resolve_confined_path(view.root, "linked-outside.txt")
            with self.assertRaisesRegex(ContractValidationError, "forbidden key"):
                WriterViewMaterializer(root / "other").materialize(
                    WriterViewInputV1(
                        world_id="world-test",
                        branch_id="branch-main",
                        scene_id="scene-kitchen",
                        turn_id="turn-0002",
                        candidate_id="candidate-secret",
                        route=SceneRoute.ORDINARY,
                        user_prompt="Continue.",
                        primary_authority=sequence(),
                        current_state={"api_key": "forbidden"},
                        characters={},
                        relationships={},
                        recent_prose=(),
                        relevant_memories={},
                        voice_examples={},
                        craft_index={},
                        accepted_records=(),
                    )
                )

    def test_response_start_gate_tamper_fails_closed_after_manifest_rebinding(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            view = WriterViewMaterializer(root / "views").materialize(
                WriterViewInputV1(
                    world_id="world-test",
                    branch_id="branch-main",
                    scene_id="scene-kitchen",
                    turn_id="turn-0001",
                    candidate_id="candidate-gate-tamper",
                    route=SceneRoute.ORDINARY,
                    user_prompt="Continue.",
                    primary_authority=sequence(),
                    current_state={"public_scene_state": "Hana is present."},
                    characters={"hana": {"name": "Hana", "age": 38}},
                    relationships={},
                    recent_prose=("Hana greeted Ted.",),
                    relevant_memories={},
                    voice_examples={},
                    craft_index={},
                    accepted_records=(),
                )
            )
            gate_path = view.root / "zz_RESPONSE_START_GATE.json"
            gate = json.loads(gate_path.read_text(encoding="utf-8"))
            gate["response_start_owner_id"] = "character:mia"
            gate_text = canonical_json(gate)
            gate_path.write_text(gate_text, encoding="utf-8")
            manifest_path = view.root / "MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            gate_entry = next(
                entry
                for entry in manifest["files"]
                if entry["path"] == "zz_RESPONSE_START_GATE.json"
            )
            gate_entry["sha256"] = text_sha256(gate_text)
            gate_entry["bytes"] = len(gate_text.encode("utf-8"))
            manifest_path.write_text(canonical_json(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                StateConflictError,
                "derived response-start gate differs",
            ):
                verify_writer_view(view.root)

    def test_writer_view_separates_supplied_source_items_from_response_scope(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            authority = sequence("ted_source_action")
            authority["items"] = [
                {
                    "item_key": "ted_source_action",
                    "owner_id": "character:ted",
                    "kind": "action",
                    "summary": "The protected user's supplied action is entry state.",
                    "protected_user_claim_keys": ["current_request"],
                    "protected_user_exact_quotes": ["supplied source"],
                    "durable_change_keys": ["source_change"],
                },
                {
                    "item_key": "hana_response",
                    "owner_id": "character:hana",
                    "kind": "dialogue_intent",
                    "summary": "Hana responds from her own perspective.",
                    "causal_parent_item_key": "ted_source_action",
                    "protected_user_claim_keys": [],
                    "protected_user_exact_quotes": [],
                    "durable_change_keys": ["response_change"],
                },
                {
                    "item_key": "return_floor",
                    "owner_id": None,
                    "kind": "stopping_boundary",
                    "summary": "Return the floor to the protected user.",
                    "protected_user_claim_keys": [],
                    "protected_user_exact_quotes": [],
                },
            ]
            authority["durable_changes"] = [
                {"change_key": "source_change", "summary": "Source-bound effect."},
                {"change_key": "response_change", "summary": "Response-bound effect."},
            ]
            authority["presence_changes"] = [
                {
                    "character_id": "character:ted",
                    "direction": "enter",
                    "effective_after_item_key": "ted_source_action",
                },
                {
                    "character_id": "character:hana",
                    "direction": "exit",
                    "effective_after_item_key": "hana_response",
                },
            ]
            view = WriterViewMaterializer(root / "views").materialize(
                WriterViewInputV1(
                    world_id="world-test",
                    branch_id="branch-main",
                    scene_id="scene-private",
                    turn_id="turn-0001",
                    candidate_id="candidate-scope",
                    route=SceneRoute.ORDINARY,
                    user_prompt="A supplied source contribution.",
                    primary_authority=authority,
                    current_state={"public_scene_state": "Two adults are present."},
                    characters={"hana": {"name": "Hana", "age": 38}},
                    relationships={},
                    recent_prose=(),
                    relevant_memories={},
                    voice_examples={},
                    craft_index={},
                    accepted_records=(),
                )
            )
            scope = json.loads(
                (view.root / "zz_CURRENT_TURN_AUTHORITY.json").read_text(
                    encoding="utf-8"
                )
            )["realization_scope"]
            self.assertEqual(scope["response_item_keys"], ["hana_response"])
            self.assertEqual(scope["internal_guidance_item_keys"], [])
            self.assertEqual(
                scope["surface_realization_item_keys"], ["hana_response"]
            )
            self.assertEqual(scope["response_start_item_key"], "hana_response")
            response_sequence = json.loads(
                (view.root / "RESPONSE_SEQUENCE.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                response_sequence["schema_version"],
                "cera.pi_scene.response_sequence.v7",
            )
            self.assertEqual(
                [
                    item["item_key"]
                    for item in response_sequence["surface_realization_items"]
                ],
                ["hana_response"],
            )
            self.assertEqual(response_sequence["internal_causal_guidance"], [])
            self.assertEqual(
                response_sequence["surface_realization_items"][0][
                    "causal_parent_item_key"
                ],
                None,
            )
            source_anchor = response_sequence["surface_realization_items"][0][
                "completed_source_anchor_key"
            ]
            self.assertRegex(source_anchor, r"^completed-source-[0-9a-f]{20}$")
            self.assertNotIn(
                "ted_source_action",
                [
                    item["item_key"]
                    for item in response_sequence["surface_realization_items"]
                ],
            )
            self.assertNotIn("source_context_item_keys", response_sequence)
            self.assertEqual(
                response_sequence["postconditions"],
                {
                    "remain_open": ["Ted may respond."],
                    "resulting_public_state_must_be_true": (
                        "Hana has answered and the conversation remains open."
                    ),
                    "termination_constraint": (
                        "Stop with the floor returned to Ted."
                    ),
                },
            )
            self.assertEqual(
                response_sequence["response_start_contract"],
                {
                    "response_start_item_key": "hana_response",
                    "response_start_scope": "character",
                    "response_start_owner_id": "character:hana",
                    "response_start_kind": "dialogue_intent",
                    "completed_source_anchor_key": source_anchor,
                    "causal_anchor_mode": "completed_source_direct",
                    "realization_mode": "surface_response_after_completed_source",
                    "completed_source_rendering": "implicit_cause_only",
                    "pre_response_narration": "forbidden",
                },
            )
            self.assertNotIn("resulting_public_state", response_sequence)
            self.assertNotIn("unresolved_threads", response_sequence)
            self.assertNotIn("stopping_boundary", response_sequence)
            for item in response_sequence["surface_realization_items"]:
                self.assertIn("owner_response_semantics", item)
                self.assertEqual(item["projection_role"], "surface_realization")
                self.assertEqual(item["response_scope"], "character")
                self.assertNotIn("concise_meaning", item)
                self.assertNotIn("evidence_keys", item)
                self.assertNotIn("planner_item_keys", item)
                self.assertNotIn("protected_user_claim_keys", item)
                self.assertNotIn("protected_user_exact_quotes", item)
            self.assertEqual(
                [change["change_key"] for change in response_sequence["durable_changes"]],
                ["response_change"],
            )
            self.assertEqual(
                [
                    change["effective_after_item_key"]
                    for change in response_sequence["presence_changes"]
                ],
                ["hana_response"],
            )
            self.assertEqual(
                json.loads(
                    (
                        view.root.parent
                        / "custody"
                        / "CANONICAL_SEQUENCE.json"
                    ).read_text(encoding="utf-8")
                ),
                authority,
            )
            provenance = json.loads(
                (
                    view.root.parent / "custody" / "PROJECTION_PROVENANCE.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                provenance["schema_version"],
                "cera.pi_scene.response_projection_provenance.v2",
            )
            self.assertEqual(
                provenance["source_anchor_bindings"],
                [
                    {
                        "canonical_source_item_key": "ted_source_action",
                        "completed_source_anchor_key": source_anchor,
                    }
                ],
            )
            self.assertEqual(provenance["constraint_item_keys"], ["return_floor"])
            self.assertEqual(
                provenance["response_item_mappings"][0],
                {
                    "canonical_causal_parent_item_key": "ted_source_action",
                    "canonical_item_key": "hana_response",
                    "completed_source_anchor_key": source_anchor,
                    "projected_causal_parent_item_key": None,
                    "response_semantics_source": "legacy_concise_meaning",
                },
            )
            visible_bytes = "\n".join(
                path.read_text(encoding="utf-8")
                for path in view.root.rglob("*")
                if path.is_file()
            )
            self.assertNotIn("The protected user's supplied action", visible_bytes)
            self.assertNotIn("ted_source_action", visible_bytes)
            self.assertIn("zz_RESPONSE_START_GATE.json", visible_bytes)

    def test_private_state_is_subtext_and_visible_action_owns_response_start(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            authority = sequence("ted_source_action")
            authority["items"] = [
                {
                    "item_key": "ted_source_action",
                    "owner_id": "character:ted",
                    "kind": "action",
                    "concise_meaning": "Ted completes the supplied action.",
                    "owner_response_semantics": None,
                    "protected_user_claim_keys": ["current_request"],
                    "protected_user_exact_quotes": ["supplied source"],
                    "durable_change_keys": [],
                },
                {
                    "item_key": "hana_internal_response",
                    "owner_id": "character:hana",
                    "kind": "private_state",
                    "concise_meaning": "Hana decides on a restrained response.",
                    "owner_response_semantics": (
                        "Hana privately chooses a restrained and receptive response."
                    ),
                    "causal_parent_item_key": "ted_source_action",
                    "protected_user_claim_keys": [],
                    "protected_user_exact_quotes": [],
                    "durable_change_keys": [],
                },
                {
                    "item_key": "hana_visible_response",
                    "owner_id": "character:hana",
                    "kind": "action",
                    "concise_meaning": "Hana visibly softens her expression.",
                    "owner_response_semantics": (
                        "Hana's expression softens with restrained appreciation."
                    ),
                    "causal_parent_item_key": "hana_internal_response",
                    "protected_user_claim_keys": [],
                    "protected_user_exact_quotes": [],
                    "durable_change_keys": [],
                },
                {
                    "item_key": "return_floor",
                    "owner_id": None,
                    "kind": "stopping_boundary",
                    "concise_meaning": "Return the floor to Ted.",
                    "owner_response_semantics": None,
                    "causal_parent_item_key": "hana_visible_response",
                    "protected_user_claim_keys": [],
                    "protected_user_exact_quotes": [],
                    "durable_change_keys": [],
                },
            ]
            view = WriterViewMaterializer(root / "views").materialize(
                WriterViewInputV1(
                    world_id="world-test",
                    branch_id="branch-main",
                    scene_id="scene-private",
                    turn_id="turn-0001",
                    candidate_id="candidate-subtext-start",
                    route=SceneRoute.ORDINARY,
                    user_prompt="A supplied source contribution.",
                    primary_authority=authority,
                    current_state={"public_scene_state": "Two adults are present."},
                    characters={"hana": {"name": "Hana", "age": 38}},
                    relationships={},
                    recent_prose=(),
                    relevant_memories={},
                    voice_examples={},
                    craft_index={},
                    accepted_records=(),
                )
            )
            response = json.loads(
                (view.root / "RESPONSE_SEQUENCE.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [item["item_key"] for item in response["internal_causal_guidance"]],
                ["hana_internal_response"],
            )
            self.assertEqual(
                response["internal_causal_guidance"][0]["projection_role"],
                "internal_causal_guidance",
            )
            self.assertEqual(
                response["internal_causal_guidance"][0]["guides_surface_item_key"],
                "hana_visible_response",
            )
            self.assertEqual(
                [item["item_key"] for item in response["surface_realization_items"]],
                ["hana_visible_response"],
            )
            self.assertEqual(
                response["surface_realization_items"][0]["guided_by_item_keys"],
                ["hana_internal_response"],
            )
            self.assertEqual(response["response_start_item_key"], "hana_visible_response")
            self.assertEqual(
                response["response_start_contract"]["response_start_kind"], "action"
            )
            gate = json.loads(
                (view.root / "zz_RESPONSE_START_GATE.json").read_text(encoding="utf-8")
            )
            self.assertEqual(gate["response_start_item_key"], "hana_visible_response")
            self.assertEqual(gate["item_projection_role"], "surface_realization")
            self.assertEqual(gate["response_start_scope"], "character")
            self.assertEqual(
                gate["causal_anchor_mode"],
                "completed_source_via_internal_guidance",
            )
            self.assertEqual(
                gate["owner_response_semantics"],
                "Hana's expression softens with restrained appreciation.",
            )

            authority["items"] = [
                authority["items"][0],
                authority["items"][1],
                authority["items"][3],
            ]
            with self.assertRaisesRegex(
                ContractValidationError, "no surface-realizable response item"
            ):
                WriterViewMaterializer(root / "invalid-views").materialize(
                    WriterViewInputV1(
                        world_id="world-test",
                        branch_id="branch-main",
                        scene_id="scene-private",
                        turn_id="turn-0002",
                        candidate_id="candidate-subtext-only",
                        route=SceneRoute.ORDINARY,
                        user_prompt="A supplied source contribution.",
                        primary_authority=authority,
                        current_state={"public_scene_state": "Two adults are present."},
                        characters={"hana": {"name": "Hana", "age": 38}},
                        relationships={},
                        recent_prose=(),
                        relevant_memories={},
                        voice_examples={},
                        craft_index={},
                        accepted_records=(),
                    )
                )

    def test_multiple_internal_items_map_to_earliest_reachable_dialogue(self) -> None:
        with TemporaryDirectory() as temporary:
            authority = sequence("source_action")
            authority["items"] = [
                {
                    "item_key": "source_action",
                    "owner_id": "character:ted",
                    "kind": "action",
                    "concise_meaning": "Ted completes the supplied action.",
                    "owner_response_semantics": None,
                    "protected_user_claim_keys": ["current_request"],
                    "protected_user_exact_quotes": ["supplied source"],
                    "durable_change_keys": [],
                },
                {
                    "item_key": "hana_perceives",
                    "owner_id": "character:hana",
                    "kind": "perception",
                    "concise_meaning": "Hana recognizes the immediate implication.",
                    "owner_response_semantics": "Hana recognizes the immediate implication.",
                    "causal_parent_item_key": "source_action",
                    "protected_user_claim_keys": [],
                    "protected_user_exact_quotes": [],
                    "durable_change_keys": [],
                },
                {
                    "item_key": "hana_decides",
                    "owner_id": "character:hana",
                    "kind": "private_state",
                    "concise_meaning": "Hana chooses a direct answer.",
                    "owner_response_semantics": "Hana privately chooses a direct answer.",
                    "causal_parent_item_key": "hana_perceives",
                    "protected_user_claim_keys": [],
                    "protected_user_exact_quotes": [],
                    "durable_change_keys": [],
                },
                {
                    "item_key": "hana_answers",
                    "owner_id": "character:hana",
                    "kind": "dialogue_intent",
                    "concise_meaning": "Hana gives the direct answer.",
                    "owner_response_semantics": "Hana gives a concise direct answer.",
                    "causal_parent_item_key": "hana_decides",
                    "protected_user_claim_keys": [],
                    "protected_user_exact_quotes": [],
                    "durable_change_keys": [],
                },
                {
                    "item_key": "return_floor",
                    "owner_id": None,
                    "kind": "stopping_boundary",
                    "concise_meaning": "Return the floor to Ted.",
                    "owner_response_semantics": None,
                    "causal_parent_item_key": "hana_answers",
                    "protected_user_claim_keys": [],
                    "protected_user_exact_quotes": [],
                    "durable_change_keys": [],
                },
            ]
            view = WriterViewMaterializer(Path(temporary) / "views").materialize(
                WriterViewInputV1(
                    world_id="world-test",
                    branch_id="branch-main",
                    scene_id="scene-dialogue",
                    turn_id="turn-0001",
                    candidate_id="candidate-dialogue-guidance",
                    route=SceneRoute.ORDINARY,
                    user_prompt="A supplied contribution.",
                    primary_authority=authority,
                    current_state={"public_scene_state": "Hana and Ted are present."},
                    characters={"hana": {"name": "Hana", "age": 38}},
                    relationships={},
                    recent_prose=(),
                    relevant_memories={},
                    voice_examples={},
                    craft_index={},
                    accepted_records=(),
                )
            )
            response = json.loads(
                (view.root / "RESPONSE_SEQUENCE.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [
                    item["guides_surface_item_key"]
                    for item in response["internal_causal_guidance"]
                ],
                ["hana_answers", "hana_answers"],
            )
            self.assertEqual(
                response["surface_realization_items"][0]["guided_by_item_keys"],
                ["hana_perceives", "hana_decides"],
            )
            self.assertEqual(
                response["response_start_contract"]["response_start_kind"],
                "dialogue_intent",
            )

    def test_ownerless_world_surface_is_explicit_and_never_fabricates_owner(self) -> None:
        with TemporaryDirectory() as temporary:
            authority = sequence("world_changes")
            authority["items"] = [
                {
                    "item_key": "world_changes",
                    "owner_id": None,
                    "kind": "material_continuity",
                    "concise_meaning": "The switched lamp dims.",
                    "owner_response_semantics": "The lamp's light visibly dims.",
                    "protected_user_claim_keys": [],
                    "protected_user_exact_quotes": [],
                    "durable_change_keys": [],
                },
                {
                    "item_key": "stop_after_change",
                    "owner_id": None,
                    "kind": "stopping_boundary",
                    "concise_meaning": "Stop after the visible change.",
                    "owner_response_semantics": None,
                    "causal_parent_item_key": "world_changes",
                    "protected_user_claim_keys": [],
                    "protected_user_exact_quotes": [],
                    "durable_change_keys": [],
                },
            ]
            view = WriterViewMaterializer(Path(temporary) / "views").materialize(
                WriterViewInputV1(
                    world_id="world-test",
                    branch_id="branch-main",
                    scene_id="scene-world",
                    turn_id="turn-0001",
                    candidate_id="candidate-world-start",
                    route=SceneRoute.ORDINARY,
                    user_prompt="Continue the visible change.",
                    primary_authority=authority,
                    current_state={"public_scene_state": "A lamp is on."},
                    characters={},
                    relationships={},
                    recent_prose=(),
                    relevant_memories={},
                    voice_examples={},
                    craft_index={},
                    accepted_records=(),
                )
            )
            gate = json.loads(
                (view.root / "zz_RESPONSE_START_GATE.json").read_text(encoding="utf-8")
            )
            self.assertEqual(gate["response_start_scope"], "world")
            self.assertIsNone(gate["response_start_owner_id"])
            self.assertEqual(gate["response_start_kind"], "material_continuity")
    def test_isolated_sillytavern_copy_excludes_user_data_and_forces_loopback(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "isolated"
            for directory in (
                source / "default",
                source / "data" / "default-user",
                source / "plugins" / "private-plugin",
                source / "node_modules",
                source / "node_modules" / "archiver" / "lib" / "plugins",
                source / "public",
                source / "src",
            ):
                directory.mkdir(parents=True, exist_ok=True)
            (source / "server.js").write_text("console.log('test');\n", encoding="utf-8")
            (source / "package.json").write_text("{}\n", encoding="utf-8")
            (source / "package-lock.json").write_text("{}\n", encoding="utf-8")
            (source / "config.yaml").write_text("listen: true\n", encoding="utf-8")
            (source / "default" / "config.yaml").write_text(
                "listen: false\n", encoding="utf-8"
            )
            (source / "data" / "default-user" / "private.txt").write_text(
                "must not copy", encoding="utf-8"
            )
            (source / "plugins" / "private-plugin" / "secret.txt").write_text(
                "must not copy", encoding="utf-8"
            )
            nested_plugin = (
                source / "node_modules" / "archiver" / "lib" / "plugins" / "zip.js"
            )
            nested_plugin.write_text(
                "module.exports = {};\n", encoding="utf-8"
            )
            node = root / "node.exe"
            node.write_text("stub", encoding="utf-8")
            manifest = stage_isolated_sillytavern(source, target)
            self.assertFalse(manifest["user_data_copied"])
            self.assertFalse(manifest["plugins_copied"])
            self.assertEqual(verify_isolated_sillytavern(target), manifest)
            self.assertEqual(list((target / "data").iterdir()), [])
            self.assertEqual(list((target / "plugins").iterdir()), [])
            self.assertFalse((target / "data" / "default-user" / "private.txt").exists())
            self.assertTrue(
                (
                    target
                    / "node_modules"
                    / "archiver"
                    / "lib"
                    / "plugins"
                    / "zip.js"
                ).is_file()
            )
            command = isolated_sillytavern_command(
                target,
                node_executable=node,
                port=8127,
            )
            self.assertIn("--no-listen", command)
            self.assertIn("--no-browserLaunchEnabled", command)
            self.assertIn(str(target / "data"), command)

    def test_pi_adapter_disables_generic_tools_and_counts_each_tool_loop_call(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "pi.cmd"
            executable.write_text("stub", encoding="utf-8")
            extension = root / "cera-scene-view.ts"
            extension.write_text("stub", encoding="utf-8")
            view = WriterViewMaterializer(root / "views").materialize(
                WriterViewInputV1(
                    world_id="world-test",
                    branch_id="branch-main",
                    scene_id="scene-kitchen",
                    turn_id="turn-0001",
                    candidate_id="candidate-one",
                    route=SceneRoute.ORDINARY,
                    user_prompt="Continue.",
                    primary_authority=sequence(),
                    current_state={"public_scene_state": "Hana is present."},
                    characters={},
                    relationships={},
                    recent_prose=(),
                    relevant_memories={},
                    voice_examples={},
                    craft_index={},
                    accepted_records=(),
                )
            )
            assistant_one = {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "toolCall", "name": "read"}],
                    "usage": {"input": 100, "cacheRead": 20, "output": 5},
                    "stopReason": "toolUse",
                },
            }
            assistant_two = {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Analysis outside the presentation envelope.\n"
                                "<cera_scene>Final scene prose.</cera_scene>"
                            ),
                        }
                    ],
                    "usage": {"input": 120, "cacheRead": 80, "output": 20},
                    "stopReason": "stop",
                },
            }
            stream = "\n".join(
                json.dumps(value)
                for value in (
                    {"type": "session", "id": "session-test"},
                    {"type": "turn_start"},
                    assistant_one,
                    {"type": "tool_execution_start", "toolName": "read"},
                    {"type": "tool_execution_end", "toolName": "read", "isError": False},
                    {"type": "turn_start"},
                    assistant_two,
                )
            )
            captured: dict[str, object] = {}

            def runner(command, cwd, environment, timeout, on_stdout_line):
                captured.update(command=tuple(command), cwd=cwd, environment=environment, timeout=timeout)
                for line in stream.splitlines(keepends=True):
                    on_stdout_line(line)
                return _ProcessResult(0, stream, "")

            ledger = PiProviderOperationLedger(
                (root / "provider_operations.jsonl").resolve(),
                maximum_operations=60,
            )
            adapter = PiSceneAdapter(
                pi_executable=executable,
                extension_path=extension,
                pi_version="0.84.1",
                operation_ledger=ledger,
                process_runner=runner,
            )
            request = PiSceneInvocationV1(
                route=SceneRoute.ORDINARY,
                purpose="writer",
                view=view,
                prompt="Write the scene.",
                candidate_id="candidate-one",
                session_dir=root / "sessions",
            )
            result = adapter.invoke(request)
            command = captured["command"]
            self.assertIn("--no-builtin-tools", command)
            self.assertIn("--no-extensions", command)
            self.assertIn("context", command)
            self.assertNotIn("context,read,list,find,search", command)
            self.assertIn("--approve", command)
            self.assertNotIn("--no-approve", command)
            self.assertNotIn("bash", command)
            self.assertNotIn("write", command)
            self.assertNotIn("edit", command)
            self.assertEqual(captured["environment"]["CERA_PI_MAX_TOOL_CALLS"], "1")
            self.assertEqual(captured["environment"]["CERA_PI_PURPOSE"], "writer")
            self.assertEqual(result.output_text, "Final scene prose.")
            self.assertEqual(result.writer_receipt.provider_operations, 2)
            self.assertEqual(result.writer_receipt.input_tokens, 220)
            self.assertEqual(result.writer_receipt.cached_input_tokens, 100)
            self.assertEqual(result.writer_receipt.tool_call_count, 1)
            self.assertTrue(captured["cwd"].name == "_cera_control")
            settings = json.loads(
                (captured["cwd"] / ".pi" / "settings.json").read_text(encoding="utf-8")
            )
            self.assertFalse(settings["retry"]["enabled"])
            self.assertEqual(settings["retry"]["maxRetries"], 0)
            self.assertEqual(settings["retry"]["provider"]["maxRetries"], 0)
            self.assertFalse(settings["compaction"]["enabled"])
            event_count = len(ledger.events)
            with self.assertRaisesRegex(StateConflictError, "purpose differs"):
                adapter.invoke(
                    PiSceneInvocationV1(
                        route=SceneRoute.ORDINARY,
                        purpose="recorder",
                        view=view,
                        prompt="Record the accepted scene.",
                        candidate_id="candidate-mismatched-purpose",
                        session_dir=root / "mismatched-sessions",
                    )
                )
            self.assertEqual(len(ledger.events), event_count)

    def test_pi_context_hides_python_custody_sequence_from_writer_only(self) -> None:
        extension = (
            Path(__file__).resolve().parents[1]
            / "integrations"
            / "pi"
            / "cera-scene-view.ts"
        ).read_text(encoding="utf-8")
        self.assertIn('const PURPOSE_ENV = "CERA_PI_PURPOSE"', extension)
        self.assertIn('new Set(["PRIMARY_SEQUENCE.json"])', extension)
        self.assertIn('process.env[PURPOSE_ENV] === "writer"', extension)
        self.assertIn("excluded from Writer access", extension)
        self.assertIn("writerContextPacket", extension)
        self.assertIn("RESPONSE REALIZATION AUTHORITY", extension)
        self.assertIn("COMPLETED OFF-PAGE SOURCE", extension)
        self.assertIn("DERIVED NONCANONICAL EXECUTION FOCUS", extension)
        self.assertIn("cera.writer_context_packet.v4", extension)

    def test_writer_output_accepts_raw_prose_and_strict_legacy_envelope(self) -> None:
        self.assertEqual(
            _parse_writer_output(
                "Analysis that is not visible.\n<cera_scene>  Visible prose.  </cera_scene>"
            ),
            "Visible prose.",
        )
        self.assertEqual(
            _parse_writer_output("  Visible raw prose.  "),
            "Visible raw prose.",
        )
        with self.assertRaisesRegex(StateConflictError, "partial or duplicate"):
            _parse_writer_output("<cera_scene>Visible but unclosed prose.")
        with self.assertRaisesRegex(StateConflictError, "scene envelope"):
            _parse_writer_output(
                "<cera_scene>One.</cera_scene><cera_scene>Two.</cera_scene>"
            )

    def test_pi_operation_ledger_charges_turn_start_and_rejects_automatic_work(self) -> None:
        with TemporaryDirectory() as temporary:
            ledger = PiProviderOperationLedger(
                (Path(temporary) / "operations.jsonl").resolve(),
                maximum_operations=12,
            )
            invocation = ledger.begin(
                candidate_id="candidate-one",
                purpose="writer",
                route="ordinary",
                request_sha256="a" * 64,
            )
            for index in range(2):
                ledger.observe_line(invocation, json.dumps({"type": "turn_start"}))
                ledger.observe_line(
                    invocation,
                    json.dumps(
                        {
                            "type": "message_end",
                            "message": {
                                "role": "assistant",
                                "usage": {"input": 100 + index, "cacheRead": 50},
                                "stopReason": "toolUse" if index == 0 else "stop",
                            },
                        }
                    ),
                )
            ledger.assert_completed(invocation, parsed_operations=2)
            ledger.finish(invocation, status="completed", output_sha256="b" * 64)
            self.assertEqual(ledger.operation_count, 2)
            forbidden = ledger.begin(
                candidate_id="candidate-two",
                purpose="recorder",
                route="ordinary",
                request_sha256="c" * 64,
            )
            ledger.observe_line(
                forbidden,
                json.dumps({"type": "auto_retry_start"}),
            )
            with self.assertRaisesRegex(StateConflictError, "forbidden automatic"):
                ledger.assert_completed(forbidden, parsed_operations=0)

    def test_ordinary_regenerate_reuses_sequence_and_accept_is_exactly_once(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator, planner, pi, store = self.make_runtime(root)
            first = coordinator.start_ordinary(turn())
            first_authority = first.candidate.primary_authority_json
            regenerated = coordinator.regenerate(
                first.review_id,
                feedback="Make the replacement more concise.",
            )
            self.assertEqual(len(planner.calls), 1)
            self.assertIsNotNone(regenerated.successor)
            successor = regenerated.successor
            assert successor is not None
            self.assertEqual(successor.candidate.primary_authority_json, first_authority)
            accepted = coordinator.accept(successor.review_id).review
            self.assertEqual(accepted.state, LeanReviewState.ACCEPTED)
            self.assertEqual(
                accepted.recording_attempt.status,
                RecordingStatus.COMPLETE,
            )
            head_before = store.load_head(world_id="world-test", branch_id="branch-main")
            with self.assertRaisesRegex(StateConflictError, "already terminal"):
                coordinator.accept(successor.review_id)
            restarted = LeanSceneStore(root / "world").load_head(
                world_id="world-test", branch_id="branch-main"
            )
            self.assertEqual(restarted.accepted_head_sha256, head_before.accepted_head_sha256)
            self.assertEqual(restarted.generation, 1)
            self.assertEqual(pi.calls[0].purpose, "writer")
            self.assertEqual(pi.calls[1].purpose, "writer")
            self.assertIn("noncanonical creator guidance", pi.calls[1].prompt)
            self.assertIn("more concise", pi.calls[1].prompt)
            self.assertFalse((pi.calls[1].view.root / "PRIMARY_SEQUENCE.json").exists())
            self.assertTrue((pi.calls[1].view.root / "USER_PROMPT.txt").is_file())
            self.assertEqual(pi.calls[2].purpose, "recorder")
            self.assertEqual(pi.calls[2].view.purpose, "recorder")
            self.assertTrue((pi.calls[2].view.root / "PRIMARY_SEQUENCE.json").is_file())
            self.assertTrue((pi.calls[2].view.root / "recent_prose" / "0001.txt").is_file())

    def test_writer_view_links_accepted_prose_once_without_losing_custody(self) -> None:
        with TemporaryDirectory() as temporary:
            coordinator, _, pi, _ = self.make_runtime(Path(temporary))
            first = coordinator.start_ordinary(turn(source="First accepted turn."))
            coordinator.accept(first.review_id)
            coordinator.start_ordinary(turn(source="Second turn."))

            second_writer = [call for call in pi.calls if call.purpose == "writer"][-1]
            accepted = json.loads(
                (second_writer.view.root / "accepted_records" / "0001.json").read_text(
                    encoding="utf-8"
                )
            )
            receipt = accepted["receipt"]
            self.assertNotIn("exact_accepted_prose", receipt)
            self.assertNotIn("exact_user_source", receipt)
            self.assertNotIn("primary_authority_json", receipt)
            self.assertNotIn("writer_receipt", receipt)
            self.assertEqual(
                receipt["exact_accepted_prose_sha256"],
                first.candidate.story_text_sha256,
            )
            self.assertEqual(
                receipt["exact_user_source_sha256"], text_sha256("First accepted turn.")
            )
            self.assertIn("ordinary_record", accepted)

    def test_decline_has_zero_accepted_effect_and_replan_is_only_second_planner_call(self) -> None:
        with TemporaryDirectory() as temporary:
            coordinator, planner, _, store = self.make_runtime(Path(temporary))
            first = coordinator.start_ordinary(turn())
            replanned = coordinator.replan(first.review_id, feedback="Let Hana be more direct.")
            self.assertEqual(len(planner.calls), 2)
            successor = replanned.successor
            assert successor is not None
            coordinator.decline(successor.review_id)
            head = store.load_head(world_id="world-test", branch_id="branch-main")
            self.assertEqual(head.generation, 0)
            self.assertIsNone(head.accepted_turn_id)

    def test_recorder_failure_and_repair_do_not_regenerate_or_recommit_scene(self) -> None:
        failures = {1}

        def fault(_accepted, attempt_number):
            return "injected_recorder_failure" if attempt_number in failures else None

        with TemporaryDirectory() as temporary:
            coordinator, planner, pi, store = self.make_runtime(Path(temporary), fault=fault)
            review = coordinator.start_ordinary(turn())
            accepted = coordinator.accept(review.review_id).review
            receipt_sha = accepted.accepted_receipt.receipt_sha256
            candidate_sha = accepted.candidate.candidate_sha256
            self.assertEqual(accepted.recording_attempt.status, RecordingStatus.PENDING_REPAIR)
            self.assertEqual(store.load_head(world_id="world-test", branch_id="branch-main").generation, 1)
            repaired = coordinator.repair_recording(review.review_id).review
            self.assertEqual(repaired.recording_attempt.status, RecordingStatus.COMPLETE)
            self.assertEqual(repaired.accepted_receipt.receipt_sha256, receipt_sha)
            self.assertEqual(repaired.candidate.candidate_sha256, candidate_sha)
            self.assertEqual(len(planner.calls), 1)
            self.assertEqual([value.purpose for value in pi.calls], ["writer", "recorder"])
            recorder_calls = [value for value in pi.calls if value.purpose == "recorder"]
            self.assertTrue(
                all(value.accepted_parent_session is None for value in recorder_calls)
            )
            self.assertTrue(
                all(
                    len(list((value.view.root / "accepted_records").glob("*.json"))) == 0
                    and len(list((value.view.root / "recent_prose").glob("*.txt"))) == 1
                    for value in recorder_calls
                )
            )
            self.assertEqual(
                json.loads(
                    (recorder_calls[0].view.root / "CURRENT_STATE.json").read_text(
                        encoding="utf-8"
                    )
                )["recording_phase"],
                "post_accept",
            )
            self.assertIn("PRIMARY_SEQUENCE.json", recorder_calls[0].prompt)
            self.assertEqual(
                list((recorder_calls[0].view.root / "characters").glob("*.json")),
                [],
            )

    def test_recorder_accepts_one_exact_json_fence_without_commentary(self) -> None:
        with TemporaryDirectory() as temporary:
            coordinator, _, pi, _ = self.make_runtime(Path(temporary))
            review = coordinator.start_ordinary(turn())
            payload = {
                "secondary_canon": [],
                "resulting_public_state": "Hana has answered.",
                "relationship_changes": [],
                "knowledge_changes": [],
                "durable_changes": [],
                "unresolved_threads": [],
            }
            pi.recorder_outputs.append(
                "```json\n" + json.dumps(payload, separators=(",", ":")) + "\n```"
            )

            accepted = coordinator.accept(review.review_id).review

            self.assertEqual(accepted.recording_attempt.status, RecordingStatus.COMPLETE)
            self.assertEqual(
                _parse_recorder_payload(
                    "Recorder result follows.\n```json\n"
                    + json.dumps(payload, separators=(",", ":"))
                    + "\n```"
                ),
                payload,
            )
            with self.assertRaises(json.JSONDecodeError):
                _parse_recorder_payload(
                    "```json\n{}\n```\n```json\n{}\n```"
                )

    def test_recorder_transport_failure_returns_accepted_pending_repair(self) -> None:
        with TemporaryDirectory() as temporary:
            coordinator, _, pi, store = self.make_runtime(Path(temporary))
            review = coordinator.start_ordinary(turn())
            pi.fail_next_recorder = True

            accepted = coordinator.accept(review.review_id).review

            self.assertEqual(accepted.state, LeanReviewState.ACCEPTED)
            self.assertIsNotNone(accepted.accepted_receipt)
            self.assertEqual(
                accepted.recording_attempt.status,
                RecordingStatus.PENDING_REPAIR,
            )
            self.assertEqual(
                store.load_head(world_id="world-test", branch_id="branch-main").generation,
                1,
            )
            repaired = coordinator.repair_recording(review.review_id).review
            self.assertEqual(repaired.recording_attempt.status, RecordingStatus.COMPLETE)

    def test_latest_recording_repair_resumes_from_authoritative_head(self) -> None:
        failures = {1}

        def fault(_accepted, attempt_number):
            return "injected_recorder_failure" if attempt_number in failures else None

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator, _, _, store = self.make_runtime(root, fault=fault)
            review = coordinator.start_ordinary(turn())
            accepted = coordinator.accept(review.review_id).review
            self.assertEqual(accepted.recording_attempt.status, RecordingStatus.PENDING_REPAIR)

            restarted = LeanPiSceneCoordinator(
                store=LeanSceneStore(root / "world"),
                planner=FakePlanner(),
                writer_views=WriterViewMaterializer(root / "restart-views"),
                pi=FakePi(),  # type: ignore[arg-type]
                session_root=root / "restart-sessions",
            )
            attempt = restarted.repair_latest_recording(turn())

            self.assertEqual(attempt.status, RecordingStatus.COMPLETE)
            self.assertEqual(attempt.attempt_number, 2)
            self.assertEqual(
                store.load_head(world_id="world-test", branch_id="branch-main").generation,
                1,
            )

    def test_existing_recorder_output_structurally_repairs_scalar_secondary_canon(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator, _, _, store = self.make_runtime(root)
            review = coordinator.start_ordinary(turn())
            accepted = store.accept(review.candidate)
            payload = {
                "secondary_canon": "Hana leaves the conversational floor open.",
                "resulting_public_state": "Hana has answered.",
                "relationship_changes": [],
                "knowledge_changes": [],
                "durable_changes": [],
                "unresolved_threads": ["Ted may respond."],
            }
            output = "```json\n" + json.dumps(payload, separators=(",", ":")) + "\n```"
            store.mark_recording_failure(
                accepted,
                recorder_request_sha256="a" * 64,
                recorder_output_sha256=text_sha256(output),
                provider_operations=2,
                failure_code="recorder_contract:ContractValidationError",
            )

            attempt = repair_latest_ordinary_recording_from_output(
                store=store,
                turn=turn(),
                output_text=output,
            )

            self.assertEqual(attempt.status, RecordingStatus.COMPLETE)
            self.assertEqual(attempt.provider_operations, 0)
            record = store.recent_accepted_payloads(
                world_id="world-test", branch_id="branch-main"
            )[0]["ordinary_record"]
            self.assertEqual(
                record["secondary_canon"],
                ["Hana leaves the conversational floor open."],
            )

    def test_recorder_cannot_author_python_custody_fields(self) -> None:
        with TemporaryDirectory() as temporary:
            coordinator, _, pi, store = self.make_runtime(Path(temporary))
            review = coordinator.start_ordinary(turn())
            pi.recorder_outputs.append(
                json.dumps(
                    {
                        "secondary_canon": [],
                        "resulting_public_state": "Hana has answered.",
                        "relationship_changes": [],
                        "knowledge_changes": [],
                        "durable_changes": [],
                        "unresolved_threads": [],
                        "accepted_turn_id": "model-authored-custody",
                    },
                    separators=(",", ":"),
                )
            )

            accepted = coordinator.accept(review.review_id).review

            self.assertEqual(accepted.state, LeanReviewState.ACCEPTED)
            self.assertEqual(
                accepted.recording_attempt.status,
                RecordingStatus.PENDING_REPAIR,
            )
            self.assertTrue(
                accepted.recording_attempt.failure_code.startswith("recorder_contract:")
            )
            self.assertIn(
                "ordinary Recorder fields changed",
                accepted.recording_attempt.failure_code,
            )
            self.assertEqual(
                store.load_head(world_id="world-test", branch_id="branch-main").generation,
                1,
            )
            repaired = coordinator.repair_recording(review.review_id).review
            self.assertEqual(repaired.recording_attempt.status, RecordingStatus.COMPLETE)
            self.assertEqual(
                store.load_head(world_id="world-test", branch_id="branch-main").generation,
                1,
            )

    def test_session_promotion_failure_cannot_hide_accepted_receipt(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            planner = FakePlanner()
            pi = FakePi()
            store = FailOncePromotionStore(root / "world")
            coordinator = LeanPiSceneCoordinator(
                store=store,
                planner=planner,
                writer_views=WriterViewMaterializer(root / "views"),
                pi=pi,  # type: ignore[arg-type]
                session_root=root / "sessions",
            )
            review = coordinator.start_ordinary(turn())

            accepted = coordinator.accept(review.review_id).review

            self.assertEqual(accepted.state, LeanReviewState.ACCEPTED)
            self.assertIsNotNone(accepted.accepted_receipt)
            self.assertEqual(
                accepted.recording_attempt.status,
                RecordingStatus.PENDING_REPAIR,
            )
            self.assertEqual(
                store.load_head(world_id="world-test", branch_id="branch-main").generation,
                1,
            )
            repaired = coordinator.repair_recording(review.review_id).review
            self.assertEqual(repaired.recording_attempt.status, RecordingStatus.COMPLETE)
            self.assertEqual(
                store.load_accepted_pi_session(
                    world_id="world-test",
                    branch_id="branch-main",
                ).accepted_turn_id,
                accepted.accepted_receipt.accepted_turn_id,
            )

    def test_restart_rejects_tampered_historical_accepted_receipt(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator, _, _, store = self.make_runtime(root)
            first = coordinator.start_ordinary(turn(source="First accepted turn."))
            coordinator.accept(first.review_id)
            second = coordinator.start_ordinary(turn(source="Second accepted turn."))
            coordinator.accept(second.review_id)
            receipt_paths = sorted((root / "world").rglob("ACCEPTED_RECEIPT.json"))
            self.assertEqual(len(receipt_paths), 2)
            payload = json.loads(receipt_paths[0].read_text(encoding="utf-8"))
            payload["exact_accepted_prose"] = "Tampered accepted prose."
            changed_sha = text_sha256(payload["exact_accepted_prose"])
            payload["exact_accepted_prose_sha256"] = changed_sha
            payload["writer_receipt"]["output_sha256"] = changed_sha
            receipt_paths[0].write_text(canonical_json(payload), encoding="utf-8")

            with self.assertRaisesRegex(StateConflictError, "hash chain"):
                store.load_head(world_id="world-test", branch_id="branch-main")

    def test_adult_dual_record_projection_links_full_event_and_route_can_transition(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator, planner, pi, store = self.make_runtime(root)
            pi.writer_outputs.extend(
                (
                    "Ordinary accepted continuity.",
                    "Protected adult accepted continuity.",
                    "Ordinary continuation after the projection.",
                )
            )
            ordinary_one = coordinator.start_ordinary(turn(source="Ordinary one."))
            coordinator.accept(ordinary_one.review_id)
            adult = coordinator.start_adult(turn(source="Adult transition.", adult=True))
            adult_result = coordinator.accept(adult.review_id).review
            self.assertEqual(adult_result.recording_attempt.status, RecordingStatus.COMPLETE)
            adult_dir = next(
                path
                for path in (root / "world").rglob("ADULT_FULL_RECORD.json")
            ).parent
            full = json.loads((adult_dir / "ADULT_FULL_RECORD.json").read_text(encoding="utf-8"))
            projection = json.loads(
                (adult_dir / "ADULT_CODEX_PROJECTION.json").read_text(encoding="utf-8")
            )
            self.assertEqual(projection["adult_full_record_sha256"], canonical_sha256(full))
            self.assertIn(
                projection["items"][0]["event_key"],
                {value["event_key"] for value in full["events"]},
            )
            base_turn = turn(source="Ordinary two.")
            context = AcceptedBranchContextProvider(
                store,
                PiSceneContextSeedV1(
                    world_id=base_turn.world_id,
                    branch_id=base_turn.branch_id,
                    scene_id=base_turn.scene_id,
                    accepted_present_character_ids=("character:ted", "character:hana"),
                    public_scene_state="Ted and Hana are talking in the kitchen.",
                    characters=base_turn.characters,
                    relationships=base_turn.relationships,
                    relevant_memories=base_turn.relevant_memories,
                    voice_examples=base_turn.voice_examples,
                    ordinary_craft_index=base_turn.craft_index,
                    adult_craft_index={"entries": ["adult-pacing"]},
                    adult_handoff=adult_handoff(),
                ),
            )
            ordinary_context = context(
                SceneRoute.ORDINARY,
                "Ordinary two.",
                (),
            )
            self.assertEqual(
                ordinary_context.recent_prose,
                (ordinary_one.candidate.story_text,),
            )
            self.assertNotIn(
                adult_result.candidate.story_text,
                ordinary_context.recent_prose,
            )
            ordinary_two = coordinator.start_ordinary(ordinary_context)
            ordinary_two_writer = [
                call for call in pi.calls if call.purpose == "writer"
            ][-1]
            accepted_adult = json.loads(
                (
                    ordinary_two_writer.view.root
                    / "accepted_records"
                    / "0002.json"
                ).read_text(encoding="utf-8")
            )
            self.assertIn("adult_projection", accepted_adult)
            self.assertNotIn("adult_full_record", accepted_adult)
            self.assertNotIn("exact_user_source", accepted_adult["receipt"])
            coordinator.accept(ordinary_two.review_id)
            self.assertEqual(
                store.load_head(world_id="world-test", branch_id="branch-main").generation,
                3,
            )
            self.assertEqual(len(planner.calls), 2)
            self.assertEqual(
                [call.route for call in pi.calls if call.purpose == "writer"],
                [SceneRoute.ORDINARY, SceneRoute.ADULT, SceneRoute.ORDINARY],
            )
            writer_calls = [call for call in pi.calls if call.purpose == "writer"]
            self.assertIsNone(writer_calls[1].accepted_parent_session)
            self.assertIsNone(writer_calls[2].accepted_parent_session)

    def test_reset_rehydrates_from_accepted_state_without_reusing_rejected_session(self) -> None:
        with TemporaryDirectory() as temporary:
            coordinator, _, pi, _ = self.make_runtime(Path(temporary))
            first = coordinator.start_ordinary(turn(source="First."))
            coordinator.accept(first.review_id)
            second = coordinator.start_ordinary(turn(source="Second."))
            reset = coordinator.regenerate(second.review_id, force_rehydrate=True)
            successor = reset.successor
            assert successor is not None
            writer_calls = [value for value in pi.calls if value.purpose == "writer"]
            self.assertIsNotNone(writer_calls[1].accepted_parent_session)
            self.assertTrue(writer_calls[1].force_rehydrate)
            self.assertTrue(writer_calls[2].force_rehydrate)
            self.assertIsNotNone(writer_calls[2].accepted_parent_session)
            self.assertIsNone(successor.candidate.writer_receipt.parent_session_id_sha256)
            self.assertTrue(successor.candidate.writer_receipt.rehydrated)

    def test_all_post_accept_writers_rehydrate_without_forking_soft_lineage(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, _, _, store = self.make_runtime(root)
            initial = first.start_ordinary(turn(source="First."))
            first.accept(initial.review_id)

            restarted_pi = FakePi()
            restarted = LeanPiSceneCoordinator(
                store=store,
                planner=FakePlanner(),
                writer_views=WriterViewMaterializer(root / "restart-views"),
                pi=restarted_pi,  # type: ignore[arg-type]
                session_root=root / "restart-sessions",
            )
            after_restart = restarted.start_ordinary(turn(source="Second."))
            first_restarted_call = next(
                value for value in restarted_pi.calls if value.purpose == "writer"
            )
            self.assertIsNotNone(first_restarted_call.accepted_parent_session)
            self.assertTrue(first_restarted_call.force_rehydrate)
            self.assertTrue(after_restart.candidate.writer_receipt.rehydrated)
            self.assertIsNone(
                after_restart.candidate.writer_receipt.parent_session_id_sha256
            )
            restarted.accept(after_restart.review_id)

            third = restarted.start_ordinary(turn(source="Third."))
            writer_calls = [
                value for value in restarted_pi.calls if value.purpose == "writer"
            ]
            self.assertIsNotNone(writer_calls[-1].accepted_parent_session)
            self.assertTrue(writer_calls[-1].force_rehydrate)
            self.assertTrue(third.candidate.writer_receipt.rehydrated)
            self.assertIsNone(third.candidate.writer_receipt.parent_session_id_sha256)

    def test_complete_fake_smoke_shape_reaches_four_accepted_turns(self) -> None:
        failures = {(2, 1)}

        def fault(accepted, attempt_number):
            return (
                "injected_smoke_recorder_failure"
                if (accepted.generation, attempt_number) in failures
                else None
            )

        with TemporaryDirectory() as temporary:
            coordinator, planner, pi, store = self.make_runtime(Path(temporary), fault=fault)
            first = coordinator.start_ordinary(turn(source="Ordinary one."))
            coordinator.accept(first.review_id)

            second = coordinator.start_adult(turn(source="Adult one.", adult=True))
            accepted_second = coordinator.accept(second.review_id).review
            self.assertEqual(
                accepted_second.recording_attempt.status,
                RecordingStatus.PENDING_REPAIR,
            )
            self.assertEqual(
                coordinator.repair_recording(second.review_id).review.recording_attempt.status,
                RecordingStatus.COMPLETE,
            )

            third = coordinator.start_adult(turn(source="Adult two.", adult=True))
            coordinator.accept(third.review_id)

            fourth = coordinator.start_ordinary(turn(source="Ordinary two."))
            original_authority = fourth.candidate.primary_authority_sha256
            regenerated = coordinator.regenerate(fourth.review_id, force_rehydrate=True)
            successor = regenerated.successor
            assert successor is not None
            self.assertEqual(successor.candidate.primary_authority_sha256, original_authority)
            coordinator.accept(successor.review_id)

            head = store.load_head(world_id="world-test", branch_id="branch-main")
            self.assertEqual(head.generation, 4)
            accepted = store.recent_accepted_payloads(
                world_id="world-test",
                branch_id="branch-main",
                adult_full=True,
            )
            self.assertEqual(
                [value["receipt"]["route"] for value in accepted],
                ["ordinary", "adult", "adult", "ordinary"],
            )
            self.assertTrue(accepted[-1]["receipt"]["writer_receipt"]["rehydrated"])
            self.assertEqual(len(planner.calls), 2)
            self.assertEqual(
                [value.purpose for value in pi.calls].count("writer"),
                5,
            )
            self.assertEqual(
                [value.purpose for value in pi.calls].count("recorder"),
                4,
            )


class PiSceneHttpTests(unittest.TestCase):
    @staticmethod
    def _request(url: str, *, token: str | None = None, payload=None, content_type="application/json"):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": content_type}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(url, data=data, headers=headers, method="GET" if payload is None else "POST")
        with urlopen(request, timeout=5) as response:
            return response.headers, json.loads(response.read().decode("utf-8"))

    def test_loopback_http_requires_auth_json_and_exposes_review_actions(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            planner = FakePlanner()
            pi = FakePi()
            coordinator = LeanPiSceneCoordinator(
                store=LeanSceneStore(root / "world"),
                planner=planner,
                writer_views=WriterViewMaterializer(root / "views"),
                pi=pi,  # type: ignore[arg-type]
                session_root=root / "sessions",
            )
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                session_id="pi-http-test",
                context_provider=lambda route, source, messages: turn(source=source),
            )
            token = "local-test-token-0123456789abcdef"
            server = build_pi_scene_server(
                adapter,
                PiSceneServerConfigV1(
                    host="127.0.0.1",
                    port=0,
                    authorization_token=token,
                    approved_origins=("http://127.0.0.1:8000",),
                ),
            )
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            port = server.server_address[1]
            try:
                with self.assertRaises(HTTPError) as unauthorized:
                    self._request(f"http://127.0.0.1:{port}/health")
                self.assertEqual(unauthorized.exception.code, 401)
                headers, health = self._request(
                    f"http://127.0.0.1:{port}/health", token=token
                )
                self.assertTrue(headers["Content-Type"].startswith("application/json"))
                self.assertTrue(health["loopback_only"])
                payload = {
                    "model": PI_SCENE_ORDINARY_MODEL,
                    "messages": [{"role": "user", "content": "Continue."}],
                    "stream": False,
                    "cera_profile_id": PI_SCENE_PROFILE,
                    "cera_session_id": "pi-http-test",
                }
                _, response = self._request(
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    token=token,
                    payload=payload,
                )
                review_id = response["cera"]["provisional_review_id"]
                provider_counts = (len(planner.calls), len(pi.calls))
                _, review = self._request(
                    f"http://127.0.0.1:{port}/v1/cera/reviews/{review_id}", token=token
                )
                _, recovered_review = self._request(
                    f"http://127.0.0.1:{port}/v1/cera/reviews/{review_id}", token=token
                )
                self.assertEqual(recovered_review, review)
                self.assertEqual((len(planner.calls), len(pi.calls)), provider_counts)
                self.assertTrue(review["accept_enabled"])
                self.assertTrue(review["regenerate_enabled"])
                self.assertTrue(review["replan_enabled"])
                _, decision = self._request(
                    f"http://127.0.0.1:{port}/v1/cera/reviews/{review_id}/decision",
                    token=token,
                    payload={"action": "accept"},
                )
                self.assertEqual(decision["review"]["state"], "accepted")
                self.assertEqual(decision["review"]["recording_status"], "complete")
                with self.assertRaises(HTTPError) as wrong_type:
                    self._request(
                        f"http://127.0.0.1:{port}/v1/chat/completions",
                        token=token,
                        payload=payload,
                        content_type="text/plain",
                    )
                self.assertEqual(wrong_type.exception.code, 415)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
