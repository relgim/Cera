"""Fail-closed compiler for manifest-governed Genesis packages."""

from __future__ import annotations

import json
from pathlib import Path

from cera.errors import (
    ContractValidationError,
    ErrorCode,
    GenesisImportBlockedError,
)
from cera.schema import from_mapping
from cera.serialization import bytes_sha256, domain_sha256

from .models import (
    AuthorizedGenesisSource,
    CompiledGenesisRevision,
    CreatorAuthorization,
    EpistemicLayer,
    GenesisDryRunPlan,
    GenesisManifest,
    GenesisModule,
    GenesisPackageClass,
    SyntheticFixtureAuthorization,
)


class GenesisCompiler:
    def compile(
        self,
        package_root: str | Path,
        authorization: CreatorAuthorization | SyntheticFixtureAuthorization | None,
    ) -> CompiledGenesisRevision:
        root = Path(package_root).resolve()
        if not root.is_dir():
            raise GenesisImportBlockedError(
                ErrorCode.CREATOR_GENESIS_PACKAGE_UNAVAILABLE,
                "Genesis package root does not exist",
            )
        manifest_path = root / "manifest.json"
        manifest_bytes = self._read_file(manifest_path)
        manifest_sha256 = bytes_sha256(manifest_bytes)
        manifest = self._decode(GenesisManifest, manifest_bytes, "manifest.json")
        if authorization is None:
            raise GenesisImportBlockedError(
                ErrorCode.CREATOR_AUTHORITY_UNAPPROVED,
                "Genesis package has no explicit approval envelope",
            )
        if (
            manifest.package_class is GenesisPackageClass.CREATOR_CANON
            and not isinstance(authorization, CreatorAuthorization)
        ):
            raise GenesisImportBlockedError(
                ErrorCode.CREATOR_AUTHORITY_UNAPPROVED,
                "creator Genesis requires creator authorization",
            )
        if (
            manifest.package_class is GenesisPackageClass.SYNTHETIC_FIXTURE
            and not isinstance(authorization, SyntheticFixtureAuthorization)
        ):
            raise GenesisImportBlockedError(
                ErrorCode.SYNTHETIC_GENESIS_REJECTED,
                "synthetic Genesis requires an isolated fixture authorization",
            )
        if authorization.package_id != manifest.package_id:
            raise GenesisImportBlockedError(
                ErrorCode.CREATOR_AUTHORITY_UNAPPROVED,
                "authorization targets a different Genesis package",
            )
        if authorization.revision_id != manifest.revision_id:
            raise GenesisImportBlockedError(
                ErrorCode.CREATOR_AUTHORITY_UNAPPROVED,
                "authorization targets a different revision",
            )
        if authorization.manifest_sha256 != manifest_sha256:
            raise GenesisImportBlockedError(
                ErrorCode.CREATOR_AUTHORITY_UNAPPROVED,
                "manifest is not covered by package authorization",
            )

        declared_paths = {"manifest.json"}
        authorized_hashes = {
            source.source_id: source.content_sha256
            for source in authorization.authorized_sources
        }
        modules: list[GenesisModule] = []
        all_records = []
        for module_ref in manifest.modules:
            if module_ref.source_id not in authorized_hashes:
                raise ContractValidationError("module source lacks creator authorization")
            module_path = self._resolve_module(root, module_ref.relative_path)
            declared_paths.add(module_ref.relative_path)
            raw = self._read_file(module_path)
            raw_sha256 = bytes_sha256(raw)
            if raw_sha256 != module_ref.content_sha256:
                raise ContractValidationError("module hash does not match manifest")
            if raw_sha256 != authorized_hashes[module_ref.source_id]:
                raise ContractValidationError("module hash does not match authorization")
            module = self._decode(GenesisModule, raw, module_ref.relative_path)
            if module.source_id != module_ref.source_id:
                raise ContractValidationError("module source ID does not match manifest")
            if module.package_class is not manifest.package_class:
                raise ContractValidationError("module package class does not match manifest")
            modules.append(module)
            all_records.extend(module.records)

        if set(authorized_hashes) != {module.source_id for module in modules}:
            raise ContractValidationError(
                "authorization sources must exactly match declared Genesis modules"
            )

        actual_json_paths = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*.json")
            if path.is_file()
        }
        undeclared = sorted(actual_json_paths - declared_paths)
        if undeclared:
            raise ContractValidationError(
                f"Genesis package contains undeclared JSON files: {', '.join(undeclared)}"
            )
        record_ids = [str(record.record_id) for record in all_records]
        if len(record_ids) != len(set(record_ids)):
            raise ContractValidationError("Genesis revision contains duplicate record IDs")
        declared_source_ids = {module.source_id for module in modules}
        for record in all_records:
            if not set(record.source_refs).issubset(declared_source_ids):
                raise ContractValidationError("record cites a source outside the revision")
        self._check_local_supersession_cycles(all_records)
        record_by_id = {record.record_id: record for record in all_records}
        for finding in manifest.unresolved_findings:
            if not set(finding.source_refs).issubset(declared_source_ids):
                raise ContractValidationError("unresolved finding cites undeclared source")
            preserved = record_by_id.get(finding.preserved_record_id)
            if preserved is None:
                raise ContractValidationError(
                    "unresolved finding lacks its preserved unknown record"
                )
            if preserved.epistemic_layer is not EpistemicLayer.UNRESOLVED_QUESTION:
                raise ContractValidationError(
                    "unresolved finding must point to an explicit unknown record"
                )
        normalized_payload = {
            "manifest": manifest,
            "modules": tuple(modules),
            "records": tuple(all_records),
        }
        return CompiledGenesisRevision(
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            modules=tuple(modules),
            records=tuple(all_records),
            bundle_sha256=domain_sha256(
                "cera.compiled_genesis_revision.v1", normalized_payload
            ),
        )

    def dry_run(
        self,
        package_root: str | Path,
        authorization: CreatorAuthorization | SyntheticFixtureAuthorization | None,
    ) -> GenesisDryRunPlan:
        compiled = self.compile(package_root, authorization)
        source_hashes = tuple(
            AuthorizedGenesisSource(
                source_id=module.source_id,
                content_sha256=next(
                    ref.content_sha256
                    for ref in compiled.manifest.modules
                    if ref.source_id == module.source_id
                ),
            )
            for module in compiled.modules
        )
        return GenesisDryRunPlan(
            package_id=compiled.manifest.package_id,
            package_class=compiled.manifest.package_class,
            revision_id=compiled.manifest.revision_id,
            manifest_sha256=compiled.manifest_sha256,
            bundle_sha256=compiled.bundle_sha256,
            compiler_contract_version=compiled.manifest.compiler_contract_version,
            world_scope=compiled.manifest.world_scope,
            source_hashes=source_hashes,
            record_hashes=tuple(record.record_sha256 for record in compiled.records),
            unresolved_findings=compiled.manifest.unresolved_findings,
        )

    @staticmethod
    def import_generated_markdown(_: str) -> None:
        raise GenesisImportBlockedError(
            ErrorCode.CREATOR_AUTHORITY_UNAPPROVED,
            "generated Markdown is a derived view and cannot be imported",
        )

    @staticmethod
    def _read_file(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ContractValidationError(f"cannot read Genesis source: {path.name}") from exc

    @staticmethod
    def _decode(model_type, raw: bytes, label: str):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractValidationError(f"{label} must be UTF-8") from exc
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ContractValidationError(f"{label} is not valid JSON") from exc
        return from_mapping(model_type, payload)

    @staticmethod
    def _resolve_module(root: Path, relative_path: str) -> Path:
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ContractValidationError("module path escapes Genesis package") from exc
        return candidate

    @staticmethod
    def _check_local_supersession_cycles(records) -> None:
        edges = {
            record.record_id: tuple(
                target for target in record.supersedes if target in {r.record_id for r in records}
            )
            for record in records
        }

        def visit(node, active, done) -> None:
            if node in active:
                raise ContractValidationError("Genesis supersession graph contains a cycle")
            if node in done:
                return
            active.add(node)
            for target in edges.get(node, ()):
                visit(target, active, done)
            active.remove(node)
            done.add(node)

        completed = set()
        for record_id in edges:
            visit(record_id, set(), completed)
