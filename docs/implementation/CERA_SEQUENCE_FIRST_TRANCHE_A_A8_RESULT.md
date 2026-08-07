# CERA Sequence-First Tranche A A8 Result

Status: provider-free focused gate passed

Authority: Queue 0068 / Overnight Roadmap 0022

## Correction

- The Reader provider surface now uses a transport-only root envelope whose
  nested closed branches match the authoritative Python verdict DTO.
- `accepted` requires zero issues; `rejected` and `inconclusive` require at
  least one issue.
- `exact_quote` requires a non-null quote and forbids an omitted Planner key.
- `omitted_planner_item` requires a current intended-sequence key and forbids a
  quote; the branch is not offered when the current sequence has no item keys.
- `whole_candidate_quality` requires both locator fields to be null.
- The Reader finalizer accepts exactly the transport envelope, unwraps it, and
  leaves the closed Python DTO as the semantic authority.
- The Reader adapter and route advance to V5, and the provider schema receives
  the explicit compatibility identity
  `cera.sequence_first.reader_provider_schema.v1`. Prompt bytes remain V3.

## Focused verification

Native JSON Schema and final OpenAI-projected schema gates accepted all valid
status/scope branches and rejected empty, missing-locator, conflicting-locator,
and unknown-key combinations. The affected Sequence-First runtime and Stage 6
bridge suites passed 95 tests before the exact-clean-tree A9 refreeze.

Provider calls: 0.

No Writer contract, model route, retry/fallback behavior, story, database,
accepted branch, live route, service, installed SillyTavern, deployment, remote,
merge, or push effect occurred.
