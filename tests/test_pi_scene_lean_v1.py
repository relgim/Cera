from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
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
from cera.pi_scene.operation_ledger import PiProviderOperationLedger
from cera.pi_scene.pi_adapter import (
    ADULT_RECORDER_SYSTEM_PROMPT,
    ADULT_WRITER_SYSTEM_PROMPT,
    ORDINARY_RECORDER_SYSTEM_PROMPT,
    ORDINARY_WRITER_SYSTEM_PROMPT,
    PiSceneInvocationResultV1,
    PiSceneInvocationV1,
    _parse_writer_output,
    _ProcessResult,
)
from cera.pi_scene.readable_debug import ReadablePiSceneDebugLog
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
from cera.providers import deepseek_composer_candidate
from cera.sequence_first.contracts import (
    ItemKind,
    SequenceDraftV1,
    SequenceItemV1,
)
from cera.serialization import canonical_json, canonical_sha256, text_sha256
from scripts.run_pi_scene_lean_server import (
    _initialize_live_runtime_roots,
    _seed_live_runtime_state,
)
from tests.provider_fakes import OfflinePiSceneAdapter


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


def branch_change(
    *,
    label: str,
    subject_ids: list[str],
    visibility: str = "public",
    knowledge_owner_id: str | None = None,
) -> dict[str, object]:
    return {
        "accepted_turn_id": "turn:accepted:0001",
        "change_key": label,
        "kind": "character_development",
        "subject_ids": subject_ids,
        "concise_change": f"Accepted branch change {label}.",
        "target_key": subject_ids[0],
        "visibility": visibility,
        "knowledge_owner_id": knowledge_owner_id,
        "source_kind": "ordinary_primary_authority",
    }


def no_genesis_character(
    character_id: str,
    *,
    accepted_branch_changes: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "character_id": character_id,
        "genesis_record_available": False,
        "accepted_branch_changes": accepted_branch_changes or [],
    }


def genesis_claim_bundle(
    character_id: str,
    *,
    label: str,
    records: int,
    payload_chars: int,
) -> dict[str, object]:
    projections: list[dict[str, object]] = []
    for index in range(records):
        payload_json = canonical_json(
            {
                "omitted_writer_payload": (
                    f"CERA-OMITTED-PAYLOAD-{label}-{index:03d}-"
                    + ("P" * payload_chars)
                )
            }
        )
        record = {
            "schema_version": "cera.genesis_record.v1",
            "record_id": f"record:{label}-{index:03d}",
            "record_version": 1,
            "record_type": "character_profile",
            "epistemic_layer": "objective_fact",
            "truth_status": "objective",
            "claim": (
                f"CERA-{label.upper()}-CLAIM-{index:03d}: the exact accepted "
                "character guidance remains authoritative."
            ),
            "authority": "creator",
            "subject_ids": [character_id],
            "owner_id": None,
            "knowledge_owner_ids": [],
            "visibility": "system_private",
            "knowledge_route": "creator_seed",
            "certainty": "established",
            "content_class": "ordinary",
            "adult_eligibility": "not_applicable",
            "story_start_presence": "not_applicable",
            "relationship_from_id": None,
            "relationship_to_id": None,
            "source_refs": [f"source:{label}"],
            "valid_from": None,
            "valid_to": None,
            "supersedes": [],
            "tags": ["writer_projection_test"],
            "expandable_sections": ["payload"],
            "payload_json": payload_json,
        }
        projections.append(
            {
                "schema_version": "cera.pi_scene_genesis_record_projection.v1",
                "_cera_revision": 1,
                "genesis_revision_id": "genesis-revision:test",
                "source_relative_path": f"modules/{label}.json",
                "source_content_sha256": text_sha256(f"source:{label}"),
                "visibility": "system_private",
                "knowledge_owner_id": None,
                "record": record,
            }
        )
    return {
        "character_id": character_id,
        "genesis_record_projections": projections,
    }


def run_pi_context_extension(
    *,
    root: Path,
    view_root: Path,
    purpose: str = "writer",
) -> subprocess.CompletedProcess[str]:
    harness = root / "node-harness"
    if not harness.exists():
        harness.mkdir()
        repository = Path(__file__).resolve().parents[1]
        for name in ("cera-scene-context.ts", "cera-scene-view.ts"):
            shutil.copyfile(repository / "integrations" / "pi" / name, harness / name)
        (harness / "package.json").write_text(
            json.dumps({"type": "module"}),
            encoding="utf-8",
        )
        typebox = harness / "node_modules" / "typebox"
        typebox.mkdir(parents=True)
        (typebox / "package.json").write_text(
            json.dumps(
                {
                    "name": "typebox",
                    "version": "0.0.0-test",
                    "type": "module",
                    "exports": "./index.js",
                }
            ),
            encoding="utf-8",
        )
        (typebox / "index.js").write_text(
            """
export const Type = {
  Object: (value) => value,
  Optional: (value) => value,
  String: (value = {}) => value,
};
""".strip()
            + "\n",
            encoding="utf-8",
        )
    script = """
import { pathToFileURL } from 'node:url';
const module = await import(pathToFileURL(process.argv[1]).href);
const tools = new Map();
const pi = {
  on: () => undefined,
  registerTool: (tool) => tools.set(tool.name, tool),
};
module.default(pi);
try {
  const result = await tools.get('context').execute();
  const text = result.content[0].text;
  const toolCallId = `call_${'c'.repeat(96)}`;
  const requestBody = (systemPrompt, invocationPrompt) => ({
    model: 'deepseek-v4-flash',
    messages: [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: invocationPrompt },
      {
        role: 'assistant',
        content: null,
        tool_calls: [{
          id: toolCallId,
          type: 'function',
          function: { name: 'context', arguments: '{}' },
        }],
        reasoning_content: '',
      },
      { role: 'tool', content: text, tool_call_id: toolCallId },
    ],
    stream: true,
    stream_options: { include_usage: true },
    max_completion_tokens: Number(process.env.CERA_TEST_MAX_OUTPUT_TOKENS),
    tools: [{
      type: 'function',
      function: {
        name: 'context',
        description: 'Load the complete bounded Python-materialized CERA scene view in one call.',
        parameters: { type: 'object', properties: {} },
        strict: false,
      },
    }],
    thinking: { type: 'disabled' },
  });
  const requestEstimate = JSON.stringify(requestBody(
    process.env.CERA_TEST_SYSTEM_PROMPT,
    process.env.CERA_TEST_INVOCATION_PROMPT,
  ));
  const reserveProofRequest = JSON.stringify(requestBody(
    process.env.CERA_TEST_MAX_SYSTEM_PROMPT,
    process.env.CERA_TEST_MAX_INVOCATION_PROMPT,
  ));
  const serializedContextBytes = Buffer.byteLength(JSON.stringify(text), 'utf8');
  console.log(JSON.stringify({
    bytes: Buffer.byteLength(text, 'utf8'),
    serializedContextBytes,
    serializedContextLimitBytes: result.details.serializedContextLimitBytes,
    nonContextRequestReserveBytes: result.details.nonContextRequestReserveBytes,
    maximumRequestBytes: result.details.maximumRequestBytes,
    files: result.details.files,
    packet: result.details.packet,
    freshSessionRequestEstimateBytes: Buffer.byteLength(requestEstimate, 'utf8'),
    freshSessionNonContextBytes:
      Buffer.byteLength(requestEstimate, 'utf8') - serializedContextBytes,
    maximumEnvelopeNonContextBytes:
      Buffer.byteLength(reserveProofRequest, 'utf8') - serializedContextBytes,
    hasSakuraClaim: text.includes('CERA-SAKURA-CLAIM-120'),
    hasHanaClaim: text.includes('CERA-HANA-CLAIM-120'),
    hasSakuraOwnerPrivateClaim: text.includes('CERA-SAKURA-OWNER-PRIVATE-CLAIM'),
    hasHanaOwnerPrivateClaim: text.includes('CERA-HANA-OWNER-PRIVATE-CLAIM'),
    hasForeignOwnerPrivateClaim: text.includes('CERA-FOREIGN-OWNER-PRIVATE-CLAIM'),
    hasOmittedPayload: text.includes('CERA-OMITTED-PAYLOAD'),
    hasSixthTurn: text.includes('CERA-RECENT-TURN-6'),
  }));
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(7);
}
"""
    environment = dict(os.environ)
    environment.update(
        {
            "CERA_PI_VIEW_ROOT": str(view_root),
            "CERA_PI_PURPOSE": purpose,
            "CERA_PI_MAX_TOOL_CALLS": "1",
            "CERA_TEST_SYSTEM_PROMPT": ADULT_WRITER_SYSTEM_PROMPT,
            "CERA_TEST_INVOCATION_PROMPT": (
                "Call context exactly once and render the complete authorized scene."
            ),
            "CERA_TEST_MAX_SYSTEM_PROMPT": max(
                (ORDINARY_WRITER_SYSTEM_PROMPT, ADULT_WRITER_SYSTEM_PROMPT),
                key=lambda value: len(json.dumps(value, ensure_ascii=False).encode("utf-8")),
            ),
            "CERA_TEST_MAX_INVOCATION_PROMPT": (
                "Call context exactly once, use its complete confined Writer view, "
                "then produce the complete scene now without another tool call. This is "
                "the only complete repair attempt. The prior candidate was rejected for "
                "current_data_conflict at the exact candidate phrase "
                + json.dumps("\\" * 1_000)
                + ": "
                + ("\\" * 2_000)
                + " The cited conflict is the repair target, not a replacement for any other "
                "authority in the unchanged Writer view. Produce a fresh complete scene from "
                "the unchanged Writer view; do not quote, patch, or continue the rejected "
                "prose. Before returning, silently re-audit the entire fresh scene against "
                "every ordered surface_realization_item: each required action must actually "
                "occur rather than be promised, intended, summarized, or deferred; every "
                "required communication must preserve its authorized speaker, addressee, and "
                "channel; preserve every cast or capability restriction and every continuously "
                "held boundary without a temporary breach; and make the required final beat "
                "the output's final sentence or paragraph, with no aftermath, waiting, "
                "ambience, summary, restatement, or other content after it."
            ),
            "CERA_TEST_MAX_OUTPUT_TOKENS": str(
                deepseek_composer_candidate().maximum_output_tokens
            ),
        }
    )
    return subprocess.run(
        [
            "node",
            "--no-warnings",
            "--experimental-strip-types",
            "--input-type=module",
            "--eval",
            script,
            str(harness / "cera-scene-view.ts"),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )


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
    external_provider_boundary = False

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
    external_provider_boundary = False

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
            if request.accepted_parent_session is None
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
            cached_input_tokens=(
                40
                if request.accepted_parent_session is not None
                and not request.force_rehydrate
                else 0
            ),
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
            external_provider_boundary = False

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

    def test_writer_prompts_preserve_autonomy_and_presentation_contract(self) -> None:
        for prompt in (ORDINARY_WRITER_SYSTEM_PROMPT, ADULT_WRITER_SYSTEM_PROMPT):
            self.assertIn("tool named context directly exactly once", prompt)
            self.assertIn("do not call a tool named invoke", prompt)
            self.assertIn("zz_CURRENT_TURN_AUTHORITY.json", prompt)
            self.assertIn("presentation_contract.source_usage", prompt)
            self.assertIn("fact_scope", prompt)
            self.assertIn("Return complete visible prose only", prompt)
            self.assertNotIn("opening sentence", prompt.lower())
        self.assertIn(
            "Ted has exactly two ordinary content protections", ORDINARY_WRITER_SYSTEM_PROMPT
        )
        self.assertIn("do not invent his speech or dialogue", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn(
            "do not invent his thoughts, feelings, memories", ORDINARY_WRITER_SYSTEM_PROMPT
        )
        self.assertIn(
            "supplied story material and intended direction", ORDINARY_WRITER_SYSTEM_PROMPT
        )
        self.assertIn(
            "not automatically a fully completed off-page event", ORDINARY_WRITER_SYSTEM_PROMPT
        )
        self.assertIn("you own chronology", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("explicit or implicit interiority", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("RESPONSE_SEQUENCE.json", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn(
            "outside the Writer root",
            ORDINARY_WRITER_SYSTEM_PROMPT,
        )
        self.assertNotIn("source_anchor_key", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("internal_causal_guidance", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("surface_realization_items", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("may share one utterance", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("guides_surface_item_key", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("provisional continuity", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("uncertainty does not erase the event", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("Factual authority:", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn(
            "remain true until an explicit RESPONSE_SEQUENCE.json transition changes them",
            ORDINARY_WRITER_SYSTEM_PROMPT,
        )
        self.assertIn(
            "No setup, staging, narration, action, or ending may invent",
            ORDINARY_WRITER_SYSTEM_PROMPT,
        )
        self.assertIn("Requested scene development:", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("Presentation freedom:", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("Ted autonomy and output:", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("Final silent hard-boundary audit:", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("Complete the core requested scenario", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn(
            "Minor cast, claimant, presence, capability, door, object-handling",
            ORDINARY_WRITER_SYSTEM_PROMPT,
        )
        self.assertIn("short-afterbeat differences may remain", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("Explicit user correction always supersedes", ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertNotIn("Final silent hard-boundary audit:", ADULT_WRITER_SYSTEM_PROMPT)
        self.assertEqual(
            ORDINARY_WRITER_SYSTEM_PROMPT.count("unplanned relation change"),
            1,
        )
        self.assertLess(
            ORDINARY_WRITER_SYSTEM_PROMPT.index("Factual authority:"),
            ORDINARY_WRITER_SYSTEM_PROMPT.index("Requested scene development:"),
        )
        self.assertLess(
            ORDINARY_WRITER_SYSTEM_PROMPT.index("Requested scene development:"),
            ORDINARY_WRITER_SYSTEM_PROMPT.index("Presentation freedom:"),
        )
        self.assertLess(
            ORDINARY_WRITER_SYSTEM_PROMPT.index("Presentation freedom:"),
            ORDINARY_WRITER_SYSTEM_PROMPT.index("Ted autonomy and output:"),
        )
        for freedom in (
            "source-adjacent action",
            "prelude",
            "explicit or implicit interiority",
            "atmosphere",
            "point of view",
            "reordered",
            "compatible detail",
        ):
            self.assertIn(freedom, ORDINARY_WRITER_SYSTEM_PROMPT)
        self.assertIn("Fully realize causal_direction", ADULT_WRITER_SYSTEM_PROMPT)
        self.assertIn("consent_and_capacity", ADULT_WRITER_SYSTEM_PROMPT)
        self.assertIn("You own presentation chronology", ADULT_WRITER_SYSTEM_PROMPT)
        self.assertIn(
            "more Ted realization freedom than ordinary scenes", ADULT_WRITER_SYSTEM_PROMPT
        )
        self.assertIn("plausible immediate Ted physical actions", ADULT_WRITER_SYSTEM_PROMPT)
        self.assertIn("limited in-scene dialogue", ADULT_WRITER_SYSTEM_PROMPT)
        self.assertIn("Do not invent or override consent, withdrawal", ADULT_WRITER_SYSTEM_PROMPT)
        self.assertIn(
            "current consent never authorizes an adjacent act", ADULT_WRITER_SYSTEM_PROMPT
        )
        self.assertIn("may persist as provisional continuity", ADULT_WRITER_SYSTEM_PROMPT)

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
                "cera.pi_scene.writer_authority_order.v15",
            )
            self.assertEqual(
                authority_order["precedence"][:2],
                [
                    "current_accepted_state_baseline",
                    "current_primary_authority_changes_and_postconditions",
                ],
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
                    "render_user_prompt": "writer_selected_under_planner_adjudication",
                    "postcondition_authority_path": (
                        "RESPONSE_SEQUENCE.json#postconditions"
                    ),
                    "internal_guidance_item_keys": [],
                    "response_authority_path": "RESPONSE_SEQUENCE.json",
                    "response_item_keys": ["hana_answers"],
                    "surface_realization_item_keys": ["hana_answers"],
                    "source_contribution_status": "planner_adjudicated_story_material",
                    "presentation_chronology": "writer_selected_within_causal_authority",
                },
            )
            self.assertEqual(
                authority_order["presentation_contract"]["opening_location"],
                "writer_selected",
            )
            self.assertEqual(
                authority_order["presentation_contract"]["source_usage"],
                (
                    "Adjudicated story material may be reordered, revisited, "
                    "framed, or dramatized within the factual precedence above."
                ),
            )
            self.assertEqual(
                authority_order["presentation_contract"]["resulting_state_usage"],
                "exact_at_end_with_only_planned_intermediate_transitions",
            )
            self.assertEqual(
                authority_order["presentation_contract"][
                    "presentation_chronology"
                ],
                "writer_selected_within_causal_authority",
            )
            self.assertFalse((view.root / "zz_RESPONSE_START_GATE.json").exists())
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

    def test_response_projection_tamper_fails_closed_after_manifest_rebinding(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            view = WriterViewMaterializer(root / "views").materialize(
                WriterViewInputV1(
                    world_id="world-test",
                    branch_id="branch-main",
                    scene_id="scene-kitchen",
                    turn_id="turn-0001",
                    candidate_id="candidate-response-tamper",
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
            response_path = view.root / "RESPONSE_SEQUENCE.json"
            response = json.loads(response_path.read_text(encoding="utf-8"))
            response["surface_realization_items"][0][
                "owner_response_semantics"
            ] = "Hana gives a different answer."
            response_text = canonical_json(response)
            response_path.write_text(response_text, encoding="utf-8")
            manifest_path = view.root / "MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            response_entry = next(
                entry
                for entry in manifest["files"]
                if entry["path"] == "RESPONSE_SEQUENCE.json"
            )
            response_entry["sha256"] = text_sha256(response_text)
            response_entry["bytes"] = len(response_text.encode("utf-8"))
            manifest_path.write_text(canonical_json(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                StateConflictError,
                "response projection custody hash changed",
            ):
                verify_writer_view(view.root)

    def test_writer_contract_preserves_established_relations_without_blocking_npc_interruption(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            authority = sequence("ted_offers_envelope")
            authority["items"] = [
                {
                    "item_key": "ted_offers_envelope",
                    "owner_id": "character:ted",
                    "kind": "action",
                    "concise_meaning": (
                        "Ted holds a sealed envelope in his hand and offers it to Hana."
                    ),
                    "owner_response_semantics": None,
                    "protected_user_claim_keys": ["current_request"],
                    "protected_user_exact_quotes": [
                        "I keep the sealed envelope in my hand and offer it to Hana."
                    ],
                    "durable_change_keys": [],
                },
                {
                    "item_key": "hana_declines_transfer",
                    "owner_id": "character:hana",
                    "kind": "action",
                    "concise_meaning": (
                        "Hana leaves the envelope with Ted and asks to verify its label "
                        "before accepting it."
                    ),
                    "owner_response_semantics": (
                        "Hana does not take the envelope and asks Ted to keep holding it "
                        "while she verifies the label."
                    ),
                    "causal_parent_item_key": "ted_offers_envelope",
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
                    "causal_parent_item_key": "hana_declines_transfer",
                    "protected_user_claim_keys": [],
                    "protected_user_exact_quotes": [],
                    "durable_change_keys": [],
                },
            ]
            authority["resulting_public_state"] = (
                "Ted still holds the sealed envelope; Hana has not accepted it."
            )
            authority["unresolved_threads"] = [
                "Whether Ted waits for Hana to verify the label."
            ]
            authority["stopping_boundary"] = (
                "Stop after Hana asks Ted to keep holding the envelope."
            )
            view = WriterViewMaterializer(root / "views").materialize(
                WriterViewInputV1(
                    world_id="world-test",
                    branch_id="branch-main",
                    scene_id="scene-entryway",
                    turn_id="turn-0001",
                    candidate_id="candidate-relation-contract",
                    route=SceneRoute.ORDINARY,
                    user_prompt=(
                        "I keep the sealed envelope in my hand and offer it to Hana."
                    ),
                    primary_authority=authority,
                    current_state={"public_scene_state": "Ted and Hana are present."},
                    characters={"hana": {"name": "Hana", "age": 38}},
                    relationships={},
                    recent_prose=(),
                    relevant_memories={},
                    voice_examples={},
                    craft_index={"approved_material": ["atmosphere", "interiority"]},
                    accepted_records=(),
                )
            )
            response = json.loads(
                (view.root / "RESPONSE_SEQUENCE.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [item["item_key"] for item in response["surface_realization_items"]],
                ["hana_declines_transfer"],
            )
            self.assertNotIn(
                "ted_offers_envelope",
                canonical_json(response),
            )
            self.assertEqual(
                response["postconditions"]["resulting_public_state_must_be_true"],
                "Ted still holds the sealed envelope; Hana has not accepted it.",
            )
            control = json.loads(
                (view.root / "zz_CURRENT_TURN_AUTHORITY.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                control["precedence"][:2],
                [
                    "current_accepted_state_baseline",
                    "current_primary_authority_changes_and_postconditions",
                ],
            )
            self.assertEqual(
                control["presentation_contract"]["opening_location"],
                "writer_selected",
            )
            self.assertEqual(
                control["presentation_contract"]["interiority"],
                "explicit_or_implicit_writer_choice",
            )
            self.assertIn(
                "may be recalled by later turns as provisional continuity",
                control["fact_scope"]["creative_detail_rule"],
            )
            self.assertIn(
                "explicit user correction supersedes them",
                control["fact_scope"]["creative_detail_rule"],
            )

    def test_writer_projection_keeps_generic_relations_until_planned_transitions(self) -> None:
        relation_kinds = (
            "possession",
            "actor_speaker",
            "location",
            "presence",
            "object_state",
        )
        transition_cases = {
            "unchanged": ((), "alpha"),
            "changed": (("alpha_to_beta",), "beta"),
            "changed_then_restored": (("alpha_to_beta", "beta_to_alpha"), "alpha"),
        }
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relation_kind in relation_kinds:
                for case_name, (transitions, ending) in transition_cases.items():
                    with self.subTest(relation_kind=relation_kind, case=case_name):
                        source_key = f"source_{relation_kind}_{case_name}"
                        items = [
                            {
                                "item_key": source_key,
                                "owner_id": "character:ted",
                                "kind": "action",
                                "concise_meaning": (
                                    f"Establish the {relation_kind} relation as alpha."
                                ),
                                "owner_response_semantics": None,
                                "protected_user_claim_keys": ["current_request"],
                                "protected_user_exact_quotes": ["relation alpha"],
                                "durable_change_keys": [],
                            }
                        ]
                        parent = source_key
                        transition_keys = []
                        for transition in transitions:
                            item_key = f"{relation_kind}_{transition}"
                            items.append(
                                {
                                    "item_key": item_key,
                                    "owner_id": "character:hana",
                                    "kind": "action",
                                    "concise_meaning": (
                                        f"Apply the planned {relation_kind} transition "
                                        f"{transition}."
                                    ),
                                    "owner_response_semantics": (
                                        f"The planned {relation_kind} relation changes "
                                        f"through {transition}."
                                    ),
                                    "causal_parent_item_key": parent,
                                    "protected_user_claim_keys": [],
                                    "protected_user_exact_quotes": [],
                                    "durable_change_keys": [],
                                }
                            )
                            transition_keys.append(item_key)
                            parent = item_key
                        answer_key = f"answer_{relation_kind}_{case_name}"
                        items.extend(
                            [
                                {
                                    "item_key": answer_key,
                                    "owner_id": "character:hana",
                                    "kind": "dialogue_intent",
                                    "concise_meaning": "Hana gives the requested answer.",
                                    "owner_response_semantics": (
                                        "Hana answers while respecting the adjudicated relation."
                                    ),
                                    "causal_parent_item_key": parent,
                                    "protected_user_claim_keys": [],
                                    "protected_user_exact_quotes": [],
                                    "durable_change_keys": [],
                                },
                                {
                                    "item_key": f"stop_{relation_kind}_{case_name}",
                                    "owner_id": None,
                                    "kind": "stopping_boundary",
                                    "concise_meaning": "Return the floor to Ted.",
                                    "owner_response_semantics": None,
                                    "causal_parent_item_key": answer_key,
                                    "protected_user_claim_keys": [],
                                    "protected_user_exact_quotes": [],
                                    "durable_change_keys": [],
                                },
                            ]
                        )
                        ending_state = f"{relation_kind} relation is {ending}."
                        authority = {
                            "items": items,
                            "durable_changes": [],
                            "presence_changes": [],
                            "resulting_public_state": ending_state,
                            "unresolved_threads": ["Ted may respond."],
                            "stopping_boundary": "Stop before Ted's next choice.",
                        }
                        view = WriterViewMaterializer(
                            root / relation_kind / case_name
                        ).materialize(
                            WriterViewInputV1(
                                world_id="world-test",
                                branch_id="branch-main",
                                scene_id="scene-generic",
                                turn_id=f"turn-{relation_kind}-{case_name}",
                                candidate_id=f"candidate-{relation_kind}-{case_name}",
                                route=SceneRoute.ORDINARY,
                                user_prompt="Preserve relation alpha and continue.",
                                primary_authority=authority,
                                current_state={
                                    "public_scene_state": (
                                        f"{relation_kind} relation is alpha."
                                    )
                                },
                                characters={"hana": {"name": "Hana", "age": 38}},
                                relationships={},
                                recent_prose=(),
                                relevant_memories={},
                                voice_examples={},
                                craft_index={"approved_material": ["atmosphere"]},
                                accepted_records=(),
                            )
                        )
                        response = json.loads(
                            (view.root / "RESPONSE_SEQUENCE.json").read_text(
                                encoding="utf-8"
                            )
                        )
                        self.assertEqual(
                            [
                                item["item_key"]
                                for item in response["surface_realization_items"]
                            ],
                            [*transition_keys, answer_key],
                        )
                        self.assertEqual(
                            response["postconditions"][
                                "resulting_public_state_must_be_true"
                            ],
                            ending_state,
                        )
                        control = json.loads(
                            (view.root / "zz_CURRENT_TURN_AUTHORITY.json").read_text(
                                encoding="utf-8"
                            )
                        )
                        self.assertEqual(
                            control["presentation_contract"]["resulting_state_usage"],
                            "exact_at_end_with_only_planned_intermediate_transitions",
                        )

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
            self.assertEqual(
                scope["presentation_chronology"],
                "writer_selected_within_causal_authority",
            )
            response_sequence = json.loads(
                (view.root / "RESPONSE_SEQUENCE.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                response_sequence["schema_version"],
                "cera.pi_scene.response_sequence.v8",
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
                "source_anchor_key"
            ]
            self.assertRegex(source_anchor, r"^source-anchor-[0-9a-f]{20}$")
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
            self.assertNotIn("response_start_contract", response_sequence)
            self.assertNotIn("response_start_item_key", response_sequence)
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
                "cera.pi_scene.response_projection_provenance.v3",
            )
            self.assertEqual(
                provenance["source_anchor_bindings"],
                [
                    {
                        "canonical_source_item_key": "ted_source_action",
                        "source_anchor_key": source_anchor,
                    }
                ],
            )
            self.assertEqual(provenance["constraint_item_keys"], ["return_floor"])
            self.assertEqual(
                provenance["response_item_mappings"][0],
                {
                    "canonical_causal_parent_item_key": "ted_source_action",
                    "canonical_item_key": "hana_response",
                    "source_anchor_key": source_anchor,
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
            self.assertNotIn("zz_RESPONSE_START_GATE.json", visible_bytes)

    def test_private_state_and_visible_action_preserve_causality_without_opening_rule(self) -> None:
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
            self.assertNotIn("response_start_item_key", response)
            self.assertNotIn("response_start_contract", response)
            self.assertFalse((view.root / "zz_RESPONSE_START_GATE.json").exists())

            authority["items"] = [
                authority["items"][0],
                authority["items"][1],
                authority["items"][3],
            ]
            interior_view = WriterViewMaterializer(root / "interior-views").materialize(
                WriterViewInputV1(
                    world_id="world-test",
                    branch_id="branch-main",
                    scene_id="scene-private",
                    turn_id="turn-0002",
                    candidate_id="candidate-interior-only",
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
            interior_response = json.loads(
                (interior_view.root / "RESPONSE_SEQUENCE.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(interior_response["surface_realization_items"], [])
            self.assertEqual(
                [
                    item["item_key"]
                    for item in interior_response["internal_causal_guidance"]
                ],
                ["hana_internal_response"],
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
            self.assertNotIn("response_start_contract", response)
            self.assertNotIn("response_start_item_key", response)

    def test_atomic_dialogue_propositions_remain_distinct_surface_obligations(self) -> None:
        with TemporaryDirectory() as temporary:
            authority = sequence("acknowledge")
            authority["items"] = [
                {
                    "item_key": "acknowledge",
                    "owner_id": "character:hana",
                    "kind": "dialogue_intent",
                    "concise_meaning": "Hana acknowledges the immediate concern.",
                    "owner_response_semantics": "Hana acknowledges the immediate concern.",
                    "protected_user_claim_keys": [],
                    "protected_user_exact_quotes": [],
                    "durable_change_keys": [],
                },
                {
                    "item_key": "express_appreciation",
                    "owner_id": "character:hana",
                    "kind": "dialogue_intent",
                    "concise_meaning": "Hana expresses appreciation for being noticed.",
                    "owner_response_semantics": "Hana expresses appreciation for being noticed.",
                    "causal_parent_item_key": "acknowledge",
                    "protected_user_claim_keys": [],
                    "protected_user_exact_quotes": [],
                    "durable_change_keys": [],
                },
                {
                    "item_key": "offer_conversation",
                    "owner_id": "character:hana",
                    "kind": "dialogue_intent",
                    "concise_meaning": "Hana asks whether Ted wants to keep talking.",
                    "owner_response_semantics": "Hana asks whether Ted wants to keep talking.",
                    "causal_parent_item_key": "express_appreciation",
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
                    "causal_parent_item_key": "offer_conversation",
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
                    candidate_id="candidate-atomic-dialogue",
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
                [item["item_key"] for item in response["surface_realization_items"]],
                ["acknowledge", "express_appreciation", "offer_conversation"],
            )
            self.assertEqual(
                [
                    item["owner_response_semantics"]
                    for item in response["surface_realization_items"]
                ],
                [
                    "Hana acknowledges the immediate concern.",
                    "Hana expresses appreciation for being noticed.",
                    "Hana asks whether Ted wants to keep talking.",
                ],
            )
            self.assertNotIn("response_start_item_key", response)
            self.assertNotIn("response_start_contract", response)
            self.assertFalse((view.root / "zz_RESPONSE_START_GATE.json").exists())

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
            response = json.loads(
                (view.root / "RESPONSE_SEQUENCE.json").read_text(encoding="utf-8")
            )
            world_item = response["surface_realization_items"][0]
            self.assertEqual(world_item["response_scope"], "world")
            self.assertIsNone(world_item["owner_id"])
            self.assertEqual(world_item["kind"], "material_continuity")
            self.assertFalse((view.root / "zz_RESPONSE_START_GATE.json").exists())

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
                    "content": [{"type": "toolCall", "name": "context"}],
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
                    {"type": "tool_execution_start", "toolName": "context"},
                    {"type": "tool_execution_end", "toolName": "context", "isError": False},
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
            adapter = OfflinePiSceneAdapter(
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
        self.assertIn("LOGIC-OWNER REALIZATION AUTHORITY", extension)
        self.assertIn("writerContextPlan", extension)
        self.assertIn("USER-SUPPLIED STORY MATERIAL", extension)
        self.assertNotIn("DERIVED NONCANONICAL EXECUTION FOCUS", extension)
        self.assertIn("MAX_WRITER_GENESIS_FILE_BYTES = 128 * 1024", extension)
        self.assertIn("MAX_WRITER_CONTEXT_BYTES = 384 * 1024", extension)
        self.assertIn("DEEPSEEK_MAXIMUM_REQUEST_BYTES = 512 * 1024", extension)
        self.assertIn("WRITER_NON_CONTEXT_REQUEST_RESERVE_BYTES = 128 * 1024", extension)
        self.assertIn("Buffer.byteLength(JSON.stringify(text), \"utf8\")", extension)
        self.assertIn("cera.writer_context_packet.v8", extension)

    def test_writer_context_projects_two_active_genesis_bundles_with_growth(self) -> None:
        sakura_id = "character:sakura"
        hana_id = "character:hana"
        enne_id = "character:enne"
        sakura = genesis_claim_bundle(
            sakura_id,
            label="sakura",
            records=121,
            payload_chars=1_800,
        )
        hana = genesis_claim_bundle(
            hana_id,
            label="hana",
            records=121,
            payload_chars=1_800,
        )

        def owner_private_record(
            bundle: dict[str, object],
            index: int,
            *,
            owner_id: str,
            subject_ids: list[str],
            claim: str,
        ) -> None:
            projections = bundle["genesis_record_projections"]
            assert isinstance(projections, list)
            projection = projections[index]
            assert isinstance(projection, dict)
            record = projection["record"]
            assert isinstance(record, dict)
            record["visibility"] = "owner_private"
            record["owner_id"] = owner_id
            record["knowledge_owner_ids"] = [owner_id]
            record["subject_ids"] = subject_ids
            record["claim"] = claim
            projection["visibility"] = "owner_private"
            projection["knowledge_owner_id"] = owner_id

        owner_private_record(
            sakura,
            0,
            owner_id=sakura_id,
            subject_ids=[sakura_id],
            claim="CERA-SAKURA-OWNER-PRIVATE-CLAIM",
        )
        owner_private_record(
            hana,
            0,
            owner_id=hana_id,
            subject_ids=[hana_id],
            claim="CERA-HANA-OWNER-PRIVATE-CLAIM",
        )
        owner_private_record(
            sakura,
            1,
            owner_id=enne_id,
            subject_ids=[sakura_id, enne_id],
            claim="CERA-FOREIGN-OWNER-PRIVATE-CLAIM",
        )
        relationships = {
            sakura_id: genesis_claim_bundle(
                sakura_id,
                label="sakura-relationship",
                records=13,
                payload_chars=600,
            ),
            hana_id: genesis_claim_bundle(
                hana_id,
                label="hana-relationship",
                records=13,
                payload_chars=600,
            ),
            enne_id: genesis_claim_bundle(
                enne_id,
                label="inactive-relationship",
                records=13,
                payload_chars=600,
            ),
            "relationship:active-legacy": {
                "target_key": "relationship:active-legacy",
                "participants": ["character:ted", sakura_id],
                "accepted_branch_changes": [
                    branch_change(
                        label="active-relationship-change",
                        subject_ids=["character:ted", sakura_id],
                    )
                ],
            },
            "relationship:inactive-legacy": {
                "target_key": "relationship:inactive-legacy",
                "participants": ["character:ted", enne_id],
                "accepted_branch_changes": [
                    branch_change(
                        label="inactive-relationship-change",
                        subject_ids=["character:ted", enne_id],
                    )
                ],
            },
        }
        memories = {
            sakura_id: genesis_claim_bundle(
                sakura_id,
                label="sakura-memory",
                records=12,
                payload_chars=600,
            ),
            hana_id: genesis_claim_bundle(
                hana_id,
                label="hana-memory",
                records=12,
                payload_chars=600,
            ),
            enne_id: genesis_claim_bundle(
                enne_id,
                label="inactive-memory",
                records=12,
                payload_chars=600,
            ),
        }
        source_semantic_context = {
            "characters": {sakura_id: sakura, hana_id: hana},
            "relationships": relationships,
            "relevant_memories": memories,
        }
        self.assertGreater(len(canonical_json(sakura).encode("utf-8")), 329_000)
        self.assertGreater(len(canonical_json(hana).encode("utf-8")), 329_000)

        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            materializer = WriterViewMaterializer(root / "views")
            view = materializer.materialize(
                WriterViewInputV1(
                    world_id="world-large-adult-context",
                    branch_id="branch-main",
                    scene_id="scene-private-room",
                    turn_id="turn-0007",
                    candidate_id="candidate-large-adult-context",
                    route=SceneRoute.ADULT,
                    user_prompt="Continue from the exact authorized adult handoff.",
                    primary_authority={
                        "schema_version": "cera.pi_scene.adult_handoff.v1",
                        "participant_ids": ["character:ted", sakura_id, hana_id],
                        "all_participants_adults": True,
                        "consent_and_capacity": "The authorized adults retain agency.",
                        "causal_direction": "Realize one bounded beat and return the floor.",
                    },
                    current_state={
                        "accepted_present_character_ids": [sakura_id, hana_id],
                        "public_scene_state": "The authorized adults remain present.",
                    },
                    characters={sakura_id: sakura, hana_id: hana},
                    relationships=relationships,
                    recent_prose=tuple(
                        f"CERA-RECENT-TURN-{index}-" + ("R" * 7_000)
                        for index in range(1, 7)
                    ),
                    relevant_memories=memories,
                    voice_examples={
                        sakura_id: {"guidance": "Precise and composed."},
                        hana_id: {"guidance": "Warm and deliberate."},
                    },
                    craft_index={"mode": "bounded-adult-test"},
                    accepted_records=tuple(
                        {
                            "accepted_order": index,
                            "bounded_continuity": "A" * 1_500,
                        }
                        for index in range(1, 7)
                    ),
                )
            )
            authority = json.loads(
                (view.root / "zz_CURRENT_TURN_AUTHORITY.json").read_text(
                    encoding="utf-8"
                )
            )
            projection = authority["context_projection"]
            self.assertEqual(
                projection["schema_version"],
                "cera.pi_scene.writer_context_projection.v2",
            )
            self.assertEqual(
                projection["active_character_ids"],
                [hana_id, sakura_id],
            )
            self.assertEqual(
                projection["source_semantic_context_sha256"],
                canonical_sha256(source_semantic_context),
            )
            self.assertEqual(projection["excluded_relationship_actor_buckets"], 2)
            self.assertEqual(projection["excluded_memory_actor_buckets"], 1)

            projected_character_files = sorted((view.root / "characters").glob("*.json"))
            self.assertEqual(len(projected_character_files), 2)
            projected_characters = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in projected_character_files
            ]
            self.assertTrue(
                all(
                    value["schema_version"]
                    == "cera.pi_scene.writer_genesis_claim_bundle.v2"
                    for value in projected_characters
                )
            )
            self.assertTrue(
                all(path.stat().st_size < 128 * 1024 for path in projected_character_files)
            )
            by_id = {value["character_id"]: value for value in projected_characters}
            self.assertEqual(by_id[sakura_id]["source_bundle_sha256"], canonical_sha256(sakura))
            self.assertEqual(by_id[hana_id]["source_bundle_sha256"], canonical_sha256(hana))
            self.assertEqual(by_id[sakura_id]["omitted_record_count"], 1)
            self.assertEqual(by_id[hana_id]["omitted_record_count"], 0)
            visible_character_text = "\n".join(
                path.read_text(encoding="utf-8") for path in projected_character_files
            )
            self.assertIn("CERA-SAKURA-CLAIM-120", visible_character_text)
            self.assertIn("CERA-HANA-CLAIM-120", visible_character_text)
            self.assertIn("CERA-SAKURA-OWNER-PRIVATE-CLAIM", visible_character_text)
            self.assertIn("CERA-HANA-OWNER-PRIVATE-CLAIM", visible_character_text)
            self.assertNotIn("CERA-FOREIGN-OWNER-PRIVATE-CLAIM", visible_character_text)
            self.assertNotIn("CERA-OMITTED-PAYLOAD", visible_character_text)
            self.assertNotIn("payload_json", visible_character_text)

            relationship_values = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in (view.root / "relationships").glob("*.json")
            ]
            self.assertEqual(len(relationship_values), 3)
            relationship_change_keys = {
                change["change_key"]
                for value in relationship_values
                for change in value.get("accepted_branch_changes", [])
            }
            self.assertIn(
                "active-relationship-change",
                relationship_change_keys,
            )
            self.assertNotIn(
                "inactive-relationship-change",
                relationship_change_keys,
            )
            self.assertNotIn(
                enne_id,
                {value.get("character_id") for value in relationship_values},
            )
            memory_values = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in (view.root / "relevant_memories").glob("*.json")
            ]
            self.assertEqual(
                {value["character_id"] for value in memory_values},
                {sakura_id, hana_id},
            )

            probe = run_pi_context_extension(root=root, view_root=view.root)
            self.assertEqual(probe.returncode, 0, probe.stderr)
            packet = json.loads(probe.stdout)
            self.assertGreater(packet["bytes"], 64 * 1024)
            self.assertLessEqual(packet["bytes"], 384 * 1024)
            self.assertEqual(packet["packet"], "cera.writer_context_packet.v8")
            self.assertTrue(packet["hasSakuraClaim"])
            self.assertTrue(packet["hasHanaClaim"])
            self.assertTrue(packet["hasSakuraOwnerPrivateClaim"])
            self.assertTrue(packet["hasHanaOwnerPrivateClaim"])
            self.assertFalse(packet["hasForeignOwnerPrivateClaim"])
            self.assertFalse(packet["hasOmittedPayload"])
            self.assertTrue(packet["hasSixthTurn"])
            route_request_limit = deepseek_composer_candidate().maximum_request_bytes
            self.assertEqual(route_request_limit, 524_288)
            self.assertEqual(packet["maximumRequestBytes"], route_request_limit)
            self.assertEqual(packet["serializedContextLimitBytes"], 384 * 1024)
            self.assertEqual(packet["nonContextRequestReserveBytes"], 128 * 1024)
            self.assertLess(packet["freshSessionRequestEstimateBytes"], route_request_limit)
            self.assertLessEqual(
                packet["maximumEnvelopeNonContextBytes"],
                packet["nonContextRequestReserveBytes"],
            )
            self.assertGreater(
                route_request_limit - packet["freshSessionRequestEstimateBytes"],
                32 * 1024,
            )

            over_cap = materializer.materialize(
                WriterViewInputV1(
                    world_id="world-large-adult-context",
                    branch_id="branch-main",
                    scene_id="scene-private-room",
                    turn_id="turn-0008",
                    candidate_id="candidate-over-cap-adult-context",
                    route=SceneRoute.ADULT,
                    user_prompt="Continue from the same bounded handoff.",
                    primary_authority=adult_handoff("over_cap"),
                    current_state={
                        "accepted_present_character_ids": [sakura_id, hana_id],
                        "public_scene_state": "The authorized adults remain present.",
                    },
                    characters={sakura_id: sakura, hana_id: hana},
                    relationships=relationships,
                    recent_prose=tuple("X" * 30_000 for _ in range(6)),
                    relevant_memories=memories,
                    voice_examples={},
                    craft_index={},
                    accepted_records=(),
                )
            )
            rejected = run_pi_context_extension(root=root, view_root=over_cap.root)
            self.assertEqual(rejected.returncode, 7)
            self.assertIn("Writer view exceeds the context bound", rejected.stderr)

            changed = json.loads(canonical_json(sakura))
            changed["genesis_record_projections"][0]["record"][
                "future_unprojected_field"
            ] = "must fail closed"
            with self.assertRaisesRegex(
                ContractValidationError,
                "Genesis Writer record fields changed",
            ):
                materializer.materialize(
                    WriterViewInputV1(
                        world_id="world-large-adult-context",
                        branch_id="branch-main",
                        scene_id="scene-private-room",
                        turn_id="turn-0009",
                        candidate_id="candidate-unknown-genesis-field",
                        route=SceneRoute.ADULT,
                        user_prompt="Continue.",
                        primary_authority=adult_handoff("unknown_field"),
                        current_state={"public_scene_state": "The scene remains bounded."},
                        characters={sakura_id: changed},
                        relationships={},
                        recent_prose=(),
                        relevant_memories={},
                        voice_examples={},
                        craft_index={},
                        accepted_records=(),
                    )
                )

    def test_writer_projection_filters_private_genesis_and_branch_overlays(self) -> None:
        active_id = "character:active"
        foreign_id = "character:foreign"
        bundle = genesis_claim_bundle(
            active_id,
            label="mixed-owner",
            records=5,
            payload_chars=200,
        )
        projections = bundle["genesis_record_projections"]
        assert isinstance(projections, list)

        def configure_record(
            index: int,
            *,
            label: str,
            visibility: str,
            owner_id: str | None,
            knowledge_owner_ids: list[str],
            subject_ids: list[str],
            relationship_from_id: str | None = None,
        ) -> dict[str, object]:
            projection = projections[index]
            assert isinstance(projection, dict)
            record = projection["record"]
            assert isinstance(record, dict)
            record["record_id"] = f"record:{label}"
            record["claim"] = f"CERA-{label.upper()}-CLAIM"
            record["payload_json"] = canonical_json(
                {"private_payload_sentinel": f"CERA-{label.upper()}-PAYLOAD"}
            )
            record["visibility"] = visibility
            record["owner_id"] = owner_id
            record["knowledge_owner_ids"] = knowledge_owner_ids
            record["subject_ids"] = subject_ids
            record["relationship_from_id"] = relationship_from_id
            projection["visibility"] = visibility
            projection["knowledge_owner_id"] = (
                owner_id
                if owner_id is not None
                else knowledge_owner_ids[0]
                if len(knowledge_owner_ids) == 1
                else None
            )
            return record

        foreign_private = configure_record(
            0,
            label="foreign-private",
            visibility="owner_private",
            owner_id=foreign_id,
            knowledge_owner_ids=[active_id],
            subject_ids=[active_id, foreign_id],
        )
        configure_record(
            1,
            label="active-private",
            visibility="owner_private",
            owner_id=active_id,
            knowledge_owner_ids=[active_id],
            subject_ids=[active_id],
        )
        configure_record(
            2,
            label="public-active",
            visibility="public",
            owner_id=None,
            knowledge_owner_ids=[],
            subject_ids=[active_id],
        )
        foreign_system = configure_record(
            3,
            label="foreign-system",
            visibility="system_private",
            owner_id=None,
            knowledge_owner_ids=[],
            subject_ids=[foreign_id],
            relationship_from_id=active_id,
        )
        configure_record(
            4,
            label="active-system",
            visibility="system_private",
            owner_id=None,
            knowledge_owner_ids=[],
            subject_ids=[active_id],
        )
        bundle["accepted_branch_changes"] = [
            branch_change(
                label="active-public-change",
                subject_ids=[active_id],
            ),
            branch_change(
                label="active-private-change",
                subject_ids=[active_id],
                visibility="character_private",
                knowledge_owner_id=active_id,
            ),
            branch_change(
                label="foreign-private-change",
                subject_ids=[active_id, foreign_id],
                visibility="character_private",
                knowledge_owner_id=foreign_id,
            ),
        ]
        memories = {
            "knowledge:active-owner": branch_change(
                label="active-private-memory",
                subject_ids=[active_id],
                visibility="character_private",
                knowledge_owner_id=active_id,
            ),
            "knowledge:foreign-owner": branch_change(
                label="foreign-private-memory",
                subject_ids=[active_id, foreign_id],
                visibility="character_private",
                knowledge_owner_id=foreign_id,
            ),
        }

        with TemporaryDirectory() as temporary:
            materializer = WriterViewMaterializer(Path(temporary) / "views")
            view = materializer.materialize(
                WriterViewInputV1(
                    world_id="world-private-projection",
                    branch_id="branch-main",
                    scene_id="scene-room",
                    turn_id="turn-0001",
                    candidate_id="candidate-private-projection",
                    route=SceneRoute.ADULT,
                    user_prompt="Continue.",
                    primary_authority=adult_handoff("private_projection"),
                    current_state={
                        "public_scene_state": "The active adult remains present.",
                        "genesis_revision": "genesis:test:private-projection",
                    },
                    characters={active_id: bundle},
                    relationships={},
                    recent_prose=(),
                    relevant_memories=memories,
                    voice_examples={},
                    craft_index={},
                    accepted_records=(),
                )
            )
            character_path = next((view.root / "characters").glob("*.json"))
            projected = json.loads(character_path.read_text(encoding="utf-8"))
            self.assertEqual(
                projected["schema_version"],
                "cera.pi_scene.writer_genesis_claim_bundle.v2",
            )
            self.assertEqual(projected["source_record_count"], 5)
            self.assertEqual(projected["omitted_record_count"], 2)
            self.assertEqual(
                set(projected["omitted_source_record_sha256s"]),
                {canonical_sha256(foreign_private), canonical_sha256(foreign_system)},
            )
            visible_claims = {claim["claim"] for claim in projected["claims"]}
            self.assertEqual(
                visible_claims,
                {
                    "CERA-ACTIVE-PRIVATE-CLAIM",
                    "CERA-PUBLIC-ACTIVE-CLAIM",
                    "CERA-ACTIVE-SYSTEM-CLAIM",
                },
            )
            projected_text = character_path.read_text(encoding="utf-8")
            self.assertNotIn("CERA-FOREIGN-PRIVATE-CLAIM", projected_text)
            self.assertNotIn("CERA-FOREIGN-PRIVATE-PAYLOAD", projected_text)
            self.assertNotIn("CERA-FOREIGN-SYSTEM-CLAIM", projected_text)
            self.assertEqual(projected["source_accepted_branch_change_count"], 3)
            self.assertEqual(projected["omitted_branch_change_count"], 1)
            self.assertEqual(
                {change["change_key"] for change in projected["accepted_branch_changes"]},
                {"active-public-change", "active-private-change"},
            )
            memory_values = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in (view.root / "relevant_memories").glob("*.json")
            ]
            self.assertEqual(
                {value["change_key"] for value in memory_values},
                {"active-private-memory"},
            )
            authority = json.loads(
                (view.root / "zz_CURRENT_TURN_AUTHORITY.json").read_text(
                    encoding="utf-8"
                )
            )
            receipt = authority["context_projection"]
            self.assertEqual(receipt["character_context_mode"], "production_genesis")
            self.assertEqual(receipt["omitted_unauthorized_genesis_records"], 2)
            self.assertEqual(receipt["omitted_unauthorized_branch_changes"], 1)
            self.assertEqual(receipt["excluded_memory_actor_buckets"], 1)

            fallback = materializer.materialize(
                WriterViewInputV1(
                    world_id="world-private-projection",
                    branch_id="branch-main",
                    scene_id="scene-room",
                    turn_id="turn-0002",
                    candidate_id="candidate-no-genesis-fallback",
                    route=SceneRoute.ADULT,
                    user_prompt="Continue.",
                    primary_authority=adult_handoff("fallback"),
                    current_state={
                        "public_scene_state": "The newly present adult remains present.",
                        "genesis_revision": "genesis:test:private-projection",
                    },
                    characters={
                        active_id: no_genesis_character(
                            active_id,
                            accepted_branch_changes=[
                                branch_change(
                                    label="fallback-active-change",
                                    subject_ids=[active_id],
                                )
                            ],
                        )
                    },
                    relationships={},
                    recent_prose=(),
                    relevant_memories={},
                    voice_examples={},
                    craft_index={},
                    accepted_records=(),
                )
            )
            fallback_value = json.loads(
                next((fallback.root / "characters").glob("*.json")).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                fallback_value["schema_version"],
                "cera.pi_scene.writer_no_genesis_character.v1",
            )
            self.assertEqual(
                fallback_value["accepted_branch_changes"][0]["change_key"],
                "fallback-active-change",
            )

            malformed_projection = json.loads(canonical_json(bundle))
            malformed_projection["genesis_record_projections"][0][
                "future_outer_projection_field"
            ] = "must fail closed"
            malformed_sources = (
                {
                    "character_id": active_id,
                    "genesis_record_projections": None,
                    "raw_private_payload": "must not pass",
                },
                {
                    **bundle,
                    "future_outer_bundle_field": "must fail closed",
                },
                malformed_projection,
            )
            for index, malformed in enumerate(malformed_sources, start=1):
                with self.subTest(malformed=index), self.assertRaisesRegex(
                    ContractValidationError,
                    (
                        "Genesis Writer bundle fields changed|"
                        "Genesis Writer bundle records are invalid|"
                        "Genesis Writer projection fields changed"
                    ),
                ):
                    materializer.materialize(
                        WriterViewInputV1(
                            world_id="world-private-projection",
                            branch_id="branch-main",
                            scene_id="scene-room",
                            turn_id=f"turn-malformed-{index}",
                            candidate_id=f"candidate-malformed-{index}",
                            route=SceneRoute.ADULT,
                            user_prompt="Continue.",
                            primary_authority=adult_handoff(f"malformed_{index}"),
                            current_state={
                                "public_scene_state": "The scene remains bounded.",
                                "genesis_revision": "genesis:test:private-projection",
                            },
                            characters={active_id: malformed},
                            relationships={},
                            recent_prose=(),
                            relevant_memories={},
                            voice_examples={},
                            craft_index={},
                            accepted_records=(),
                        )
                    )

            with self.assertRaisesRegex(
                ContractValidationError,
                "Writer Genesis revision custody changed",
            ):
                materializer.materialize(
                    WriterViewInputV1(
                        world_id="world-private-projection",
                        branch_id="branch-main",
                        scene_id="scene-room",
                        turn_id="turn-malformed-revision",
                        candidate_id="candidate-malformed-revision",
                        route=SceneRoute.ADULT,
                        user_prompt="Continue.",
                        primary_authority=adult_handoff("malformed_revision"),
                        current_state={
                            "public_scene_state": "The scene remains bounded.",
                            "genesis_revision": None,
                        },
                        characters={active_id: {"raw_private_payload": "must not pass"}},
                        relationships={},
                        recent_prose=(),
                        relevant_memories={},
                        voice_examples={},
                        craft_index={},
                        accepted_records=(),
                    )
                )

            malformed_private_memory = branch_change(
                label="malformed-private-memory",
                subject_ids=[active_id],
                visibility="character_private",
                knowledge_owner_id=active_id,
            )
            malformed_private_memory["knowledge_owner_id"] = None
            with self.assertRaisesRegex(
                ContractValidationError,
                "private overlay has no owner",
            ):
                materializer.materialize(
                    WriterViewInputV1(
                        world_id="world-private-projection",
                        branch_id="branch-main",
                        scene_id="scene-room",
                        turn_id="turn-malformed-private",
                        candidate_id="candidate-malformed-private",
                        route=SceneRoute.ADULT,
                        user_prompt="Continue.",
                        primary_authority=adult_handoff("malformed_private"),
                        current_state={
                            "public_scene_state": "The scene remains bounded.",
                            "genesis_revision": "genesis:test:private-projection",
                        },
                        characters={active_id: bundle},
                        relationships={},
                        recent_prose=(),
                        relevant_memories={"knowledge:malformed": malformed_private_memory},
                        voice_examples={},
                        craft_index={},
                        accepted_records=(),
                    )
                )

            with self.assertRaisesRegex(
                ContractValidationError,
                "production overlay fields changed",
            ):
                materializer.materialize(
                    WriterViewInputV1(
                        world_id="world-private-projection",
                        branch_id="branch-main",
                        scene_id="scene-room",
                        turn_id="turn-raw-overlay",
                        candidate_id="candidate-raw-overlay",
                        route=SceneRoute.ADULT,
                        user_prompt="Continue.",
                        primary_authority=adult_handoff("raw_overlay"),
                        current_state={
                            "public_scene_state": "The scene remains bounded.",
                            "genesis_revision": "genesis:test:private-projection",
                        },
                        characters={active_id: bundle},
                        relationships={
                            "relationship:unknown": {
                                "participants": [active_id],
                                "raw_private_payload": "must fail closed",
                            }
                        },
                        recent_prose=(),
                        relevant_memories={},
                        voice_examples={},
                        craft_index={},
                        accepted_records=(),
                    )
                )

    def test_writer_context_enforces_escaped_and_schema_specific_bounds(self) -> None:
        active_id = "character:active"

        def materialize_legacy(root: Path, *, candidate_id: str, escaped_chars: int):
            escaped = "\\" * escaped_chars
            return WriterViewMaterializer(root / "views").materialize(
                WriterViewInputV1(
                    world_id="world-legacy-bound",
                    branch_id="branch-main",
                    scene_id="scene-room",
                    turn_id=candidate_id.replace("candidate", "turn"),
                    candidate_id=candidate_id,
                    route=SceneRoute.ADULT,
                    user_prompt="Continue.",
                    primary_authority=adult_handoff(candidate_id),
                    current_state={"public_scene_state": "One active adult remains present."},
                    characters={active_id: {"blob": escaped}},
                    relationships={
                        "relationship:legacy": {
                            "participants": [active_id],
                            "blob": escaped,
                        }
                    },
                    recent_prose=(),
                    relevant_memories={
                        "memory:legacy": {
                            "character_id": active_id,
                            "blob": escaped,
                        }
                    },
                    voice_examples={},
                    craft_index={},
                    accepted_records=(),
                )
            )

        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            oversized_legacy = materialize_legacy(
                root,
                candidate_id="candidate-legacy-108k",
                escaped_chars=54_000,
            )
            oversized_probe = run_pi_context_extension(
                root=root,
                view_root=oversized_legacy.root,
            )
            self.assertEqual(oversized_probe.returncode, 7)
            self.assertIn("context file exceeds the read bound", oversized_probe.stderr)

            escaped_over = materialize_legacy(
                root,
                candidate_id="candidate-escaped-over",
                escaped_chars=32_500,
            )
            self.assertTrue(
                all(
                    path.stat().st_size < 64 * 1024
                    for directory in ("characters", "relationships", "relevant_memories")
                    for path in (escaped_over.root / directory).glob("*.json")
                )
            )
            escaped_rejected = run_pi_context_extension(
                root=root,
                view_root=escaped_over.root,
            )
            self.assertEqual(escaped_rejected.returncode, 7)
            self.assertIn(
                "Writer view exceeds the serialized request bound",
                escaped_rejected.stderr,
            )

            escaped_under = materialize_legacy(
                root,
                candidate_id="candidate-escaped-under",
                escaped_chars=32_000,
            )
            accepted = run_pi_context_extension(root=root, view_root=escaped_under.root)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            packet = json.loads(accepted.stdout)
            self.assertLessEqual(
                packet["serializedContextBytes"],
                packet["serializedContextLimitBytes"],
            )
            self.assertGreater(
                packet["serializedContextBytes"],
                packet["serializedContextLimitBytes"] - 24 * 1024,
            )
            self.assertLessEqual(
                packet["maximumEnvelopeNonContextBytes"],
                packet["nonContextRequestReserveBytes"],
            )
            self.assertLessEqual(
                packet["serializedContextBytes"]
                + packet["maximumEnvelopeNonContextBytes"],
                deepseek_composer_candidate().maximum_request_bytes,
            )

            recorder = WriterViewMaterializer(root / "recorder-views").materialize(
                WriterViewInputV1(
                    world_id="world-recorder-bound",
                    branch_id="branch-main",
                    scene_id="scene-room",
                    turn_id="turn-recorder-bound",
                    candidate_id="candidate-recorder-bound",
                    route=SceneRoute.ADULT,
                    user_prompt="Record the accepted turn.",
                    primary_authority=adult_handoff("recorder_bound"),
                    current_state={"recording_phase": "post_accept"},
                    characters={active_id: {"blob": "R" * 70_000}},
                    relationships={},
                    recent_prose=("Accepted visible prose.",),
                    relevant_memories={},
                    voice_examples={},
                    craft_index={},
                    accepted_records=(),
                    purpose="recorder",
                )
            )
            recorder_authority = json.loads(
                (recorder.root / "zz_CURRENT_TURN_AUTHORITY.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                recorder_authority["schema_version"],
                "cera.pi_scene.writer_authority_order.v12",
            )
            self.assertIsNone(recorder_authority["context_projection"])
            recorder_character = next((recorder.root / "characters").glob("*.json"))
            self.assertGreater(recorder_character.stat().st_size, 64 * 1024)
            self.assertIn("R" * 100, recorder_character.read_text(encoding="utf-8"))
            recorder_probe = run_pi_context_extension(
                root=root,
                view_root=recorder.root,
                purpose="recorder",
            )
            self.assertEqual(recorder_probe.returncode, 7)
            self.assertIn("context file exceeds the read bound", recorder_probe.stderr)

    def test_preexisting_v1_writer_projection_fails_closed_on_reuse(self) -> None:
        active_id = "character:active"
        source = WriterViewInputV1(
            world_id="world-stale-projection",
            branch_id="branch-main",
            scene_id="scene-room",
            turn_id="turn-0001",
            candidate_id="candidate-stale-projection",
            route=SceneRoute.ADULT,
            user_prompt="Continue.",
            primary_authority=adult_handoff("stale_projection"),
            current_state={
                "public_scene_state": "The active adult remains present.",
                "genesis_revision": "genesis:test:stale-projection",
            },
            characters={
                active_id: genesis_claim_bundle(
                    active_id,
                    label="stale-projection",
                    records=1,
                    payload_chars=100,
                )
            },
            relationships={},
            recent_prose=(),
            relevant_memories={},
            voice_examples={},
            craft_index={},
            accepted_records=(),
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            materializer = WriterViewMaterializer(root / "views")
            view = materializer.materialize(source)
            character_path = next((view.root / "characters").glob("*.json"))
            current = json.loads(character_path.read_text(encoding="utf-8"))
            stale = {
                "schema_version": "cera.pi_scene.writer_genesis_claim_bundle.v1",
                "character_id": current["character_id"],
                "source_bundle_sha256": current["source_bundle_sha256"],
                "claims": current["claims"],
            }
            stale_text = canonical_json(stale)
            character_path.write_text(stale_text, encoding="utf-8")
            manifest_path = view.root / "MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            relative = character_path.relative_to(view.root).as_posix()
            entry = next(item for item in manifest["files"] if item["path"] == relative)
            entry["sha256"] = text_sha256(stale_text)
            entry["bytes"] = len(stale_text.encode("utf-8"))
            manifest_path.write_text(canonical_json(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                StateConflictError,
                "Writer Genesis projection is stale",
            ):
                verify_writer_view(view.root)
            with self.assertRaisesRegex(
                StateConflictError,
                "Writer Genesis projection is stale",
            ):
                materializer.materialize(source)
            probe = run_pi_context_extension(root=root, view_root=view.root)
            self.assertEqual(probe.returncode, 7)
            self.assertIn("Writer Genesis projection is stale or invalid", probe.stderr)

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

    def test_ordinary_regenerate_reuses_logic_and_accept_is_exactly_once(self) -> None:
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
            self.assertEqual(successor.result.planner_provider_operations, 0)
            self.assertIsNotNone(successor.creator_guidance)
            assert successor.creator_guidance is not None
            self.assertEqual(
                successor.creator_guidance.text,
                "Make the replacement more concise.",
            )
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
            self.assertNotIn("more concise", pi.calls[1].prompt)
            regenerated_state = json.loads(
                (pi.calls[1].view.root / "CURRENT_STATE.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                regenerated_state["creator_control_guidance"]["text"],
                "Make the replacement more concise.",
            )
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
            assert writer_calls[2].accepted_parent_session is not None
            self.assertEqual(
                successor.candidate.writer_receipt.parent_session_id_sha256,
                writer_calls[2].accepted_parent_session.session_id_sha256,
            )
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
            assert first_restarted_call.accepted_parent_session is not None
            self.assertEqual(
                after_restart.candidate.writer_receipt.parent_session_id_sha256,
                first_restarted_call.accepted_parent_session.session_id_sha256,
            )
            restarted.accept(after_restart.review_id)

            third = restarted.start_ordinary(turn(source="Third."))
            writer_calls = [
                value for value in restarted_pi.calls if value.purpose == "writer"
            ]
            self.assertIsNotNone(writer_calls[-1].accepted_parent_session)
            self.assertTrue(writer_calls[-1].force_rehydrate)
            self.assertTrue(third.candidate.writer_receipt.rehydrated)
            assert writer_calls[-1].accepted_parent_session is not None
            self.assertEqual(
                third.candidate.writer_receipt.parent_session_id_sha256,
                writer_calls[-1].accepted_parent_session.session_id_sha256,
            )

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
            self.assertEqual(successor.result.planner_provider_operations, 0)
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
