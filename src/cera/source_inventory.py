"""Repository source-discovery validation for packaged runtime modules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from cera.errors import ContractValidationError
from cera.serialization import domain_sha256, text_sha256


_REQUIRED_PACKAGES = frozenset(
    {
        "adult",
        "adult_craft",
        "composer",
        "consolidation",
        "continuous",
        "contracts",
        "evaluation",
        "evidence",
        "genesis",
        "ingress",
        "kernel",
        "providers",
        "realization",
        "reasoner",
        "resumption",
        "runtime",
        "storage",
    }
)


@dataclass(frozen=True, slots=True)
class RepositorySourceInventoryReceipt:
    SCHEMA_VERSION: ClassVar[str] = "cera.repository_source_inventory_receipt.v1"

    schema_version: str
    package_names: tuple[str, ...]
    source_paths: tuple[str, ...]
    source_hashes: tuple[str, ...]
    gitignore_sha256: str
    status: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("source inventory schema is invalid")
        if self.status != "valid":
            raise ContractValidationError("source inventory receipt must be valid")
        if len(self.source_paths) != len(self.source_hashes):
            raise ContractValidationError(
                "source inventory paths and hashes differ"
            )

    @property
    def receipt_sha256(self) -> str:
        return domain_sha256(
            "cera.repository_source_inventory_receipt.v1",
            self,
        )


def validate_repository_source_inventory(
    repository_root: str | Path,
) -> RepositorySourceInventoryReceipt:
    root = Path(repository_root).resolve()
    source_root = root / "src" / "cera"
    gitignore = root / ".gitignore"
    if not source_root.is_dir() or not gitignore.is_file():
        raise ContractValidationError(
            "repository source root or .gitignore is missing"
        )
    package_names = tuple(
        sorted(
            path.name
            for path in source_root.iterdir()
            if path.is_dir() and not path.name.startswith("__")
        )
    )
    missing = _REQUIRED_PACKAGES - set(package_names)
    if missing:
        raise ContractValidationError(
            "repository source inventory lacks required packages: "
            + ",".join(sorted(missing))
        )
    for package in package_names:
        if not (source_root / package / "__init__.py").is_file():
            raise ContractValidationError(
                f"source package lacks __init__.py: {package}"
            )
    ignore_text = gitignore.read_text(encoding="utf-8")
    active_lines = {
        value.strip()
        for value in ignore_text.splitlines()
        if value.strip() and not value.lstrip().startswith("#")
    }
    if "runtime/" in active_lines:
        raise ContractValidationError(
            "broad runtime ignore rule hides src/cera/runtime"
        )
    if "/runtime/" not in active_lines:
        raise ContractValidationError(
            "root runtime output ignore rule is not explicit"
        )
    if "!src/cera/runtime/**" in active_lines:
        raise ContractValidationError(
            "redundant runtime source exception can re-include generated files"
        )
    paths = tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in source_root.rglob("*.py")
            if "__pycache__" not in path.parts
        )
    )
    hashes = tuple(
        text_sha256((root / path).read_text(encoding="utf-8"))
        for path in paths
    )
    return RepositorySourceInventoryReceipt(
        schema_version=RepositorySourceInventoryReceipt.SCHEMA_VERSION,
        package_names=package_names,
        source_paths=paths,
        source_hashes=hashes,
        gitignore_sha256=text_sha256(ignore_text),
        status="valid",
    )
