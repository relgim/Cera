# CERA Continuous Work Queue — Revision 0060

queue_revision: `0060`
queue_status: `active_provider_free_sequence_first_correction_then_git_and_claude_review`
queue_owner: `ChatGPT Pro repository-review thread`
creator: `Ted`
manager_repository: `D:\AIChatBot\Cera`
execution_repository: `D:\CP25\source`
parent_queue_path: `.chatgpt/pro-review/continuous-work/WORK_QUEUE_0059.md`
parent_queue_sha256: `544cee3fad3d9bced8be757932aaa718a4d2a6856a2ebd234006644301fe4492`
manager_command_path: `.chatgpt/operations/CODEX_MANAGER_COMMAND_0060.md`
manager_review_path: `.chatgpt/operations/CERA_SEQUENCE_FIRST_IMPLEMENTATION_MANAGER_REVIEW_0060.md`
sequence_first_policy_path: `docs/authority/CERA_SEQUENCE_FIRST_RUNTIME_V1.md`
implementation_refinement_path: `docs/authority/CERA_SEQUENCE_FIRST_IMPLEMENTATION_REFINEMENT_V1.md`
claude_review_request_path: `docs/review/CERA_CLAUDE_SEQUENCE_FIRST_IMPLEMENTATION_REVIEW_REQUEST_2026-08-05.md`
autonomous_protocol_path: `.chatgpt/operations/CERA_AUTONOMOUS_MANAGER_PROTOCOL_V3.md`

## Decision

Claude should not perform another implementation review before Codex corrects the current additive draft. Claude already reviewed the published old checkpoint and supplied the needed architecture and call-path findings. Reviewing an incomplete local draft would create stale feedback.

The mandatory order is:

```text
Codex provider-free correction
-> complete provider-free qualification
-> exact Git publication
-> Claude review of exact new commit
-> newer manager classification
-> only then live qualification
```

## Stage 0 — Reconcile and preserve

Provider calls: `0`.

- read all Queue 0060 authority and the latest execution self-continuation state;
- preserve Queue 0059 publication and all historical evidence;
- inventory current additive `src/cera/sequence_first` and tests;
- classify any concurrent work before editing;
- do not delete historical exhaustive/compact code required for immutable replay.

## Stage 1 — Provider-free implementation correction

Implement `CERA_SEQUENCE_FIRST_IMPLEMENTATION_REFINEMENT_V1.md`.

Required outcomes:

- semantic draft/custody-envelope separation;
- no Python-derived presence or responder salience;
- no model-authored backgrounded list;
- responders derived from Planner item owners;
- ordered presence changes;
- binary Validator verdict and orthogonal review flags;
- no duplicate beat coverage;
- target-key durable changes;
- Python-derived recall eligibility;
- no mini-role-ledger regrowth;
- persistent one-time-instruction Planner;
- fresh compact candidate-specific Validator;
- proven prose-only DeepSeek route;
- provider-free Stage 6 adapter with no regex/name-driven cast authority.

## Stage 2 — Provider-free qualification

```text
exact tests
-> focused tests
-> complete fake pipeline
-> adversarial presence/branch/restart tests
-> Stage 6 adapter integration tests
-> source freeze
-> one complete suite
```

One complete suite only at the frozen boundary. No provider calls.

## Stage 3 — Review publication

After Stage 2 passes:

- materialize current manager docs into the execution repository;
- review and stage exact coherent paths;
- create accurate local checkpoint commit(s);
- fast-forward the existing dedicated review branch or create a duplicate-safe successor;
- push without force;
- verify the exact remote commit;
- record `GIT_PUBLICATION_RESULT_Q0060.json`.

No generated provider evidence, raw outputs, database copies, caches, environments, archives, credentials, or secrets may be committed.

## Stage 4 — Mandatory external review stop

After verified publication:

- report the exact repository, review branch, commit SHA, tree SHA, and test results;
- provide the Claude review request document;
- stop.

Do not start a live canary, SillyTavern, or the twenty-turn campaign until Claude's exact-commit review is inspected and a newer manager queue authorizes continuation.

## Provider authority

Queue 0060 provider ceiling:

- Codex/Sol: `0`;
- DeepSeek: `0`;
- Reader: `0`.

## Git authority

Allowed:

- local provider-free source/test/doc commits;
- one reviewed non-force push to the dedicated review branch;
- exact publication receipts.

Prohibited:

- force push;
- default-branch mutation or merge;
- history rewrite;
- destructive cleanup;
- unrelated bulk staging;
- credential or secret publication.

## Stop and effect boundaries

Do not stop for intermediate provider-free defects with a bounded correction available, context compaction, or Ted being unavailable.

Stop for:

- verified review publication awaiting Claude;
- irreconcilable Git state or test evidence;
- a genuine product/model-role decision;
- an excluded effect.

No production mutation, active/default route promotion, installed-user SillyTavern change, deployment, adult-route activation, live provider call, or remote operation beyond the authorized review push.
