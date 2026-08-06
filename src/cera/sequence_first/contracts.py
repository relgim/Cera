"""Compact semantic contracts and private custody for sequence-first CERA.

Provider-authored objects in this module contain story semantics only.  Python
attaches world, branch, turn, candidate, hashes, and transaction custody only
after a semantic object has decoded and passed closed-reference validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import ClassVar

from cera.errors import ContractValidationError
from cera.serialization import canonical_sha256, text_sha256


LOCAL_KEY_PATTERN = r"[a-z][a-z0-9_]{0,95}"
LOCAL_KEY_JSON_PATTERN = rf"^{LOCAL_KEY_PATTERN}$"
_LOCAL_KEY = re.compile(rf"{LOCAL_KEY_PATTERN}\Z")
STABLE_IDENTITY_PATTERN = r"[a-z][a-z0-9_.:-]{0,191}"
STABLE_IDENTITY_JSON_PATTERN = rf"^{STABLE_IDENTITY_PATTERN}$"
_IDENTITY = re.compile(rf"{STABLE_IDENTITY_PATTERN}\Z")
# This is exactly the stable-identity subset whose bytes begin with the
# character namespace. Keep both JSON forms beside the authoritative Python
# validator so provider schemas cannot admit values the DTO must reject.
CHARACTER_ID_PATTERN = r"character:[a-z0-9_.:-]{0,182}"
CHARACTER_ID_JSON_PATTERN = rf"^{CHARACTER_ID_PATTERN}$"
PROTECTED_USER_ID = "character:ted"


def _text(value: str, field: str, *, maximum: int = 8_000) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ContractValidationError(f"{field} must be non-empty and bounded")


def _optional_text(value: str | None, field: str, *, maximum: int = 2_000) -> None:
    if value is not None:
        _text(value, field, maximum=maximum)


def _identity(value: str, field: str) -> None:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise ContractValidationError(f"{field} is not a stable identity")


def _character(value: str, field: str) -> None:
    _identity(value, field)
    if not value.startswith("character:"):
        raise ContractValidationError(f"{field} is not a character identity")


def _key(value: str, field: str) -> None:
    if not isinstance(value, str) or _LOCAL_KEY.fullmatch(value) is None:
        raise ContractValidationError(f"{field} is not a local key")


def _unique(values: tuple[str, ...], field: str) -> None:
    if len(values) != len(set(values)):
        raise ContractValidationError(f"{field} contains duplicates")


class SequenceRole(StrEnum):
    INTENDED = "intended"
    REALIZED = "realized"
    SAFE_RESTRICTED = "safe_restricted"


class PrimarySequenceStatus(StrEnum):
    PLANNED = "planned"
    REALIZED = "realized"


class ItemKind(StrEnum):
    ACTION = "action"
    DIALOGUE_INTENT = "dialogue_intent"
    PRIVATE_STATE = "private_state"
    PERCEPTION = "perception"
    MATERIAL_CONTINUITY = "material_continuity"
    KNOWLEDGE_CHANGE = "knowledge_change"
    RELATIONSHIP_CHANGE = "relationship_change"
    REMOTE_COMMUNICATION = "remote_communication"
    SCENE_TRANSITION = "scene_transition"
    STOPPING_BOUNDARY = "stopping_boundary"


class DurableChangeKind(StrEnum):
    MATERIAL = "material"
    KNOWLEDGE = "knowledge"
    RELATIONSHIP = "relationship"
    CHARACTER_DEVELOPMENT = "character_development"


class Visibility(StrEnum):
    PUBLIC = "public"
    CHARACTER_PRIVATE = "character_private"


class PresenceDirection(StrEnum):
    ENTER = "enter"
    LEAVE = "leave"


class TargetOperationKind(StrEnum):
    ADD = "add"
    REPLACE = "replace"


class ValidatorVerdict(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"


class ReviewSeverity(StrEnum):
    NOTICE = "notice"
    IMPORTANT = "important"
    CREATOR_DECISION = "creator_decision"


class ConflictClass(StrEnum):
    REQUIRED_ITEM_OMITTED = "required_item_omitted"
    CAUSAL_ORDER_CHANGED = "causal_order_changed"
    ACCEPTED_STATE_CONTRADICTION = "accepted_state_contradiction"
    CHARACTER_LOGIC_CONTRADICTION = "character_logic_contradiction"
    KNOWLEDGE_OR_PRIVACY_BREACH = "knowledge_or_privacy_breach"
    PRESENCE_CONTRADICTION = "presence_contradiction"
    PROTECTED_USER_INVENTION = "protected_user_invention"
    MATERIAL_ADDITION = "material_addition"
    STOPPING_BOUNDARY_CROSSED = "stopping_boundary_crossed"
    CAPABILITY_RESTRICTION = "capability_restriction"
    AUTHORITY_AMBIGUITY = "authority_ambiguity"


class ControllerFailureType(StrEnum):
    SEMANTIC_WRITER_CONFLICT = "semantic_writer_conflict"
    READER_QUALITY_REJECTION = "reader_quality_rejection"
    TRANSPORT = "transport"
    SCHEMA = "schema"
    BRANCH = "branch"
    AUTHORITY = "authority"
    ACCOUNTING = "accounting"
    CAPABILITY = "capability"


class ReaderStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


class WriterAttemptStatus(StrEnum):
    VALIDATOR_REJECTED = "validator_rejected"
    READER_REJECTED = "reader_rejected"
    ACCEPTED = "accepted"


@dataclass(frozen=True, slots=True)
class ProtectedSourceClaimV1:
    claim_key: str
    exact_text: str

    def __post_init__(self) -> None:
        _key(self.claim_key, "protected_source_claim.claim_key")
        _text(self.exact_text, "protected_source_claim.exact_text", maximum=4_000)


@dataclass(frozen=True, slots=True)
class EvidenceRecordV1:
    evidence_key: str
    subject_id: str
    visibility: Visibility
    exact_content: str
    knowledge_owner_id: str | None = None

    def __post_init__(self) -> None:
        _identity(self.evidence_key, "evidence_record.evidence_key")
        _identity(self.subject_id, "evidence_record.subject_id")
        _text(self.exact_content, "evidence_record.exact_content")
        if self.visibility is Visibility.CHARACTER_PRIVATE:
            if self.knowledge_owner_id is None:
                raise ContractValidationError("private evidence requires an owner")
            _character(self.knowledge_owner_id, "evidence_record.knowledge_owner_id")
        elif self.knowledge_owner_id is not None:
            raise ContractValidationError("public evidence cannot have a private owner")


@dataclass(frozen=True, slots=True)
class CharacterDeltaV1:
    character_id: str
    concise_delta: str
    visibility: Visibility = Visibility.PUBLIC
    knowledge_owner_id: str | None = None

    def __post_init__(self) -> None:
        _character(self.character_id, "character_delta.character_id")
        _text(self.concise_delta, "character_delta.concise_delta", maximum=4_000)
        if self.visibility is Visibility.CHARACTER_PRIVATE:
            if self.knowledge_owner_id != self.character_id:
                raise ContractValidationError(
                    "private character delta must be owned by its character"
                )
        elif self.knowledge_owner_id is not None:
            raise ContractValidationError("public character delta cannot have an owner")


@dataclass(frozen=True, slots=True)
class ApprovedTargetV1:
    """Opaque semantic handle; exact destination and revision remain private."""

    target_key: str
    allowed_change_kinds: tuple[DurableChangeKind, ...]

    def __post_init__(self) -> None:
        _identity(self.target_key, "approved_target.target_key")
        if not self.allowed_change_kinds:
            raise ContractValidationError("approved target requires an allowed change kind")
        if len(self.allowed_change_kinds) != len(set(self.allowed_change_kinds)):
            raise ContractValidationError("approved target change kinds contain duplicates")


@dataclass(frozen=True, slots=True)
class DurableChangeV1:
    change_key: str
    kind: DurableChangeKind
    subject_ids: tuple[str, ...]
    concise_change: str
    target_key: str
    visibility: Visibility = Visibility.PUBLIC
    knowledge_owner_id: str | None = None

    def __post_init__(self) -> None:
        _key(self.change_key, "durable_change.change_key")
        if not self.subject_ids:
            raise ContractValidationError("durable change requires a subject")
        _unique(self.subject_ids, "durable_change.subject_ids")
        for subject_id in self.subject_ids:
            _identity(subject_id, "durable_change.subject_ids")
        _text(self.concise_change, "durable_change.concise_change", maximum=2_000)
        _identity(self.target_key, "durable_change.target_key")
        if self.visibility is Visibility.CHARACTER_PRIVATE:
            if self.knowledge_owner_id is None:
                raise ContractValidationError("private change requires a knowledge owner")
            _character(self.knowledge_owner_id, "durable_change.knowledge_owner_id")
            if self.knowledge_owner_id not in self.subject_ids:
                raise ContractValidationError(
                    "private change owner must be one of its subjects"
                )
        elif self.knowledge_owner_id is not None:
            raise ContractValidationError("public change cannot have a private owner")


@dataclass(frozen=True, slots=True)
class SequenceItemV1:
    item_key: str
    kind: ItemKind
    concise_meaning: str
    owner_id: str | None = None
    causal_parent_item_key: str | None = None
    evidence_keys: tuple[str, ...] = ()
    protected_user_claim_keys: tuple[str, ...] = ()
    protected_user_exact_quotes: tuple[str, ...] = ()
    durable_change_keys: tuple[str, ...] = ()
    planner_item_keys: tuple[str, ...] = ()

    RESPONDER_KINDS: ClassVar[frozenset[ItemKind]] = frozenset(
        {
            ItemKind.ACTION,
            ItemKind.DIALOGUE_INTENT,
            ItemKind.PRIVATE_STATE,
            ItemKind.PERCEPTION,
            ItemKind.REMOTE_COMMUNICATION,
        }
    )
    OWNER_REQUIRED: ClassVar[frozenset[ItemKind]] = RESPONDER_KINDS

    def __post_init__(self) -> None:
        _key(self.item_key, "sequence_item.item_key")
        _text(self.concise_meaning, "sequence_item.concise_meaning", maximum=2_000)
        if self.kind in self.OWNER_REQUIRED and self.owner_id is None:
            raise ContractValidationError(f"{self.kind.value} item requires an owner")
        if self.owner_id is not None:
            _character(self.owner_id, "sequence_item.owner_id")
        if self.causal_parent_item_key is not None:
            _key(self.causal_parent_item_key, "sequence_item.causal_parent_item_key")
        for field, values, validator in (
            ("evidence_keys", self.evidence_keys, _identity),
            ("protected_user_claim_keys", self.protected_user_claim_keys, _key),
            ("durable_change_keys", self.durable_change_keys, _key),
            ("planner_item_keys", self.planner_item_keys, _key),
        ):
            _unique(values, f"sequence_item.{field}")
            for value in values:
                validator(value, f"sequence_item.{field}")
        _unique(
            self.protected_user_exact_quotes,
            "sequence_item.protected_user_exact_quotes",
        )
        for quote in self.protected_user_exact_quotes:
            _text(quote, "sequence_item.protected_user_exact_quotes", maximum=4_000)
        if self.owner_id == PROTECTED_USER_ID and not (
            self.protected_user_claim_keys or self.protected_user_exact_quotes
        ):
            raise ContractValidationError(
                "protected-user-owned item requires exact current source authority"
            )
        if self.owner_id != PROTECTED_USER_ID and (
            self.protected_user_claim_keys or self.protected_user_exact_quotes
        ):
            raise ContractValidationError(
                "protected-user source authority requires protected-user ownership"
            )


@dataclass(frozen=True, slots=True)
class PresenceChangeV1:
    character_id: str
    direction: PresenceDirection
    effective_after_item_key: str

    def __post_init__(self) -> None:
        _character(self.character_id, "presence_change.character_id")
        _key(self.effective_after_item_key, "presence_change.effective_after_item_key")


@dataclass(frozen=True, slots=True)
class SequenceDraftV1:
    """Provider-authored semantic sequence with no Python custody fields."""

    SCHEMA_VERSION: ClassVar[str] = "cera.sequence_first.sequence_draft.v4"

    items: tuple[SequenceItemV1, ...]
    durable_changes: tuple[DurableChangeV1, ...]
    presence_changes: tuple[PresenceChangeV1, ...]
    resulting_public_state: str
    unresolved_threads: tuple[str, ...]
    stopping_boundary: str

    def __post_init__(self) -> None:
        if not self.items:
            raise ContractValidationError("sequence requires at least one item")
        item_keys = tuple(item.item_key for item in self.items)
        _unique(item_keys, "sequence.item_keys")
        seen: set[str] = set()
        for item in self.items:
            if (
                item.causal_parent_item_key is not None
                and item.causal_parent_item_key not in seen
            ):
                raise ContractValidationError(
                    "item causal parent must precede the child item"
                )
            seen.add(item.item_key)
        change_keys = tuple(change.change_key for change in self.durable_changes)
        _unique(change_keys, "sequence.change_keys")
        known_changes = set(change_keys)
        for item in self.items:
            if not set(item.durable_change_keys).issubset(known_changes):
                raise ContractValidationError("item cites an unknown durable change")
        known_items = set(item_keys)
        for change in self.presence_changes:
            if change.effective_after_item_key not in known_items:
                raise ContractValidationError("presence change cites an unknown item")
        _text(self.resulting_public_state, "sequence.resulting_public_state")
        _unique(self.unresolved_threads, "sequence.unresolved_threads")
        for value in self.unresolved_threads:
            _text(value, "sequence.unresolved_threads", maximum=1_000)
        _text(self.stopping_boundary, "sequence.stopping_boundary", maximum=2_000)

    @property
    def semantic_sha256(self) -> str:
        return canonical_sha256(self)

    @property
    def responding_character_ids(self) -> tuple[str, ...]:
        """Mechanical view of behavioral Planner owner choices.

        Structural state/continuity items may identify an assertion owner for
        evidence and knowledge custody without making that character respond.
        """

        return tuple(
            dict.fromkeys(
                item.owner_id
                for item in self.items
                if item.kind in SequenceItemV1.RESPONDER_KINDS
                and item.owner_id is not None
                and item.owner_id != PROTECTED_USER_ID
            )
        )

    @property
    def covered_planner_item_keys(self) -> tuple[str, ...]:
        """Coverage is derived from realized-item references, never authored twice."""

        return tuple(
            dict.fromkeys(
                planner_key
                for item in self.items
                for planner_key in item.planner_item_keys
            )
        )


@dataclass(frozen=True, slots=True)
class SequenceFirstTurnSemanticInputV1:
    """Entire model-visible Planner request; deliberately custody-free."""

    SCHEMA_VERSION: ClassVar[str] = "cera.sequence_first.turn_semantics.v3"

    exact_current_source: str
    current_source_key: str
    protected_source_claims: tuple[ProtectedSourceClaimV1, ...]
    known_character_ids: tuple[str, ...]
    accepted_present_character_ids: tuple[str, ...]
    explicitly_authorized_remote_character_ids: tuple[str, ...]
    current_public_scene_state: str
    prior_realized_sequence: SequenceDraftV1 | None
    character_deltas: tuple[CharacterDeltaV1, ...]
    evidence_records: tuple[EvidenceRecordV1, ...]
    approved_targets: tuple[ApprovedTargetV1, ...]
    unresolved_threads: tuple[str, ...]
    hard_boundaries: tuple[str, ...]
    scene_reinitialization: bool = False

    def __post_init__(self) -> None:
        _text(self.exact_current_source, "turn_semantics.exact_current_source")
        _identity(self.current_source_key, "turn_semantics.current_source_key")
        claim_keys = tuple(claim.claim_key for claim in self.protected_source_claims)
        _unique(claim_keys, "turn_semantics.protected_source_claims")
        for claim in self.protected_source_claims:
            if claim.exact_text not in self.exact_current_source:
                raise ContractValidationError(
                    "protected source claim is not exact current source text"
                )
        for field in (
            "known_character_ids",
            "accepted_present_character_ids",
            "explicitly_authorized_remote_character_ids",
        ):
            values = getattr(self, field)
            _unique(values, f"turn_semantics.{field}")
            for value in values:
                _character(value, f"turn_semantics.{field}")
        known = set(self.known_character_ids)
        if not set(self.accepted_present_character_ids).issubset(known) or not set(
            self.explicitly_authorized_remote_character_ids
        ).issubset(known):
            raise ContractValidationError(
                "presence or remote authority cites an unknown character"
            )
        if set(self.accepted_present_character_ids).intersection(
            self.explicitly_authorized_remote_character_ids
        ):
            raise ContractValidationError("present and remote-authorized characters overlap")
        # A Stage 6 scene reinitialization may carry a non-empty presence set only
        # after the Python bridge has bound explicit initialization authority.
        # This semantic DTO cannot create that authority; it merely preserves the
        # bridge-established set and prevents inheritance of prior scene sequence.
        _text(
            self.current_public_scene_state,
            "turn_semantics.current_public_scene_state",
        )
        delta_ids = tuple(delta.character_id for delta in self.character_deltas)
        _unique(delta_ids, "turn_semantics.character_deltas")
        evidence_keys = tuple(record.evidence_key for record in self.evidence_records)
        _unique(evidence_keys, "turn_semantics.evidence_records")
        target_keys = tuple(target.target_key for target in self.approved_targets)
        _unique(target_keys, "turn_semantics.approved_targets")
        _unique(self.unresolved_threads, "turn_semantics.unresolved_threads")
        _unique(self.hard_boundaries, "turn_semantics.hard_boundaries")
        for value in (*self.unresolved_threads, *self.hard_boundaries):
            _text(value, "turn_semantics.boundary_or_thread", maximum=2_000)

    def validate_intended(self, draft: SequenceDraftV1) -> None:
        if any(item.planner_item_keys for item in draft.items):
            raise ContractValidationError("intended items cannot cite Planner item keys")
        self._validate_common_references(draft)
        present = set(self.accepted_present_character_ids)
        remote = set(self.explicitly_authorized_remote_character_ids)
        changes_by_item: dict[str, tuple[PresenceChangeV1, ...]] = {
            item.item_key: tuple(
                change
                for change in draft.presence_changes
                if change.effective_after_item_key == item.item_key
            )
            for item in draft.items
        }
        for item in draft.items:
            owner = item.owner_id
            enters_on_item = any(
                change.character_id == owner
                and change.direction is PresenceDirection.ENTER
                for change in changes_by_item[item.item_key]
            )
            if owner is not None and owner != PROTECTED_USER_ID and owner not in present:
                if not (
                    item.kind is ItemKind.REMOTE_COMMUNICATION
                    and (
                        owner in remote
                        or self.current_source_key in item.evidence_keys
                    )
                ) and not (
                    enters_on_item
                    and self.current_source_key in item.evidence_keys
                ):
                    raise ContractValidationError(
                        "absent item owner lacks remote or ordered entry authority"
                    )
            for change in changes_by_item[item.item_key]:
                if change.direction is PresenceDirection.ENTER:
                    if change.character_id in present:
                        raise ContractValidationError(
                            "presence entry duplicates a present character"
                        )
                    present.add(change.character_id)
                else:
                    if change.character_id not in present:
                        raise ContractValidationError(
                            "presence exit names an absent character"
                        )
                    present.remove(change.character_id)
        apply_presence_changes(self.accepted_present_character_ids, draft)

    def validate_realized(
        self,
        realized: SequenceDraftV1,
        *,
        intended: SequenceDraftV1,
    ) -> None:
        self._validate_common_references(realized)
        intended_keys = {item.item_key for item in intended.items}
        for item in realized.items:
            if not set(item.planner_item_keys).issubset(intended_keys):
                raise ContractValidationError(
                    "realized item cites an unknown Planner item"
                )
        if set(realized.covered_planner_item_keys) != intended_keys:
            raise ContractValidationError(
                "accepted realized sequence does not cover every intended item"
            )
        intended_changes = {
            change.change_key: canonical_sha256(change)
            for change in intended.durable_changes
        }
        realized_changes = {
            change.change_key: canonical_sha256(change)
            for change in realized.durable_changes
        }
        if realized_changes != intended_changes:
            raise ContractValidationError(
                "accepted realized sequence dropped or changed a planned durable change"
            )
        intended_presence = {
            (change.character_id, change.direction.value, change.effective_after_item_key)
            for change in intended.presence_changes
        }
        realized_item_to_planner = {
            item.item_key: item.planner_item_keys[0]
            for item in realized.items
            if len(item.planner_item_keys) == 1
        }
        realized_presence = {
            (
                change.character_id,
                change.direction.value,
                realized_item_to_planner.get(change.effective_after_item_key),
            )
            for change in realized.presence_changes
        }
        if realized_presence != intended_presence:
            raise ContractValidationError(
                "accepted realized sequence dropped or changed a planned presence change"
            )
        if realized.stopping_boundary != intended.stopping_boundary:
            raise ContractValidationError(
                "accepted realized sequence changed the planned stopping boundary"
            )
        apply_presence_changes(self.accepted_present_character_ids, realized)

    def derived_backgrounded_character_ids(
        self,
        intended: SequenceDraftV1,
    ) -> tuple[str, ...]:
        self.validate_intended(intended)
        responders = set(intended.responding_character_ids)
        return tuple(
            character_id
            for character_id in self.accepted_present_character_ids
            if character_id != PROTECTED_USER_ID and character_id not in responders
        )

    def _validate_common_references(self, draft: SequenceDraftV1) -> None:
        known_characters = set(self.known_character_ids)
        evidence_by_key = {
            record.evidence_key: record for record in self.evidence_records
        }
        protected_claim_keys = {
            claim.claim_key for claim in self.protected_source_claims
        }
        approved_targets = {
            target.target_key: set(target.allowed_change_kinds)
            for target in self.approved_targets
        }
        for item in draft.items:
            if item.owner_id is not None and item.owner_id not in known_characters:
                raise ContractValidationError("item cites an unknown character")
            allowed_evidence_keys = set(evidence_by_key).union(
                {self.current_source_key}
            )
            if not set(item.evidence_keys).issubset(allowed_evidence_keys):
                raise ContractValidationError("item cites unavailable evidence")
            if not set(item.protected_user_claim_keys).issubset(
                protected_claim_keys
            ):
                raise ContractValidationError("item cites unavailable protected source")
            for quote in item.protected_user_exact_quotes:
                if quote not in self.exact_current_source:
                    raise ContractValidationError(
                        "protected-user exact quote is absent from current source"
                    )
            for evidence_key in item.evidence_keys:
                if evidence_key == self.current_source_key:
                    continue
                record = evidence_by_key[evidence_key]
                if (
                    record.visibility is Visibility.CHARACTER_PRIVATE
                    and record.knowledge_owner_id != item.owner_id
                ):
                    raise ContractValidationError(
                        "private evidence used by a different assertion owner"
                    )
        for change in draft.durable_changes:
            if not set(change.subject_ids).issubset(known_characters):
                raise ContractValidationError(
                    "durable change cites an unknown character"
                )
            if change.target_key not in approved_targets:
                raise ContractValidationError("durable change target is not approved")
            if change.kind not in approved_targets[change.target_key]:
                raise ContractValidationError(
                    "durable change kind is not approved for its target"
                )


@dataclass(frozen=True, slots=True)
class ProviderReferenceScopeV1:
    """Python-owned finite references available to one provider operation."""

    known_character_ids: tuple[str, ...]
    evidence_keys: tuple[str, ...]
    protected_source_claim_keys: tuple[str, ...]
    approved_target_keys: tuple[str, ...]
    planner_item_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        fields_and_validators = (
            ("known_character_ids", _character),
            ("evidence_keys", _identity),
            ("protected_source_claim_keys", _key),
            ("approved_target_keys", _identity),
            ("planner_item_keys", _key),
        )
        for field_name, validator in fields_and_validators:
            values = getattr(self, field_name)
            _unique(values, f"provider_reference_scope.{field_name}")
            for value in values:
                validator(value, f"provider_reference_scope.{field_name}")

    @classmethod
    def from_turn(
        cls,
        semantic_input: SequenceFirstTurnSemanticInputV1,
        *,
        intended_sequence: SequenceDraftV1 | None = None,
    ) -> "ProviderReferenceScopeV1":
        return cls(
            known_character_ids=semantic_input.known_character_ids,
            evidence_keys=(
                semantic_input.current_source_key,
                *(record.evidence_key for record in semantic_input.evidence_records),
            ),
            protected_source_claim_keys=tuple(
                claim.claim_key for claim in semantic_input.protected_source_claims
            ),
            approved_target_keys=tuple(
                target.target_key for target in semantic_input.approved_targets
            ),
            planner_item_keys=(
                ()
                if intended_sequence is None
                else tuple(item.item_key for item in intended_sequence.items)
            ),
        )


def apply_presence_changes(
    accepted_present_character_ids: tuple[str, ...],
    draft: SequenceDraftV1,
) -> tuple[str, ...]:
    """Apply already-approved ordered changes without interpreting prose."""

    present = list(accepted_present_character_ids)
    changes_by_item: dict[str, list[PresenceChangeV1]] = {}
    for change in draft.presence_changes:
        changes_by_item.setdefault(change.effective_after_item_key, []).append(change)
    for item in draft.items:
        for change in changes_by_item.get(item.item_key, ()):
            if change.direction is PresenceDirection.ENTER:
                if change.character_id in present:
                    raise ContractValidationError("presence entry duplicates a present character")
                present.append(change.character_id)
            else:
                if change.character_id not in present:
                    raise ContractValidationError("presence exit names an absent character")
                present.remove(change.character_id)
    return tuple(present)


@dataclass(frozen=True, slots=True)
class SequenceCustodyEnvelopeV1:
    """Python-only immutable binding, never submitted for model authorship."""

    SCHEMA_VERSION: ClassVar[str] = "cera.sequence_first.custody.v1"

    request_id: str
    candidate_id: str
    world_id: str
    branch_id: str
    scene_id: str
    turn_id: str
    parent_accepted_turn_id: str | None
    exact_source_sha256: str
    accepted_head_sha256: str | None
    transaction_id: str
    persistence_targets: tuple["PersistenceTargetCustodyV1", ...] = ()

    def __post_init__(self) -> None:
        for field in (
            "request_id",
            "candidate_id",
            "world_id",
            "branch_id",
            "scene_id",
            "turn_id",
            "transaction_id",
        ):
            _identity(getattr(self, field), f"custody.{field}")
        if self.parent_accepted_turn_id is not None:
            _identity(self.parent_accepted_turn_id, "custody.parent_accepted_turn_id")
        for field in ("exact_source_sha256", "accepted_head_sha256"):
            value = getattr(self, field)
            if value is not None and (
                len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ContractValidationError(f"custody.{field} is not SHA-256")
        target_keys = tuple(target.target_key for target in self.persistence_targets)
        _unique(target_keys, "custody.persistence_targets")


@dataclass(frozen=True, slots=True)
class PersistenceTargetCustodyV1:
    """Private exact destination bound to one model-visible opaque target key."""

    target_key: str
    target_file: str
    record_class: str
    target_record_id: str
    target_subject_ids: tuple[str, ...]
    expected_file_revision: int
    operation: TargetOperationKind
    field_path: str
    expected_prior_value_sha256: str | None = None

    def __post_init__(self) -> None:
        _identity(self.target_key, "target_custody.target_key")
        _text(self.target_file, "target_custody.target_file", maximum=512)
        normalized = self.target_file.replace("\\", "/")
        if (
            normalized.startswith("/")
            or ".." in normalized.split("/")
            or not normalized.casefold().endswith(".json")
            or normalized.split("/", 1)[0]
            not in {"Characters", "Relationships"}
        ):
            raise ContractValidationError("target custody file is outside ACTIVE policy")
        if self.record_class not in {"character", "relationship"}:
            raise ContractValidationError("target custody record class is invalid")
        _identity(self.target_record_id, "target_custody.target_record_id")
        if not self.target_subject_ids:
            raise ContractValidationError("target custody requires subject IDs")
        _unique(self.target_subject_ids, "target_custody.target_subject_ids")
        for subject_id in self.target_subject_ids:
            _identity(subject_id, "target_custody.target_subject_ids")
        if self.record_class == "relationship" and self.target_subject_ids != tuple(
            sorted(self.target_subject_ids)
        ):
            raise ContractValidationError(
                "relationship target subjects must use canonical order"
            )
        if type(self.expected_file_revision) is not int or self.expected_file_revision < 1:
            raise ContractValidationError("target custody revision is invalid")
        if not self.field_path.startswith("/") or self.field_path in {
            "/_cera_revision",
            "/schema_version",
        }:
            raise ContractValidationError("target custody field path is invalid")
        if self.operation is TargetOperationKind.REPLACE:
            if self.expected_prior_value_sha256 is None or (
                len(self.expected_prior_value_sha256) != 64
                or any(
                    value not in "0123456789abcdef"
                    for value in self.expected_prior_value_sha256
                )
            ):
                raise ContractValidationError(
                    "replace target custody requires prior value SHA-256"
                )
        elif self.expected_prior_value_sha256 is not None:
            raise ContractValidationError("add target custody cannot bind a prior value")


@dataclass(frozen=True, slots=True)
class SequenceFirstTurnRequestV1:
    """Python boundary object combining separate semantics and custody."""

    semantic_input: SequenceFirstTurnSemanticInputV1
    custody: SequenceCustodyEnvelopeV1

    def __post_init__(self) -> None:
        if text_sha256(self.semantic_input.exact_current_source) != self.custody.exact_source_sha256:
            raise ContractValidationError("custody does not bind exact current source")
        semantic_target_keys = {
            target.target_key for target in self.semantic_input.approved_targets
        }
        custody_target_keys = {
            target.target_key for target in self.custody.persistence_targets
        }
        if semantic_target_keys != custody_target_keys:
            raise ContractValidationError(
                "model-visible targets do not match private persistence custody"
            )


@dataclass(frozen=True, slots=True)
class BoundSequenceV1:
    """Python-attached binding created only after semantic validation."""

    role: SequenceRole
    semantic: SequenceDraftV1
    custody: SequenceCustodyEnvelopeV1

    @property
    def binding_sha256(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class VoiceCueV1:
    character_id: str
    concise_voice_cue: str

    def __post_init__(self) -> None:
        _character(self.character_id, "voice_cue.character_id")
        _text(self.concise_voice_cue, "voice_cue.concise_voice_cue", maximum=1_000)


@dataclass(frozen=True, slots=True)
class SequenceFirstWriterBriefV1:
    """Model-visible semantic Writer input with no persistence authority."""

    SCHEMA_VERSION: ClassVar[str] = "cera.sequence_first.writer_brief.v4"

    intended_sequence: SequenceDraftV1
    current_public_scene_state: str
    protected_source_claims: tuple[ProtectedSourceClaimV1, ...]
    voice_cues: tuple[VoiceCueV1, ...]
    backgrounded_character_ids: tuple[str, ...]
    hard_boundaries: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.current_public_scene_state, "writer_brief.current_public_scene_state")
        cue_ids = tuple(cue.character_id for cue in self.voice_cues)
        _unique(cue_ids, "writer_brief.voice_cues")
        if not set(cue_ids).issubset(self.intended_sequence.responding_character_ids):
            raise ContractValidationError("Writer voice cue belongs to a non-responder")
        for character_id in self.backgrounded_character_ids:
            _character(character_id, "writer_brief.backgrounded_character_ids")
        _unique(
            self.backgrounded_character_ids,
            "writer_brief.backgrounded_character_ids",
        )
        if PROTECTED_USER_ID in self.backgrounded_character_ids:
            raise ContractValidationError("protected user cannot be a backgrounded NPC")
        if set(self.backgrounded_character_ids).intersection(
            self.intended_sequence.responding_character_ids
        ):
            raise ContractValidationError(
                "Writer backgrounded NPC also owns a behavioral item"
            )
        _unique(self.hard_boundaries, "writer_brief.hard_boundaries")
        for value in self.hard_boundaries:
            _text(value, "writer_brief.hard_boundaries", maximum=2_000)

    @property
    def stopping_boundary(self) -> str:
        """Derived compatibility view; never serialized as duplicate authority."""

        return self.intended_sequence.stopping_boundary


@dataclass(frozen=True, slots=True)
class WriterResponseV1:
    """The entire normal DeepSeek response wire: exactly two fields."""

    SCHEMA_VERSION: ClassVar[str] = "cera.scene_writer_draft.v1"

    schema_version: str
    story_text: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("Writer response schema changed")
        _text(self.story_text, "writer_response.story_text", maximum=100_000)


@dataclass(frozen=True, slots=True)
class ReviewFlagV1:
    flag_code: str
    severity: ReviewSeverity
    concise_explanation: str

    def __post_init__(self) -> None:
        _key(self.flag_code, "review_flag.flag_code")
        _text(self.concise_explanation, "review_flag.concise_explanation", maximum=1_000)


@dataclass(frozen=True, slots=True)
class ValidationConflictV1:
    conflict_class: ConflictClass
    concise_explanation: str
    exact_quote: str | None = None
    omitted_planner_item_key: str | None = None

    def __post_init__(self) -> None:
        _text(self.concise_explanation, "validation_conflict.concise_explanation", maximum=2_000)
        _optional_text(self.exact_quote, "validation_conflict.exact_quote", maximum=1_000)
        if self.omitted_planner_item_key is not None:
            _key(
                self.omitted_planner_item_key,
                "validation_conflict.omitted_planner_item_key",
            )
        if self.exact_quote is None and self.omitted_planner_item_key is None:
            raise ContractValidationError(
                "validation conflict requires an exact quote or omitted item"
            )


@dataclass(frozen=True, slots=True)
class ValidatorDecisionV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.sequence_first.validator_decision.v3"

    verdict: ValidatorVerdict
    realized_sequence: SequenceDraftV1 | None
    review_flags: tuple[ReviewFlagV1, ...] = ()
    conflict: ValidationConflictV1 | None = None

    def __post_init__(self) -> None:
        if self.verdict is ValidatorVerdict.ACCEPT:
            if self.realized_sequence is None or self.conflict is not None:
                raise ContractValidationError("accepted Validator branch is invalid")
        elif self.realized_sequence is not None or self.conflict is None:
            raise ContractValidationError("rejected Validator branch is invalid")
        flag_codes = tuple(flag.flag_code for flag in self.review_flags)
        _unique(flag_codes, "validator_decision.review_flags")


def writer_retry_eligible(
    *,
    failure_type: ControllerFailureType,
    decision: ValidatorDecisionV1 | None = None,
) -> bool:
    """Python policy derived from closed conflict/controller classes."""

    if failure_type is ControllerFailureType.READER_QUALITY_REJECTION:
        return True
    if failure_type is not ControllerFailureType.SEMANTIC_WRITER_CONFLICT:
        return False
    if decision is None or decision.verdict is not ValidatorVerdict.REJECT:
        return False
    assert decision.conflict is not None
    return decision.conflict.conflict_class not in {
        ConflictClass.CAPABILITY_RESTRICTION,
        ConflictClass.AUTHORITY_AMBIGUITY,
    }


@dataclass(frozen=True, slots=True)
class SequenceFirstValidatorInputV1:
    """Candidate-specific semantic request; no custody envelope."""

    intended_sequence: SequenceDraftV1
    exact_writer_prose: str
    exact_current_source: str
    prior_realized_sequence: SequenceDraftV1 | None
    accepted_present_character_ids: tuple[str, ...]
    current_public_scene_state: str
    protected_source_claims: tuple[ProtectedSourceClaimV1, ...]
    hard_boundaries: tuple[str, ...]
    reference_scope: ProviderReferenceScopeV1
    accepted_character_deltas: tuple[CharacterDeltaV1, ...] = ()
    accepted_evidence_records: tuple[EvidenceRecordV1, ...] = ()

    def __post_init__(self) -> None:
        _text(self.exact_writer_prose, "validator_input.exact_writer_prose", maximum=100_000)
        _text(
            self.exact_current_source,
            "validator_input.exact_current_source",
            maximum=100_000,
        )
        _text(self.current_public_scene_state, "validator_input.current_public_scene_state")


@dataclass(frozen=True, slots=True)
class ReaderIssueV1:
    issue_code: str
    concise_explanation: str
    exact_quote: str | None = None

    def __post_init__(self) -> None:
        _key(self.issue_code, "reader_issue.issue_code")
        _text(self.concise_explanation, "reader_issue.concise_explanation", maximum=1_000)
        _optional_text(self.exact_quote, "reader_issue.exact_quote", maximum=1_000)


@dataclass(frozen=True, slots=True)
class ReaderVerdictV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.sequence_first.reader_verdict.v1"

    status: ReaderStatus
    issues: tuple[ReaderIssueV1, ...] = ()

    def __post_init__(self) -> None:
        if self.status is ReaderStatus.ACCEPTED and self.issues:
            raise ContractValidationError("accepted Reader verdict cannot contain issues")
        if self.status is not ReaderStatus.ACCEPTED and not self.issues:
            raise ContractValidationError("non-accepted Reader verdict requires issues")


@dataclass(frozen=True, slots=True)
class SequenceFirstReaderInputV1:
    exact_writer_prose: str
    intended_sequence: SequenceDraftV1
    realized_sequence: SequenceDraftV1

    def __post_init__(self) -> None:
        _text(self.exact_writer_prose, "reader_input.exact_writer_prose", maximum=100_000)


@dataclass(frozen=True, slots=True)
class WriterRetryFeedbackV1:
    """Typed, noncanonical correction metadata for a fresh Writer attempt."""

    owner: str
    feedback_type: str
    issue_code: str
    concise_reason: str
    required_correction: str
    exact_quote: str | None = None
    omitted_planner_item_key: str | None = None

    def __post_init__(self) -> None:
        if self.owner not in {"validator", "reader"}:
            raise ContractValidationError("retry feedback owner is invalid")
        _key(self.feedback_type, "retry_feedback.feedback_type")
        _key(self.issue_code, "retry_feedback.issue_code")
        _text(self.concise_reason, "retry_feedback.concise_reason", maximum=1_000)
        _text(self.required_correction, "retry_feedback.required_correction", maximum=1_000)
        _optional_text(self.exact_quote, "retry_feedback.exact_quote", maximum=1_000)
        if self.omitted_planner_item_key is not None:
            _key(self.omitted_planner_item_key, "retry_feedback.omitted_planner_item_key")
        if (self.exact_quote is None) == (self.omitted_planner_item_key is None):
            raise ContractValidationError(
                "retry feedback requires exactly one quote or omitted Planner item"
            )


@dataclass(frozen=True, slots=True)
class WriterAttemptReceiptV1:
    attempt_number: int
    status: WriterAttemptStatus
    writer_prose_sha256: str
    concise_reason: str | None = None
    retry_feedback_sha256: str | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.attempt_number <= 3:
            raise ContractValidationError("Writer attempt number is outside the bounded route")
        if len(self.writer_prose_sha256) != 64 or any(
            value not in "0123456789abcdef" for value in self.writer_prose_sha256
        ):
            raise ContractValidationError("Writer attempt hash is invalid")
        _optional_text(self.concise_reason, "writer_attempt.concise_reason", maximum=1_000)
        if self.retry_feedback_sha256 is not None and (
            len(self.retry_feedback_sha256) != 64
            or any(value not in "0123456789abcdef" for value in self.retry_feedback_sha256)
        ):
            raise ContractValidationError("Writer retry feedback hash is invalid")
        if self.status is WriterAttemptStatus.ACCEPTED and self.concise_reason is not None:
            raise ContractValidationError("accepted Writer attempt cannot have a rejection reason")


@dataclass(frozen=True, slots=True)
class SequenceFirstCandidateV1:
    """Python-bound provisional candidate; still noncanonical until acceptance."""

    SCHEMA_VERSION: ClassVar[str] = "cera.sequence_first.candidate.v2"

    intended_sequence: BoundSequenceV1
    accepted_present_character_ids: tuple[str, ...]
    writer_response: WriterResponseV1
    realized_sequence: BoundSequenceV1
    reader_verdict: ReaderVerdictV1
    accepted_attempt_number: int

    def __post_init__(self) -> None:
        if self.intended_sequence.role is not SequenceRole.INTENDED:
            raise ContractValidationError("candidate lacks intended binding")
        _unique(
            self.accepted_present_character_ids,
            "candidate.accepted_present_character_ids",
        )
        for character_id in self.accepted_present_character_ids:
            _character(character_id, "candidate.accepted_present_character_ids")
        if self.realized_sequence.role not in {
            SequenceRole.REALIZED,
            SequenceRole.SAFE_RESTRICTED,
        }:
            raise ContractValidationError("candidate lacks realized binding")
        if self.intended_sequence.custody != self.realized_sequence.custody:
            raise ContractValidationError("candidate sequence custody changed")
        if self.reader_verdict.status is not ReaderStatus.ACCEPTED:
            raise ContractValidationError("candidate Reader verdict is not accepted")
        if not 1 <= self.accepted_attempt_number <= 3:
            raise ContractValidationError("candidate accepted attempt is invalid")

    @property
    def custody(self) -> SequenceCustodyEnvelopeV1:
        return self.realized_sequence.custody

    @property
    def prose_sha256(self) -> str:
        return text_sha256(self.writer_response.story_text)

    @property
    def candidate_sha256(self) -> str:
        return canonical_sha256(self)
