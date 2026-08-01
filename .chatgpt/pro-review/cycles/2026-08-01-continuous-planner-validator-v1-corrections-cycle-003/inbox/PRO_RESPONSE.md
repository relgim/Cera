# CERA ChatGPT Pro Repository Review

review_cycle_id: 2026-08-01-continuous-planner-validator-v1-corrections-cycle-003
reviewed_checkpoint_id: 2026-08-01-continuous-planner-validator-v1-corrections-003
reviewed_checkpoint_git_sha: cb5307ce0ffd109fb8169a1818511b5ea120ae18
reviewed_evidence_sha256: dff2edc169fc54e3faabbe9231597b0c0765b2c69d8cb2e4a1e5d1749fdd66da
reviewed_task_set_sha256: e2c918effe95f281ca660f3c8a2fe6dceae4aa2a346ecdb22a9b5d9729fe6a05
reviewed_job4_task_id: continuous-corrections-v3-provider-free-integration-audit
response_nonce: e2b9060f9183f46448a197e7e4f0f9ed6d8354bb7aeb9818bad31397281017bc
review_scope: repository_cycle
review_disposition: corrections_required

## Independent findings

1. The cycle identity, checkpoint, predecessor, manifest, task set, publication, trigger, and completed Job 4 receipts are coherent. Progressions 1-3 and Job 4 made zero provider calls. Job 4 completed on its first direct execution with 21/21 labeled assertions across 16 unique unittest methods. The source and disposable SQLite hashes remained unchanged, and active profile `cera.active_runtime.d180.v1` remained unchanged. No live story, production branch, service, installed SillyTavern, deployment, merge, remote, or push effect was found. Earlier failed and correction-cycle evidence remains immutable.

2. The three prior review findings that concerned concrete harness drift are substantially corrected. The short-canary summary path no longer becomes `ACTIVE/ACTIVE/...`; stored-thread identity can be recovered through the stable-prefix wrapper; the harness records Planner-ledger append, injection return, Planner snapshot persistence, and final synchronization; candidate-derived character summaries are removed from current Planner authority; historical derived summary reads remain owner-private; and the generic runtime can expose one recent accepted turn without resending the same character summary. These are valid improvements.

3. Codex call accounting is still not located at the actual external-provider submission boundary. `CodexSDKTransport.invoke()` calls the ledger marker immediately before `runner.run()`. For a stored Codex call, `runner.run()` still performs local subprocess launch, request decoding, SDK import, compatibility activation, account inspection, thread start or resume, and other worker stages before the worker reaches the external `thread_run` operation. Any failure in those local stages is therefore recorded as `transport_invoked` even when the worker's own progress evidence says no provider request occurred. The provider-free test uses a fake runner whose `run()` method is treated as the provider boundary, so it does not expose this distinction. DeepSeek's marker is near its HTTP submission boundary; Codex's is not.

4. The protected-user source-claim projector is not a reliable semantic boundary. It classifies a clause as Ted-owned when it contains `Ted`, `I`, `me`, or a related token and treats most unattributed quoted text as Ted dialogue. It can therefore misattribute NPC first-person dialogue, dialogue with a trailing speaker attribution, signs or quoted material, and mixed narration. Conversely, ordinary unquoted user dialogue or commands without one of those tokens may receive no claim. In the exact first canary prompt, `Hello, my name is Ted.` is projected but `Is this the Hanezawa residence?` is not independently represented as dialogue.

5. The protected-user validator is both bypassable and overbroad. It detects Ted semantics by matching free-text words such as `Ted`, `visitor`, `he`, `you`, or `your`, then accepts a protected field when any cited exact claim appears somewhere in that field. A field can therefore preserve a valid supplied quote while appending an unsupplied action, such as entering the house, and still pass. Equivalent invention can evade the detector by using an unlisted label such as `guest`, `tenant`, `speaker`, or `newcomer`. At the same time, legitimate NPC logic addressed toward Ted can be rejected merely because it contains `you` or `his`. Claim kind is not matched to the claimed dialogue or action, and a Ted actor beat can carry exact-source mode without a semantic claim when its free text avoids the detector.

6. Protected-user enforcement ends at the provisional Planner sequence. Python does not revalidate the DeepSeek realization, the Validator complete final sequence, the accepted event summary, or world edits against the source claims. `validate_traceability()` proves only that final items point to Planner beat keys. A DeepSeek addition or Validator finalization can therefore introduce unsupplied Ted dialogue, movement, decision, thought, or consent state and still reach a Good disposable-acceptance package. The separate Validator also receives the current source and binding manifest but not the Python claim manifest, so it cannot independently verify the exact claim-key and span contract that Python applied to the Planner.

7. Accepted-session evidence is not actually owner-scoped. `_bind_latest_accepted_session_evidence()` creates one public binding and one private binding for every NPC listed in the accepted event, but every binding hashes the same complete accepted envelope. It does not project the observable subset separately or filter private content by `FinalSequenceItemV1.private_state_owner_ids`. In a multi-NPC accepted turn, Mia can receive a Mia-private authority handle over an envelope containing Sakura-private material, while the public alias points to those same bytes. The current tests use a single-NPC sequence and therefore do not expose this cross-owner laundering.

8. Accepted-session evidence is also insufficiently scoped and independently verifiable. The binding carries no scene identity or final-sequence item keys, and the runtime unconditionally exposes the latest accepted turn even when the next request begins a new scene. Participant IDs are read from the accepted event without rechecking the current event bytes against the journal's accepted-event hash. The separate Validator session is not given the accepted envelope or an owner-filtered projection; it sees only hashes and the Planner sequence. It cannot independently determine whether a claimed current-scene fact or private state was actually present in the accepted turn unless it performs an additional event lookup, which is neither bound nor required by the current request contract.

9. The protected-user claim set is omitted from `RequestEvidenceBindingRegistry.registry_sha256`. The candidate records the registry hash, but that hash covers only evidence bindings, not the exact source claims that authorize the Planner's protected-user fields. Two requests with identical source binding and world bindings but different projected claims can therefore share the same registry hash. The durable candidate and later Validator package do not carry a hash-bound claim ledger.

10. Acceptance snapshot custody is improved but still trusts an insufficient check. `mark_acceptance_session_snapshot_persisted()` reads the snapshot JSON and compares the stored `snapshot_sha256` field to the caller-supplied hash; it does not decode the snapshot through `ContinuousSessionSnapshotStore.load()` or recompute the hash from the contained snapshot. It verifies that the turn appears in `accepted_turn_ids` but not that the exact accepted-envelope hash has both an `accepted_final_sequence` event and an `accepted_final_sequence_synchronized` event. A self-consistent but wrong raw envelope can therefore satisfy the world-journal method. The stable snapshot path is also overwritten on later turns, so earlier acceptance journals retain a hash without retaining the exact snapshot bytes they claim to bind.

11. Contract and provider identities are not fully version-reconciled. `cera.character_summary_envelope.v2` changed from permitting ACTIVE or Validator-derived sources to permitting ACTIVE only without a schema-version increment. The Validator now receives rich Planner sequence v2, evidence binding v3, accepted-session bindings, and protected-user claim keys, but `CONTINUOUS_VALIDATOR_PROMPT_VERSION` remains v3, the Validator adapter remains v2, and its stable instructions do not define accepted-session authority. The DeepSeek input contract also changed materially while its continuous prompt and adapter identities remain v1. Existing stored Validator compatibility can therefore be reused across a changed input-authority contract.

12. The exact live harness still duplicates orchestration instead of exercising the corrected generic coordinator end to end. Its Turn 2 path continues to supply a Sakura summary rather than binding the accepted Turn 1 envelope through the new accepted-session mechanism. Job 4 separately tests the harness only up to the first provider boundary and tests accepted-session behavior through scripted generic-runtime stages. It does not run the complete ten-call harness with fake stored runners, a fake DeepSeek transport, a separate fake Validator, scene summary, three accepted turns, and the complete synchronization journal. The passing audit supports individual contracts but does not yet establish that the frozen live harness composes them correctly.

13. The Job 4 report correctly distinguishes 21 labeled assertions from 16 unique tests and accurately reports zero provider calls and zero effects. Its passing scope does not include a pre-`thread_run` Codex worker failure, mixed-speaker protected-user parsing, a valid claim plus an invented action in one field, final-sequence or prose invention, multi-NPC private accepted context, cross-scene accepted-session use, event-hash tampering, snapshot-envelope tampering, changed Validator prompt compatibility, or the complete fake ten-call harness. These remain material provider-free gaps rather than merely live quality uncertainty.

## Required corrections

1. Move the Codex invocation boundary into the stored runner or worker at the exact point immediately before the external `thread.run` submission. Preserve separate durable states for worker launch, worker preflight, external submission, provider return, and post-return validation. Worker failures before `thread_run` must remain zero-call failures; an in-flight or post-submission ambiguity must consume the slot. Test SDK import, account, compatibility, thread-resume, pre-`thread_run`, `thread_run`, timeout, malformed result, and MCP-finalization paths through the actual stored-runner protocol.

2. Replace lexical protected-user inference with a deterministic ingress projection. The current user source should produce typed supplied events and utterances independent of whether a sentence contains `Ted` or a first-person pronoun. Speaker ownership must be explicit for quoted and unquoted dialogue. Planner beats should reference structured claim keys rather than prove safety through substring inclusion in free text. Claim kind and exact supplied scope must prohibit additional action, dialogue, thought, decision, movement, consent, emotion, or state.

3. Extend protected-user checks through the complete candidate. Python must validate the DeepSeek realization and Validator final sequence against the same supplied-event ledger before disposable acceptance. Final-sequence items and accepted event records must carry the exact protected-user claim keys they realize. World edits derived from those items must remain traceable to the same claims. The Validator request must receive the exact claim manifest and authority notes needed for an independent check.

4. Replace whole-envelope accepted-session aliases with canonical projections. Create one public observable projection and separate owner-filtered private projections derived from exact final-sequence item keys and `private_state_owner_ids`. Each projection must bind scene ID, turn ID, accepted envelope, promotion receipt, accepted pair and event hashes, Planner thread, synchronized snapshot, and synchronization receipt. A participant ID alone must never grant access to every private field in the envelope. Revalidate the accepted event hash before using its participants.

5. Make accepted-session authority explicitly scene-bounded. Same-scene projections may support exact current-scene continuity; a Scene Change must either expire them or expose only an explicit transition projection. Older events, durable traits, rules, and private information absent from the projection must still require ACTIVE or exact event evidence. Add multi-NPC, cross-scene, sibling-branch, modified-event, and wrong-owner negative tests.

6. Give the separate Validator the exact accepted-session and protected-user projections used by the Planner, or require Python-bound exact ACTIVE event reads before validation. The Validator must not be asked to validate a hash whose source content exists only in the Planner's stored thread. Bump the Validator prompt, adapter, and compatibility identities, and update its stable instructions to define accepted-session scope and claim enforcement. Bump the DeepSeek prompt or adapter identity for the changed rich-sequence input contract.

7. Include the complete protected-user claim ledger and accepted-session projection ledger in the request registry hash, candidate hash, debug replay record, and Validator request. Provider-returned debug data must remain unable to create or modify these Python-owned claims.

8. Harden acceptance snapshot verification. Decode and recompute the snapshot through the typed snapshot store; require the exact Planner role, world, branch, thread, accepted-envelope event, synchronized event, and envelope hash. Persist an immutable per-turn snapshot receipt or immutable snapshot bytes before advancing the journal, while maintaining a separate current-session pointer. Bind the typed injection receipt rather than accepting only an arbitrary 64-character hash.

9. Increment `CharacterSummaryEnvelope` to a new schema identity for the ACTIVE-only contract and reconcile every prompt, adapter, compatibility, registry, migration, replay, and documentation identity affected by rich sequence v2, evidence v3, accepted-session authority, and source claims. A stored thread created under the prior Validator prompt must not be reused under the changed authority contract.

10. Refactor the short-canary harness to call the same orchestration components as the generic runtime wherever possible. The provider-free qualification must execute the complete ten-stage schedule with scripted fake stored Planner and Validator runners and a fake DeepSeek response, including Turn 2 without a repeated Sakura summary, Scene Change, accepted-session validation, all three disposable acceptances, snapshot synchronization, thread continuity, and final cleanup.

## Next three progressions

### Progression 1 — `continuous-protected-source-and-final-output-authority-v4`

Introduce an ingress-owned typed protected-user event and utterance ledger, replace lexical whole-beat detection, hash the exact claim set into request evidence, pass the ledger to Planner and Validator, and enforce it over Planner beats, DeepSeek prose, Validator final sequences, accepted events, and edits. Cover the exact doorway prompt, plain unquoted questions, imperatives, mixed NPC/Ted quotation, trailing speaker attribution, valid supplied dialogue plus invented movement, actor and non-actor invention, and protected-user consent or thought insertion.

### Progression 2 — `continuous-owner-scoped-accepted-context-and-validator-parity-v4`

Create canonical public and per-owner accepted-session projections with exact item, scene, pair, event, receipt, thread, snapshot, and synchronization bindings. Remove full-envelope public/private aliases, verify event bytes, enforce same-scene and explicit-transition scope, and include the exact projections in the separate Validator request. Add multi-NPC private-state, wrong-owner, modified-event, sibling-branch, Scene Change, missing-item, and no-repeated-card tests.

### Progression 3 — `continuous-true-submission-snapshot-and-identity-v4`

Move Codex accounting to the worker's actual external submission boundary, preserve ambiguous in-flight recovery, validate typed immutable Planner snapshots and injection receipts, bump all changed schemas/prompts/adapters/compatibilities, and replace duplicated canary orchestration with shared runtime components. Run the complete provider-free suite, documentation and source-inventory validation, compilation, active-profile validation, diff checks, and a complete scripted ten-stage canary.

## Recommended next Job 4

Run a new provider-free integration audit under a new checkpoint and cycle identity. It should execute the complete exact ten-stage short-canary schedule with separate fake stored Planner and Validator threads and a fake DeepSeek transport, not merely isolated unit methods. It should include at least:

- Codex worker failure before `thread_run` counted as zero and failure at or after `thread_run` counted as one;
- stranded in-flight call recovery consuming the bounded slot without double-counting a completed receipt;
- the exact first doorway message, including its second unquoted question, projected as supplied user dialogue;
- mixed NPC and Ted dialogue with explicit speaker ownership;
- a valid Ted quote plus an invented Ted movement rejected in Planner, prose, and final sequence;
- Turn 2 planned from a receipt-bound owner-filtered Turn 1 projection with no repeated Sakura summary or ACTIVE character read;
- a multi-NPC accepted turn proving public and private projections do not cross owners;
- Scene Change preventing prior-scene private projection use while preserving the explicit transition summary and five-pair tail;
- event-byte, pair, thread, snapshot, synchronization, and sibling-branch tamper rejection;
- typed snapshot recomputation and immutable per-turn snapshot evidence;
- updated Planner, Validator, DeepSeek, schema, adapter, and compatibility identities;
- Good Accept, Concern False Positive, and Critical False Positive through the complete shared acceptance transaction;
- one direct execution attempt, zero provider calls, unchanged source and disposable SQLite hashes, and unchanged D-180 profile.

Provider calls should remain exactly zero. A live ten-call canary should be considered only after this provider-free cycle is accepted and Ted separately authorizes a new identity-bound live Stage 4.

## Explicitly not authorized

This response does not authorize any Sol, Terra, DeepSeek, Composer, Validator, Scene Summary, verifier, or other provider call; a rerun or modification of any prior live Job 4; the live ten-call canary; the 20-turn or 63-call SillyTavern run; retry, fallback, hidden repair, provider substitution, or Fast mode; compact-v7 repair or activation; D-186, scoped-v6, or any other production/default activation; live-story acceptance; mutation of the live Hanezawa database; installed SillyTavern alteration; service restart; deployment; Job 5; merge; remote operation; push; or any expansion of creator authority. The next work remains provider-free and advisory until Ted separately authorizes a later live Stage 4.