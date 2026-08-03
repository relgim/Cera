# Validator final-stop derivation correction

## Defect

The Stage 4 V2 provider returned a schema-valid `complete_final_sequence` whose independent `final_stop_state` string differed from the last item's `resulting_state`. Python correctly rejected it. The provider had nevertheless complied with the submitted JSON Schema because JSON Schema cannot express equality between those two fields.

This is distinct from the previously corrected `field_name` enum defect. Retrying the same provider package would be improper because the wire contract itself invited two independently generated copies of one fact.

## Correction

The active provider wire now contains only the authoritative ordered `items`. Python deterministically constructs the domain `FinalSequenceV1` and copies the final item's `resulting_state` into `final_stop_state`.

The active compatibility identities advance to:

- schema `cera.continuous_semantic_validator_draft.v3`;
- adapter `cera.continuous_validator_adapter.v11`.

Historical V1 and V2 readers remain available for immutable evidence. Their semantics are not weakened. The closed five-value `FinalFieldName` enum from the first Queue 0047 checkpoint remains unchanged.

## Invariants

- The provider can no longer submit `final_stop_state` on the active wire.
- An injected `final_stop_state` is rejected as an extra property.
- The final stop state in the domain DTO always equals the last item's resulting state by construction.
- Empty item lists remain invalid.
- Writer DTO, prompt, adapter, model route, and transport remain byte/behavior unchanged.
- No hidden repair, retry, provider substitution, or fallback is introduced.
