"""Select only cognition-cited evidence for independent ordinary validation."""

from __future__ import annotations

from cera.cognition import (
    CognitionPlanV1,
    CognitionProviderTurnHandoffV1,
)
from cera.errors import ContractValidationError
from cera.semantic_validation import ValidationEvidenceV1
from cera.sequence_first.contracts import SequenceFirstTurnSemanticInputV1
from cera.serialization import canonical_sha256


def selected_validation_evidence(
    *,
    plan: CognitionPlanV1,
    turn: SequenceFirstTurnSemanticInputV1,
    handoff: CognitionProviderTurnHandoffV1,
) -> tuple[ValidationEvidenceV1, ...]:
    if (
        handoff.plan_semantic_sha256 != plan.semantic_sha256
        or handoff.turn_semantic_sha256 != canonical_sha256(turn)
    ):
        raise ContractValidationError(
            "cognition selected-evidence handoff changed plan or request"
        )
    return tuple(
        ValidationEvidenceV1(
            evidence_ref=value.evidence_ref,
            concise_authoritative_fact=value.concise_authoritative_fact,
        )
        for value in handoff.selected_evidence
    )
