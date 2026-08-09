"""Qualify CERA's native stored Codex Reasoner lifecycle.

The command performs one prerequisite non-story MCP canary followed, only on
success, by five one-shot Sol-medium Reasoner turns on immutable stored-thread
candidate forks.  It never calls the Composer, publishes prose, commits story
state, or changes the active SillyTavern route.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from typing import Any, Iterator

from cera.evidence import (
    EvidenceAccessScope,
    EvidenceRequesterRole,
    EvidenceSnapshot,
    EvidenceWorldMode,
)
from cera.ids import IdKind, deterministic_id
from cera.kernel import TurnKernel
from cera.providers import (
    CodexSDKTransport,
    StoredCodexThreadRunner,
    codex_mcp_probe_output_schema,
    codex_reasoner_candidate,
)
from cera.providers.codex_worker import _BASE_INSTRUCTIONS_BY_ROLE
from cera.provider_dispatch_guard import assert_provider_dispatch_allowed
from cera.reasoner import (
    CodexSceneReasonerPort,
    ReasonerCoordinator,
    RequestBoundMcpEvidenceBridge,
)
from cera.reasoner.codex import build_codex_reasoner_packet, build_codex_reasoner_prompt
from cera.reasoner_session import OpenAICodexStoredThreadBackend
from cera.runtime import HanezawaHumanTestWorld
from cera.serialization import canonical_json, domain_sha256, text_sha256, to_primitive

from scripts.run_codex_branch_session_ab import outcome_summary, prepare_message


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    ROOT
    / "evaluation"
    / "evidence"
    / "native_stored_reasoner_live_five_2026-07-31_v4"
)
PACKET_MARKER = "The complete authoritative packet follows as canonical JSON:"
PROVISIONAL_CONTEXT_POLICY = (
    "STORED-THREAD AUTHORITY POLICY: Earlier CERA packets and Reasoner drafts "
    "in this stored thread are non-authoritative provisional context. The newest "
    "CERA packet is the complete current authority and supersedes every conflict. "
    "Never cite provider context as evidence or silently turn it into canon, "
    "character knowledge, or durable memory. Python remains final authority."
)
MESSAGES = (
    "Hello, my name is Ted. Is this the Hanezawa household?",
    (
        "Sakura has confirmed the address but remains cautious at the threshold. "
        'Ted says, "I am Ted, the housemate expected today."'
    ),
    (
        "Adjustment: preserve Sakura's established distrust and keep any trauma "
        "response subtle rather than making her openly welcoming. Ted waits "
        "outside without entering."
    ),
    (
        'Ted says, "I understand. You can verify me before letting me in." '
        "Continue according to Sakura's character and the household situation."
    ),
    (
        "Tomi hears Ted's name from inside and interrupts because she wants to "
        "see the expected housemate. Sakura still controls the doorway and "
        "responds according to her established character."
    ),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def split_active_prompt(prompt: str) -> tuple[str, str]:
    delimiter = "\n" + PACKET_MARKER + "\n"
    if prompt.count(delimiter) != 1:
        raise RuntimeError("active Reasoner prompt cannot be split exactly once")
    stable, packet_json = prompt.split(delimiter, 1)
    return stable, PACKET_MARKER + "\n" + packet_json


def safe_error(exc: BaseException, secret_values: tuple[str, ...]) -> str:
    message = str(exc) or type(exc).__name__
    for value in secret_values:
        message = message.replace(value, "[stored-thread-id-redacted]")
    return " ".join(message.split())[:1000]


class SnapshotOnlyEvidenceTools:
    """Synthetic fixture with no story, Genesis, database, or exact evidence."""

    def __init__(self, snapshot: EvidenceSnapshot) -> None:
        self.snapshot = snapshot
        self.tool_call_count = 0
        self.exact_evidence: dict = {}
        self.cumulative_returned_bytes = 0

    def evidence_budget_status(self) -> dict[str, int]:
        """Expose the same request-local budget contract as runtime evidence tools."""

        return {
            "maximum_followup_searches": 0,
            "used_followup_searches": 0,
            "remaining_followup_searches": 0,
        }

    def get_turn_snapshot(self) -> EvidenceSnapshot:
        self.tool_call_count += 1
        return self.snapshot

    def resolve_entities(self, _request):
        raise AssertionError("snapshot-only probe forbids entity resolution")

    def search_evidence(self, _request):
        raise AssertionError("snapshot-only probe forbids search")

    def fetch_evidence(self, _request):
        raise AssertionError("snapshot-only probe forbids fetch")

    def get_character_sections(self, _request):
        raise AssertionError("snapshot-only probe forbids character lookup")

    def get_continuity(self, _request):
        raise AssertionError("snapshot-only probe forbids continuity traversal")


def probe_id(kind: IdKind, suffix: str):
    return deterministic_id(kind, "cera.native_stored_reasoner_canary.v1", suffix)


def canary_snapshot() -> EvidenceSnapshot:
    return EvidenceSnapshot(
        schema_version=EvidenceSnapshot.SCHEMA_VERSION,
        snapshot_token=probe_id(IdKind.SNAPSHOT, "snapshot"),
        request_id=probe_id(IdKind.REQUEST, "request"),
        world_id=probe_id(IdKind.WORLD, "world"),
        branch_id=probe_id(IdKind.BRANCH, "branch"),
        generation=0,
        branch_head_artifact_id=None,
        genesis_revision_id=probe_id(IdKind.GENESIS_REVISION, "genesis"),
        access_scope=EvidenceAccessScope(
            requester_role=EvidenceRequesterRole.SYSTEM_REASONER,
            perspective_id=None,
            permitted_private_owner_ids=(),
            allow_system_private=False,
            allowed_content_classes=("ordinary",),
            allow_audit_history=False,
        ),
        visibility_policy_version="cera.native_stored_canary.v1",
        world_mode=EvidenceWorldMode.SYNTHETIC_FIXTURE,
        authority_revision=0,
    )


@contextmanager
def stored_backend(
    *, workspace: Path, base_instructions: str
) -> Iterator[OpenAICodexStoredThreadBackend]:
    assert_provider_dispatch_allowed("scripts.native_stored_reasoner.provider_runtime")
    from openai_codex import Codex, CodexConfig

    with Codex(CodexConfig(config_overrides=("mcp_servers={}",), env={})) as codex:
        if codex.account().account is None:
            raise RuntimeError("ChatGPT Codex session is unavailable")
        yield OpenAICodexStoredThreadBackend(
            codex=codex,
            model="gpt-5.6-sol",
            cwd=str(workspace),
            base_instructions=base_instructions,
            service_name="cera_native_stored_reasoner_live_five",
        )


def create_root(*, workspace: Path, base_instructions: str) -> str:
    with stored_backend(
        workspace=workspace, base_instructions=base_instructions
    ) as backend:
        return backend.start_stored_thread()


def fork_thread(
    parent_thread_id: str, *, workspace: Path, base_instructions: str
) -> str:
    with stored_backend(
        workspace=workspace, base_instructions=base_instructions
    ) as backend:
        if not backend.resume_stored_thread(parent_thread_id):
            raise RuntimeError("stored parent checkpoint could not be resumed")
        return backend.fork_stored_thread(parent_thread_id)


def resume_thread(
    thread_id: str, *, workspace: Path, base_instructions: str
) -> bool:
    with stored_backend(
        workspace=workspace, base_instructions=base_instructions
    ) as backend:
        return backend.resume_stored_thread(thread_id)


def archive_threads_leaf_first(
    thread_ids: list[str], *, workspace: Path, base_instructions: str
) -> list[str]:
    archived_hashes: list[str] = []
    with stored_backend(
        workspace=workspace, base_instructions=base_instructions
    ) as backend:
        for thread_id in reversed(thread_ids):
            backend.archive_stored_leaf(thread_id)
            archived_hashes.append(text_sha256(thread_id))
    return archived_hashes


class SplitPromptTransport:
    def __init__(self, transport: CodexSDKTransport, stable_instructions: str) -> None:
        self.transport = transport
        self.route = transport.route
        self.stable_instructions = stable_instructions

    def invoke(self, prompt: str, **kwargs):
        stable, variable = split_active_prompt(prompt)
        if stable != self.stable_instructions:
            raise RuntimeError("Reasoner stable instructions changed within the batch")
        return self.transport.invoke(variable, **kwargs)


def run_canary(output: Path) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": "cera.native_stored_reasoner_canary.v4",
        "started_at": utc_now(),
        "status": "running",
        "provider_calls": 0,
        "retry_count": 0,
        "fallback_count": 0,
        "story_state_committed": False,
    }
    write_json(output / "canary.json", record)
    thread_ids: list[str] = []
    with TemporaryDirectory(prefix="cera-native-stored-canary-") as temporary:
        temporary_path = Path(temporary)
        lifecycle_workspace = temporary_path / "lifecycle"
        lifecycle_workspace.mkdir()
        turn_workspace = temporary_path / "turn"
        turn_workspace.mkdir()
        base_instructions = (
            _BASE_INSTRUCTIONS_BY_ROLE["scene_reasoner"]
            + "\n\nSynthetic non-story stored-session MCP qualification only."
        )
        snapshot = canary_snapshot()
        request_sha256 = domain_sha256(
            "cera.native_stored_reasoner_canary.request.v1",
            {"snapshot_binding_sha256": snapshot.binding_sha256},
        )
        tools = SnapshotOnlyEvidenceTools(snapshot)
        bridge = RequestBoundMcpEvidenceBridge(
            tools,
            reasoner_request_sha256=request_sha256,
            snapshot=snapshot,
            minimum_tool_calls=1,
        )
        started = time.perf_counter()
        try:
            root = create_root(
                workspace=lifecycle_workspace,
                base_instructions=base_instructions,
            )
            thread_ids.append(root)
            candidate = fork_thread(
                root,
                workspace=lifecycle_workspace,
                base_instructions=base_instructions,
            )
            thread_ids.append(candidate)
            route = codex_reasoner_candidate(model="gpt-5.6-sol", effort="medium")
            record["provider_calls"] = 1
            with bridge:
                result = CodexSDKTransport(
                    route,
                    workspace=turn_workspace,
                    runner=StoredCodexThreadRunner(candidate),
                ).invoke(
                    (
                        "This is a synthetic transport probe with no story, character, "
                        "Genesis, or adult content. Call cera_get_turn_snapshot exactly "
                        "once. Copy its snapshot_token into the result, set status to "
                        "ok, set tool_calls to 1, and call no other tool."
                    ),
                    output_schema=codex_mcp_probe_output_schema(),
                    mcp_binding=bridge.runtime_binding,
                )
                bridge_receipt = bridge.finalize(result)
            expected = {
                "status": "ok",
                "snapshot_token": str(snapshot.snapshot_token),
                "tool_calls": 1,
            }
            if result.parsed_json != expected or tools.tool_call_count != 1:
                raise RuntimeError("stored-session canary returned the wrong payload")
            if not resume_thread(
                candidate,
                workspace=lifecycle_workspace,
                base_instructions=base_instructions,
            ):
                raise RuntimeError("stored candidate could not be resumed after its turn")
            receipt = result.receipt
            record.update(
                {
                    "status": "passed",
                    "root_thread_sha256": text_sha256(root),
                    "candidate_thread_sha256": text_sha256(candidate),
                    "resume_after_turn": True,
                    "payload_valid": True,
                    "tool_call_count": result.tool_call_count,
                    "bridge_receipt_sha256": bridge_receipt.receipt_sha256,
                    "duration_seconds": round(receipt.duration_ms / 1000, 3),
                    "input_tokens": receipt.input_tokens,
                    "cached_input_tokens": receipt.cached_input_tokens,
                    "output_tokens": receipt.output_tokens,
                    "reasoning_output_tokens": receipt.reasoning_output_tokens,
                }
            )
        except Exception as exc:
            record.update(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error_message": safe_error(exc, tuple(thread_ids)),
                }
            )
        finally:
            if thread_ids:
                try:
                    record["archived_thread_sha256"] = archive_threads_leaf_first(
                        thread_ids,
                        workspace=lifecycle_workspace,
                        base_instructions=base_instructions,
                    )
                    record["cleanup_status"] = "archived_leaf_first"
                except Exception as exc:
                    record["cleanup_status"] = "failed"
                    record["cleanup_error_type"] = type(exc).__name__
                    record["cleanup_error_message"] = safe_error(
                        exc, tuple(thread_ids)
                    )
                    record["status"] = "failed"
            record["wall_seconds"] = round(time.perf_counter() - started, 3)
            record["finished_at"] = utc_now()
            write_json(output / "canary.json", record)
    return record


def run_five(output: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "schema_version": "cera.native_stored_reasoner_live_five.v4",
        "started_at": utc_now(),
        "status": "running",
        "model": "gpt-5.6-sol",
        "effort": "medium",
        "calls_authorized": 5,
        "provider_calls": 0,
        "retry_count": 0,
        "fallback_count": 0,
        "deepseek_calls": 0,
        "story_state_committed": False,
        "production_route_changed": False,
        "sillytavern_changed": False,
        "stored_context_is_story_authority": False,
        "turns": [],
    }
    write_json(output / "five_run.json", summary)
    thread_ids: list[str] = []
    with TemporaryDirectory(prefix="cera-native-stored-five-") as temporary:
        temporary_path = Path(temporary)
        lifecycle_workspace = temporary_path / "lifecycle"
        lifecycle_workspace.mkdir()
        world = HanezawaHumanTestWorld.initialize(
            ROOT, temporary_path / "world.sqlite3", replace=True
        )
        requests = tuple(
            prepare_message(
                world,
                message=message,
                session_id="native-stored-reasoner-live-five",
            ).application_request.reasoner_request
            for message in MESSAGES
        )
        packets = tuple(
            build_codex_reasoner_packet(request, evidence_tools_available=True)
            for request in requests
        )
        prompts = tuple(build_codex_reasoner_prompt(packet) for packet in packets)
        stable_instructions, _ = split_active_prompt(prompts[0])
        if any(
            split_active_prompt(prompt)[0] != stable_instructions
            for prompt in prompts
        ):
            raise RuntimeError("active Reasoner stable prefix varied across turns")
        base_instructions = (
            _BASE_INSTRUCTIONS_BY_ROLE["scene_reasoner"]
            + "\n\n"
            + PROVISIONAL_CONTEXT_POLICY
            + "\n\n"
            + stable_instructions
        )
        summary["stable_instructions_sha256"] = text_sha256(stable_instructions)
        summary["base_instructions_sha256"] = text_sha256(base_instructions)
        route = codex_reasoner_candidate(model="gpt-5.6-sol", effort="medium")
        coordinator = ReasonerCoordinator(world.service, TurnKernel(world.service))
        started = time.perf_counter()
        try:
            root = create_root(
                workspace=lifecycle_workspace,
                base_instructions=base_instructions,
            )
            thread_ids.append(root)
            parent = root
            for index, (message, request, packet, prompt) in enumerate(
                zip(MESSAGES, requests, packets, prompts), start=1
            ):
                turn: dict[str, Any] = {
                    "index": index,
                    "status": "started",
                    "message_sha256": text_sha256(message),
                    "request_sha256": request.request_sha256,
                    "packet_sha256": text_sha256(canonical_json(packet)),
                    "packet_bytes": len(canonical_json(packet).encode("utf-8")),
                    "legacy_full_prompt_sha256": text_sha256(prompt),
                    "variable_prompt_sha256": text_sha256(
                        split_active_prompt(prompt)[1]
                    ),
                    "parent_thread_sha256": text_sha256(parent),
                }
                turn_started = time.perf_counter()
                candidate: str | None = None
                try:
                    candidate = fork_thread(
                        parent,
                        workspace=lifecycle_workspace,
                        base_instructions=base_instructions,
                    )
                    thread_ids.append(candidate)
                    turn["candidate_thread_sha256"] = text_sha256(candidate)
                    turn_workspace = temporary_path / f"turn_{index}"
                    turn_workspace.mkdir()
                    transport = SplitPromptTransport(
                        CodexSDKTransport(
                            route,
                            workspace=turn_workspace,
                            runner=StoredCodexThreadRunner(candidate),
                        ),
                        stable_instructions,
                    )
                    summary["provider_calls"] += 1
                    call = coordinator.execute(
                        request,
                        CodexSceneReasonerPort(
                            transport,
                            evidence_tools_enabled=True,
                        ),
                    )
                    if not resume_thread(
                        candidate,
                        workspace=lifecycle_workspace,
                        base_instructions=base_instructions,
                    ):
                        raise RuntimeError(
                            "stored candidate could not be resumed after validation"
                        )
                    receipt = call.provider_call_receipt
                    parent = candidate
                    turn.update(
                        {
                            "status": "passed",
                            "benchmark_parent_promoted": True,
                            "provider_receipt": to_primitive(receipt),
                            "duration_seconds": round(receipt.duration_ms / 1000, 3),
                            "input_tokens": receipt.input_tokens,
                            "cached_input_tokens": receipt.cached_input_tokens,
                            "uncached_input_tokens": (
                                receipt.input_tokens - receipt.cached_input_tokens
                            ),
                            "output_tokens": receipt.output_tokens,
                            "reasoning_output_tokens": receipt.reasoning_output_tokens,
                            "mcp_tool_call_count": (
                                call.mcp_bridge_receipt.provider_observed_tool_calls
                                if call.mcp_bridge_receipt is not None
                                else 0
                            ),
                            "outcome": outcome_summary(call),
                        }
                    )
                except Exception as exc:
                    turn.update(
                        {
                            "status": "failed",
                            "benchmark_parent_promoted": False,
                            "error_type": type(exc).__name__,
                            "error_message": safe_error(exc, tuple(thread_ids)),
                        }
                    )
                    if getattr(exc, "provider_call_receipt", None) is not None:
                        turn["provider_receipt"] = to_primitive(
                            exc.provider_call_receipt
                        )
                turn["wall_seconds"] = round(
                    time.perf_counter() - turn_started, 3
                )
                summary["turns"].append(turn)
                write_json(output / "five_run.json", summary)
                print(
                    "CERA_NATIVE_STORED_TURN="
                    + canonical_json(
                        {
                            "index": index,
                            "status": turn["status"],
                            "duration_seconds": turn.get("duration_seconds"),
                            "cached_input_tokens": turn.get("cached_input_tokens"),
                            "mcp_tool_call_count": turn.get("mcp_tool_call_count"),
                        }
                    ),
                    flush=True,
                )
                if turn["status"] != "passed":
                    break
        finally:
            if thread_ids:
                try:
                    summary["archived_thread_sha256"] = archive_threads_leaf_first(
                        thread_ids,
                        workspace=lifecycle_workspace,
                        base_instructions=base_instructions,
                    )
                    summary["cleanup_status"] = "archived_leaf_first"
                except Exception as exc:
                    summary["cleanup_status"] = "failed"
                    summary["cleanup_error_type"] = type(exc).__name__
                    summary["cleanup_error_message"] = safe_error(
                        exc, tuple(thread_ids)
                    )
            summary["finished_at"] = utc_now()
            summary["passed"] = sum(
                turn["status"] == "passed" for turn in summary["turns"]
            )
            summary["failed"] = len(summary["turns"]) - summary["passed"]
            summary["status"] = (
                "passed"
                if summary["passed"] == 5 and summary.get("cleanup_status")
                == "archived_leaf_first"
                else "completed_with_failures"
            )
            summary["wall_seconds"] = round(time.perf_counter() - started, 3)
            summary["database_validation"] = {
                "artifact_count": world.store.table_count("artifacts"),
                "integrity_check": list(world.store.integrity_check()),
                "foreign_key_findings": len(world.store.foreign_key_check()),
            }
            write_json(output / "five_run.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("--confirm-live is required")
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite live evidence: {output}")
    output.mkdir(parents=True)

    canary = run_canary(output)
    print(
        "CERA_NATIVE_STORED_CANARY="
        + canonical_json(
            {
                "status": canary["status"],
                "provider_calls": canary["provider_calls"],
                "duration_seconds": canary.get("duration_seconds"),
                "cleanup_status": canary.get("cleanup_status"),
            }
        ),
        flush=True,
    )
    if canary["status"] != "passed":
        return 1

    five = run_five(output)
    print(
        "CERA_NATIVE_STORED_FIVE_RESULT="
        + canonical_json(
            {
                "status": five["status"],
                "passed": five["passed"],
                "failed": five["failed"],
                "provider_calls": five["provider_calls"],
                "wall_seconds": five["wall_seconds"],
                "cleanup_status": five.get("cleanup_status"),
            }
        ),
        flush=True,
    )
    return 0 if five["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
