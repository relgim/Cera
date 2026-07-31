"""Deterministic compiler input builder for creator-approved Hanezawa revisions.

Each Markdown revision and the visual JSON remain immutable provenance
artifacts. This module projects one explicitly selected revision into CERA's
typed, manifest-governed JSON records and generated read-only views; it never
treats a generated view as authority.
"""

from __future__ import annotations

from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from cera.contracts import (
    Certainty,
    EvidenceAuthority,
    KnowledgeRoute,
    TruthStatus,
    Visibility,
)
from cera.ids import IdKind, TypedId, deterministic_id
from cera.serialization import bytes_sha256, canonical_json, text_sha256

from .models import (
    AdultEligibility,
    AuthorizedGenesisSource,
    COMPILER_CONTRACT_VERSION,
    CreatorAuthorization,
    EpistemicLayer,
    GenesisManifest,
    GenesisModule,
    GenesisModuleRef,
    GenesisPackageClass,
    GenesisRecord,
    GenesisRecordType,
    GenesisUnresolvedFinding,
    StoryStartPresence,
)
from .hanezawa_source import (
    ALL_HANEZAWA_IDS,
    AUTHORIZATION_ID,
    AUTHORIZATION_SOURCE_ID,
    CHARACTER_HEADING_RE as _CHARACTER_HEADING_RE,
    CHARACTER_IDS,
    CHARACTER_NAMES,
    CORE_FILENAME,
    CREATOR_AUTHORIZATION_STATEMENT,
    EVENT_RE as _EVENT_RE,
    EXPECTED_SOURCE_HASHES,
    HUSBAND_ID,
    MEMORY_RE as _MEMORY_RE,
    PACKAGE_ID,
    PROMPT_OVERLAY_FILENAME,
    REVISION_ID,
    SOURCE_INTEGRITY_FILENAME,
    TED_ID,
    VISUAL_FILENAME,
    WORLD_ID,
    MarkdownDocument,
    MarkdownSection,
    character_section_is_private as _character_section_is_private,
    content_class as _content_class,
    first_meaningful_line as _first_meaningful_line,
    knowledge_ids as _knowledge_ids,
    limit_adult_wording as _limit_adult_wording,
    module as _module,
    objective_record as _base_objective_record,
    parse_labeled_bullets as _parse_labeled_bullets,
    parse_table as _parse_table,
    plain_bullets as _plain_bullets,
    pretty_json_bytes as _pretty_json_bytes,
    section_payload as _base_section_payload,
    slug as _slug,
    source_ref as _base_source_ref,
    subject_ids as _subject_ids,
    unique_ids as _unique_ids,
    validate_overlay as _validate_overlay,
)


V1_2_CORE_FILENAME = "HANEZAWA_CORE_GENESIS_V1_2.md"
V1_2_SOURCE_INTEGRITY_FILENAME = "SOURCE_INTEGRITY_V1_2.json"
V1_2_EXPECTED_SOURCE_HASHES = {
    V1_2_CORE_FILENAME: "76b43c58f6e2c2c902a8af49027c3f9356d6f45078f8b88bec9ba39b11f9fd4a",
    VISUAL_FILENAME: EXPECTED_SOURCE_HASHES[VISUAL_FILENAME],
    PROMPT_OVERLAY_FILENAME: EXPECTED_SOURCE_HASHES[PROMPT_OVERLAY_FILENAME],
}
V1_2_CREATOR_AUTHORIZATION_STATEMENT = (
    "Please do this when you are completely finished with your current task. "
    "I give you permission, continue, then work on hanezawa files afterwards."
)


@dataclass(frozen=True, slots=True)
class HanezawaBuildSpec:
    version: str
    core_filename: str
    expected_source_hashes: dict[str, str]
    authorization_statement: str
    revision_id: TypedId
    authorization_id: TypedId
    authorization_source_id: TypedId
    revision_number: int
    parent_revision_id: TypedId | None
    revision_label: str
    module_namespace: str
    record_namespace: str
    finding_namespace: str
    package_directory: str
    authorization_filename: str
    generated_directory: str
    source_integrity_filename: str
    expected_event_count: int
    expected_memory_counts: dict[str, int]
    builder_version: str


V1_1_BUILD_SPEC = HanezawaBuildSpec(
    version="v1_1",
    core_filename=CORE_FILENAME,
    expected_source_hashes=dict(EXPECTED_SOURCE_HASHES),
    authorization_statement=CREATOR_AUTHORIZATION_STATEMENT,
    revision_id=REVISION_ID,
    authorization_id=AUTHORIZATION_ID,
    authorization_source_id=AUTHORIZATION_SOURCE_ID,
    revision_number=1,
    parent_revision_id=None,
    revision_label="Hanezawa Core Genesis V1.1",
    module_namespace="cera.genesis.hanezawa.v1.1.module",
    record_namespace="cera.genesis.hanezawa.v1.1.record",
    finding_namespace="cera.genesis.hanezawa.v1.1.finding",
    package_directory="hanezawa_core_v1_1",
    authorization_filename="hanezawa_core_v1_1.json",
    generated_directory="hanezawa_core_v1_1",
    source_integrity_filename=SOURCE_INTEGRITY_FILENAME,
    expected_event_count=72,
    expected_memory_counts={name: 10 for name in CHARACTER_NAMES},
    builder_version="cera.hanezawa_builder.v1",
)

V1_2_REVISION_ID = deterministic_id(
    IdKind.GENESIS_REVISION,
    "cera.genesis.revision",
    "hanezawa-core-v1.2",
)
V1_2_AUTHORIZATION_ID = deterministic_id(
    IdKind.AUTHORIZATION,
    "cera.genesis.authorization",
    "hanezawa-core-v1.2",
)
V1_2_AUTHORIZATION_SOURCE_ID = deterministic_id(
    IdKind.SOURCE,
    "cera.creator.conversation",
    "hanezawa-core-v1.2-install",
)
V1_2_BUILD_SPEC = HanezawaBuildSpec(
    version="v1_2",
    core_filename=V1_2_CORE_FILENAME,
    expected_source_hashes=V1_2_EXPECTED_SOURCE_HASHES,
    authorization_statement=V1_2_CREATOR_AUTHORIZATION_STATEMENT,
    revision_id=V1_2_REVISION_ID,
    authorization_id=V1_2_AUTHORIZATION_ID,
    authorization_source_id=V1_2_AUTHORIZATION_SOURCE_ID,
    revision_number=2,
    parent_revision_id=REVISION_ID,
    revision_label="Hanezawa Core Genesis V1.2",
    module_namespace="cera.genesis.hanezawa.v1.2.module",
    record_namespace="cera.genesis.hanezawa.v1.2.record",
    finding_namespace="cera.genesis.hanezawa.v1.2.finding",
    package_directory="hanezawa_core_v1_2",
    authorization_filename="hanezawa_core_v1_2.json",
    generated_directory="hanezawa_core_v1_2",
    source_integrity_filename=V1_2_SOURCE_INTEGRITY_FILENAME,
    expected_event_count=79,
    expected_memory_counts={
        "Hana": 13,
        "Sakura": 12,
        "Mia": 12,
        "Enne": 12,
        "Tomi": 12,
        "Aoi": 11,
        "Yuuni": 12,
    },
    builder_version="cera.hanezawa_builder.v2",
)


@dataclass(frozen=True, slots=True)
class _BuildContext:
    spec: HanezawaBuildSpec
    parent_records: dict[TypedId, GenesisRecord]


_ACTIVE_BUILD: ContextVar[_BuildContext] = ContextVar(
    "cera_hanezawa_active_build",
    default=_BuildContext(V1_1_BUILD_SPEC, {}),
)


def _context() -> _BuildContext:
    return _ACTIVE_BUILD.get()


def _spec() -> HanezawaBuildSpec:
    return _context().spec


def _module_source_id(name: str) -> TypedId:
    return deterministic_id(IdKind.SOURCE, _spec().module_namespace, name)


def _record_id(key: str) -> TypedId:
    return deterministic_id(IdKind.RECORD, _spec().record_namespace, key)


def _parent_record(key: str) -> GenesisRecord | None:
    parent_revision = _spec().parent_revision_id
    if parent_revision is None:
        return None
    parent_id = deterministic_id(
        IdKind.RECORD,
        V1_1_BUILD_SPEC.record_namespace,
        key,
    )
    return _context().parent_records.get(parent_id)


def _versioning(
    key: str,
    *,
    record_version: int = 1,
    supersedes: tuple[TypedId, ...] = (),
) -> tuple[int, tuple[TypedId, ...]]:
    parent = _parent_record(key)
    if parent is None:
        return record_version, supersedes
    return (
        max(record_version, parent.record_version + 1),
        tuple(dict.fromkeys((parent.record_id, *supersedes))),
    )


def _core_sha256(source_hashes: dict[str, str]) -> str:
    return source_hashes[_spec().core_filename]


def _source_ref(section: MarkdownSection, core_sha256: str) -> dict[str, Any]:
    return _base_source_ref(
        section,
        core_sha256,
        core_filename=_spec().core_filename,
    )


def _section_payload(section: MarkdownSection, core_sha256: str) -> dict[str, Any]:
    return _base_section_payload(
        section,
        core_sha256,
        core_filename=_spec().core_filename,
    )


def _objective_record(*args, **kwargs) -> GenesisRecord:
    key = args[1] if len(args) > 1 else kwargs["key"]
    record_version, supersedes = _versioning(
        key,
        record_version=kwargs.pop("record_version", 1),
        supersedes=kwargs.pop("supersedes", ()),
    )
    return _base_objective_record(
        *args,
        **kwargs,
        record_version=record_version,
        supersedes=supersedes,
        record_id_factory=_record_id,
    )


@dataclass(frozen=True, slots=True)
class BuiltGenesis:
    package_root: Path
    authorization_path: Path
    source_integrity_path: Path
    view_root: Path
    manifest: GenesisManifest
    authorization: CreatorAuthorization
    modules: tuple[GenesisModule, ...]


def build_hanezawa_genesis(
    source_root: str | Path,
    package_root: str | Path,
    authorization_path: str | Path,
    view_root: str | Path,
    source_integrity_path: str | Path,
) -> BuiltGenesis:
    """Build V1.1 without changing its historical byte-level projection."""

    return _build_hanezawa_genesis(
        V1_1_BUILD_SPEC,
        source_root,
        package_root,
        authorization_path,
        view_root,
        source_integrity_path,
    )


def build_hanezawa_genesis_v1_2(
    source_root: str | Path,
    package_root: str | Path,
    authorization_path: str | Path,
    view_root: str | Path,
    source_integrity_path: str | Path,
) -> BuiltGenesis:
    """Build the complete V1.2 child revision without mutating V1.1."""

    return _build_hanezawa_genesis(
        V1_2_BUILD_SPEC,
        source_root,
        package_root,
        authorization_path,
        view_root,
        source_integrity_path,
    )


def _build_hanezawa_genesis(
    spec: HanezawaBuildSpec,
    source_root: str | Path,
    package_root: str | Path,
    authorization_path: str | Path,
    view_root: str | Path,
    source_integrity_path: str | Path,
) -> BuiltGenesis:
    source_root = Path(source_root).resolve()
    package_root = Path(package_root).resolve()
    authorization_path = Path(authorization_path).resolve()
    view_root = Path(view_root).resolve()
    source_integrity_path = Path(source_integrity_path).resolve()
    _require_new_target(package_root, "package_root")
    _require_new_target(view_root, "view_root")
    if authorization_path.exists():
        raise FileExistsError(f"authorization output already exists: {authorization_path}")
    if source_integrity_path.exists():
        raise FileExistsError(f"source integrity output already exists: {source_integrity_path}")

    core_path = source_root / spec.core_filename
    visual_path = source_root / VISUAL_FILENAME
    overlay_path = source_root / PROMPT_OVERLAY_FILENAME
    for path in (core_path, visual_path, overlay_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    source_hashes = {
        path.name: bytes_sha256(path.read_bytes())
        for path in (core_path, visual_path, overlay_path)
    }
    for name, expected in spec.expected_source_hashes.items():
        if source_hashes.get(name) != expected:
            raise ValueError(f"source hash mismatch for {name}")

    visual = json.loads(visual_path.read_text(encoding="utf-8"))
    overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
    _validate_overlay(overlay)
    parent_records = (
        _build_parent_record_map(source_root, visual, overlay)
        if spec.parent_revision_id is not None
        else {}
    )
    token = _ACTIVE_BUILD.set(_BuildContext(spec, parent_records))
    try:
        document = MarkdownDocument(core_path.read_text(encoding="utf-8"))
        modules = _build_modules(document, visual, overlay, source_hashes)
        if parent_records:
            modules = _complete_parent_supersession(modules, parent_records)

        package_root.mkdir(parents=True)
        module_refs: list[GenesisModuleRef] = []
        for relative_path, module in modules:
            target = package_root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = _pretty_json_bytes(module)
            target.write_bytes(raw)
            module_refs.append(
                GenesisModuleRef(
                    source_id=module.source_id,
                    relative_path=relative_path,
                    content_sha256=bytes_sha256(raw),
                )
            )

        unknown_records = tuple(
            record
            for _, module in modules
            for record in module.records
            if record.epistemic_layer is EpistemicLayer.UNRESOLVED_QUESTION
        )
        ledger_source_id = next(
            module.source_id
            for path, module in modules
            if path == "modules/ledger.json"
        )
        findings = tuple(
            GenesisUnresolvedFinding(
                finding_id=deterministic_id(
                    IdKind.GENESIS_FINDING,
                    spec.finding_namespace,
                    str(record.record_id),
                ),
                description=record.claim,
                source_refs=(ledger_source_id,),
                preserved_record_id=record.record_id,
            )
            for record in unknown_records
        )
        manifest = GenesisManifest(
            schema_version=GenesisManifest.SCHEMA_VERSION,
            package_id=PACKAGE_ID,
            package_class=GenesisPackageClass.CREATOR_CANON,
            revision_id=spec.revision_id,
            revision_number=spec.revision_number,
            parent_revision_id=spec.parent_revision_id,
            modules=tuple(module_refs),
            unresolved_findings=findings,
            compiler_contract_version=COMPILER_CONTRACT_VERSION,
            world_scope="creator_worlds",
            revision_label=spec.revision_label,
        )
        manifest_raw = _pretty_json_bytes(manifest)
        (package_root / "manifest.json").write_bytes(manifest_raw)
        manifest_sha256 = bytes_sha256(manifest_raw)

        authorization = CreatorAuthorization(
            schema_version=CreatorAuthorization.SCHEMA_VERSION,
            authorization_id=spec.authorization_id,
            package_id=PACKAGE_ID,
            revision_id=spec.revision_id,
            manifest_sha256=manifest_sha256,
            authorized_sources=tuple(
                AuthorizedGenesisSource(ref.source_id, ref.content_sha256)
                for ref in module_refs
            ),
            authorization_source_id=spec.authorization_source_id,
            authorization_statement_sha256=text_sha256(
                spec.authorization_statement
            ),
            scope="install_genesis_revision",
            authorized_by="creator",
        )
        authorization_path.parent.mkdir(parents=True, exist_ok=True)
        authorization_path.write_bytes(_pretty_json_bytes(authorization))

        source_integrity = {
            "schema_version": "cera.genesis_source_integrity.v1",
            "status": "immutable_creator_sources",
            "authorization_id": str(spec.authorization_id),
            "authorization_statement": spec.authorization_statement,
            "authorization_statement_sha256": text_sha256(
                spec.authorization_statement
            ),
            "artifacts": [
                {
                    "path": name,
                    "sha256": source_hashes[name],
                    "bytes": (source_root / name).stat().st_size,
                    "role": (
                        "creator_core_source"
                        if name == spec.core_filename
                        else "creator_visual_source"
                        if name == VISUAL_FILENAME
                        else "creator_prompt_policy_overlay"
                    ),
                }
                for name in (
                    spec.core_filename,
                    VISUAL_FILENAME,
                    PROMPT_OVERLAY_FILENAME,
                )
            ],
            "compiled_package": spec.package_directory,
            "manifest_sha256": manifest_sha256,
            "runtime_authority": "manifest_governed_compiled_json",
            "generated_views_are_authority": False,
        }
        if spec.parent_revision_id is not None:
            source_integrity["parent_revision_id"] = str(spec.parent_revision_id)
        source_integrity_path.write_bytes(_pretty_json_bytes(source_integrity))
        _write_views(
            view_root,
            manifest,
            tuple(module for _, module in modules),
            source_hashes,
        )
        return BuiltGenesis(
            package_root=package_root,
            authorization_path=authorization_path,
            source_integrity_path=source_integrity_path,
            view_root=view_root,
            manifest=manifest,
            authorization=authorization,
            modules=tuple(module for _, module in modules),
        )
    finally:
        _ACTIVE_BUILD.reset(token)


def _build_parent_record_map(
    source_root: Path,
    visual: dict[str, Any],
    overlay: dict[str, Any],
) -> dict[TypedId, GenesisRecord]:
    core_path = source_root / V1_1_BUILD_SPEC.core_filename
    if not core_path.is_file():
        raise FileNotFoundError(
            "V1.2 compilation requires the immutable V1.1 parent source"
        )
    parent_hashes = {
        name: bytes_sha256((source_root / name).read_bytes())
        for name in (
            V1_1_BUILD_SPEC.core_filename,
            VISUAL_FILENAME,
            PROMPT_OVERLAY_FILENAME,
        )
    }
    for name, expected in V1_1_BUILD_SPEC.expected_source_hashes.items():
        if parent_hashes.get(name) != expected:
            raise ValueError(f"V1.1 parent source hash mismatch for {name}")
    token = _ACTIVE_BUILD.set(_BuildContext(V1_1_BUILD_SPEC, {}))
    try:
        modules = _build_modules(
            MarkdownDocument(core_path.read_text(encoding="utf-8")),
            visual,
            overlay,
            parent_hashes,
        )
    finally:
        _ACTIVE_BUILD.reset(token)
    records = tuple(
        record
        for _, module in modules
        for record in module.records
    )
    superseded = {
        record_id
        for record in records
        for record_id in record.supersedes
    }
    return {
        record.record_id: record
        for record in records
        if record.record_id not in superseded
    }


def _complete_parent_supersession(
    modules: tuple[tuple[str, GenesisModule], ...],
    parent_records: dict[TypedId, GenesisRecord],
) -> tuple[tuple[str, GenesisModule], ...]:
    already_superseded = {
        record_id
        for _, module in modules
        for record in module.records
        for record_id in record.supersedes
    }
    unmatched = tuple(
        record
        for record_id, record in sorted(
            parent_records.items(),
            key=lambda item: str(item[0]),
        )
        if record_id not in already_superseded
    )
    if not unmatched:
        return modules
    ledger_index = next(
        index
        for index, (path, _) in enumerate(modules)
        if path == "modules/ledger.json"
    )
    path, ledger = modules[ledger_index]
    replacement = _base_objective_record(
        ledger.source_id,
        "revision:v1_2-complete-parent-replacement",
        GenesisRecordType.SUPERSESSION_LEDGER,
        "Hanezawa Core Genesis V1.2 is a complete child snapshot; unmatched V1.1 compiled records are retired rather than inherited.",
        (WORLD_ID,),
        {
            "parent_revision_id": str(V1_1_BUILD_SPEC.revision_id),
            "replacement_revision_id": str(V1_2_BUILD_SPEC.revision_id),
            "retired_record_ids": [str(record.record_id) for record in unmatched],
            "reason": "complete_creator_authorized_revision_replacement",
        },
        tags=("supersession", "v1_2", "complete_parent_replacement"),
        visibility=Visibility.SYSTEM_PRIVATE,
        supersedes=tuple(record.record_id for record in unmatched),
        record_version=max(record.record_version for record in unmatched) + 1,
        record_id_factory=_record_id,
    )
    revised_ledger = GenesisModule(
        schema_version=ledger.schema_version,
        source_id=ledger.source_id,
        package_class=ledger.package_class,
        title=ledger.title,
        records=tuple((*ledger.records, replacement)),
    )
    output = list(modules)
    output[ledger_index] = (path, revised_ledger)
    return tuple(output)


def _build_modules(
    document: MarkdownDocument,
    visual: dict[str, Any],
    overlay: dict[str, Any],
    source_hashes: dict[str, str],
) -> tuple[tuple[str, GenesisModule], ...]:
    modules: list[tuple[str, GenesisModule]] = []
    modules.append(("modules/household.json", _household_module(document, source_hashes)))
    modules.extend(_character_modules(document, visual, source_hashes))
    modules.append(
        ("modules/relationships_family.json", _family_relationship_module(document, source_hashes))
    )
    modules.append(("modules/ted_boundary.json", _ted_module(document, source_hashes)))
    modules.append(("modules/events.json", _event_module(document, source_hashes)))
    modules.extend(_memory_modules(document, source_hashes))
    modules.append(("modules/visual.json", _visual_module(visual, overlay, source_hashes)))
    if _spec().version == "v1_2":
        modules.append(
            (
                "modules/character_expression.json",
                _character_expression_module(document, source_hashes),
            )
        )
    modules.append(("modules/frameworks.json", _framework_module(document, source_hashes)))
    modules.append(("modules/ledger.json", _ledger_module(document, overlay, source_hashes)))
    return tuple(modules)


def _household_module(
    document: MarkdownDocument, source_hashes: dict[str, str]
) -> GenesisModule:
    source_id = _module_source_id("household")
    records: list[GenesisRecord] = []
    for top_prefix in ("2.", "3.", "4.", "13."):
        top_level = 1 if top_prefix == "13." else 2
        child_level = 2 if top_prefix == "13." else 3
        top = document.first(top_prefix, level=top_level)
        for section in document.children(top, level=child_level):
            record_type = (
                GenesisRecordType.HOUSEHOLD_RULE
                if top_prefix == "3."
                else GenesisRecordType.WORLD
            )
            # Section 4 is a system-level truth/knowledge model. Its subsections
            # deliberately juxtapose objective facts with unequal character
            # knowledge, so exposing the raw section as public evidence would
            # collapse those boundaries. Character-visible projections come
            # from the separately typed event, memory, and belief records.
            system_private = (
                top_prefix == "4."
                or "surveillance" in section.title.casefold()
                or "knowledge" in section.title.casefold()
            )
            records.append(
                _objective_record(
                    source_id,
                    f"household:{section.title}",
                    record_type,
                    _first_meaningful_line(section.body, section.title),
                    (WORLD_ID,),
                    _section_payload(section, _core_sha256(source_hashes)),
                    tags=("household", _slug(section.title)),
                    visibility=Visibility.SYSTEM_PRIVATE if system_private else Visibility.PUBLIC,
                    knowledge_owner_ids=ALL_HANEZAWA_IDS if not system_private else (),
                )
            )
    story_start = document.first("4.5 ", level=3)
    for name, character_id in CHARACTER_IDS.items():
        records.append(
            _objective_record(
                source_id,
                f"story-start:{name}",
                GenesisRecordType.STORY_START_PLACEMENT,
                f"{name} Hanezawa is present in the Hanezawa household at story start.",
                (character_id, WORLD_ID),
                {
                    **_section_payload(story_start, _core_sha256(source_hashes)),
                    "character_id": str(character_id),
                    "presence": "present",
                },
                tags=("story_start", name.casefold(), "present"),
                visibility=Visibility.SYSTEM_PRIVATE,
                story_start_presence=StoryStartPresence.PRESENT,
            )
        )
    return _module(source_id, "Hanezawa household, rules, and story start", records)


def _character_modules(
    document: MarkdownDocument,
    visual: dict[str, Any],
    source_hashes: dict[str, str],
) -> tuple[tuple[str, GenesisModule], ...]:
    character_parent = document.first("5. Character dossiers", level=1)
    visual_by_name = {
        item["display_name"].split()[0]: item
        for item in visual["characters"].values()
    }
    output: list[tuple[str, GenesisModule]] = []
    for heading in document.children(character_parent, level=2):
        match = _CHARACTER_HEADING_RE.match(heading.title)
        if not match:
            continue
        name = match.group(2)
        character_id = CHARACTER_IDS[name]
        source_id = _module_source_id(f"character-{name.casefold()}")
        subsections = document.children(heading, level=3)
        records: list[GenesisRecord] = []
        records.append(
            _objective_record(
                source_id,
                f"character-profile:{name}",
                GenesisRecordType.CHARACTER_PROFILE,
                heading.title,
                (character_id,),
                {
                    "source": _source_ref(heading, _core_sha256(source_hashes)),
                    "section_headings": [section.title for section in subsections],
                    "character_id": str(character_id),
                },
                tags=("character_profile", name.casefold()),
                visibility=Visibility.SYSTEM_PRIVATE,
            )
        )
        for section in subsections:
            if section.title == "Seven deadly sins":
                rows = _parse_table(section.body)
                if len(rows) != 7:
                    raise ValueError(f"{name} must have exactly seven sin rows")
                for row in rows:
                    sin = row[0]
                    records.append(
                        _objective_record(
                            source_id,
                            f"sin:{name}:{sin}",
                            GenesisRecordType.CHARACTER_STATE,
                            f"{name}'s {sin} lens: {row[1]}",
                            (character_id,),
                            {
                                "sin": sin,
                                "baseline": row[1],
                                "healthy_expression": row[2],
                                "unhealthy_expression": row[3],
                                "triggers_and_rationalization": row[4],
                                "counterforces": row[5],
                                "source": _source_ref(section, _core_sha256(source_hashes)),
                            },
                            tags=("seven_deadly_sins", name.casefold(), sin.casefold()),
                            visibility=Visibility.SYSTEM_PRIVATE,
                            content_class=_content_class(" ".join(row)),
                        )
                    )
                continue
            record_type = (
                GenesisRecordType.IDENTITY
                if section.title in {"Identity and function", "Physical profile and stable visual identity"}
                else GenesisRecordType.VOICE_PROFILE
                if section.title == "Speech system"
                else GenesisRecordType.CHARACTER_STATE
            )
            private = _character_section_is_private(section.title)
            records.append(
                _character_section_record(
                    source_id,
                    character_id,
                    name,
                    section,
                    record_type,
                    _core_sha256(source_hashes),
                    private,
                )
            )
        age = int(visual_by_name[name]["age"])
        records.append(
            _objective_record(
                source_id,
                f"adult-eligibility:{name}",
                GenesisRecordType.ADULT_ELIGIBILITY,
                f"{name} Hanezawa is creator-confirmed age {age} and identity-eligible for adult routing; scene consent and capacity remain separate.",
                (character_id,),
                {"age": age, "identity_eligible": True, "consent_pre_authorized": False},
                tags=("adult_eligibility", name.casefold(), f"age_{age}"),
                visibility=Visibility.SYSTEM_PRIVATE,
                adult_eligibility=AdultEligibility.CONFIRMED_IDENTITY_ELIGIBLE,
            )
        )
        output.append(
            (
                f"modules/characters/{name.casefold()}.json",
                _module(source_id, f"{name} Hanezawa character module", records),
            )
        )
    if len(output) != 7:
        raise ValueError("expected seven Hanezawa character modules")
    return tuple(output)


def _family_relationship_module(
    document: MarkdownDocument, source_hashes: dict[str, str]
) -> GenesisModule:
    parent = document.first("6. Complete directional", level=1)
    source_id = _module_source_id("relationships-family")
    records: list[GenesisRecord] = []
    for section in document.children(parent, level=3):
        if "→" not in section.title:
            continue
        left, right = (part.strip() for part in section.title.split("→", 1))
        if left not in CHARACTER_IDS or right not in CHARACTER_IDS:
            continue
        fields = _parse_labeled_bullets(section.body)
        records.append(
            _relationship_record(
                source_id,
                left,
                right,
                fields.get("Core bond", _first_meaningful_line(section.body, section.title)),
                fields,
                section,
                _core_sha256(source_hashes),
            )
        )
    if len(records) != 42:
        raise ValueError(f"expected 42 family relationship records, found {len(records)}")
    return _module(source_id, "Directional Hanezawa family relationships", records)


def _ted_module(
    document: MarkdownDocument, source_hashes: dict[str, str]
) -> GenesisModule:
    source_id = _module_source_id("ted-boundary")
    boundary = document.first("1.5 Ted's protected authority", level=3)
    records: list[GenesisRecord] = [
        _objective_record(
            source_id,
            "ted:protected-authority",
            GenesisRecordType.CREATOR_PREFERENCE,
            _first_meaningful_line(boundary.body, boundary.title),
            (TED_ID, WORLD_ID),
            _section_payload(boundary, _core_sha256(source_hashes)),
            tags=("ted", "protected_user", "authority_boundary"),
            visibility=Visibility.SYSTEM_PRIVATE,
            epistemic_layer=EpistemicLayer.CREATOR_PREFERENCE,
        )
    ]
    parent = document.first("7. Initial Hanezawa-to-Ted", level=1)
    records.append(
        _objective_record(
            source_id,
            "ted:relationship-model-contract",
            GenesisRecordType.CREATOR_PREFERENCE,
            _first_meaningful_line(parent.body, parent.title),
            (TED_ID, WORLD_ID),
            _section_payload(parent, _core_sha256(source_hashes)),
            tags=("ted", "user_authored", "relationship_model_contract"),
            visibility=Visibility.SYSTEM_PRIVATE,
            epistemic_layer=EpistemicLayer.CREATOR_PREFERENCE,
        )
    )
    for section in document.children(parent, level=2):
        match = re.match(r"^7\.\d\s+([A-Za-z]+)\s+→\s+Ted$", section.title)
        if not match:
            continue
        name = match.group(1)
        fields = _parse_labeled_bullets(section.body)
        records.append(
            _relationship_record(
                source_id,
                name,
                "Ted",
                fields.get("Emotional stance", fields.get("Belief", section.title)),
                fields,
                section,
                _core_sha256(source_hashes),
            )
        )
    if len(records) != 9:
        raise ValueError(
            "expected two Ted authority contracts plus seven relationships, "
            f"found {len(records)}"
        )
    return _module(source_id, "Ted protected boundary and starting relationship models", records)


def _event_module(
    document: MarkdownDocument, source_hashes: dict[str, str]
) -> GenesisModule:
    source_id = _module_source_id("events")
    parent = document.first("8. Atomized anchor-event ledger", level=1)
    records: list[GenesisRecord] = [
        _objective_record(
            source_id,
            "events:ledger-contract",
            GenesisRecordType.CREATOR_PREFERENCE,
            _first_meaningful_line(parent.body, parent.title),
            (WORLD_ID,),
            _section_payload(parent, _core_sha256(source_hashes)),
            tags=("formative_event", "ledger_contract", "no_future_plot"),
            visibility=Visibility.SYSTEM_PRIVATE,
            epistemic_layer=EpistemicLayer.CREATOR_PREFERENCE,
        )
    ]
    event_sections = list(document.children(parent, level=3))
    if _spec().version == "v1_2":
        additions = document.first("16.12 Stable additional event records", level=2)
        event_sections.extend(document.children(additions, level=3))
    event_count = 0
    for section in event_sections:
        match = _EVENT_RE.match(section.title)
        if not match:
            continue
        event_id, title = match.groups()
        fields = _parse_labeled_bullets(section.body)
        claim = fields.get("Objective event", title)
        participant_text = fields.get("Participants and material scope", "")
        subjects = _subject_ids(participant_text) or (WORLD_ID,)
        knowledge_text = fields.get("Knowledge distribution", "")
        knowledge = _knowledge_ids(knowledge_text)
        if not knowledge and any(
            token in knowledge_text.casefold()
            for token in ("only participants", "only these", "participants know")
        ):
            knowledge = tuple(
                subject for subject in subjects if subject.kind is IdKind.CHARACTER
            )
        records.append(
            _objective_record(
                source_id,
                f"event:{event_id}",
                GenesisRecordType.FORMATIVE_EVENT,
                claim,
                subjects,
                {
                    "event_id": event_id,
                    "title": title,
                    "fields": fields,
                    "source": _source_ref(section, _core_sha256(source_hashes)),
                },
                tags=("formative_event", event_id.casefold(), _slug(title)),
                visibility=Visibility.SHARED,
                knowledge_owner_ids=knowledge,
                content_class=_content_class(section.body),
            )
        )
        event_count += 1
    if event_count != _spec().expected_event_count:
        raise ValueError(
            f"expected {_spec().expected_event_count} events, found {event_count}"
        )
    return _module(source_id, "Atomized pre-story anchor events", records)


def _memory_modules(
    document: MarkdownDocument, source_hashes: dict[str, str]
) -> tuple[tuple[str, GenesisModule], ...]:
    parent = document.first("9. Owner-specific Genesis memory seeds", level=1)
    grouped: dict[str, list[GenesisRecord]] = {name: [] for name in CHARACTER_NAMES}
    code_owner = {"H": "Hana", "S": "Sakura", "M": "Mia", "E": "Enne", "T": "Tomi", "A": "Aoi", "Y": "Yuuni"}
    memory_sections = list(document.children(parent, level=3))
    if _spec().version == "v1_2":
        additions = document.first(
            "16.13 Stable additional owner-memory records",
            level=2,
        )
        memory_sections.extend(document.children(additions, level=3))
    for section in memory_sections:
        match = _MEMORY_RE.match(section.title)
        if not match:
            continue
        memory_id, title = match.groups()
        owner_name = code_owner[memory_id[0]]
        owner_id = CHARACTER_IDS[owner_name]
        source_id = _module_source_id(f"memory-{owner_name.casefold()}")
        fields = _parse_labeled_bullets(section.body)
        claim = fields.get("Remembered content", title)
        subjects = _unique_ids((owner_id,) + _subject_ids(section.body))
        key = f"memory:{memory_id}"
        record_version, supersedes = _versioning(key)
        grouped[owner_name].append(
            GenesisRecord(
                schema_version=GenesisRecord.SCHEMA_VERSION,
                record_id=_record_id(key),
                record_version=record_version,
                record_type=GenesisRecordType.MEMORY_SEED,
                epistemic_layer=EpistemicLayer.CONSCIOUS_BELIEF,
                truth_status=TruthStatus.CHARACTER_OWNED,
                claim=claim,
                authority=EvidenceAuthority.CREATOR,
                subject_ids=subjects,
                owner_id=owner_id,
                knowledge_owner_ids=(owner_id,),
                visibility=Visibility.OWNER_PRIVATE,
                knowledge_route=KnowledgeRoute.CREATOR_SEED,
                certainty=Certainty.BELIEVED,
                content_class=_content_class(section.body),
                adult_eligibility=AdultEligibility.NOT_APPLICABLE,
                story_start_presence=StoryStartPresence.NOT_APPLICABLE,
                relationship_from_id=None,
                relationship_to_id=None,
                source_refs=(source_id,),
                valid_from=None,
                valid_to=None,
                supersedes=supersedes,
                tags=("genesis_memory", owner_name.casefold(), memory_id.casefold()),
                expandable_sections=tuple(_slug(key) for key in fields),
                payload_json=canonical_json(
                    {
                        "memory_id": memory_id,
                        "title": title,
                        "owner_id": str(owner_id),
                        "fields": fields,
                        "source": _source_ref(section, _core_sha256(source_hashes)),
                    }
                ),
            )
        )
    output: list[tuple[str, GenesisModule]] = []
    for name in CHARACTER_NAMES:
        records = grouped[name]
        expected = _spec().expected_memory_counts[name]
        if len(records) != expected:
            raise ValueError(
                f"expected {expected} {name} memories, found {len(records)}"
            )
        source_id = _module_source_id(f"memory-{name.casefold()}")
        output.append(
            (
                f"modules/memories/{name.casefold()}.json",
                _module(source_id, f"{name} owner-private Genesis memories", records),
            )
        )
    return tuple(output)


def _visual_module(
    visual: dict[str, Any],
    overlay: dict[str, Any],
    source_hashes: dict[str, str],
) -> GenesisModule:
    source_id = _module_source_id("visual")
    records: list[GenesisRecord] = []
    for raw in visual["characters"].values():
        name = raw["display_name"].split()[0]
        character_id = CHARACTER_IDS[name]
        source_anima = raw["anima"]
        runtime_anchor = _limit_adult_wording(source_anima["natural_language_identity_anchor"])
        ordinary_addendum = ""
        protected_addendum = ""
        if name == "Yuuni":
            ordinary_addendum = overlay["age_language_policy"]["yuuni_ordinary_visual"]
            protected_addendum = overlay["age_language_policy"]["yuuni_protected_visual"]
        stable_visual = {key: value for key, value in raw.items() if key != "anima"}
        payload = {
            "character_id": str(character_id),
            "stable_visual": stable_visual,
            "runtime_prompt_projection": {
                "identity_anchor": runtime_anchor,
                "ordinary_visual_addendum": ordinary_addendum,
                "protected_visual_addendum": protected_addendum,
                "positive_prefix_policy_record": "anima_prompt_policy_v1",
                "negative_prompt_policy_record": "anima_prompt_policy_v1",
                "append_identity_drift_negatives": False,
            },
            "source_anima_sha256": text_sha256(canonical_json(source_anima)),
            "source": {
                "artifact": VISUAL_FILENAME,
                "sha256": source_hashes[VISUAL_FILENAME],
                "json_pointer": f"/characters/{raw['character_id'].split('.')[-1]}",
            },
        }
        records.append(
            _objective_record(
                source_id,
                f"visual:{name}",
                GenesisRecordType.VISUAL_CANON,
                f"Stable visual identity and wardrobe authority for {name} Hanezawa.",
                (character_id,),
                payload,
                tags=("visual_canon", "anima", name.casefold()),
                visibility=Visibility.SYSTEM_PRIVATE,
            )
        )
    records.append(
        _objective_record(
            source_id,
            "visual:prompt-policy",
            GenesisRecordType.CREATOR_PREFERENCE,
            "Use the creator-authorized Anima positive prefix and exact negative prompt without appending source drift negatives.",
            (WORLD_ID,),
            {
                "policy_id": "anima_prompt_policy_v1",
                "positive_prefix": overlay["positive_prefix"],
                "negative_prompt": overlay["negative_prompt"],
                "negative_prompt_is_exact": True,
                "append_identity_drift_negatives": False,
                "age_language_policy": overlay["age_language_policy"],
                "runtime_boundary": overlay["runtime_boundary"],
                "source": {
                    "artifact": PROMPT_OVERLAY_FILENAME,
                    "sha256": source_hashes[PROMPT_OVERLAY_FILENAME],
                },
            },
            tags=("creator_preference", "visual_prompt", "anima"),
            visibility=Visibility.SYSTEM_PRIVATE,
            epistemic_layer=EpistemicLayer.CREATOR_PREFERENCE,
        )
    )
    return _module(source_id, "Anima visual canon and creator prompt policy", records)


_EXPRESSION_MODE_NAMES = {
    "Sexual refusal": "sexual_refusal",
    "General refusal": "general_refusal",
    "Defending her own dignity": "self_dignity_defense",
    "Defending Mother or a daughter": "family_defense",
    "Defending Mother or a sister": "family_defense",
    "Caught off guard": "caught_off_guard",
    "Extremely angry": "extreme_anger",
    "Deeply disappointed": "deep_disappointment",
    "Trying to morally correct": "moral_correction",
    "Latent-M identity preservation while still refusing": (
        "latent_m_identity_preservation"
    ),
    "Partial trust fracture": "partial_trust_fracture",
    "Complete trust destruction": "complete_trust_destruction",
}


def _character_expression_module(
    document: MarkdownDocument,
    source_hashes: dict[str, str],
) -> GenesisModule:
    """Compile V1.2 identity and speech evidence into selectively fetchable records."""

    source_id = _module_source_id("character-expression")
    core_sha256 = _core_sha256(source_hashes)
    records: list[GenesisRecord] = []

    semantic_separations = document.first("16.2 Shared semantic separations", level=2)
    records.append(
        _objective_record(
            source_id,
            "expression:shared-semantic-separations",
            GenesisRecordType.CREATOR_PREFERENCE,
            "Attraction, bodily response, desire, consent, objectification, and chosen action remain distinct semantics.",
            (WORLD_ID,),
            _section_payload(semantic_separations, core_sha256),
            tags=(
                "character_expression",
                "semantic_separation",
                "consent_boundary",
                "objectification_boundary",
            ),
            visibility=Visibility.SYSTEM_PRIVATE,
            epistemic_layer=EpistemicLayer.CREATOR_PREFERENCE,
            content_class=_content_class(semantic_separations.body),
        )
    )
    for key, prefix, tags in (
        (
            "expression:baseline-invariants",
            "16.8 Required baseline tests",
            ("baseline_invariants", "semantic_validation"),
        ),
        (
            "expression:trust-fracture-model",
            "16.9 Trust-fracture and response-intensity model",
            ("trust_fracture", "response_intensity"),
        ),
        (
            "expression:authority-and-non-predetermination",
            "16.15 Authority, visibility, and non-predetermination",
            ("authority_boundary", "no_future_plot"),
        ),
    ):
        section = document.first(prefix, level=2)
        records.append(
            _objective_record(
                source_id,
                key,
                GenesisRecordType.CREATOR_PREFERENCE,
                _first_meaningful_line(section.body, section.title),
                (WORLD_ID,),
                _section_payload(section, core_sha256),
                tags=("character_expression", *tags),
                visibility=Visibility.SYSTEM_PRIVATE,
                epistemic_layer=EpistemicLayer.CREATOR_PREFERENCE,
                content_class=_content_class(section.body),
            )
        )

    public_dispositions = _named_public_dispositions(
        document.first("16.7.2 Public dispositions", level=3).body
    )
    activation_cues = _plain_bullets(
        document.first("16.7.3 Activation", level=3).body
    )
    profile_parent = document.first(
        "16.4 Character embodied-identity profiles",
        level=2,
    )
    profiles = document.children(profile_parent, level=3)
    if len(profiles) != 7:
        raise ValueError(
            f"expected seven embodied-identity profiles, found {len(profiles)}"
        )
    for profile in profiles:
        match = re.match(
            r"^16\.4\.\d\s+([A-Za-z]+)\s+Hanezawa$",
            profile.title,
        )
        if not match or match.group(1) not in CHARACTER_IDS:
            raise ValueError(
                f"invalid embodied-identity profile heading: {profile.title}"
            )
        name = match.group(1)
        character_id = CHARACTER_IDS[name]
        subsections = document.children(profile, level=4)
        section_map = {
            _slug(section.title): {
                "heading": section.title,
                "source_text": section.body,
            }
            for section in subsections
        }
        development = next(
            (
                section.body
                for section in subsections
                if section.title == "Development boundary"
            ),
            "",
        )
        records.append(
            _private_expression_record(
                source_id,
                key=f"expression:embodied:{name}",
                character_id=character_id,
                record_type=GenesisRecordType.CHARACTER_STATE,
                claim=(
                    f"{name}'s owner-scoped embodied identity and sexual-ethics "
                    "worldview governs how she interprets attention, dignity, "
                    "pleasure, privacy, and objectification."
                ),
                payload={
                    "profile_id": f"embodied_identity:{name.casefold()}",
                    "title": profile.title,
                    "public_disposition": public_dispositions.get(name, ""),
                    "activation_cues": activation_cues,
                    "profile_sections": section_map,
                    "identity_invariants": development,
                    "source": _source_ref(profile, core_sha256),
                },
                tags=(
                    "character_expression",
                    "embodied_identity_profile",
                    "sexual_ethics",
                    "objectification_boundary",
                    name.casefold(),
                ),
                expandable_sections=(
                    "public_disposition",
                    "activation_cues",
                    "profile_sections",
                    "identity_invariants",
                ),
                content_class=_content_class(profile.body),
            )
        )

    response_parent = document.first(
        "16.11 Full refusal, dignity, retaliation, and trust-fracture speech standard",
        level=2,
    )
    response_characters = document.children(response_parent, level=3)
    if len(response_characters) != 7:
        raise ValueError(
            f"expected seven response-style characters, found {len(response_characters)}"
        )
    response_record_count = 0
    response_example_count = 0
    for character_section in response_characters:
        match = re.match(
            r"^16\.11\.\d\s+([A-Za-z]+)$",
            character_section.title,
        )
        if not match or match.group(1) not in CHARACTER_IDS:
            raise ValueError(
                f"invalid response-style character heading: {character_section.title}"
            )
        name = match.group(1)
        character_id = CHARACTER_IDS[name]
        metaphor_domains = _labeled_inline_value(
            character_section.body,
            "Natural metaphor and comparison domains",
        )
        fidelity_rule = _labeled_inline_value(
            character_section.body,
            "Fidelity rule",
        )
        if not metaphor_domains or not fidelity_rule:
            raise ValueError(f"{name} response style lacks domains or fidelity rule")
        records.append(
            _objective_record(
                source_id,
                f"expression:signature:{name}",
                GenesisRecordType.VOICE_PROFILE,
                f"{name}'s rhetorical signature uses character-specific reasoning without mandatory catchphrases.",
                (character_id,),
                {
                    "signature_id": f"character_expression:{name.casefold()}",
                    "speaker_id": str(character_id),
                    "metaphor_domains": metaphor_domains,
                    "fidelity_rule": fidelity_rule,
                    "examples_are_non_executable": True,
                    "source": _source_ref(character_section, core_sha256),
                },
                tags=(
                    "character_expression",
                    "rhetorical_signature",
                    "speech_realization",
                    name.casefold(),
                ),
                visibility=Visibility.SYSTEM_PRIVATE,
                content_class=_content_class(character_section.body),
            )
        )
        modes = document.children(character_section, level=4)
        if len(modes) != 11:
            raise ValueError(
                f"expected eleven response modes for {name}, found {len(modes)}"
            )
        for mode_section in modes:
            mode = _EXPRESSION_MODE_NAMES.get(mode_section.title)
            if mode is None:
                raise ValueError(
                    f"unsupported response mode heading: {mode_section.title}"
                )
            examples = _blockquote_examples(mode_section.body)
            if len(examples) != 2:
                raise ValueError(
                    f"{name} {mode} requires exactly two style examples"
                )
            records.append(
                _objective_record(
                    source_id,
                    f"expression:mode:{name}:{mode}",
                    GenesisRecordType.VOICE_PROFILE,
                    (
                        f"{name}'s {mode.replace('_', ' ')} mode preserves her "
                        "selected meaning, boundary, identity, and trust consequence."
                    ),
                    (character_id,),
                    {
                        "response_mode_id": f"{name.casefold()}:{mode}",
                        "speaker_id": str(character_id),
                        "response_mode": mode,
                        "response_mode_heading": mode_section.title,
                        "metaphor_domains": metaphor_domains,
                        "fidelity_rule": fidelity_rule,
                        "style_examples": examples,
                        "examples_are_non_executable": True,
                        "examples_must_not_be_copied_or_lightly_paraphrased": True,
                        "source": _source_ref(mode_section, core_sha256),
                    },
                    tags=(
                        "character_expression",
                        "speech_realization",
                        "response_mode",
                        mode,
                        "examples_noncopyable",
                        name.casefold(),
                    ),
                    visibility=Visibility.SYSTEM_PRIVATE,
                    content_class=_content_class(mode_section.body),
                )
            )
            response_record_count += 1
            response_example_count += len(examples)
    if response_record_count != 77 or response_example_count != 154:
        raise ValueError(
            "V1.2 response-style compilation requires 77 modes and 154 examples"
        )

    records.extend(
        _hana_aging_expression_records(
            document,
            source_id,
            core_sha256,
        )
    )
    runtime_contract = document.first(
        "16.14 Runtime projection and DeepSeek Writer realization standard",
        level=2,
    )
    records.append(
        _objective_record(
            source_id,
            "expression:runtime-projection-contract",
            GenesisRecordType.CREATOR_PREFERENCE,
            "Character Director selects meaning; Python projects bounded active-speaker evidence; DeepSeek realizes fresh prose.",
            (WORLD_ID,),
            _section_payload(runtime_contract, core_sha256),
            tags=(
                "character_expression",
                "runtime_projection",
                "active_speakers_only",
                "examples_noncopyable",
            ),
            visibility=Visibility.SYSTEM_PRIVATE,
            epistemic_layer=EpistemicLayer.CREATOR_PREFERENCE,
            content_class=_content_class(runtime_contract.body),
        )
    )
    return _module(
        source_id,
        "Hanezawa embodied identity and character-specific expression authority",
        records,
    )


def _private_expression_record(
    source_id: TypedId,
    *,
    key: str,
    character_id: TypedId,
    record_type: GenesisRecordType,
    claim: str,
    payload: dict[str, Any],
    tags: tuple[str, ...],
    expandable_sections: tuple[str, ...],
    content_class: str,
) -> GenesisRecord:
    record_version, supersedes = _versioning(key)
    return GenesisRecord(
        schema_version=GenesisRecord.SCHEMA_VERSION,
        record_id=_record_id(key),
        record_version=record_version,
        record_type=record_type,
        epistemic_layer=EpistemicLayer.PRIVATE_BELIEF,
        truth_status=TruthStatus.CHARACTER_OWNED,
        claim=claim,
        authority=EvidenceAuthority.CREATOR,
        subject_ids=(character_id,),
        owner_id=character_id,
        knowledge_owner_ids=(character_id,),
        visibility=Visibility.OWNER_PRIVATE,
        knowledge_route=KnowledgeRoute.CREATOR_SEED,
        certainty=Certainty.BELIEVED,
        content_class=content_class,
        adult_eligibility=AdultEligibility.NOT_APPLICABLE,
        story_start_presence=StoryStartPresence.NOT_APPLICABLE,
        relationship_from_id=None,
        relationship_to_id=None,
        source_refs=(source_id,),
        valid_from=None,
        valid_to=None,
        supersedes=supersedes,
        tags=tags,
        expandable_sections=expandable_sections,
        payload_json=canonical_json(payload),
    )


def _hana_aging_expression_records(
    document: MarkdownDocument,
    source_id: TypedId,
    core_sha256: str,
) -> tuple[GenesisRecord, ...]:
    character_id = CHARACTER_IDS["Hana"]
    truth = document.first(
        "16.10.1 Objective truth versus Hana's private distortion",
        level=3,
    )
    thoughts = document.first(
        "16.10.2 Hana private-thought references",
        level=3,
    )
    sakura = document.first(
        "16.10.3 Connection to Sakura's delegated authority",
        level=3,
    )
    belief = _private_expression_record(
        source_id,
        key="expression:hana:aging-self-blame",
        character_id=character_id,
        record_type=GenesisRecordType.CHARACTER_STATE,
        claim=(
            "Hana privately fears that aging and prioritizing motherhood made "
            "her insufficient as a wife."
        ),
        payload={
            "belief_id": "hana_aging_self_blame",
            "private_causal_model": truth.body,
            "private_thought_references": _blockquote_examples(thoughts.body),
            "sakura_authority_connection": sakura.body,
            "objective_correction_record_id": str(
                _record_id("expression:hana:aging-objective-correction")
            ),
            "source": _source_ref(truth, core_sha256),
        },
        tags=(
            "character_expression",
            "hana",
            "aging_self_blame",
            "cognitive_distortion",
            "owner_private",
        ),
        expandable_sections=(
            "private_causal_model",
            "private_thought_references",
            "sakura_authority_connection",
        ),
        content_class="ordinary",
    )
    correction = _objective_record(
        source_id,
        "expression:hana:aging-objective-correction",
        GenesisRecordType.CHARACTER_STATE,
        "Hana's aging and motherhood did not cause her husband's betrayal; his deception and betrayal were his choices.",
        (character_id, HUSBAND_ID),
        {
            "belief_record_id": str(belief.record_id),
            "objective_cause_assignment": "husband_choice",
            "prohibited_cause_assignments": (
                "hana_aging",
                "hana_motherhood",
                "hana_body_changes",
            ),
            "source": _source_ref(truth, core_sha256),
        },
        tags=(
            "character_expression",
            "hana",
            "aging_objective_correction",
            "betrayal_responsibility",
        ),
        visibility=Visibility.SYSTEM_PRIVATE,
    )

    support_records: list[GenesisRecord] = [belief, correction]
    for prefix, activation, leakage_rule in (
        (
            "16.10.4 Daughter support before the affair is disclosed",
            "pre_disclosure",
            "Mia, Tomi, Aoi, and Yuuni must not receive or reveal affair knowledge.",
        ),
        (
            "16.10.5 Conditional speech after the affair becomes known",
            "requires_valid_affair_disclosure",
            "These examples cannot activate before valid branch-local disclosure or learning.",
        ),
    ):
        section = document.first(prefix, level=3)
        groups = _named_example_groups(section.body)
        if set(groups) != {
            "Sakura",
            "Mia",
            "Enne",
            "Tomi",
            "Aoi",
            "Yuuni",
        }:
            raise ValueError(f"aging support examples are incomplete: {prefix}")
        for name, examples in groups.items():
            support_records.append(
                _objective_record(
                    source_id,
                    f"expression:aging-support:{activation}:{name}",
                    GenesisRecordType.VOICE_PROFILE,
                    (
                        f"{name}'s {activation.replace('_', ' ')} support for "
                        "Hana's aging insecurity preserves her own knowledge boundary."
                    ),
                    (CHARACTER_IDS[name], character_id),
                    {
                        "support_mode_id": (
                            f"{name.casefold()}:hana_aging:{activation}"
                        ),
                        "speaker_id": str(CHARACTER_IDS[name]),
                        "target_id": str(character_id),
                        "activation_requirement": activation,
                        "knowledge_constraint": leakage_rule,
                        "style_examples": examples,
                        "examples_are_non_executable": True,
                        "examples_must_not_be_copied_or_lightly_paraphrased": True,
                        "source": _source_ref(section, core_sha256),
                    },
                    tags=(
                        "character_expression",
                        "aging_support",
                        activation,
                        "examples_noncopyable",
                        name.casefold(),
                    ),
                    visibility=Visibility.SYSTEM_PRIVATE,
                )
            )
    return tuple(support_records)


def _blockquote_examples(body: str) -> tuple[str, ...]:
    return tuple(
        line.strip()[1:].strip()
        for line in body.splitlines()
        if line.strip().startswith(">")
    )


def _labeled_inline_value(body: str, label: str) -> str:
    match = re.search(
        rf"(?m)^\*\*{re.escape(label)}:\*\*\s*(.+?)\s*$",
        body,
    )
    return match.group(1).strip() if match else ""


def _named_example_groups(body: str) -> dict[str, tuple[str, ...]]:
    heading = re.compile(
        r"(?m)^\*\*(Hana|Sakura|Mia|Enne|Tomi|Aoi|Yuuni)\*\*\s*$"
    )
    matches = list(heading.finditer(body))
    groups: dict[str, tuple[str, ...]] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        groups[match.group(1)] = _blockquote_examples(body[match.end() : end])
    return groups


def _named_public_dispositions(body: str) -> dict[str, str]:
    dispositions: dict[str, str] = {}
    for value in _plain_bullets(body):
        for name in CHARACTER_NAMES:
            if value.startswith(f"{name} "):
                dispositions[name] = value.rstrip(";.")
                break
    if set(dispositions) != set(CHARACTER_NAMES):
        raise ValueError("public embodied-identity dispositions are incomplete")
    return dispositions


def _framework_module(
    document: MarkdownDocument, source_hashes: dict[str, str]
) -> GenesisModule:
    """Preserve cross-character reasoning, development, and projection contracts."""

    source_id = _module_source_id("frameworks")
    records: list[GenesisRecord] = []
    purpose = document.first("0. Purpose", level=2)
    records.append(
        _objective_record(
            source_id,
            "framework:purpose",
            GenesisRecordType.CREATOR_PREFERENCE,
            _first_meaningful_line(purpose.body, purpose.title),
            (WORLD_ID,),
            _section_payload(purpose, _core_sha256(source_hashes)),
            tags=("framework", "purpose", "runtime_boundary"),
            visibility=Visibility.SYSTEM_PRIVATE,
            epistemic_layer=EpistemicLayer.CREATOR_PREFERENCE,
        )
    )
    authority_parent = document.first("1. Authority, truth, and memory model", level=2)
    for section in document.children(authority_parent, level=3):
        records.append(
            _objective_record(
                source_id,
                f"framework:{section.title}",
                GenesisRecordType.CREATOR_PREFERENCE,
                _first_meaningful_line(section.body, section.title),
                _subject_ids(section.body) or (WORLD_ID,),
                _section_payload(section, _core_sha256(source_hashes)),
                tags=("framework", _slug(section.title)),
                visibility=Visibility.SYSTEM_PRIVATE,
                epistemic_layer=EpistemicLayer.CREATOR_PREFERENCE,
                content_class=_content_class(section.body),
            )
        )
    validation_prefix = "16." if _spec().version == "v1_1" else "17."
    for prefix in ("10.", "11.", "12.", "14.", validation_prefix):
        parent = document.first(prefix, level=1)
        for section in document.children(parent, level=2):
            records.append(
                _objective_record(
                    source_id,
                    f"framework:{section.title}",
                    GenesisRecordType.CREATOR_PREFERENCE,
                    _first_meaningful_line(section.body, section.title),
                    _subject_ids(section.body) or (WORLD_ID,),
                    _section_payload(section, _core_sha256(source_hashes)),
                    tags=("framework", _slug(parent.title), _slug(section.title)),
                    visibility=Visibility.SYSTEM_PRIVATE,
                    epistemic_layer=EpistemicLayer.CREATOR_PREFERENCE,
                    content_class=_content_class(section.body),
                )
            )
    return _module(
        source_id,
        "Cross-character authority, development, protection, voice, and projection frameworks",
        records,
    )


def _ledger_module(
    document: MarkdownDocument,
    overlay: dict[str, Any],
    source_hashes: dict[str, str],
) -> GenesisModule:
    source_id = _module_source_id("ledger")
    records: list[GenesisRecord] = []
    supersessions = document.first("15.1 Active supersessions", level=2)
    rows = _parse_table(supersessions.body)
    for index, row in enumerate(rows, start=1):
        records.append(
            _objective_record(
                source_id,
                f"ledger:supersession:{index}",
                GenesisRecordType.SUPERSESSION_LEDGER,
                row[1],
                _subject_ids(" ".join(row)) or (WORLD_ID,),
                {
                    "older_claim": row[0],
                    "active_resolution": row[1],
                    "source": _source_ref(supersessions, _core_sha256(source_hashes)),
                },
                tags=(
                    "supersession",
                    "provenance",
                    _spec().version,
                    f"entry_{index}",
                ),
                visibility=Visibility.SYSTEM_PRIVATE,
            )
        )
    unknowns = document.first("15.3 Explicit unknowns", level=2)
    skipped_conflicts = {
        "whether the husband is Mia's or Yuuni's biological father;",
        "Chiyo's precise historical role;",
    }
    for index, statement in enumerate(_plain_bullets(unknowns.body), start=1):
        if statement in skipped_conflicts:
            continue
        subjects = _subject_ids(statement) or (WORLD_ID,)
        key = f"unknown:{index}:{statement}"
        record_version, supersedes = _versioning(key)
        records.append(
            GenesisRecord(
                schema_version=GenesisRecord.SCHEMA_VERSION,
                record_id=_record_id(key),
                record_version=record_version,
                record_type=GenesisRecordType.UNRESOLVED_QUESTION,
                epistemic_layer=EpistemicLayer.UNRESOLVED_QUESTION,
                truth_status=TruthStatus.UNKNOWN,
                claim=statement.rstrip(";."),
                authority=EvidenceAuthority.CREATOR,
                subject_ids=subjects,
                owner_id=None,
                knowledge_owner_ids=(),
                visibility=Visibility.SYSTEM_PRIVATE,
                knowledge_route=KnowledgeRoute.CREATOR_SEED,
                certainty=Certainty.UNKNOWN,
                content_class=_content_class(statement),
                adult_eligibility=AdultEligibility.NOT_APPLICABLE,
                story_start_presence=StoryStartPresence.NOT_APPLICABLE,
                relationship_from_id=None,
                relationship_to_id=None,
                source_refs=(source_id,),
                valid_from=None,
                valid_to=None,
                supersedes=supersedes,
                tags=("explicit_unknown", f"unknown_{index}"),
                expandable_sections=("source",),
                payload_json=canonical_json(
                    {
                        "question": statement.rstrip(";."),
                        "source": _source_ref(unknowns, _core_sha256(source_hashes)),
                    }
                ),
            )
        )
    records.extend(_creator_corrections(source_id, source_hashes, overlay))
    return _module(source_id, "Supersessions, explicit unknowns, and later creator corrections", records)


def _creator_corrections(
    source_id: TypedId,
    source_hashes: dict[str, str],
    overlay: dict[str, Any],
) -> tuple[GenesisRecord, ...]:
    return (
        _objective_record(
            source_id,
            "correction:husband-biological-father",
            GenesisRecordType.FAMILY,
            "Hana's unnamed husband is the biological father of Mia and Yuuni.",
            (HUSBAND_ID, CHARACTER_IDS["Hana"], CHARACTER_IDS["Mia"], CHARACTER_IDS["Yuuni"]),
            {
                "father_id": str(HUSBAND_ID),
                "biological_children": [str(CHARACTER_IDS["Mia"]), str(CHARACTER_IDS["Yuuni"])],
                "source_authority": "direct creator decision predating and retained over V1.1 conflict",
            },
            tags=("family", "biological_father", "creator_correction"),
            visibility=Visibility.SYSTEM_PRIVATE,
        ),
        _objective_record(
            source_id,
            "correction:husband-not-adoptive-father",
            GenesisRecordType.FAMILY,
            "Hana alone legally adopted Sakura, Enne, Tomi, and Aoi; her husband is not their adoptive father.",
            (HUSBAND_ID, CHARACTER_IDS["Hana"], CHARACTER_IDS["Sakura"], CHARACTER_IDS["Enne"], CHARACTER_IDS["Tomi"], CHARACTER_IDS["Aoi"]),
            {
                "sole_legal_adopter": str(CHARACTER_IDS["Hana"]),
                "adopted_children": [str(CHARACTER_IDS[name]) for name in ("Sakura", "Enne", "Tomi", "Aoi")],
                "husband_is_adoptive_father": False,
            },
            tags=("family", "adoption", "creator_correction"),
            visibility=Visibility.SYSTEM_PRIVATE,
        ),
        _objective_record(
            source_id,
            "correction:chiyo-excluded",
            GenesisRecordType.CREATOR_PREFERENCE,
            "Chiyo is excluded from active CERA Genesis and must not be inferred into Hanezawa backstory.",
            (WORLD_ID,),
            {"excluded_entity": "Chiyo", "historical_source_may_remain_provenance_only": True},
            tags=("creator_preference", "excluded_entity", "chiyo"),
            visibility=Visibility.SYSTEM_PRIVATE,
            epistemic_layer=EpistemicLayer.CREATOR_PREFERENCE,
            supersedes=(_record_id("ledger:supersession:2"),),
            record_version=2,
        ),
        _objective_record(
            source_id,
            "correction:yuuni-visual",
            GenesisRecordType.CREATOR_PREFERENCE,
            "Yuuni's ordinary visual presentation may be deliberately childish and youthful, without changing her canonical age, agency, or protected-route age clarity.",
            (CHARACTER_IDS["Yuuni"],),
            {
                "ordinary_visual": overlay["age_language_policy"]["yuuni_ordinary_visual"],
                "protected_visual": overlay["age_language_policy"]["yuuni_protected_visual"],
                "canonical_age": 18,
                "mental_incapacity_inferred": False,
            },
            tags=("creator_preference", "yuuni", "visual_age_style"),
            visibility=Visibility.SYSTEM_PRIVATE,
            epistemic_layer=EpistemicLayer.CREATOR_PREFERENCE,
            supersedes=(_record_id("ledger:supersession:26"),),
            record_version=2,
        ),
    )


def _character_section_record(
    source_id: TypedId,
    character_id: TypedId,
    name: str,
    section: MarkdownSection,
    record_type: GenesisRecordType,
    core_sha256: str,
    private: bool,
) -> GenesisRecord:
    claim = _first_meaningful_line(section.body, section.title)
    key = f"character-section:{name}:{section.title}"
    if private:
        record_version, supersedes = _versioning(key)
        return GenesisRecord(
            schema_version=GenesisRecord.SCHEMA_VERSION,
            record_id=_record_id(key),
            record_version=record_version,
            record_type=record_type,
            epistemic_layer=EpistemicLayer.PRIVATE_BELIEF,
            truth_status=TruthStatus.CHARACTER_OWNED,
            claim=claim,
            authority=EvidenceAuthority.CREATOR,
            subject_ids=(character_id,),
            owner_id=character_id,
            knowledge_owner_ids=(character_id,),
            visibility=Visibility.OWNER_PRIVATE,
            knowledge_route=KnowledgeRoute.CREATOR_SEED,
            certainty=Certainty.BELIEVED,
            content_class=_content_class(section.title + " " + section.body),
            adult_eligibility=AdultEligibility.NOT_APPLICABLE,
            story_start_presence=StoryStartPresence.NOT_APPLICABLE,
            relationship_from_id=None,
            relationship_to_id=None,
            source_refs=(source_id,),
            valid_from=None,
            valid_to=None,
            supersedes=supersedes,
            tags=(name.casefold(), _slug(section.title), "owner_private"),
            expandable_sections=("source_text",),
            payload_json=canonical_json(_section_payload(section, core_sha256)),
        )
    return _objective_record(
        source_id,
        key,
        record_type,
        claim,
        (character_id,),
        _section_payload(section, core_sha256),
        tags=(name.casefold(), _slug(section.title)),
        visibility=Visibility.SYSTEM_PRIVATE,
        content_class=_content_class(section.title + " " + section.body),
    )


def _relationship_record(
    source_id: TypedId,
    from_name: str,
    to_name: str,
    claim: str,
    fields: dict[str, str],
    section: MarkdownSection,
    core_sha256: str,
) -> GenesisRecord:
    from_id = CHARACTER_IDS[from_name]
    to_id = TED_ID if to_name == "Ted" else CHARACTER_IDS[to_name]
    key = f"relationship:{from_name}:{to_name}"
    record_version, supersedes = _versioning(key)
    return GenesisRecord(
        schema_version=GenesisRecord.SCHEMA_VERSION,
        record_id=_record_id(key),
        record_version=record_version,
        record_type=GenesisRecordType.RELATIONSHIP_EDGE,
        epistemic_layer=EpistemicLayer.PRIVATE_BELIEF,
        truth_status=TruthStatus.CHARACTER_OWNED,
        claim=claim,
        authority=EvidenceAuthority.CREATOR,
        subject_ids=(from_id, to_id),
        owner_id=from_id,
        knowledge_owner_ids=(from_id,),
        visibility=Visibility.OWNER_PRIVATE,
        knowledge_route=KnowledgeRoute.CREATOR_SEED,
        certainty=Certainty.BELIEVED,
        content_class=_content_class(section.body),
        adult_eligibility=AdultEligibility.NOT_APPLICABLE,
        story_start_presence=StoryStartPresence.NOT_APPLICABLE,
        relationship_from_id=from_id,
        relationship_to_id=to_id,
        source_refs=(source_id,),
        valid_from=None,
        valid_to=None,
        supersedes=supersedes,
        tags=("relationship", from_name.casefold(), to_name.casefold()),
        expandable_sections=tuple(_slug(key) for key in fields),
        payload_json=canonical_json(
            {
                "from_id": str(from_id),
                "to_id": str(to_id),
                "fields": fields,
                "source": _source_ref(section, core_sha256),
            }
        ),
    )


def _require_new_target(path: Path, label: str) -> None:
    if path.exists():
        raise FileExistsError(f"{label} already exists: {path}")


def _write_views(
    view_root: Path,
    manifest: GenesisManifest,
    modules: tuple[GenesisModule, ...],
    source_hashes: dict[str, str],
) -> None:
    view_root.mkdir(parents=True)
    records = tuple(record for module in modules for record in module.records)
    counts = Counter(record.record_type.value for record in records)
    header = [
        "<!-- GENERATED, NOT AUTHORITY -->",
        (
            f"# Hanezawa Genesis "
            f"{_spec().version.replace('_', '.').upper()} — Generated Master Index"
        ),
        "",
        "**Status:** generated read-only view; discard and rebuild on conflict",
        f"**Generator:** `{COMPILER_CONTRACT_VERSION}` / `{_spec().builder_version}`",
        f"**Revision:** `{manifest.revision_id}`",
        "**Branch scope:** immutable Genesis; branch overlays excluded",
        "**Privacy view:** system index; private claims are listed by ID/title only",
        f"**Core source SHA-256:** `{_core_sha256(source_hashes)}`",
        f"**Visual source SHA-256:** `{source_hashes[VISUAL_FILENAME]}`",
        "",
        "## Record counts",
        "",
        "| Record type | Count |",
        "|---|---:|",
    ]
    header.extend(f"| `{name}` | {count} |" for name, count in sorted(counts.items()))
    header.extend(["", "## Modules", ""])
    for module in modules:
        header.append(f"- `{module.source_id}` — {module.title}: {len(module.records)} records")
    (view_root / "MASTER_INDEX.md").write_text("\n".join(header) + "\n", encoding="utf-8")

    character_root = view_root / "characters"
    character_root.mkdir()
    for name, character_id in CHARACTER_IDS.items():
        owned = [record for record in records if character_id in record.subject_ids]
        lines = [
            "<!-- GENERATED, NOT AUTHORITY -->",
            f"# {name} Hanezawa — Generated Evidence Index",
            "",
            f"**Character ID:** `{character_id}`",
            f"**Genesis revision:** `{manifest.revision_id}`",
            "**Privacy:** index labels only; fetch remains access-controlled",
            "",
        ]
        for record in owned:
            lines.append(
                f"- `{record.record_id}` · `{record.record_type.value}` · "
                f"`{record.visibility.value}` — {record.claim}"
            )
        (character_root / f"{name.casefold()}.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    for record_type, filename in (
        (GenesisRecordType.FORMATIVE_EVENT, "EVENT_INDEX.md"),
        (GenesisRecordType.MEMORY_SEED, "MEMORY_INDEX.md"),
        (GenesisRecordType.RELATIONSHIP_EDGE, "RELATIONSHIP_INDEX.md"),
    ):
        selected = [record for record in records if record.record_type is record_type]
        lines = [
            "<!-- GENERATED, NOT AUTHORITY -->",
            f"# {record_type.value.replace('_', ' ').title()} Index",
            "",
            f"**Revision:** `{manifest.revision_id}`",
            "**Status:** generated lookup aid; authoritative content remains JSON",
            "",
        ]
        lines.extend(f"- `{record.record_id}` — {record.claim}" for record in selected)
        (view_root / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")


def default_repository_paths(repo_root: str | Path) -> dict[str, Path]:
    root = Path(repo_root).resolve()
    return {
        "source_root": root / "genesis" / "cera_authority",
        "package_root": (
            root / "genesis" / "packages" / V1_1_BUILD_SPEC.package_directory
        ),
        "authorization_path": (
            root
            / "genesis"
            / "authorizations"
            / V1_1_BUILD_SPEC.authorization_filename
        ),
        "view_root": (
            root / "genesis" / "generated" / V1_1_BUILD_SPEC.generated_directory
        ),
        "source_integrity_path": (
            root
            / "genesis"
            / "cera_authority"
            / V1_1_BUILD_SPEC.source_integrity_filename
        ),
    }


def default_v1_2_repository_paths(repo_root: str | Path) -> dict[str, Path]:
    root = Path(repo_root).resolve()
    return {
        "source_root": root / "genesis" / "cera_authority",
        "package_root": (
            root / "genesis" / "packages" / V1_2_BUILD_SPEC.package_directory
        ),
        "authorization_path": (
            root
            / "genesis"
            / "authorizations"
            / V1_2_BUILD_SPEC.authorization_filename
        ),
        "view_root": (
            root / "genesis" / "generated" / V1_2_BUILD_SPEC.generated_directory
        ),
        "source_integrity_path": (
            root
            / "genesis"
            / "cera_authority"
            / V1_2_BUILD_SPEC.source_integrity_filename
        ),
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Build a Hanezawa Genesis revision")
    parser.add_argument("repo_root", type=Path)
    parser.add_argument(
        "--version",
        choices=("v1_1", "v1_2"),
        default="v1_1",
    )
    args = parser.parse_args()
    if args.version == "v1_2":
        built = build_hanezawa_genesis_v1_2(
            **default_v1_2_repository_paths(args.repo_root)
        )
    else:
        built = build_hanezawa_genesis(
            **default_repository_paths(args.repo_root)
        )
    print(built.package_root)


if __name__ == "__main__":
    main()
