"""Qualification-only SillyTavern staging with two repository integrations.

The general isolation helper intentionally copies no server plugin.  The final
creator-path qualification needs the CERA relay and creator extension, so this
module starts from that clean copy, removes every third-party client extension,
and installs only the two hash-bound repository sources into the disposable
tree.  It never reads or mutates installed user data.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import bytes_sha256, canonical_bytes, canonical_sha256

from .sillytavern_isolation import stage_isolated_sillytavern

MANIFEST_NAME = "CERA_QUALIFICATION_ISOLATED_COPY_MANIFEST.json"
_BASE_MANIFEST_NAME = "CERA_ISOLATED_COPY_MANIFEST.json"
_METADATA_BRIDGE_RELATIVE = Path("public/scripts/openai.js")
_PROVIDER_STAGE_RETRY_SERVER_BRIDGE_RELATIVE = Path("src/endpoints/backends/chat-completions.js")
_STAGED_NODE_SUITE_RELATIVES = (
    Path("plugins/cera-review-proxy/test.mjs"),
    Path("public/scripts/extensions/third-party/cera-creator-review/metadata-panel.test.mjs"),
)
PROVIDER_STAGE_RETRY_TERMINAL_UI_CONTRACT: dict[str, Any] = {
    "schema_version": "cera.pi_scene.qualification_critical_provider_stage_ui.v2",
    "projection_key": "critical_provider_stage_failure",
    "severity": "critical",
    "provider_required": True,
    "stage_required": True,
    "maximum_attempts_per_stage_occurrence": 3,
    "maximum_retry_actions_per_stage_occurrence": 2,
    "maximum_resume_prepared_actions_per_stage_occurrence": 1,
    "maximum_recording_repair_actions_per_request": 1,
    "maximum_recording_repair_successor_attempts": 3,
    "maximum_recording_repair_successor_retry_actions": 2,
    "manual_resume_prepared_enabled_when_backend_issued": True,
    "manual_recording_repair_enabled_when_backend_issued": True,
    "recursive_recording_repair": False,
    "recording_repair_required_terminal_only_without_action": True,
    "terminal_states": [
        "attempts_exhausted",
        "recording_repair_required",
        "recovery_required",
        "blocked_ambiguous_timeout",
    ],
    "terminal_provider_retry_enabled": False,
    "collapsible": True,
    "display_fields": [
        "severity",
        "provider",
        "model_family",
        "stage",
        "state",
        "maximum_attempts",
        "stage_attempts_total",
        "retry_actions_accepted",
        "failure_category",
    ],
    "hash_safe_only": True,
    "raw_provider_or_story_prose_allowed": False,
}
_LEGACY_METADATA_BRIDGE = (
    b"        if (data?.cera?.provisional && "
    b"data.cera.provisional_review_id) {\n"
    b"            const queue = Array.isArray(window.ceraCompletionMetadataQueue)\n"
    b"                ? window.ceraCompletionMetadataQueue\n"
    b"                : (window.ceraCompletionMetadataQueue = []);\n"
    b"            queue.push(structuredClone(data.cera));\n"
    b"            if (queue.length > 8) queue.splice(0, queue.length - 8);\n"
    b"            window.dispatchEvent(new CustomEvent("
    b"'cera:completion-metadata', {\n"
    b"                detail: data.cera,\n"
    b"            }));\n"
    b"        }\n"
)
_FULL_MODEL_METADATA_BRIDGE = (
    b"        if (data?.cera && typeof "
    b"window.ceraCaptureCompletionMetadata === 'function') {\n"
    b"            window.ceraCaptureCompletionMetadata(data.cera);\n"
    b"        }\n"
)
_STREAMING_ERROR_CAPTURE_TARGET = (
    b"        if (data.error) {\n"
    b"            !quiet && toastr.error(data.error.message || response.statusText, "
    b"'Chat Completion API');\n"
)
_STREAMING_ERROR_CAPTURE_BRIDGE = (
    b"        if (data?.schema_version === "
    b"'cera.provider_stage_retry_status_envelope.v1'\n"
    b"            && typeof window.ceraCaptureProviderStageRetryStatus === "
    b"'function') {\n"
    b"            window.ceraCaptureProviderStageRetryStatus(data);\n"
    b"        }\n"
    b"        if (data.error) {\n"
    b"            if (typeof window.ceraCaptureTransportFailure === 'function') {\n"
    b"                window.ceraCaptureTransportFailure(data);\n"
    b"            }\n"
    b"            !quiet && toastr.error(data.error.message || response.statusText, "
    b"'Chat Completion API');\n"
)
_SERVER_ERROR_FORWARD_TARGET = (
    b"            const message = fetchResponse.statusText || 'Unknown error occurred';\n"
    b"            const quota_error = fetchResponse.status === 429 && "
    b"errorData?.error?.type === 'insufficient_quota';\n"
    b"            console.error('Chat completion request error: ', message, "
    b"responseText);\n\n"
    b"            if (!response.headersSent) {\n"
    b"                response.send({ error: { message }, quota_error: quota_error });\n"
    b"            } else if (!response.writableEnded) {\n"
)
_PROVIDER_STAGE_SERVER_ERROR_FORWARD_BRIDGE = (
    b"            const message = fetchResponse.statusText || 'Unknown error occurred';\n"
    b"            const quota_error = fetchResponse.status === 429 && "
    b"errorData?.error?.type === 'insufficient_quota';\n"
    b"            const ceraProviderStageRetryEnvelope = (\n"
    b"                request.body.chat_completion_source === "
    b"CHAT_COMPLETION_SOURCES.CUSTOM\n"
    b"                && ['cera-alpha', 'cera-pi-scene-ordinary', "
    b"'cera-pi-scene-adult'].includes(request.body.model)\n"
    b"                && fetchResponse.status === 409\n"
    b"                && errorData?.schema_version === "
    b"'cera.provider_stage_retry_status_envelope.v1'\n"
    b"                && Object.keys(errorData).sort().join(',') === "
    b"'actions,schema_version,status'\n"
    b"                && errorData?.status?.schema_version === "
    b"'cera.provider_stage_retry_status.v1'\n"
    b"                && /^stage-retry-[a-f0-9]{64}$/.test("
    b"errorData.status.chain_id)\n"
    b"                && Array.isArray(errorData.actions)\n"
    b"                && errorData.actions.length <= 1\n"
    b"            ) ? errorData : null;\n"
    b"            console.error(\n"
    b"                'Chat completion request error: ',\n"
    b"                message,\n"
    b"                ceraProviderStageRetryEnvelope\n"
    b"                    ? '[CERA provider-stage status retained]'\n"
    b"                    : responseText,\n"
    b"            );\n\n"
    b"            if (!response.headersSent) {\n"
    b"                if (ceraProviderStageRetryEnvelope) {\n"
    b"                    return response.status(fetchResponse.status).send(\n"
    b"                        ceraProviderStageRetryEnvelope,\n"
    b"                    );\n"
    b"                }\n"
    b"                response.send({ error: { message }, quota_error: quota_error });\n"
    b"            } else if (!response.writableEnded) {\n"
)


def stage_qualification_sillytavern(
    source_root: Path,
    target_root: Path,
    *,
    repository_proxy_root: Path,
    repository_extension_root: Path,
) -> dict[str, Any]:
    target = target_root.resolve()
    base = stage_isolated_sillytavern(source_root, target)
    base_manifest_path = target / _BASE_MANIFEST_NAME
    if not base_manifest_path.is_file():
        raise StateConflictError("base isolated SillyTavern manifest disappeared")

    plugins_root = _within(target, target / "plugins")
    third_party_root = _within(
        target,
        target / "public" / "scripts" / "extensions" / "third-party",
    )
    _replace_empty_directory(plugins_root)
    _replace_empty_directory(third_party_root)

    proxy_target = plugins_root / "cera-review-proxy"
    extension_target = third_party_root / "cera-creator-review"
    _copy_plain_tree(repository_proxy_root.resolve(), proxy_target)
    _copy_plain_tree(repository_extension_root.resolve(), extension_target)
    _enable_repository_plugins(target / "config.yaml")
    bridge_path = _within(target, target / _METADATA_BRIDGE_RELATIVE)
    _install_completion_metadata_bridge(bridge_path)
    provider_stage_retry_server_bridge_path = _within(
        target,
        target / _PROVIDER_STAGE_RETRY_SERVER_BRIDGE_RELATIVE,
    )
    _install_provider_stage_retry_server_bridge(provider_stage_retry_server_bridge_path)

    base_manifest_path.unlink()
    entries = _tree_entries(target)
    proxy_entries = _relative_entries(proxy_target, target)
    extension_entries = _relative_entries(extension_target, target)
    body = {
        "schema_version": "cera.pi_scene.qualification_isolated_sillytavern.v4",
        "base_manifest_sha256": base["manifest_sha256"],
        "base_user_data_copied": base["user_data_copied"],
        "base_plugins_copied": base["plugins_copied"],
        "repository_proxy_source_sha256": _source_tree_sha256(repository_proxy_root.resolve()),
        "repository_extension_source_sha256": _source_tree_sha256(
            repository_extension_root.resolve()
        ),
        "installed_proxy_entries": proxy_entries,
        "installed_extension_entries": extension_entries,
        "metadata_bridge_path": _METADATA_BRIDGE_RELATIVE.as_posix(),
        "metadata_bridge_sha256": bytes_sha256(bridge_path.read_bytes()),
        "metadata_bridge_contract": "cera.full_model.capture_function.v1",
        "provider_stage_retry_client_bridge_contract": (
            "cera.provider_stage_retry.capture_function.v1"
        ),
        "provider_stage_retry_server_bridge_path": (
            _PROVIDER_STAGE_RETRY_SERVER_BRIDGE_RELATIVE.as_posix()
        ),
        "provider_stage_retry_server_bridge_sha256": bytes_sha256(
            provider_stage_retry_server_bridge_path.read_bytes()
        ),
        "provider_stage_retry_server_bridge_contract": (
            "cera.provider_stage_retry.status_envelope_forward.v1"
        ),
        "provider_stage_retry_terminal_ui_contract": dict(
            PROVIDER_STAGE_RETRY_TERMINAL_UI_CONTRACT
        ),
        "staged_node_suite_paths": [value.as_posix() for value in _STAGED_NODE_SUITE_RELATIVES],
        "staged_node_suite_source_sha256": canonical_sha256(
            [
                {
                    "path": value.as_posix(),
                    "sha256": bytes_sha256((target / value).read_bytes()),
                }
                for value in _STAGED_NODE_SUITE_RELATIVES
            ]
        ),
        "copied_file_count": len(entries),
        "copied_tree_sha256": canonical_sha256(entries),
        "only_repository_cera_integrations": True,
        "user_data_copied": False,
        "production": False,
        "loopback_launch_required": True,
        "review_loopback_override_env": "CERA_REVIEW_LOOPBACK_ROOT",
        "review_loopback_policy": "dynamic_http_127_0_0_1_port_no_path",
        "installed_default_port_5101_untouched": True,
    }
    manifest = {**body, "manifest_sha256": canonical_sha256(body)}
    _write_new_json(target / MANIFEST_NAME, manifest)
    verify_qualification_sillytavern(target)
    return manifest


def verify_qualification_sillytavern(target_root: Path) -> dict[str, Any]:
    target = target_root.resolve()
    path = target / MANIFEST_NAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateConflictError("qualification SillyTavern manifest is unreadable") from exc
    if not isinstance(value, dict):
        raise StateConflictError("qualification SillyTavern manifest is invalid")
    expected = value.get("manifest_sha256")
    unsigned = {key: item for key, item in value.items() if key != "manifest_sha256"}
    if value.get(
        "schema_version"
    ) != "cera.pi_scene.qualification_isolated_sillytavern.v4" or expected != canonical_sha256(
        unsigned
    ):
        raise StateConflictError("qualification SillyTavern manifest binding changed")
    if (
        value.get("only_repository_cera_integrations") is not True
        or value.get("user_data_copied") is not False
        or value.get("base_user_data_copied") is not False
        or value.get("base_plugins_copied") is not False
        or value.get("review_loopback_override_env") != "CERA_REVIEW_LOOPBACK_ROOT"
        or value.get("review_loopback_policy") != "dynamic_http_127_0_0_1_port_no_path"
        or value.get("installed_default_port_5101_untouched") is not True
    ):
        raise StateConflictError("qualification SillyTavern contains unapproved material")
    for required in ("server.js", "package.json", "node_modules", "public", "src", "data"):
        if not (target / required).exists():
            raise StateConflictError(f"qualification SillyTavern lacks {required}")
    if any((target / "data").iterdir()):
        raise StateConflictError("qualification SillyTavern data root is occupied")
    proxy_root = target / "plugins" / "cera-review-proxy"
    extension_root = (
        target / "public" / "scripts" / "extensions" / "third-party" / "cera-creator-review"
    )
    if set((target / "plugins").iterdir()) != {proxy_root}:
        raise StateConflictError("qualification SillyTavern has an unapproved server plugin")
    if set((extension_root.parent).iterdir()) != {extension_root}:
        raise StateConflictError("qualification SillyTavern has an unapproved client extension")
    if _relative_entries(proxy_root, target) != value.get("installed_proxy_entries"):
        raise StateConflictError("qualification CERA proxy integration changed")
    if _relative_entries(extension_root, target) != value.get("installed_extension_entries"):
        raise StateConflictError("qualification CERA creator extension changed")
    bridge_path = target / _METADATA_BRIDGE_RELATIVE
    if (
        not bridge_path.is_file()
        or bytes_sha256(bridge_path.read_bytes()) != value.get("metadata_bridge_sha256")
        or value.get("metadata_bridge_path") != _METADATA_BRIDGE_RELATIVE.as_posix()
        or value.get("metadata_bridge_contract") != "cera.full_model.capture_function.v1"
    ):
        raise StateConflictError("qualification completion metadata bridge changed")
    _verify_completion_metadata_bridge(bridge_path)
    provider_stage_retry_server_bridge_path = target / _PROVIDER_STAGE_RETRY_SERVER_BRIDGE_RELATIVE
    if (
        not provider_stage_retry_server_bridge_path.is_file()
        or bytes_sha256(provider_stage_retry_server_bridge_path.read_bytes())
        != value.get("provider_stage_retry_server_bridge_sha256")
        or value.get("provider_stage_retry_server_bridge_path")
        != _PROVIDER_STAGE_RETRY_SERVER_BRIDGE_RELATIVE.as_posix()
        or value.get("provider_stage_retry_client_bridge_contract")
        != "cera.provider_stage_retry.capture_function.v1"
        or value.get("provider_stage_retry_server_bridge_contract")
        != "cera.provider_stage_retry.status_envelope_forward.v1"
        or value.get("provider_stage_retry_terminal_ui_contract")
        != PROVIDER_STAGE_RETRY_TERMINAL_UI_CONTRACT
    ):
        raise StateConflictError("qualification provider-stage retry bridge changed")
    _verify_provider_stage_retry_server_bridge(provider_stage_retry_server_bridge_path)
    suite_entries = [
        {
            "path": relative.as_posix(),
            "sha256": bytes_sha256((target / relative).read_bytes()),
        }
        for relative in _STAGED_NODE_SUITE_RELATIVES
        if (target / relative).is_file()
    ]
    if (
        value.get("staged_node_suite_paths")
        != [relative.as_posix() for relative in _STAGED_NODE_SUITE_RELATIVES]
        or len(suite_entries) != len(_STAGED_NODE_SUITE_RELATIVES)
        or value.get("staged_node_suite_source_sha256") != canonical_sha256(suite_entries)
    ):
        raise StateConflictError("qualification staged Node suite binding changed")
    entries = _tree_entries(target)
    if len(entries) != value.get("copied_file_count") or canonical_sha256(entries) != value.get(
        "copied_tree_sha256"
    ):
        raise StateConflictError("qualification SillyTavern executable tree changed")
    config = (target / "config.yaml").read_text(encoding="utf-8")
    if (
        "enableServerPlugins: true" not in config
        or "enableServerPluginsAutoUpdate: false" not in config
    ):
        raise StateConflictError("qualification SillyTavern plugin policy changed")
    return value


def run_staged_sillytavern_node_suites(
    target_root: Path,
    *,
    node_executable: Path,
) -> dict[str, Any]:
    """Execute only the two hash-bound repository suites in the staged copy."""

    target = target_root.resolve()
    manifest = verify_qualification_sillytavern(target)
    node = node_executable.resolve()
    if not node.is_file():
        raise ContractValidationError("qualification Node.js executable is unavailable")
    command = (
        str(node),
        "--test",
        *(str((target / relative).resolve()) for relative in _STAGED_NODE_SUITE_RELATIVES),
    )
    completed = subprocess.run(
        command,
        cwd=target,
        capture_output=True,
        text=False,
        timeout=180,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise StateConflictError("qualification staged SillyTavern Node suites failed")
    body = {
        "schema_version": "cera.pi_scene.qualification_staged_node_suites.v1",
        "suite_count": len(_STAGED_NODE_SUITE_RELATIVES),
        "suite_source_sha256": manifest["staged_node_suite_source_sha256"],
        "node_executable_sha256": bytes_sha256(node.read_bytes()),
        "stdout_sha256": bytes_sha256(completed.stdout),
        "stderr_sha256": bytes_sha256(completed.stderr),
        "return_code": completed.returncode,
        "provider_calls": 0,
    }
    return {**body, "proof_sha256": canonical_sha256(body)}


def qualification_sillytavern_command(
    target_root: Path,
    *,
    node_executable: Path,
    port: int,
) -> tuple[str, ...]:
    target = target_root.resolve()
    verify_qualification_sillytavern(target)
    if not node_executable.is_file() or not 1 <= port <= 65535:
        raise ContractValidationError("qualification SillyTavern launch is invalid")
    return (
        str(node_executable.resolve()),
        str(target / "server.js"),
        "--port",
        str(port),
        "--dataRoot",
        str(target / "data"),
        "--configPath",
        str(target / "config.yaml"),
        "--no-listen",
        "--no-browserLaunchEnabled",
        "--whitelist",
    )


def _replace_empty_directory(path: Path) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise StateConflictError("qualification integration root is unsafe")
        shutil.rmtree(path)
    path.mkdir(parents=True)


def _copy_plain_tree(source: Path, target: Path) -> None:
    if not source.is_dir() or source.is_symlink() or target.exists():
        raise ContractValidationError("qualification integration source is invalid")
    if any(value.is_symlink() for value in source.rglob("*")):
        raise StateConflictError("qualification integration source contains a symlink")
    shutil.copytree(source, target, copy_function=shutil.copy2)


def _enable_repository_plugins(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    lines = [
        line
        for line in text.splitlines()
        if not line.strip().startswith(("enableServerPlugins:", "enableServerPluginsAutoUpdate:"))
    ]
    lines.extend(("enableServerPlugins: true", "enableServerPluginsAutoUpdate: false"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _install_completion_metadata_bridge(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise StateConflictError("qualification metadata bridge target is unavailable")
    data = path.read_bytes()
    legacy_lf = _LEGACY_METADATA_BRIDGE
    legacy_crlf = legacy_lf.replace(b"\n", b"\r\n")
    replacement_lf = _FULL_MODEL_METADATA_BRIDGE
    if data.count(legacy_lf) == 1:
        updated = data.replace(legacy_lf, replacement_lf, 1)
    elif data.count(legacy_crlf) == 1:
        updated = data.replace(
            legacy_crlf,
            replacement_lf.replace(b"\n", b"\r\n"),
            1,
        )
    elif _bridge_occurrences(data) == 1:
        updated = data
    else:
        raise StateConflictError("qualification SillyTavern metadata bridge source is unsupported")
    updated = _install_exact_bridge(
        updated,
        target=_STREAMING_ERROR_CAPTURE_TARGET,
        replacement=_STREAMING_ERROR_CAPTURE_BRIDGE,
        label="streaming transport failure capture",
    )
    path.write_bytes(updated)
    _verify_completion_metadata_bridge(path)


def _verify_completion_metadata_bridge(path: Path) -> None:
    data = path.read_bytes()
    if (
        _bridge_occurrences(data) != 1
        or _transport_capture_occurrences(data) != 1
        or _LEGACY_METADATA_BRIDGE in data
        or _LEGACY_METADATA_BRIDGE.replace(b"\n", b"\r\n") in data
    ):
        raise StateConflictError("qualification completion metadata bridge is invalid")


def _bridge_occurrences(data: bytes) -> int:
    return data.count(_FULL_MODEL_METADATA_BRIDGE) + data.count(
        _FULL_MODEL_METADATA_BRIDGE.replace(b"\n", b"\r\n")
    )


def _transport_capture_occurrences(data: bytes) -> int:
    return sum(
        data.count(value) + data.count(value.replace(b"\n", b"\r\n"))
        for value in (_STREAMING_ERROR_CAPTURE_BRIDGE,)
    )


def _install_provider_stage_retry_server_bridge(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise StateConflictError("qualification provider-stage retry bridge target is unavailable")
    updated = _install_exact_bridge(
        path.read_bytes(),
        target=_SERVER_ERROR_FORWARD_TARGET,
        replacement=_PROVIDER_STAGE_SERVER_ERROR_FORWARD_BRIDGE,
        label="server provider-stage status forwarding",
    )
    path.write_bytes(updated)
    _verify_provider_stage_retry_server_bridge(path)


def _verify_provider_stage_retry_server_bridge(path: Path) -> None:
    data = path.read_bytes()
    occurrences = data.count(_PROVIDER_STAGE_SERVER_ERROR_FORWARD_BRIDGE) + data.count(
        _PROVIDER_STAGE_SERVER_ERROR_FORWARD_BRIDGE.replace(b"\n", b"\r\n")
    )
    if occurrences != 1 or _SERVER_ERROR_FORWARD_TARGET in data:
        raise StateConflictError("qualification provider-stage retry server bridge is invalid")


def _install_exact_bridge(
    data: bytes,
    *,
    target: bytes,
    replacement: bytes,
    label: str,
) -> bytes:
    target_crlf = target.replace(b"\n", b"\r\n")
    replacement_crlf = replacement.replace(b"\n", b"\r\n")
    if data.count(target) == 1:
        return data.replace(target, replacement, 1)
    if data.count(target_crlf) == 1:
        return data.replace(target_crlf, replacement_crlf, 1)
    if data.count(replacement) + data.count(replacement_crlf) == 1:
        return data
    raise StateConflictError(f"qualification SillyTavern {label} source is unsupported")


def _within(root: Path, path: Path) -> Path:
    value = path.resolve()
    if not value.is_relative_to(root):
        raise ContractValidationError("qualification path escaped its disposable root")
    return value


def _tree_entries(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if path.is_symlink():
            raise StateConflictError("qualification SillyTavern tree contains a symlink")
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        data = path.read_bytes()
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": len(data),
                "sha256": bytes_sha256(data),
            }
        )
    return entries


def _relative_entries(root: Path, base: Path) -> list[dict[str, Any]]:
    return [
        value
        for value in _tree_entries(base)
        if str(value["path"]).startswith(root.relative_to(base).as_posix() + "/")
    ]


def _source_tree_sha256(root: Path) -> str:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not path.is_file():
            continue
        data = path.read_bytes()
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": len(data),
                "sha256": bytes_sha256(data),
            }
        )
    return canonical_sha256(entries)


def _write_new_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("xb") as stream:
        stream.write(canonical_bytes(payload) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


__all__ = [
    "MANIFEST_NAME",
    "PROVIDER_STAGE_RETRY_TERMINAL_UI_CONTRACT",
    "qualification_sillytavern_command",
    "run_staged_sillytavern_node_suites",
    "stage_qualification_sillytavern",
    "verify_qualification_sillytavern",
]
