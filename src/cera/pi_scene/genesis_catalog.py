"""Strict selection of the newest revision in an accepted Genesis catalog."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cera.errors import ContractValidationError, StateConflictError

from ._world_workspace_files import (
    SHA256_PATTERN,
    assert_plain_tree,
    is_link_or_reparse,
    read_json_object,
    sha256_bytes,
    tree_sha256,
)


@dataclass(frozen=True, slots=True)
class GenesisRevisionPinV1:
    schema_version: str
    package_id: str
    revision_id: str
    revision_number: int
    revision_label: str
    parent_revision_id: str | None
    manifest_sha256: str
    package_tree_sha256: str

    SCHEMA_VERSION = "cera.pi_scene_genesis_revision_pin.v1"

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Genesis pin schema changed")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.package_id, self.revision_id, self.revision_label)
        ):
            raise ContractValidationError("Genesis pin identity is invalid")
        if type(self.revision_number) is not int or self.revision_number < 1:
            raise ContractValidationError("Genesis pin revision number is invalid")
        if self.parent_revision_id is not None and (
            not isinstance(self.parent_revision_id, str)
            or not self.parent_revision_id.strip()
        ):
            raise ContractValidationError("Genesis pin parent revision is invalid")
        if (self.revision_number == 1) != (self.parent_revision_id is None):
            raise ContractValidationError("Genesis pin parent lineage is invalid")
        if not all(
            isinstance(value, str) and SHA256_PATTERN.fullmatch(value)
            for value in (self.manifest_sha256, self.package_tree_sha256)
        ):
            raise ContractValidationError("Genesis pin hash is invalid")

    @classmethod
    def from_dict(cls, raw: object) -> "GenesisRevisionPinV1":
        if not isinstance(raw, dict) or set(raw) != {
            "schema_version",
            "package_id",
            "revision_id",
            "revision_number",
            "revision_label",
            "parent_revision_id",
            "manifest_sha256",
            "package_tree_sha256",
        }:
            raise ContractValidationError("Genesis pin fields changed")
        return cls(**raw)


@dataclass(frozen=True, slots=True)
class AcceptedGenesisRevisionV1:
    package_root: Path
    pin: GenesisRevisionPinV1


class AcceptedGenesisCatalog:
    """An explicitly accepted package directory, not an ambient repo scan."""

    def __init__(self, accepted_packages_root: Path) -> None:
        if not accepted_packages_root.is_absolute():
            raise ContractValidationError(
                "accepted Genesis catalog root must be absolute"
            )
        self.root = accepted_packages_root.resolve()
        assert_plain_tree(self.root, "accepted Genesis catalog")

    def newest(
        self, *, package_id: str | None = None
    ) -> AcceptedGenesisRevisionV1:
        candidates: list[AcceptedGenesisRevisionV1] = []
        for package_root in sorted(
            value for value in self.root.iterdir() if value.is_dir()
        ):
            candidate = self._validate_package(package_root)
            if candidate is None:
                continue
            if package_id is None or candidate.pin.package_id == package_id:
                candidates.append(candidate)
        if not candidates:
            raise StateConflictError(
                "no accepted creator-canon Genesis revision is available"
            )
        package_ids = {value.pin.package_id for value in candidates}
        if package_id is None and len(package_ids) != 1:
            raise StateConflictError(
                "multiple accepted Genesis packages require an explicit package_id"
            )
        seen_numbers: set[int] = set()
        seen_revisions: set[str] = set()
        for candidate in candidates:
            if (
                candidate.pin.revision_number in seen_numbers
                or candidate.pin.revision_id in seen_revisions
            ):
                raise StateConflictError(
                    "accepted Genesis revision identity is ambiguous"
                )
            seen_numbers.add(candidate.pin.revision_number)
            seen_revisions.add(candidate.pin.revision_id)
        by_number = {value.pin.revision_number: value for value in candidates}
        for number, candidate in by_number.items():
            previous = by_number.get(number - 1)
            if (
                previous is not None
                and candidate.pin.parent_revision_id != previous.pin.revision_id
            ):
                raise StateConflictError("accepted Genesis parent lineage changed")
        return max(candidates, key=lambda value: value.pin.revision_number)

    def _validate_package(
        self, package_root: Path
    ) -> AcceptedGenesisRevisionV1 | None:
        assert_plain_tree(package_root, "accepted Genesis package")
        manifest_path = package_root / "manifest.json"
        if not manifest_path.is_file():
            return None
        manifest = read_json_object(manifest_path, "Genesis manifest")
        if manifest.get("package_class") != "creator_canon":
            return None
        required = {
            "schema_version",
            "package_id",
            "revision_id",
            "revision_number",
            "revision_label",
            "parent_revision_id",
            "modules",
        }
        if not required.issubset(manifest):
            raise ContractValidationError(
                "Genesis manifest lacks required authority fields"
            )
        if manifest["schema_version"] != "cera.genesis_manifest.v1":
            raise ContractValidationError("Genesis manifest schema changed")
        if (
            type(manifest["revision_number"]) is not int
            or manifest["revision_number"] < 1
        ):
            raise ContractValidationError(
                "Genesis manifest revision number is invalid"
            )
        modules = manifest["modules"]
        if not isinstance(modules, list) or not modules:
            raise ContractValidationError("Genesis manifest modules are invalid")
        declared = {"manifest.json"}
        for module in modules:
            if not isinstance(module, dict):
                raise ContractValidationError("Genesis module descriptor is invalid")
            relative = module.get("relative_path")
            expected = module.get("content_sha256")
            if (
                not isinstance(relative, str)
                or not relative
                or Path(relative).is_absolute()
                or ".." in Path(relative).parts
                or ":" in relative
                or not isinstance(expected, str)
                or SHA256_PATTERN.fullmatch(expected) is None
                or relative in declared
            ):
                raise ContractValidationError(
                    "Genesis module path or hash is invalid"
                )
            target = package_root.joinpath(*Path(relative).parts)
            if not target.is_file() or is_link_or_reparse(target):
                raise StateConflictError("declared Genesis module is unavailable")
            if sha256_bytes(target.read_bytes()) != expected:
                raise StateConflictError("declared Genesis module hash changed")
            declared.add(Path(relative).as_posix())
        actual = {
            path.relative_to(package_root).as_posix()
            for path in package_root.rglob("*")
            if path.is_file()
        }
        if actual != declared:
            raise StateConflictError(
                "accepted Genesis package contains undeclared files"
            )
        pin = GenesisRevisionPinV1(
            schema_version=GenesisRevisionPinV1.SCHEMA_VERSION,
            package_id=manifest["package_id"],
            revision_id=manifest["revision_id"],
            revision_number=manifest["revision_number"],
            revision_label=manifest["revision_label"],
            parent_revision_id=manifest.get("parent_revision_id"),
            manifest_sha256=sha256_bytes(manifest_path.read_bytes()),
            package_tree_sha256=tree_sha256(package_root),
        )
        return AcceptedGenesisRevisionV1(package_root=package_root, pin=pin)
