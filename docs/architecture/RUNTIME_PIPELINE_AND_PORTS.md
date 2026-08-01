# Runtime Pipeline and Ports

**Status:** controlling component and stage contract

## 1. Component boundary

Core business logic depends on interfaces, never provider SDKs:

```text
TurnKernel
  <- RawTurnIngressFacade
  <- IntentInterpreterPort (fake only in Structural Contract v2)
  -> EvidenceRepositoryPort
  -> SeedDossierAssembler
  -> SceneReasonerPort
  -> AdultMechanicsPort (conditional, non-psychological)
  -> SceneComposerPort
  -> SceneRealizationVerifierPort
  -> StoryRepositoryPort
  -> DerivedConsolidatorPort (deferred, conditional)
  -> PresentationRendererPort
```

Initial adapters:

- `CodexSceneReasonerPort`
- future qualified `DeepSeekAdultMechanicsPort`
- `DeepSeekSceneComposerPort`
- local Python repository, validation, and renderer adapters

Provider identifiers, model tiers, credentials, endpoint details, and account entitlements belong in deployment configuration.

## 1A. D-186 shadow continuous Planner and Validator route

The additive D-186 route is test-only and leaves the active D-180 path
unchanged:

```text
same branch-bound Planner stored thread
-> Python request-local source/exact-read binding registry
-> rich causal sequence with open DeepSeek realization space
-> DeepSeek V4 Flash complete realization
-> separate branch-bound Validator stored thread
-> complete final sequence + creator review + semantic edit package
-> isolated CANDIDATE world view
-> creator Accept or False Positive
-> atomic ACTIVE promotion + accepted event
-> accepted-final envelope injected once into model-visible Planner history
-> atomic local Planner snapshot + synchronization receipt
```

Planner and Validator have distinct compatibility hashes and physical provider
threads. Provider conversation is advisory. Python owns current world files,
revision preconditions, edit-package validation, creator-action enforcement,
promotion, rollback, indexes, receipts, and durable session snapshots.

The documented Codex app-server `thread/inject_items` operation performs that
history append without starting a model turn. Python records the accepted
envelope first, injects its canonical `[TURN ACCEPTED]` representation, and
then records synchronization. A failure is terminal and blocks continuation;
CERA never silently resends the envelope or falls back to next-turn prompt
piggybacking. The provider thread remains context only, never story authority.

Recent accepted same-scene context is exposed to the next request only through
a Python-owned typed accepted-session projection. Python reloads the exact
accepted pair and event, validates their envelope/item/scene/revision/hash
agreement, and binds the promotion receipt, exact Planner thread, immutable
per-turn snapshot, final synchronization receipt, world, branch, and optional
single knowledge owner. The public projection contains no private items; one
owner projection may add only that owner's private items. A single-NPC beat may
use its exact owner-bound projection without resending the complete character
card. ACTIVE remains required for durable card facts, rules, older recalled
events, and private information absent from that accepted context.

The only V1 route mappings are Planner Sol/xhigh to Validator Sol/medium and
Planner Sol/medium to Validator Terra/high. Sol/high and every other mapping
fail explicitly. Scene Change is a distinct Validator task, uses an explicit
accepted-turn allow-list, and continues both existing physical sessions.

Each Codex role receives one request-bound authenticated read-only world MCP
view with `cera_world_list`, `cera_world_search`, and `cera_world_read`.
Search/list descriptors locate records but are never evidence. Python allocates
the current-source handle before prompting; only an exact world read or a
revision/hash-bound deterministic initial character projection allocates a
record handle. Every hard beat resolves its handles against the current
world/branch/turn, exact path, revision, content hash, visibility, knowledge
owner, and read operation. Invented, stale, sibling, unread, or owner-transferred
bindings fail before composition. Final sequence items and edits remain
traceable through Planner beat keys to those bindings.

Python projects exact Ted action-state and dialogue spans only when a bounded
attribution grammar proves that Ted owns them. NPC-attributed and unattributed
quotations do not create authority. Planner output cites those claim keys and
their source binding; final sequence items carry the exact claim-key union.
DeepSeek returns exact occurrence spans for every realized claim, and Python
validates them before Validator assessment. Whole-beat validation rejects
protected-user semantics that do not preserve an exact supplied span,
including when Ted is omitted from `actor_ids`. Mechanical connectives cannot
carry semantic claims.

Planner can inspect only ACTIVE, labeled non-authoritative DERIVED views, and
its own session; Validator can additionally inspect only its current candidate
ACTIVE_VIEW. The transport's highest supported 32-call
ceiling is runaway protection, not a retrieval quota; reaching it fails the
turn. No filesystem write tool is exposed.

Every Planner, Composer, Validator, and Scene Summary transport durably marks
the true provider-submission boundary. Codex distinguishes worker start and
local preflight from `thread_run`; only `thread_run` proves a call was submitted.
DeepSeek marks the immediate HTTP dispatch boundary. A response that later
fails bridge finalization, decoding, schema/domain validation, or world-MCP
reconciliation still counts as one call and retains privacy-safe
receipt/telemetry/tool hashes. An unresolved submitted/in-flight record consumes
the bounded slot after restart until governed diagnosis resolves the ambiguity;
a proven local preflight failure consumes zero.

## 2. Stage assignment

| Stage | Owner | Why | Input -> output | Authority | Validation/failure | Calls/latency |
|---|---|---|---|---|---|---|
| Intake and identity | Python | Exact, repeatable, security-sensitive | raw request -> `TurnRequest` | Authoritative metadata | Schema/hash failure -> explicit error | 0; local milliseconds |
| Intent interpretation | Provider-neutral port; scripted fake only | Produces advisory spans, responders, route hints, and evidence obligations without owning authority | raw envelope -> `IntentInterpretationDraft` | Advisory | Python rejects gaps, rewrites, cast/route drift | 0 in this phase |
| Seed assembly | Python | Snapshot, privacy, and exact-record authorization are deterministic | obligations + pre-expanded evidence -> dossier + receipt | Authoritative authorization result | Ambiguous/unavailable remains unresolved; tamper/staleness rejected | 0; bounded local lookups |
| Branch/replay resolution | Python | Transactional and deterministic | request IDs, branch head -> generation context | Authoritative | Conflict/stale head -> no mutation | 0; local |
| Preflight classification | Python rules plus typed metadata | Hard identity, consent, privacy, route boundaries cannot be delegated | source units + current authority -> route/checkpoint | Authoritative gate | Unknown stays unknown; blocked route stops | 0; local |
| Semantic interpretation | Scene Reasoner | Requires contextual judgment | seed dossier -> interpretation/query plan | Advisory until validated | Unavailable/malformed -> explicit error | 1 reasoner turn, possibly tool round-trips |
| Retrieval expansion | Codex requests; Python executes | Codex knows what evidence is missing; Python owns access | typed query -> filtered evidence | Evidence records retain own authority | Privacy/branch/budget violation -> denied result, never guessed | Usually 0-4 bounded local tool calls |
| Character/sequence decision | Scene Reasoner | Best role for psychology, consequence, multi-character logic | evidence dossier -> `SceneDecision`/`SequencePlan` | Advisory | Python checks IDs, evidence, cast, authority, consent, feasibility | Included in reasoner call |
| Decision validation | Python | Hard invariants and citations are testable | decision + dossier -> validated decision | Authoritative acceptance/rejection | Failure -> explicit error; no Composer | 0; local |
| Protected adult mechanics | Adult Mechanics adapter | Useful only for consent-valid protected realization detail and never re-decides psychology | validated decision + safe ledger + selected craft refs -> `AdultMechanicsProposal` | Advisory operational | Python checks adult/consent/scope/coverage and decision fidelity; failure -> error | Conditional +1 provider call after qualification |
| Complete prose | DeepSeek Scene Composer | Prose, voice, pacing, and physical realization | validated decision/plan/context -> candidate prose | Creative candidate | Failure/malformed -> error; no fallback | Normally 1 provider call |
| Realization verification | `SceneRealizationVerifierPort` after Python structural validation | Separate evidence is needed that planned beats/participants occur in prose | candidate + semantic expected beats + selected participants + exact protected-user authority -> verifier draft -> Python-validated receipt | Advisory inspection; Python owns acceptance and crossing | Missing/inconclusive/rejected/incomplete/malformed -> no event or publication; no retry/fallback | Scripted fake: 0 calls; qualification-eligible Sol-medium adapter: exactly 1 call only when separately authorized |
| Acceptance/commit | Python | Atomic truth and recovery | validated candidate + state delta -> accepted artifact/receipt | Authoritative | All-or-nothing transaction | 0; local DB |
| Rendering | Python/presentation adapter | UI must not alter story truth | accepted prose -> ST display | Presentation only | Render failure leaves artifact committed and retrievable | 0; local |
| Derived consolidation | `DerivedConsolidatorPort` proposes; Python validates/commits | Semantic memory benefits from reasoning, truth requires evidence and atomicity | bound exact event evidence -> typed memory/relationship/thread/development proposal or no change | Advisory, then validated-derived authority only after commit | Failure is explicit; accepted prose/head/generation remain; no fallback | Deferred, conditional 0-1 call |

Latency figures are qualitative until transport benchmarks exist. Expected normal provider cost is one Codex reasoning call plus one DeepSeek composition call. A protected adult plan adds one DeepSeek call. Provider failures do not trigger a substitute model.

## 3. `SceneReasonerPort`

Conceptual operations:

```text
reason(request, evidence_tools) -> SceneDecision
resume_after_external_event(request, receipt_evidence, evidence_tools)
  -> AftermathDecision
project_temporary_aftermath(checkpoint, evidence_tools)
  -> TemporaryAftermathProjection
```

The adapter must:

- send only the authorized packet;
- expose only typed retrieval operations;
- parse one documented result schema;
- record transport/model/effort/tokens/latency without prompt secrets;
- refuse to invent a result on timeout, denial, or parse failure.

Phase 5 implements this provider-neutral port plus a scripted `FakeSceneReasonerPort` for test/development only. The fake executes declared evidence-tool calls and returns a declared structured outcome; it contains no psychology or scenario rules and is rejected by production configuration.

Phase 8 implements the two resumption operations as provider-free contracts with a production-prohibited `FakeSceneReasonerResumptionPort`. The temporary projection remains optional scratch state. A validated external receipt can produce an `AftermathDecision`, but that decision remains advisory until the existing Composer contract and one atomic Python commit accept the aftermath. No external handler or live Codex transport is part of this implementation.

Phase 11 adds a separately bounded Codex SDK/app-server transport primitive. It uses ChatGPT-session authentication, an ephemeral thread, an isolated workspace containing only a privacy-safe progress sidecar, a no-files/no-network permission profile, explicit model/effort, JSON Schema output, a killable parent timeout, and privacy-safe receipts. The supervised parent terminates the full SDK/app-server process tree on timeout. The last allow-listed worker stage and an observed-dispatch count survive in failure evidence; no request is retried.

After the v10/v11 verifier stalls, CERA added and subsequently live-qualified
`PersistentNoMcpCodexRunner`. It may reuse one supervised app-server
process only for one fixed role/model/effort identity, serializes calls, and
creates a fresh ephemeral Codex thread in a fresh isolated workspace for every
request. Reasoner and Verifier require separate runner instances; conversation
and story state are never reused. Request-bound MCP reasoning remains on the
isolated one-shot worker. A hang closes the persistent runner and fails the
route without replaying that request. Four unique non-story Sol calls qualified
one two-request Reasoner process and one two-request Verifier process. The
undispatched continuous v12 runner may activate reuse only from the exact
hash-bound passed evidence; it still requires sufficient full-route creator
call authority and establishes no story-role quality by itself.

### Provider-free branch-bound session seam

D-172 adds an inactive `ReasonerSessionPort` above the Codex transport. It does
not replace or activate the current fresh-thread adapter. Python owns a durable
session ledger whose active pointer always names one accepted checkpoint. Each
new Reasoner turn forks a candidate provider thread from that checkpoint.

```text
accepted checkpoint
-> candidate fork
-> provisional Composer/review work
-> Python accepted receipt and checkpoint promotion
or
-> rejected/invalidated child deletion; accepted pointer unchanged
```

Provider conversation remains advisory. A current `ContextAuthorityDelta`
reasserts branch head, generation, authority revision, evidence snapshot,
exact evidence versions/hashes, revocations, and active creator constraints.
Previously materialized evidence may be referenced only when its exact version,
hash, sections, and checkpoint belong to accepted ancestry. Reconstruction
resends the complete Python-authorized context.

Regeneration forks from the accepted checkpoint for the replaced artifact's
parent, not from the artifact being replaced. Python still binds the candidate
to the current head and generation until an atomic regeneration commit wins.
A branch fork receives a distinct session identity and provider thread fork at
the exact accepted fork artifact. Rejected and sibling context cannot enter the
new accepted lineage.

The current v24 Reasoner prompt can be split byte-for-byte into stable
instructions and a variable canonical packet. This provider-free compiler is
shadow-only; it proves no evidence or instruction is removed and does not
change the active route. The verifier retains an independent session role and
must not inherit Reasoner context.

### Native stored Codex checkpoint adapter

D-174 corrects D-173's ephemeral-fork mismatch without changing the Python
state machine. Each accepted checkpoint now maps to an immutable stored Codex
thread. A new candidate is a stored leaf fork of that checkpoint; CERA never
runs another Reasoner turn directly on the accepted parent. This provides
persistent context while preserving clean rejection, exact regeneration, and
sibling-branch isolation.

```text
Python accepted checkpoint A -> stored provider thread A
current cue                 -> stored candidate fork B -> one Reasoner turn
creator Accept + commit     -> checkpoint B becomes accepted
creator Decline/failure     -> archive leaf B; A remains accepted
regenerate B                -> fork from B's accepted parent A
branch at A                 -> independent stored fork rooted at A
restart                     -> resume the stored thread for the accepted checkpoint
```

The installed SDK exposes start, resume, fork, and archive at its high-level
surface. Empirical D-178 qualification found that an otherwise empty stored
thread must be named before the allocating app-server exits so its rollout is
materialized. It also found the local generated `thread/delete` path unreliable.
CERA therefore archives rejected/failed candidate leaves, which makes them
non-resumable and keeps them outside accepted ancestry. Accepted ancestors are
never archived while active.

Acceptance on the active D-180 immutable-checkpoint path does not create a
second model call or inject a provider item. D-186 is a separate shadow
experiment and uses the documented app-server history-injection operation
described in section 1A. SQLite custody evidence contains
only provider-thread hashes, lifecycle status, retention status, and the fixed
fact that provider context is not story authority.

The stored-turn worker launches a fresh supervised app-server process for the
actual call, resumes the exact stored candidate, and rebuilds the current
request-bound MCP bearer and loopback binding in that process. This preserves
request-scoped evidence authority; it does not reuse a stale bearer from an
earlier turn. D-179's canary and five-turn qualification passed, so this worker
is active on the local SillyTavern human-test route.

The SillyTavern `Sol` selector maps `M`, `H`, and `Ex` to Reasoner efforts
`medium`, `high`, and `xhigh`. Effort is compatibility-bound; a change rotates
and reconstructs the branch session from accepted Python authority. The
independent verifier remains Sol-medium.

Phase 12 adds the request-bound MCP projection of the existing `ReasonerEvidenceTools`. Structural Contract v4 advances that bridge to seven operations: `cera_get_turn_snapshot`, `cera_resolve_entities`, `cera_search_evidence`, `cera_search_query_plan`, `cera_fetch_evidence`, `cera_get_character_sections`, and `cera_get_continuity`. `cera_search_query_plan` executes one Codex-owned bounded primary term set plus explicit paraphrase variants as one metered search; it still returns candidate references rather than exact decision evidence. The active MCP v4 projection makes `cera_fetch_evidence` expand one returned evidence ID per call through singular `evidence_id` plus an advertised `sections` array; Python's internal batch-fetch API remains unchanged. One authenticated loopback server wraps one immutable snapshot. Global MCP configuration is cleared for the worker; its ephemeral bearer, URL, arguments, results, story text, and private evidence are not retained in receipts. Codex SDK tool observations must exactly match the bridge's ordered hash/error trace. Pre-dispatch framework rejection is also recorded as a value-free field/reason receipt. After the first live story qualification exposed unnecessary failed lookups on sufficient exact dossiers, D-106 makes bridge exposure conditional: investigation calls still require this bridge, while Python may omit MCP entirely when the exact seed is sufficient. An unbridged call cannot report tool usage and retains no fabricated bridge receipt.

Phase 13 implemented the historical provider-authored `ReasonerOutcome` path.
Structural Contract v2 supersedes that provider DTO with
`CodexReasonerDraftV2`. Structural Contract v3 advanced the provider DTO to
`CodexReasonerDraftV3` and the Python-compiled authority to
`ReasonerOutcome` v4. Structural Contract v4 advances the active boundary to
`CodexReasonerDraftV4` and `ReasonerOutcome` v5. The draft may name an exact,
uniquely occurring source quote as an action/dialogue claim for the protected
user. Python derives the source offsets and hash; an absent or repeated quote
is invalid. The existing `ReasonerCoordinator` still validates the
compiled authoritative outcome, but Python now derives decision/segment/beat
IDs, hard citations and versions, route labels, and hashes. Historical schemas
remain readable only for fixture migration.

The V4 provider-free correction adds a raw-payload semantic precheck for the
complete Reasoner status matrix and model-authored semantic-set uniqueness.
Its diagnostics are field-path codes without values. JSON Schema remains a
syntax constraint; Python remains the cross-field and authority validator.

Reasoner adapter v25 removes canonical evidence UUID copying from the live
provider output contract. Exact seed records carry deterministic request-local
aliases (`evidence:seed_NNN`), and each exact MCP fetch receives a bounded
request-local alias (`evidence:fetch_NNN`). The provider schema permits only
those alias shapes for the current invocation. Python resolves aliases to the
exact authorized records before domain decoding and rejects unallocated or
stale aliases. Canonical evidence IDs, citations, versions, and hashes remain
Python-owned; aliases never persist as story authority and never transfer
between stored turns.

The provider compatibility layer now sits inside the transport boundary:

```text
provider-neutral DTO schema
-> versioned provider projection
-> recursive dialect preflight
-> one-shot provider dispatch
-> JSON decoding
-> unchanged authoritative dataclass and cross-field validation
```

OpenAI receives a strict-schema projection; DeepSeek receives JSON-object mode
plus a versioned prompt-schema projection. Projection can simplify provider
syntax but cannot accept story authority, derive bookkeeping, relax privacy or
consent, or bypass any later Python validator.

`SceneDecision` includes current-reply beats and may include future `SequencePlan` segments. Future segments are intentions, not committed events. Each later turn revalidates them against current branch truth.

## 4. `SequencePlan`

A SequencePlan represents causal possibilities without pre-authoring the user's next choice:

```text
current_segment:
  binding beats for this reply
future_segments:
  conditional character/world consequences
  activation conditions
  invalidation conditions
  open Ted choice
```

Only the current accepted reply becomes story truth. Future segments are branch-local planning aids and must be revised or discarded when new source changes the scene.

## 5. Composer boundary

The Composer packet contains:

- exact current source units allowed for realization;
- validated responding characters and moves;
- current-segment beats and stop boundary;
- presentation-neutral dialogue contract;
- relevant scene/material/knowledge facts;
- selected realization cards and craft modules;
- validated adult mechanics only when applicable;
- prohibited additions.

The Composer does not receive irrelevant private memories, inactive character cards, the full Adult EX library, raw retrieval indexes, or database access.

Phase 6 implements the provider-neutral `SceneComposerPort` and a declarative `FakeSceneComposerPort` only. The fake returns one configured `ComposerCandidate` plus a separate `RealizationManifest`; it has no character, genre, phrase, psychology, or scenario logic and is rejected in production.

Phase 11 adds a one-shot DeepSeek Chat Completions transport primitive for the current `deepseek-v4-pro` and `deepseek-v4-flash` model IDs. It reads `DEEPSEEK_API_KEY` only from the process environment, requires the approved HTTPS endpoint, applies request/output/cost ceilings, makes exactly one call, verifies the returned model identity, and stores only hashed/sanitized receipt metadata.

D-169 makes non-thinking explicit for the default Flash Composer call. Codex
already owns the causal plan; DeepSeek owns realization, so hidden Composer
reasoning is optional rather than a second planning authority. A provider may
still return any documented terminal finish reason. Only `stop` proceeds to
typed decoding. A non-stop, empty, malformed, or non-object completion fails
closed with no retry or fallback. When response metadata is available, a
`ProviderFailureCallReceipt` retains the exact normalized finish reason and
the privacy-safe call receipt (timing, usage, model, cost, and hashes), never
the partial response text.

Phase 14 added the first typed Composer adapter. Structural Contract v2 used
`DeepSeekCompositionDraftV2` with provider-copied quote anchors. Structural
Contract v3 replaced that boundary with `DeepSeekCompositionDraftV3`:
ordered local-keyed story segments plus source, realization, and terminal
segment references. Python strips outer segment whitespace, joins segments
with a double newline, and derives story text, offsets, spans, hashes,
participant projections, and manifest bookkeeping. Structural Contract v4
used `DeepSeekCompositionDraftV4`: one source obligation may map to multiple
ordered unique segment keys, and every adult specificity obligation maps by a
Python-owned `obligation_key` to its exact beat/channel/owner and segment
ranges. `DeepSeekCompositionDraftV5` replaced nullable
`source_unit_id`/`beat_id` realization references with one mandatory typed
`authority_id`, restricted to a source unit or beat. Adult channel names never
substitute for general `RealizationKind`. The active
`DeepSeekCompositionDraftV6` removes redundant provider-authored `owner_id`;
Python derives the realization owner from the validated authority map and
normalizes only exact duplicate references before applying the same domain
obligations.
Every selected NPC still
requires bounded identity or voice evidence before a provider call.

Structural Contract v2 adds `SceneRealizationVerifierPort` after Composer
validation. Active verification request v4 carries the surrounding exact
protected-user source only as context and a separate list of exact action or
dialogue claims as the sole semantic allowances. It also carries semantic
expected-beat descriptions and states and a required
no-unsupplied-realization check in addition to candidate, participants,
boundaries, and Composer anchors. Acceptance must cover the exact beats,
participants, and semantic checks.

For ordinary SillyTavern-style turns, the user's submitted message is already
visible before CERA's reply. Python therefore retains each exact source unit as
context-only verifier authority even when no direct narration claim is
required. NPC prose may perceive, remember, interpret, or react to that
already-visible source. Only an exact Python-validated claim authorizes direct
renarration of a supplied protected-user action or dialogue. Pronouns,
background presence, and previously explicit location are not by themselves
new protected-user authorship; a rejection finding must anchor the newly added
story-progressing behavior or private state.

The first qualification-eligible adapter is
`CodexSceneRealizationVerifierPort`, pinned to Sol-medium. It makes exactly one
independent post-composition call, exposes no retrieval or other tools, and is
prompted and typed only to inspect the supplied candidate. It may not generate,
continue, rewrite, improve, repair, or replace prose. The model returns semantic
sets and, for a violation, a unique exact candidate quote with a literal-zero
compatibility sentinel. Python derives occurrence, offsets, and hashes, checks
status-dependent and set-coverage semantics, and owns the final
accepted/rejected/inconclusive receipt. Malformed, unavailable, rejected, or
inconclusive output fails closed without retry or fallback.

The durable receipt retains only safe codes, hashes, adapter evidence, and an
optional privacy-safe provider receipt. Candidate prose, prompts, raw model
output, and protected source remain transient. Scripted/echo ports remain
production-prohibited and not qualification-eligible.

On later typed or semantic failure, the operational bundle retains every
privacy-safe receipt handle from crossed stages. Qualification export writes
that bundle before disposable storage closes; it retains no raw prose, source,
private evidence, prompt, or secret.

Phase 15 adds Python-owned `ComposerContextAssembler` and `LiveShapedTurnPipeline`. Exact evidence already authorized and used by the Reasoner remains transiently available for context selection. The original path fetches one exact `speech_system` card per selected NPC; the provider-free Adult ON/EX extension can instead extract only the plan-selected sections plus baseline voice. DeepSeek still receives no retrieval tools.

Hanezawa V1.2 extends this existing seam rather than adding another provider
stage or provider-owned bookkeeping object. The v6 Codex adapter may search,
fetch, and cite active-speaker `character_expression` evidence when the
current meaning actually requires it. Python maps those exact citations to
speaker-scoped `CHARACTER_EXPRESSION` context blocks and rejects any
unselected-character or wrong-owner applicability. The v6 DeepSeek prompt
realizes fresh wording under the selected intent and boundary. It cannot
select trust state, consent, response ownership, participants, or durable
truth.

Phase 16 adds the separate `OrdinaryTurnCommitBuilder` and coordinator. It revalidates the complete live-shaped chain, creates no derived records, and atomically commits source, generation, presentation-neutral artifact, complete sanitized receipt evidence, branch head, and `CommitReceipt`. Append and regeneration topology was introduced in `cera.scene_composer_request.v2`; the Adult ON/EX extension advances the current request to v3. Python verifies that regeneration replaces the current head with an immutable sibling. Adult, blocked, and aftermath results remain rejected by this ordinary publisher.

Phase 17 closes the publication-to-memory evidence gap. The ordinary transaction also inserts one system-private direct accepted-turn event containing only validated current-segment structure/source/artifact bindings. It is objective event evidence, not a memory or psychology update. `PostPublicationCoordinator` then runs pure rendering and deferred consolidation independently after commit; either may fail without changing accepted story truth. A committed consolidation is discovered by its artifact-derived request ID on replay and is not invoked or inserted twice.

Phase 18 makes those consumers restart-safe. Render, consolidation, and dependent derived-view work identities are inserted atomically with the story artifact. The dispatcher claims one item at a time and records completed, no-change, or sanitized failed evidence. It never retries failed or interrupted work unless the caller supplies the exact failed work ID and an authorization reason. This is provider-free operational infrastructure, not a background scheduler or live-route qualification.

Phase 19 places `PostPublicationOperationsService` above that dispatcher. It is the only operator-facing Python boundary for safe inspection, pending dispatch, interrupted recovery, and failed retry. Its registered reports and receipts deliberately omit story prose, evidence, provider/request payloads, protected identity, and raw rationales. No CLI, HTTP, SillyTavern, or production adapter is implied.

Phase 20 places `ProviderFreeOrdinaryApplication` above the complete ordinary stack. It is explicitly prohibited in production and starts from an already prepared typed Reasoner request, not raw SillyTavern text. It performs exact committed-request replay before adapters, otherwise executes the existing bounded Reasoner/Composer path, atomically publishes story/event/work, dispatches requested downstream consumers, and returns accepted presentation-neutral prose plus the safe operator report. Adult and non-ordinary routes are rejected.

The provider-free Adult ON/EX gate extends the non-committing live-shaped path only. A consent-valid `ReasonerOutcome` v2 may carry an `AdultCraftNeed`; its beat needs are a non-empty subset of current beats that need adult-specific realization craft. Python selects catalog fragments, builds a channel-aware `SpecificityContract`, and assembles only selected realization-card sections. After deterministic beat/channel checks, a provider-neutral semantic-specificity port sees only locked beat-local prose and semantic bindings, returns a typed result, and produces a no-content receipt. It runs once per candidate with no retry or fallback. One typed beat-scoped repair may run; the repaired candidate must pass a new semantic request before complete structural revalidation. `SceneComposerRequest` v3 and `LiveShapedTurnReceipt` v3 bind the new evidence. No adult publisher or production application was added.

Python performs structural candidate validation against the validated decision and exact source bindings. It checks cast, floor, intent/action hashes, current-beat order, creator-event source-unit order/state, protected-user permissions, declared semantic-inference boundaries, complete-Core flags, and presentation leakage. This is not a claim that Python can prove psychological or prose quality from spans and IDs.

Acceptance, persistence, and rendering are separate:

```text
ComposerCandidate + RealizationManifest
-> structural ComposerValidationReceipt
-> deterministic adult checks when active
-> independent SceneRealizationVerificationReceipt
-> Python FinalStoryAcceptanceReceipt
-> in-memory AcceptedStoryArtifact
-> atomic TurnCommitBundle and CommitReceipt
-> pure RenderedStory projection
```

Structural Composer acceptance never creates an accepted artifact. The artifact is created only after every active pre-publication check accepts and is bound to `FinalStoryAcceptanceReceipt`. Its deterministic transaction ID is reserved correlation only; durable branch truth still requires a later successful `CommitReceipt`.

## 6. `DerivedConsolidatorPort`

Conceptual operation:

```text
consolidate(request) -> ConsolidationExecutionResult
```

The request contains a bound evidence snapshot plus exact authorized event records, eligible derived record types, a maximum record count, and hard boundaries. The result contains either a typed proposal or an explicit unavailable failure; it never contains story prose or a Genesis edit.

Python validates proposal authority, source refs, evidence ownership/knowledge, protected-user boundaries, record-type-specific sections, supersession lineage, and snapshot freshness. It then journals and atomically commits the exact validated bundle under the branch's `authority_revision`. Public, owner-private, and system-private views are rebuilt separately and can fail without changing the canonical records or accepted story.

Phase 9 implements a production-prohibited scripted fake for deterministic contract testing. Synthetic proposals remain noncanonical. Disposable real-Genesis calibration may commit `validated_derived` records from validated events, but still makes zero provider calls. A future Codex adapter requires separate qualification and authorization.

## 7. Model tier policy

Use role names in contracts and qualify concrete models per role.

- Default reasoner: highest-quality qualified Codex reasoning tier whose latency is accepted by the creator.
- Faster Codex/Terra-style tier: optional ordinary fast path only after matched tests show non-inferiority on logic and boundaries.
- Lower/older tier: evaluation comparator, not automatic fallback.
- DeepSeek: Composer and a possible conditional Adult Mechanics adapter only after separate provider/content-family qualification.
- ChatGPT Instant: off by default; consider for a narrow advisory verifier or low-risk classification only if it beats deterministic Python or the existing reasoner on measured value.

Changing a model tier creates a new route identity and requires promotion evidence.

Phase 10 implements the provider-neutral evaluation and promotion foundation. Each candidate binds role, adapter, prompt/transport versions, configuration hash, and live model/revision where applicable. Offline fake/local runs can prove contract and harness behavior only. Live qualification additionally requires sealed holdout evidence, privacy-safe telemetry, zero hard defects, role-policy thresholds, matched blinded human review, and a separate creator promotion decision. Evaluation code cannot mutate story authority, promote a route, or deploy CERA.

## 8. No-availability behavior

Stable errors:

- reasoner unavailable -> `CERA_REASONER_UNAVAILABLE`
- composer unavailable -> `CERA_COMPOSER_UNAVAILABLE`
- adult planner unavailable -> `CERA_ADULT_PLANNER_UNAVAILABLE`
- derived consolidator unavailable -> `CERA_CONSOLIDATOR_UNAVAILABLE`
- invalid provider result -> role-specific contract error

Each error includes a trace ID and retry advice for the creator, but CERA does not automatically retry, fall back, or commit.

Structural Contract v2 also appends `TurnStageAuditEntry` and, on failure,
`TurnFailureEvidenceBundle` in SQLite migration 11. Structural Contract v3
uses additive `TurnFailureEvidenceBundleV2`, retaining valid handles plus
canonical payloads only for an explicit allow-list of privacy-safe receipt
schemas. These records retain only safe IDs, hashes, stage/call counters, and
receipt data that already forbids protected content. They never
retain raw source, accepted/candidate prose, private evidence, prompts, or
secrets. Restart inspection is read-only and never authorizes automatic resume.

Rejected candidate prose has a separate opt-in qualification-review port. Its
bounded excerpts are creator-local, manually deleted after adjudication,
non-authoritative, excluded from general runtime receipts and telemetry, and
represented in a failure bundle only by review ID/hash.
