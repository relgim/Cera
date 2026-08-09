# CERA Runtime Route and Filter/Validator Reconciliation Request

## Review status

This is an architecture-review request only. Do not implement, edit runtime
source, dispatch a provider call, mutate story state, modify installed
SillyTavern, or change an active route while performing this review.

Review target:

```text
Repository: D:\Cera\worktrees\C78-quality-fixes
Branch: fix/cera-runtime-quality-20260809
Commit: 1cc8ff8cce3496b00bd1641d993b94427d6ea417
Tree: 10c2bc3dcee7e48e9f0abb394e87bdf6883de24f
```

This commit is the exact runtime-source checkpoint. This review request may be
published in a documentation-only descendant commit; that must not be mistaken
for a further runtime implementation change.

The latest combined provider-free focused gate at this checkpoint passed 128
tests with one Windows symlink-privilege skip and no provider calls. This does
not establish live SillyTavern readiness.

## Purpose

Perform a fresh, owner-level reconciliation of CERA's ordinary and adult
runtime routes. Determine how to restore the creator-intended semantic
Validator and DeepSeek Filter/Validator responsibilities without undoing the
useful accepted-state, branch, privacy, restart, and Recorder-custody work that
already exists.

Do not assume that Queue 0072's lean route remains the correct product design.
Treat it as an implementation decision that must now be reconciled against the
creator decisions below.

## Creator decisions that control this review

1. Each user prompt has exactly one logic owner for its complete visible
   candidate.
2. Ordinary logic belongs to Codex. DeepSeek realizes the ordinary prose.
3. Adult logic and adult prose belong to DeepSeek when the adult route is
   active. A candidate must not switch logic owner partway through its visible
   response.
4. Python owns deterministic identity, schema validation, hashes, branch
   custody, accepted state, atomic persistence, rollback, and restart recovery.
   Python does not judge psychology, causal quality, or prose fidelity.
5. The ordinary route has a Codex semantic Validator. It is distinct from
   Python's structural validation.
6. The adult route has a DeepSeek Filter/Validator. It validates the adult
   candidate and creates:

   - a protected full record for later DeepSeek reasoning;
   - a synchronized non-explicit projection for later Codex reasoning.

7. Python validates and binds both adult records by IDs and hashes. Exact adult
   prose and protected full records never enter Codex context.
8. The `Adult EX` control is not the ordinary/adult route selector. It controls
   how much adult craft/example context DeepSeek may retrieve. With Pi,
   character cards, accepted continuity, and authorized adult material are
   retrieved from a confined branch-scoped view.
9. Character autonomy is currently one global setting with `both` selected for
   testing. Character mind and body have greater precedence than user-supplied
   NPC outcomes, although sufficiently strong opposing pressure may overcome
   either tendency.
10. Genesis is the starting revision copied into each new chat world. A new
    chat receives a new world/branch workspace. A fork copies its source chat's
    complete branch state and then diverges.
11. Accepted prose remains the immutable visible artifact. Derived records
    cannot silently rewrite it.
12. No model output becomes durable canon merely because a provider returned
    it. Creator acceptance and Python atomic promotion remain required.

## Original Filter/Validator design

The earlier architecture contained a semantic continuity formatter/filter:

```text
DeepSeek adult planning and prose
-> DeepSeek Safe-Continuity Filter/Formatter
-> protected full record + non-explicit realized sequence
-> Codex validation of the safe package
-> creator review
-> Python commit
```

Relevant authority:

```text
D:\AIChatBot\Cera\docs\authority\
CERA_CONSENSUAL_ADULT_CAPABILITY_FALLBACK_V1.md

D:\AIChatBot\Cera\docs\authority\
CERA_SEQUENCE_FIRST_RUNTIME_V1.md
```

The Filter compared the planned development with the exact realized prose,
preserved the story-relevant decision path, and produced a safe package Codex
could reason from.

## Where the architecture changed

Queue 0072 and Roadmap 0026 deliberately removed the mandatory Validator and
Reader stages:

```text
D:\AIChatBot\Cera\.chatgpt\pro-review\continuous-work\WORK_QUEUE_0072.md

D:\AIChatBot\Cera\.chatgpt\pro-review\overnight\
CERA_OVERNIGHT_ROADMAP_0026.md

D:\AIChatBot\Cera\.chatgpt\operations\CODEX_MANAGER_COMMAND_0072.md
```

That route became:

```text
DeepSeek candidate
-> creator review
-> Accept
-> minimal Python prose commit
-> post-Accept DeepSeek Recorder
-> full adult record + Codex projection
```

The post-Accept Recorder retained the data-extraction half of the original
Filter, but its semantic validation responsibility was removed. The active
Recorder prompt explicitly says the prose is already accepted and must not be
judged.

This was a material architecture change, not a terminology-only rename.

## Current manager roadmap and supersession conflict

The manager repository still names Queue 0077 and Roadmap 0031 as active:

```text
D:\AIChatBot\Cera\.chatgpt\pro-review\continuous-work\CURRENT.md
SHA-256: 64390aa2b2fb5637bbfd540416b1eab4cb9e4e521bcf735486bb6f3c7fe6e233

D:\AIChatBot\Cera\.chatgpt\pro-review\continuous-work\WORK_QUEUE_0077.md
SHA-256: 729f2a51b065ba8bb519e7f7e02cfcf1796efe0667135ac98c5a22442cbd64de

D:\AIChatBot\Cera\.chatgpt\pro-review\overnight\CERA_OVERNIGHT_ROADMAP_0031.md
SHA-256: 9695204e9f7f51cffe04ab19b27fd7a9160aac8839898a6ef74566dcde338f88
```

That authority freezes this older source:

```text
Commit: 58ae752cb7a4a24be4f43b6f1f67bf27f960ee0e
Tree: 901ee8bfbbfb3e92b34931374568624d52c7e623
Status: active_bounded_route_comparison_then_measured_campaign
```

Its planned work is:

1. eight paired Writer-route fixtures, consisting of four ordinary and four
   adult/dark comparisons;
2. a fixed twenty-turn campaign;
3. a passing target of twenty accepted turns, at least eighteen first-candidate
   passes, at most two feedback-free Regenerates, first-pass Recorder success,
   and no repeated generalized or deterministic runtime defect;
4. a ceiling of 12 Sol-family, 66 DeepSeek-family, and 0 Terra operations.

Queue 0077 predates both the provider-free corrections listed below and the
creator clarification restoring the Codex Validator and DeepSeek
Filter/Validator. It freezes a source that still implements the lean
post-Accept Recorder route and prohibits semantic route changes during the
measurement.

Therefore Queue 0077/Roadmap 0031 must not be executed unchanged. Provider
testing against its frozen bytes would measure an architecture the creator has
now rejected and would omit the current correction work. The review must
classify Queue 0077/Roadmap 0031 as superseded and propose a new provider-free
implementation authority. No provider call is authorized by this review
request.

## Current implemented ordinary route

```text
SillyTavern user prompt
-> local OpenAI-compatible relay
-> Python PiScene HTTP adapter
   - validates request shape, model, session, and controls
   - extracts the newest exact user source
   - resolves accepted world/branch context
-> retained per-chat Codex Planner
   - creates the structured causal and psychological sequence
-> Python structural/authority validation
-> Python branch-scoped Writer view
-> Pi + DeepSeek ordinary Writer
   - reads the confined view
   - returns visible prose
-> Python provisional candidate and durable review identity
-> creator Accept / Decline / Regenerate / Replan
```

Current action behavior:

- `Accept` commits exact prose, exact source, exact Codex sequence, and the
  minimal branch receipt before Recorder work.
- `Regenerate` reuses the exact frozen Codex sequence and requests an
  independent prose realization.
- `Replan` sends the same exact source plus optional creator guidance back to
  Codex for a different causal sequence.
- `Decline` creates no accepted story effect.
- A later normal user message currently auto-accepts a prior unresolved,
  structurally valid provisional candidate.
- A post-Accept DeepSeek Recorder extracts secondary canon, public state,
  relationship changes, knowledge changes, durable changes, and unresolved
  threads.

Current omission: no Codex semantic Validator compares the DeepSeek prose with
the frozen Codex sequence before creator review or automatic acceptance.

## Current implemented adult route

```text
SillyTavern user prompt using the adult model identity
-> Python PiScene HTTP adapter
-> Python accepted adult context + required adult handoff
-> Python protected adult Writer view
-> Pi + DeepSeek Adult Scene
   - owns logic and prose in one call
-> Python provisional candidate
-> creator Accept / Decline / Regenerate
-> on Accept, minimal protected prose/handoff receipt
-> post-Accept DeepSeek Adult Recorder
   - protected full adult record
   - non-explicit Codex-readable projection
-> Python structural/hash validation and record attachment
```

Current omissions:

- Route selection is tied to ordinary/adult model identities rather than the
  intended automatic logic-owner transition.
- `Adult EX` is not implemented as a separate retrieval-context control.
- There is no pre-Accept DeepSeek Filter/Validator.
- The post-Accept Adult Recorder is forbidden from judging the prose.
- The current runtime still binds the Recorder to the V1 adult projection
  envelope; the new V2 presence and scoped-durable fields are not wired into
  the runtime yet.

## Current supporting infrastructure that should be preserved

The reconciliation should preserve unless concrete evidence shows a defect:

- exact accepted prose and minimal receipt committed before optional derived
  recording work;
- Python-owned branch/generation/parent/hash custody;
- crash-safe Accept and idempotent creator-decision replay;
- Recorder failure represented as repairable pending state rather than false
  failed acceptance;
- independent Regenerate and explicit Replan semantics;
- protected full adult records separated from Codex-safe projections;
- cumulative accepted branch-state reduction independent of the recent prose
  window;
- tamper detection for accepted receipts, branch heads, records, and
  projections;
- new-chat Genesis isolation and complete fork inheritance;
- per-chat retained Codex Planner thread identity;
- provider-free dispatch guard and explicit provider accounting;
- confined Pi Writer views with no arbitrary repository or drive access.

## Complete correction history after the Queue 0077 frozen source

The runtime-source review target is twenty commits ahead of Queue 0077's frozen
commit. The following is the complete commit delta from `58ae752` through
`1cc8ff8`, in order:

| Commit | Change | Relevance to the corrected roadmap |
|---|---|---|
| `d2fe031` | Require bounded Pi context completion | Prevents a Writer result from claiming context retrieval without a completed tool result. |
| `e6bd3db` | Verify isolated runtime and strict requests | Hardens isolated SillyTavern runtime custody and request decoding. |
| `69ccfc4` | Restore persistent Planner thread identity | Restores retained per-chat Codex Planner continuity. |
| `6f4ebb6` | Persist per-chat thread custody | Makes Planner thread identity restart-recoverable and branch-bound. |
| `915b93c` | Reject tampered Planner thread state | Adds closed failure for altered persisted Planner identity. |
| `e69f4f1` | Pin provider-free gate to checkout | Prevents tests from silently importing another CERA checkout. |
| `d26529d` | Harden recording custody and decoding | Adds strict durable decoding, phase-one Accept recovery, atomic bundles, and record/projection hash checks. |
| `dbac9a9` | Preserve accepted turns while recording | Represents an accepted turn as usable or explicitly pending while derived recording is incomplete. |
| `b33852e` | Split ordinary and adult continuity views | Prevents protected adult prose from entering the Codex/ordinary view and separates route-specific continuity. |
| `129225d` | Consume sanitized adult continuity | Limits Codex Planner evidence to the non-explicit adult projection. |
| `401c54d` | Reconcile recording and Pi-session recovery | Handles stale soft sessions safely and reconciles orphan recording attempts without downgrade. |
| `09cd90e` | Reduce cumulative accepted branch state | Replaces recent-window-as-memory behavior with cumulative accepted state reduction. |
| `9f35ace` | Prove completed context tool call | Matches Pi context tool start/end identities before accepting completion. |
| `f1ae2c7` | Record creator decisions and lessons | Documents Genesis, branch, autonomy, routing, provisional-canon, and testing decisions known at that point. |
| `ff96941` | Block provider dispatch in offline gates | Adds a hard provider-dispatch guard for provider-free verification. |
| `3f5514b` | Isolate Genesis world workspaces | Adds new-chat Genesis copying, fork isolation, lineage, and confined branch MCP workspace primitives. |
| `09ccec8` | Harden session review runtime | Adds per-chat Planner registry/factory seams, review idempotency, CLI ceiling wiring, and prompt de-duplication support. |
| `ee11500` | Align branch workspace custody | Aligns world workspace paths with store custody and provides request-bound MCP bridge lifecycle primitives. |
| `b0b12a6` | Keep creator guidance single-sourced | Updates the legacy test contract so stable guidance is not duplicated in the Writer prompt. |
| `1cc8ff8` | Reconcile accepted branch state custody | Binds records to accepted turns, verifies the branch-head anchor, reconciles pending completion, retains old pending continuity, and versions non-explicit adult state effects. |

Provider-free verification at the final source checkpoint passed 128 focused
tests with one Windows symlink-privilege skip. This is evidence for the listed
surfaces, not proof that the complete repository or live SillyTavern route is
ready.

## Current implementation disposition

### Implemented and focused-tested

- strict accepted receipt, record, projection, and branch-head custody;
- crash recovery for Accept, recording attempts, and replayed review decisions;
- cumulative branch reduction with route-specific privacy views;
- persistent per-chat Planner thread state and tamper rejection;
- isolated Genesis/new-chat/fork workspace primitives;
- request-bound confined MCP bridge primitives;
- provider-free dispatch blocking and checkout-pinned test execution;
- exact-sequence Regenerate and explicit Replan foundations.

### Built as a seam but not fully wired into the live launcher

- the world-workspace manager and request-bound MCP bridge;
- the per-chat Planner backend factory that must receive the exact workspace
  bridge;
- migration of launcher resume state from the old static seed/root to the
  shared branch-world workspace;
- adult projection V2 presence and scoped durable effects.

### Still missing or requiring product reconciliation

- the pre-Accept ordinary Codex Semantic Validator;
- the pre-Accept DeepSeek Adult Filter/Validator;
- staged dual adult records promoted atomically only after creator acceptance;
- automatic one-owner ordinary/adult route selection and return-to-Codex
  transition;
- `Adult EX` as an independent branch-scoped example/craft retrieval control;
- live Codex access to only the assigned world directory through the confined
  retrieval bridge;
- a durable provisional-canon proposition/dependency ledger beyond IDs;
- the final exact-checkpoint complete repository suite;
- the final isolated SillyTavern test of three progressing ordinary prompts and
  three progressing adult prompts without API/review-state errors.

These gaps must remain visible. A review must not describe a seam as a live
feature or treat the 128 focused tests as final readiness qualification.

## Architecture requiring review

### Proposed ordinary route

Review this candidate architecture rather than accepting it automatically:

```text
User
-> Python ingress and accepted-context resolution
-> retained Codex Planner
-> Python structural/authority validation
-> DeepSeek Writer
-> Codex Semantic Validator
   - compare exact prose with frozen sequence and accepted evidence
   - return accept/reject plus typed material conflicts
   - never write canon
-> Python validates the verdict
-> creator review
-> Accept atomically commits exact prose and sequence receipt
-> ordinary Recorder derives future-relevant records
-> Python validates and attaches derived records
```

Determine whether the ordinary Recorder should remain separate from the Codex
Validator. The default recommendation is to keep them separate: validation
judges fidelity before acceptance, while recording derives persistence only
after acceptance.

### Proposed adult route

Review this candidate architecture rather than accepting it automatically:

```text
User or typed ordinary-to-adult handoff
-> Python accepted-context and route-authority resolution
-> DeepSeek Adult Logic/Writer
-> independent DeepSeek Filter/Validator
   - compare exact candidate with frozen adult authority and accepted state
   - return a typed semantic verdict
   - create a proposed protected full record
   - create a synchronized proposed Codex-safe projection
-> Python validates verdict, schemas, IDs, hashes, event links, and privacy
-> creator review
-> Accept atomically promotes:
   - exact accepted prose;
   - protected full record;
   - Codex-safe projection;
   - route transition state.
```

Nothing from a rejected adult candidate becomes accepted state. Future
DeepSeek reasoning reads the protected full record; future Codex reasoning
reads only the filtered projection.

Determine whether the Filter/Validator should prepare both records before
creator review and have Python promote them on Accept. This is the default
recommendation because it restores validation without permitting unaccepted
data to enter canon.

## Required separation of validation types

The corrected design must not call all checks simply "validation."

| Check | Correct owner | Purpose |
|---|---|---|
| Request/schema/ID/hash validation | Python | Deterministic safety and custody |
| Ordinary causal planning | Codex Planner | Decide what logically happens |
| Ordinary realization fidelity | Codex Validator | Judge prose against sequence and evidence |
| Adult logic and prose | DeepSeek Adult Scene | Produce the complete adult candidate |
| Adult realization and continuity fidelity | DeepSeek Filter/Validator | Judge candidate and construct dual records |
| Canon promotion | Python after creator acceptance | Atomic authoritative persistence |
| Subjective prose preference | Ted | Human product judgment |

## Questions the review must resolve

1. Should the Codex Validator always run on ordinary candidates, or only before
   automatic acceptance and on flagged cases?
2. Should the Codex Validator use a fresh compact session per candidate or a
   separate persistent branch session? Explain contamination, latency, and
   cache tradeoffs.
3. Should the DeepSeek Filter/Validator always be a separate invocation from
   the Adult Scene Writer? Explain why same-call self-validation is or is not
   sufficiently independent.
4. Exactly when is the adult candidate shown to Ted: before Filter completion,
   or only after the Filter and Python checks pass?
5. If the Filter rejects an adult candidate, should CERA automatically Decline,
   expose the reason and wait, or permit one explicitly requested Regenerate?
6. How is automatic ordinary/adult logic ownership selected when the current
   prompt begins inside adult material, without treating the `Adult EX` toggle
   as a route selector?
7. What exact content is available when `Adult EX` is off versus on? Preserve
   core character/continuity access while controlling only extended examples
   and craft context.
8. Can the post-Accept Recorder be eliminated on adult turns because the
   Filter has already produced staged dual records? If retained, identify its
   non-duplicated responsibility.
9. What qualifies a candidate for automatic acceptance when Ted sends another
   prompt without deciding? A semantic Validator must own meaning; Python may
   own only deterministic hard failures.
10. What is the correct failure behavior when Codex Validator or DeepSeek
    Filter is unavailable? The established preference is an explicit visible
    error, not silent fallback or model substitution.
11. How should the Filter represent a typed `return_to_codex` transition so
    that one prompt never changes logic owner midway, while the next prompt can
    safely return to ordinary reasoning?
12. What provider-call and latency cost does the restored design add, and which
    calls can benefit from stable prompt-prefix caching without treating cache
    or provider session history as story authority?

## Required workflow simulations

Simulate at least:

1. Ordinary candidate passes Codex validation and is accepted.
2. Ordinary candidate contradicts the frozen sequence and is rejected.
3. Regenerate reuses the exact sequence and receives independent validation.
4. Replan changes the Codex sequence and invalidates the prior realization.
5. Adult candidate passes DeepSeek Filter validation, then commits both records.
6. Adult candidate fails Filter validation and leaves zero accepted effect.
7. Adult accepted turn returns to Codex through the non-explicit projection.
8. Adult Filter succeeds but creator Declines; staged records are discarded.
9. Validator/Filter transport failure produces a visible recoverable error.
10. Process restart and branch fork preserve accepted dual-record custody but
    never preserve rejected candidates as authority.
11. `Adult EX` off/on changes only the authorized example/craft surface.
12. A new chat receives a fresh Genesis workspace with no unrelated chat data.

## Required review output

Return:

1. An independent assessment of how and why the architecture drifted.
2. The corrected ordinary route.
3. The corrected adult route.
4. A responsibility matrix for Python, Codex Planner, Codex Validator,
   DeepSeek Writer, DeepSeek Adult Scene, DeepSeek Filter/Validator, Recorder,
   and Ted.
5. Exact inputs, outputs, authority level, validation owner, failure behavior,
   and persistence timing for every stage.
6. A decision on whether Validator and Recorder remain separate in each route.
7. A decision on automatic route ownership and the independent `Adult EX`
   control.
8. A classification of Queue 0077/Roadmap 0031 and a replacement manager
   roadmap that begins provider-free and binds the corrected architecture.
9. A migration plan from commit
   `1cc8ff8cce3496b00bd1641d993b94427d6ea417` that preserves useful completed
   work and removes obsolete lean-route assumptions.
10. A disposition for every commit and remaining gap listed in this request:
    preserve, integrate, supersede, correct, or defer with a stated reason.
11. Provider-free tests required before any new live call, followed by the
    bounded route and SillyTavern qualification sequence.
12. Questions that genuinely require Ted's creator decision.

Do not implement the result. Produce a corrected review and architecture plan
for Ted and Codex to approve before source work resumes.
