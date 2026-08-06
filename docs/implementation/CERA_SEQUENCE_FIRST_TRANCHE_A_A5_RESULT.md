# CERA Sequence-First Tranche A A5 Result

Status: provider-free focused gate passed

Authority: Queue 0066 / Overnight Roadmap 0020

## Correction

- An exhausted Stage 6 request now writes one immutable branch-local
  `cera.sequence_first.planned_terminal.v1` artifact before returning the
  qualification failure.
- The artifact binds the exact request and custody, source and accepted-head
  hashes, intended sequence and binding hash, attempt receipts, terminal
  Validator/Reader evidence, provider evidence hash, and
  `primary_sequence_status: planned`.
- Its effect fields are closed to zero story, presence, durable, promotion, and
  unresolved creator-review effects.
- Storage is outside `ACTIVE` and creator review, identical replay is
  idempotent, conflicting replay fails closed, and restart loading verifies
  world/branch/turn scope.
- New accepted story artifacts use additive V3 provenance with separate
  `validator_and_reader_qualified` and `explicit_creator_acceptance` fields.
  Historical V1 and V2 accepted artifacts remain restart-readable.

## Focused verification

The exact exhausted three-attempt loopback HTTP regression, accepted-artifact
provenance regression, and historical V1/V2 restart regression passed: 3 tests.

Provider calls: 0.

No accepted story, presence, durable state, promotion receipt, unresolved
review, live route, service, installed SillyTavern, deployment, remote, merge,
or push effect occurred.
