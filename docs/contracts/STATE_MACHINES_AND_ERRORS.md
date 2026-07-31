# State Machines and Error Contract

**Status:** controlling lifecycle specification

## 1. Normal turn

```text
RECEIVED
-> IDENTIFIED
-> INTENT_INTERPRETED
-> PREFLIGHT_VALIDATED
-> SEED_REAUTHORIZED
-> RETRIEVING
-> REASONING
-> DECISION_VALIDATED
-> ADULT_ENRICHMENT (conditional)
-> COMPOSING
-> REPLY_VALIDATED
-> REALIZATION_VERIFIED
-> COMMITTING
-> ACCEPTED
-> RENDERED
```

Any pre-commit failure moves to `FAILED_NO_COMMIT`. Commit failure rolls back and moves to `FAILED_NO_COMMIT`. Rendering failure moves to `ACCEPTED_RENDER_PENDING`; story truth remains the accepted presentation-neutral artifact.

Phase 6 exercises `COMPOSING -> REPLY_VALIDATED` in memory only. Constructing a structurally accepted artifact does not enter lifecycle state `ACCEPTED`; that state requires an atomic commit and `CommitReceipt`. Phase 8 exercises that commit path for externally receipted non-graphic aftermath only; it does not yet publish an ordinary live-provider turn.

Structural Contract v2 requires `REALIZATION_VERIFIED` before an objective
accepted-turn event or durable derived memory can be created. Composer
declarations and anchors do not skip this state.

## 2. Blocked turn

```text
PREFLIGHT_VALIDATED
-> BLOCKED_CHECKPOINTED
-> REJECTED_NO_STORY_COMMIT
-> PROJECTED_NONCANONICAL (when reasoner available)
-> AWAITING_EXTERNAL_RECEIPT
```

No state in this chain implies the blocked event occurred.

## 3. Receipt and aftermath

```text
AWAITING_EXTERNAL_RECEIPT
-> RECEIPT_RECEIVED
-> RECEIPT_VALIDATING
-> RECEIPT_REJECTED_NO_COMMIT
or
-> RECEIPT_VALIDATED_PENDING
-> PROJECTION_RECONCILED
-> AFTERMATH_DECIDED
-> AFTERMATH_COMPOSED
-> AFTERMATH_VALIDATED
-> ATOMIC_EVENT_AFTERMATH_COMMIT
-> ACCEPTED
```

The validated receipt remains pending and restart-safe if reasoner/composer/commit fails. It is not canonical until `ATOMIC_EVENT_AFTERMATH_COMMIT`.

Phase 8 persists the recovery-significant states `RECEIPT_RECEIVED`, `RECEIPT_VALIDATED_PENDING`, `AFTERMATH_DECIDED`, `AFTERMATH_VALIDATED`, and `ACCEPTED/committed`. Validation/reconciliation/composition substeps that have not produced a validated artifact do not advance story truth.

## 4. Generation and regeneration

An accepted generation is immutable:

```text
parent artifact A
  -> generation G1 -> accepted artifact B
  -> regeneration G2 -> accepted artifact C
```

B and C are sibling candidates sharing A. Selecting C advances the active branch pointer through a transaction; B remains addressable. A failed G2 leaves B active.

Exact replay of an accepted request/generation returns its stored artifact and commit receipt with zero provider calls.

## 5. Branch fork

```text
branch main at artifact B
-> fork branch X at B
-> fork branch Y at B
```

X and Y inherit committed ancestors only. New events, memories, relationship records, summaries, projections, receipts, and overlays are branch-local. A merge is not supported until separately designed and authorized.

## 6. Restart recovery

The journal records stage, input hashes, output artifact IDs, and transaction status.

- Before provider dispatch: restart may safely resume only by explicit user retry.
- After provider response but before acceptance: retain privacy-safe call and
  failure receipts; do not retain or publish a partial candidate and do not
  auto-call.
- During transaction: database recovery decides committed versus rolled back.
- After commit/before render: render from accepted artifact.
- Awaiting receipt: restore checkpoint/projection scratch state.
- Valid receipt pending aftermath: restore receipt; do not apply twice or repeat calls automatically.
- Aftermath decided: restore the bound decision and fake reasoner receipt; do not repeat the reasoner call.
- Aftermath validated: restore the exact staged `TurnCommitBundle`; do not repeat Composer work.
- Atomic resumption failure: retain the validated receipt and projection; no source, artifact, state record, branch-head advance, or receipt completion survives a rolled-back transaction.

Pre-publication provider stages additionally append a start/completion/failure
journal. Active `TurnFailureEvidenceBundleV3` preserves safe hashes, valid
receipt handles, and allow-listed canonical privacy-safe receipt payloads
across later typed or semantic failure. It never retains raw source, prompt,
private evidence, or candidate/accepted prose. A separately governed rejected
candidate review artifact may be referenced only by ID/hash. Restart inspection reports the
ordered completed stages and terminal status with
`automatic_resume_permitted=false`; it never repeats a provider call.

A continued provider conversation is never recovery authority. Under D-172 it
may be reusable advisory context only through the branch-bound session state
machine:

```text
SESSION_ACTIVE_AT_ACCEPTED_CHECKPOINT
-> STORED_CANDIDATE_LEAF_FORKED
-> CANDIDATE_BOUND_TO_CREATOR_REVIEW
-> PYTHON_ACCEPTED_RECEIPT_RECORDED -> CHECKPOINT_PROMOTED
or
-> REJECTED_LEAF_ARCHIVED | FAILED_LEAF_ARCHIVED
```

A provider/schema/pipeline failure archives only the stored candidate child.
The accepted parent remains current and no automatic call occurs. An archived
child is non-resumable and remains off accepted ancestry before the durable
rejection transition. Explicit creator feedback may create a
branch-local control constraint; a bare decline creates none. Global scope is
an explicit creator action. Neither form is story authority or character
knowledge.

On prompt, schema, model, effort, service-tier, transport, adapter, tool,
Genesis, authority-policy, privacy-projection, protected-user, or autonomy
identity change, the active session is invalidated and reconstructed under a
new compatibility hash. Context pressure rotates a compatible session from a
complete Python reconstruction. Unsupported cache/TTFT/compaction metrics are
stored as null/unknown, never inferred.

Restart first fails closed on any completion-ambiguous child. An accepted
provider handle may be resumed only when the stored-thread adapter confirms the
exact rollout. Otherwise CERA reconstructs a new stored root from the accepted
Python checkpoint; it does not replay the failed call. Every provider
checkpoint remains advisory. SQLite's append-only custody journal records
allocation, acceptance, rejected/failed leaf archival, accepted-lineage
archival, resume, and missing-thread evidence using hashes rather than raw
provider content. Historical deletion event values remain readable only for
pre-D-178 records.

## 7. Deferred derived consolidation

```text
ACCEPTED_STORY_EXISTS
-> CONSOLIDATION_EVIDENCE_BOUND
-> PROPOSAL_RECEIVED | VALIDATED_NO_CHANGE
-> PROPOSAL_VALIDATED
-> CONSOLIDATION_PREPARED
-> DERIVED_AUTHORITY_COMMITTED
-> DERIVED_VIEWS_REBUILT
```

`CONSOLIDATION_PREPARED` stores the exact validated bundle for restart. The commit atomically inserts derived records, preserves superseded history, writes its receipt, and advances `authority_revision`. It does not change accepted prose, branch head, generation, source ledger, or Genesis revision.

Snapshot or authority-revision drift produces `FAILED_NO_COMMIT`. A simulated failure inside record insertion rolls back every inserted record and receipt while retaining the prepared journal. Restart retries only that exact bundle; it does not repeat the consolidator call. Exact replay returns the stored receipt. A validated no-change result creates no transaction.

View rebuilding is downstream of canonical derived commit. Failure leaves the committed records intact and exposes `CERA_DERIVED_VIEW_STALE`; it does not roll back story or invent a summary. Rebuild is deterministic from current branch-visible authority.

## 7A. Durable post-publication work

Requested render, consolidation, and derived-view work is inserted as `pending` in the accepted-story transaction. Derived-view work depends on consolidation.

```text
pending -> running -> completed | no_change | failed
failed --exact work ID + authorization reason--> running
running --explicit restart recovery--> failed/manual_after_review
```

There is no automatic failed-work retry. A process exit after story commit but before dispatch leaves pending work. A process exit while work is running is ambiguous, so restart recovery records failure instead of repeating a possibly completed external call. A later explicitly authorized retry first checks durable consolidation identity; it reuses an existing derived commit rather than calling the consolidator twice.

No-change consolidation is a durable terminal result with bounded receipt evidence. Its dependent view work terminates as no-change. A failed or pending consolidation leaves derived-view work pending and cannot change story truth.

Phase 19 exposes this lifecycle through four operations: read-only inspect/list, pending dispatch, explicit interrupted recovery, and explicit failed retry. Dispatch does not retry failed work. Recovery converts running to failed. Retry requires exact failed IDs from the selected artifact plus a current rationale; only the rationale hash is retained. Every operator error states that accepted story truth was retained.

## 7B. Provider-free ordinary application

```text
TYPED_ORDINARY_REQUEST
-> EXACT_COMMITTED_REPLAY (zero calls)
or
-> REASONER_AND_COMPOSER
-> STORY_AND_WORK_COMMIT
-> DOWNSTREAM_DISPATCH
-> ACCEPTED_ARTIFACT_PLUS_SAFE_REPORT
```

An exact replay never dispatches work. New pre-commit failure returns the existing stable no-commit error contract. Downstream failure after publication returns accepted story truth plus failed/pending operational state. Explicit Phase 19 commands are the only way to recover or retry that downstream work.

## 8. Error envelope

```json
{
  "schema_version": "cera.error.v1",
  "error_code": "CERA_...",
  "message": "stable user-facing summary",
  "trace_id": "trace:...",
  "request_id": "request:...",
  "branch_id": "branch:...",
  "generation_id": "generation:...",
  "stage": "...",
  "story_state_committed": false,
  "retry_mode": "manual_after_review|resubmit_corrected_receipt|not_applicable",
  "details": []
}
```

Errors never contain provider secrets, hidden prompts, private evidence, raw protected source, or internal reasoning.

## 7A. Evaluation, promotion, and deployment readiness

```text
SUITE_SEALED
-> ROUTE_RUN_RECORDED
-> FINDINGS_VALIDATED
-> OFFLINE_EVIDENCE_ONLY | LIVE_EVIDENCE_RECORDED
-> MATCHED_HUMAN_REVIEW_COMPLETE
-> NOT_ELIGIBLE | PENDING_EVIDENCE | ELIGIBLE_FOR_CREATOR_REVIEW
-> CREATOR_DECISION_REQUIRED
```

Offline evidence never transitions to live-qualified status. Any hard authority/privacy/identity/consent/cast/branch/protected-user defect transitions to `NOT_ELIGIBLE` regardless of average score. A report-integrity, case-binding, telemetry, or contamination failure ends the assessment without promotion.

Deployment readiness is a second independent gate. Missing role qualification, human review, privacy review, production-world authorization, SillyTavern authorization, credential approval, creator fresh-chat acceptance, or deployment authorization returns `BLOCKED`. Satisfying the input gates returns only `ELIGIBLE_FOR_CREATOR_DECISION`; code does not deploy or replace the final creator decision.

## 9. Required error codes

| Code | Meaning |
|---|---|
| `CERA_INTAKE_INVALID` | Request/source identity or schema is invalid |
| `CERA_EVIDENCE_SERVICE_UNAVAILABLE` | Required bounded evidence service unavailable |
| `CERA_REASONER_UNAVAILABLE` | Required reasoner transport/provider unavailable |
| `CERA_REASONER_CONTRACT_INVALID` | Reasoner output failed schema/authority validation |
| `CERA_COMPOSER_UNAVAILABLE` | Composer unavailable |
| `CERA_COMPOSER_CONTRACT_INVALID` | Candidate reply invalid |
| `CERA_ADULT_AUTHORITY_UNRESOLVED` | Adult identity/consent/capacity insufficient |
| `CERA_ADULT_PLANNER_UNAVAILABLE` | Compatibility code: required consent-valid Adult Mechanics adapter unavailable |
| `CERA_ADULT_CONTEXT_INVALID` | Current-turn adult craft/context activation is unsupported or lacks authority |
| `CERA_ADULT_ROUTE_SYNC_FAILED` | Safe ledger and restricted exact envelope failed identity/hash/order/state/participant synchronization |
| `CERA_ADULT_MECHANICS_CONTRACT_INVALID` | Mechanics proposal changed authority/decision/scope or violated its no-prose/no-psychology contract |
| `CERA_ADULT_CRAFT_CATALOG_INVALID` | Preserved source, compiled fragment, catalog manifest, or provenance binding is invalid |
| `CERA_ADULT_CRAFT_SELECTION_INCOMPLETE` | No within-budget fragment set covers the plan-derived AdultCraftNeed |
| `CERA_ADULT_SPECIFICITY_FAILED` | Accepted candidate lacks a required concept in its bound beat/channel span |
| `CERA_ADULT_SEMANTIC_SPECIFICITY_FAILED` | Beat-local prose failed actor/action/object, transition, material-outcome, ambiguity, or coverage-claim verification |
| `CERA_ADULT_BEAT_REPAIR_FAILED` | The single permitted beat replacement failed its span, lock, specificity, or structural checks |
| `CERA_BLOCKED_NONCONSENSUAL_EVENT` | Generation stopped at blocker boundary |
| `CERA_RECEIPT_MISSING` | Expected external receipt absent |
| `CERA_RECEIPT_MALFORMED` | Schema/controlled values invalid |
| `CERA_RECEIPT_INTEGRITY_FAILED` | Hash/binding mismatch |
| `CERA_RECEIPT_STALE` | Branch head or starting artifact changed |
| `CERA_RECEIPT_WRONG_BRANCH` | Receipt branch mismatch |
| `CERA_RECEIPT_DUPLICATE_CONFLICT` | Same identity with different content |
| `CERA_EVIDENCE_INSUFFICIENT` | Required evidence unavailable within authorized scope |
| `CERA_EVIDENCE_SNAPSHOT_STALE` | Snapshot binding no longer matches request/world/branch/head/Genesis/policy |
| `CERA_EVIDENCE_ACCESS_DENIED` | Exact record, section, traversal, world mode, or privacy scope is unauthorized |
| `CERA_EVIDENCE_LIMIT_EXCEEDED` | Query, result, fetch, depth, follow-up, or byte budget was exceeded |
| `CERA_EVIDENCE_INDEX_INVALID` | Derived evidence index is stale or corrupt and requires explicit rebuild |
| `CERA_EVIDENCE_BRIDGE_UNAVAILABLE` | Required request-bound MCP evidence bridge or pinned runtime is unavailable |
| `CERA_EVIDENCE_BRIDGE_CONTRACT_INVALID` | MCP binding, arguments, allow-list, observation, or bridge/provider trace is invalid |
| `CERA_CONSOLIDATOR_UNAVAILABLE` | Required derived-consolidation adapter unavailable; no fallback or commit |
| `CERA_CONSOLIDATION_CONTRACT_INVALID` | Proposal, evidence, ownership, supersession, or transaction binding is invalid |
| `CERA_DERIVED_VIEW_STALE` | A regenerable derived summary/index is absent, stale, or failed rebuilding |
| `CERA_EVALUATION_CONTRACT_INVALID` | Evaluation case, result, report, route, or telemetry failed its typed binding |
| `CERA_EVALUATION_CONTAMINATED` | Holdout/evaluation evidence overlaps an excluded craft or training asset |
| `CERA_PROVIDER_CONFIG_INVALID` | Live-provider route, endpoint, auth mode, model, or version is not an approved qualification configuration |
| `CERA_PROVIDER_BUDGET_EXCEEDED` | A live request exceeds its byte, output-token, call, or estimated-cost ceiling |
| `CERA_PROMOTION_BLOCKED` | A route has hard defects, insufficient evidence, failed human preference, or lacks creator approval |
| `CERA_DEPLOYMENT_NOT_AUTHORIZED` | One or more deployment-readiness authorizations are absent |
| `CERA_STATE_CONFLICT` | Optimistic concurrency/authority conflict |
| `CERA_TRANSACTION_ROLLED_BACK` | Atomic commit failed |
| `CERA_VERIFIER_FAILED` | Required verification was unavailable, malformed, incomplete, inconclusive, or rejected; safe crossed-stage/provider receipt handles are retained and nothing is published |
| `CERA_DELIVERY_AFTER_COMMIT_FAILED` | Story commit succeeded but delivery failed; replay accepted artifact |
| `CERA_RENDER_FAILED` | Accepted artifact exists but UI rendering failed |

## 10. Repair policy

Default remains zero automatic repairs. The separately authorized provider-free Adult ON/EX gate implements one production-prohibited, validation-directed beat replacement when exactly one beat fails deterministic or semantic specificity. It cannot change provider, source, authority packet, route, decision, branch, or locked non-target text. Python splices the replacement, runs a new no-retry semantic verification, and then reruns all deterministic and structural checks. Semantic failure after the splice ends the turn without a second repair. Live use still requires qualification, promotion evidence, and creator authorization.
