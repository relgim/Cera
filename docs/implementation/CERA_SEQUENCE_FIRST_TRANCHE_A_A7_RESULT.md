# CERA Sequence-First Tranche A A7 Result

Status: provider-free focused gate passed

Authority: Queue 0068 / Overnight Roadmap 0022

## Correction

- Accepted-artifact restart loading now selects an exact V1, V2, or V3 custody
  contract and rejects unknown versions.
- V1 preserves only its documented historical compatibility: an absent
  intended sequence may use the realized sequence, and an absent primary status
  may use `realized`. Optional binding hashes are verified when present.
- V2 requires realized and intended sequences, both exact binding hashes,
  `primary_sequence_status: realized`, and its legacy
  `acceptance_basis: automatic_qualification` provenance.
- V3 requires realized and intended sequences, both exact binding hashes,
  `primary_sequence_status: realized`,
  `qualification_basis: validator_and_reader_qualified`, and
  `creator_acceptance_basis: explicit_creator_acceptance`.
- Missing required fields, malformed typed sequences, binding tamper, status
  tamper, and cross-version provenance now fail closed instead of silently
  reconstructing authoritative custody.

## Focused verification

Valid historical V1/V2 restart, required-field deletion matrices, malformed
sequence data, binding tamper, status tamper, provenance tamper, and legacy-field
insertion gates passed.

Provider calls: 0.

No story, database, accepted branch, route, service, installed SillyTavern,
deployment, remote, merge, or push effect occurred.
