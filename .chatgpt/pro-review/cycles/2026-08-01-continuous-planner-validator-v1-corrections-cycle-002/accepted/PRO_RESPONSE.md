# CERA ChatGPT Pro Repository Review

review_cycle_id: 2026-08-01-continuous-planner-validator-v1-corrections-cycle-002
reviewed_checkpoint_id: 2026-08-01-continuous-planner-validator-v1-corrections-002
reviewed_checkpoint_git_sha: 4722b5e08fc981cac375374e6c845c738385ed33
reviewed_evidence_sha256: ecc62c9910b340517c025511ed97e50a0fc93283b97f233aed08831f158934b2
reviewed_task_set_sha256: 4241ab8fd6085b60c8d49a35240c37e92064e5cd65b5b07a261decd4e9479692
reviewed_job4_task_id: continuous-corrections-v2-provider-free-integration-audit
response_nonce: 179d948d2147bd0b542426e746177a119400d28f5db8e764cf330ea6195fa80f
review_scope: repository_cycle
review_disposition: corrections_required

## Independent findings

1. The checkpoint, cycle, predecessor, manifest, task-set, publication, trigger, and completed Job 4 identities are coherent. Progressions 1-3 and Job 4 made zero provider calls. The direct provider-free Job 4 completed on its first attempt with 23/23 labeled assertions across 20 unique unittest methods, and the source and disposable SQLite hashes remained unchanged. The active `cera.active_runtime.d180.v1` profile remained unchanged. No live story, production branch, service, installed SillyTavern, deployment, merge, remote, or push effect occurred. Prior failed and correction-cycle evidence remains preserved rather than rewritten.

2. The v2 character-summary contract is materially stronger. Python now builds or validates each summary against an exact record path, character identity, internal revision, source hash, stable field pointers, and derivation receipt. Stale text, wrong-character summaries, and fabricated latest-change lists fail before the Planner call. ACTIVE and DERIVED evidence are also distinguished, private evidence is restricted to one exact NPC owner per beat, and a derived scene summary cannot be the sole hard authority for an NPC decision. These corrections close the specific defects identified in the preceding review.

3. The corrected live ten-call harness is nevertheless still not runnable as frozen. `source_character_summary()` now returns an envelope whose `source_path_or_record_id` already begins with `ACTIVE/`, but `JobHarness.run_turn()` prepends another `ACTIVE/` before calling `allocate_initial_projection`. The resulting path is `ACTIVE/ACTIVE/Characters/...`, so the first summarized turn will fail during local evidence preparation before the Planner call. The generic `ContinuousShadowTurnCoordinator` uses the validated path correctly, but the separately frozen live harness has drifted from it.

4. A second pre-provider harness defect remains in the Codex adapters. The live harness wraps `CodexSDKTransport` in `StablePrefixTransport`. `_transport_stored_thread_sha256()` then reads `transport.runner.provider_thread_id`, but `StablePrefixTransport` exposes only `transport`, `route`, and `stable`; it does not expose or delegate `runner`. The first Planner or Validator adapter call therefore cannot obtain the required stored-thread hash. Provider-free adapter tests instantiate a direct fake transport with a `runner` attribute and do not exercise this exact wrapper chain.

5. The live harness also bypasses part of the new acceptance-synchronization protocol. After `world.apply_creator_action()`, it appends and injects the accepted final sequence into the Planner session, but it does not call `mark_acceptance_planner_ledger_appended()` or `mark_acceptance_model_synchronized()`. A successful disposable turn would therefore leave its acceptance journal permanently marked pending even though model-visible injection occurred. The generic coordinator performs these updates, but the live harness does not. This means the reviewed harness does not yet prove the same state machine that the provider-free runtime tests qualify.

6. Provider-call accounting is improved but remains inexact at the actual transport boundary. Local output-schema and MCP-binding construction now occurs before the ledger, and adapter-level post-return failures count as invoked. However, `ContinuousProviderCallLedger.execute()` records `transport_invoked` only after `dispatch()` returns or raises and infers invocation from `external_provider_calls_observed`. Some real `CodexSDKTransport` post-provider failures attach a provider receipt while retaining the exception's default observed-call value of zero, including MCP-observation rejection and malformed/non-object JSON paths. The ledger will classify those as `pretransport_failed` even though a provider request completed. A process loss while a request is in flight can also leave only a `prepared_not_invoked` record because no durable event is written at the exact submission boundary. Provider receipt presence and worker progress must not be overridden by an optional zero-valued exception field.

7. The complete acceptance journal now recovers ACTIVE, exact accepted pair and event, promotion receipt, optional False Positive diagnostic, and timeline evidence. Pending model injection correctly blocks another turn. The successful synchronization path is still not durably closed. `ContinuousShadowTurnCoordinator` mutates the in-memory Planner ledger, performs model-visible injection, and marks the world journal synchronized, but it does not atomically persist a `ContinuousSessionSnapshotStore` snapshot. The snapshot checkpoint remains explicit only in the old Job 4 harness. A crash after the world journal says `synchronized` but before a durable local session snapshot can leave restart state without the accepted-envelope ledger or with an older session handle while `pending_acceptance_synchronization()` reports nothing pending. The acceptance transaction also does not bind the Planner thread hash or a typed injection-operation receipt.

8. Protected-user authority remains too semantic-free. Requiring `character:ted` to use `EXACT_SOURCE_ONLY` proves that the current user message was cited, but it does not prove that the specific Ted action or dialogue described by the beat appeared in that message. A beat can cite a user message containing only a greeting and then claim that Ted entered the house. It can also omit Ted from `actor_ids` while placing an invented Ted action, dialogue, movement, decision, or thought in free-text perception, observable-direction, continuity, or resulting-state fields. The current negative test covers only a minimal-connective beat that explicitly names Ted as the actor. Exact protected-user claims need Python-owned source spans or typed supplied-action/dialogue claims, and every beat field must be checked against those claims.

9. The evidence policy currently prevents the continuous session from serving as an exact accepted current-scene substrate. Every beat with an NPC actor must cite an ACTIVE world-record binding in the current request. Previously accepted final-sequence envelopes already injected into the same Planner thread are not represented as Python-owned request evidence. Consequently a same-scene continuation must receive a character summary again or perform another ACTIVE read even when the immediately preceding accepted sequence already contains the exact relevant state. This is safe but materially conflicts with the creator's main optimization goal: recent accepted user-message/final-sequence pairs should carry the active scene, while file reads restore exact older or durable information on demand. Accepted session context needs its own receipt-bound evidence class rather than being either untrusted latent memory or forcing repeated card retrieval.

10. Validator-derived character summaries are still not proven to be accepted Validator output. `_validate_derived_character_summary_source()` requires a hash-bound `CANDIDATES/.../VALIDATOR_PACKAGE.json`, but it does not require that package to have an accepted promotion receipt and does not bind the summary text or latest-change list to a typed field or edit operation in the package. The provider-free test creates an arbitrary summary record beside a candidate package, computes a self-consistent derivation receipt, and accepts it. A rejected candidate can therefore become the provenance anchor for a later Planner summary. The derived-summary path should either be removed for V1 or require an accepted package plus an exact typed summary/update payload.

11. Direct MCP retrieval of a derived character-summary file can also lose its private ownership. `DERIVED/CharacterSummaries/...` is classified by `_record_type()` as `charactersummaries`, not `characters`. Unless the record explicitly carries `visibility` and `knowledge_owner_id`, `_read()` defaults it to public. The runtime's preselected summary binding is manually marked character-private, but a Planner-initiated MCP read of the same derived record can create a public binding containing private character reasoning. Derived character-summary records must be classified as character-private by record type and bound to their `character_id` owner.

12. The root diagnostic surface is improved but not complete. SDK import, active-profile inspection, backend construction, compatibility construction, stored-thread materialization, and role separation are named operations. Entering the Codex context with `stack.enter_context(codex_context)`, opening/checking the disposable SQLite connection, and some lifecycle-directory operations remain outside a named diagnostic wrapper. These are pre-provider failures and should preserve exact safe ownership before a live canary.

13. The provider-free Job 4 accurately discloses one direct attempt and distinguishes unique tests from labeled assertions. Its passing scope does not include the exact `StablePrefixTransport` wrapper, the double-`ACTIVE` summary path, actual `CodexSDKTransport` MCP/JSON post-provider failures, process death during an in-flight ledger entry, successful injection followed by a pre-snapshot crash, rejected-package-derived summaries, derived-summary MCP privacy, protected-user free-text invention, or a same-scene continuation relying only on accepted session evidence. The current passing audit therefore supports the corrected contracts but does not yet justify a live ten-call canary.

## Required corrections

1. Reconcile the frozen live harness with the generic corrected runtime. Remove the double `ACTIVE/` path construction, validate every character-summary envelope before binding, expose or recursively unwrap the underlying Codex runner for stored-thread identity, and apply the exact acceptance-journal ledger/synchronization updates after every disposable acceptance. Add one provider-free test that instantiates the exact `StablePrefixTransport` and `JobHarness.run_turn` path through the first pre-provider boundary.

2. Move provider-call accounting to the real transport submission boundary. A transport or runner must durably signal `transport_invoked` immediately before the external request is submitted. Provider receipt presence, worker-progress evidence, or an observed value of one must force invoked accounting; an optional zero field must not override stronger evidence. On restart, an unresolved prepared/in-flight record must be treated as ambiguous and consume or block the bounded call slot rather than being silently reused. Test the actual Codex MCP-observation, malformed JSON, non-object JSON, timeout, and worker-failure paths as well as DeepSeek transport failures.

3. Complete successful acceptance synchronization as one durable transaction. Bind the Planner provider-thread hash, accepted-envelope hash, model-visible injection operation/receipt, and `ContinuousSessionSnapshotStore` snapshot into the acceptance journal. Do not mark the transaction synchronized until the local session snapshot is atomically persisted. Add crash tests after the in-memory ledger append, after provider injection returns, after the world journal update, and before and after snapshot replacement. Ambiguous injection must remain typed pending and block continuation without automatic replay.

4. Add Python-owned protected-user source claims. Parse or mechanically project exact supplied dialogue/action spans from the current message and require every protected-user event in every rich-beat field to bind one of those claims. A mechanical connective may affect only formatting or continuity syntax and cannot establish movement, action, speech, thought, choice, consent, emotion, or a new story fact. Add negative tests for Ted invention with and without Ted in `actor_ids`.

5. Add a receipt-bound accepted-session evidence class for recent accepted user-message/final-sequence envelopes. It must bind world, branch, turn, acceptance receipt, envelope hash, Planner thread/session identity, and synchronization state. Permit this exact evidence to support current-scene continuity without resending the same character summary, while still requiring ACTIVE authority for durable card facts, rules, private information not present in the accepted sequence, and older recalled events. Test a multi-turn scene where Turn 2 uses Turn 1's accepted sequence with no repeated character card or file read.

6. Remove or harden Validator-derived character summaries. A derived summary must originate from an accepted Validator package and an exact typed summary/update field or operation whose bytes match the derived record. It must bind the promotion receipt and accepted event. Rejected, superseded, merely candidate, or manually assembled package material must fail. MCP reads of `DERIVED/CharacterSummaries` must always remain character-private and owner-bound.

7. Finish named root diagnostics for Codex context entry, SQLite open/integrity/close, lifecycle-directory creation, and every other pre-provider setup step. Preserve safe stage and operation evidence without credentials or raw story material.

8. After these corrections, run the complete focused and provider-free suites, compilation, documentation and schema inventory validation, active-profile validation, and diff checks. Freeze a new checkpoint and run a new provider-free Job 4 under a new identity. Do not reinterpret the current completed audit as qualification of the corrected live harness.

## Next three progressions

### Progression 1 — `continuous-live-harness-and-transport-accounting-v3`

Bring the exact short-canary harness onto the corrected runtime path: fix summary paths, stable-prefix runner/thread identity, acceptance-journal state updates, and all remaining pre-provider diagnostics. Add a durable transport-invocation hook and ambiguous in-flight recovery. Exercise actual Codex and DeepSeek transport failure classifications with fake runners and no provider calls.

### Progression 2 — `continuous-accepted-context-and-protected-user-authority-v3`

Add receipt-bound accepted-final/session-context evidence, cache-first same-scene continuation, exact protected-user source claims, whole-beat protected-user validation, and character-private handling for all derived summary reads. Demonstrate that a recent accepted sequence can support the next scene beat without repeating a complete card while invented Ted behavior and private-knowledge transfer still fail.

### Progression 3 — `continuous-acceptance-snapshot-and-summary-provenance-v3`

Extend the acceptance transaction through provider-thread identity, injection evidence, and atomic session-snapshot persistence. Remove or fully bind Validator-derived summaries to accepted typed package output. Simulate every success and crash cut point, reconcile schema/inventory documentation, and rerun the complete provider-free gate.

## Recommended next Job 4

Run a new provider-free integration audit under a new checkpoint and cycle identity. It should execute the exact corrected live-harness classes with fake stored runners and include at least:

- direct `StablePrefixTransport` Planner and Validator construction with exact stored-thread hashes;
- first-turn character-summary binding with no doubled path;
- exact world-journal updates after accepted-final injection;
- actual `CodexSDKTransport` MCP-observation and malformed/non-object JSON failures counted as one;
- local schema/MCP preflight counted as zero;
- process-loss or ambiguous in-flight ledger recovery;
- successful model injection followed by crashes before and after atomic session-snapshot persistence;
- same-scene Turn 2 planning from Turn 1's accepted envelope without a repeated character summary or file read;
- protected-user invention in actor and non-actor free-text fields;
- rejected-package and manually fabricated derived character summaries;
- derived character-summary MCP privacy and owner binding;
- Good Accept, Concern False Positive, and Critical False Positive through the complete acceptance transaction;
- direct runner execution in one attempt, unchanged source/disposable SQLite hashes, and unchanged D-180 profile.

Provider calls should remain exactly zero. The live ten-call canary should be considered only after this provider-free cycle is accepted and after a separate creator-bound live authorization is published under a new identity.

## Explicitly not authorized

This response does not authorize a rerun or modification of either prior live Job 4; any Sol, Terra, DeepSeek, Composer, Validator, Scene Summary, verifier, or other provider call; the live ten-call canary; the 20-turn or 63-call SillyTavern run; retry, fallback, hidden repair, provider substitution, or Fast mode; compact-v7 repair or activation; D-186, scoped-v6, or any other production/default activation; live-story acceptance; mutation of the live Hanezawa database; installed SillyTavern alteration; service restart; deployment; Job 5; merge; remote operation; push; or any expansion of creator authority. The next work remains provider-free and advisory until Ted separately authorizes a later live Stage 4.