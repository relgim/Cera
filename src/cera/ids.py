"""Opaque typed identities used across CERA contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from uuid import UUID, uuid4, uuid5

from .errors import IdentityError


class IdKind(StrEnum):
    WORLD = "world"
    REQUEST = "request"
    SESSION = "session"
    BRANCH = "branch"
    ARTIFACT = "artifact"
    GENERATION = "generation"
    CHARACTER = "character"
    SOURCE = "source"
    SOURCE_UNIT = "source_unit"
    SOURCE_CLAIM = "source_claim"
    PROTECTED_SOURCE = "protected_source"
    PROTECTED_SOURCE_SEGMENT = "protected_source_segment"
    DECISION = "decision"
    TRACE = "trace"
    VALIDATION = "validation"
    TRANSACTION = "transaction"
    EVIDENCE = "evidence"
    OBLIGATION = "obligation"
    RECORD = "record"
    SNAPSHOT = "snapshot"
    GENESIS_REVISION = "genesis_revision"
    GENESIS_PACKAGE = "genesis_package"
    GENESIS_FINDING = "genesis_finding"
    AUTHORIZATION = "authorization"
    GENESIS_RECEIPT = "genesis_receipt"
    SEED_RECEIPT = "seed_receipt"
    LOOKUP_RECEIPT = "lookup_receipt"
    PROVIDER_RECEIPT = "provider_receipt"
    MCP_BRIDGE_RECEIPT = "mcp_bridge_receipt"
    COMPOSER_CANDIDATE = "composer_candidate"
    REALIZATION_MANIFEST = "realization_manifest"
    REALIZATION_VERIFICATION = "realization_verification"
    FAILURE_BUNDLE = "failure_bundle"
    STAGE_JOURNAL = "stage_journal"
    RENDERED_ARTIFACT = "rendered_artifact"
    POST_PUBLICATION_WORK = "post_publication_work"
    BEAT = "beat"
    SCENE_BLOCK = "scene_block"
    WRITER_SCAFFOLD = "writer_scaffold"
    SEGMENT = "segment"
    ADULT_PLAN = "adult_plan"
    ADULT_AUTHORITY = "adult_authority"
    ADULT_CONTEXT = "adult_context"
    ADULT_CRAFT_NEED = "adult_craft_need"
    CRAFT_REFERENCE = "craft_reference"
    CRAFT_CATALOG = "craft_catalog"
    CRAFT_SELECTION = "craft_selection"
    REALIZATION_SECTION = "realization_section"
    SPECIFICITY_CONTRACT = "specificity_contract"
    SPECIFICITY_VALIDATION = "specificity_validation"
    SEMANTIC_SPECIFICITY = "semantic_specificity"
    BEAT_REPAIR = "beat_repair"
    CHECKPOINT = "checkpoint"
    REJECTION = "rejection"
    PROJECTION = "projection"
    EXTERNAL_REQUEST = "external_request"
    EXTERNAL_RECEIPT = "external_receipt"
    EXTERNAL_STEP = "external_step"
    EVENT = "event"
    MEMORY = "memory"
    RELATIONSHIP = "relationship"
    THREAD = "thread"
    MATERIAL = "material"
    DEVELOPMENT = "development"
    COMMIT = "commit"
    EVALUATION_CASE = "evaluation_case"
    EVALUATION_RUN = "evaluation_run"
    EVALUATION_FINDING = "evaluation_finding"
    REVIEW_PACKET = "review_packet"
    REVIEW_BALLOT = "review_ballot"
    REVIEW_FINDING = "review_finding"
    PREPARED_PUBLICATION = "prepared_publication"
    CREATOR_DECISION = "creator_decision"
    PROMOTION_ASSESSMENT = "promotion_assessment"
    TELEMETRY_EVENT = "telemetry_event"
    CONTEXT_DELTA = "context_delta"
    CREATOR_CONSTRAINT = "creator_constraint"
    SESSION_RECEIPT = "session_receipt"


# Every durable authority/evidence record keeps its semantic ID kind instead
# of being coerced to a generic ``record:`` identity.  Contracts that point to
# an authoritative record must use this shared set so event and memory
# continuity cannot disagree between storage, retrieval, and composition.
AUTHORITY_RECORD_ID_KIND_ORDER = (
    IdKind.RECORD,
    IdKind.EVENT,
    IdKind.MEMORY,
    IdKind.RELATIONSHIP,
    IdKind.THREAD,
    IdKind.MATERIAL,
    IdKind.DEVELOPMENT,
)
AUTHORITY_RECORD_ID_KINDS = frozenset(AUTHORITY_RECORD_ID_KIND_ORDER)


_ID_PATTERN = re.compile(
    r"^(?P<kind>[a-z][a-z0-9_]*):(?P<value>[A-Za-z0-9][A-Za-z0-9._-]{0,127})$"
)
_DETERMINISTIC_NAMESPACE = UUID("1ec9c634-4b5e-4df0-8d5e-7204eb43e4e2")


@dataclass(frozen=True, slots=True, order=True)
class TypedId:
    kind: IdKind
    value: str

    def __post_init__(self) -> None:
        candidate = f"{self.kind.value}:{self.value}"
        if not _ID_PATTERN.fullmatch(candidate):
            raise IdentityError(f"malformed typed ID: {candidate!r}")

    def __str__(self) -> str:
        return f"{self.kind.value}:{self.value}"

    @classmethod
    def parse(cls, value: str, expected_kind: IdKind | None = None) -> "TypedId":
        if not isinstance(value, str):
            raise IdentityError("typed ID must be a string")
        match = _ID_PATTERN.fullmatch(value)
        if match is None:
            raise IdentityError(f"malformed typed ID: {value!r}")
        try:
            kind = IdKind(match.group("kind"))
        except ValueError as exc:
            raise IdentityError(f"unknown typed ID kind: {value!r}") from exc
        if expected_kind is not None and kind is not expected_kind:
            raise IdentityError(
                f"expected {expected_kind.value} ID, received {kind.value} ID"
            )
        return cls(kind=kind, value=match.group("value"))


def new_id(kind: IdKind) -> TypedId:
    return TypedId(kind=kind, value=str(uuid4()))


def deterministic_id(kind: IdKind, namespace: str, external_key: str) -> TypedId:
    if not namespace.strip() or not external_key.strip():
        raise IdentityError("deterministic ID namespace and key must be non-empty")
    value = uuid5(_DETERMINISTIC_NAMESPACE, f"{kind.value}:{namespace}:{external_key}")
    return TypedId(kind=kind, value=str(value))


def require_kind(value: TypedId, expected_kind: IdKind, field_name: str) -> None:
    if value.kind is not expected_kind:
        raise IdentityError(
            f"{field_name} requires {expected_kind.value}, received {value.kind.value}"
        )


def require_authority_record_kind(value: TypedId, field_name: str) -> None:
    if value.kind not in AUTHORITY_RECORD_ID_KINDS:
        raise IdentityError(
            f"{field_name} requires an authority-record ID, received {value.kind.value}"
        )
