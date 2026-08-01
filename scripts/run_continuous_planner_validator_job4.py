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
from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.continuous.diagnostics import ContinuousRootDiagnosticRecorder
from cera.continuous.evidence import (
    build_character_summary_envelope,
)
from cera.continuous.contracts import (
    AcceptedTurnPairV1,
    CharacterSummaryEnvelopeV1,
    RichPlannerSequenceV1,
)
from cera.continuous.prompting import (
    CONTINUOUS_PLANNER_PROMPT_VERSION,
    CONTINUOUS_VALIDATOR_PROMPT_VERSION,
    PLANNER_STABLE_INSTRUCTIONS,
    VALIDATOR_STABLE_INSTRUCTIONS,
)
from cera.continuous.provider import (
    CodexContinuousPlannerPort,
    CodexContinuousValidatorPort,
    DeepSeekContinuousComposerPort,
    ContinuousValidatorDraftV1,
    continuous_deepseek_route,
    continuous_planner_route,
    continuous_validator_route,
)
from cera.continuous.sessions import (
    ContinuousSessionCompatibilityV1,
    ContinuousSessionCoordinator,
    ContinuousSessionRole,
    assert_separate_role_sessions,
)
from cera.continuous.runtime import (
    ContinuousShadowTurnCoordinator,
    ContinuousTurnRequestV1,
)
from cera.continuous.world import (
    ContinuousWorldStore,
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
    world: ContinuousWorldStore,
    world_file_revision: int,
    latest_changes: tuple[str, ...] = (),
) -> CharacterSummaryEnvelopeV1:
    del root, world_file_revision, latest_changes
    return build_character_summary_envelope(
        branch_root=world.branch_root(WORLD_ID, BRANCH_ID),
        source_path=f"ACTIVE/Characters/{character.capitalize()}.json",
        character_id=f"character:{character}_hanezawa",
    )


def seed_world(store: ContinuousWorldStore, root: Path) -> None:
    package = root / "genesis" / "packages" / "hanezawa_core_v1_2" / "modules"
    for name in ("hana", "sakura", "mia", "enne", "tomi", "aoi", "yuuni"):
        source = package / "characters" / f"{name}.json"
        module = json.loads(source.read_text(encoding="utf-8"))
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
            raise RuntimeError(f"{name} concise summary source sections are incomplete")
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
                "reasoning_summary": "\n\n".join(sections),
                "latest_accepted_changes": [],
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
            CONTINUOUS_PLANNER_PROMPT_VERSION
            if role is ContinuousSessionRole.PLANNER
            else CONTINUOUS_VALIDATOR_PROMPT_VERSION
        ),
        output_schema_version=(
            RichPlannerSequenceV1.SCHEMA_VERSION
            if role is ContinuousSessionRole.PLANNER
            else ContinuousValidatorDraftV1.SCHEMA_VERSION
        ),
        world_directory_identity_sha256=world.world_identity_sha256(WORLD_ID, BRANCH_ID),
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


class _HarnessPlannerPort:
    def __init__(self, harness: "JobHarness") -> None:
        self.harness = harness

    def plan(self, prompt: str):
        number = self.harness._active_turn_number
        turn_id = self.harness._active_turn_id
        return self.harness.provider_call(
            f"turn-{number}-planner",
            "planner",
            lambda: self.harness.codex_planner(prompt, turn_id),
        )


class _HarnessComposerPort:
    def __init__(self, harness: "JobHarness") -> None:
        self.harness = harness

    def compose(self, prompt: str):
        number = self.harness._active_turn_number
        return self.harness.provider_call(
            f"turn-{number}-deepseek",
            "composer",
            lambda: self.harness.deepseek(prompt),
        )


class _HarnessValidatorPort:
    def __init__(self, harness: "JobHarness") -> None:
        self.harness = harness

    def validate(self, prompt: str, **kwargs):
        number = self.harness._active_turn_number
        turn_id = self.harness._active_turn_id
        return self.harness.provider_call(
            self.harness._active_validator_label or f"turn-{number}-validator",
            "validator",
            lambda: self.harness.codex_validator(
                prompt,
                turn_id,
                accepted_pairs=tuple(kwargs.get("accepted_pairs", ())),
            ),
        )


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
        call_ledger: ContinuousProviderCallLedger,
        root_diagnostic: ContinuousRootDiagnosticRecorder | None = None,
    ) -> None:
        self.source_root = source_root
        self.cycle = cycle
        self.world = world
        self.planner_session = planner_session
        self.validator_session = validator_session
        self.planner_handle = planner_handle
        self.validator_handle = validator_handle
        self.lifecycle_root = lifecycle_root
        self.call_ledger = call_ledger
        self.root_diagnostic = root_diagnostic
        self.accepted_pairs: list[AcceptedTurnPairV1] = []
        self.call_records: list[dict[str, Any]] = []
        self.poll_records: list[dict[str, Any]] = []
        self.provider_calls = 0
        self.scene_change_envelope = None
        self._active_turn_number = 0
        self._active_turn_id = ""
        self._active_validator_label: str | None = None
        self.coordinator = ContinuousShadowTurnCoordinator(
            world=world,
            planner_session=planner_session,
            validator_session=validator_session,
            planner=_HarnessPlannerPort(self),
            composer=_HarnessComposerPort(self),
            validator=_HarnessValidatorPort(self),
        )

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
            parsed = parse_response(
                data,
                template_hash,
                require_planning_sections=manifest["cycle_sequence"] >= 7,
            )
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
            self.provider_calls = self.call_ledger.dispatched_call_count
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
            before_count = self.provider_calls
            self.provider_calls = self.call_ledger.dispatched_call_count
            observed = self.provider_calls - before_count
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
        self._create_workspace(workspace, "create_planner_call_workspace")
        dispatcher = ContinuousWorldToolDispatcher(
            self.world.branch_root(WORLD_ID, BRANCH_ID),
            ContinuousSessionRole.PLANNER,
            current_turn_id=turn_id,
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
            return CodexContinuousPlannerPort(
                transport, world_bridge=bridge, call_ledger=self.call_ledger
            ).plan(prompt)

    def codex_validator(
        self,
        prompt: str,
        turn_id: str,
        *,
        accepted_pairs: tuple[AcceptedTurnPairV1, ...] = (),
    ):
        workspace = self.lifecycle_root / f"call_{len(self.call_records) + 1:02d}_validator"
        self._create_workspace(workspace, "create_validator_call_workspace")
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
                transport, world_bridge=bridge, call_ledger=self.call_ledger
            ).validate(prompt, accepted_pairs=accepted_pairs)

    def _create_workspace(self, workspace: Path, operation: str) -> None:
        if self.root_diagnostic is None:
            workspace.mkdir()
            return
        self.root_diagnostic.run("lifecycle_directory", operation, workspace.mkdir)

    def deepseek(self, prompt: str):
        return DeepSeekContinuousComposerPort(
            DeepSeekChatTransport(continuous_deepseek_route()),
            call_ledger=self.call_ledger,
        ).compose(prompt)

    # The canary executes turns through the exact shared
    # ContinuousShadowTurnCoordinator used by runtime instead of maintaining a
    # second orchestration implementation.
    def run_turn(
        self,
        *,
        turn_number: int,
        scene_id: str,
        summaries: tuple[CharacterSummaryEnvelopeV1, ...],
        scene_change_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._active_turn_number = turn_number
        self._active_turn_id = f"turn-{turn_number:03d}"
        self._active_validator_label = None
        request = ContinuousTurnRequestV1(
            world_id=WORLD_ID,
            branch_id=BRANCH_ID,
            scene_id=scene_id,
            turn_id=self._active_turn_id,
            user_message=TURN_MESSAGES[turn_number - 1],
            current_authority_packet={
                "schema_version": "cera.continuous_job4_turn_packet.v2",
                "protected_user_id": "character:ted",
                "content_class": "ordinary",
                "depth": "auto",
                "story_posture": (
                    "initial doorway turn; Sakura is the primary relevant household character"
                    if turn_number == 1
                    else "continue from exact accepted current-scene authority"
                ),
                "hard_boundaries": (
                    "Do not invent Ted's unsupplied thought, dialogue, or consequential action.",
                    "Preserve character knowledge ownership and branch isolation.",
                    "Stop only after a materially developed unit reaches a real protected-user choice.",
                ),
            },
            character_summaries=summaries,
            cera_scene_change=scene_change_context is not None,
        )
        if scene_change_context is None:
            candidate = self.coordinator.prepare(request)
        else:
            if self.scene_change_envelope is None:
                raise RuntimeError("validated scene-change envelope is unavailable")
            candidate = self.coordinator.prepare_after_validated_scene_change(
                request,
                scene_change_envelope=self.scene_change_envelope,
                summary_provider_calls=1,
            )
        package = candidate.validator_package
        if not package.permits_disposable_acceptance(CreatorReviewAction.ACCEPT):
            raise RuntimeError("Validator package did not qualify for disposable acceptance")
        receipt = self.coordinator.apply_creator_action(
            self._active_turn_id, CreatorReviewAction.ACCEPT
        )
        pair = self.world.accepted_turn_pairs(
            WORLD_ID, BRANCH_ID, (self._active_turn_id,)
        )[0]
        self.accepted_pairs.append(pair)
        usage = json.loads(
            (candidate.debug_root / "usage.json").read_text(encoding="utf-8")
        )
        planner_usage = tuple(usage.get("planner_prompt_components", ()))
        summary_bytes = sum(
            int(value.get("byte_count", 0))
            for value in planner_usage
            if value.get("component") == "character_summaries"
        )
        total_bytes = sum(int(value.get("byte_count", 0)) for value in planner_usage)
        return {
            "turn_id": self._active_turn_id,
            "sequence_sha256": candidate.planner_sequence.sequence_sha256,
            "story_sha256": text_sha256(candidate.deepseek_story_text),
            "validator_package_sha256": package.package_sha256,
            "promotion_receipt_sha256": receipt.receipt_sha256,
            "planner_prompt_usage": planner_usage,
            "composer_prompt_usage": tuple(usage.get("composer_prompt_components", ())),
            "validator_prompt_usage": tuple(usage.get("validator_prompt_components", ())),
            "character_summary_share": 0.0 if total_bytes == 0 else summary_bytes / total_bytes,
            "beat_count": len(candidate.planner_sequence.beats),
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
                for beat in candidate.planner_sequence.beats
            ),
            "operation_count": len(package.world_edit_operations),
            "changed_files": list(receipt.changed_files),
            "created_fields": list(receipt.created_fields),
            "debug_complete": not candidate.debug_root.joinpath("errors.json").is_symlink(),
            "accepted_final_injected": True,
        }

    def summarize_scene(self) -> dict[str, Any]:
        self._active_turn_number = 3
        self._active_turn_id = "turn-003"
        self._active_validator_label = "scene-1-validator-summary"
        request = ContinuousTurnRequestV1(
            world_id=WORLD_ID,
            branch_id=BRANCH_ID,
            scene_id="scene-002",
            turn_id="turn-003",
            user_message=TURN_MESSAGES[2],
            current_authority_packet={"protected_user_id": "character:ted"},
            cera_scene_change=True,
        )
        summary = self.coordinator.prepare_scene_change_summary(
            request,
            completed_scene_id="scene-001",
            accepted_turn_ids=tuple(value.accepted_turn_id for value in self.accepted_pairs[:2]),
        )
        self.scene_change_envelope = summary.scene_change_envelope
        package = summary.scene_summary_package
        return {
            "package_sha256": package.package_sha256,
            "summary_sha256": canonical_sha256(package.optional_scene_summary),
            "accepted_turn_ids": list(
                package.optional_scene_summary.accepted_turn_ids
            ),
            "exact_pair_count": len(
                package.optional_scene_summary.last_five_exact_pairs
            ),
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
    root_diagnostic = ContinuousRootDiagnosticRecorder(runtime_root)
    if not source_db.is_file():
        error = FileNotFoundError("Hanezawa human-test SQLite source is unavailable")
        root_diagnostic.record_failure("source_database", "inspect_source_database", error)
        raise SystemExit(str(error))
    call_ledger = ContinuousProviderCallLedger(
        runtime_root / "PROVIDER_CALL_LEDGER.jsonl", maximum_calls=10
    )
    copied_db = runtime_root / "hanezawa_human_test_disposable.sqlite3"
    source_hash_before = root_diagnostic.run(
        "source_database", "hash_source_database", lambda: bytes_sha256(source_db.read_bytes())
    )
    root_diagnostic.run(
        "source_database", "copy_disposable_database", lambda: shutil.copy2(source_db, copied_db)
    )
    copy_hash_before = bytes_sha256(copied_db.read_bytes())
    connection = root_diagnostic.run(
        "source_database",
        "open_disposable_database_read_only",
        lambda: sqlite3.connect(f"file:{copied_db.as_posix()}?mode=ro", uri=True),
    )
    try:
        integrity = root_diagnostic.run(
            "source_database",
            "check_disposable_database_integrity",
            lambda: connection.execute("PRAGMA integrity_check").fetchone()[0],
        )
        foreign_keys = root_diagnostic.run(
            "source_database",
            "check_disposable_database_foreign_keys",
            lambda: connection.execute("PRAGMA foreign_key_check").fetchall(),
        )
    except BaseException:
        root_diagnostic.run(
            "source_database",
            "close_disposable_database_after_check_failure",
            connection.close,
        )
        raise
    root_diagnostic.run(
        "source_database",
        "close_disposable_database",
        connection.close,
    )
    world = root_diagnostic.run(
        "world_construction",
        "construct_continuous_world_store",
        lambda: ContinuousWorldStore(runtime_root / "worlds"),
    )
    root_diagnostic.run("world_seeding", "seed_disposable_world", lambda: seed_world(world, ROOT))
    branch_root = world.branch_root(WORLD_ID, BRANCH_ID)
    lifecycle_root = runtime_root / "provider_workspaces"
    root_diagnostic.run(
        "lifecycle_directory",
        "create_provider_workspace_root",
        lifecycle_root.mkdir,
    )
    active_runtime_before = root_diagnostic.run(
        "active_profile", "inspect_active_runtime_profile", active_runtime_status
    )
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
        def import_codex_sdk():
            from openai_codex import Codex, CodexConfig

            return Codex, CodexConfig

        Codex, CodexConfig = root_diagnostic.run(
            "sdk_capability", "import_codex_sdk", import_codex_sdk
        )

        with ExitStack() as stack:
            codex_context = root_diagnostic.run(
                "backend_construction",
                "construct_codex_client",
                lambda: Codex(CodexConfig(config_overrides=("mcp_servers={}",), env={})),
            )
            codex = root_diagnostic.run(
                "backend_context",
                "enter_codex_client_context",
                lambda: stack.enter_context(codex_context),
            )
            account = root_diagnostic.run(
                "account_inspection", "inspect_codex_account", codex.account
            )
            if account.account is None:
                raise RuntimeError("ChatGPT Codex session is unavailable")
            planner_backend = root_diagnostic.run(
                "backend_construction",
                "construct_planner_backend",
                lambda: OpenAICodexStoredThreadBackend(
                    codex=codex,
                    model="gpt-5.6-sol",
                    cwd=str(lifecycle_root),
                    base_instructions=(
                        _BASE_INSTRUCTIONS_BY_ROLE["scene_reasoner"]
                        + "\n\n"
                        + PLANNER_STABLE_INSTRUCTIONS
                    ),
                    service_name="cera_continuous_job4_planner",
                ),
            )
            validator_backend = root_diagnostic.run(
                "backend_construction",
                "construct_validator_backend",
                lambda: OpenAICodexStoredThreadBackend(
                    codex=codex,
                    model="gpt-5.6-terra",
                    cwd=str(lifecycle_root),
                    base_instructions=(
                        _BASE_INSTRUCTIONS_BY_ROLE["scene_realization_verifier"]
                        + "\n\n"
                        + VALIDATOR_STABLE_INSTRUCTIONS
                    ),
                    service_name="cera_continuous_job4_validator",
                ),
            )
            planner_compatibility = root_diagnostic.run(
                "compatibility_creation",
                "create_planner_compatibility",
                lambda: compatibility(world, ContinuousSessionRole.PLANNER),
            )
            validator_compatibility = root_diagnostic.run(
                "compatibility_creation",
                "create_validator_compatibility",
                lambda: compatibility(world, ContinuousSessionRole.VALIDATOR),
            )
            planner_session = root_diagnostic.run(
                "session_construction",
                "construct_planner_session",
                lambda: ContinuousSessionCoordinator(
                    planner_compatibility,
                    CodexContinuousStoredSessionPort(planner_backend),
                ),
            )
            validator_session = root_diagnostic.run(
                "session_construction",
                "construct_validator_session",
                lambda: ContinuousSessionCoordinator(
                    validator_compatibility,
                    CodexContinuousStoredSessionPort(validator_backend),
                ),
            )
            planner_handle = root_diagnostic.run(
                "stored_thread_construction",
                "ensure_planner_stored_thread",
                lambda: planner_session.ensure_session().provider_thread_id,
            )
            validator_handle = root_diagnostic.run(
                "stored_thread_construction",
                "ensure_validator_stored_thread",
                lambda: validator_session.ensure_session().provider_thread_id,
            )
            root_diagnostic.run(
                "role_separation",
                "assert_materialized_role_separation",
                lambda: assert_separate_role_sessions(
                    planner_session, validator_session
                ),
            )
            harness = JobHarness(
                source_root=ROOT,
                cycle=cycle,
                world=world,
                planner_session=planner_session,
                validator_session=validator_session,
                planner_handle=planner_handle,
                validator_handle=validator_handle,
                lifecycle_root=lifecycle_root,
                call_ledger=call_ledger,
                root_diagnostic=root_diagnostic,
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
                    world=world,
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
                    world=world,
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
                    world=world,
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
