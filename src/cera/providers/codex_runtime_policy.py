"""Closed Codex runtime/tool-surface policy for provider-backed CERA turns."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import cast

from cera.errors import ContractValidationError

CODEX_DISABLED_MULTI_AGENT_NAMESPACE = "cera_disabled_multi_agent_v2"
CODEX_REMOTE_CONTROL_DISABLED_ENVIRONMENT_VARIABLE = (
    "CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED"
)
CODEX_REQUEST_BOUND_MCP_SERVER_NAME = "cera_request_evidence"
CODEX_CONTINUOUS_WORLD_MCP_SERVER_NAME = "cera_continuous_world"
CODEX_APPROVED_REQUEST_BOUND_MCP_SERVER_NAMES = frozenset(
    {
        CODEX_REQUEST_BOUND_MCP_SERVER_NAME,
        CODEX_CONTINUOUS_WORLD_MCP_SERVER_NAME,
    }
)
CODEX_CHATGPT_BASE_URL = "https://chatgpt.com/backend-api/"
CODEX_MODEL_CATALOG_SHA256 = "a9b13b0c6935adaf465206151609ca3322ef6bec836ebab40c686a9ba76393bd"
CODEX_MODEL_INSTRUCTIONS_SHA256 = "910d589b12f34e69cb6d4bf399bc38f74ce9f68f182e3a34d54b91611f26ff3f"
CODEX_AUTO_COMPACT_TOKEN_LIMIT = 9_223_372_036_854_775_807
CODEX_RUNTIME_TOOL_SURFACE_POLICY_ID = (
    "cera.codex_runtime_tool_surface.v3.catalog_"
    + CODEX_MODEL_CATALOG_SHA256
    + ".instructions_"
    + CODEX_MODEL_INSTRUCTIONS_SHA256
)

_CODEX_CLI_SHA256 = "51398051c2332b6afe08dc3b9dbb4056085c197f35ca57a307ee303d450cada5"
_CODEX_EXPECTED_INHERITED_MCP_SURFACE = (
    ("node_repl", False),
    ("openaiDeveloperDocs", False),
)
_CODEX_MCP_SERVER_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_CODEX_MCP_SERVER_LIMIT = 32
_CODEX_MCP_STATUS_PAGE_LIMIT = 4
_CODEX_CONFIG_LAYER_LIMIT = 64
_CODEX_CONFIG_FILE_BYTE_LIMIT = 1024 * 1024
_CODEX_MODEL_CATALOG_FILE = "gpt-5.6-cera-direct-v1.models.json"
_CODEX_MODEL_INSTRUCTIONS_FILE = "cera-model-instructions-fallback.txt"
QUALIFIED_CODEX_MODELS = frozenset({"gpt-5.6-sol", "gpt-5.6-luna"})
_EXPLICIT_EXTERNAL_CONTEXT_MARKER = re.compile(
    r"\$[A-Za-z_]|(?:skill|plugin|app|mcp)://",
    re.IGNORECASE,
)
_FORBIDDEN_CODEX_LAUNCH_ENVIRONMENT_NAMES = frozenset(
    {
        "ALL_PROXY",
        "CODEX_ACCESS_TOKEN",
        "CODEX_APP_SERVER_MANAGED_CONFIG_PATH",
        "CODEX_API_KEY",
        "CODEX_AUTHAPI_BASE_URL",
        "CODEX_CA_CERTIFICATE",
        "CODEX_HOME",
        "CODEX_REFRESH_TOKEN_URL_OVERRIDE",
        "CODEX_REVOKE_TOKEN_URL_OVERRIDE",
        "CODEX_SQLITE_HOME",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "OPENAI_API_KEY",
        "OPENAI_ORGANIZATION",
        "OPENAI_PROJECT",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "all_proxy",
        "https_proxy",
        "http_proxy",
    }
)
_BLANKED_CODEX_CHILD_ENVIRONMENT_NAMES = (
    "CERA_PI_SCENE_TOKEN",
    "DEEPSEEK_API_KEY",
)

_ALLOWED_COMPLETED_RESULT_ITEM_TYPES = frozenset(
    {"reasoning", "userMessage", "agentMessage", "mcpToolCall"}
)
_FORBIDDEN_COMPLETED_RESULT_ITEM_TYPES = frozenset(
    {
        "collabAgentToolCall",
        "commandExecution",
        "contextCompaction",
        "dynamicToolCall",
        "fileChange",
        "imageGeneration",
        "imageView",
        "plan",
        "webSearch",
    }
)
_UNKNOWN_COMPLETED_RESULT_ITEM_TYPE = "unknown"
COMPLETED_RESULT_ITEM_TYPE_EVIDENCE = _FORBIDDEN_COMPLETED_RESULT_ITEM_TYPES | {
    _UNKNOWN_COMPLETED_RESULT_ITEM_TYPE
}

_APP_SERVER_STARTUP_CONFIG_PATHS = (
    ("web_search",),
    ("analytics", "enabled"),
    ("chatgpt_base_url",),
    ("check_for_update_on_startup",),
    ("model_auto_compact_token_limit",),
    ("model_auto_compact_token_limit_scope",),
    ("developer_instructions",),
    ("feedback", "enabled"),
    ("include_apps_instructions",),
    ("include_collaboration_mode_instructions",),
    ("include_environment_context",),
    ("include_permissions_instructions",),
    ("notify",),
    ("model_instructions_file",),
    ("model_provider",),
    ("openai_base_url",),
    ("otel", "exporter"),
    ("otel", "metrics_exporter"),
    ("otel", "trace_exporter"),
    ("personality",),
    ("project_doc_fallback_filenames",),
    ("project_doc_max_bytes",),
    ("features", "apps"),
    ("features", "apply_patch_freeform"),
    ("features", "auth_elicitation"),
    ("features", "browser_use"),
    ("features", "browser_use_external"),
    ("features", "browser_use_full_cdp_access"),
    ("features", "code_mode", "enabled"),
    ("features", "code_mode", "excluded_tool_namespaces"),
    ("features", "code_mode_host"),
    ("features", "collab"),
    ("features", "computer_use"),
    ("features", "connectors"),
    ("features", "current_time_reminder"),
    ("features", "goals"),
    ("features", "guardian_approval"),
    ("features", "hooks"),
    ("features", "image_generation"),
    ("features", "in_app_browser"),
    ("features", "memories"),
    ("features", "multi_agent"),
    ("features", "multi_agent_v2", "enabled"),
    ("features", "multi_agent_v2", "max_concurrent_threads_per_session"),
    ("features", "multi_agent_v2", "multi_agent_mode_hint_text"),
    ("features", "multi_agent_v2", "non_code_mode_only"),
    ("features", "multi_agent_v2", "root_agent_usage_hint_text"),
    ("features", "multi_agent_v2", "subagent_usage_hint_text"),
    ("features", "multi_agent_v2", "tool_namespace"),
    ("features", "network_proxy"),
    ("features", "plugins"),
    ("features", "remote_plugin"),
    ("features", "remote_compaction_v2"),
    ("features", "respect_system_proxy"),
    ("features", "request_permissions_tool"),
    ("features", "search_tool"),
    ("features", "shell_snapshot"),
    ("features", "shell_tool"),
    ("features", "skill_mcp_dependency_install"),
    ("features", "skill_search"),
    ("features", "standalone_web_search"),
    ("features", "tool_call_mcp_elicitation"),
    ("features", "tool_search"),
    ("features", "tool_suggest"),
    ("features", "token_budget"),
    ("features", "unified_exec"),
    ("features", "web_search"),
    ("features", "workspace_dependencies"),
    ("memories", "dedicated_tools"),
    ("memories", "generate_memories"),
    ("memories", "use_memories"),
    ("orchestrator", "mcp", "enabled"),
    ("orchestrator", "skills", "enabled"),
    ("skills", "bundled", "enabled"),
    ("skills", "include_instructions"),
    ("tools", "experimental_request_user_input", "enabled"),
)


def unsupported_completed_result_item_types(
    result_items: Iterable[object],
) -> tuple[str, ...]:
    """Return only bounded type metadata for disallowed completed items."""

    unsupported: set[str] = set()
    for wrapped in result_items:
        item = wrapped.root if hasattr(wrapped, "root") else wrapped
        item_type = getattr(item, "type", None)
        if item_type in _ALLOWED_COMPLETED_RESULT_ITEM_TYPES:
            continue
        unsupported.add(
            item_type
            if item_type in _FORBIDDEN_COMPLETED_RESULT_ITEM_TYPES
            else _UNKNOWN_COMPLETED_RESULT_ITEM_TYPE
        )
    return tuple(sorted(unsupported))


def require_qualified_codex_model(model: object) -> str:
    """Reject any model selector outside the hash-bound static catalog."""

    if not isinstance(model, str) or model not in QUALIFIED_CODEX_MODELS:
        raise ContractValidationError("Codex model is outside the qualified static catalog")
    return model


def validate_codex_prompt_markers(prompt: object) -> str:
    """Reject explicit host-skill/app selection without retaining marker text."""

    if not isinstance(prompt, str) or not prompt.strip():
        raise ContractValidationError("Codex invocation requires a text prompt")
    if (
        _EXPLICIT_EXTERNAL_CONTEXT_MARKER.search(prompt) is not None
        or "skill.md" in prompt.casefold()
    ):
        raise ContractValidationError("Codex prompt contains a forbidden external-context marker")
    return prompt


def runtime_config_and_environment(
    mcp_binding: dict[str, object] | None,
) -> tuple[dict[str, object], dict[str, str]]:
    """Build one request-scoped no-files runtime configuration."""

    config: dict[str, object] = {
        "web_search": "disabled",
        "analytics": {"enabled": False},
        "chatgpt_base_url": CODEX_CHATGPT_BASE_URL,
        "check_for_update_on_startup": False,
        "model_auto_compact_token_limit": CODEX_AUTO_COMPACT_TOKEN_LIMIT,
        "model_auto_compact_token_limit_scope": "total",
        "developer_instructions": "",
        "feedback": {"enabled": False},
        "include_apps_instructions": False,
        "include_collaboration_mode_instructions": False,
        "include_environment_context": False,
        "include_permissions_instructions": False,
        "notify": [],
        "model_instructions_file": str(codex_model_instructions_path()),
        "model_provider": "openai",
        "model_providers": {},
        "openai_base_url": "",
        "otel": {
            "exporter": "none",
            "metrics_exporter": "none",
            "trace_exporter": "none",
        },
        "personality": "none",
        "project_doc_fallback_filenames": [],
        "project_doc_max_bytes": 0,
        "features": {
            "apps": False,
            "apply_patch_freeform": False,
            "auth_elicitation": False,
            "browser_use": False,
            "browser_use_external": False,
            "browser_use_full_cdp_access": False,
            "code_mode": {
                "enabled": False,
                "excluded_tool_namespaces": [CODEX_DISABLED_MULTI_AGENT_NAMESPACE],
            },
            "code_mode_host": False,
            "collab": False,
            "computer_use": False,
            "connectors": False,
            "current_time_reminder": False,
            "goals": False,
            "guardian_approval": False,
            "hooks": False,
            "image_generation": False,
            "in_app_browser": False,
            "memories": False,
            "multi_agent": False,
            "multi_agent_v2": {
                "enabled": False,
                "max_concurrent_threads_per_session": 1,
                "multi_agent_mode_hint_text": "",
                "non_code_mode_only": False,
                "root_agent_usage_hint_text": "",
                "subagent_usage_hint_text": "",
                "tool_namespace": CODEX_DISABLED_MULTI_AGENT_NAMESPACE,
            },
            "network_proxy": False,
            "plugins": False,
            "remote_plugin": False,
            "remote_compaction_v2": False,
            "respect_system_proxy": False,
            "request_permissions_tool": False,
            "search_tool": False,
            "shell_snapshot": False,
            "shell_tool": False,
            "skill_mcp_dependency_install": False,
            "skill_search": False,
            "standalone_web_search": False,
            "tool_call_mcp_elicitation": False,
            "tool_search": False,
            "tool_suggest": False,
            "token_budget": False,
            "unified_exec": False,
            "web_search": False,
            "workspace_dependencies": False,
        },
        "memories": {
            "dedicated_tools": False,
            "generate_memories": False,
            "use_memories": False,
        },
        "orchestrator": {
            "mcp": {"enabled": False},
            "skills": {"enabled": False},
        },
        "skills": {
            "bundled": {"enabled": False},
            "include_instructions": False,
        },
        "tools": {"experimental_request_user_input": {"enabled": False}},
        "default_permissions": "cera-no-files",
        "permissions": {
            "cera-no-files": {
                "description": "CERA provider qualification without filesystem or network",
                "filesystem": {
                    ":root": "deny",
                    ":minimal": "read",
                    ":tmpdir": "deny",
                    ":slash_tmp": "deny",
                },
                "network": {"enabled": False},
            }
        },
    }
    codex_environment: dict[str, str] = {}
    if mcp_binding is None:
        return config, codex_environment
    required_keys = {
        "server_name",
        "url",
        "bearer_token_environment_variable",
        "bearer_token",
        "enabled_tools",
        "binding_sha256",
        "required",
        "startup_timeout_seconds",
        "tool_timeout_seconds",
        "minimum_tool_calls",
        "maximum_tool_calls",
    }
    if set(mcp_binding) != required_keys or mcp_binding["required"] is not True:
        raise ValueError("request-bound MCP binding is malformed")
    token_environment_variable = mcp_binding["bearer_token_environment_variable"]
    if token_environment_variable != "CERA_REQUEST_EVIDENCE_TOKEN":
        raise ValueError("request-bound MCP token source is not approved")
    bearer_token = mcp_binding["bearer_token"]
    if not isinstance(bearer_token, str) or not bearer_token:
        raise ValueError("request-bound MCP token is invalid")
    codex_environment[token_environment_variable] = bearer_token
    server_name = mcp_binding["server_name"]
    if (
        not isinstance(server_name, str)
        or server_name not in CODEX_APPROVED_REQUEST_BOUND_MCP_SERVER_NAMES
    ):
        raise ValueError("request-bound MCP server name is not approved")
    config[f"mcp_servers.{server_name}"] = {
        "url": mcp_binding["url"],
        "bearer_token_env_var": token_environment_variable,
        "enabled": True,
        "required": True,
        "enabled_tools": mcp_binding["enabled_tools"],
        "default_tools_approval_mode": "approve",
        "startup_timeout_sec": mcp_binding["startup_timeout_seconds"],
        "tool_timeout_sec": mcp_binding["tool_timeout_seconds"],
        "supports_parallel_tool_calls": False,
    }
    return config, codex_environment


def _codex_config_override_literal(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    raise TypeError("Codex startup override has an unsupported value type")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _bundled_codex_executable_path() -> Path:
    from codex_cli_bin import bundled_codex_path  # type: ignore[import-untyped]

    codex_path = Path(bundled_codex_path()).resolve()
    if not codex_path.is_file() or _file_sha256(codex_path) != _CODEX_CLI_SHA256:
        raise RuntimeError("pinned Codex CLI binary changed")
    return codex_path


def codex_model_catalog_path() -> Path:
    """Return the repository-bound catalog after exact byte validation."""

    path = Path(__file__).resolve().parent / "runtime_contracts" / _CODEX_MODEL_CATALOG_FILE
    if not path.is_file() or _file_sha256(path) != CODEX_MODEL_CATALOG_SHA256:
        raise RuntimeError("qualified Codex model catalog changed")
    return path


def codex_model_instructions_path() -> Path:
    """Return the packaged safe fallback instructions after byte validation."""

    path = Path(__file__).resolve().parent / "runtime_contracts" / _CODEX_MODEL_INSTRUCTIONS_FILE
    if not path.is_file() or _file_sha256(path) != CODEX_MODEL_INSTRUCTIONS_SHA256:
        raise RuntimeError("qualified Codex fallback instructions changed")
    return path


def codex_app_server_config_overrides(cwd: str | Path) -> tuple[str, ...]:
    """Return the closed startup config for one app-server process."""

    validate_codex_prelaunch_config_sources(cwd)
    config, environment = runtime_config_and_environment(None)
    if environment or any(key.startswith("mcp_servers") for key in config):
        raise RuntimeError("Codex startup config unexpectedly retained authority")
    _bundled_codex_executable_path()
    overrides = [
        "mcp_servers={}",
        "model_providers={}",
        "model_catalog_json=" + _codex_config_override_literal(str(codex_model_catalog_path())),
    ]
    for path in _APP_SERVER_STARTUP_CONFIG_PATHS:
        value: object = config
        for segment in path:
            if not isinstance(value, dict) or segment not in value:
                raise RuntimeError("Codex startup config path is unavailable")
            value = value[segment]
        overrides.append(f"{'.'.join(path)}={_codex_config_override_literal(value)}")
    overrides.extend(
        (
            "mcp_servers.node_repl.enabled=false",
            "mcp_servers.openaiDeveloperDocs.enabled=false",
        )
    )
    if len(overrides) != len(set(overrides)):
        raise RuntimeError("Codex startup config contains duplicate overrides")
    return tuple(overrides)


def codex_app_server_environment(
    base_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the closed launch overlay for one Codex app-server process."""

    environment = dict(base_environment or {})
    if set(environment) - {"CERA_REQUEST_EVIDENCE_TOKEN"} or any(
        not isinstance(value, str) or not value for value in environment.values()
    ):
        raise ValueError("Codex app-server environment overlay is invalid")
    if any(name in os.environ for name in _FORBIDDEN_CODEX_LAUNCH_ENVIRONMENT_NAMES):
        raise RuntimeError("Codex app-server inherited a forbidden authority variable")
    for name in _BLANKED_CODEX_CHILD_ENVIRONMENT_NAMES:
        environment[name] = ""
    environment.setdefault("CERA_REQUEST_EVIDENCE_TOKEN", "")
    environment[CODEX_REMOTE_CONTROL_DISABLED_ENVIRONMENT_VARIABLE] = "1"
    environment["RUST_LOG"] = "warn"
    return environment


def _resolved_codex_config_cwd(cwd: str | Path) -> Path:
    value = Path(cwd).resolve()
    if not value.is_absolute() or not value.is_dir():
        raise ValueError("Codex config-read cwd is unavailable")
    return value


def _codex_config_source_paths(cwd: Path) -> tuple[Path, ...]:
    project_root = cwd
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".git").exists():
            project_root = candidate
            break
    project_directories: list[Path] = []
    for candidate in (cwd, *cwd.parents):
        project_directories.append(candidate)
        if candidate == project_root:
            break
    codex_home = Path.home() / ".codex"
    paths = [
        codex_home / "config.toml",
        codex_home / "managed_config.toml",
    ]
    if os.name == "nt":
        paths.append(Path("C:/ProgramData/OpenAI/Codex/config.toml"))
    paths.extend(directory / ".codex" / "config.toml" for directory in project_directories)
    return tuple(dict.fromkeys(path.resolve() for path in paths))


def _config_mapping(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if not callable(model_dump):
        return None
    dumped = model_dump(by_alias=True, exclude_none=False, mode="python")
    return dumped if isinstance(dumped, dict) else None


def _configuration_has_forbidden_prelaunch_authority(
    config: dict[str, object],
) -> bool:
    stack: list[object] = [config]
    observed = 0
    forbidden_keys = {
        "config_lock_toml",
        "config_lockfile",
        "experimental_compact_prompt_file",
    }
    while stack:
        observed += 1
        if observed > 4096:
            return True
        value = stack.pop()
        mapping = _config_mapping(value)
        if mapping is not None:
            if any(mapping.get(key) is not None for key in forbidden_keys):
                return True
            stack.extend(mapping.values())
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
    return False


def validate_codex_prelaunch_config_sources(cwd: str | Path) -> None:
    """Reject file-backed config locks and compact-prompt paths content-blindly."""

    resolved_cwd = _resolved_codex_config_cwd(cwd)
    for path in _codex_config_source_paths(resolved_cwd):
        if not path.exists():
            continue
        try:
            if not path.is_file() or path.stat().st_size > _CODEX_CONFIG_FILE_BYTE_LIMIT:
                raise RuntimeError
            parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, RuntimeError, UnicodeError, tomllib.TOMLDecodeError):
            raise RuntimeError("Codex config source could not be validated") from None
        if _configuration_has_forbidden_prelaunch_authority(parsed):
            raise RuntimeError("Codex config source retained forbidden authority")


def _effective_mcp_surface_from_config_response(
    response: object,
) -> tuple[tuple[str, bool], ...]:
    config = getattr(response, "config", None)
    model_extra = getattr(config, "model_extra", None)
    if not isinstance(model_extra, dict):
        raise RuntimeError("Codex config/read omitted extra configuration")
    catalog = model_extra.get("model_catalog_json")
    if not isinstance(catalog, str) or Path(catalog).resolve() != codex_model_catalog_path():
        raise RuntimeError("Codex config/read model catalog changed")
    instructions = model_extra.get("model_instructions_file")
    if (
        not isinstance(instructions, str)
        or Path(instructions).resolve() != codex_model_instructions_path()
    ):
        raise RuntimeError("Codex config/read fallback instructions changed")
    features = model_extra.get("features")
    compact_scope = getattr(config, "model_auto_compact_token_limit_scope", None)
    compact_scope_value = getattr(compact_scope, "value", compact_scope)
    if (
        getattr(config, "model_provider", None) != "openai"
        or getattr(config, "model_auto_compact_token_limit", None) != CODEX_AUTO_COMPACT_TOKEN_LIMIT
        or compact_scope_value != "total"
        or model_extra.get("openai_base_url") != ""
        or model_extra.get("chatgpt_base_url") != CODEX_CHATGPT_BASE_URL
        or model_extra.get("experimental_thread_config_endpoint") is not None
        or model_extra.get("model_providers") != {}
        or model_extra.get("experimental_compact_prompt_file") is not None
        or model_extra.get("config_lock_toml") is not None
        or not isinstance(features, dict)
        or features.get("network_proxy") is not False
        or features.get("respect_system_proxy") is not False
        or features.get("remote_compaction_v2") is not False
        or _configuration_has_forbidden_prelaunch_authority(model_extra)
    ):
        raise RuntimeError("Codex config/read provider authority changed")
    servers = model_extra.get("mcp_servers")
    if not isinstance(servers, dict) or not 0 < len(servers) <= _CODEX_MCP_SERVER_LIMIT:
        raise RuntimeError("Codex config/read returned invalid MCP metadata")
    surface: list[tuple[str, bool]] = []
    for name, definition in servers.items():
        if (
            not isinstance(name, str)
            or _CODEX_MCP_SERVER_NAME.fullmatch(name) is None
            or not isinstance(definition, dict)
            or type(definition.get("enabled")) is not bool
        ):
            raise RuntimeError("Codex config/read returned invalid MCP metadata")
        if name in CODEX_APPROVED_REQUEST_BOUND_MCP_SERVER_NAMES:
            raise RuntimeError("host config collides with request-bound CERA MCP")
        surface.append((name, definition["enabled"]))
    surface.sort()
    if len(surface) != len({name for name, _enabled in surface}):
        raise RuntimeError("Codex config/read returned duplicate MCP names")
    return tuple(surface)


def validate_codex_app_server_configuration(
    codex: object,
    *,
    cwd: str | Path,
) -> None:
    """Require the exact effective catalog and disabled inherited MCP set."""

    from openai_codex.generated.v2_all import ConfigReadResponse

    client = getattr(codex, "_client", None)
    request = getattr(client, "request", None)
    if not callable(request):
        raise RuntimeError("Codex config/read client is unavailable")
    response = request(
        "config/read",
        {
            "cwd": str(_resolved_codex_config_cwd(cwd)),
            "includeLayers": True,
        },
        response_model=ConfigReadResponse,
    )
    layers = getattr(response, "layers", None)
    if not isinstance(layers, list) or len(layers) > _CODEX_CONFIG_LAYER_LIMIT:
        raise RuntimeError("Codex config/read layers are invalid")
    for layer in layers:
        if getattr(layer, "disabled_reason", None) is not None:
            continue
        layer_config = _config_mapping(getattr(layer, "config", None))
        if layer_config is None:
            raise RuntimeError("Codex config/read layers are invalid")
        if _configuration_has_forbidden_prelaunch_authority(layer_config):
            raise RuntimeError("Codex config/read layer retained forbidden authority")
    surface = _effective_mcp_surface_from_config_response(response)
    if surface != _CODEX_EXPECTED_INHERITED_MCP_SURFACE:
        raise RuntimeError("Codex inherited MCP surface is not closed")


def validate_codex_mcp_server_status(
    codex: object,
    *,
    thread_id: str | None = None,
    request_server_name: str | None = None,
    request_tool_names: Iterable[str] = (),
) -> None:
    """Require direct CERA-only MCP tool/resource exposure metadata."""

    from openai_codex.generated.v2_all import ListMcpServerStatusResponse

    inherited_names = {name for name, _enabled in _CODEX_EXPECTED_INHERITED_MCP_SURFACE}
    requested_tools = frozenset(request_tool_names)
    if request_server_name is None:
        if requested_tools:
            raise ValueError("request MCP tools have no server")
    elif (
        not isinstance(request_server_name, str)
        or request_server_name not in CODEX_APPROVED_REQUEST_BOUND_MCP_SERVER_NAMES
        or not requested_tools
        or any(_CODEX_MCP_SERVER_NAME.fullmatch(name) is None for name in requested_tools)
    ):
        raise ValueError("request MCP status expectation is invalid")
    if thread_id is not None and (not isinstance(thread_id, str) or not thread_id):
        raise ValueError("Codex MCP status thread identity is invalid")
    client = getattr(codex, "_client", None)
    request = getattr(client, "request", None)
    if not callable(request):
        raise RuntimeError("Codex MCP status client is unavailable")

    cursor: str | None = None
    seen_cursors: set[str] = set()
    observed: dict[str, frozenset[str]] = {}
    for _page in range(_CODEX_MCP_STATUS_PAGE_LIMIT):
        params: dict[str, object] = {
            "cursor": cursor,
            "detail": "full",
            "limit": _CODEX_MCP_SERVER_LIMIT,
        }
        if thread_id is not None:
            params["threadId"] = thread_id
        response = request(
            "mcpServerStatus/list",
            params,
            response_model=ListMcpServerStatusResponse,
        )
        data = getattr(response, "data", None)
        if not isinstance(data, list):
            raise RuntimeError("Codex MCP status response is invalid")
        for status in data:
            name = getattr(status, "name", None)
            tools = getattr(status, "tools", None)
            resources = getattr(status, "resources", None)
            templates = getattr(status, "resource_templates", None)
            if (
                not isinstance(name, str)
                or _CODEX_MCP_SERVER_NAME.fullmatch(name) is None
                or name in observed
                or not isinstance(tools, dict)
                or len(tools) > _CODEX_MCP_SERVER_LIMIT
                or any(
                    not isinstance(tool_name, str)
                    or _CODEX_MCP_SERVER_NAME.fullmatch(tool_name) is None
                    for tool_name in tools
                )
                or not isinstance(resources, list)
                or resources
                or not isinstance(templates, list)
                or templates
            ):
                raise RuntimeError("Codex MCP status metadata is invalid")
            observed[name] = frozenset(tools)
            if len(observed) > _CODEX_MCP_SERVER_LIMIT:
                raise RuntimeError("Codex MCP status exceeded its server bound")
        next_cursor = getattr(response, "next_cursor", None)
        if next_cursor is None:
            break
        if (
            not isinstance(next_cursor, str)
            or not next_cursor
            or len(next_cursor) > 512
            or next_cursor in seen_cursors
        ):
            raise RuntimeError("Codex MCP status pagination is invalid")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    else:
        raise RuntimeError("Codex MCP status exceeded its page bound")

    allowed_names = inherited_names | (
        set() if request_server_name is None else {request_server_name}
    )
    if set(observed) - allowed_names:
        raise RuntimeError("Codex MCP tool surface is not closed")
    if any(observed.get(name, frozenset()) for name in inherited_names):
        raise RuntimeError("Codex MCP tool surface is not closed")
    if request_server_name is None:
        if CODEX_APPROVED_REQUEST_BOUND_MCP_SERVER_NAMES.intersection(observed):
            raise RuntimeError("Codex MCP tool surface is not closed")
    elif observed.get(request_server_name) != requested_tools:
        raise RuntimeError("Codex MCP tool surface is not closed")


def start_codex_thread_without_environments(
    codex: object,
    *,
    model: str,
    cwd: str,
    ephemeral: bool,
    base_instructions: str,
    config: dict[str, object],
    service_name: str,
    service_tier: str | None = None,
) -> object:
    """Start a thread with an explicit empty experimental environment set."""

    from openai_codex.api import Thread
    from openai_codex.client import CodexClient

    require_qualified_codex_model(model)
    if not isinstance(base_instructions, str) or not base_instructions.strip():
        raise ContractValidationError("Codex thread/start requires base instructions")
    client = getattr(codex, "_client", None)
    start = getattr(client, "thread_start", None)
    if not callable(start):
        raise RuntimeError("Codex low-level thread/start client is unavailable")
    params: dict[str, object] = {
        "approvalPolicy": "never",
        "baseInstructions": base_instructions,
        "config": config,
        "cwd": cwd,
        "environments": [],
        "ephemeral": ephemeral,
        "model": model,
        "serviceName": service_name,
    }
    if service_tier is not None:
        params["serviceTier"] = service_tier
    started = start(params)
    thread_id = getattr(getattr(started, "thread", None), "id", None)
    if not isinstance(thread_id, str) or not thread_id:
        raise RuntimeError("Codex thread/start omitted its thread identity")
    return Thread(cast(CodexClient, client), thread_id)
