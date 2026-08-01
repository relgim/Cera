# CERA ChatGPT Pro Repository Review

review_cycle_id: 2026-08-01-continuous-planner-validator-v1-corrections-cycle-004
reviewed_checkpoint_id: 2026-08-01-continuous-planner-validator-v1-corrections-004
reviewed_checkpoint_git_sha: 21d4b228a47dc738a051266cc1f92233b40f554e
reviewed_evidence_sha256: c7891a5478ed87dfb7b3e8bcd3cb706ee4902c9f732a75fbd72db158b8c7a5a1
reviewed_task_set_sha256: c21e767f76f0c2326a86331bc195980336515e41b91d36d82e32ee61d50118b6
reviewed_job4_task_id: continuous-corrections-v4-provider-free-integration-audit
response_nonce: a3f9ef7289d3faacc84534c173dbdd21aa5d1ca7f0e8831568ba49f3fea4e4fe
review_scope: repository_cycle
review_disposition: corrections_required

## Independent findings

1. The checkpoint, cycle, predecessor, manifest, task-set, publication, trigger, and completed Job 4 identities are coherent. Progressions 1-3 and Job 4 made zero provider calls. Job 4 completed on its first direct execution with 13/13 labeled assertions across nine unique unittest methods. The source and disposable SQLite hashes remained unchanged, and active profile `cera.active_runtime.d180.v1` remained unchanged. No live story, production branch, service, installed SillyTavern, deployment, merge, remote, or push effect was found. Earlier failed and correction-cycle evidence remains preserved rather than rewritten.

2. Cycle 004 makes several valid structural improvements. Codex transport accounting now distinguishes worker start, worker preflight, and the observed `thread_run` boundary; the acceptance path stores an immutable per-turn Planner snapshot receipt in addition to the current-session pointer; the short-canary harness delegates turn and Scene Change orchestration to `ContinuousShadowTurnCoordinator`; Planner, Validator, and DeepSeek prompt and adapter identities were advanced; protected-user claim and realization records are typed; and same-scene accepted context is reconstructed from exact pair, event, envelope, promotion, thread, snapshot, and synchronization evidence. These are material corrections to the defects identified in cycle 003.

3. Protected-user source authority is still inferred from a bounded text heuristic rather than supplied by a deterministic ingress contract. `project_protected_user_source_claims()` recognizes only a small verb and punctuation grammar. A supplied action such as `Ted walked inside.` or `I turned around.` receives no action claim because those verbs are outside the allow-list. A trailing attribution such as `"Wait," I said.` receives no dialogue claim because `said` is outside the speech-verb grammar. Conversely, a source beginning `Please ...`, `Hello ...`, or a question word can be classified as direct Ted dialogue even when that text is narration, an instruction to the system, or quoted scenario material. The exact doorway fixture is covered, but ordinary future user forms remain capable of false rejection or false authorization.

4. Composer validation proves only exact copying of registered source claims; it does not reject newly invented protected-user content expressed with different text. `validate_composer_realization()` verifies declared spans and searches for undeclared exact occurrences of claim text. A Composer result containing `Ted steps inside.` with no declared realization and no exact copied source claim can pass this function. The same applies to invented thought, emotion, consent, movement, or a paraphrased utterance. The new realization-span contract therefore closes untraceable copying but does not close protected-user semantic invention.

5. Protected-user enforcement remains incomplete after the Composer. `validate_traceability()` requires each final-sequence item to carry the union of protected claim keys from its Planner beats, and the event carries the union from final items. It does not validate the semantic text of `realized_event`, `valid_deepseek_additions`, `knowledge_changes`, `material_changes`, `resulting_state`, the event summary, world-edit values, or world-edit reasons against those claims. A Validator can retain the expected claim-key set while adding an unsupplied Ted action or private state. A Good package can therefore reach disposable acceptance even though the Planner claim contract was not preserved in the final candidate.

6. The exact authority set is not part of the candidate identity. `ContinuousTurnCandidateV1` stores `evidence_registry_sha256`, and replay debug records now include the registry, claim manifest, and projections, but `candidate_sha256` hashes only the turn ID, Planner sequence, story text, and Validator package. The acceptance journal and promotion receipt likewise do not bind the request evidence registry, protected-user realization ledger, or accepted-session projection ledger. Two candidates with the same visible sequence, prose, and package but different Python-owned authority sets can therefore share one candidate identity.

7. Accepted-session owner authority can still be created for an NPC who was not established by the accepted final sequence. `_bind_latest_accepted_session_evidence()` derives owners from `EventRecordCandidateV1.participant_ids` plus private-state owners. `FinalSequenceItemV1` has no actor or subject identities, and the package contract does not require event participants to match Planner beat actors or final-item subjects. For every listed participant, the runtime creates an owner-private projection containing all public items even when no accepted item belongs to that participant. On the next turn, that owner-bound binding can satisfy the hard-character-authority check for the arbitrarily listed NPC without an ACTIVE read. This converts an unchecked Validator participant label into future character authority.

8. Accepted-session privacy is enforced only at whole-item granularity. A public projection excludes an entire final item whenever `private_state_owner_ids` is non-empty, even if that item also contains observable action or material state. An owner projection includes the complete item, including knowledge and material fields that have no independent visibility or owner tags. This can either discard useful public continuity or expose more of a mixed item than the owner-specific private portion. The current contract needs explicit item subjects and field-level visibility, or it must require the Validator to split observable and owner-private content into separate final items before projection.

9. Same-scene expiry and byte verification are materially stronger. The runtime returns no accepted-session projection when the accepted event scene differs from the current request scene, and it rechecks accepted pair and event bytes against the acceptance journal before projection. Immutable snapshots are also retained after the current pointer advances. These controls should be preserved while correcting subject and field scoping.

10. Contract identity reconciliation is still incomplete. `build_validator_prompt()` continues to emit `schema_version: cera.continuous_validator_request.v1` even though the request now includes protected-user claims, Composer realization spans, and accepted-session projections that did not exist in that contract. The live compatibility constructor also continues to declare `protected_user_policy_version: cera.protected_user.v1` and `session_policy_version: cera.continuous_session.v1` after material protected-user and accepted-session authority changes. Prompt and adapter bumps reduce stored-thread reuse risk, but the typed request and policy identities remain misleading and cannot independently reject an old contract.

11. The frozen live short-canary configuration still resends Sakura's complete summary on Turn 2. The main harness creates `sakura_summary_2` and passes it to `run_turn()` even though the shared coordinator now supports owner-scoped accepted Turn 1 evidence. This means the actual future live route would not test the creator's central cache-first requirement: Turn 2 should continue from the accepted final sequence without repeating Sakura's card or forcing an ACTIVE character read.

12. The live harness remains bound to the already-failed historical cycle and task identities. `run_continuous_planner_validator_job4.py` still hardcodes cycle `2026-08-01-continuous-planner-validator-v1-cycle-001` and task `continuous-planner-validator-three-turn-scene-change-canary-v1`, then rejects any manifest that does not match. That old Job 4 identity is immutable failed evidence and its source result paths already exist. A later authorized live canary cannot safely use this harness without either attempting to reuse the prohibited old identity or modifying the file again. The canary must accept and validate a newly bound cycle/task authorization without broadening scope.

13. Provider-free Job 4 did not execute the exact live `JobHarness` through all ten stages. The ten-stage assertion counts queued Planner, Composer, Validator, and summary calls through the generic coordinator. The exact `JobHarness` is tested only through its first provider boundary. The audit therefore does not prove the harness's provider-call wrapper, polling boundaries, fake route telemetry, complete three-turn acceptance, Scene Change handoff, Turn 2 cache-first behavior, thread archival, final report construction, and cleanup as one composed run.

14. The true-submission accounting design is plausible, but the reviewed Job 4 tests the ledger callbacks directly rather than exercising the actual stored-worker progress observer across pre-`thread_run`, `thread_run`, timeout, and malformed-result process states. The worker source records `thread_run` immediately before the external call, and ambiguous worker states conservatively consume the slot, which is the correct direction. Before a live canary, one provider-free subprocess worker fixture should prove that the progress sidecar and parent observer produce the intended durable ledger sequence rather than only testing manually invoked callbacks.

## Required corrections

1. Replace heuristic protected-user ownership inference with an ingress-owned supplied-event and supplied-utterance contract. The raw SillyTavern/CERA request should carry or mechanically derive explicit speaker/actor ownership before story planning, with exact spans and typed action, dialogue, state, or instruction classifications. Unsupported forms must remain unclaimed without treating generic greeting, imperative, or question syntax as proof of Ted ownership. Add broad positive and negative fixtures rather than extending a narrow verb list one case at a time.

2. Enforce protected-user authority across the complete candidate. Composer output must expose typed actor/speaker segments, not merely exact copied spans. Python must reject any protected-user segment without an exact supplied claim, including paraphrases and newly worded action, movement, thought, emotion, consent, or decision. Validator final items, event records, and world edits must retain typed protected-user realizations and be checked against the same claim ledger before creator review or disposable acceptance.

3. Bind the complete authority ledger into candidate and acceptance identity. `candidate_sha256` must include the evidence-registry hash, protected-user claim ledger, Composer realization ledger, accepted-session projection ledger, and relevant prompt/schema identities. The candidate package, promotion receipt, debug replay, and acceptance journal must all agree on that authority identity before ACTIVE promotion.

4. Add exact actor and subject identities to final-sequence items, or provide an equivalent typed mapping from final items back to Planner beat actors. Require `EventRecordCandidateV1.participant_ids` to equal the justified participant union. An accepted-session owner projection may be created only when accepted items explicitly belong to that owner; an arbitrary event participant must not receive a private hard-authority binding.

5. Split accepted final information into canonical observable and owner-private projections. Observable action, material state, knowledge state, and private state need explicit visibility and owner metadata. A mixed final item must be split or projected field-by-field so public continuity is not discarded and owner projections do not inherit unrelated content.

6. Advance every materially changed contract identity, including the Validator request schema and the protected-user/session policy versions used by continuous-session compatibility. Add incompatibility tests proving that Planner or Validator threads created under the prior request and policy identities cannot resume under the corrected contracts.

7. Remove the repeated Sakura summary from the exact Turn 2 canary path. The frozen provider-free harness must prove that Turn 2 receives no Sakura summary and performs no Sakura ACTIVE read while using the exact owner-scoped Turn 1 accepted projection. Later card or rule retrieval should remain available only when the next decision actually requires durable information absent from that projection.

8. Parameterize the short-canary harness with an exact newly published cycle ID, task ID, authorization hash, checkpoint SHA, call ceiling, and result paths. It must explicitly reject the historical failed cycle identity. No future live run may modify, overwrite, or reuse the old Job 4 evidence.

9. Add a complete provider-free execution of the exact `JobHarness` using scripted stored Planner and Validator runners plus a fake DeepSeek transport. It must traverse all ten stages, all three disposable acceptances, the Scene Change summary, exact context injection and immutable snapshots, Pro polling boundaries, route telemetry, final archival, reporting, and cleanup in one attempt.

10. Exercise the actual subprocess progress-observer protocol provider-free. Use a fake worker module that stops at SDK import, account check, thread resume, immediately before `thread_run`, at `thread_run`, after return, and during malformed output. Confirm zero-call versus one-call accounting, conservative ambiguous recovery, and no double-counting of completed receipts.

## Next three progressions

### Progression 1 — `continuous-ingress-claims-and-final-candidate-enforcement-v5`

Introduce an ingress-owned supplied-event and utterance ledger, replace heuristic speaker/actor authorization, add typed Composer actor/speaker realizations, and enforce protected-user authority over Planner beats, DeepSeek output, Validator final items, events, and world edits. Include the complete claim and realization ledgers in the evidence registry and candidate identity.

### Progression 2 — `continuous-subject-scoped-accepted-context-and-authority-binding-v5`

Add exact final-item actors/subjects and field-level visibility, validate event participants, build canonical public and one-owner projections, prevent arbitrary participant authority, and bind projection hashes through candidate, Validator, promotion, and acceptance records. Advance the Validator request and continuous policy identities and prove stored-thread incompatibility with prior contracts.

### Progression 3 — `continuous-new-identity-canary-harness-qualification-v5`

Parameterize the short-canary harness for a newly published identity, remove the repeated Turn 2 Sakura summary, exercise the real worker-stage observer with provider-free subprocess fixtures, and run the complete exact ten-stage `JobHarness` with scripted Planner, DeepSeek, Validator, Scene Change, acceptance, snapshot, archival, reporting, and cleanup behavior. Reconcile all documentation and run the complete provider-free gate.

## Recommended next Job 4

Run another provider-free integration audit under a new checkpoint and cycle identity. It should execute one complete exact ten-stage short-canary through the parameterized `JobHarness`, not a sum of isolated unit calls, and include at least:

- explicit ingress ownership for the exact doorway prompt, ordinary unquoted questions, supplied movement, mixed NPC/Ted quotation, trailing attributions, narration, and system-style instructions;
- rejection of an invented protected-user action that does not copy any source text;
- rejection of a valid supplied quote followed by an unsupplied movement in Composer prose, Validator final sequence, event summary, and world edit;
- candidate-hash changes when claim, realization, or accepted-projection authority changes while visible prose remains identical;
- final-item actor/subject and event-participant equality;
- rejection of an arbitrary participant receiving an owner projection;
- correct public and owner-private projection of a mixed observable/private accepted event;
- Turn 2 using Turn 1 accepted context with no repeated Sakura summary and no Sakura ACTIVE read;
- Scene Change expiring prior-scene owner-private projections while preserving the explicit summary and exact tail;
- actual subprocess worker progress before and at `thread_run` producing zero and one counted calls respectively;
- immutable per-turn snapshot and injection-receipt verification;
- a newly bound cycle/task identity with explicit rejection of the historical failed identity;
- Good Accept, Concern False Positive, and Critical False Positive through the same complete shared transaction;
- one direct execution attempt, zero provider calls, unchanged source/disposable SQLite hashes, and unchanged D-180 profile.

Provider calls should remain exactly zero. A live ten-call canary should be considered only after this provider-free cycle is accepted and Ted separately authorizes a new identity-bound live Stage 4.

## Explicitly not authorized

This response does not authorize any Sol, Terra, DeepSeek, Composer, Validator, Scene Summary, verifier, or other provider call; a rerun, overwrite, or modification of any prior live Job 4; the live ten-call canary; the 20-turn or 63-call SillyTavern run; retry, fallback, hidden repair, provider substitution, or Fast mode; compact-v7 repair or activation; D-186, scoped-v6, or any other production/default activation; live-story acceptance; mutation of the live Hanezawa database; installed SillyTavern alteration; service restart; deployment; Job 5; merge; remote operation; push; or any expansion of creator authority. The next work remains provider-free and advisory until Ted separately authorizes a later live Stage 4.
