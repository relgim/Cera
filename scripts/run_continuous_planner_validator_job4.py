"""Run the exact D-186 ten-call continuous Planner/Validator canary.

This harness is intentionally one-shot and terminal. It never retries or
substitutes a provider, never touches the active SillyTavern route, and writes
only to the ignored disposable continuous-world root and the exact review-cycle
Job 4 source artifacts.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
from typing import Any, Callable

from cera.continuous.codex_stored import CodexContinuousStoredSessionPort
from cera.continuous.contracts import (
    AcceptedFinalSequenceEnvelopeV1,
    AcceptedTurnPairV1,
    CharacterSummaryEnvelopeV1,
    ValidatorTaskMode,
)
from cera.continuous.prompting import (
    PLANNER_STABLE_INSTRUCTIONS,
    VALIDATOR_STABLE_INSTRUCTIONS,
    build_continuous_composer_prompt,
    build_planner_turn_prompt,
    build_validator_prompt,
    character_summary_share,
)
from cera.continuous.provider import (
    CodexContinuousPlannerPort,
    CodexContinuousValidatorPort,
    DeepSeekContinuousComposerPort,
    continuous_deepseek_route,
    continuous_planner_route,
    continuous_validator_route,
)
from cera.continuous.sessions import (
    ContinuousSessionCompatibilityV1,
    ContinuousSessionCoordinator,
    ContinuousSessionRole,
    ContinuousSessionSnapshotStore,
    assert_separate_role_sessions,
)
from cera.continuous.world import (
    ContinuousDebugRecorder,
    ContinuousWorldStore,
    SceneChangeCoordinator,
)
from cera.continuous.world_mcp import (
    ContinuousWorldMcpBridge,
    ContinuousWorldToolDispatcher,
)
from cera.creator_review.models import CreatorReviewAction
from cera.active_runtime_validation import active_runtime_status
from cera.providers import CodexSDKTransport, DeepSeekChatTransport, StoredCodexThreadRunner
from cera.providers.codex_worker import _BASE_INSTRUCTIONS_BY_ROLE
from cera.reasoner_session import OpenAICodexStoredThreadBackend
from cera.serialization import (
    bytes_sha256,
    canonical_bytes,
    canonical_json,
    canonical_sha256,
    text_sha256,
    to_primitive,
)


ROOT = Path(__file__).resolve().parents[1]
CYCLE_ID = "2026-08-01-continuous-planner-validator-v1-cycle-001"
TASK_ID = "continuous-planner-validator-three-turn-scene-change-canary-v1"
WORLD_ID = "hanezawa-job4"
BRANCH_ID = "canary-main"
TURN_MESSAGES = (
    "Hello, my name is Ted. Is this the Hanezawa residence?",
    "I'm the tenant who was supposed to arrive today.",
    (
        'Several days later, Ted is in the kitchen with Mia and asks, '
        '"Is Sakura always that cautious with visitors?"'
    ),
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical_bytes(value) + b"\n")
    temporary.replace(path)


def git_head(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class StablePrefixTransport:
    """Keep stable role instructions in the stored thread, not every turn."""

    def __init__(self, transport: CodexSDKTransport, stable: str) -> None:
        self.transport = transport
        self.route = transport.route
        self.stable = stable

    def invoke(self, prompt: str, **kwargs):
        prefix = self.stable + "\n\n"
        if not prompt.startswith(prefix):
            raise RuntimeError("continuous stable prompt prefix changed")
        return self.transport.invoke(prompt[len(prefix) :], **kwargs)


def provider_debug(result: Any) -> dict[str, Any]:
    return {
        "provider_receipt": to_primitive(result.provider_receipt),
        "operation_telemetry": to_primitive(result.operation_telemetry),
        "tool_call_count": result.tool_call_count,
        "failed_tool_call_count": result.failed_tool_call_count,
        "world_tool_debug": result.world_tool_debug,
    }


def snapshot_files(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): text_sha256(path.read_text(encoding="utf-8"))
        for path in sorted(value for value in root.rglob("*") if value.is_file())
    }


def exact_diff(before: dict[str, str], after: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {"path": path, "before_sha256": before.get(path), "after_sha256": after.get(path)}
        for path in sorted(set(before).union(after))
        if before.get(path) != after.get(path)
    ]


def source_character_summary(
    root: Path,
    character: str,
    *,
    world_file_revision: int,
    latest_changes: tuple[str, ...] = (),
) -> CharacterSummaryEnvelopeV1:
    path = root / "genesis" / "packages" / "hanezawa_core_v1_2" / "modules" / "characters" / f"{character}.json"
    module = json.loads(path.read_text(encoding="utf-8"))
    desired = {
        "Core premise",
        "Speech system",
        "Initial stance toward Ted",
        "Fidelity invariants",
    }
    sections = []
    for record in module["records"]:
        payload = json.loads(record["payload_json"])
        if payload.get("heading") in desired and isinstance(payload.get("source_text"), str):
            sections.append(payload["source_text"])
    if len(sections) != len(desired):
        raise RuntimeError(f"{character} concise summary source sections are incomplete")
    return CharacterSummaryEnvelopeV1(
        schema_version=CharacterSummaryEnvelopeV1.SCHEMA_VERSION,
        character_id=f"character:{character}_hanezawa",
        source_path_or_record_id=f"Characters/{character.capitalize()}.json",
        source_revision=world_file_revision,
        latest_accepted_changes=latest_changes,
        summary="\n\n".join(sections),
    )


def seed_world(store: ContinuousWorldStore, root: Path) -> None:
    package = root / "genesis" / "packages" / "hanezawa_core_v1_2" / "modules"
    for name in ("hana", "sakura", "mia", "enne", "tomi", "aoi", "yuuni"):
        source = package / "characters" / f"{name}.json"
        store.seed_active_json(
            WORLD_ID,
            BRANCH_ID,
            f"Characters/{name.capitalize()}.json",
            {
                "schema_version": "cera.continuous_genesis_character.v1",
                "character_id": f"character:{name}_hanezawa",
                "source_path": source.relative_to(root).as_posix(),
                "source_sha256": bytes_sha256(source.read_bytes()),
                "genesis_records": json.loads(source.read_text(encoding="utf-8"))["records"],
                "accepted_state": {
                    "knowledge": {},
                    "relationships": {},
                    "development": {},
                    "material": {},
                },
            },
        )
    for relative, target in (
        ("relationships_family.json", "Relationships/Family.json"),
        ("household.json", "Rules/Household.json"),
        ("ted_boundary.json", "Rules/TedBoundary.json"),
    ):
        source = package / relative
        store.seed_active_json(
            WORLD_ID,
            BRANCH_ID,
            target,
            {
                "schema_version": "cera.continuous_genesis_module.v1",
                "source_path": source.relative_to(root).as_posix(),
                "source_sha256": bytes_sha256(source.read_bytes()),
                "genesis_records": json.loads(source.read_text(encoding="utf-8"))["records"],
            },
        )


def compatibility(
    world: ContinuousWorldStore,
    role: ContinuousSessionRole,
) -> ContinuousSessionCompatibilityV1:
    return ContinuousSessionCompatibilityV1(
        schema_version=ContinuousSessionCompatibilityV1.SCHEMA_VERSION,
        world_id=WORLD_ID,
        branch_id=BRANCH_ID,
        role=role,
        provider="openai_codex",
        model="gpt-5.6-sol" if role is ContinuousSessionRole.PLANNER else "gpt-5.6-terra",
        reasoning_effort="medium" if role is ContinuousSessionRole.PLANNER else "high",
        prompt_version=(
            "cera.continuous_planner_prompt.v1"
            if role is ContinuousSessionRole.PLANNER
            else "cera.continuous_validator_prompt.v1"
        ),
        output_schema_version=(
            "cera.rich_planner_sequence.v1"
            if role is ContinuousSessionRole.PLANNER
            else "cera.continuous_validator_draft.v1"
        ),
        world_directory_identity_sha256=world.world_directory_identity(WORLD_ID, BRANCH_ID),
        authority_policy_version="cera.owner_architecture.v2+d186",
        privacy_policy_version="cera.privacy.v1",
        protected_user_policy_version="cera.protected_user.v1",
        session_policy_version="cera.continuous_session.v1",
    )


def read_world_revision(world: ContinuousWorldStore, character: str) -> int:
    path = world.branch_root(WORLD_ID, BRANCH_ID) / "ACTIVE" / "Characters" / f"{character}.json"
    return int(json.loads(path.read_text(encoding="utf-8"))["_cera_revision"])


class ProCorrectionStop(RuntimeError):
    pass


class JobHarness:
    def __init__(
        self,
        *,
        source_root: Path,
        cycle: Path,
        world: ContinuousWorldStore,
        planner_session: ContinuousSessionCoordinator,
        validator_session: ContinuousSessionCoordinator,
        planner_handle: str,
        validator_handle: str,
        lifecycle_root: Path,
    ) -> None:
        self.source_root = source_root
        self.cycle = cycle
        self.world = world
        self.planner_session = planner_session
        self.validator_session = validator_session
        self.planner_handle = planner_handle
        self.validator_handle = validator_handle
        self.lifecycle_root = lifecycle_root
        self.accepted_pairs: list[AcceptedTurnPairV1] = []
        self.call_records: list[dict[str, Any]] = []
        self.poll_records: list[dict[str, Any]] = []
        self.provider_calls = 0
        self.scene_change_envelope = None

    def poll_pro(self, boundary: str) -> None:
        response = self.cycle / "inbox" / "PRO_RESPONSE.md"
        record: dict[str, Any] = {"boundary": boundary, "observed_at": utc_now()}
        if not response.is_file():
            record["state"] = "absent"
            self.poll_records.append(record)
            return
        try:
            from tools.pro_review_cycle_core import parse_response, response_mismatches, sha256_bytes

            data = response.read_bytes()
            template_hash = sha256_bytes(
                (self.cycle / "outbox" / "PRO_RESPONSE_TEMPLATE.md").read_bytes()
            )
            manifest = json.loads((self.cycle / "CYCLE_MANIFEST.json").read_text(encoding="utf-8"))
            parsed = parse_response(data, template_hash)
            mismatches = response_mismatches(manifest, parsed)
            if mismatches:
                record.update({"state": "identity_mismatch", "mismatches": mismatches})
            else:
                record.update(
                    {
                        "state": "valid",
                        "response_sha256": sha256_bytes(data),
                        "disposition": parsed["review_disposition"],
                    }
                )
                self.poll_records.append(record)
                if parsed["review_disposition"] in {"corrections_required", "blocked"}:
                    raise ProCorrectionStop(
                        f"Pro requested {parsed['review_disposition']} at {boundary}"
                    )
                return
        except ProCorrectionStop:
            raise
        except Exception as exc:
            record.update({"state": "not_yet_valid", "error_type": type(exc).__name__})
        self.poll_records.append(record)

    def provider_call(self, label: str, owner: str, operation: Callable[[], Any]) -> Any:
        self.poll_pro(f"before:{label}")
        print(f"JOB4 call {len(self.call_records) + 1}/10 start: {label}", flush=True)
        started = time.perf_counter_ns()
        record: dict[str, Any] = {
            "index": len(self.call_records) + 1,
            "label": label,
            "owner": owner,
            "started_at": utc_now(),
            "status": "started",
        }
        try:
            result = operation()
            self.provider_calls += int(
                getattr(getattr(result, "provider_receipt", None), "external_provider_calls", 1)
            )
            telemetry = getattr(result, "operation_telemetry", None)
            expected_thread_hash = {
                "planner": text_sha256(self.planner_handle),
                "validator": text_sha256(self.validator_handle),
            }.get(owner)
            if expected_thread_hash is not None:
                if telemetry is None:
                    raise RuntimeError("Codex call omitted required operation telemetry")
                if telemetry.provider_thread_id_sha256 != expected_thread_hash:
                    raise RuntimeError("Codex provider thread continuity hash changed")
                expected_model, expected_effort = {
                    "planner": ("gpt-5.6-sol", "medium"),
                    "validator": ("gpt-5.6-terra", "high"),
                }[owner]
                if (
                    telemetry.model != expected_model
                    or telemetry.reasoning_effort != expected_effort
                    or telemetry.fast_mode_enabled
                ):
                    raise RuntimeError("Codex canary route identity changed")
            elif owner == "composer":
                receipt = result.provider_receipt
                if (
                    receipt.requested_model != "deepseek-v4-flash"
                    or receipt.external_provider_calls != 1
                ):
                    raise RuntimeError("DeepSeek canary route identity changed")
            record.update(
                {
                    "status": "passed",
                    "duration_ns": time.perf_counter_ns() - started,
                    "result": provider_debug(result),
                }
            )
            self.call_records.append(record)
            self.poll_pro(f"after:{label}")
            print(f"JOB4 call {record['index']}/10 passed: {label}", flush=True)
            return result
        except ProCorrectionStop:
            record.update(
                {"status": "passed_then_stopped_by_pro", "duration_ns": time.perf_counter_ns() - started}
            )
            if record not in self.call_records:
                self.call_records.append(record)
            raise
        except BaseException as exc:
            observed = int(getattr(exc, "external_provider_calls_observed", 0))
            self.provider_calls += observed
            record.update(
                {
                    "status": "failed",
                    "duration_ns": time.perf_counter_ns() - started,
                    "error_type": type(exc).__name__,
                    "external_provider_calls_observed": observed,
                    "safe_diagnostics": list(getattr(exc, "safe_diagnostics", ())),
                    "provider_call_receipt": to_primitive(
                        getattr(exc, "provider_call_receipt", None)
                    ),
                }
            )
            self.call_records.append(record)
            try:
                self.poll_pro(f"after-failure:{label}")
            except ProCorrectionStop:
                pass
            print(f"JOB4 call {record['index']}/10 failed: {label}", flush=True)
            raise

    def codex_planner(self, prompt: str, turn_id: str):
        workspace = self.lifecycle_root / f"call_{len(self.call_records) + 1:02d}_planner"
        workspace.mkdir()
        dispatcher = ContinuousWorldToolDispatcher(
            self.world.branch_root(WORLD_ID, BRANCH_ID),
            ContinuousSessionRole.PLANNER,
        )
        with ContinuousWorldMcpBridge(dispatcher) as bridge:
            transport = StablePrefixTransport(
                CodexSDKTransport(
                    continuous_planner_route(effort="medium"),
                    workspace=workspace,
                    runner=StoredCodexThreadRunner(self.planner_handle),
                ),
                PLANNER_STABLE_INSTRUCTIONS,
            )
            return CodexContinuousPlannerPort(transport, world_bridge=bridge).plan(prompt)

    def codex_validator(
        self,
        prompt: str,
        turn_id: str,
        *,
        accepted_pairs: tuple[AcceptedTurnPairV1, ...] = (),
    ):
        workspace = self.lifecycle_root / f"call_{len(self.call_records) + 1:02d}_validator"
        workspace.mkdir()
        dispatcher = ContinuousWorldToolDispatcher(
            self.world.branch_root(WORLD_ID, BRANCH_ID),
            ContinuousSessionRole.VALIDATOR,
            current_turn_id=turn_id,
        )
        with ContinuousWorldMcpBridge(dispatcher) as bridge:
            transport = StablePrefixTransport(
                CodexSDKTransport(
                    continuous_validator_route(model="gpt-5.6-terra", effort="high"),
                    workspace=workspace,
                    runner=StoredCodexThreadRunner(self.validator_handle),
                ),
                VALIDATOR_STABLE_INSTRUCTIONS,
            )
            return CodexContinuousValidatorPort(
                transport, world_bridge=bridge
            ).validate(prompt, accepted_pairs=accepted_pairs)

    def deepseek(self, prompt: str):
        return DeepSeekContinuousComposerPort(
            DeepSeekChatTransport(continuous_deepseek_route())
        ).compose(prompt)

    def run_turn(
        self,
        *,
        turn_number: int,
        scene_id: str,
        summaries: tuple[CharacterSummaryEnvelopeV1, ...],
        scene_change_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        message = TURN_MESSAGES[turn_number - 1]
        turn_id = f"turn-{turn_number:03d}"
        candidate = self.world.create_candidate(WORLD_ID, BRANCH_ID, turn_id)
        debug = ContinuousDebugRecorder(
            self.world.branch_root(WORLD_ID, BRANCH_ID), scene_id, turn_id
        )
        debug.initialize()
        packet = {
            "schema_version": "cera.continuous_job4_turn_packet.v1",
            "world_id": WORLD_ID,
            "branch_id": BRANCH_ID,
            "scene_id": scene_id,
            "turn_id": turn_id,
            "current_user_message": message,
            "protected_user_id": "character:ted",
            "content_class": "ordinary",
            "depth": "auto",
            "story_posture": (
                "initial doorway turn; Sakura is the primary relevant household character"
                if turn_number == 1
                else "continue from accepted stored context and current ACTIVE world"
            ),
            "hard_boundaries": [
                "Do not invent Ted's unsupplied thought, dialogue, or consequential action.",
                "Preserve character knowledge ownership and branch isolation.",
                "Stop only after a materially developed unit reaches a real protected-user choice.",
            ],
        }
        planner_prompt, planner_usage = build_planner_turn_prompt(
            current_packet=packet,
            accepted_envelopes=(),
            character_summaries=summaries,
            scene_change_envelope=scene_change_context,
        )
        debug.write_text("planner_raw_prompt.txt", planner_prompt)
        debug.write_json(
            "planner_prompt_components.json",
            [to_primitive(value) for value in planner_usage],
        )
        planner_result = self.provider_call(
            f"turn-{turn_number}-planner",
            "planner",
            lambda: self.codex_planner(planner_prompt, turn_id),
        )
        sequence = planner_result.value
        if sequence.world_id != WORLD_ID or sequence.branch_id != BRANCH_ID or sequence.scene_id != scene_id:
            raise RuntimeError("Planner changed world, branch, or scene scope")
        self.planner_session.record_planner_provisional(turn_id, sequence.sequence_sha256)
        debug.write_json("planner_output.json", to_primitive(sequence))
        debug.write_json("planner_tools.json", provider_debug(planner_result))

        composer_prompt, composer_usage = build_continuous_composer_prompt(
            current_user_source=message,
            planner_sequence=sequence,
            character_summaries=summaries,
        )
        debug.write_json("deepseek_request.json", {"prompt": composer_prompt})
        composer_result = self.provider_call(
            f"turn-{turn_number}-deepseek",
            "composer",
            lambda: self.deepseek(composer_prompt),
        )
        story = composer_result.value.story_text
        debug.write_json("deepseek_output.json", {"story_text": story})

        validator_prompt, validator_usage = build_validator_prompt(
            task_mode=ValidatorTaskMode.FINALIZE_TURN,
            current_user_source=message,
            planner_sequence=sequence,
            deepseek_realization=story,
            accepted_turn_id=turn_id,
            world_file_manifest=self.world.active_manifest(WORLD_ID, BRANCH_ID),
        )
        debug.write_json("validator_request.json", {"prompt": validator_prompt})
        validator_result = self.provider_call(
            f"turn-{turn_number}-validator",
            "validator",
            lambda: self.codex_validator(validator_prompt, turn_id),
        )
        package = validator_result.value
        debug.write_json("validator_output.json", to_primitive(package))
        debug.write_json("validator_tools.json", provider_debug(validator_result))
        if package.world_id != WORLD_ID or package.branch_id != BRANCH_ID:
            raise RuntimeError("Validator changed world or branch scope")
        if not package.permits_disposable_acceptance(CreatorReviewAction.ACCEPT):
            raise RuntimeError("Validator package did not qualify for disposable acceptance")
        self.validator_session.record_validator_candidate(turn_id, package.package_sha256)
        self.world.record_candidate_package(WORLD_ID, BRANCH_ID, candidate, package)
        before = snapshot_files(candidate.root / "ACTIVE_VIEW")
        pair = AcceptedTurnPairV1(
            accepted_turn_id=turn_id,
            user_message=message,
            complete_final_sequence=package.complete_final_sequence,
        )
        receipt = self.world.apply_creator_action(
            world_id=WORLD_ID,
            branch_id=BRANCH_ID,
            turn_id=turn_id,
            action=CreatorReviewAction.ACCEPT,
            package=package,
            accepted_pair=pair,
        )
        envelope = AcceptedFinalSequenceEnvelopeV1(
            schema_version=AcceptedFinalSequenceEnvelopeV1.SCHEMA_VERSION,
            accepted_turn_id=turn_id,
            user_message=message,
            complete_final_sequence=package.complete_final_sequence,
            acceptance_receipt_sha256=receipt.receipt_sha256,
        )
        self.planner_session.append_accepted_final_sequence(envelope)
        injected = self.planner_session.synchronize_accepted_final_sequence(envelope)
        if not injected:
            raise RuntimeError("accepted final sequence was not injected exactly once")
        self.accepted_pairs.append(pair)
        after = snapshot_files(candidate.root / "ACTIVE_VIEW")
        debug.write_json("candidate_before.json", before)
        debug.write_json("candidate_after.json", after)
        debug.write_json("exact_diff.json", exact_diff(before, after))
        debug.write_json("edit_package.json", to_primitive(package.world_edit_operations))
        debug.write_json("new_field_log.json", to_primitive(package.created_field_log))
        debug.write_json("creator_action.json", {"action": "accept", "disposable_test_only": True})
        debug.write_json("promotion_or_discard_receipt.json", to_primitive(receipt))
        debug.write_json(
            "provider_routes.json",
            {
                "planner": provider_debug(planner_result),
                "composer": provider_debug(composer_result),
                "validator": provider_debug(validator_result),
            },
        )
        debug.write_json(
            "usage.json",
            {
                "planner_prompt_components": to_primitive(planner_usage),
                "composer_prompt_components": to_primitive(composer_usage),
                "validator_prompt_components": to_primitive(validator_usage),
            },
        )
        debug.write_json("stage_timings.json", {"recorded_in_provider_call_records": True})
        debug.write_json("errors.json", [])
        debug.write_json(
            "replay_input.json",
            {
                "turn_packet": packet,
                "character_summaries": to_primitive(summaries),
                "accepted_envelopes_sent": [],
                "accepted_envelope_injected_sha256": envelope.envelope_sha256,
                "scene_change_context": scene_change_context,
                "planner_output": to_primitive(sequence),
                "deepseek_output_sha256": text_sha256(story),
                "validator_output": to_primitive(package),
            },
        )
        if debug.validate_complete():
            raise RuntimeError("continuous turn debug artifact set is incomplete")
        snapshot_store = ContinuousSessionSnapshotStore(
            self.world.branch_root(WORLD_ID, BRANCH_ID)
        )
        self.planner_session.checkpoint(snapshot_store)
        self.validator_session.checkpoint(snapshot_store)
        return {
            "turn_id": turn_id,
            "sequence_sha256": sequence.sequence_sha256,
            "story_sha256": text_sha256(story),
            "validator_package_sha256": package.package_sha256,
            "promotion_receipt_sha256": receipt.receipt_sha256,
            "planner_prompt_usage": to_primitive(planner_usage),
            "composer_prompt_usage": to_primitive(composer_usage),
            "validator_prompt_usage": to_primitive(validator_usage),
            "character_summary_share": character_summary_share(planner_usage),
            "beat_count": len(sequence.beats),
            "rich_beat_field_coverage": all(
                all(
                    (
                        beat.actor_ids,
                        beat.evidence_grounded_perception,
                        beat.immediate_goal,
                        beat.relevant_character_pressures,
                        beat.selected_tactic,
                        beat.causal_explanation,
                        beat.observable_action_or_dialogue_direction,
                        beat.private_state_guidance,
                        beat.physical_material_continuity,
                        beat.resulting_state,
                        beat.deepseek_realization_space,
                        beat.source_evidence_bindings,
                    )
                )
                for beat in sequence.beats
            ),
            "operation_count": len(package.world_edit_operations),
            "changed_files": list(receipt.changed_files),
            "created_fields": list(receipt.created_fields),
            "debug_complete": True,
            "accepted_final_injected": injected,
        }

    def summarize_scene(self) -> dict[str, Any]:
        pairs = tuple(self.accepted_pairs[:2])
        accepted_ids = tuple(value.accepted_turn_id for value in pairs)
        prompt, usage = build_validator_prompt(
            task_mode=ValidatorTaskMode.SCENE_SUMMARY,
            current_user_source=None,
            planner_sequence=None,
            deepseek_realization=None,
            accepted_turn_id=None,
            accepted_scene_turn_ids=accepted_ids,
            accepted_pairs=tuple(to_primitive(value) for value in pairs),
            world_file_manifest=self.world.active_manifest(WORLD_ID, BRANCH_ID),
        )
        result = self.provider_call(
            "scene-1-validator-summary",
            "validator",
            lambda: self.codex_validator(
                prompt, "turn-003", accepted_pairs=pairs
            ),
        )
        package = result.value
        if package.optional_scene_summary is None:
            raise RuntimeError("Validator omitted the Scene 1 summary")
        envelope = SceneChangeCoordinator(self.world).process(
            world_id=WORLD_ID,
            branch_id=BRANCH_ID,
            completed_scene_id="scene-001",
            first_new_scene_prompt=TURN_MESSAGES[2],
            accepted_turn_ids=accepted_ids,
            summarize=lambda _pairs: package.optional_scene_summary,
        )
        self.validator_session.record_scene_summary("scene-001", package.package_sha256)
        self.planner_session.record_scene_change(
            "scene-001", canonical_sha256(to_primitive(envelope))
        )
        self.scene_change_envelope = envelope
        branch = self.world.branch_root(WORLD_ID, BRANCH_ID)
        debug = ContinuousDebugRecorder(branch, "scene-002", "turn-003")
        debug.initialize()
        debug.write_json("scene_change_request.json", {"prompt": prompt, "usage": to_primitive(usage)})
        debug.write_json("scene_change_output.json", to_primitive(package))
        debug.write_json("scene_change_tools.json", provider_debug(result))
        debug.write_json("scene_change_timing.json", {"recorded_in_provider_call_records": True})
        return {
            "package_sha256": package.package_sha256,
            "summary_sha256": canonical_sha256(package.optional_scene_summary),
            "accepted_turn_ids": list(accepted_ids),
            "exact_pair_count": len(package.optional_scene_summary.last_five_exact_pairs),
            "new_prompt_excluded": (
                TURN_MESSAGES[2]
                not in package.optional_scene_summary.shortest_complete_summary
            ),
            "same_validator_thread_sha256": text_sha256(self.validator_handle),
        }


def build_report(result: dict[str, Any]) -> str:
    calls = result.get("calls", [])
    lines = [
        "# Continuous Planner/Validator Job 4 Report",
        "",
        f"**Task:** `{TASK_ID}`  ",
        f"**Status:** `{result['status']}`  ",
        f"**Provider calls observed:** {result['provider_calls']} / 10  ",
        "**Retry/fallback:** 0 / 0",
        "",
        "## Route and isolation",
        "",
        "- Planner: `gpt-5.6-sol`, medium, Fast disabled.",
        "- Composer: `deepseek-v4-flash`, thinking disabled.",
        "- Validator: `gpt-5.6-terra`, high, Fast disabled.",
        f"- Planner thread hash: `{result.get('planner_thread_sha256')}`.",
        f"- Validator thread hash: `{result.get('validator_thread_sha256')}`.",
            f"- Separate threads: `{result.get('separate_thread_ids')}`.",
        f"- Codex continuity hashes verified: `{result.get('continuous_thread_hashes_verified')}`.",
        f"- Stored threads archived: `{result.get('thread_archival')}`.",
        "",
        "## Calls",
        "",
        "| # | Stage | Owner | Status | Wall seconds |",
        "|---:|---|---|---|---:|",
    ]
    for call in calls:
        lines.append(
            f"| {call['index']} | {call['label']} | {call['owner']} | {call['status']} | {call.get('duration_ns', 0) / 1_000_000_000:.3f} |"
        )
    lines.extend(
        (
            "",
            "## Verification",
            "",
            f"- Accepted disposable turns: {len(result.get('turns', []))}.",
            f"- Scene summary: `{result.get('scene_summary', {}).get('summary_sha256')}`.",
            f"- Turn 3 prompt excluded from Scene 1 summary: `{result.get('scene_summary', {}).get('new_prompt_excluded')}`.",
            f"- Source SQLite unchanged: `{result.get('source_database_unchanged')}`.",
            f"- Active route unchanged: `{result.get('active_route_unchanged')}`.",
            f"- Every accepted final sequence injected: `{all(value.get('accepted_final_injected') for value in result.get('turns', []))}`.",
            f"- Live story writes: `{result.get('story_database_writes')}`.",
            "- Complete raw prompts, outputs, tool traces, candidate snapshots, diffs, edit logs, receipts, usage, timings, errors, and replay inputs remain under the ignored disposable runtime root.",
            "",
            "## Terminal failure",
            "",
            canonical_json(result.get("failure")),
            "",
        )
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--cycle-directory", type=Path, required=True)
    parser.add_argument("--source-database", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--expected-checkpoint-sha", required=True)
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("--confirm-live is required")
    cycle = args.cycle_directory.resolve()
    source_db = args.source_database.resolve()
    runtime_root = args.runtime_root.resolve()
    if git_head(ROOT) != args.expected_checkpoint_sha:
        raise SystemExit("isolated worktree HEAD does not match the frozen checkpoint")
    manifest = json.loads((cycle / "CYCLE_MANIFEST.json").read_text(encoding="utf-8"))
    if manifest["cycle_id"] != CYCLE_ID or manifest["job4"]["task_id"] != TASK_ID:
        raise SystemExit("Job 4 cycle identity changed")
    if not (cycle / "receipts" / "TRIGGER_SENT.json").is_file():
        raise SystemExit("Job 4 cannot start before the Pro trigger receipt")
    report_path = cycle / "source" / "JOB4_REPORT.md"
    result_path = cycle / "source" / "JOB4_RESULT.json"
    if report_path.exists() or result_path.exists() or runtime_root.exists():
        raise SystemExit("refusing to overwrite Job 4 evidence")
    if not source_db.is_file():
        raise SystemExit("Hanezawa human-test SQLite source is unavailable")

    runtime_root.mkdir(parents=True)
    copied_db = runtime_root / "hanezawa_human_test_disposable.sqlite3"
    source_hash_before = bytes_sha256(source_db.read_bytes())
    shutil.copy2(source_db, copied_db)
    copy_hash_before = bytes_sha256(copied_db.read_bytes())
    with sqlite3.connect(f"file:{copied_db.as_posix()}?mode=ro", uri=True) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    world = ContinuousWorldStore(runtime_root / "worlds")
    seed_world(world, ROOT)
    branch_root = world.branch_root(WORLD_ID, BRANCH_ID)
    lifecycle_root = runtime_root / "provider_workspaces"
    lifecycle_root.mkdir()
    active_runtime_before = active_runtime_status()
    result: dict[str, Any] = {
        "schema_version": "cera.continuous_planner_validator_job4_detail.v1",
        "cycle_id": CYCLE_ID,
        "task_id": TASK_ID,
        "started_at": utc_now(),
        "status": "running",
        "provider_calls": 0,
        "retry_count": 0,
        "fallback_count": 0,
        "story_database_writes": 0,
        "active_runtime_before": active_runtime_before,
        "turns": [],
        "calls": [],
        "pro_polls": [],
        "source_database_sha256_before": source_hash_before,
        "copy_database_sha256_before": copy_hash_before,
        "copy_database_integrity": integrity,
        "copy_database_foreign_key_findings": len(foreign_keys),
        "runtime_root": str(runtime_root),
    }
    planner_handle = None
    validator_handle = None
    harness = None
    try:
        from openai_codex import Codex, CodexConfig

        with ExitStack() as stack:
            codex = stack.enter_context(
                Codex(CodexConfig(config_overrides=("mcp_servers={}",), env={}))
            )
            if codex.account().account is None:
                raise RuntimeError("ChatGPT Codex session is unavailable")
            planner_backend = OpenAICodexStoredThreadBackend(
                codex=codex,
                model="gpt-5.6-sol",
                cwd=str(lifecycle_root),
                base_instructions=(
                    _BASE_INSTRUCTIONS_BY_ROLE["scene_reasoner"]
                    + "\n\n"
                    + PLANNER_STABLE_INSTRUCTIONS
                ),
                service_name="cera_continuous_job4_planner",
            )
            validator_backend = OpenAICodexStoredThreadBackend(
                codex=codex,
                model="gpt-5.6-terra",
                cwd=str(lifecycle_root),
                base_instructions=(
                    _BASE_INSTRUCTIONS_BY_ROLE["scene_realization_verifier"]
                    + "\n\n"
                    + VALIDATOR_STABLE_INSTRUCTIONS
                ),
                service_name="cera_continuous_job4_validator",
            )
            planner_session = ContinuousSessionCoordinator(
                compatibility(world, ContinuousSessionRole.PLANNER),
                CodexContinuousStoredSessionPort(planner_backend),
            )
            validator_session = ContinuousSessionCoordinator(
                compatibility(world, ContinuousSessionRole.VALIDATOR),
                CodexContinuousStoredSessionPort(validator_backend),
            )
            assert_separate_role_sessions(planner_session, validator_session)
            planner_handle = planner_session.ensure_session().provider_thread_id
            validator_handle = validator_session.ensure_session().provider_thread_id
            harness = JobHarness(
                source_root=ROOT,
                cycle=cycle,
                world=world,
                planner_session=planner_session,
                validator_session=validator_session,
                planner_handle=planner_handle,
                validator_handle=validator_handle,
                lifecycle_root=lifecycle_root,
            )
            try:
                result.update(
                    {
                        "planner_thread_sha256": text_sha256(planner_handle),
                        "validator_thread_sha256": text_sha256(validator_handle),
                        "separate_thread_ids": planner_handle != validator_handle,
                    }
                )
                sakura_summary = source_character_summary(
                    ROOT,
                    "sakura",
                    world_file_revision=read_world_revision(world, "Sakura"),
                )
                result["turns"].append(
                    harness.run_turn(
                        turn_number=1,
                        scene_id="scene-001",
                        summaries=(sakura_summary,),
                    )
                )
                sakura_summary_2 = source_character_summary(
                    ROOT,
                    "sakura",
                    world_file_revision=read_world_revision(world, "Sakura"),
                    latest_changes=(
                        "Sakura directly heard Ted identify himself at the threshold.",
                    ),
                )
                result["turns"].append(
                    harness.run_turn(
                        turn_number=2,
                        scene_id="scene-001",
                        summaries=(sakura_summary_2,),
                    )
                )
                result["scene_summary"] = harness.summarize_scene()
                mia_summary = source_character_summary(
                    ROOT,
                    "mia",
                    world_file_revision=read_world_revision(world, "Mia"),
                )
                result["turns"].append(
                    harness.run_turn(
                        turn_number=3,
                        scene_id="scene-002",
                        summaries=(mia_summary,),
                        scene_change_context=to_primitive(harness.scene_change_envelope),
                    )
                )
                if len(harness.call_records) != 10:
                    raise RuntimeError("successful Job 4 did not use the exact ten-call schedule")
                if harness.provider_calls != 10:
                    raise RuntimeError("successful Job 4 provider-call accounting changed")
                if planner_session.unsynchronized_accepted_turn_ids:
                    raise RuntimeError("accepted Planner context remained unsynchronized")
                result["status"] = "completed"
            finally:
                archived: dict[str, bool] = {}
                archive_failed = False
                for role, backend, handle in (
                    ("planner", planner_backend, planner_handle),
                    ("validator", validator_backend, validator_handle),
                ):
                    if handle is None:
                        continue
                    try:
                        backend.archive_stored_thread(handle)
                        archived[role] = True
                    except Exception:
                        archived[role] = False
                        archive_failed = True
                result["thread_archival"] = archived
                if archive_failed:
                    raise RuntimeError("stored canary thread archival failed")
    except BaseException as exc:
        result["status"] = "failed"
        result["failure"] = {
            "stage": (
                harness.call_records[-1]["label"]
                if harness is not None and harness.call_records
                else "pre_provider"
            ),
            "error_type": type(exc).__name__,
            "message": "Terminal one-shot Job 4 failure; inspect privacy-safe stage evidence.",
        }
    finally:
        if harness is not None:
            result["calls"] = harness.call_records
            result["pro_polls"] = harness.poll_records
            result["provider_calls"] = harness.provider_calls
            result["continuous_thread_hashes_verified"] = all(
                call.get("result", {})
                .get("operation_telemetry", {})
                .get("provider_thread_id_sha256")
                == (
                    result.get("planner_thread_sha256")
                    if call.get("owner") == "planner"
                    else result.get("validator_thread_sha256")
                )
                for call in harness.call_records
                if call.get("owner") in {"planner", "validator"}
                and call.get("status") == "passed"
            )
        result["source_database_sha256_after"] = bytes_sha256(source_db.read_bytes())
        result["copy_database_sha256_after"] = bytes_sha256(copied_db.read_bytes())
        result["source_database_unchanged"] = (
            result["source_database_sha256_after"] == source_hash_before
        )
        result["copy_database_unchanged"] = (
            result["copy_database_sha256_after"] == copy_hash_before
        )
        try:
            result["active_runtime_after"] = active_runtime_status()
            result["active_route_unchanged"] = (
                canonical_sha256(result["active_runtime_after"])
                == canonical_sha256(active_runtime_before)
            )
        except Exception:
            result["active_runtime_after"] = {
                "valid": False,
                "diagnostic": "active runtime validation failed after Job 4",
            }
            result["active_route_unchanged"] = False
        result["world_active_sha256"] = world.tree_sha256(branch_root / "ACTIVE")
        result["finished_at"] = utc_now()
        detail_path = runtime_root / "JOB4_DETAIL.json"
        write_json(detail_path, result)
        report = build_report(result)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8", newline="\n")
        job4_result = {
            "schema_version": "cera.pro_review_job4_result.v1",
            "cycle_id": CYCLE_ID,
            "task_id": TASK_ID,
            "status": result["status"],
            "report_relative_path": "source/JOB4_REPORT.md",
            "report_sha256": text_sha256(report),
            "effects": {
                "provider_calls": int(result["provider_calls"]),
                "story_database_writes": 0,
                "active_route_changes": 0,
                "deployment_remote_or_push_effects": 0,
            },
            "verification": [
                {
                    "command": "continuous-planner-validator-three-turn-scene-change-canary-v1",
                    "status": "passed" if result["status"] == "completed" else "failed",
                    "summary": (
                        "Exact ten-call disposable canary completed"
                        if result["status"] == "completed"
                        else "Terminal one-shot canary stopped at the recorded owning stage"
                    ),
                },
                {
                    "command": "source-and-copy-sqlite-hash-check",
                    "status": (
                        "passed"
                        if result["source_database_unchanged"] and result["copy_database_unchanged"]
                        else "failed"
                    ),
                    "summary": "Source and disposable-copy hashes were compared before and after",
                },
            ],
        }
        write_json(result_path, job4_result)
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
