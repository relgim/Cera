"""Compile audited Sera adult references into CERA-owned craft fragments.

This is an offline build tool.  It reads only repository-local immutable copies,
verifies all 24 hashes against CURATION.json, and emits canonical JSON.  Runtime
catalog loading never reads the historical E-drive source tree.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cera.adult_craft.models import (  # noqa: E402
    AdultCraftAssetClass,
    AdultCraftAxis,
    AdultCraftCatalogManifest,
    AdultCraftConcept,
    AdultCraftFamily,
    AdultCraftFragment,
    AdultCraftFragmentIndexEntry,
    AdultCraftMode,
    CraftSourceBinding,
    CraftVocabularyGroup,
    RealizationChannel,
)
from cera.errors import ContractValidationError  # noqa: E402
from cera.ids import IdKind, deterministic_id  # noqa: E402
from cera.serialization import bytes_sha256, canonical_json, text_sha256  # noqa: E402


COMPILER_VERSION = "cera.adult_craft_compiler.v1"
SOURCE_SCHEMA = "cera.adult_source_integrity.v1"
_HEADING = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$")


@dataclass(frozen=True)
class MarkdownSection:
    level: int
    title: str
    heading_path: tuple[str, ...]
    line_start: int
    line_end: int
    text: str
    has_child: bool


def compile_catalog(
    *,
    provenance_files: Path,
    curation_path: Path,
    output_root: Path,
    integrity_path: Path,
) -> AdultCraftCatalogManifest:
    curation_bytes = curation_path.read_bytes()
    curation = json.loads(curation_bytes.decode("utf-8"))
    if curation.get("schema_version") != "cera.adult_craft_curation.v1":
        raise ContractValidationError("unknown adult craft curation schema")
    sources = curation.get("sources")
    if not isinstance(sources, list) or len(sources) != 24:
        raise ContractValidationError("curation must enumerate exactly 24 adult sources")
    if len({value.get("path") for value in sources if isinstance(value, dict)}) != 24:
        raise ContractValidationError("adult curation source paths must be unique")

    integrity_entries = []
    compiled: list[AdultCraftFragment] = []
    status_counts = {"provenance_only": 0, "audit_only": 0}
    lexicon_rules = curation.get("lexicon_rules", [])
    for source in sources:
        path_value = source["path"]
        source_path = (provenance_files / path_value).resolve()
        if provenance_files.resolve() not in source_path.parents:
            raise ContractValidationError("curated adult source escaped provenance root")
        raw = source_path.read_bytes()
        actual_sha = bytes_sha256(raw)
        if actual_sha != source["sha256"]:
            raise ContractValidationError(f"adult source hash mismatch: {path_value}")
        integrity_entries.append(
            {"relative_path": path_value, "bytes": len(raw), "sha256": actual_sha}
        )
        status = source["status"]
        if status in status_counts:
            status_counts[status] += 1
            continue
        if status != "compile":
            raise ContractValidationError(f"unsupported adult curation status: {status}")
        text = raw.decode("utf-8-sig")
        for section in _select_sections(text, source["strategy"]):
            if section.title in set(source.get("excluded_headings", [])):
                continue
            compiled.append(
                _compile_fragment(
                    curation["catalog_version"],
                    source,
                    section,
                    lexicon_rules,
                )
            )

    integrity_payload = {
        "schema_version": SOURCE_SCHEMA,
        "source_set": "sera_adult_source_v1",
        "artifact_count": len(integrity_entries),
        "artifacts": integrity_entries,
        "runtime_authority": False,
        "runtime_external_dependency": False,
    }
    integrity_path.parent.mkdir(parents=True, exist_ok=True)
    _write_canonical(integrity_path, integrity_payload)
    source_integrity_sha = bytes_sha256(integrity_path.read_bytes())

    fragments_root = output_root / "fragments"
    fragments_root.mkdir(parents=True, exist_ok=True)
    if any(fragments_root.iterdir()):
        raise ContractValidationError("adult fragment output must be empty before compilation")
    entries: list[AdultCraftFragmentIndexEntry] = []
    for fragment in sorted(compiled, key=lambda value: str(value.fragment_id)):
        relative = f"fragments/{fragment.fragment_id.value}.json"
        _write_canonical(output_root / relative, fragment)
        entries.append(
            AdultCraftFragmentIndexEntry(
                fragment_id=fragment.fragment_id,
                relative_path=relative,
                fragment_sha256=fragment.fragment_sha256,
                craft_text_sha256=fragment.craft_text_sha256,
            )
        )
    manifest = AdultCraftCatalogManifest(
        schema_version=AdultCraftCatalogManifest.SCHEMA_VERSION,
        catalog_id=deterministic_id(
            IdKind.CRAFT_CATALOG,
            "cera.adult_craft_catalog.v1",
            f"{curation['catalog_version']}|{source_integrity_sha}|{bytes_sha256(curation_bytes)}",
        ),
        catalog_version=curation["catalog_version"],
        compiler_version=COMPILER_VERSION,
        source_integrity_sha256=source_integrity_sha,
        curation_sha256=bytes_sha256(curation_bytes),
        source_artifact_count=24,
        compiled_fragment_count=len(entries),
        provenance_only_source_count=status_counts["provenance_only"],
        audit_only_source_count=status_counts["audit_only"],
        fragment_entries=tuple(entries),
        runtime_external_path_dependencies=(),
        coverage_declarations_are_audit_claims_only=True,
        blocked_nonconsensual_generation_excluded=True,
    )
    _write_canonical(output_root / "manifest.json", manifest)
    return manifest


def _compile_fragment(catalog_version, source, section, lexicon_rules):
    searchable = f"{source['path']} {' '.join(section.heading_path)} {section.text}".casefold()
    families = [AdultCraftFamily(value) for value in source["families"]]
    if "adult anatomy" in searchable and AdultCraftFamily.ANAL not in families:
        families.append(AdultCraftFamily.ANAL)
    axes = _axes(searchable)
    channels = _channels(searchable, axes)
    vocabulary_by_concept: dict[AdultCraftConcept, list[str]] = {}
    for rule in lexicon_rules:
        if rule["path"] != source["path"]:
            continue
        if rule["heading_contains"].casefold() not in " / ".join(section.heading_path).casefold():
            continue
        for concept, terms in rule["groups"].items():
            typed_concept = AdultCraftConcept(concept)
            collected = vocabulary_by_concept.setdefault(typed_concept, [])
            for term in terms:
                if term.casefold() not in {value.casefold() for value in collected}:
                    collected.append(term)
    vocabulary_groups = [
        CraftVocabularyGroup(concept, tuple(terms))
        for concept, terms in vocabulary_by_concept.items()
    ]
    concepts = list(dict.fromkeys(value.concept for value in vocabulary_groups))
    if AdultCraftAxis.BUILDUP in axes:
        concepts.append(AdultCraftConcept.BUILDUP)
    if AdultCraftAxis.AFTERMATH in axes:
        concepts.append(AdultCraftConcept.AFTERMATH)
    if AdultCraftAxis.MATERIAL_CONTINUITY in axes:
        concepts.append(AdultCraftConcept.MATERIAL_CONTINUITY)
    if AdultCraftAxis.VOCALIZATION in axes:
        concepts.append(AdultCraftConcept.VOCALIZATION)
    if AdultCraftAxis.ACTION_BOUND_SOUND in axes:
        concepts.append(AdultCraftConcept.SOUND_EFFECT)
    if AdultCraftAxis.CAUSAL_PHYSIOLOGY in axes:
        concepts.append(AdultCraftConcept.BODILY_FLUIDS)
    concepts = list(dict.fromkeys(concepts))
    modes = [AdultCraftMode(value) for value in source["modes"]]
    if source["path"] == "hentai_vocab_quotes.md" and any(
        "quotes" in value.casefold() for value in section.heading_path
    ):
        modes = [AdultCraftMode.EX]
    asset_class = AdultCraftAssetClass(source["asset_class"])
    if any("quotes" in value.casefold() for value in section.heading_path):
        asset_class = AdultCraftAssetClass.MICRO_EXAMPLE
    fragment_key = f"{catalog_version}|{source['path']}|{'/'.join(section.heading_path)}|{text_sha256(section.text)}"
    return AdultCraftFragment(
        schema_version=AdultCraftFragment.SCHEMA_VERSION,
        fragment_id=deterministic_id(
            IdKind.CRAFT_REFERENCE,
            "cera.adult_craft_fragment.v1",
            fragment_key,
        ),
        catalog_version=catalog_version,
        asset_class=asset_class,
        modes=tuple(modes),
        families=tuple(families),
        axes=tuple(axes),
        channels=tuple(channels),
        coverage_concepts=tuple(concepts),
        vocabulary_groups=tuple(vocabulary_groups),
        activation_requirements=(
            "Python route is consent_valid_adult.",
            "Every participant is a confirmed adult with current informed and free consent/capacity.",
            "The validated SequencePlan activates this fragment's family and craft axis.",
        ),
        prohibited_inferences=(
            "Craft material cannot establish an event, preference, experience, consent, climax, injury, pregnancy, relationship fact, or character identity.",
            "Bodily response, vocalization, silence, freezing, or compliance cannot establish desire, pleasure, or consent.",
            "Conditional examples cannot be copied as a script or promoted to scene truth.",
        ),
        source=CraftSourceBinding(
            source_relative_path=source["path"],
            source_sha256=source["sha256"],
            heading_path=section.heading_path,
            line_start=section.line_start,
            line_end=section.line_end,
        ),
        craft_text=section.text,
        craft_text_sha256=text_sha256(section.text),
        craft_only=True,
        evaluation_evidence=False,
        blocked_nonconsensual_generation_excluded=True,
    )


def _axes(searchable: str) -> list[AdultCraftAxis]:
    result = []
    mapping = (
        (AdultCraftAxis.DIRECT_VOCABULARY, ("vocab", "anatomy", "language", "adult actions")),
        (AdultCraftAxis.BUILDUP, ("buildup", "gradual", "progression", "threshold", "tension", "hinge", "escalat")),
        (AdultCraftAxis.CAUSAL_PHYSIOLOGY, ("physiolog", "fluid", "body state", "bodily", "sensory", "toilet", "scat")),
        (AdultCraftAxis.VOCALIZATION, ("vocal", "dialogue", "spoken voice", "voice")),
        (AdultCraftAxis.ACTION_BOUND_SOUND, ("sound", " sfx", "pop", "click")),
        (AdultCraftAxis.CLIMAX, ("climax", "cervix", "reproductive", "peak")),
        (AdultCraftAxis.AFTERMATH, ("aftermath", "continuation", "aftereffect", "cleanup", "residue")),
        (AdultCraftAxis.MATERIAL_CONTINUITY, ("material", "continuity", "residue", "fluid", "waste")),
        (AdultCraftAxis.PSYCHOLOGICAL_CONTRAST, ("psycholog", "inner voice", "focalization", "contradiction", "private phrase")),
        (AdultCraftAxis.REALISM_AGENCY, ("agency", "choice", "resistance", "consent", "realism", "boundary")),
    )
    for axis, needles in mapping:
        if any(value in searchable for value in needles):
            result.append(axis)
    return result or [AdultCraftAxis.BUILDUP]


def _channels(searchable: str, axes: list[AdultCraftAxis]) -> list[RealizationChannel]:
    result = [RealizationChannel.NARRATION]
    if AdultCraftAxis.VOCALIZATION in axes:
        result.append(RealizationChannel.DIALOGUE)
    if "inner" in searchable or AdultCraftAxis.PSYCHOLOGICAL_CONTRAST in axes:
        result.append(RealizationChannel.INNER_VOICE)
    if AdultCraftAxis.ACTION_BOUND_SOUND in axes:
        result.append(RealizationChannel.SOUND_EFFECT)
    if AdultCraftAxis.CAUSAL_PHYSIOLOGY in axes:
        result.append(RealizationChannel.PHYSIOLOGY)
    return list(dict.fromkeys(result))


def _select_sections(text: str, strategy: str) -> tuple[MarkdownSection, ...]:
    lines = text.splitlines(keepends=True)
    headings = []
    stack: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = _HEADING.match(line.rstrip("\r\n"))
        if not match:
            continue
        level = len(match.group("marks"))
        title = match.group("title").strip()
        stack = [value for value in stack if value[0] < level]
        stack.append((level, title))
        headings.append((index, level, title, tuple(value[1] for value in stack)))
    if not headings:
        raise ContractValidationError("adult Markdown source has no headings")
    sections = []
    for position, (start, level, title, path) in enumerate(headings):
        end = len(lines)
        child = False
        for next_start, next_level, _, _ in headings[position + 1 :]:
            if next_level > level:
                child = True
            if next_level <= level:
                end = next_start
                break
        sections.append(
            MarkdownSection(
                level=level,
                title=title,
                heading_path=path,
                line_start=start + 1,
                line_end=end,
                text="".join(lines[start:end]).strip(),
                has_child=child,
            )
        )
    if strategy == "whole":
        root = sections[0]
        return (
            MarkdownSection(
                root.level,
                root.title,
                root.heading_path,
                1,
                len(lines),
                text.strip(),
                root.has_child,
            ),
        )
    if strategy == "h2":
        return tuple(value for value in sections if value.level == 2)
    if strategy == "leaf":
        return tuple(value for value in sections if value.level >= 2 and not value.has_child)
    raise ContractValidationError(f"unsupported Markdown section strategy: {strategy}")


def _write_canonical(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--provenance-files",
        type=Path,
        default=PROJECT_ROOT / "adult" / "provenance" / "sera_adult_source_v1" / "files",
    )
    parser.add_argument(
        "--curation",
        type=Path,
        default=PROJECT_ROOT / "adult" / "catalog" / "adult_craft_v1" / "CURATION.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "adult" / "catalog" / "adult_craft_v1",
    )
    parser.add_argument(
        "--integrity",
        type=Path,
        default=PROJECT_ROOT / "adult" / "provenance" / "sera_adult_source_v1" / "SOURCE_INTEGRITY.json",
    )
    args = parser.parse_args()
    manifest = compile_catalog(
        provenance_files=args.provenance_files.resolve(),
        curation_path=args.curation.resolve(),
        output_root=args.output.resolve(),
        integrity_path=args.integrity.resolve(),
    )
    print(canonical_json({
        "catalog_id": str(manifest.catalog_id),
        "manifest_sha256": manifest.manifest_sha256,
        "fragments": manifest.compiled_fragment_count,
        "sources": manifest.source_artifact_count,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
