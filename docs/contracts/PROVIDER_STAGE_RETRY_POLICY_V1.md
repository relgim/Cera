# Provider-Stage Retry Policy V1

**Status:** approved for provider-free implementation

**Approved by:** Ted

**Date:** 2026-08-10

**Scope:** Planner, Writer, Luna Validator, Recorder, Adult Scene, and Adult Filter
**Historical audit:**
[`CERA_PROVIDER_STAGE_RETRY_REVIEW_PACKET_2026-08-09.txt`](../implementation/CERA_PROVIDER_STAGE_RETRY_REVIEW_PACKET_2026-08-09.txt)

This document is the concise controlling policy. The historical review packet
is evidence and rationale, not the active implementation specification.

## 1. Core policy

- Retry only the failed provider stage from its exact frozen stage input.
- Never replay the whole request or an already completed upstream stage.
- One initial stage attempt plus at most two manual Retry actions are allowed.
- The backend owns the attempt limit. The UI limit is defense in depth.
- No fourth attempt, automatic provider redispatch, fallback, model
  substitution, or reasoning reduction is allowed.
- Provider-free status reconciliation and recovery may run automatically.
- Semantic rejection is a successful provider operation, not a provider Retry
  event.
- Unresolved provider disposition enters `blocked_ambiguous`; it is not
  `attempts_exhausted`.
- Protected adult prompts, evidence, and results remain in protected custody.
- Installed SillyTavern is not synchronized until disposable qualification
  passes and separate installation authority is given.

## 2. Scope and identity

Every retry chain is scoped by:

- world and branch;
- generation;
- provider stage;
- unique stage occurrence;
- exact accepted-state boundary;
- exact frozen-input identity.

Retry counters are never chat-lifetime counters.

Provider Retry, semantic Regenerate, and Replan have separate identities,
counters, DTOs, UI actions, and qualification ceilings. None resets another.
The qualification manifest must bound the maximum provider operations for the
complete generation, not only one stage.

## 3. Stage-attempt states

The shared states are:

- `eligible`: a closed retryable failure permits a manual Retry;
- `in_progress`: the current attempt is owned and no other attempt is allowed.
  When and only when that attempt is durably prepared but has never won a
  provider-dispatch claim, the backend exposes one exact manual
  `resume_prepared` control;
- `succeeded`: an exact result is frozen and may continue downstream once;
- `blocked_ambiguous`: provider disposition is unknown; only Check Status or
  provider-free repair is allowed;
- `attempts_exhausted`: three closed, confirmed failures occurred and all
  owners are fenced;
- `recording_repair_required`: accepted story is preserved but Recorder work
  is incomplete after the third closed retryable failure;
- `recovery_required`: a known non-Retry failure or non-dispatch custody
  conflict stopped this occurrence with its last accepted branch head
  preserved. The shared V1 panel is read-only in this state.

`attempts_exhausted` and `recovery_required` expose no generic clickable
backend action in V1. "Explicit recovery" means a separately authorized
operator or stage-specific workflow that establishes a proven-safe next
occurrence; it never marks the stopped Retry chain `succeeded`. Check Status
remains available only for `blocked_ambiguous`, and Recorder repair remains the
dedicated action for `recording_repair_required`.

`blocked_ambiguous` may later become recovered success, confirmed retryable
failure, confirmed exhaustion, or operator repair required. It never silently
authorizes another provider call.

`blocked_ambiguous` is reserved for unresolved dispatch custody. Input,
authority, ledger, owner-retirement, result-checkpoint, and other deterministic
conflicts enter `recovery_required`, not provider ambiguity.

## 4. Exhaustion behavior

Planner, Writer, Luna, Adult Scene, or Adult Filter exhaustion terminates only
the current stage-occurrence/generation chain. It preserves the branch at its
last accepted head. A later generation requires a new request occurrence from
a proven-safe accepted boundary; browser reload, generic Regenerate, or replay
cannot become attempt four.

Recorder exhaustion preserves the accepted assistant prose and enters
`recording_repair_required`. Continuity-dependent generation remains blocked
until Recorder is repaired and completed or the branch is explicitly restored
to its last fully recorded accepted state.

The first exhausted Recorder occurrence may expose one backend-issued manual
`repair_recording` control. Accepting it creates at most one new Recorder
stage occurrence bound to the exact accepted review/head, exhausted parent,
and frozen Recorder input. The repair successor owns a fresh one-initial plus
two-manual-Retry budget; it is never attempt four of the parent. If that
successor exhausts, it remains `recording_repair_required` with no repair
action and no recursive successor. The repair control authorizes only the
successor's initial Recorder attempt. It never authorizes whole-request replay,
provider substitution, or replay of any upstream stage.

## 5. Retryable failure taxonomy

Retry may be offered only after closed disposition and failed-stage effect
proof for:

- closed provider transport timeout or temporary unavailability;
- provider process termination;
- interrupted or incomplete provider stream/completion;
- a received provider envelope or protocol result that Python deterministically
  proves structurally invalid, when a fresh stochastic provider result may
  differ.

The following are not provider Retry events:

- authentication or authorization failure;
- invalid request, deterministic stage input, or API configuration;
- context-window overflow or unsupported model/parameter;
- a complete, structurally valid result whose provider finish status is merely
  `length`, `max_tokens`, or `token_limit`;
- semantic rejection by Luna or Adult Filter;
- creator Decline, Regenerate, or Replan;
- changed branch, generation, request, or stage authority;
- custody, storage, path, or configuration failure;
- exhausted provider/campaign budget;
- unresolved dispatch ambiguity.

A known non-Retry failure closes the accepted attempt, records only its closed
safe reason and hash/accounting evidence, retires the owner, and enters
`recovery_required` without offering Retry or redispatch. When the provider
boundary proves only that the failure is not Retry-eligible, the closed generic
reason `provider_failure_not_retryable` is used instead of guessing a specific
cause or parsing exception text. Recorder story acceptance remains preserved;
this state is distinct from third-attempt retryable Recorder exhaustion and its
`recording_repair_required` workflow.

Completion status alone does not decide success. A complete, structurally valid
result is successful even when the provider reports `length`, `max_tokens`, or
`token_limit`. When that status accompanies an actually incomplete structured
result, classify it as `provider_completion_incomplete`: close and preserve the
attempt, then offer only the existing governed manual Retry. Do not automatically
redispatch or continue the provider session. Repeated truncation still requires
an input/provider-configuration decision rather than unbounded retries.

## 6. Counters and accounting

The controlling counters are:

- `stage_attempts_total`: every accepted stage execution, including a proven
  pre-transport failure;
- `retry_actions_accepted`: backend-accepted manual Retry actions;
- `provider_operations_observed_total`: ledger-proven provider operations;
- `provider_operations_conservative_total`: observed operations plus unresolved
  possible operations.

A pre-transport failure consumes its stage attempt and, when applicable, its
accepted Retry action. It consumes zero provider operations. A submitted or
possibly submitted operation remains conservatively charged.

Every fresh owner binds `maximum_provider_operations` before dispatch. Codex
owners bind one operation; Pi/DeepSeek owners bind the configured per-invocation
ceiling. Until reconciliation proves exact ledger accounting, an interrupted
dispatch reserves that full bound rather than assuming one operation.

Pre-transport Retry classification is limited to typed process/start failure,
temporary unavailability, or a typed pre-submit connection/start timeout.
Output, stream, and completion-shape failures cannot be claimed before
transport. A known non-Retry attempt may close with zero observed and zero
conservative operations when durable evidence proves no provider operation.
This includes a deterministic preparation/build failure after the attempt was
accepted but before transport: the owner is retired and the chain enters
`recovery_required`; it is never offered as provider Retry.

## 7. Owner retirement and exactly-once authority

Owner retirement means CERA has durably revoked the old attempt's authority to
publish or bind a result. A late response may be retained as evidence, but it
is rejected from downstream publication.

Owner retirement does not claim that the remote provider stopped computing,
that the operation was uncharged, or that a late network response cannot
arrive.

Every successful stage result is frozen before downstream work. Downstream
binding is idempotent and may occur at most once.

A crash after an attempt is durably prepared but before its dispatch claim
does not authorize automatic redispatch. The backend may issue one exact
manual `resume_prepared` action bound to that chain hash and persisted owner.
It resumes the same attempt, consumes no additional Retry action, and is
idempotent under duplicate clicks or lost responses.

## 8. Exact frozen stage input

Frozen semantic input includes all stage-relevant information:

- normalized prompt/messages, plan, candidate, or accepted receipt;
- accepted-state version and branch/generation authority;
- retrieved memories, dossiers, and evidence;
- exact tool-result bundles or immutable snapshot identifiers that guarantee
  equivalent results;
- provider, model, reasoning mode, routing, content-policy route, and stage
  configuration.

Attempt ID, provider session/thread ID, ledger prefix, transport nonce, and
fresh credentials are attempt-specific and are not part of the frozen semantic
input.

## 9. SillyTavern presentation

The primary panel shows:

- safe provider/stage name;
- attempt number, such as `Attempt 2 of 3`;
- safe failure category;
- whether story prose was already accepted;
- Retry, Check Status, recording repair, or a read-only terminal state;
- one fixed concise explanation.

Hashes, schema versions, operation counts, and correlation identifiers belong
under **Technical details**. `blocked_ambiguous` has a distinct panel and must
never say that three attempts failed.

## 10. Latency and qualification profiles

The 180-second diagnostic concern is measured from
`provider_transport_invoked` to provider terminal result/failure. It applies
only to second-or-later calls on a physical session with meaningful retained
reuse.

- New physical session: `cold_start`.
- First call on a replacement session: `cold_rehydration`.
- Later calls on that same physical session: `retained`.

Fresh/single-use stages still receive measured baselines but are not classified
as retained. Report stage prepared-to-terminal, backend HTTP, SillyTavern
end-to-end, and SillyTavern overhead separately. Crossing 180 seconds flags a
diagnostic concern; it does not cancel or retry a call.

The production-representative Planner profile is `medium`. `xhigh` is a
separate stress/quality-ceiling profile. Luna remains Extra High. A missing
artificial DeepSeek max-token flag does not block live qualification; record
the actual bounded production configuration without claiming an unknown
provider maximum.

The initial 10/10 direct and 5/5 SillyTavern campaign is a canary. Report count,
mean, median, minimum, maximum, failures, and individual threshold violations.
Do not make a product p95 claim from these sample sizes.

## 11. Trusted-local custody scope

CERA currently assumes one trusted local backend instance per runtime root.
Required protections cover realistic events:

- duplicate clicks or HTTP replay;
- browser reload or lost response;
- provider timeout with unknown disposition;
- CERA crash/restart;
- partial writes and duplicate provider dispatch;
- stale result publication;
- accepted-state and Recorder consistency;
- accidental path/junction/configuration mistakes;
- protected-content privacy and accurate provider accounting.

Use the existing transactional SQLite/WAL design, or a comparably small
application-owned transaction layer, for retry metadata, idempotency, and
attempt authority. Exact protected input/result bytes may remain in protected
checkpoint storage with hashes bound transactionally to metadata.

Deferred unless the deployment model changes:

- hostile same-user directory replacement or file tampering;
- deliberate history rollback/deletion and external monotonic anchoring;
- multi-user, multi-host, or untrusted-directory security;
- POSIX/Linux hardening for the Windows deployment;
- native Win32 handle-relative filesystem defense.

The unfinished native Windows parent-swap WIP is not part of this approved
implementation direction.

## 12. Schema generation and compatibility

Contracts must be easy to create, read, validate, and update without rewriting
Python and JavaScript by hand.

The implementation will use:

- canonical versioned JSON Schemas under a dedicated schema directory;
- one repository Python generator;
- generated Python contract/validation code;
- generated JavaScript normalizers/validators;
- generated positive and negative fixtures;
- generated compact field documentation;
- a `--check` mode that fails CI when generated files drift.

Published schema versions are immutable. Breaking changes create a new version
and an explicit compatibility adapter. The current Planner transport schema is
a temporary compatibility view; the shared contract uses generic
provider-stage names.

## 13. Implementation and live authority boundary

This approval authorizes provider-free implementation, focused tests, schema
generation, runtime integration behind disabled provider dispatch, and the
final provider-free gate.

It does not by itself authorize provider calls, installed-SillyTavern changes,
deployment, or live qualification. Before live work:

1. the amended policy must be implemented;
2. focused crash/restart/idempotency/privacy tests must pass;
3. the full provider-free gate must pass on one clean exact tree;
4. the qualification root and disposable SillyTavern must be freshly frozen.

The governed live goal remains:

- 10 ordinary and 10 adult direct-backend messages in the previously approved
  retained-session order;
- then 5 ordinary and 5 adult messages through disposable SillyTavern;
- manual Retry only after a naturally occurring eligible failure;
- no injected live failure solely to exercise Retry;
- preserved cumulative provider accounting across restart.
