from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from cera.continuous.world_mcp import WORLD_MCP_SERVER_NAME, WORLD_MCP_TOOLS
from cera.errors import ContractValidationError
from cera.providers.codex_runtime_policy import (
    CODEX_APPROVED_REQUEST_BOUND_MCP_SERVER_NAMES,
    CODEX_AUTO_COMPACT_TOKEN_LIMIT,
    CODEX_CHATGPT_BASE_URL,
    CODEX_CONTINUOUS_WORLD_MCP_SERVER_NAME,
    CODEX_DISABLED_MULTI_AGENT_NAMESPACE,
    CODEX_MODEL_CATALOG_SHA256,
    CODEX_MODEL_INSTRUCTIONS_SHA256,
    CODEX_REMOTE_CONTROL_DISABLED_ENVIRONMENT_VARIABLE,
    CODEX_REQUEST_BOUND_MCP_SERVER_NAME,
    CODEX_RUNTIME_TOOL_SURFACE_POLICY_ID,
    QUALIFIED_CODEX_MODELS,
    codex_app_server_config_overrides,
    codex_app_server_environment,
    codex_model_catalog_path,
    codex_model_instructions_path,
    runtime_config_and_environment,
    start_codex_thread_without_environments,
    unsupported_completed_result_item_types,
    validate_codex_app_server_configuration,
    validate_codex_mcp_server_status,
    validate_codex_prelaunch_config_sources,
    validate_codex_prompt_markers,
)
from cera.providers.codex_sdk_compat import (
    CODEX_SDK_COMPATIBILITY_ID,
    CODEX_SDK_COMPATIBILITY_SOURCE_SHA256,
    EXPECTED_ROUTE_NOTIFICATION_SHA256,
)
from cera.providers.codex_session_worker import _config as persistent_runtime_config
from cera.reasoner_session.codex_stored import _stored_runtime_config

ROOT = Path(__file__).resolve().parents[1]
HASH_A = "a" * 64
_SOURCE_ENTRY_SHA256 = {
    "gpt-5.6-sol": "5f0c83e43591ca07e4b616edfb79429efe4fe4e70f3e8a77d60160cf3166387e",
    "gpt-5.6-luna": "77b47b7663b11ed16b51866c7f117c2fcd26fe4773355c20794374b117def0f8",
}
_TRANSFORMED_ENTRY_SHA256 = {
    "gpt-5.6-sol": "2064c5faf65cf3a6b838c70956eb8f6025c7f065a235278deb23e1836c0635b0",
    "gpt-5.6-luna": "912c340df59ab0deb5c93c0daa0279d88fda906340145f4b8f28c64eeca49b30",
}
_SOURCE_MULTI_AGENT_VERSION = {
    "gpt-5.6-sol": "v2",
    "gpt-5.6-luna": "v1",
}


def _setuptools_build_backend_available() -> bool:
    try:
        return importlib.util.find_spec("setuptools.build_meta") is not None
    except ModuleNotFoundError:
        return False


def _canonical_entry_sha256(entry: dict[str, object]) -> str:
    payload = json.dumps(
        entry,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _binding(
    token: str,
    port: int,
    *,
    server_name: str = CODEX_REQUEST_BOUND_MCP_SERVER_NAME,
    enabled_tools: tuple[str, ...] = ("cera_get_turn_snapshot",),
) -> dict[str, object]:
    return {
        "server_name": server_name,
        "url": f"http://127.0.0.1:{port}/mcp",
        "bearer_token_environment_variable": "CERA_REQUEST_EVIDENCE_TOKEN",
        "bearer_token": token,
        "enabled_tools": list(enabled_tools),
        "binding_sha256": HASH_A,
        "required": True,
        "startup_timeout_seconds": 10,
        "tool_timeout_seconds": 10,
        "minimum_tool_calls": 0,
        "maximum_tool_calls": 12,
    }


class _ConfigAndStatusClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.config_extra: dict[str, object] = {
            "chatgpt_base_url": CODEX_CHATGPT_BASE_URL,
            "experimental_compact_prompt_file": None,
            "experimental_thread_config_endpoint": None,
            "features": {
                "network_proxy": False,
                "remote_compaction_v2": False,
                "respect_system_proxy": False,
            },
            "model_catalog_json": str(codex_model_catalog_path()),
            "model_instructions_file": str(codex_model_instructions_path()),
            "model_providers": {},
            "mcp_servers": {
                "node_repl": {"enabled": False},
                "openaiDeveloperDocs": {"enabled": False},
            },
            "openai_base_url": "",
        }
        self.layers: list[object] = []
        self.statuses = [
            self.status("node_repl"),
            self.status("openaiDeveloperDocs"),
        ]

    @staticmethod
    def status(
        name: str,
        tools: tuple[str, ...] = (),
        *,
        resources: list[object] | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            name=name,
            tools={tool: object() for tool in tools},
            resources=[] if resources is None else resources,
            resource_templates=[],
            server_info=None,
        )

    def request(self, method, params, *, response_model):
        del response_model
        self.calls.append((method, params))
        if method == "config/read":
            return SimpleNamespace(
                config=SimpleNamespace(
                    model_extra=self.config_extra,
                    model_auto_compact_token_limit=(CODEX_AUTO_COMPACT_TOKEN_LIMIT),
                    model_auto_compact_token_limit_scope=SimpleNamespace(value="total"),
                    model_provider="openai",
                ),
                layers=self.layers,
            )
        if method == "mcpServerStatus/list":
            return SimpleNamespace(data=self.statuses, next_cursor=None)
        raise AssertionError(method)


class CodexRuntimePolicyTests(unittest.TestCase):
    def test_catalog_is_hash_bound_and_preserves_raw_entries_except_selectors(
        self,
    ) -> None:
        path = codex_model_catalog_path()
        payload = path.read_bytes()
        self.assertEqual(len(payload), 70_672)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), CODEX_MODEL_CATALOG_SHA256)
        catalog = json.loads(payload)
        self.assertEqual(set(catalog), {"models"})
        self.assertEqual(
            tuple(model["slug"] for model in catalog["models"]),
            ("gpt-5.6-sol", "gpt-5.6-luna"),
        )

        for model in catalog["models"]:
            slug = model["slug"]
            self.assertIn(slug, QUALIFIED_CODEX_MODELS)
            self.assertEqual(model["tool_mode"], "direct")
            self.assertEqual(model["multi_agent_version"], "disabled")
            self.assertIs(model["supports_search_tool"], False)
            self.assertEqual(
                _canonical_entry_sha256(model),
                _TRANSFORMED_ENTRY_SHA256[slug],
            )
            restored = copy.deepcopy(model)
            restored["tool_mode"] = "code_mode_only"
            restored["multi_agent_version"] = _SOURCE_MULTI_AGENT_VERSION[slug]
            restored["supports_search_tool"] = True
            self.assertEqual(
                _canonical_entry_sha256(restored),
                _SOURCE_ENTRY_SHA256[slug],
            )

        with (
            patch(
                "cera.providers.codex_runtime_policy._file_sha256",
                return_value="0" * 64,
            ),
            self.assertRaisesRegex(RuntimeError, "model catalog changed"),
        ):
            codex_model_catalog_path()

    def test_catalog_is_declared_as_package_data(self) -> None:
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn(
            "providers/runtime_contracts/*.json",
            config["tool"]["setuptools"]["package-data"]["cera"],
        )
        self.assertIn(
            "providers/runtime_contracts/*.txt",
            config["tool"]["setuptools"]["package-data"]["cera"],
        )

    @unittest.skipUnless(
        _setuptools_build_backend_available(),
        "declared setuptools build backend is unavailable",
    )
    def test_built_wheel_contains_hash_bound_runtime_contracts(self) -> None:
        with TemporaryDirectory(prefix="cera-runtime-policy-wheel-") as directory:
            environment = dict(os.environ)
            environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
            environment["PIP_NO_INDEX"] = "1"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    str(ROOT),
                    "--no-build-isolation",
                    "--no-cache-dir",
                    "--no-deps",
                    "--wheel-dir",
                    directory,
                ],
                cwd=directory,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=120,
            )
            self.assertEqual(completed.returncode, 0)
            wheels = tuple(Path(directory).glob("*.whl"))
            self.assertEqual(len(wheels), 1)
            with ZipFile(wheels[0]) as archive:
                catalog = archive.read(
                    "cera/providers/runtime_contracts/gpt-5.6-cera-direct-v1.models.json"
                )
                instructions = archive.read(
                    "cera/providers/runtime_contracts/cera-model-instructions-fallback.txt"
                )
        self.assertEqual(
            hashlib.sha256(catalog).hexdigest(),
            CODEX_MODEL_CATALOG_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(instructions).hexdigest(),
            CODEX_MODEL_INSTRUCTIONS_SHA256,
        )

    def test_fallback_instructions_are_hash_bound_and_nonempty(self) -> None:
        path = codex_model_instructions_path()
        payload = path.read_bytes()
        self.assertTrue(payload.strip())
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            CODEX_MODEL_INSTRUCTIONS_SHA256,
        )

        with (
            patch(
                "cera.providers.codex_runtime_policy._file_sha256",
                return_value="0" * 64,
            ),
            self.assertRaisesRegex(RuntimeError, "fallback instructions changed"),
        ):
            codex_model_instructions_path()

    def test_request_binding_is_dotted_and_rebuilt_without_secret_retention(
        self,
    ) -> None:
        first_config, first_env = runtime_config_and_environment(_binding("first-secret", 41001))
        second_config, second_env = runtime_config_and_environment(_binding("second-secret", 41002))
        self.assertEqual(first_env, {"CERA_REQUEST_EVIDENCE_TOKEN": "first-secret"})
        self.assertEqual(second_env, {"CERA_REQUEST_EVIDENCE_TOKEN": "second-secret"})
        self.assertNotIn("first-secret", repr(second_config))
        self.assertNotIn("second-secret", repr(second_config))
        mcp_key = f"mcp_servers.{CODEX_REQUEST_BOUND_MCP_SERVER_NAME}"
        self.assertNotIn("mcp_servers", first_config)
        self.assertNotIn("mcp_servers", second_config)
        self.assertNotEqual(first_config[mcp_key], second_config[mcp_key])

        world_config, world_env = runtime_config_and_environment(
            _binding(
                "world-secret",
                41003,
                server_name=WORLD_MCP_SERVER_NAME,
                enabled_tools=WORLD_MCP_TOOLS,
            )
        )
        world_mcp_key = f"mcp_servers.{WORLD_MCP_SERVER_NAME}"
        self.assertEqual(world_env, {"CERA_REQUEST_EVIDENCE_TOKEN": "world-secret"})
        self.assertIn(world_mcp_key, world_config)
        self.assertNotIn(mcp_key, world_config)
        world_definition = world_config[world_mcp_key]
        self.assertIsInstance(world_definition, dict)
        assert isinstance(world_definition, dict)
        self.assertEqual(world_definition["enabled_tools"], list(WORLD_MCP_TOOLS))

        with self.assertRaisesRegex(ValueError, "server name is not approved"):
            runtime_config_and_environment(
                _binding("unknown-secret", 41004, server_name="unknown_request_server")
            )
        malformed_binding = _binding("malformed-secret", 41005)
        malformed_binding["server_name"] = []
        with self.assertRaisesRegex(ValueError, "server name is not approved"):
            runtime_config_and_environment(malformed_binding)

    def test_request_server_allowlist_is_policy_and_sdk_compatibility_bound(self) -> None:
        self.assertEqual(
            CODEX_APPROVED_REQUEST_BOUND_MCP_SERVER_NAMES,
            frozenset(
                {
                    CODEX_REQUEST_BOUND_MCP_SERVER_NAME,
                    CODEX_CONTINUOUS_WORLD_MCP_SERVER_NAME,
                }
            ),
        )
        self.assertEqual(CODEX_CONTINUOUS_WORLD_MCP_SERVER_NAME, WORLD_MCP_SERVER_NAME)
        self.assertIn("cera.codex_runtime_tool_surface.v3", CODEX_RUNTIME_TOOL_SURFACE_POLICY_ID)
        self.assertIn(CODEX_MODEL_CATALOG_SHA256, CODEX_RUNTIME_TOOL_SURFACE_POLICY_ID)
        self.assertIn(CODEX_MODEL_INSTRUCTIONS_SHA256, CODEX_RUNTIME_TOOL_SURFACE_POLICY_ID)
        self.assertIn(CODEX_RUNTIME_TOOL_SURFACE_POLICY_ID, CODEX_SDK_COMPATIBILITY_ID)
        expected_source_sha256 = hashlib.sha256(
            (
                EXPECTED_ROUTE_NOTIFICATION_SHA256 + "+" + CODEX_RUNTIME_TOOL_SURFACE_POLICY_ID
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(CODEX_SDK_COMPATIBILITY_SOURCE_SHA256, expected_source_sha256)
        prior_policy_id = CODEX_RUNTIME_TOOL_SURFACE_POLICY_ID.replace(
            "cera.codex_runtime_tool_surface.v3",
            "cera.codex_runtime_tool_surface.v2",
            1,
        )
        prior_source_sha256 = hashlib.sha256(
            (EXPECTED_ROUTE_NOTIFICATION_SHA256 + "+" + prior_policy_id).encode("utf-8")
        ).hexdigest()
        self.assertNotEqual(CODEX_SDK_COMPATIBILITY_SOURCE_SHA256, prior_source_sha256)

    def test_all_runtime_paths_share_the_hardened_tool_surface(self) -> None:
        baseline, environment = runtime_config_and_environment(None)
        persistent = persistent_runtime_config()
        stored = _stored_runtime_config()
        self.assertEqual(environment, {})
        self.assertEqual(persistent, baseline)

        normalized_stored = copy.deepcopy(stored)
        normalized_stored["permissions"]["cera-no-files"]["description"] = baseline["permissions"][
            "cera-no-files"
        ]["description"]
        self.assertEqual(normalized_stored, baseline)
        for config in (baseline, persistent, stored):
            self.assertFalse(any(key.startswith("mcp_servers") for key in config))
            self.assertEqual(config["project_doc_max_bytes"], 0)
            self.assertEqual(config["project_doc_fallback_filenames"], [])
            self.assertFalse(config["include_environment_context"])
            self.assertFalse(config["include_permissions_instructions"])
            self.assertEqual(config["developer_instructions"], "")
            self.assertEqual(config["chatgpt_base_url"], CODEX_CHATGPT_BASE_URL)
            self.assertEqual(config["model_provider"], "openai")
            self.assertEqual(config["model_providers"], {})
            self.assertEqual(config["openai_base_url"], "")
            self.assertEqual(
                config["model_auto_compact_token_limit"],
                CODEX_AUTO_COMPACT_TOKEN_LIMIT,
            )
            self.assertEqual(
                config["model_auto_compact_token_limit_scope"],
                "total",
            )
            self.assertEqual(
                config["model_instructions_file"],
                str(codex_model_instructions_path()),
            )
            self.assertEqual(config["notify"], [])
            self.assertEqual(config["personality"], "none")
            features = config["features"]
            for name in (
                "apps",
                "auth_elicitation",
                "browser_use",
                "code_mode_host",
                "computer_use",
                "image_generation",
                "memories",
                "multi_agent",
                "network_proxy",
                "plugins",
                "remote_compaction_v2",
                "respect_system_proxy",
                "shell_snapshot",
                "shell_tool",
                "tool_call_mcp_elicitation",
                "workspace_dependencies",
            ):
                self.assertIs(features[name], False)
            self.assertEqual(
                features["multi_agent_v2"],
                {
                    "enabled": False,
                    "max_concurrent_threads_per_session": 1,
                    "multi_agent_mode_hint_text": "",
                    "non_code_mode_only": False,
                    "root_agent_usage_hint_text": "",
                    "subagent_usage_hint_text": "",
                    "tool_namespace": CODEX_DISABLED_MULTI_AGENT_NAMESPACE,
                },
            )

    def test_startup_overrides_pin_catalog_and_disable_all_inherited_mcp(self) -> None:
        overrides = codex_app_server_config_overrides(ROOT)
        self.assertEqual(overrides[0], "mcp_servers={}")
        self.assertEqual(len(overrides), len(set(overrides)))
        self.assertIn(
            "model_catalog_json=" + json.dumps(str(codex_model_catalog_path())),
            overrides,
        )
        self.assertIn(
            "model_instructions_file=" + json.dumps(str(codex_model_instructions_path())),
            overrides,
        )
        self.assertIn('model_provider="openai"', overrides)
        self.assertIn('openai_base_url=""', overrides)
        self.assertIn(
            "chatgpt_base_url=" + json.dumps(CODEX_CHATGPT_BASE_URL),
            overrides,
        )
        self.assertIn("model_providers={}", overrides)
        self.assertIn("features.respect_system_proxy=false", overrides)
        self.assertIn("features.network_proxy=false", overrides)
        self.assertIn("features.remote_compaction_v2=false", overrides)
        self.assertIn(
            f"model_auto_compact_token_limit={CODEX_AUTO_COMPACT_TOKEN_LIMIT}",
            overrides,
        )
        self.assertIn('model_auto_compact_token_limit_scope="total"', overrides)
        self.assertIn("mcp_servers.node_repl.enabled=false", overrides)
        self.assertIn("mcp_servers.openaiDeveloperDocs.enabled=false", overrides)
        self.assertIn("features.code_mode_host=false", overrides)
        self.assertFalse(any("node_repl.env" in value for value in overrides))

        for path in (
            ROOT / "src/cera/providers/codex_worker.py",
            ROOT / "src/cera/providers/codex_stored_turn_worker.py",
            ROOT / "src/cera/providers/codex_session_worker.py",
            ROOT / "src/cera/reasoner_session/runtime.py",
            ROOT / "scripts/run_pi_scene_lean_server.py",
        ):
            source = path.read_text(encoding="utf-8")
            self.assertIn("codex_app_server_config_overrides(", source, path.as_posix())
            self.assertIn("config_overrides=app_server_overrides", source, path.as_posix())
            self.assertIn("env=codex_app_server_environment(", source, path.as_posix())
            self.assertNotIn("thread/compact", source, path.as_posix())
            self.assertNotIn("compact/start", source, path.as_posix())

    def test_launch_environment_is_presence_checked_and_parent_secrets_are_blanked(
        self,
    ) -> None:
        with patch.dict(os.environ, {}, clear=True):
            request_environment = {"CERA_REQUEST_EVIDENCE_TOKEN": "request-secret"}
            launch = codex_app_server_environment(request_environment)
        self.assertEqual(
            launch,
            {
                "CERA_REQUEST_EVIDENCE_TOKEN": "request-secret",
                "CERA_PI_SCENE_TOKEN": "",
                "DEEPSEEK_API_KEY": "",
                CODEX_REMOTE_CONTROL_DISABLED_ENVIRONMENT_VARIABLE: "1",
                "RUST_LOG": "warn",
            },
        )
        self.assertEqual(
            request_environment,
            {"CERA_REQUEST_EVIDENCE_TOKEN": "request-secret"},
        )
        with patch.dict(
            os.environ,
            {"CERA_REQUEST_EVIDENCE_TOKEN": "stale-parent-token"},
            clear=True,
        ):
            no_binding_launch = codex_app_server_environment()
        self.assertEqual(no_binding_launch["CERA_REQUEST_EVIDENCE_TOKEN"], "")
        for name in (
            "CODEX_ACCESS_TOKEN",
            "CODEX_APP_SERVER_MANAGED_CONFIG_PATH",
            "CODEX_API_KEY",
            "CODEX_AUTHAPI_BASE_URL",
            "CODEX_CA_CERTIFICATE",
            "CODEX_HOME",
            "CODEX_REFRESH_TOKEN_URL_OVERRIDE",
            "CODEX_REVOKE_TOKEN_URL_OVERRIDE",
            "CODEX_SQLITE_HOME",
            "CURL_CA_BUNDLE",
            "OPENAI_API_KEY",
            "OPENAI_ORGANIZATION",
            "OPENAI_PROJECT",
            "REQUESTS_CA_BUNDLE",
            "SSL_CERT_DIR",
            "SSL_CERT_FILE",
            "HTTP_PROXY",
            "http_proxy",
        ):
            with patch.dict(os.environ, {name: "present"}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "forbidden authority"):
                    codex_app_server_environment()

    def test_effective_config_and_full_status_are_closed(self) -> None:
        client = _ConfigAndStatusClient()
        codex = SimpleNamespace(_client=client)
        validate_codex_app_server_configuration(codex, cwd=ROOT)
        validate_codex_mcp_server_status(codex)

        client.statuses.append(
            client.status(
                CODEX_REQUEST_BOUND_MCP_SERVER_NAME,
                ("cera_get_turn_snapshot",),
            )
        )
        validate_codex_mcp_server_status(
            codex,
            thread_id="thread-safe-id",
            request_server_name=CODEX_REQUEST_BOUND_MCP_SERVER_NAME,
            request_tool_names=("cera_get_turn_snapshot",),
        )
        self.assertEqual(client.calls[-1][1]["detail"], "full")
        self.assertEqual(client.calls[-1][1]["threadId"], "thread-safe-id")
        self.assertTrue(client.calls[0][1]["includeLayers"])

        client.statuses[-1] = client.status(
            WORLD_MCP_SERVER_NAME,
            WORLD_MCP_TOOLS,
        )
        validate_codex_mcp_server_status(
            codex,
            thread_id="thread-world-id",
            request_server_name=WORLD_MCP_SERVER_NAME,
            request_tool_names=WORLD_MCP_TOOLS,
        )
        with self.assertRaisesRegex(RuntimeError, "not closed"):
            validate_codex_mcp_server_status(codex)
        with self.assertRaisesRegex(ValueError, "expectation is invalid"):
            validate_codex_mcp_server_status(
                codex,
                request_server_name="unknown_request_server",
                request_tool_names=("unknown_tool",),
            )
        with self.assertRaisesRegex(ValueError, "expectation is invalid"):
            validate_codex_mcp_server_status(
                codex,
                request_server_name=[],  # type: ignore[arg-type]
                request_tool_names=("unknown_tool",),
            )

        client.statuses[0] = client.status("node_repl", ("js",))
        with self.assertRaisesRegex(RuntimeError, "not closed"):
            validate_codex_mcp_server_status(codex)
        client.statuses[0] = client.status("node_repl", resources=[object()])
        with self.assertRaisesRegex(RuntimeError, "metadata"):
            validate_codex_mcp_server_status(codex)

    def test_effective_config_rejects_each_reserved_request_server_collision(self) -> None:
        for server_name in CODEX_APPROVED_REQUEST_BOUND_MCP_SERVER_NAMES:
            with self.subTest(server_name=server_name):
                client = _ConfigAndStatusClient()
                servers = client.config_extra["mcp_servers"]
                assert isinstance(servers, dict)
                servers[server_name] = {"enabled": False}
                with self.assertRaisesRegex(RuntimeError, "collides"):
                    validate_codex_app_server_configuration(
                        SimpleNamespace(_client=client),
                        cwd=ROOT,
                    )

    def test_effective_provider_authority_tampering_fails_closed(self) -> None:
        mutations = (
            ("openai_base_url", "https://invalid.example/v1"),
            ("chatgpt_base_url", "https://invalid.example/backend-api"),
            ("experimental_thread_config_endpoint", "https://invalid.example"),
            ("model_providers", {"custom": {}}),
            ("features", {"respect_system_proxy": True}),
            ("experimental_compact_prompt_file", str(ROOT / "unexpected.txt")),
            ("model_instructions_file", str(ROOT / "unexpected.txt")),
        )
        for key, value in mutations:
            with self.subTest(key=key):
                client = _ConfigAndStatusClient()
                client.config_extra[key] = value
                with self.assertRaises(RuntimeError):
                    validate_codex_app_server_configuration(
                        SimpleNamespace(_client=client),
                        cwd=ROOT,
                    )
        client = _ConfigAndStatusClient()
        codex = SimpleNamespace(
            _client=client,
        )
        original_request = client.request

        def wrong_provider(method, params, *, response_model):
            response = original_request(
                method,
                params,
                response_model=response_model,
            )
            if method == "config/read":
                response.config.model_provider = "custom"
            return response

        client.request = wrong_provider
        with self.assertRaisesRegex(RuntimeError, "provider authority changed"):
            validate_codex_app_server_configuration(codex, cwd=ROOT)

        for value in (True, CODEX_AUTO_COMPACT_TOKEN_LIMIT - 1):
            with self.subTest(compact_limit=value):
                client = _ConfigAndStatusClient()
                original_request = client.request

                def wrong_limit(
                    method,
                    params,
                    *,
                    response_model,
                    _original_request=original_request,
                    _value=value,
                ):
                    response = _original_request(
                        method,
                        params,
                        response_model=response_model,
                    )
                    if method == "config/read":
                        response.config.model_auto_compact_token_limit = _value
                    return response

                client.request = wrong_limit
                with self.assertRaisesRegex(RuntimeError, "provider authority"):
                    validate_codex_app_server_configuration(
                        SimpleNamespace(_client=client),
                        cwd=ROOT,
                    )

        client = _ConfigAndStatusClient()
        original_request = client.request

        def wrong_scope(method, params, *, response_model):
            response = original_request(
                method,
                params,
                response_model=response_model,
            )
            if method == "config/read":
                response.config.model_auto_compact_token_limit_scope = SimpleNamespace(
                    value="body_after_prefix"
                )
            return response

        client.request = wrong_scope
        with self.assertRaisesRegex(RuntimeError, "provider authority"):
            validate_codex_app_server_configuration(
                SimpleNamespace(_client=client),
                cwd=ROOT,
            )

    def test_prelaunch_config_source_guard_is_content_blind(self) -> None:
        with TemporaryDirectory(prefix="cera-codex-config-source-") as directory:
            cwd = Path(directory)
            config_directory = cwd / ".codex"
            config_directory.mkdir()
            config_path = config_directory / "config.toml"
            config_path.write_text(
                "[features]\nshell_tool = false\n",
                encoding="utf-8",
            )
            validate_codex_prelaunch_config_sources(cwd)

            secret = "private-config-path"
            config_path.write_text(
                '[debug.config_lockfile]\nload_path = "' + secret + '"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "retained forbidden authority",
            ) as captured:
                validate_codex_prelaunch_config_sources(cwd)
            self.assertNotIn(secret, str(captured.exception))

            config_path.write_text(
                'experimental_compact_prompt_file = "' + secret + '"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "forbidden authority"):
                validate_codex_prelaunch_config_sources(cwd)

    def test_enabled_config_read_layer_with_lock_fails_closed(self) -> None:
        secret = "private-layer-path"
        client = _ConfigAndStatusClient()
        client.layers.append(
            SimpleNamespace(
                disabled_reason=None,
                config={
                    "debug": {
                        "config_lockfile": {"load_path": secret},
                    }
                },
            )
        )
        with self.assertRaisesRegex(RuntimeError, "forbidden authority") as captured:
            validate_codex_app_server_configuration(
                SimpleNamespace(_client=client),
                cwd=ROOT,
            )
        self.assertNotIn(secret, str(captured.exception))

        client.layers[0].disabled_reason = "untrusted"
        validate_codex_app_server_configuration(
            SimpleNamespace(_client=client),
            cwd=ROOT,
        )

    def test_prompt_guard_is_content_blind_and_closed(self) -> None:
        self.assertEqual(
            validate_codex_prompt_markers("bounded ordinary CERA prompt"),
            "bounded ordinary CERA prompt",
        )
        markers = (
            "$ForbiddenSkill",
            "skill://forbidden",
            "PLUGIN://forbidden",
            "app://forbidden",
            "mcp://forbidden",
            "nested/path/SKILL.md",
        )
        for marker in markers:
            with self.assertRaisesRegex(
                ContractValidationError,
                "forbidden external-context marker",
            ) as captured:
                validate_codex_prompt_markers("prefix " + marker)
            self.assertNotIn(marker, str(captured.exception))

    def test_thread_start_is_environmentless_and_model_allowlisted(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.params: list[dict[str, object]] = []

            def thread_start(self, params):
                self.params.append(params)
                return SimpleNamespace(thread=SimpleNamespace(id="thread:one"))

        client = Client()
        codex = SimpleNamespace(_client=client)
        thread = start_codex_thread_without_environments(
            codex,
            model="gpt-5.6-sol",
            cwd=str(ROOT),
            ephemeral=True,
            base_instructions="stable",
            config={},
            service_name="test",
        )
        self.assertEqual(thread.id, "thread:one")
        self.assertEqual(client.params[0]["environments"], [])
        self.assertEqual(client.params[0]["approvalPolicy"], "never")

        with self.assertRaisesRegex(ContractValidationError, "static catalog"):
            start_codex_thread_without_environments(
                codex,
                model="gpt-5.6-unknown",
                cwd=str(ROOT),
                ephemeral=True,
                base_instructions="stable",
                config={},
                service_name="test",
            )
        self.assertEqual(len(client.params), 1)

    def test_completed_item_type_evidence_allows_user_messages_and_is_bounded(
        self,
    ) -> None:
        secret = "private-result-content"
        items = [
            SimpleNamespace(type="reasoning", content=secret),
            SimpleNamespace(type="userMessage", content=secret),
            SimpleNamespace(root=SimpleNamespace(type="agentMessage", content=secret)),
            SimpleNamespace(type="mcpToolCall", arguments=secret),
        ]
        items.extend(SimpleNamespace(type="plan", content=secret) for _ in range(100))
        items.extend(
            (
                SimpleNamespace(type="commandExecution", output=secret),
                SimpleNamespace(type="contextCompaction", content=secret),
                SimpleNamespace(type="futureSecretTool", arguments=secret),
                SimpleNamespace(type=None, content=secret),
            )
        )
        evidence = unsupported_completed_result_item_types(items)
        self.assertEqual(
            evidence,
            ("commandExecution", "contextCompaction", "plan", "unknown"),
        )
        self.assertNotIn(secret, repr(evidence))


if __name__ == "__main__":
    unittest.main()
