# Continuous Planner Lean Context and Cache Policy

**Status:** controlling provider-free Planner-context policy; shadow/test selection only  
**Decision:** D-200  
**Active route:** unchanged `cera.active_runtime.d180.v1`

## 1. Creator decision

CERA treats one compatible physical stored Planner thread, including its provider-side working context and cache behavior, as reliable working context unless evidence shows a concrete continuity failure.

The purpose is to remove repeated prompt material and reduce Planner latency. Python retains exact accepted state for validation, recovery, branching, and reconstruction, but ordinary turns do not resend that state merely because the provider context might theoretically forget it.

This policy does not claim knowledge of undocumented provider cache internals. It establishes the product assumption and the behavior to test.

## 2. Closed context modes

### `lean_continuous` - default

- Create or resume one compatible physical Planner thread.
- Install stable Planner instructions once as stored-thread/base instructions.
- Use a comparatively large first-turn initialization packet containing the current scene anchor, required rules and boundaries, relevant initial character summaries, directory/tool scope, and current source/protected-user custody.
- After creator acceptance, append the exact accepted user-message/final-sequence envelope and stable reference descriptors once through the non-generating stored-thread context operation.
- On later ordinary turns, send only current source units, minimal world/branch/session/scene/turn identity, protected-user custody, a compact accepted-head receipt, stable keys, and context specifically required now.
- Do not resend stable instructions, prior accepted pairs, prior complete sequences, accepted-session fact payloads, or unchanged Planner character summaries.
- Use bounded world retrieval only when an exact decision needs evidence not already sufficient in working context.

### `projection_assisted` - explicit diagnostic mode

This mode adds the smallest exact accepted-session projection needed for one demonstrated continuity defect or an explicitly authorized controlled comparison. It is not the default and cannot activate silently inside a live attempt.

Allowed triggers include:

- contradiction with an accepted same-scene event;
- failure to use a previously accepted fact that was already injected;
- a privacy/owner error attributable to missing exact context;
- an explicitly authorized A/B diagnostic.

The exact trigger, selected stable keys, and payload byte count are recorded. Full-history replay and full-card resending remain forbidden.

Thread loss, archival, incompatibility, deliberate restart, and a non-forkable branch are never `projection_assisted`. They are `reconstruction`.

### `reconstruction`

Reconstruction creates a new physical thread for a lost, archived, incompatible, deliberately restarted, or non-forkable branch thread. Python installs the stable base once, injects one bounded accepted tail plus only the summaries required at reconstruction time, emits a separate initialization/reconstruction receipt, and then returns to `lean_continuous`. Same-branch recovery preserves stable keys while rebinding their session/thread custody. A non-forkable child branch allocates new deterministic child-branch keys from the validated parent checkpoint; parent keys remain foreign and unusable in the child.

Reconstruction is physical-thread initialization, never an ordinary-turn packet or a silent fallback.

## 3. Branch precedence

For a child branch, Python must first atomically materialize the complete parent
cutoff into a previously nonexistent child directory. The immutable
materialization receipt binds both actual directory identities, the ordered
accepted head, complete parent and initial-child ACTIVE manifests, every
Character/Relationship/Rule/Location/Event/Scene record, WORLD_STATE, the
deterministic world index, accepted checkpoint artifacts, policy identities,
and every current character-summary source eligible for child-thread
suppression. Child WORLD_STATE differs only by its child branch identity; its
index is deterministically rebuilt from that state.

CERA attempts a physical provider fork only after revalidating that exact
materialization receipt and a Python-owned branch-fork receipt binding it to
the parent/child branches, accepted ancestry, parent thread, and privacy
boundary. Empty, partial, stale, foreign, replayed, or modified child snapshots
fail before transport. A summary delivery is inherited only while its child
source path, revision, bytes, character owner, authority class, and derived
envelope remain exact; stale deliveries are not suppression authority.

If fork is unavailable, lost, incompatible, or unsafe, CERA creates a new physical thread through `reconstruction`. After either valid initialization, ordinary child turns use `lean_continuous`.

Provider-retained transcript context is never branch authority and cannot authorize parent-private or sibling state.

## 4. Planner-only scope and role closure

D-200 governs the Planner's compatible physical stored thread.

- Composer stays stateless and receives the current source, complete current Planner sequence, current protected-user custody, and current character-expression, voice, craft, and realization context required for the turn.
- Validator remains physically separate and receives the exact current verification closure.
- Composer and Validator never receive prior accepted-session projections merely as a precaution.

## 5. Character-summary delivery ledger

Send an incomplete, revision-bound Planner character summary only for:

- first relevant appearance in a new physical thread;
- a newly relevant character not active in current working context;
- a material accepted record revision;
- explicit Scene Change to a new primary character or topic;
- reconstruction;
- an exact Planner-requested evidence need.

Python records character ID, record revision, selected-content hash, source identity/hash, physical-thread identity, delivery reason, prompt identity, and receipt. A source revision that leaves selected summary content unchanged does not trigger a resend.

Every summary remains incomplete, source/revision bound, and explicit that more information is available. This Planner ledger never suppresses current Composer realization context.

## 6. Accepted-context reference bridge

The stored Planner thread receives each accepted envelope exactly once:

```text
accepted user message
+ complete Validator-approved final sequence
+ acceptance identity
+ stable accepted-context reference descriptors
+ owner and visibility labels
```

Each stable reference binds world, branch, Planner session, physical thread, accepted turn/envelope/pair/event, acceptance, injection, snapshot, synchronization, field owner, role, visibility, and accepted ancestry.

A lean prompt carries only the compact accepted-head receipt and stable keys, never prior fact values. Python rejects unknown, stale, foreign-branch, sibling, rejected, provisional, or unsynchronized references. `projection_assisted` may resolve and preload only its named keys.

Rejected, provisional, adjusted, declined, failed-injection, and unsynchronized candidates never become stored accepted authority.

## 7. Retrieval behavior

The Planner may search only authorized branch `ACTIVE` authority, explicitly non-authoritative `DERIVED` navigation views, and its own Planner session context.

Demand-driven retrieval applies when an exact current decision needs a named older event, household rule, returning character, changed record, exact owner-private fact, or uncertainty resolution. Search locates candidates; consequential use still requires authorized exact fetch and binding.

## 8. Telemetry layers

Record separately:

- one-time base/stable-instruction bytes and token estimate;
- exact submitted per-turn Planner/Composer text where known;
- logical prompt-component bytes without mislabeling them as submitted bytes;
- accepted-context injection bytes and receipt;
- reconstruction bytes and receipt;
- provider total/cached/uncached input, output, reasoning, first-result latency, total latency, and cumulative context growth when exposed;
- physical-thread identity and mode;
- world search/read calls and failures;
- rich-sequence depth and validation outcome.

Provider cache hits do not require a persistent conversation, and persistent context does not prove a cache hit. Causal cache claims require a separately authorized control.

## 9. Provider-free qualification

Before any live use, prove closed modes, one-time base instructions, exact once-only accepted injection, Turn 1/Turn 2 lean absence, stable-reference custody, current downstream role context, minimal projection assistance, no silent mode change, compatible resume, accepted-checkpoint fork, lost/non-forkable reconstruction, private/sibling isolation, exclusion of nonaccepted authority, exact telemetry separation, retained terminal-v4/capability/transaction/archive/completion/recovery behavior, and the exact scripted ten-stage audit with zero external calls.

## 10. Later live short canary

The first separately authorized live attempt uses `lean_continuous` only. Turn 1 carries required initialization and Sakura summary; Turn 2 uses the same compatible thread without repeating Sakura, Turn 1 accepted material, or fact payloads. Scene Change remains explicit; Turn 3 may add Mia's current summary because the primary character changed.

Report Turn 1-to-Turn 2 prompt and latency changes without claiming hidden cache causation. A concrete lean failure stops the attempt; it never switches mode inside that attempt.

## 11. Authority and exclusions

Python remains final authority for creator acceptance, persistence, branch state, source claims, reconstruction, and atomic publication. D-200 changes Planner context efficiency, not creator, identity, privacy, knowledge, consent/capacity, protected-user, evidence, branch, or transaction boundaries.

This decision does not authorize any external provider call, live canary, production/default activation, live story or database mutation, installed SillyTavern or service change, deployment, merge, remote operation, push, retry, fallback, hidden repair, provider substitution, Fast mode, Detailer, extra verifier, or automatic False Positive.

## 12. V2 closed submission packets

The compatible thread is not authority to send an open dictionary. Python must
classify and bind the request before constructing exactly one
`cera.continuous_planner_turn_packet.v1` identity:

```text
first_turn_initialization
lean_continuous_continuation
projection_assisted_continuation
scene_change
```

The prompt boundary accepts only the validated packet object. Context mode,
projection trigger/keys, Scene Change hash, packet telemetry, debug evidence,
candidate authority, and replay all derive from the same canonical bytes.
Physical-thread initialization remains separately identified as first-thread,
reconstruction, or accepted-checkpoint fork initialization.

Ordinary lean packets have an exact allowed field set. Closed nested records,
current-source/span/claim custody, summary-to-evidence binding, accepted-head
scope, stable-key synchronization custody, and Scene Change context hashes are
validated before provider submission. No caller label or nesting can introduce
prior pairs, sequences, projections, prose/history, unchanged summaries,
stable-instruction duplication, foreign payloads, initialization material, or
untyped data.

After a successful materialization and provider fork, the child receives a distinct physical
thread and child-rekeyed stable references. The child first lean packet rejects
parent and sibling keys. If the fork is not safe or available, reconstruction
uses the same required child materialization custody before its distinct
initialization contract and returns to lean mode.
