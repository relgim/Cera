"""Deterministic evidence reuse across accepted session ancestry."""

from __future__ import annotations

from dataclasses import dataclass

from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId, require_kind
from cera.serialization import re_is_sha256

from .models import ContextEvidenceBinding, EvidenceDelivery


@dataclass(frozen=True, slots=True)
class AcceptedEvidenceMaterialization:
    record_id: TypedId
    version: int
    payload_sha256: str
    exact_section_ids: tuple[str, ...]
    checkpoint_id: TypedId

    def __post_init__(self) -> None:
        require_kind(self.checkpoint_id, IdKind.CHECKPOINT, "checkpoint_id")
        if self.version < 1 or not re_is_sha256(self.payload_sha256):
            raise ContractValidationError("accepted evidence materialization is invalid")


def reuse_accepted_evidence(
    current_exact: tuple[ContextEvidenceBinding, ...],
    materialized: tuple[AcceptedEvidenceMaterialization, ...],
    *,
    accepted_ancestor_ids: frozenset[TypedId],
) -> tuple[ContextEvidenceBinding, ...]:
    """Reference only byte-identical evidence from accepted ancestry.

    Changed versions, hashes, sections, rejected checkpoints, and sibling
    checkpoints remain exact-inline.  This saves resends without trusting stale
    provider context as evidence authority.
    """

    if any(value.delivery is not EvidenceDelivery.EXACT_INLINE for value in current_exact):
        raise ContractValidationError("current evidence planner input must be exact-inline")
    by_id = {value.record_id: value for value in materialized}
    if len(by_id) != len(materialized):
        raise ContractValidationError("materialized evidence inventory is duplicated")
    result: list[ContextEvidenceBinding] = []
    for current in current_exact:
        prior = by_id.get(current.record_id)
        if (
            prior is not None
            and prior.checkpoint_id in accepted_ancestor_ids
            and prior.version == current.version
            and prior.payload_sha256 == current.payload_sha256
            and prior.exact_section_ids == current.exact_section_ids
        ):
            result.append(
                ContextEvidenceBinding(
                    record_id=current.record_id,
                    version=current.version,
                    payload_sha256=current.payload_sha256,
                    delivery=EvidenceDelivery.MATERIALIZED_REFERENCE,
                    exact_section_ids=current.exact_section_ids,
                    materialized_checkpoint_id=prior.checkpoint_id,
                )
            )
        else:
            result.append(current)
    return tuple(result)
