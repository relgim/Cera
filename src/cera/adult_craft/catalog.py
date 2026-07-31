"""Repository-local loader for immutable, compiled adult craft fragments."""

from __future__ import annotations

import json
from pathlib import Path

from cera.errors import ContractValidationError
from cera.schema import from_mapping
from cera.serialization import bytes_sha256

from .models import AdultCraftCatalogManifest, AdultCraftFragment


class AdultCraftCatalog:
    """A fully verified in-memory catalog with no external runtime dependency."""

    def __init__(
        self,
        root: Path,
        manifest: AdultCraftCatalogManifest,
        fragments: tuple[AdultCraftFragment, ...],
    ) -> None:
        self.root = root.resolve()
        self.manifest = manifest
        self.fragments = fragments
        self._by_id = {value.fragment_id: value for value in fragments}
        if len(self._by_id) != len(fragments):
            raise ContractValidationError("adult craft catalog contains duplicate fragments")

    @classmethod
    def load(cls, root: str | Path) -> "AdultCraftCatalog":
        catalog_root = Path(root).resolve()
        manifest_path = catalog_root / "manifest.json"
        manifest = from_mapping(
            AdultCraftCatalogManifest,
            _load_json_object(manifest_path),
        )
        fragments: list[AdultCraftFragment] = []
        for entry in manifest.fragment_entries:
            path = (catalog_root / entry.relative_path).resolve()
            if catalog_root not in path.parents:
                raise ContractValidationError("adult craft fragment escaped catalog root")
            fragment = from_mapping(AdultCraftFragment, _load_json_object(path))
            if fragment.fragment_id != entry.fragment_id:
                raise ContractValidationError("adult craft fragment ID differs from manifest")
            if fragment.fragment_sha256 != entry.fragment_sha256:
                raise ContractValidationError("adult craft fragment record hash differs from manifest")
            if fragment.craft_text_sha256 != entry.craft_text_sha256:
                raise ContractValidationError("adult craft text hash differs from manifest")
            fragments.append(fragment)
        return cls(catalog_root, manifest, tuple(fragments))

    def get(self, fragment_id):
        try:
            return self._by_id[fragment_id]
        except KeyError as exc:
            raise ContractValidationError("unknown adult craft fragment ID") from exc


def verify_source_integrity(
    provenance_root: str | Path,
    integrity_path: str | Path,
) -> tuple[tuple[str, str], ...]:
    """Verify the byte hashes of all immutable copied sources."""

    root = Path(provenance_root).resolve()
    integrity = _load_json_object(Path(integrity_path))
    if integrity.get("schema_version") != "cera.adult_source_integrity.v1":
        raise ContractValidationError("unknown adult source-integrity schema")
    artifacts = integrity.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 24:
        raise ContractValidationError("adult source integrity requires exactly 24 artifacts")
    verified: list[tuple[str, str]] = []
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != {"relative_path", "bytes", "sha256"}:
            raise ContractValidationError("adult source-integrity entry is malformed")
        relative = item["relative_path"]
        if not isinstance(relative, str) or relative.startswith("/") or ":" in relative:
            raise ContractValidationError("adult source-integrity path is not relative")
        path = (root / relative).resolve()
        if root not in path.parents:
            raise ContractValidationError("adult source-integrity path escaped provenance root")
        payload = path.read_bytes()
        if len(payload) != item["bytes"] or bytes_sha256(payload) != item["sha256"]:
            raise ContractValidationError(f"adult source hash mismatch: {relative}")
        verified.append((relative, item["sha256"]))
    return tuple(verified)


def _load_json_object(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"unable to load adult catalog JSON: {path.name}") from exc
    if not isinstance(payload, dict):
        raise ContractValidationError("adult catalog JSON must be an object")
    return payload
