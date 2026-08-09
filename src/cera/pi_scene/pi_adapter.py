"""Dedicated Pi + DeepSeek subprocess adapter for CERA scene work.

The adapter launches Pi with no built-in tools, no discovered extensions,
skills, prompt templates, or context files.  Only the repository-pinned CERA
Writer-view extension is loaded.  Provider/session state is a replaceable
performance layer; the caller supplies a fully materialized accepted view.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
from threading import Thread
import time
from typing import Callable, Mapping, Sequence
from uuid import NAMESPACE_URL, uuid5

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, text_sha256

from .contracts import PiWriterReceiptV1, SceneRoute
from .operation_ledger import PiProviderOperationLedger
from .readable_debug import ReadablePiSceneDebugLog
from .store import AcceptedPiSessionV1
from .writer_view import MaterializedWriterViewV1, verify_writer_view


ORDINARY_WRITER_SYSTEM_PROMPT = """You are CERA's DeepSeek Scene Writer operating inside a dedicated read-only Pi Scene session. Call the tool named context directly exactly once before writing; do not call a tool named invoke. Its branch-scoped view is the complete authorized context. Apply zz_CURRENT_TURN_AUTHORITY.json first: RESPONSE_SEQUENCE.json is the sole realization obligation; CURRENT_STATE.json is accepted present-tense state; accepted records and recent prose are supporting continuity only. Python preserves the complete canonical Planner sequence outside the Writer root. USER_PROMPT.txt is completed off-page source context. Never mention, describe, quote, paraphrase, or stage any action or dialogue it contains, including as an opening or temporal lead-in. A completed_source_anchor_key marks an already-finished cause, not a prose beat.

RESPONSE_SEQUENCE.json separates internal_causal_guidance from surface_realization_items. Internal guidance is bounded subtext, not a standalone visible beat. Each internal item names guides_surface_item_key and each surface item names guided_by_item_keys. Carry that exact causal psychology into the linked surface response. It may remain implicit, or be expressed only after the linked surface response has begun and only within its authorized semantics. Never expand it into unsupported physical condition, noticed-condition knowledge, household or day history, recurring practice, trust development, setting or object provenance, or prior relationship.

The context tool ends with FINAL RESPONSE START GATE. That gate is a derived noncanonical execution focus copied from RESPONSE_SEQUENCE's response_start_contract and selected surface item, not a second authority. Begin visible prose immediately with its owner_response_semantics, response_start_kind, and response_start_scope. Character scope uses response_start_owner_id. World scope intentionally has no character owner. Leave the completed cause implicit as required by completed_source_rendering. pre_response_narration is forbidden: put no atmospheric setup, room or spatial restatement, temporal bridge, narrator recap, source-side staging, or explicit or anaphoric reference to the completed source before the selected surface response. Start from the NPC or world response. Transient staging may begin only after that response begins. The gate's ordered_surface_requirements is an exact compact recency checklist of every mandatory surface item in causal order.

Realize every surface_realization_item and every ordered_surface_requirement in causal order, using linked internal guidance as subtext, and stop at the termination constraint. Every surface item is independently mandatory. For each dialogue_intent, realize the exact communicative proposition in that item's owner_response_semantics before advancing; a generic reply, neighboring proposition, plausible explanation, or later question does not substitute for it. Adjacent atomic dialogue items may share one natural utterance, sentence, or paragraph, but every independently required meaning must remain materially present. Do not collapse separately keyed propositions in a way that omits or changes any one of them. Treat resulting_public_state as a postcondition to satisfy, not a prose checklist; treat remain_open as unresolved constraints and the termination constraint as a stopping rule. Never narrate those constraints as independent beats. Supporting history must never replace the current scene, reopen a resolved conversational floor, supply an unaccepted event, or establish unsupported backstory. If supporting history competes with the current turn, the current turn wins. Do not invent Ted dialogue or Ted private thoughts or feelings. Freely add compatible transient staging, gesture, dialogue, sensory detail, atmosphere, and characterization inside the authorized response after its surface start. Apply fact_scope through one deletion test for every invented detail: if removing it changes causality, identity, relationship development, accepted knowledge, presence or location authority, ownership or provenance, physical or private condition, recurring practice, or anything a later turn could reasonably rely on, it requires accepted authority and must be omitted. Otherwise transient presentation remains free. Do not establish an unauthorized consequential event. Do not discuss tools, files, policy, instructions, or analysis. Return complete visible prose only, with no tags, labels, analysis, or bookkeeping."""


ADULT_WRITER_SYSTEM_PROMPT = """You are CERA's DeepSeek Adult Scene Writer operating inside a dedicated read-only Pi Scene session. Call the tool named context directly exactly once before writing; do not call a tool named invoke. Its branch-scoped view is the complete authorized context. Apply the authority order stated in zz_CURRENT_TURN_AUTHORITY.json: the current route, USER_PROMPT.txt, and ADULT_HANDOFF.json control this response and outrank all accepted ordinary or adult history. Supporting history and craft material provide continuity and realization style only; they must never substitute an earlier scene for the current handoff. All depicted participants must be adults. Treat the handoff's consent_and_capacity field as already-established current authority rather than renegotiating or reopening it. The realization_scope marks USER_PROMPT.txt as already-supplied context: do not render, restate, quote, or paraphrase it. Begin the handoff's response after that supplied contribution. Fully realize causal_direction in its stated order, then stop at stopping_boundary; do not stop early, narrate planning, or expose handoff metadata. Start from the NPC or world response instead of replaying the user's contribution. Do not invent Ted dialogue or Ted private thoughts or feelings. Freely add compatible transient staging, gesture, dialogue, sensory detail, atmosphere, and characterization. Apply fact_scope strictly: freely invented detail is allowed only when it is scene-local, reversible, non-identifying, non-causal, and unsafe for a future turn to rely on as fact. Anything future-relevant requires accepted authority. Do not establish unsupported durable facts or an unauthorized consequential event. Do not discuss tools, files, policy, instructions, or analysis. Return complete visible prose only, with no tags, labels, analysis, or bookkeeping."""


ORDINARY_RECORDER_SYSTEM_PROMPT = """You are CERA's post-Accept ordinary Recorder. Call the tool named context directly exactly once; never call a tool named invoke and never call a second tool. The visible prose is already accepted and must never be regenerated, revised, or judged. Your entire assistant response must begin with { and end with }; return no analysis, explanation, label, or Markdown fence. Return one JSON object only with these keys: secondary_canon, resulting_public_state, relationship_changes, knowledge_changes, durable_changes, unresolved_threads. resulting_public_state must be one concise non-empty string; every other value must be a JSON array of concise strings and may be empty. Extract only what the accepted prose supports. Do not author hashes, sequence keys, item references, identity, branch, path, revision, or transaction fields; Python binds those custody and exact-sequence values."""


ADULT_RECORDER_SYSTEM_PROMPT = """You are CERA's post-Accept adult continuity Recorder. Call the tool named context directly exactly once; never call a tool named invoke and never call a second tool. The visible prose is already accepted and must never be regenerated, revised, or judged. Your entire assistant response must begin with { and end with }; return no analysis, explanation, label, or Markdown fence. Return one JSON object only with keys full_record and codex_projection. full_record must contain only decision_path and events. decision_path is a JSON array of concise strings. events is a JSON array of objects with exactly event_key, summary, motive, alternatives_considered, consent_or_boundary_transition, thoughts_and_feelings, durable_effects, knowledge_scope. alternatives_considered, thoughts_and_feelings, durable_effects, and knowledge_scope must each be a JSON array of concise strings even when there is only one item; never return a string, null, or object for any of them. The other event values are strings. codex_projection must contain only decision_path_summary, items, resulting_public_state, unresolved_threads. decision_path_summary and unresolved_threads are JSON arrays of concise strings. Every projection item has exactly event_key, non_explicit_summary, lasting_story_meaning and references an exact full-record event_key. Python deterministically copies the one resulting_public_state and unresolved_threads from the projection into the full record, so do not duplicate them. Do not author schemas, hashes, identity, branch, path, revision, or transaction fields; Python binds those custody values."""


MAX_TOOL_CALLS_PER_INVOCATION = 1


@dataclass(frozen=True, slots=True)
class PiSceneInvocationV1:
    route: SceneRoute
    purpose: str
    view: MaterializedWriterViewV1
    prompt: str
    candidate_id: str
    session_dir: Path
    accepted_parent_session: AcceptedPiSessionV1 | None = None
    force_rehydrate: bool = False

    def __post_init__(self) -> None:
        if self.purpose not in {"writer", "recorder"}:
            raise ContractValidationError("Pi Scene purpose is invalid")
        if not self.prompt.strip() or not self.candidate_id.strip():
            raise ContractValidationError("Pi Scene invocation is incomplete")
        if self.force_rehydrate and self.accepted_parent_session is None:
            # Rehydrating the first turn is harmless but is not a reset proof.
            raise ContractValidationError("Pi reset requires an accepted parent session")


@dataclass(frozen=True, slots=True)
class PiSceneInvocationResultV1:
    output_text: str
    session_id: str
    session_dir: Path
    writer_receipt: PiWriterReceiptV1
    raw_event_count: int


@dataclass(frozen=True, slots=True)
class _ProcessResult:
    returncode: int
    stdout: str
    stderr: str


ProcessRunner = Callable[
    [Sequence[str], Path, Mapping[str, str], int, Callable[[str], None]],
    _ProcessResult,
]


class PiSceneAdapter:
    """One invocation, zero automatic retries, exact operation accounting."""

    def __init__(
        self,
        *,
        pi_executable: Path,
        extension_path: Path,
        pi_version: str,
        operation_ledger: PiProviderOperationLedger,
        provider: str = "deepseek",
        model: str = "deepseek-v4-flash",
        timeout_seconds: int = 600,
        process_runner: ProcessRunner | None = None,
        readable_debug: ReadablePiSceneDebugLog | None = None,
    ) -> None:
        self.pi_executable = pi_executable.resolve()
        self.extension_path = extension_path.resolve()
        self.pi_version = pi_version
        self.operation_ledger = operation_ledger
        self.provider = provider
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._process_runner = process_runner or _run_process
        self.readable_debug = readable_debug
        if not self.pi_executable.is_file():
            raise ContractValidationError("Pi executable is unavailable")
        if not self.extension_path.is_file():
            raise ContractValidationError("CERA Pi extension is unavailable")
        if provider != "deepseek" or model != "deepseek-v4-flash":
            raise ContractValidationError("initial Pi Scene route requires DeepSeek V4 Flash")
        if not pi_version.strip() or type(timeout_seconds) is not int or timeout_seconds < 1:
            raise ContractValidationError("Pi Scene adapter configuration is invalid")

    def invoke(self, request: PiSceneInvocationV1) -> PiSceneInvocationResultV1:
        view = verify_writer_view(request.view.root)
        if view.manifest_sha256 != request.view.manifest_sha256:
            raise StateConflictError("Pi invocation Writer-view binding changed")
        if view.purpose != request.purpose:
            raise StateConflictError("Pi invocation purpose differs from its materialized view")
        request.session_dir.mkdir(parents=True, exist_ok=True)
        control_dir = _prepare_control_dir(request.session_dir)
        system_prompt = _system_prompt(request.route, request.purpose)
        command = self.command_for(request, system_prompt=system_prompt)
        environment = dict(os.environ)
        environment.update(
            {
                "CERA_PI_VIEW_ROOT": str(view.root),
                "PI_TELEMETRY": "0",
                "CERA_PI_MAX_TOOL_CALLS": str(MAX_TOOL_CALLS_PER_INVOCATION),
                "CERA_PI_PURPOSE": request.purpose,
            }
        )
        request_binding = canonical_sha256(
            {
                "route": request.route.value,
                "purpose": request.purpose,
                "view_manifest_sha256": view.manifest_sha256,
                "prompt": request.prompt,
                "system_prompt": system_prompt,
                "model": self.model,
                "thinking": "off",
                "tools": ["context"],
                "parent_session_id_sha256": (
                    None
                    if request.accepted_parent_session is None or request.force_rehydrate
                    else request.accepted_parent_session.session_id_sha256
                ),
            }
        )
        invocation_id = self.operation_ledger.begin(
            candidate_id=request.candidate_id,
            purpose=request.purpose,
            route=request.route.value,
            request_sha256=request_binding,
        )
        started = time.perf_counter()
        try:
            process = self._process_runner(
                command,
                control_dir,
                environment,
                self.timeout_seconds,
                lambda line: self.operation_ledger.observe_line(invocation_id, line),
            )
        except BaseException as exc:
            self.operation_ledger.finish(
                invocation_id,
                status="failed",
                failure_type=type(exc).__name__,
            )
            raise
        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        if process.returncode != 0:
            self.operation_ledger.finish(
                invocation_id,
                status="failed",
                failure_type=f"process_exit_{process.returncode}",
            )
            raise StateConflictError(
                "Pi Scene process failed without an accepted output "
                f"(exit={process.returncode}, stderr_sha256={text_sha256(process.stderr)})"
            )
        try:
            parsed = _parse_pi_json_stream(process.stdout)
            self.operation_ledger.assert_completed(
                invocation_id,
                parsed_operations=parsed.provider_operations,
            )
            output_text = (
                _parse_writer_output(parsed.output_text)
                if request.purpose == "writer"
                else parsed.output_text.strip()
            )
            if not output_text:
                raise StateConflictError("Pi Scene returned no final assistant text")
            parent_hash = (
                None
                if request.accepted_parent_session is None or request.force_rehydrate
                else request.accepted_parent_session.session_id_sha256
            )
            receipt = PiWriterReceiptV1(
                schema_version=PiWriterReceiptV1.SCHEMA_VERSION,
                route=request.route,
                provider=self.provider,
                model=self.model,
                pi_version=self.pi_version,
                session_id_sha256=text_sha256(parsed.session_id),
                parent_session_id_sha256=parent_hash,
                request_sha256=request_binding,
                output_sha256=text_sha256(output_text),
                provider_operations=parsed.provider_operations,
                tool_call_count=parsed.tool_call_count,
                failed_tool_call_count=parsed.failed_tool_call_count,
                input_tokens=parsed.input_tokens,
                cached_input_tokens=parsed.cached_input_tokens,
                output_tokens=parsed.output_tokens,
                reasoning_tokens=parsed.reasoning_tokens,
                duration_ms=duration_ms,
                finish_status=parsed.finish_status,
                rehydrated=(request.force_rehydrate or request.accepted_parent_session is None),
            )
        except BaseException as exc:
            self.operation_ledger.finish(
                invocation_id,
                status="failed",
                failure_type=type(exc).__name__,
            )
            raise
        self.operation_ledger.finish(
            invocation_id,
            status="completed",
            output_sha256=receipt.output_sha256,
        )
        if self.readable_debug is not None and self.readable_debug.enabled:
            self.readable_debug.write(
                stage=f"deepseek-{request.purpose}",
                identity=request.candidate_id,
                sections={
                    "Route": request.route.value,
                    "DeepSeek system prompt": system_prompt,
                    "Pi invocation prompt": request.prompt,
                    "Complete confined context-tool Writer view": self.readable_debug.writer_view(view.root),
                    "DeepSeek output": output_text,
                    "Provider receipt": receipt,
                },
            )
        return PiSceneInvocationResultV1(
            output_text=output_text,
            session_id=parsed.session_id,
            session_dir=request.session_dir.resolve(),
            writer_receipt=receipt,
            raw_event_count=parsed.event_count,
        )

    def command_for(
        self,
        request: PiSceneInvocationV1,
        *,
        system_prompt: str | None = None,
    ) -> tuple[str, ...]:
        system_prompt = system_prompt or _system_prompt(request.route, request.purpose)
        command = [
            str(self.pi_executable),
            "--provider",
            self.provider,
            "--model",
            self.model,
            "--thinking",
            "off",
            "--mode",
            "json",
            "--print",
            "--no-builtin-tools",
            "--no-extensions",
            "--extension",
            str(self.extension_path),
            "--tools",
            "context",
            "--no-skills",
            "--no-prompt-templates",
            "--no-context-files",
            "--approve",
            "--session-dir",
            str(request.session_dir.resolve()),
            "--system-prompt",
            system_prompt,
            "--name",
            f"cera-{request.route.value}-{request.purpose}-{text_sha256(request.candidate_id)[:12]}",
        ]
        if request.accepted_parent_session is not None and not request.force_rehydrate:
            command.extend(["--fork", request.accepted_parent_session.session_id])
        else:
            command.extend(
                [
                    "--session-id",
                    str(uuid5(NAMESPACE_URL, f"cera.pi_scene:{request.candidate_id}")),
                ]
            )
        command.append(request.prompt)
        return tuple(command)


@dataclass(frozen=True, slots=True)
class _ParsedPiStream:
    session_id: str
    output_text: str
    provider_operations: int
    tool_call_count: int
    failed_tool_call_count: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    finish_status: str
    event_count: int


def _parse_pi_json_stream(stdout: str) -> _ParsedPiStream:
    session_id: str | None = None
    final_text = ""
    provider_operations = 0
    tool_call_count = 0
    failed_tool_call_count = 0
    input_tokens = 0
    cached_input_tokens = 0
    output_tokens = 0
    reasoning_tokens = 0
    finish_status = "unknown"
    event_count = 0
    for raw_line in stdout.splitlines():
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise StateConflictError("Pi JSON event stream contained non-JSON output") from exc
        if not isinstance(event, dict):
            raise StateConflictError("Pi JSON event is not an object")
        event_count += 1
        if event.get("type") == "session":
            value = event.get("id")
            if not isinstance(value, str) or not value.strip():
                raise StateConflictError("Pi session header omitted its identity")
            session_id = value
        elif event.get("type") == "tool_execution_start":
            tool_call_count += 1
        elif event.get("type") == "tool_execution_end" and event.get("isError") is True:
            failed_tool_call_count += 1
        elif event.get("type") == "message_end":
            message = event.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            usage = message.get("usage")
            if isinstance(usage, dict):
                provider_operations += 1
                input_tokens += _usage_int(usage, "input", "input_tokens", "prompt_tokens")
                cached_input_tokens += _usage_int(
                    usage,
                    "cacheRead",
                    "cache_read",
                    "cached_input_tokens",
                    "prompt_cache_hit_tokens",
                )
                output_tokens += _usage_int(
                    usage,
                    "output",
                    "output_tokens",
                    "completion_tokens",
                )
                reasoning_tokens += _usage_int(
                    usage,
                    "reasoning",
                    "reasoning_tokens",
                    "reasoning_output_tokens",
                )
            finish_status = str(
                message.get("stopReason", message.get("stop_reason", message.get("finish_reason", "unknown")))
            )
            text = _assistant_text(message.get("content"))
            if text.strip():
                final_text = text
    if session_id is None:
        raise StateConflictError("Pi JSON event stream omitted the session header")
    if provider_operations < 1:
        raise StateConflictError("Pi JSON event stream did not prove a provider operation")
    if cached_input_tokens > input_tokens:
        cached_input_tokens = input_tokens
    return _ParsedPiStream(
        session_id=session_id,
        output_text=final_text,
        provider_operations=provider_operations,
        tool_call_count=tool_call_count,
        failed_tool_call_count=failed_tool_call_count,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        finish_status=finish_status,
        event_count=event_count,
    )


def _parse_writer_output(output_text: str) -> str:
    """Accept raw prose while retaining strict legacy-envelope compatibility."""

    opening = "<cera_scene>"
    closing = "</cera_scene>"
    opening_count = output_text.count(opening)
    closing_count = output_text.count(closing)
    if opening_count == 0 and closing_count == 0:
        prose = output_text.strip()
        if not prose:
            raise StateConflictError("Pi Writer returned empty prose")
        return prose
    if opening_count != 1 or closing_count != 1:
        raise StateConflictError("Pi Writer returned a partial or duplicate scene envelope")
    start = output_text.index(opening) + len(opening)
    end = output_text.find(closing, start)
    if end < start:
        raise StateConflictError("Pi Writer scene envelope order is invalid")
    prose = output_text[start:end].strip()
    if not prose:
        raise StateConflictError("Pi Writer scene envelope is empty")
    return prose


def _assistant_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _usage_int(usage: Mapping[str, object], *names: str) -> int:
    for name in names:
        value = usage.get(name)
        if type(value) is int and value >= 0:
            return value
    return 0


def _system_prompt(route: SceneRoute, purpose: str) -> str:
    if purpose == "writer":
        return (
            ORDINARY_WRITER_SYSTEM_PROMPT
            if route is SceneRoute.ORDINARY
            else ADULT_WRITER_SYSTEM_PROMPT
        )
    return (
        ORDINARY_RECORDER_SYSTEM_PROMPT
        if route is SceneRoute.ORDINARY
        else ADULT_RECORDER_SYSTEM_PROMPT
    )


def _run_process(
    command: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: int,
    on_stdout_line: Callable[[str], None],
) -> _ProcessResult:
    command_list = _native_command(command)
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    callback_errors: list[BaseException] = []
    try:
        process = subprocess.Popen(
            command_list,
            cwd=str(cwd),
            env=dict(environment),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    except OSError as exc:
        raise StateConflictError("Pi Scene process could not start") from exc

    def read_stdout() -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                stdout_lines.append(line)
                on_stdout_line(line)
        except BaseException as exc:
            callback_errors.append(exc)
            process.kill()

    def read_stderr() -> None:
        assert process.stderr is not None
        stderr_lines.extend(process.stderr.readlines())

    stdout_worker = Thread(target=read_stdout, daemon=True)
    stderr_worker = Thread(target=read_stderr, daemon=True)
    stdout_worker.start()
    stderr_worker.start()
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        stdout_worker.join(timeout=5)
        stderr_worker.join(timeout=5)
        raise StateConflictError("Pi Scene process exceeded its timeout") from exc
    stdout_worker.join(timeout=5)
    stderr_worker.join(timeout=5)
    if callback_errors:
        raise StateConflictError("Pi Scene stream accounting failed") from callback_errors[0]
    return _ProcessResult(returncode, "".join(stdout_lines), "".join(stderr_lines))


def _native_command(command: Sequence[str]) -> list[str]:
    values = list(command)
    executable = Path(values[0])
    if executable.suffix.lower() != ".cmd":
        return values
    node = shutil.which("node")
    cli = (
        executable.parent
        / "node_modules"
        / "@earendil-works"
        / "pi-coding-agent"
        / "dist"
        / "cli.js"
    )
    if node is None or not cli.is_file():
        raise StateConflictError("native Pi Node entrypoint is unavailable")
    return [node, str(cli), *values[1:]]


def _prepare_control_dir(session_dir: Path) -> Path:
    root = (session_dir / "_cera_control").resolve()
    settings_dir = root / ".pi"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings = settings_dir / "settings.json"
    payload = {
        "compaction": {"enabled": False},
        "retry": {
            "enabled": False,
            "maxRetries": 0,
            "provider": {"maxRetries": 0},
        },
    }
    expected = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if settings.exists() and settings.read_text(encoding="utf-8") != expected:
        raise StateConflictError("Pi control settings changed")
    if not settings.exists():
        temporary = settings.with_suffix(".json.tmp")
        temporary.write_text(expected, encoding="utf-8")
        os.replace(temporary, settings)
    return root
