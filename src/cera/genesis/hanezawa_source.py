"""Shared source parsing and record primitives for Hanezawa Genesis V1.1."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable, Iterable

from cera.contracts import (
    Certainty,
    EvidenceAuthority,
    KnowledgeRoute,
    TruthStatus,
    Visibility,
)
from cera.ids import IdKind, TypedId, deterministic_id
from cera.serialization import canonical_json, to_primitive

from .models import (
    AdultEligibility,
    EpistemicLayer,
    GenesisModule,
    GenesisPackageClass,
    GenesisRecord,
    GenesisRecordType,
    StoryStartPresence,
)


CORE_FILENAME = "HANEZAWA_CORE_GENESIS_V1_1.md"
VISUAL_FILENAME = "HANEZAWA_VISUAL_CANON_ANIMA_V1.json"
PROMPT_OVERLAY_FILENAME = "ANIMA_PROMPT_POLICY_CREATOR_OVERLAY_V1.json"
SOURCE_INTEGRITY_FILENAME = "SOURCE_INTEGRITY.json"

EXPECTED_SOURCE_HASHES = {
    CORE_FILENAME: "edf16b1204a0b8c656b0418e39b41c3306a995dc70a324c939b507483f783053",
    VISUAL_FILENAME: "58cbb3231ce5d09434f830b57f430171ff69479848b0795f40f92a86407d6da9",
    PROMPT_OVERLAY_FILENAME: "43680759edf68e57954bfc0c735525cfaba135f5c2594977a84f5804c7de12d4",
}

CREATOR_AUTHORIZATION_STATEMENT = (
    "I authorize Codex to install the attached V1.1 core Genesis and visual-canon "
    "JSON in D:\\AIChatBot\\Cera, verify their hashes, preserve them as immutable "
    "creator source artifacts, compile the core material into CERA’s typed "
    "authoritative JSON Genesis records, generate derived indexes/views, and run "
    "the complete Genesis validation suite. Do not activate live providers, Adult "
    "EX, SillyTavern, deployment, or production story data."
)

CHARACTER_NAMES = ("Hana", "Sakura", "Mia", "Enne", "Tomi", "Aoi", "Yuuni")
CHARACTER_IDS = {
    name: TypedId(IdKind.CHARACTER, f"{name.casefold()}_hanezawa")
    for name in CHARACTER_NAMES
}
TED_ID = TypedId(IdKind.CHARACTER, "ted")
HUSBAND_ID = TypedId(IdKind.CHARACTER, "hana_husband")
WORLD_ID = TypedId(IdKind.WORLD, "hanezawa_household")
ALL_HANEZAWA_IDS = tuple(CHARACTER_IDS.values())

PACKAGE_ID = deterministic_id(
    IdKind.GENESIS_PACKAGE, "cera.genesis.package", "hanezawa-core"
)
REVISION_ID = deterministic_id(
    IdKind.GENESIS_REVISION, "cera.genesis.revision", "hanezawa-core-v1.1"
)
AUTHORIZATION_ID = deterministic_id(
    IdKind.AUTHORIZATION, "cera.genesis.authorization", "hanezawa-core-v1.1"
)
AUTHORIZATION_SOURCE_ID = deterministic_id(
    IdKind.SOURCE, "cera.creator.conversation", "hanezawa-core-v1.1-install"
)

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
LABELED_BULLET_RE = re.compile(r"^-\s+\*\*(.+?):\*\*\s*(.*)$")
EVENT_RE = re.compile(r"^([A-Z]{2}\d{2})\s+[—-]\s+(.+)$")
MEMORY_RE = re.compile(r"^([HSMETAY]-M\d{2})\s+[—-]\s+(.+)$")
CHARACTER_HEADING_RE = re.compile(r"^5\.(\d)\s+([A-Za-z]+)\s+Hanezawa\b")
PROTECTED_WORDS = re.compile(
    r"\b(?:adult-private|sexual|assault|abduction|masturbation|nude|BDSM|arousal|M capacity)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class MarkdownSection:
    level: int
    title: str
    heading_line: int
    end_line: int
    body: str

    @property
    def line_start(self) -> int:
        return self.heading_line + 1


class MarkdownDocument:
    def __init__(self, text: str) -> None:
        self.lines = text.splitlines()
        headings: list[tuple[int, str, int]] = []
        for index, line in enumerate(self.lines):
            match = HEADING_RE.match(line)
            if match:
                headings.append((len(match.group(1)), match.group(2), index))
        sections: list[MarkdownSection] = []
        for position, (level, title, start) in enumerate(headings):
            end = len(self.lines)
            for next_level, _, next_start in headings[position + 1 :]:
                if next_level <= level:
                    end = next_start
                    break
            sections.append(
                MarkdownSection(
                    level=level,
                    title=title,
                    heading_line=start,
                    end_line=end,
                    body="\n".join(self.lines[start + 1 : end]).strip(),
                )
            )
        self.sections = tuple(sections)

    def first(self, title_prefix: str, *, level: int | None = None) -> MarkdownSection:
        for section in self.sections:
            if section.title.startswith(title_prefix) and (
                level is None or section.level == level
            ):
                return section
        raise ValueError(f"missing Markdown section: {title_prefix}")

    def children(
        self, parent: MarkdownSection, *, level: int
    ) -> tuple[MarkdownSection, ...]:
        return tuple(
            section
            for section in self.sections
            if section.level == level
            and parent.heading_line < section.heading_line < parent.end_line
        )


def parse_labeled_bullets(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    current: str | None = None
    for raw_line in body.splitlines():
        line = raw_line.strip()
        match = LABELED_BULLET_RE.match(line)
        if match:
            current = match.group(1).strip()
            fields[current] = match.group(2).strip()
        elif current and line and not line.startswith(("#", "|", ">")):
            fields[current] = f"{fields[current]} {line}".strip()
    return fields


def parse_table(body: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells or all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue
        if cells[0] in {"Sin", "Older or conflicting premise"}:
            continue
        rows.append(cells)
    return rows


def plain_bullets(body: str) -> tuple[str, ...]:
    return tuple(
        line.strip()[2:].strip()
        for line in body.splitlines()
        if line.strip().startswith("- ") and not line.strip().startswith("- **")
    )


def first_meaningful_line(body: str, fallback: str) -> str:
    fields = parse_labeled_bullets(body)
    if fields:
        return next(iter(fields.values()))
    for line in body.splitlines():
        value = line.strip().lstrip("> ")
        if value and not value.startswith(("|", "```", "**")):
            return value
    return fallback


def section_payload(
    section: MarkdownSection,
    core_sha256: str,
    *,
    core_filename: str = CORE_FILENAME,
) -> dict[str, Any]:
    return {
        "heading": section.title,
        "source_text": section.body,
        "source": source_ref(
            section,
            core_sha256,
            core_filename=core_filename,
        ),
    }


def source_ref(
    section: MarkdownSection,
    core_sha256: str,
    *,
    core_filename: str = CORE_FILENAME,
) -> dict[str, Any]:
    return {
        "artifact": core_filename,
        "sha256": core_sha256,
        "line_start": section.line_start,
        "line_end": section.end_line,
        "heading": section.title,
    }


def subject_ids(text: str) -> tuple[TypedId, ...]:
    found: list[TypedId] = []
    for name in CHARACTER_NAMES:
        if re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            found.append(CHARACTER_IDS[name])
    if re.search(r"\bTed\b", text, re.IGNORECASE):
        found.append(TED_ID)
    if re.search(r"\bhusband\b|\bfather\b", text, re.IGNORECASE):
        found.append(HUSBAND_ID)
    return unique_ids(found)


def knowledge_ids(text: str) -> tuple[TypedId, ...]:
    lowered = text.casefold()
    if "known to all" in lowered or "all family" in lowered or "all members" in lowered:
        return ALL_HANEZAWA_IDS
    return subject_ids(text)


def unique_ids(values: Iterable[TypedId]) -> tuple[TypedId, ...]:
    return tuple(dict.fromkeys(values))


def content_class(text: str) -> str:
    return "protected_non_graphic" if PROTECTED_WORDS.search(text) else "ordinary"


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return normalized[:100] or "section"


def limit_adult_wording(value: str) -> str:
    seen = False

    def replace(match: re.Match[str]) -> str:
        nonlocal seen
        if not seen:
            seen = True
            return match.group(0)
        return ""

    normalized = re.sub(r"\badult\b", replace, value, flags=re.IGNORECASE)
    return " ".join(normalized.replace(" ,", ",").split())


def validate_overlay(overlay: dict[str, Any]) -> None:
    if overlay.get("negative_prompt_is_exact") is not True:
        raise ValueError("visual overlay must define an exact negative prompt")
    if "loli" in overlay["negative_prompt"].casefold():
        raise ValueError("creator visual negative prompt must not contain loli")
    required = ("masterpiece", "very aesthetic", "@Ani2rel", "v0q1d", "Cyaniji")
    if not all(token in overlay["positive_prefix"] for token in required):
        raise ValueError("creator visual positive prefix is incomplete")


def character_section_is_private(title: str) -> bool:
    lowered = title.casefold()
    return any(
        token in lowered
        for token in (
            "self-perception",
            "husband and loneliness",
            "adult-private",
            "secret burdens",
            "current male nonresponse",
            "initial interest",
            "hidden victory",
            "teacher manipulation",
            "ted objection",
            "youngest-sister identity",
            "emotional insight",
        )
    )


def module_source_id(name: str) -> TypedId:
    return deterministic_id(IdKind.SOURCE, "cera.genesis.hanezawa.v1.1.module", name)


def record_id(key: str) -> TypedId:
    return deterministic_id(IdKind.RECORD, "cera.genesis.hanezawa.v1.1.record", key)


def module(
    source_id: TypedId, title: str, records: Iterable[GenesisRecord]
) -> GenesisModule:
    return GenesisModule(
        schema_version=GenesisModule.SCHEMA_VERSION,
        source_id=source_id,
        package_class=GenesisPackageClass.CREATOR_CANON,
        title=title,
        records=tuple(records),
    )


def objective_record(
    source_id: TypedId,
    key: str,
    record_type: GenesisRecordType,
    claim: str,
    subject_ids_value: tuple[TypedId, ...],
    payload: dict[str, Any],
    *,
    tags: tuple[str, ...],
    visibility: Visibility,
    knowledge_owner_ids: tuple[TypedId, ...] = (),
    content_class: str = "ordinary",
    epistemic_layer: EpistemicLayer = EpistemicLayer.OBJECTIVE_FACT,
    adult_eligibility: AdultEligibility = AdultEligibility.NOT_APPLICABLE,
    story_start_presence: StoryStartPresence = StoryStartPresence.NOT_APPLICABLE,
    supersedes: tuple[TypedId, ...] = (),
    record_version: int = 1,
    record_id_factory: Callable[[str], TypedId] = record_id,
) -> GenesisRecord:
    return GenesisRecord(
        schema_version=GenesisRecord.SCHEMA_VERSION,
        record_id=record_id_factory(key),
        record_version=record_version,
        record_type=record_type,
        epistemic_layer=epistemic_layer,
        truth_status=TruthStatus.OBJECTIVE,
        claim=claim,
        authority=EvidenceAuthority.CREATOR,
        subject_ids=unique_ids(subject_ids_value),
        owner_id=None,
        knowledge_owner_ids=unique_ids(knowledge_owner_ids),
        visibility=visibility,
        knowledge_route=KnowledgeRoute.CREATOR_SEED,
        certainty=Certainty.ESTABLISHED,
        content_class=content_class,
        adult_eligibility=adult_eligibility,
        story_start_presence=story_start_presence,
        relationship_from_id=None,
        relationship_to_id=None,
        source_refs=(source_id,),
        valid_from=None,
        valid_to=None,
        supersedes=supersedes,
        tags=tuple(dict.fromkeys(tags)),
        expandable_sections=tuple(slug(key) for key in payload if key != "source"),
        payload_json=canonical_json(payload),
    )


def pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(to_primitive(value), ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    ).encode("utf-8")
