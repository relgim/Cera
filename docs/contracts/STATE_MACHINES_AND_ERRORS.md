# State Machines and Error Contract

**Status:** controlling lifecycle specification

<!-- CERA_RUNTIME_MODEL_V3_ACTIVE_START -->
## Runtime Model V3 state path (active)

The controlling authority is
[`CERA_RUNTIME_MODEL_V3.md`](../authority/CERA_RUNTIME_MODEL_V3.md); the one
active role matrix is
[`CERA_RUNTIME_MODEL_V3_ROLE_CONFLICT_MAP.md`](../architecture/CERA_RUNTIME_MODEL_V3_ROLE_CONFLICT_MAP.md).
A turn advances through Python ingress, Planner, Python plan compilation,
Writer prose, Python mechanical envelope, Semantic Validator, Python hard
enforcement, Reader, and `review_ready`. Writer mechanical failure stops before
Validator. Validator rejection or inconclusive status stops before Reader.
Python hard-authority failure cannot be overridden. Reader rejection or
inconclusive status stops before candidate publication. No rejected path enters
accepted provider ancestry or mutates story state. Only creator acceptance
opens Python's atomic commit transition.

Rejected, inconclusive, and error Validator decisions may retain exact
Python-custodied diagnostic spans/adjudications and typed reasons only. They
contain no canonical realization segments or finalization package and stop
before Reader. Diagnostic evidence is never event, memory, persistence,
accepted-context, candidate, or commit input.

The D-186 state machine below is historical where it gives Composer output
semantic self-certification duties. D-204 controls the V3 successor while D-180
remains the active product route.
<!-- CERA_RUNTIME_MODEL_V3_ACTIVE_END -->

## D-220 Pi Scene ordinary review V3 disposition

D-223 updates only the standing-policy classifier: Python-pass ordinary
rejections are provisional unless the Luna primary conflict is one of the five
exact D-223 hard classes, Reader is inconclusive, or Reader includes
`whole_candidate_quality`. The rejected validator receipts remain attached.
The acceptance transition, durable audit, and Recorder sequence are unchanged.

This state path is ordinary-only and narrows D-219 only after the independent
Luna, Reader, and Python lanes have joined for one exact frozen candidate.
Ordinary review V2 is historical; V3 is current.

```text
WRITER_CANDIDATE_FROZEN
-> CHECKS_PENDING (Luna || Reader || Python)
-> JOIN_EXACT_CANDIDATE_BINDINGS
   -> all required checks pass
      -> AUTOMATIC_ACCEPTED
      or -> MANUAL_REVIEW_READY -> creator Accept | Regenerate | Decline
   -> Python pass + at least one reject + every signal D-220 soft
      -> STANDING_POLICY_PROVISIONAL_ACCEPTED (exactly once)
   -> Python failure/inconclusive or any hard Luna/Reader signal
      -> HARD_REJECTION_REVIEW_READY
```

Soft Luna classes are exactly `omitted_decision`, `presence_violation`,
`stopping_boundary`, and `capability_restriction`. A Reader rejection is soft
only when every issue scope is `exact_quote` or `omitted_planner_item`.
Everything else is hard, including Python failure/inconclusive, every other
Luna class, Reader inconclusive, and any `whole_candidate` issue. Hard wins over
soft. Pending, missing, malformed, stale, or candidate-mismatched evidence is
not soft.

`STANDING_POLICY_PROVISIONAL_ACCEPTED` uses the first exact Writer candidate,
performs no second Writer call, and waits for no manual action. It retains the
original rejected Luna/Reader evidence and commits once through the normal
atomic acceptance boundary with `canon_status=provisional`. Its terminal V3
decision identity is output-only `standing_policy_accept_provisional`.
Qualification projects this state through current manifest V25 over fixture
V21 (introduced in historical manifest V23 and first exercised by spent
manifest V24), phase result V6, and complete result V5. It does not
increment manual-action or Regenerate counters
and is never relabeled as `checks_passed` or automatic first-pass acceptance.

The acceptance transition requires immutable, separate policy provenance:
`standing_creator_policy` `ordinary_provisional_continuity` v1, policy text
SHA-256 `1fe7bf05034f1543040eac456818269768760e58dc625eecc03508bd64a804e2`, and
canonical policy-object SHA-256
`47729a4fc27046e8da768c8e0f1bc6670be48e576e606f193339de30b3bf3b23`.
Python recomputes the audit from the candidate, both validator bindings, Python
qualification, exact tolerated reason codes, and immutable policy object. A
client cannot submit, derive, repair, or replay this decision. Provenance drift
or a partial acceptance write fails closed and restart reconciles the same
durable exact-once transition without provider redispatch.

Adult review, Adult Filter, consent/capacity, protected custody, and strict
acceptance are unchanged. Source integration and focused provider-free evidence
pass; the fresh V21/V25 complete gate and live qualification remain pending.
This lifecycle text is not a completed-live-result claim.

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
-> atomically materialize complete child X at B
-> validate materialization receipt before transport
-> fork provider thread X at B
-> atomically materialize complete child Y at B
-> validate materialization receipt before transport
-> fork provider thread Y at B
```

X and Y inherit committed ancestors only. New events, memories, relationship records, summaries, projections, receipts, and overlays are branch-local. A merge is not supported until separately designed and authorized.

An existing empty or partial child, a mismatched physical directory identity,
any ACTIVE record/index/WORLD_STATE change, a stale parent cutoff, a changed
accepted checkpoint artifact, a foreign/replayed receipt, or a mismatched
summary source terminates before provider fork or cross-branch reconstruction.

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

## 6A. Pi Scene manual Planner transport Retry

Pi Scene exposes a manual Transport Retry only for a terminal Planner provider
transport failure. Python must prove that the failed call created no candidate,
review, accepted head, recording, or other bound story-state effect. Writer,
Luna Validator, adult-scene, adult-filter, and Recorder failures are not
eligible in this version. An ineligible, pending, ambiguous, progressed without
recoverable custody, or accepted result never exposes the action.

Before the exact Planner dispatch, after deterministic pre-Planner work such as
automatic acceptance of the prior review, Python freezes the normalized
request, resolved route, turn context, accepted head, effect snapshot, active
Planner-thread hash, and provider-ledger prefix in a protected dispatch
authority. On typed Planner success, Python atomically redacts the raw request
and retains a hash-only `planner_completed_pending_progress` marker until the
ordinary review or adult operation becomes durable. A terminal failed Planner
call plus unchanged effects is promoted to one immutable failure authority and
public receipt.

```text
dispatch_staged
-> planner_completed_pending_progress
   -> progressed -> terminal
   or -> planner_result_unavailable
or
-> failure_eligible
-> eligible
-> authorized
-> owner_rotated
-> dispatch_started
-> succeeded
or
-> terminal_failed -> successor eligible Retry
or
-> blocked
```

`authorized` records the one creator action but performs no provider call.
`owner_rotated` archives the failed retained thread and creates a distinct empty
retained Planner thread. Creating that thread may use an external provider
lifecycle transport, but it is not a Sol/model inference turn and does not add
a provider-call-ledger operation. `dispatch_started` binds the fresh thread and
exact provider-ledger prefix before the single new Planner call.

The state machine is restart-safe and idempotent:

- a stale safe projection or index is rebuilt from the validated protected
  authority;
- an identical replay can recover a terminal Planner failure recorded after a
  crash but before HTTP error rendering;
- a crash after fresh-thread creation but before a Sol ledger event resumes at
  `owner_rotated`, never archives or resumes the failed thread again;
- a crash after the Planner consumed a result but before durable story progress
  first searches exact persisted progress by the original turn-context hash;
  recovered progress keeps the Planner thread, while an absent result retires
  that exact thread as `completed_uncommitted_never_resume` and never
  redispatches the old request;
- automatic Accept may advance `current_state` and `recent_prose`; recovery is
  therefore bound to the marker's original turn hash, exact source and
  controls, and selected accepted receipt rather than equality with the
  rebuilt post-Accept turn;
- a crash after durable review progress terminalizes the already-generated
  response provider-free and does not dispatch another Planner call;
- an exact fresh-thread failure creates a successor Retry even when unrelated
  chats appended valid Sol events before or after it;
- malformed same-thread Planner evidence is hash-bound without trusting its
  graph, never becomes Retry authority, and retires that potentially consumed
  thread before another prompt can run; a retirement failure keeps the branch
  `blocked`;
- a mutated provider-ledger prefix or evidence that cannot safely authorize
  continued branch progress is redacted into
  `planner_evidence_invalid_blocked`; even successful thread retirement does
  not clear that evidence conflict;
- evidence that cannot be attributed to one exact thread, or has route, head,
  or unrecognized effect drift, remains `blocked` and cannot redispatch.

The raw normalized request exists only in the dedicated protected Retry runtime
root while a Planner dispatch or eligible action requires it. Typed Planner
success replaces it with the hash-only completion marker before downstream
Writer, Validator, adult, or Recorder work. Safe journals, indexes,
debug logs, branch evidence, and public status contain only bounded identities,
hashes, counts, and closed reason codes. Every permanent block and terminal
success redacts the raw custody idempotently.

Authenticated `GET /v1/cera/transport-retries/{retry_id}` is provider-free and
idempotent; it may repair derived local custody projections before returning
exactly one of `eligible`, `in_progress`, `succeeded`, `superseded`, or
`blocked`. It never dispatches a model operation. `POST` accepts only `{}` and
is the sole manual dispatch action.
Repeated GET or POST after a terminal result returns the same stored result and
never adds a provider operation. The successful completion includes a closed
transport-Retry attempt summary so accounting retains every prior charged
failed Planner operation as well as the successful pipeline operations.

Transport Retry is not:

- **Regenerate**, which draws another candidate under the exact frozen story
  logic and accepted-state snapshot;
- **semantic repair**, which addresses candidate meaning or validation;
- **Recorder repair**, which retries post-Accept record attachment without
  regenerating or recommitting the scene.

Transport Retry changes no request, route, controls, story logic, provider,
model, or output. It never merges, patches, normalizes, falls back, substitutes
a model, or runs automatically.

`CERA_PLANNER_RESULT_UNAVAILABLE` is a distinct non-Retry 409. It means a
charged or conservatively ambiguous Planner call consumed thread custody, but
no exact durable result could be recovered. Successful local retirement
unblocks a different prompt (which cold-rehydrates a different thread); failed
retirement or unrecognized durable effect keeps that branch blocked for repair
or a new chat. The same disposition covers malformed exact-thread evidence,
which is never accepted as proof of success or Retry eligibility. A closed
local `pretransport_failed` call is not this state: it
keeps the safe thread and preserves the original local error.

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
| `CERA_PROVIDER_TRANSPORT_FAILED` | Planner transport terminally failed; a manual Retry is present only when exact zero-effect proof passed |
| `CERA_PLANNER_RESULT_UNAVAILABLE` | A completed or conservatively charged Planner result was lost before exact durable progress; Transport Retry is disabled and its consumed thread is never resumed |
| `CERA_REQUEST_REPLAY_PENDING` | The exact request or branch has unresolved durable custody, so provider redispatch is blocked |
| `CERA_TRANSPORT_RETRY_NOT_FOUND` | The authenticated Retry identity is malformed or unavailable; existence detail is not disclosed |
| `CERA_PROMOTION_BLOCKED` | A route has hard defects, insufficient evidence, failed human preference, or lacks creator approval |
| `CERA_DEPLOYMENT_NOT_AUTHORIZED` | One or more deployment-readiness authorizations are absent |
| `CERA_STATE_CONFLICT` | Optimistic concurrency/authority conflict |
| `CERA_TRANSACTION_ROLLED_BACK` | Atomic commit failed |
| `CERA_VERIFIER_FAILED` | Required verification was unavailable, malformed, incomplete, inconclusive, or rejected; safe crossed-stage/provider receipt handles are retained and nothing is published |
| `CERA_DELIVERY_AFTER_COMMIT_FAILED` | Story commit succeeded but delivery failed; replay accepted artifact |
| `CERA_RENDER_FAILED` | Accepted artifact exists but UI rendering failed |

## 10. Repair policy

Default remains zero automatic repairs. The separately authorized provider-free Adult ON/EX gate implements one production-prohibited, validation-directed beat replacement when exactly one beat fails deterministic or semantic specificity. It cannot change provider, source, authority packet, route, decision, branch, or locked non-target text. Python splices the replacement, runs a new no-retry semantic verification, and then reruns all deterministic and structural checks. Semantic failure after the splice ends the turn without a second repair. Live use still requires qualification, promotion evidence, and creator authorization.

D-220 ordinary soft rejection is a disposition, not repair. It preserves and
provisionally accepts the first candidate exactly once, makes no Writer call,
and does not turn a rejected validator result into a pass. Hard ordinary repair
and rejection behavior remains governed separately by D-219 and the active
execution policy.

## 11. D-186 shadow continuous candidate and Scene Change states (historical role allocation)

```text
ACTIVE snapshot
-> isolated CANDIDATE copy
-> Planner provisional sequence
-> DeepSeek candidate prose
-> Validator closed package
-> creator review pending
-> Accept | False Positive
   -> preflight mutable-current and compact immutable snapshot final/temp paths
   -> verify all file revisions and package invariants
   -> apply every edit to prepared files
   -> event + accepted exact pair + index + world revision
   -> atomic ACTIVE directory replacement
   -> record accepted-final envelope in Python ledger
   -> inject canonical envelope once into Planner model-visible history
   -> record injection-returned receipt
   -> atomically persist immutable per-turn Planner session snapshot
   -> reload/hash/type-check the immutable snapshot receipt
   -> record snapshot and final synchronization receipt
or
-> Rewrite | Replan | Adjustment | Decline
   -> ACTIVE unchanged; candidate remains diagnostic only
```

Ordinary Accept is valid only for Good plus `accept_allowed`. False Positive
is valid only for publication-eligible Concern/Critical and records the
original assessment outside ACTIVE as a Validator-owned, non-story diagnostic.
It promotes exactly the unchanged candidate bytes and never creates a Planner
constraint.

Scene Change is an explicit creator flag on the first new-scene message. Python
holds that message, gives the separate Validator only the old scene's accepted
turn allow-list and exact pairs, saves the result under `DERIVED/Scenes` as a
non-authoritative regenerable view, then supplies the labeled summary, its
source turn/hash/revision/regeneration metadata, at most five exact accepted pairs, and held message to the same Planner
thread. Provider failure, schema failure, revision conflict, missing accepted
pair, summary leakage, or promotion failure stops without retry or fallback.
An accepted-final history-injection failure also blocks the next Planner turn;
an unsynchronized accepted ledger entry is never silently piggybacked or
replayed.

The acceptance journal v6 stays pending after any crash between in-memory
ledger append, injection return, journal update, snapshot replacement, stable
accepted-reference persistence, or final synchronization. It binds the exact
Planner thread, envelope, injection operation, immutable session snapshot,
stable-reference artifact, and final synchronization receipt. `synchronized`
is impossible before both the atomic snapshot and stable-reference artifact
exist. Ambiguous injection is never automatically replayed. The mutable
current-session pointer is convenience state only; synchronization proof binds
and revalidates the immutable per-turn artifacts before finalization.

New accepted snapshots use the V2 compact locator under
`PLANNER_SESSION/ACCEPTED/v2`. The locator contains abbreviated hashes only;
the immutable envelope and receipt retain the complete accepted-turn identity,
snapshot/thread/envelope hashes, nested injection receipt, encoded inner-file
hash, physical outer-file hash, and hash-bound path plan. Python preflights the
248-character resolved budget for final and same-directory temporary paths
before creator promotion. Compact-locator collision, changed bytes, root drift,
or path overflow is terminal. Historical V1 receipts and paths remain
decodeable without rewriting prior evidence.

Accepted-session context is not raw conversational memory. Under D-200,
ordinary compatible Planner turns receive a payload-free compact accepted-head
receipt and stable keys resolved from exact accepted pair/event/envelope bytes.
Python rechecks branch, physical thread, accepted ancestry, owner, visibility,
and synchronization custody before a key becomes a request binding. A typed
same-scene projection is created only for an explicit `projection_assisted`
turn, contains only its named minimal key selection, and remains owner-local.

V2 closes the ordinary submission boundary before any Planner call:

```text
trusted ingress + Python evidence/summary/reference binding
-> classify first | lean | projection-assisted | Scene Change
-> construct exact cera.continuous_planner_turn_packet.v1 field set
-> validate source spans, claims, summary bindings, stable custody, and mode
-> hash-bind prompt/debug/replay/candidate authority
-> submit once
```

Unknown top-level fields, caller containers nested under allowed labels,
unbound or foreign stable keys, mismatched source/receipt bytes, a changed
summary binding, an unpaired Scene Change context, or an implicit context-mode
change fails before provider submission. Reconstruction and accepted-checkpoint
forks remain physical-thread initialization states and cannot be relabeled as
ordinary turns.

Protected-user realization follows a separate exact-proof path. Python projects
only source spans whose attribution proves Ted owns the action or dialogue,
each final sequence item names the exact claim keys it uses, and DeepSeek marks
every copied occurrence with exact candidate offsets. Python validates those
spans before Validator assessment; search, pronouns, NPC-attributed quotations,
or unattributed quotations do not create protected-user authority.

Directory promotion journal v2 records prepared, ACTIVE-moved, prepared-
installed, backup-removed, committed, and finalized states with exact prior and
prepared tree hashes. Restart recovery either restores the verified prior tree
or finishes the verified prepared tree; it never merges trees, repeats semantic
validation, calls a provider, accepts an unverified tree, or leaves ACTIVE
missing. Mutable `create_file` operations accept revisioned JSON objects only.
