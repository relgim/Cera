"""Run CERA tests against this checkout, never an editable install elsewhere.

This runner intentionally removes provider credentials and prepends the
checkout's ``src`` directory before importing CERA.  It exists because linked
worktrees may share a virtual environment whose editable-install pointer names
a different checkout.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = (ROOT / "src").resolve()
_PROVIDER_ENVIRONMENT = (
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
)


def _configure_checkout() -> Path:
    sys.dont_write_bytecode = True
    for name in _PROVIDER_ENVIRONMENT:
        os.environ.pop(name, None)
    source_text = str(SOURCE_ROOT)
    if not sys.path or Path(sys.path[0]).resolve() != SOURCE_ROOT:
        sys.path.insert(0, source_text)
    import cera

    package_root = Path(cera.__file__).resolve().parent
    expected = SOURCE_ROOT / "cera"
    if package_root != expected:
        raise RuntimeError(
            "quality gate imported CERA from the wrong checkout: "
            f"expected={expected}, actual={package_root}"
        )
    return package_root


def _compile_python() -> int:
    roots = (
        ROOT / "src",
        ROOT / "scripts",
        ROOT / "tests",
        ROOT / "tools",
        ROOT / "genesis",
    )
    paths = sorted(
        path
        for base in roots
        for path in base.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    for path in paths:
        source = path.read_text(encoding="utf-8-sig")
        compile(source, str(path), "exec", dont_inherit=True)
    return len(paths)


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
    arguments = parser.parse_args(argv)
    package_root = _configure_checkout()
    compiled = _compile_python()
    print(f"checkout={ROOT}")
    print(f"cera_import={package_root}")
    print(f"compiled_python_files={compiled}")
    print("provider_credentials=removed")
    if arguments.compile_only:
        return 0
    result = unittest.TextTestRunner(verbosity=2).run(_suite(tuple(arguments.tests)))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
