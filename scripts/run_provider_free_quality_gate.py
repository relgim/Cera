"""Run the provider-free CERA quality gate against this exact checkout.

This runner intentionally removes provider credentials and prepends the
checkout's ``src`` directory before importing CERA.  It exists because linked
worktrees may share a virtual environment whose editable-install pointer names
a different checkout.  Compilation is limited to Git-tracked Python sources;
the reported commit, tree, and source manifest bind the bytes that were checked.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import unittest
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = (ROOT / "src").resolve()
_PROVIDER_ENVIRONMENT = (
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "CERA_REQUEST_EVIDENCE_TOKEN",
)
_PINNED_QUALITY_TOOLS = {
    "mypy": "2.3.0",
    "ruff": "0.16.2",
}
_GENERATED_CONTRACT_CHECK = "scripts/generate_provider_stage_retry_contracts.py"
_PROVIDER_STAGE_RETRY_SCHEMA_TARGETS = (
    "schemas/provider_stage_retry/v1/action.schema.json",
    "schemas/provider_stage_retry/v1/blocked_ambiguous.schema.json",
    "schemas/provider_stage_retry/v1/common.schema.json",
    "schemas/provider_stage_retry/v1/compatibility_adapter.schema.json",
    "schemas/provider_stage_retry/v1/exhausted.schema.json",
    "schemas/provider_stage_retry/v1/status.schema.json",
    "schemas/provider_stage_retry/v1/status_envelope.schema.json",
)
_PROVIDER_STAGE_RETRY_FORMAT_TARGETS = (
    "scripts/run_pi_scene_full_model_qualification.py",
    "scripts/run_pi_scene_lean_server.py",
    "src/cera/adult_pipeline/pi_roles.py",
    "src/cera/adult_pipeline/pipeline.py",
    "src/cera/generated/__init__.py",
    "src/cera/generated/provider_stage_retry_contracts_v1.py",
    "src/cera/pi_scene/adult_stage_retry_integration.py",
    "src/cera/pi_scene/http.py",
    "src/cera/pi_scene/operation_ledger.py",
    "src/cera/pi_scene/provider_stage_retry.py",
    "src/cera/pi_scene/provider_stage_retry_adapters.py",
    "src/cera/pi_scene/provider_stage_retry_adult_actions.py",
    "src/cera/pi_scene/provider_stage_retry_assembly.py",
    "src/cera/pi_scene/provider_stage_retry_blob.py",
    "src/cera/pi_scene/provider_stage_retry_controller.py",
    "src/cera/pi_scene/provider_stage_retry_port.py",
    "src/cera/pi_scene/provider_stage_retry_runtime.py",
    "src/cera/pi_scene/provider_stage_retry_store.py",
    "src/cera/pi_scene/provider_stage_retry_scope.py",
    "src/cera/pi_scene/provider_stage_retry_packets.py",
    "src/cera/pi_scene/provider_stage_retry_executor.py",
    "src/cera/pi_scene/provider_stage_retry_http.py",
    "src/cera/pi_scene/provider_stage_retry_ordinary.py",
    "src/cera/pi_scene/provider_stage_retry_ordinary_custody.py",
    "src/cera/pi_scene/provider_stage_retry_ordinary_retrieval.py",
    "src/cera/pi_scene/qualification_isolation.py",
    "src/cera/pi_scene/runtime.py",
    "src/cera/providers/__init__.py",
    "src/cera/storage/migrations.py",
    "src/cera/storage/provider_stage_retry_store.py",
    "tests/test_provider_stage_retry_schema_generation.py",
    "tests/test_provider_stage_retry.py",
    "tests/test_provider_stage_retry_adapters.py",
    "tests/test_provider_stage_retry_sqlite.py",
    "tests/test_provider_stage_retry_scope.py",
    "tests/test_provider_stage_retry_packets.py",
    "tests/test_provider_stage_retry_runtime.py",
    "tests/test_provider_stage_retry_executor.py",
    "tests/test_provider_retryable_failure_metadata.py",
    "tests/test_adult_pipeline_pi_integration.py",
    "tests/test_pi_scene_adult_stage_retry_integration.py",
    "tests/test_pi_scene_full_model_qualification.py",
    "tests/test_provider_stage_retry_assembly.py",
    "tests/test_provider_stage_retry_http.py",
    "tests/test_provider_stage_retry_ordinary.py",
    "tests/test_provider_stage_retry_ordinary_custody.py",
    "tests/test_provider_stage_retry_ordinary_retrieval.py",
    "tests/test_sqlite_store.py",
)
# These pre-existing provider modules are intentionally not whole-file formatted
# as part of this narrow correction.  They remain exact lint, type, compile, and
# test dependencies; adding them to Ruff format would require a large unrelated
# legacy rewrite.
_PROVIDER_STAGE_RETRY_LEGACY_SOURCE_TARGETS = (
    "src/cera/pi_scene/pi_adapter.py",
    "src/cera/providers/codex.py",
    "src/cera/providers/codex_exec.py",
    "src/cera/providers/deepseek.py",
    "src/cera/providers/models.py",
)
_PROVIDER_STAGE_RETRY_TYPE_TARGETS = (
    _GENERATED_CONTRACT_CHECK,
    "scripts/run_pi_scene_full_model_qualification.py",
    "scripts/run_pi_scene_lean_server.py",
    "src/cera/adult_pipeline/pi_roles.py",
    "src/cera/adult_pipeline/pipeline.py",
    "src/cera/generated/__init__.py",
    "src/cera/generated/provider_stage_retry_contracts_v1.py",
    "src/cera/pi_scene/adult_stage_retry_integration.py",
    "src/cera/pi_scene/http.py",
    "src/cera/pi_scene/operation_ledger.py",
    "src/cera/pi_scene/provider_stage_retry.py",
    "src/cera/pi_scene/provider_stage_retry_adapters.py",
    "src/cera/pi_scene/provider_stage_retry_adult_actions.py",
    "src/cera/pi_scene/provider_stage_retry_assembly.py",
    "src/cera/pi_scene/provider_stage_retry_blob.py",
    "src/cera/pi_scene/provider_stage_retry_controller.py",
    "src/cera/pi_scene/provider_stage_retry_port.py",
    "src/cera/pi_scene/provider_stage_retry_runtime.py",
    "src/cera/pi_scene/provider_stage_retry_store.py",
    "src/cera/pi_scene/provider_stage_retry_scope.py",
    "src/cera/pi_scene/provider_stage_retry_packets.py",
    "src/cera/pi_scene/provider_stage_retry_executor.py",
    "src/cera/pi_scene/provider_stage_retry_http.py",
    "src/cera/pi_scene/provider_stage_retry_ordinary.py",
    "src/cera/pi_scene/provider_stage_retry_ordinary_custody.py",
    "src/cera/pi_scene/provider_stage_retry_ordinary_retrieval.py",
    "src/cera/pi_scene/qualification.py",
    "src/cera/pi_scene/qualification_isolation.py",
    "src/cera/pi_scene/runtime.py",
    "src/cera/providers/__init__.py",
    "src/cera/storage/migrations.py",
    "src/cera/storage/provider_stage_retry_store.py",
)
_PROVIDER_STAGE_RETRY_LEGACY_TYPE_TARGETS = _PROVIDER_STAGE_RETRY_LEGACY_SOURCE_TARGETS
_PROVIDER_STAGE_RETRY_TEST_MODULES = (
    "tests.test_adult_pipeline_pi_integration",
    "tests.test_pi_scene_adult_stage_retry_integration",
    "tests.test_pi_scene_full_model_launcher",
    "tests.test_pi_scene_http_session_review",
    "tests.test_pi_scene_lean_v1",
    "tests.test_provider_stage_retry_schema_generation",
    "tests.test_provider_stage_retry",
    "tests.test_provider_stage_retry_adapters",
    "tests.test_provider_stage_retry_assembly",
    "tests.test_provider_stage_retry_sqlite",
    "tests.test_provider_stage_retry_scope",
    "tests.test_provider_stage_retry_packets",
    "tests.test_provider_stage_retry_runtime",
    "tests.test_provider_stage_retry_executor",
    "tests.test_provider_stage_retry_http",
    "tests.test_provider_stage_retry_ordinary",
    "tests.test_provider_stage_retry_ordinary_custody",
    "tests.test_provider_stage_retry_ordinary_retrieval",
    "tests.test_provider_retryable_failure_metadata",
    "tests.test_pi_scene_full_model_qualification",
    "tests.test_sqlite_store",
)
_PROVIDER_STAGE_RETRY_COMPILE_TARGETS = (
    *_PROVIDER_STAGE_RETRY_TYPE_TARGETS,
    *_PROVIDER_STAGE_RETRY_LEGACY_TYPE_TARGETS,
    *(f"{module.replace('.', '/')}.py" for module in _PROVIDER_STAGE_RETRY_TEST_MODULES),
)
_SILLYTAVERN_RETRY_NODE_CHECK_TARGETS = (
    "integrations/sillytavern/generated/provider-stage-retry-contracts-v1.mjs",
    "integrations/sillytavern/cera-review-proxy-plugin/generated/provider-stage-retry-contracts-v1.mjs",
    "integrations/sillytavern/cera-review-proxy-plugin/index.js",
    "integrations/sillytavern/cera-review-proxy-plugin/test.mjs",
    "integrations/sillytavern/creator-review-extension/generated/provider-stage-retry-contracts-v1.mjs",
    "integrations/sillytavern/creator-review-extension/index.js",
    "integrations/sillytavern/creator-review-extension/review-actions.js",
    "integrations/sillytavern/creator-review-extension/metadata-panel.test.mjs",
)
_SILLYTAVERN_RETRY_NODE_TEST_TARGETS = (
    "integrations/sillytavern/cera-review-proxy-plugin/test.mjs",
    "integrations/sillytavern/creator-review-extension/metadata-panel.test.mjs",
)
# Repository integration must intentionally differ from the installed copy
# until the disposable SillyTavern campaign passes and the user separately
# authorizes installation. Keep every source/behavior contract in discovery,
# but defer only the two byte-equivalence assertions at this boundary.
_DEFERRED_PREQUALIFICATION_TESTS = frozenset(
    {
        "tests.test_sillytavern_installation_contract."
        "SillyTavernInstallationContractTests."
        "test_installed_creator_review_extension_matches_repository_source",
        "tests.test_sillytavern_installation_contract."
        "SillyTavernInstallationContractTests."
        "test_installed_loopback_relay_matches_repository_source",
    }
)
_FORMAT_TARGETS = (
    _GENERATED_CONTRACT_CHECK,
    "scripts/run_provider_free_quality_gate.py",
    "src/cera/provider_dispatch_guard.py",
    "src/cera/cognition",
    "src/cera/pi_scene/qualification.py",
    "src/cera/semantic_validation",
    "tests/test_cognition_contracts.py",
    "tests/test_cognition_provider.py",
    "tests/test_pi_scene_cognition_authority.py",
    "tests/test_provider_dispatch_guard.py",
    "tests/test_provider_free_quality_gate.py",
    "tests/test_semantic_validation_contracts.py",
    "tests/test_semantic_validation_session.py",
    "tests/test_phase1_provider_boundaries.py",
    *_PROVIDER_STAGE_RETRY_FORMAT_TARGETS,
)
_LINT_TARGETS = (*_FORMAT_TARGETS, *_PROVIDER_STAGE_RETRY_LEGACY_SOURCE_TARGETS)
_TYPE_TARGETS = (
    "scripts/run_provider_free_quality_gate.py",
    "src/cera/provider_dispatch_guard.py",
    "src/cera/cognition",
    "src/cera/semantic_validation",
    *_PROVIDER_STAGE_RETRY_TYPE_TARGETS,
)


def _configure_checkout() -> Path:
    sys.dont_write_bytecode = True
    for name in _PROVIDER_ENVIRONMENT:
        os.environ.pop(name, None)
    # Set the kill switch before importing any CERA module.  A package import
    # must never get an opportunity to dispatch while the gate is bootstrapping.
    os.environ["CERA_PROVIDER_DISPATCH_DISABLED"] = "1"
    source_text = str(SOURCE_ROOT)
    if not sys.path or Path(sys.path[0]).resolve() != SOURCE_ROOT:
        sys.path.insert(0, source_text)
    root_text = str(ROOT)
    if not any(Path(value or os.getcwd()).resolve() == ROOT for value in sys.path):
        sys.path.insert(1, root_text)
    import cera
    from cera.provider_dispatch_guard import PROVIDER_DISPATCH_DISABLED_ENV

    if PROVIDER_DISPATCH_DISABLED_ENV != "CERA_PROVIDER_DISPATCH_DISABLED":
        raise RuntimeError("provider dispatch guard environment identity changed")

    package_root = Path(cera.__file__).resolve().parent
    expected = SOURCE_ROOT / "cera"
    if package_root != expected:
        raise RuntimeError(
            "quality gate imported CERA from the wrong checkout: "
            f"expected={expected}, actual={package_root}"
        )
    return package_root


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(ROOT), *arguments),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        diagnostic = " ".join(completed.stderr.split())[:400] or "unknown git error"
        raise RuntimeError(f"quality gate could not inspect Git checkout: {diagnostic}")
    return completed.stdout.strip()


def _checkout_identity(
    *,
    expected_commit: str | None = None,
    expected_tree: str | None = None,
    require_clean: bool = False,
) -> tuple[str, str, bool]:
    if (expected_commit is None) != (expected_tree is None):
        raise RuntimeError("quality gate commit and tree pins must be supplied together")
    for value, label in (
        (expected_commit, "commit"),
        (expected_tree, "tree"),
    ):
        if value is not None and re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise RuntimeError(f"quality gate {label} pin must be a full lowercase Git hash")
    checkout_root = Path(_git("rev-parse", "--show-toplevel")).resolve()
    if checkout_root != ROOT:
        raise RuntimeError(
            "quality gate resolved a different Git checkout: "
            f"expected={ROOT}, actual={checkout_root}"
        )
    commit = _git("rev-parse", "HEAD")
    tree = _git("rev-parse", "HEAD^{tree}")
    if expected_commit is not None and commit != expected_commit:
        raise RuntimeError(
            f"quality gate commit pin mismatch: expected={expected_commit}, actual={commit}"
        )
    if expected_tree is not None and tree != expected_tree:
        raise RuntimeError(
            f"quality gate tree pin mismatch: expected={expected_tree}, actual={tree}"
        )
    clean = not _git("status", "--porcelain=v1", "--untracked-files=all")
    if require_clean and not clean:
        raise RuntimeError("quality gate requires a clean checkout")
    return commit, tree, clean


def _tracked_python_paths() -> tuple[Path, ...]:
    completed = subprocess.run(
        ("git", "-C", str(ROOT), "ls-files", "-z", "--", "*.py"),
        check=False,
        capture_output=True,
    )
    if completed.returncode:
        diagnostic = completed.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(
            "quality gate could not enumerate tracked Python sources: "
            f"{' '.join(diagnostic.split())[:400]}"
        )
    paths: list[Path] = []
    for raw_relative in completed.stdout.split(b"\0"):
        if not raw_relative:
            continue
        relative = Path(raw_relative.decode("utf-8"))
        path = (ROOT / relative).resolve()
        try:
            path.relative_to(ROOT)
        except ValueError as exc:
            raise RuntimeError(f"tracked Python source escaped checkout: {relative}") from exc
        if not path.is_file():
            raise RuntimeError(f"tracked Python source is unavailable: {relative}")
        paths.append(path)
    if not paths:
        raise RuntimeError("quality gate found no tracked Python sources")
    return tuple(sorted(paths))


def _tracked_python_sources() -> tuple[tuple[Path, bytes], ...]:
    return tuple((path, path.read_bytes()) for path in _tracked_python_paths())


def _assert_required_retry_targets(
    sources: tuple[tuple[Path, bytes], ...],
) -> None:
    missing_files = sorted(
        target
        for target in (
            *_PROVIDER_STAGE_RETRY_SCHEMA_TARGETS,
            *_SILLYTAVERN_RETRY_NODE_CHECK_TARGETS,
        )
        if not (ROOT / target).is_file()
    )
    if missing_files:
        raise RuntimeError(
            "provider-stage Retry gate targets are unavailable: " + ", ".join(missing_files)
        )
    compiled_targets = {path.relative_to(ROOT).as_posix() for path, _ in sources}
    missing_python = sorted(set(_PROVIDER_STAGE_RETRY_COMPILE_TARGETS) - compiled_targets)
    if missing_python:
        raise RuntimeError(
            "provider-stage Retry Python targets are not Git-tracked for compilation: "
            + ", ".join(missing_python)
        )


def _compile_python(
    sources: tuple[tuple[Path, bytes], ...] | None = None,
) -> int:
    snapshot = _tracked_python_sources() if sources is None else sources
    for path, content in snapshot:
        source = content.decode("utf-8-sig")
        compile(source, str(path), "exec", dont_inherit=True)
    return len(snapshot)


def _tracked_python_manifest_sha256(
    sources: tuple[tuple[Path, bytes], ...] | None = None,
) -> str:
    digest = hashlib.sha256()
    snapshot = _tracked_python_sources() if sources is None else sources
    for path, content in snapshot:
        relative = path.relative_to(ROOT).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_final_checkout_identity(
    *,
    commit: str,
    tree: str,
    clean: bool,
    manifest_sha256: str,
    pyproject_sha256: str,
    require_clean: bool,
) -> None:
    final_commit, final_tree, final_clean = _checkout_identity(
        expected_commit=commit,
        expected_tree=tree,
        require_clean=require_clean,
    )
    final_sources = _tracked_python_sources()
    final_manifest_sha256 = _tracked_python_manifest_sha256(final_sources)
    final_pyproject_sha256 = _file_sha256(ROOT / "pyproject.toml")
    if (final_commit, final_tree, final_clean) != (commit, tree, clean):
        raise RuntimeError("quality gate checkout identity changed while checks were running")
    if final_manifest_sha256 != manifest_sha256:
        raise RuntimeError("tracked Python bytes changed while quality checks were running")
    if final_pyproject_sha256 != pyproject_sha256:
        raise RuntimeError("quality-tool configuration changed while checks were running")


def _assert_quality_tool_versions() -> None:
    for distribution, expected in sorted(_PINNED_QUALITY_TOOLS.items()):
        try:
            actual = version(distribution)
        except PackageNotFoundError as exc:
            raise RuntimeError(
                f"missing pinned quality tool {distribution}=={expected}; "
                "install the project's dev extra"
            ) from exc
        if actual != expected:
            raise RuntimeError(
                f"quality tool version mismatch for {distribution}: "
                f"expected={expected}, actual={actual}"
            )


def _run_checked(command: tuple[str, ...], *, label: str) -> None:
    environment = dict(os.environ)
    for name in _PROVIDER_ENVIRONMENT:
        environment.pop(name, None)
    environment["CERA_PROVIDER_DISPATCH_DISABLED"] = "1"
    existing_pythonpath = environment.get("PYTHONPATH")
    python_paths = (str(SOURCE_ROOT), str(ROOT))
    environment["PYTHONPATH"] = os.pathsep.join(
        (*python_paths, *((existing_pythonpath,) if existing_pythonpath else ()))
    )
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        env=environment,
    )
    if completed.returncode:
        raise RuntimeError(f"{label} failed with exit code {completed.returncode}")


def _run_quality_tools() -> None:
    _assert_quality_tool_versions()
    _run_checked(
        (
            sys.executable,
            str(ROOT / _GENERATED_CONTRACT_CHECK),
            "--check",
        ),
        label="provider-stage generated contract drift check",
    )
    _run_checked(
        (
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--check",
            "--config",
            str(ROOT / "pyproject.toml"),
            *(_FORMAT_TARGETS),
        ),
        label="Ruff format check",
    )
    _run_checked(
        (
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--config",
            str(ROOT / "pyproject.toml"),
            *(_LINT_TARGETS),
        ),
        label="Ruff lint check",
    )
    _run_checked(
        (
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(ROOT / "pyproject.toml"),
            *(_TYPE_TARGETS),
        ),
        label="mypy type check",
    )
    _run_checked(
        (
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(ROOT / "pyproject.toml"),
            "--allow-redefinition",
            *_PROVIDER_STAGE_RETRY_LEGACY_TYPE_TARGETS,
        ),
        label="mypy legacy provider Retry dependency type check",
    )
    for target in _SILLYTAVERN_RETRY_NODE_CHECK_TARGETS:
        _run_checked(
            ("node", "--check", target),
            label=f"SillyTavern provider-stage Retry syntax check ({target})",
        )
    _run_checked(
        (
            "node",
            "--test",
            *_SILLYTAVERN_RETRY_NODE_TEST_TARGETS,
        ),
        label="SillyTavern provider-stage Retry tests",
    )


def _test_cases(suite: unittest.TestSuite) -> tuple[unittest.TestCase, ...]:
    cases: list[unittest.TestCase] = []
    for value in suite:
        if isinstance(value, unittest.TestSuite):
            cases.extend(_test_cases(value))
        elif isinstance(value, unittest.TestCase):
            cases.append(value)
        else:
            raise RuntimeError("quality gate discovered an unknown unittest value")
    return tuple(cases)


def _suite(names: tuple[str, ...]) -> unittest.TestSuite:
    loader = unittest.defaultTestLoader
    if names:
        return loader.loadTestsFromNames(names)
    discovered = _test_cases(loader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT)))
    discovered_ids = {value.id() for value in discovered}
    missing = _DEFERRED_PREQUALIFICATION_TESTS - discovered_ids
    if missing:
        raise RuntimeError(
            "deferred installed-SillyTavern test identity changed: " + ", ".join(sorted(missing))
        )
    return unittest.TestSuite(
        value for value in discovered if value.id() not in _DEFERRED_PREQUALIFICATION_TESTS
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "tests",
        nargs="*",
        help="Optional dotted unittest names; omit for complete discovery.",
    )
    parser.add_argument(
        "--compile-only",
        action="store_true",
        help="Compile tracked checkout Python without running tests.",
    )
    parser.add_argument(
        "--expected-commit",
        help="Fail unless HEAD is this exact full commit hash.",
    )
    parser.add_argument(
        "--expected-tree",
        help="Fail unless HEAD has this exact full tree hash.",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Development-only: allow changes outside the pinned HEAD tree.",
    )
    arguments = parser.parse_args(argv)
    package_root = _configure_checkout()
    commit, tree, clean = _checkout_identity(
        expected_commit=arguments.expected_commit,
        expected_tree=arguments.expected_tree,
        require_clean=not arguments.allow_dirty,
    )
    sources = _tracked_python_sources()
    _assert_required_retry_targets(sources)
    compiled = _compile_python(sources)
    manifest_sha256 = _tracked_python_manifest_sha256(sources)
    pyproject_sha256 = _file_sha256(ROOT / "pyproject.toml")
    from cera.active_runtime import ACTIVE_RUNTIME_PROFILE

    print(f"checkout={ROOT}")
    print(f"checkout_head_commit={commit}")
    print(f"checkout_head_tree={tree}")
    print(f"checkout_clean={str(clean).lower()}")
    print(f"python_runtime={sys.version.split()[0]}")
    print(f"cera_import={package_root}")
    print(f"active_runtime_profile_id={ACTIVE_RUNTIME_PROFILE.profile_id}")
    print(f"active_runtime_profile_sha256={ACTIVE_RUNTIME_PROFILE.profile_sha256}")
    print(f"compiled_python_files={compiled}")
    print(f"tracked_python_manifest_sha256={manifest_sha256}")
    print(f"pyproject_sha256={pyproject_sha256}")
    print("provider_credentials=removed")
    print("provider_dispatch_guard=enabled")
    if arguments.compile_only:
        _assert_final_checkout_identity(
            commit=commit,
            tree=tree,
            clean=clean,
            manifest_sha256=manifest_sha256,
            pyproject_sha256=pyproject_sha256,
            require_clean=not arguments.allow_dirty,
        )
        return 0
    _run_quality_tools()
    print("ruff_format=passed")
    print("ruff_lint=passed")
    print("mypy=passed")
    print("sillytavern_retry_syntax=passed")
    print("sillytavern_retry_tests=passed")
    if not arguments.tests:
        print("installed_sillytavern_equivalence=deferred_until_disposable_qualification")
    result = unittest.TextTestRunner(verbosity=2).run(_suite(tuple(arguments.tests)))
    if not result.wasSuccessful():
        return 1
    _assert_final_checkout_identity(
        commit=commit,
        tree=tree,
        clean=clean,
        manifest_sha256=manifest_sha256,
        pyproject_sha256=pyproject_sha256,
        require_clean=not arguments.allow_dirty,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
