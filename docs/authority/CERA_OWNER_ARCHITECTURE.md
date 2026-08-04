# CERA Owner Architecture

**Status:** controlling implementation specification  
**Version:** `cera.owner_architecture.v2`  
**Date:** 2026-07-29

<!-- CERA_RUNTIME_MODEL_V3_ACTIVE_START -->
## Runtime Model V3 authority (active)

The controlling role allocation is
[`CERA_RUNTIME_MODEL_V3.md`](CERA_RUNTIME_MODEL_V3.md), with the sole active
role matrix in
[`CERA_RUNTIME_MODEL_V3_ROLE_CONFLICT_MAP.md`](../architecture/CERA_RUNTIME_MODEL_V3_ROLE_CONFLICT_MAP.md).
DeepSeek is a stateless Writer that returns immutable story prose only. Python
creates its mechanical envelope. An independent Codex Semantic Validator owns
exact-span semantic classification and returns a verdict without rewriting.
Python enforces hard authority, then an independent non-rewriting Reader checks
the complete response before Python creates a review-ready candidate. Only
creator acceptance lets Python commit story truth.

Queue 0050 separates rejected semantic evidence from accepted authority. An
accepted or concerning Validator decision keeps strict canonical realization
segments and protected adjudications. A rejected, inconclusive, or error
decision uses diagnostic-only spans/adjudications with explicit grounding and
violation status; Python derives their exact text/hashes, and they terminate
before Reader, candidate creation, accepted ancestry, event, memory,
persistence, or commit.

D-204 supersedes the role allocation in D-186 through D-194 without rewriting
their evidence. D-180 remains the active product route until Runtime Model V3
passes later live qualification and explicit promotion. The older Composer and
Realization Manifest descriptions below are historical or apply only to the
D-180 route; they do not define the V3 Writer contract.
<!-- CERA_RUNTIME_MODEL_V3_ACTIVE_END -->

## 1. Product goal

CERA turns a creator message into a branch-safe, character-specific, causally coherent story response. The user may supply the first domino; the reasoner determines how supported character and world consequences unfold across one or more scene beats; the composer gives those beats voice, pacing, physical continuity, and prose.

The system must support ordinary scenes, multi-character conversations, indirect memory, long-running relationships, informed and freely consensual adult scenes, regeneration, restart, branch forks, and non-graphic aftermath after an externally completed blocked event.

Quality is not permission to invent authority. Character richness must remain bounded by evidence, privacy, knowledge, branch truth, consent/capacity, and the protected user's ownership.

## 2. Non-negotiable boundaries

### Repository

`D:\AIChatBot\Cera` is the only active repository. All Vera/V6 and E-drive sources are reference-only.

### Protected user

Ted owns his unsupplied dialogue, private thought, feeling, motive, attraction, consent/refusal, meaningful reaction, relationship commitment, destination, departure, and next consequential action. CERA may realize exact source-authored Ted facts and bounded mechanics already underway, then must stop before his next unsupplied choice.

### Truth

Only validated records committed by Python are canonical branch state. These are never independently authoritative:

- raw provider output;
- retrieval ranking;
- a model proposal;
- a prompt example;
- a summary or generated Markdown view;
- a rejected turn;
- a temporary aftermath projection;
- an external completion receipt before validation and commit.

### Consent/capacity

Identity/adult status, present consent, capacity, pressure, freedom to stop, body response, pleasure, attraction, desire, intent, action, and completion are separate fields. Physiological or vocal response, freezing, silence, compliance under force, and failure to resist never establish consent or enjoyment.

### Branches

All mutable story truth is branch-scoped. Regeneration creates immutable sibling candidates; it does not rewrite the prior accepted artifact. A fork inherits only committed ancestor state and then diverges.

## 3. Runtime ownership

### Deterministic Python Turn Kernel

Python owns:

- raw-turn identity, source-span preservation, and prepared-turn ingress;
- deterministic seed-dossier assembly, evidence obligations, and seed receipts;
- request/source identity and hashing;
- branch, generation, and replay identity;
- deterministic authority and route checks;
- adult identity and consent/capacity gates;
- source-unit ledger compilation where rules are deterministic;
- retrieval authorization and result filtering;
- schemas and validation;
- provider orchestration without silent substitution;
- event coverage, cast, protected-user, and material-state checks;
- database transactions, idempotency, rollback, recovery, and publication;
- renderer separation and operational telemetry.

Python does not invent character psychology or prose.

### Runtime Scene Reasoner

The business interface is `SceneReasonerPort`. The first adapter is `CodexSceneReasonerPort`.

The reasoner owns:

- scene-level interpretation of the Python-prepared source within typed authority;
- creation and expansion of evidence queries;
- character perception, belief, motive conflict, intent, tactics, and choices;
- participant and conversational-floor selection;
- non-graphic causal and consequence reasoning;
- multi-beat and multi-scene `SequencePlan` construction;
- uncertainties and prohibited inferences;
- advisory event, memory, relationship, thread, and development candidates.

The reasoner cannot directly read arbitrary files or databases. It receives a
Python-assembled, immutable-snapshot seed dossier and uses bounded evidence
tools exposed by Python only when obligations remain. It returns
`CodexReasonerDraftV2`: semantic decisions, local beat keys, evidence IDs, and
craft requirements. Python derives canonical IDs, record versions, hard
citations, hashes, normalized labels, route bookkeeping, and the authoritative
`ReasonerOutcome`. The draft is advisory and cannot become authority directly.

### DeepSeek Scene Composer

The composer owns one complete visible response:

- character voice and dialogue;
- private interiority allowed by the packet;
- staging, sensory detail, rhythm, physical continuity, and atmosphere;
- realization of every validated beat;
- causal buildup, turning points, immediate aftermath, and a natural stop.

It returns a minimal `DeepSeekCompositionDraft` containing story prose plus
source-coverage and beat/participant realization anchors. It does not create
semantic-inference records, realization booleans, IDs, hashes, participant
manifests, consequential outcomes, consent state, durable truth, or Ted's next
action. Python derives those bookkeeping records. Its raw response is never
directly published.

### Adult ON/EX craft selection

For a consent-valid adult decision, the Scene Reasoner may attach a semantic `AdultCraftNeed` derived from the validated current beats. It names bounded content families, subfamilies, craft axes, realization channels, outcome authority/commitment, and selected-character card sections. It contains no craft excerpt and cannot establish an event.

Python selects the smallest sufficient set of hash-bound CERA craft fragments within the route budget and emits a channel-aware `SpecificityContract`. ON is compact/direct. EX is the default for active explicit realization unless the semantic need proves ON sufficient. The selector has no fixed module count and fails closed when required concepts cannot be covered.

An `AdultMechanicsPort` remains an optional future advisory enrichment only if separately qualified; it is not required by the current catalog path. Neither craft selection nor a mechanics adapter may own character psychology, intent, tactic, responder selection, floor, relationship meaning, consequence direction, consent truth, prose, or durable state, and neither is ever invoked for blocked non-consensual generation.

### ChatGPT Instant and other models

ChatGPT Instant has no automatic runtime role. A faster or cheaper model may be qualified later for a bounded advisory task only when matched evaluation shows real latency/cost value without authority loss. It must not duplicate the reasoner merely because it is available.

Model names and account entitlements are adapter configuration, not business logic. Interactive Codex/ChatGPT access must not be assumed to be a runtime transport.

## 4. Normal pipeline

1. A raw-turn facade binds exact message, cast, branch, generation, authority,
   privacy scope, and idempotency before any advisory adapter runs.
2. A provider-neutral `IntentInterpreterPort` proposes contiguous source spans,
   responders, route hints, scene anchors, unknowns, and typed evidence
   obligations. Its current implementation is a scripted fake only.
3. Python rejects source gaps, ordinary-source rewriting, route disagreement,
   or unauthorized responders, then invokes the existing prepared-turn kernel.
4. `SeedDossierAssembler` reauthorizes any pre-expanded evidence under the
   immutable snapshot, resolves bounded obligations, and emits a seed receipt.
5. The Reasoner receives exact seed evidence and may issue bounded query plans.
   Search references never count as exact evidence; Python filters and expands.
6. Codex returns `CodexReasonerDraftV2`. Python compiles and validates the
   authoritative `ReasonerOutcome`, canonical IDs, citations, versions, and
   route bookkeeping.
7. For a consent-valid adult route, Python performs beat-local ON/EX selection,
   builds the discriminated-union `SpecificityContract`, and selects only
   relevant card sections.
8. DeepSeek returns one minimal composition draft containing prose and anchored
   realization evidence. Python derives the structural manifest and validates
   source, cast, floor, beats, knowledge, boundaries, and adult specificity.
9. A provider-neutral `SceneRealizationVerifierPort` independently checks that
   selected beats and participants were actually realized. Scripted fakes are
   test-only; an unavailable, rejected, or inconclusive verifier fails closed.
10. Unverified realization cannot create an objective event, durable derived
    memory, or accepted artifact.
11. Python atomically commits source, verified presentation-neutral artifact,
    objective event, generation, safe receipts, and requested durable work.
12. Rendering and deferred derived-memory consolidation remain downstream and
    cannot rewrite accepted prose or invent objective events.
13. Every major pre-publication stage appends a privacy-safe audit journal
    entry. Failure bundles retain only IDs, hashes, counters, and already-valid
    receipts—not source, prose, private evidence, prompts, or secrets.
14. Restart inspection never automatically resumes a provider stage. It
    exposes completed/failed stage evidence for explicit operator review.

## 5. Accepted artifact

`AcceptedStoryArtifact` means the final validated, presentation-neutral story prose after all publication-critical repairs or candidate replacement, but before SillyTavern HTML, colors, or display rendering.

It excludes:

- an unvalidated provider response;
- text removed by protected-user or coverage repair;
- beat/diagnostic markers;
- UI HTML;
- rejected candidates;
- earlier revisions.

The accepted artifact is immutable and content-addressed. Presentation and summaries are regenerated from it.

## 6. Failure policy

- No silent provider fallback.
- No recursive retry.
- No route substitution that changes quality or semantics.
- No state mutation on a failed or blocked turn.
- No unverified realization becomes an event or durable memory.
- Valid safe receipts survive later typed, semantic, or verifier failure.
- Provider or adapter unavailability produces a stable, machine-readable SillyTavern error and trace ID.
- A single bounded repair may exist only after separate evaluation and promotion. It must target a typed defect, use the same authority packet, produce a full replacement candidate, run at most once, and be visible in telemetry.
- Keeping an already validated Core artifact when an optional enhancement is rejected is not a fallback; however, no Detailer is active by default.

## 7. Memory and development

Genesis contains creator-established identity and initial facts. It is versioned and immutable at runtime. Story events and branch-local overlays may change current beliefs, habits, trust, tactics, fears, relationships, and material state without rewriting Genesis.

Every memory has an owner, knowledge route, privacy scope, evidence references, branch, certainty, temporal status, and supersession history. Summaries and indexes are lookup aids, never substitutes for evidence expansion.

## 8. Blocked-event rule

When Python identifies blocked non-consensual content, neither Codex nor DeepSeek continues generation through that event. Python emits a `BlockedTurnCheckpoint`; Codex may create a temporary, explicitly noncanonical projection using facts through the checkpoint. CERA may later accept a non-graphic, integrity-bound external completion receipt and resume with non-graphic aftermath only. The complete contract is in `BLOCKED_TURN_AND_RESUMPTION.md`.

## 9. Promotion standard

Architecture stages are promoted by role-specific evidence. A route must pass deterministic contracts, restart/replay/regeneration/branch tests, varied ordinary and high-risk cases, state/prose agreement, and blinded human review. A hard authority, privacy, identity, consent/capacity, cast, branch, or protected-user defect cannot be averaged away.

## 10. Behavioral authority and creator review

D-160 adds claim-level source authority, creator-owned character autonomy and
prompt-handling controls, broad causal event blocks with writer scaffolds,
adaptive causal runway, precise protected-user allowances, effective-character
projections, and atomic psychological development. The controlling details are
in `../architecture/BEHAVIORAL_AUTHORITY_AND_SCENE_DEVELOPMENT.md`.

The default autonomy mode is `both`; the default prompt mode is `adjustment`.
DeepSeek prose is provisional until the required Sol review package and Python
validation are complete and the creator accepts it. This acceptance boundary
supersedes any local-development shortcut that commits a candidate before the
creator can review it. Hard authority invalidity is never overridable, and an
unavailable required Sol stage produces an error with no fallback.

## 11. Branch-bound Reasoner context

The durable story authority remains Python and SQLite. Runtime Codex may keep
branch-bound conversational context only through `ReasonerSessionPort` and a
Python-owned ledger. D-174 maps every checkpoint to a native stored app-server
thread. One accepted checkpoint is current. Every provisional turn is an
immutable stored leaf fork and is promoted only after the creator accepts the
already validated presentation-neutral artifact and Python commits it.

Rejected output is not accepted history. Its stored candidate leaf is archived,
made non-resumable, and excluded from accepted ancestry. V1 retains its
candidate hash, isolated child checkpoint, reason codes, custody event, and
exact creator feedback but no raw rejected prose in CERA authority records.
Explicit feedback becomes a control-plane constraint, branch-local by default;
global scope requires explicit creator selection. A constraint is neither
story evidence nor character knowledge and cannot override a hard validator.

Provider context may improve continuity and reduce repeated prefix processing,
but it is disposable. Every turn reasserts current authority, and a complete
Python reconstruction can replace an unavailable, incompatible, compacted, or
rotated provider session without changing canon.

D-179 activates this branch-bound stored-thread path for the local human-test
route. The Scene Reasoner's creator-selected effort is `medium`, `high`, or
`xhigh`. Effort is part of the compatibility hash: changing it rotates and
reconstructs from Python's accepted branch state rather than mixing effort
identities. The independent realization verifier remains Sol-medium and never
inherits Reasoner conversation context.
