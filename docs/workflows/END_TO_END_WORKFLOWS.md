# End-to-End Workflow Simulations

**Status:** controlling behavioral examples  
**Rule:** examples describe contracts, not fixed story outcomes

## 1. Ordinary turn

1. Ted sends a household question to one character.
2. Python hashes the source, resolves branch head, and confirms no replay.
3. Seed retrieval supplies the addressee, nearby eligible cast, current scene, relevant relationships, and open threads.
4. Codex notices that one past event may affect trust and calls `search_evidence`, then expands its event reference.
5. Codex selects the addressee as floor owner, chooses a character-specific answer/tactic, and leaves Ted's next choice open.
6. Python validates evidence, cast, knowledge, and Ted boundary.
7. DeepSeek writes one complete reply.
8. Python validates and atomically commits accepted prose and directly established state.
9. SillyTavern rendering adds display styling without changing prose.

## 2. Indirect-memory turn

Sakura mentions a cue related to a past traumatic event without naming it.

1. The seed dossier contains a compact private memory index hit, not the full event.
2. Codex searches `character:sakura + cue tags + trauma + current branch`.
3. Python returns only Sakura-owner-safe memory entries and their authoritative event handles.
4. Codex expands the relevant event chain and development history.
5. Codex distinguishes the objective event, Sakura's recollection, later coping evidence, and what other present characters know.
6. The selected response reflects the memory without exposing it to another character who lacks a knowledge route.
7. Any new development cites the current accepted reply plus the prior memory; the base card is unchanged.

## 3. Multi-character conversation

1. Python supplies all present eligible characters and per-character knowledge partitions.
2. Codex selects one lead responder and adds a second only if correction, protection, direct stake, or an established tactic justifies intervention.
3. Each move cites evidence available to that owner.
4. Python rejects decorative speaker rotation or private-knowledge leakage.
5. DeepSeek realizes unequal participation and stops when Ted must answer.

## 4. Consent-valid adult turn

1. Python confirms every participant is an adult and validates current informed, freely given consent, capacity, scope, pressure, and freedom to stop.
2. Codex creates the non-graphic character decision and SequencePlan: motives, choices, causal thresholds, consequences, and stop boundary.
3. Python validates it.
4. If protected mechanical enrichment is useful, a qualified `AdultMechanicsPort` receives the validated plan, synchronized safe ledger, active-character references, and only current selected craft IDs; it never receives exact protected prose or re-decides psychology.
5. Python rejects any expansion outside consent/content/cast/source scope.
6. DeepSeek Scene Composer writes complete prose with buildup, character layers, causal sounds/material changes, highlight, and immediate aftermath.
7. Python validates and commits. Physiological response remains distinct from consent and meaning.

## 5. Boundary-crossing prompt

The current source begins with an allowed interaction but then establishes a non-consensual crossing.

1. Python preserves the earlier neutral facts and identifies the first blocked source boundary.
2. It does not rewrite intent as accidental and does not treat prior agreement to a different scope as consent.
3. It creates a checkpoint and operational rejection.
4. No Reasoner/Adult Mechanics/Composer stage generates through the blocked continuation.
5. Story state remains at the prior accepted artifact.

## 6. Temporary projection

1. With only pre-boundary facts, Codex may hypothesize immediate character effects.
2. Output is split into established facts, character knowledge, derived interpretations, unknowns, and prohibited assumptions.
3. The projection may be shown as explicitly hypothetical operational guidance if the UI supports it.
4. It is excluded from canonical retrieval and cannot update memory, trust, trauma, or material state.

## 7. Invalid external receipt

An external receipt arrives with the wrong branch or a changed starting artifact hash.

1. Python returns `CERA_RECEIPT_WRONG_BRANCH` or `CERA_RECEIPT_INTEGRITY_FAILED`.
2. It does not call Codex or DeepSeek.
3. It does not mutate story or scratch state.
4. A malformed, stale, missing, or duplicate-conflicting receipt behaves the same way.

## 8. Valid receipt and automatic aftermath

1. Python validates the receipt's world/Genesis/protected-user/external-request/registry/request/source/branch/generation/artifact/checkpoint bindings, callback, hash, controlled fields, sequence, consent, injury, material, reproductive, safety, and knowledge coverage.
2. Codex reconciles every temporary hypothesis as confirmed, contradicted, unknown, or discarded.
3. Codex plans only the non-graphic aftermath: safety, perceived meaning, immediate action, relationship consequence, memory candidates, and unresolved medical/material/reproductive concerns.
4. The regular Composer realizes the aftermath; Adult Mechanics enrichment is not called.
5. Python validates and atomically commits event, aftermath prose, objective state, knowledge, private memory, relationship, material/reproductive state, branch-local development, receipt/generation records, branch head, and scratch-projection deletion.
6. The projection is deleted.

## 9. Trauma retrieval after resumption

Later, Hana encounters a relevant cue.

1. Retrieval finds her private indexed memory and expands the cited objective event.
2. Codex also retrieves subsequent development evidence.
3. Hana's current response may differ from the immediate aftermath if later evidence supports change.
4. Other characters do not know the memory merely because the reader sees her interiority.
5. Reproductive facts, fears, and unknown conception status remain separate.

## 10. Regeneration

1. From parent artifact A, generation G1 produced B.
2. Regeneration G2 reuses the same parent/source authority but receives a new generation ID.
3. G2 produces candidate C without overwriting B.
4. If C fails, B remains active.
5. If C is selected, a transaction advances the branch head. Both artifacts and receipts remain auditable; derived state follows only the selected branch.

## 11. Restart

- Restart after acceptance replays the stored artifact with zero provider calls.
- Restart after provider response but before validation does not auto-call or publish the candidate.
- Restart during commit resolves via transaction journal.
- Restart while awaiting an external receipt restores the checkpoint and projection scratch state.
- Restart with a validated pending receipt resumes only after explicit/system-configured resumption; idempotency prevents double application.
- Restart after a persisted aftermath decision does not repeat the reasoner call.
- Restart after a staged aftermath commit does not repeat Composer work; it retries only the exact stored transaction under manual/system control.

## 12. Branch fork

1. Fork X and fork Y share ancestor artifact A.
2. X records a private memory and relationship change.
3. Y cannot retrieve either record.
4. Generated summaries, search indexes, and character overlays are built with branch filters.
5. No merge is inferred.

## 13. Deferred memory consolidation and supersession

1. After an accepted turn, Python opens a fresh evidence snapshot bound to the story head, generation, Genesis revision, and current `authority_revision`.
2. Retrieval expands the exact validated event evidence required for the proposed owner; a compact search hit alone cannot support durable memory.
3. The `DerivedConsolidatorPort` proposes Hana's owner-private memory, one directional relationship observation, a thread update, a bounded development observation, or an explicit no-change result.
4. Python rejects any missing event, private evidence owned by another character, protected-user inner state, automatic diagnosis, Genesis rewrite, permanent/global trait, or strong development based on one event.
5. Python prepares the exact validated bundle and atomically commits its records. Accepted prose, branch head, generation, source ledger, and Genesis revision do not change; only `authority_revision` advances.
6. If another consolidation committed after the snapshot, the prepared bundle fails stale and inserts nothing. Restart may submit only a newly evidenced request; it cannot reinterpret the old bundle as current.
7. Public, Hana-private, and system-private views rebuild from canonical current records. Retrieval still cites the underlying record rather than treating a summary as authority.
8. A later evidence-backed memory version may supersede the first only after the exact predecessor is expanded. The old version remains available to audit but is excluded from ordinary current retrieval.
9. If the branch had forked before the first consolidation, the earlier child and its siblings do not inherit that later parent memory even though all still share the same story artifact.

## 14. Route evaluation and promotion

1. Python selects a sealed suite partition for exactly one provider-neutral runtime role and one immutable route identity.
2. The route executes outside the evaluator. Python records output hashes, counters, criterion observations from the approved evaluator, and one privacy-safe telemetry event per case.
3. Python rejects duplicate/missing cases, criterion drift, route/hash mismatch, fake/live evidence confusion, telemetry mismatch, story-authority writes, or known craft-asset overlap.
4. Deterministic findings preserve hard defects; quality successes cannot average away an authority, privacy, identity, consent/capacity, cast, branch, or protected-user failure.
5. For actual provider candidates, Python builds blinded A/B packets. The reviewer sees shared safe context and candidates A/B, never route identity. The route mapping remains in a separate key.
6. Promotion policy checks role-specific case and holdout counts, risk coverage, machine pass rate, human reviewed cases/ballots, matched preference, hard defects, and live-provider receipts.
7. An offline fake that passes everything remains `offline_evidence_only`. A live route that qualifies becomes only `eligible_for_creator_review`; it is not promoted automatically.
8. Deployment readiness separately checks all qualified roles, telemetry privacy review, production-world authorization, SillyTavern authorization, credential approval, fresh-chat acceptance, and deployment authorization. The result can invite a creator decision but never deploy itself.

## 15. Branch-bound Reasoner session

1. Python creates or reconstructs a session at the current accepted branch head.
2. The provider thread receives stable instructions once and a complete authoritative reconstruction; this context remains advisory.
3. A new request creates `ContextAuthorityDelta` and forks a candidate from the accepted checkpoint.
4. Provisional Composer output and Sol review remain outside story authority while the candidate is open.
5. Accept first completes the existing atomic story commit. Python then injects `AcceptedTurnReceipt` without a model call and promotes the child checkpoint.
6. Decline or correction marks the child rejected. A bare decline creates no reusable rule. Explicit feedback creates a branch-local constraint unless the creator explicitly selects global scope.
7. The next candidate forks from the accepted parent, not the rejected child, and receives active constraint bindings.
8. Regeneration forks from the checkpoint for the replaced artifact's parent. Acceptance creates a sibling accepted lineage; failure leaves the replaced artifact active.
9. A CERA branch fork creates a distinct session/provider-thread identity at the exact fork artifact. Branch-local constraints and rejected children do not leak.
10. Provider loss, incompatible versions, or context rotation reconstructs from Python. No provider call is automatically repeated.
