"""Request-bound read-only MCP view of one continuous world branch."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hmac
from importlib.metadata import version
import json
from pathlib import Path
import secrets
import socket
import threading
import time
from typing import Any, ClassVar

from cera.errors import ContractValidationError, StateConflictError
from cera.providers import CodexMcpRuntimeBinding
from cera.serialization import canonical_sha256, domain_sha256, text_sha256

from .sessions import ContinuousSessionRole, WorldPathAccessPolicyV1


WORLD_MCP_SDK_VERSION = "1.29.0"
WORLD_MCP_SERVER_NAME = "cera_continuous_world"
WORLD_MCP_TOKEN_ENV = "CERA_REQUEST_EVIDENCE_TOKEN"
WORLD_MCP_TOOLS = (
    "cera_world_list",
    "cera_world_search",
    "cera_world_read",
)
WORLD_MCP_MAXIMUM_CALLS = 32


@dataclass(frozen=True, slots=True)
class ContinuousWorldToolCallV1:
    call_index: int
    tool_name: str
    request_sha256: str
    returned_bytes: int
    duration_ns: int
    success: bool


class ContinuousWorldToolDispatcher:
    """Mechanical world lookup with role and current-candidate isolation."""

    def __init__(
        self,
        branch_root: Path,
        role: ContinuousSessionRole,
        *,
        current_turn_id: str | None = None,
        maximum_calls: int = WORLD_MCP_MAXIMUM_CALLS,
    ) -> None:
        self.branch_root = branch_root.resolve()
        self.role = role
        self.current_turn_id = current_turn_id
        if not self.branch_root.is_dir():
            raise ContractValidationError("continuous world branch root is unavailable")
        if not 1 <= maximum_calls <= WORLD_MCP_MAXIMUM_CALLS:
            raise ContractValidationError("continuous world MCP call ceiling is invalid")
        self.maximum_calls = maximum_calls
        self.policy = WorldPathAccessPolicyV1(str(self.branch_root))
        self.calls: list[ContinuousWorldToolCallV1] = []
        self.local_debug_calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    @property
    def binding_sha256(self) -> str:
        return domain_sha256(
            "cera.continuous_world_mcp.v1",
            {
                "branch_root_sha256": text_sha256(str(self.branch_root).casefold()),
                "role": self.role.value,
                "current_turn_id": self.current_turn_id,
                "tools": WORLD_MCP_TOOLS,
                "maximum_calls": self.maximum_calls,
            },
        )

    def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name not in WORLD_MCP_TOOLS or not isinstance(arguments, dict):
            raise ContractValidationError("continuous world MCP request is invalid")
        started = time.perf_counter_ns()
        request_hash = canonical_sha256({"tool": tool_name, "arguments": arguments})
        success = False
        returned_bytes = 0
        with self._lock:
            if len(self.calls) >= self.maximum_calls:
                raise StateConflictError(
                    "continuous world MCP reached its hard runaway ceiling"
                )
            try:
                if tool_name == "cera_world_list":
                    result = self._list(**arguments)
                elif tool_name == "cera_world_search":
                    result = self._search(**arguments)
                else:
                    result = self._read(**arguments)
                returned_bytes = len(
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode(
                        "utf-8"
                    )
                )
                success = True
                self.local_debug_calls.append(
                    {
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "result_paths": [
                            value.get("path")
                            for value in result.get("records", [])
                            if isinstance(value, dict) and isinstance(value.get("path"), str)
                        ]
                        if isinstance(result, dict)
                        else [],
                        "exact_path": (
                            result.get("path")
                            if isinstance(result, dict) and isinstance(result.get("path"), str)
                            else None
                        ),
                        "returned_bytes": returned_bytes,
                    }
                )
                return result
            finally:
                self.calls.append(
                    ContinuousWorldToolCallV1(
                        call_index=len(self.calls) + 1,
                        tool_name=tool_name,
                        request_sha256=request_hash,
                        returned_bytes=returned_bytes,
                        duration_ns=time.perf_counter_ns() - started,
                        success=success,
                    )
                )

    def _allowed_roots(self) -> tuple[Path, ...]:
        roots = [self.branch_root / "ACTIVE"]
        if self.role is ContinuousSessionRole.VALIDATOR and self.current_turn_id:
            roots.append(
                self.branch_root
                / "CANDIDATES"
                / self.current_turn_id
                / "ACTIVE_VIEW"
            )
        return tuple(value for value in roots if value.is_dir())

    def _authorized_path(self, relative_path: str) -> Path:
        if (
            not isinstance(relative_path, str)
            or not relative_path.strip()
            or Path(relative_path).is_absolute()
            or ".." in Path(relative_path).parts
            or ":" in relative_path
        ):
            raise PermissionError("continuous world path is invalid")
        target = (self.branch_root / relative_path).resolve()
        self.policy.authorize(
            self.role,
            str(target),
            current_turn_id=self.current_turn_id,
        )
        if not target.is_file() or target.is_symlink():
            raise FileNotFoundError("continuous world record is unavailable")
        return target

    def _list(self, prefix: str = "", limit: int = 100) -> dict[str, Any]:
        if not isinstance(prefix, str) or type(limit) is not int or not 1 <= limit <= 100:
            raise ContractValidationError("continuous world list arguments are invalid")
        rows = []
        for root in self._allowed_roots():
            for path in sorted(value for value in root.rglob("*") if value.is_file()):
                relative = path.relative_to(self.branch_root).as_posix()
                if prefix and not relative.casefold().startswith(prefix.casefold()):
                    continue
                rows.append(self._descriptor(path))
                if len(rows) == limit:
                    return {"records": rows, "truncated": True}
        return {"records": rows, "truncated": False}

    def _search(
        self,
        terms: list[str],
        record_types: list[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        if (
            not isinstance(terms, list)
            or not terms
            or not all(isinstance(value, str) and value.strip() for value in terms)
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise ContractValidationError("continuous world search arguments are invalid")
        types = {value.casefold() for value in (record_types or [])}
        needles = tuple(value.casefold() for value in terms)
        rows = []
        for root in self._allowed_roots():
            for path in sorted(value for value in root.rglob("*") if value.is_file()):
                relative = path.relative_to(self.branch_root).as_posix()
                record_type = relative.split("/", 2)[1].casefold() if "/" in relative else ""
                if types and record_type not in types:
                    continue
                text = path.read_text(encoding="utf-8")
                haystack = (relative + "\n" + text).casefold()
                if not all(value in haystack for value in needles):
                    continue
                row = self._descriptor(path)
                row["matched_terms"] = list(terms)
                rows.append(row)
                if len(rows) == limit:
                    return {"records": rows, "truncated": True}
        return {"records": rows, "truncated": False}

    def _read(self, path: str) -> dict[str, Any]:
        target = self._authorized_path(path)
        raw = target.read_bytes()
        if len(raw) > 1_048_576:
            raise StateConflictError("continuous world record exceeds exact-read ceiling")
        text = raw.decode("utf-8")
        return {
            **self._descriptor(target),
            "content": json.loads(text) if target.suffix.casefold() == ".json" else text,
        }

    def _descriptor(self, path: Path) -> dict[str, Any]:
        relative = path.relative_to(self.branch_root).as_posix()
        text = path.read_text(encoding="utf-8")
        revision = None
        if path.suffix.casefold() == ".json":
            payload = json.loads(text)
            if isinstance(payload, dict):
                revision = payload.get("_cera_revision")
        return {
            "path": relative,
            "revision": revision,
            "byte_count": len(text.encode("utf-8")),
            "content_sha256": text_sha256(text),
        }


class ContinuousWorldMcpBridge:
    """Ephemeral authenticated loopback MCP server for one role and turn."""

    def __init__(self, dispatcher: ContinuousWorldToolDispatcher) -> None:
        self.dispatcher = dispatcher
        self._token = secrets.token_urlsafe(32)
        self._url: str | None = None
        self._server = None
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._errors: list[BaseException] = []

    @property
    def runtime_binding(self) -> CodexMcpRuntimeBinding:
        if self._url is None:
            raise StateConflictError("continuous world MCP bridge is not running")
        return CodexMcpRuntimeBinding(
            server_name=WORLD_MCP_SERVER_NAME,
            url=self._url,
            bearer_token_environment_variable=WORLD_MCP_TOKEN_ENV,
            bearer_token=self._token,
            enabled_tools=WORLD_MCP_TOOLS,
            binding_sha256=self.dispatcher.binding_sha256,
            startup_timeout_seconds=10,
            tool_timeout_seconds=30,
            minimum_tool_calls=0,
            maximum_tool_calls=self.dispatcher.maximum_calls,
        )

    def start(self) -> "ContinuousWorldMcpBridge":
        if self._thread is not None:
            raise StateConflictError("continuous world MCP bridge cannot start twice")
        if version("mcp") != WORLD_MCP_SDK_VERSION:
            raise RuntimeError("continuous world MCP SDK version changed")
        import uvicorn
        from mcp.server.auth.provider import AccessToken
        from mcp.server.auth.settings import AuthSettings
        from mcp.server.fastmcp import FastMCP

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(256)
        self._socket = listener
        port = int(listener.getsockname()[1])
        self._url = f"http://127.0.0.1:{port}/mcp"
        expected_token = self._token

        class TokenVerifier:
            async def verify_token(self, candidate: str):
                if not hmac.compare_digest(candidate, expected_token):
                    return None
                return AccessToken(
                    token=candidate,
                    client_id="cera-continuous-runtime",
                    scopes=["cera.world.read"],
                )

        mcp = FastMCP(
            WORLD_MCP_SERVER_NAME,
            instructions=(
                "Read-only current-branch CERA world lookup. Search/list locate records; "
                "read returns exact content. Reaching 32 calls is a terminal failure."
            ),
            host="127.0.0.1",
            port=port,
            json_response=True,
            stateless_http=True,
            max_request_body_size=65_536,
            token_verifier=TokenVerifier(),
            auth=AuthSettings(
                issuer_url=f"http://127.0.0.1:{port}/",
                resource_server_url=self._url,
                required_scopes=["cera.world.read"],
            ),
        )

        @mcp.tool(name="cera_world_list", structured_output=True)
        def world_list(prefix: str = "", limit: int = 100) -> dict[str, Any]:
            return self.dispatcher.invoke(
                "cera_world_list", {"prefix": prefix, "limit": limit}
            )

        @mcp.tool(name="cera_world_search", structured_output=True)
        def world_search(
            terms: list[str],
            record_types: list[str] | None = None,
            limit: int = 20,
        ) -> dict[str, Any]:
            return self.dispatcher.invoke(
                "cera_world_search",
                {"terms": terms, "record_types": record_types, "limit": limit},
            )

        @mcp.tool(name="cera_world_read", structured_output=True)
        def world_read(path: str) -> dict[str, Any]:
            return self.dispatcher.invoke("cera_world_read", {"path": path})

        server = uvicorn.Server(
            uvicorn.Config(
                mcp.streamable_http_app(),
                host="127.0.0.1",
                port=port,
                log_level="error",
                lifespan="on",
            )
        )
        self._server = server

        def serve() -> None:
            try:
                asyncio.run(server.serve(sockets=[listener]))
            except BaseException as exc:
                self._errors.append(exc)

        self._thread = threading.Thread(
            target=serve, name="cera-continuous-world-mcp", daemon=True
        )
        self._thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if server.started:
                return self
            if self._errors or not self._thread.is_alive():
                break
            time.sleep(0.01)
        self.stop(suppress_errors=True)
        raise RuntimeError("continuous world MCP bridge failed to start")

    def stop(self, *, suppress_errors: bool = False) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        alive = self._thread is not None and self._thread.is_alive()
        if self._socket is not None:
            self._socket.close()
        self._url = None
        if not suppress_errors and (alive or self._errors):
            raise RuntimeError("continuous world MCP bridge did not stop cleanly")

    def finalize(self, provider_result: Any) -> dict[str, Any]:
        expected = tuple(value.tool_name for value in self.dispatcher.calls)
        if provider_result.tool_names != expected:
            raise StateConflictError("continuous world MCP tool sequence changed")
        if any(value != WORLD_MCP_SERVER_NAME for value in provider_result.tool_server_names):
            raise StateConflictError("continuous world MCP server identity changed")
        return {
            "binding_sha256": self.dispatcher.binding_sha256,
            "tool_call_count": len(self.dispatcher.calls),
            "returned_bytes": sum(value.returned_bytes for value in self.dispatcher.calls),
            "ceiling_reached": len(self.dispatcher.calls) >= self.dispatcher.maximum_calls,
            "calls": [
                {
                    "call_index": value.call_index,
                    "tool_name": value.tool_name,
                    "request_sha256": value.request_sha256,
                    "returned_bytes": value.returned_bytes,
                    "duration_ns": value.duration_ns,
                    "success": value.success,
                }
                for value in self.dispatcher.calls
            ],
            "local_debug_calls": self.dispatcher.local_debug_calls,
        }

    def __enter__(self) -> "ContinuousWorldMcpBridge":
        return self.start()

    def __exit__(self, exc_type, exc, _traceback) -> None:
        self.stop(suppress_errors=exc_type is not None)
