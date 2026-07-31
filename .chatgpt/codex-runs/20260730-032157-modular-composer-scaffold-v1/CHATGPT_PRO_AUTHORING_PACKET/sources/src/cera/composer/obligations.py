"""Canonical request-local Composer output obligations and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId, require_kind
from cera.serialization import domain_sha256

from .models import SceneComposerRequest


class CoverageObligationMode(StrEnum):
    MUST_BE_EMPTY = "must_be_empty"
    EXACT_SEQUENCE = "exact_sequence"


class ComposerOutputDiagnosticCode(StrEnum):
    MISSING_SELECTED_NPC_OWNER = "MISSING_SELECTED_NPC_OWNER"
    REALIZATION_AUTHORITY_OWNER_MISMATCH = (
        "REALIZATION_AUTHORITY_OWNER_MISMATCH"
    )
    BEAT_AUTHORITY_RELATION_MISMATCH = (
        "BEAT_AUTHORITY_RELATION_MISMATCH"
    )
    SOURCE_COVERAGE_NOT_EMPTY = "SOURCE_COVERAGE_NOT_EMPTY"
    SOURCE_COVERAGE_SEQUENCE_MISMATCH = (
        "SOURCE_COVERAGE_SEQUENCE_MISMATCH"
    )
    SPECIFICITY_COVERAGE_NOT_EMPTY = "SPECIFICITY_COVERAGE_NOT_EMPTY"
    SPECIFICITY_COVERAGE_SEQUENCE_MISMATCH = (
        "SPECIFICITY_COVERAGE_SEQUENCE_MISMATCH"
    )
    TERMINAL_SEGMENT_RULE_FAILED = "TERMINAL_SEGMENT_RULE_FAILED"
    SOURCE_COVERAGE_DUPLICATE = "SOURCE_COVERAGE_DUPLICATE"
    SOURCE_COVERAGE_SEGMENT_KEYS_EMPTY = (
        "SOURCE_COVERAGE_SEGMENT_KEYS_EMPTY"
    )
    SPECIFICITY_COVERAGE_DUPLICATE = "SPECIFICITY_COVERAGE_DUPLICATE"
    SPECIFICITY_COVERAGE_SEGMENT_KEYS_EMPTY = (
        "SPECIFICITY_COVERAGE_SEGMENT_KEYS_EMPTY"
    )
    REALIZATION_ASSOCIATION_DUPLICATE = (
        "REALIZATION_ASSOCIATION_DUPLICATE"
    )
    STORY_SEGMENT_DUPLICATE = "STORY_SEGMENT_DUPLICATE"
    TYPED_DECODING_FAILED = "TYPED_DECODING_FAILED"


@dataclass(frozen=True, slots=True)
class AuthorityOwnerAssociation:
    authority_id: TypedId
    owner_id: TypedId

    def __post_init__(self) -> None:
        if self.authority_id.kind not in {
            IdKind.SOURCE_UNIT,
            IdKind.BEAT,
        }:
            raise ContractValidationError(
                "Composer authority-owner association has invalid authority"
            )
        require_kind(self.owner_id, IdKind.CHARACTER, "owner_id")

    def to_provider_dict(self) -> dict[str, str]:
        return {
            "authority_id": str(self.authority_id),
            "owner_id": str(self.owner_id),
        }


@dataclass(frozen=True, slots=True)
class OrderedCoverageObligation:
    mode: CoverageObligationMode
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.values) != len(set(self.values)):
            raise ContractValidationError(
                "coverage obligation values must be distinct"
            )
        if (
            self.mode is CoverageObligationMode.MUST_BE_EMPTY
            and self.values
        ):
            raise ContractValidationError(
                "must-be-empty coverage cannot advertise values"
            )
        if (
            self.mode is CoverageObligationMode.EXACT_SEQUENCE
            and not self.values
        ):
            raise ContractValidationError(
                "exact-sequence coverage requires values"
            )

    def to_provider_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "values": list(self.values),
            "each_returned_entry_requires_one_or_more_existing_segment_keys": (
                True
            ),
            "segment_keys_must_be_ordered_and_distinct": True,
        }


@dataclass(frozen=True, slots=True)
class ComposerOutputObligations:
    protected_user_id: TypedId
    selected_npc_ids: tuple[TypedId, ...]
    required_beat_associations: tuple[AuthorityOwnerAssociation, ...]
    allowed_realization_associations: tuple[
        AuthorityOwnerAssociation, ...
    ]
    source_coverage: OrderedCoverageObligation
    specificity_coverage: OrderedCoverageObligation
    terminal_segment_key_must_name_final_segment: bool

    def __post_init__(self) -> None:
        require_kind(
            self.protected_user_id,
            IdKind.CHARACTER,
            "protected_user_id",
        )
        if not self.selected_npc_ids:
            raise ContractValidationError(
                "Composer output obligations require selected NPCs"
            )
        for value in self.selected_npc_ids:
            require_kind(value, IdKind.CHARACTER, "selected_npc_ids")
        _unique_ids(self.selected_npc_ids, "selected NPC IDs")
        _unique_associations(
            self.required_beat_associations,
            "required beat associations",
        )
        _unique_associations(
            self.allowed_realization_associations,
            "allowed realization associations",
        )
        if not set(self.required_beat_associations).issubset(
            set(self.allowed_realization_associations)
        ):
            raise ContractValidationError(
                "required beat associations must be allowed"
            )
        if not self.terminal_segment_key_must_name_final_segment:
            raise ContractValidationError(
                "active Composer contract requires final terminal segment"
            )

    @property
    def obligation_sha256(self) -> str:
        return domain_sha256("cera.composer_output_obligations.v1", self)

    def to_provider_dict(self) -> dict[str, object]:
        return {
            "schema_version": "cera.composer_output_obligations.v1",
            "obligation_sha256": self.obligation_sha256,
            "protected_user_id": str(self.protected_user_id),
            "selected_npc_ids_requiring_realization": [
                str(value) for value in self.selected_npc_ids
            ],
            "required_beat_authority_owner_associations": [
                value.to_provider_dict()
                for value in self.required_beat_associations
            ],
            "allowed_realization_authority_owner_associations": [
                value.to_provider_dict()
                for value in self.allowed_realization_associations
            ],
            "source_coverage": self.source_coverage.to_provider_dict(),
            "specificity_coverage": (
                self.specificity_coverage.to_provider_dict()
            ),
            "terminal_segment_key_must_name_final_segment": True,
        }


def build_composer_output_obligations(
    request: SceneComposerRequest,
) -> ComposerOutputObligations:
    decision = request.reasoner_outcome.decision
    assert decision is not None
    required_beats = tuple(
        AuthorityOwnerAssociation(value.beat_id, value.actor_id)
        for value in decision.current_segment.ordered_beats
    )
    source_associations = tuple(
        AuthorityOwnerAssociation(
            value.source_unit_id,
            request.prepared_turn.request.protected_user_id,
        )
        for value in request.source_packet.units
    )
    source_values = (
        tuple(
            str(value.source_unit_id)
            for value in request.source_packet.units
        )
        if request.creator_event_coverage_required
        else ()
    )
    specificity_values = (
        tuple(
            value.obligation_key
            for value in request.specificity_contract.beat_requirements
        )
        if request.specificity_contract is not None
        else ()
    )
    return ComposerOutputObligations(
        protected_user_id=request.prepared_turn.request.protected_user_id,
        selected_npc_ids=request.selected_npc_ids,
        required_beat_associations=required_beats,
        allowed_realization_associations=(
            *source_associations,
            *required_beats,
        ),
        source_coverage=OrderedCoverageObligation(
            (
                CoverageObligationMode.EXACT_SEQUENCE
                if source_values
                else CoverageObligationMode.MUST_BE_EMPTY
            ),
            source_values,
        ),
        specificity_coverage=OrderedCoverageObligation(
            (
                CoverageObligationMode.EXACT_SEQUENCE
                if specificity_values
                else CoverageObligationMode.MUST_BE_EMPTY
            ),
            specificity_values,
        ),
        terminal_segment_key_must_name_final_segment=True,
    )


def evaluate_draft_obligations(
    response: Any,
    obligations: ComposerOutputObligations,
) -> tuple[ComposerOutputDiagnosticCode, ...]:
    """Return every safe atomic mismatch without retaining provider values."""

    diagnostics: list[ComposerOutputDiagnosticCode] = []
    actual_associations = tuple(
        AuthorityOwnerAssociation(
            _realization_authority(value),
            value.owner_id,
        )
        for value in response.realization_segments
    )
    actual_association_set = set(actual_associations)
    allowed = set(obligations.allowed_realization_associations)
    if not actual_association_set.issubset(allowed):
        diagnostics.append(
            ComposerOutputDiagnosticCode
            .REALIZATION_AUTHORITY_OWNER_MISMATCH
        )
    actual_beat_associations = {
        value
        for value in actual_association_set
        if value.authority_id.kind is IdKind.BEAT
    }
    if actual_beat_associations != set(
        obligations.required_beat_associations
    ):
        diagnostics.append(
            ComposerOutputDiagnosticCode
            .BEAT_AUTHORITY_RELATION_MISMATCH
        )
    realized_owners = {
        value.owner_id for value in actual_association_set
    }
    if not set(obligations.selected_npc_ids).issubset(realized_owners):
        diagnostics.append(
            ComposerOutputDiagnosticCode.MISSING_SELECTED_NPC_OWNER
        )

    actual_source = tuple(
        str(value.source_unit_id) for value in response.source_coverage
    )
    _evaluate_coverage(
        actual_source,
        obligations.source_coverage,
        not_empty_code=(
            ComposerOutputDiagnosticCode.SOURCE_COVERAGE_NOT_EMPTY
        ),
        sequence_code=(
            ComposerOutputDiagnosticCode
            .SOURCE_COVERAGE_SEQUENCE_MISMATCH
        ),
        diagnostics=diagnostics,
    )
    actual_specificity = tuple(
        value.obligation_key for value in response.specificity_coverage
    )
    _evaluate_coverage(
        actual_specificity,
        obligations.specificity_coverage,
        not_empty_code=(
            ComposerOutputDiagnosticCode.SPECIFICITY_COVERAGE_NOT_EMPTY
        ),
        sequence_code=(
            ComposerOutputDiagnosticCode
            .SPECIFICITY_COVERAGE_SEQUENCE_MISMATCH
        ),
        diagnostics=diagnostics,
    )
    if (
        not response.story_segments
        or response.terminal_segment_key
        != response.story_segments[-1].segment_key
    ):
        diagnostics.append(
            ComposerOutputDiagnosticCode.TERMINAL_SEGMENT_RULE_FAILED
        )
    return tuple(dict.fromkeys(diagnostics))


def typed_decoding_diagnostic(
    exc: Exception,
) -> ComposerOutputDiagnosticCode:
    message = str(exc).casefold()
    if "source coverage obligations" in message:
        return ComposerOutputDiagnosticCode.SOURCE_COVERAGE_DUPLICATE
    if "source coverage requires at least one segment key" in message:
        return (
            ComposerOutputDiagnosticCode
            .SOURCE_COVERAGE_SEGMENT_KEYS_EMPTY
        )
    if "specificity coverage obligations" in message:
        return ComposerOutputDiagnosticCode.SPECIFICITY_COVERAGE_DUPLICATE
    if "specificity coverage requires an obligation and segment keys" in message:
        return (
            ComposerOutputDiagnosticCode
            .SPECIFICITY_COVERAGE_SEGMENT_KEYS_EMPTY
        )
    if "realization segment references" in message:
        return (
            ComposerOutputDiagnosticCode.REALIZATION_ASSOCIATION_DUPLICATE
        )
    if "story segment keys" in message:
        return ComposerOutputDiagnosticCode.STORY_SEGMENT_DUPLICATE
    if "terminal segment key" in message:
        return ComposerOutputDiagnosticCode.TERMINAL_SEGMENT_RULE_FAILED
    return ComposerOutputDiagnosticCode.TYPED_DECODING_FAILED


def _realization_authority(value: Any) -> TypedId:
    authority_id = getattr(value, "authority_id", None)
    if isinstance(authority_id, TypedId):
        return authority_id
    source_unit_id = getattr(value, "source_unit_id", None)
    beat_id = getattr(value, "beat_id", None)
    authority_id = source_unit_id if source_unit_id is not None else beat_id
    if not isinstance(authority_id, TypedId):
        raise ContractValidationError(
            "realization segment omitted its authority"
        )
    return authority_id


def _evaluate_coverage(
    actual: tuple[str, ...],
    expected: OrderedCoverageObligation,
    *,
    not_empty_code: ComposerOutputDiagnosticCode,
    sequence_code: ComposerOutputDiagnosticCode,
    diagnostics: list[ComposerOutputDiagnosticCode],
) -> None:
    if expected.mode is CoverageObligationMode.MUST_BE_EMPTY:
        if actual:
            diagnostics.append(not_empty_code)
    elif actual != expected.values:
        diagnostics.append(sequence_code)


def _unique_ids(values: tuple[TypedId, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ContractValidationError(f"{label} must be distinct")


def _unique_associations(
    values: tuple[AuthorityOwnerAssociation, ...],
    label: str,
) -> None:
    if len(values) != len(set(values)):
        raise ContractValidationError(f"{label} must be distinct")
