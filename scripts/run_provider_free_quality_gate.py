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
_FORMAT_TARGETS = (
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
)
_LINT_TARGETS = _FORMAT_TARGETS
_TYPE_TARGETS = (
    "scripts/run_provider_free_quality_gate.py",
    "src/cera/provider_dispatch_guard.py",
    "src/cera/cognition",
    "src/cera/semantic_validation",
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


def _suite(names: tuple[str, ...]) -> unittest.TestSuite:
    loader = unittest.defaultTestLoader
    if names:
        return loader.loadTestsFromNames(names)
    return loader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))


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
