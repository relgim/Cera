"""Select only cognition-cited evidence for independent ordinary validation."""

from __future__ import annotations

from cera.cognition import CognitionPlanV1
from cera.errors import ContractValidationError
from cera.semantic_validation import ValidationEvidenceV1
from cera.sequence_first.contracts import SequenceFirstTurnSemanticInputV1


def selected_validation_evidence(
    *,
    plan: CognitionPlanV1,
    turn: SequenceFirstTurnSemanticInputV1,
) -> tuple[ValidationEvidenceV1, ...]:
    cited: set[str] = set()
    for decision in plan.decision_records:
        cited.update(decision.causal_trigger_refs)
        cited.update(decision.decisive_factor_refs)
        cited.update(value.source_ref for value in decision.observer_frame.directly_perceived)
        for pressure in decision.material_pressures:
            cited.update(pressure.evidence_refs)
    # The exact current source is already a dedicated validation field.  Do
    # not duplicate it in the evidence array merely because a decision cites
    # the turn trigger.
    cited.discard(turn.current_source_key)
    available = {record.evidence_key: record.exact_content for record in turn.evidence_records}
    missing = sorted(cited - set(available))
    if missing:
        raise ContractValidationError(
            "cognition validation cites unavailable evidence: " + ", ".join(missing)
        )
    return tuple(
        ValidationEvidenceV1(
            evidence_ref=reference,
            concise_authoritative_fact=available[reference],
        )
        for reference in sorted(cited)
    )
