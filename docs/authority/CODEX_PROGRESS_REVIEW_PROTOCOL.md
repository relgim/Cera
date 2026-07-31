# CERA Codex ↔ ChatGPT Pro Progress Review Protocol

**Effective:** 2026-07-31
**Status:** creator-authorized engineering workflow
**Scope:** every Codex session that changes CERA code, prompts, configuration, tests, runtime integration, evaluation, or controlling documentation

## Purpose

CERA progresses in small, auditable tranches rather than through continuous
architecture churn. Codex remains the technical implementer. ChatGPT Pro
reviews each completed checkpoint, identifies unsupported claims or unnecessary
complexity, and recommends and bounds the next two or three substantial
progressions. Ted explicitly authorizes execution. Neither Codex nor ChatGPT Pro
may silently grant creator authority.

This protocol supplements `D:\AIChatBot\Cera\AGENTS.md`. It does not weaken the
owner architecture, typed contracts, creator authority, privacy, branch,
knowledge, identity, consent/capacity, protected-user, evidence, publication,
no-silent-fallback, or no-recursive-retry boundaries.

## Mandatory startup

Before editing, Codex must read, in order:

1. `D:\AIChatBot\Cera\AGENTS.md`
2. `D:\AIChatBot\Cera\docs\START_HERE.md`
3. `D:\AIChatBot\Cera\docs\authority\CODEX_PROGRESS_REVIEW_PROTOCOL.md`
4. `D:\AIChatBot\Cera\docs\handoff\CURRENT.md`
5. `D:\AIChatBot\Cera\docs\authority\CERA_OWNER_ARCHITECTURE.md`
6. `D:\AIChatBot\Cera\docs\authority\DECISIONS_AND_SUPERSESSIONS.md`
7. The latest applicable consumed repository-cycle response reported by
   `python tools/pro_review_cycle.py latest-consumed`, when one exists
8. The latest applicable historical `PRO_RESPONSE*.md` under
   `D:\AIChatBot\Cera\.chatgpt\pro-review\checkpoints\`, when one exists
9. Every task-specific controlling contract and result named by the current prompt

Codex must then inspect repository status, current HEAD, staged changes,
untracked files, and the exact active runtime identity before modifying
anything.

## Git prerequisite

Before the first tranche:

1. Create and verify a lossless backup outside `D:\AIChatBot\Cera`.
2. Inventory every repo-local path and classify it as tracked source/authority,
   tracked immutable evidence, generated output, runtime state, local
   secret-risk material, or intentionally untracked material.
3. Initialize a local Git repository without adding a remote or pushing.
4. Update ignore rules only from the explicit inventory. Never use a blind
   `git add .` before reviewing the inventory.
5. Never delete, relocate, rewrite, or compress historical evidence merely to
   make the baseline smaller.
6. Record every intentionally ignored or untracked evidence/runtime path in a
   tracked manifest with preservation location and rationale.
7. Create a local baseline commit before active implementation changes.
8. Record the baseline commit SHA and inventory in the first checkpoint request.

Do not run `git clean`, destructive reset, stash, checkout-overwrite, force
operations, or remote operations during bootstrap. If a safe baseline cannot be
established, stop and request review. Do not continue unversioned.

The baseline commit is a prerequisite. It does not replace the checkpoint
commit required after the tranche.

## Tranche size

A tranche contains **no more than three substantial progressions**.

Two progressions are acceptable when a third would be unsafe, unrelated,
insufficiently supported, or outside authorization. One progression is
acceptable after a terminal diagnostic failure that makes further work
speculative. Codex must never quietly turn the tranche into a fourth
progression. D-182 permits one separately named, independent Job 4 as a
latency-hiding review buffer only when its scope and authorization artifact were
fixed before Jobs 1-3 were published.

A substantial progression is one coherent issue resolution, bug fix,
implementation, qualification, or evidence-backed product improvement with:

- a defined owner and failure class;
- explicit acceptance criteria;
- focused tests or evidence;
- a user-visible or architecture-relevant result;
- an honest statement of what remains unproven.

The following do not count as separate progressions by themselves:

- increasing the total test count;
- writing a result file;
- updating version strings required by the same change;
- rerunning tests without a changed hypothesis;
- formatting or link cleanup;
- obtaining another advisory acceptance token;
- splitting one architectural change into artificial subtasks.

If one task expands into another owning abstraction or materially new behavior,
stop and defer the expansion.

## Task selection and precedence

The first tranche is defined by the initial stabilization prompt. ChatGPT Pro
may recommend and bound later work after reviewing the preceding checkpoint.
A later tranche begins only inside Ted's explicit or standing bounded
authorization. A Pro recommendation cannot create that authority. A creator
instruction may pre-authorize named Jobs 1-3, the independent Job 4 buffer, and
the rules for applying in-scope review corrections across ordinary cycles.

Codex must not self-authorize additional architecture, prompt systems, model
ladders, Adult activation, production binding, deployment, or unrelated cleanup
merely because the code makes it convenient.

Priority order:

1. **P0 — truth and change control:** Git, active identity consistency,
   authority/privacy defects, incorrect current documentation, and
   transactional/restart defects.
2. **P1 — exact current-route reliability:** current-version end-to-end
   qualification, failure recovery, state/prose agreement, and deterministic
   continuity checks.
3. **P2 — creator product evidence:** varied creator-labeled turns, voice
   distinction, realism, first-candidate acceptance, factual invention, and
   scene development.
4. **P3 — latency and maintainability:** measured routing changes, verifier
   value, module decomposition, CI, and tooling.
5. **P4 — Adult live publication, production binding, promotion, and
   deployment:** only after separate creator authority and prerequisite evidence.

A lower-priority task must not displace an unresolved higher-priority defect
without an explicit creator or ChatGPT Pro decision.

## Execution rules

For each progression, Codex must:

1. State the exact problem and current evidence.
2. Identify the owning abstraction before editing.
3. Choose the smallest coherent correction.
4. Preserve unrelated user changes.
5. Add focused regression coverage.
6. Run narrow tests first and the complete relevant suite only after focused success.
7. Preserve failed live evidence under a new immutable identity.
8. Make no silent retry, fallback, route substitution, evidence overwrite, or validator weakening.
9. Report provider calls, model identities, reasoning efforts, token/cost evidence, database writes, and affected branches truthfully.
10. Avoid claiming semantic or prose quality from structural tests.

A live provider call is permitted only when the current task explicitly names
the purpose, exact maximum call count, route, data boundary, and stop condition.
A failed live stage ends that attempt. Codex may diagnose it, but it must not
automatically patch and rerun when doing so creates another progression or
exceeds authorization.

## Checkpoint and final commit

After completing the tranche's authorized progressions, Codex must freeze the
review identity. That identity includes the current Git object ID plus exact
content hashes for the evidence and each task result. A task may require the
single final local commit only after the overlapped review is consumed; in that
case the immutable task-set hashes, not an uncommitted summary alone, are the
review boundary.

Before final completion Codex must:

1. Run required focused and broad verification.
2. Inspect the complete diff and repository status.
3. Confirm historical evidence and unrelated files were not changed.
4. Create the authorized local checkpoint/final commit containing only the
   reviewed tranche.
5. Use a descriptive message such as
   `cera(checkpoint 1): establish governed stabilization baseline`.
6. Never push.
7. Leave the tracked worktree clean, except for explicitly declared runtime or
   immutable evidence outputs whose status and rationale are included in the request.

Do not combine unrelated or unauthorized work with the current checkpoint
commit. A separately authorized Job 4 result belongs to the next review
package, not retroactively to the Jobs 1-3 package reviewed while it ran.

A terminal diagnostic progression may be committed when it adds a valid
harness, regression test, safe evidence, or documented owning diagnosis.
Repeating a failed attempt without new evidence is not progress.

## Mandatory ChatGPT Pro repository review cycle

For the primary workflow create:

```text
D:\AIChatBot\Cera\.chatgpt\pro-review\cycles\<cycle-id>\CYCLE_SPEC.json
```

The generated immutable request must contain:

- creator goal and checkpoint number;
- starting baseline or prior checkpoint SHA;
- bound checkpoint Git object ID and complete task-set SHA-256;
- exact one, two, or three progressions completed;
- why each progression was selected;
- changed-file inventory and diff summary;
- exact Git status semantics for additions, modifications, deletions, copies,
  and renames, with tombstones for paths that no longer have current bytes;
- focused and complete verification commands/results;
- provider calls, retries, fallbacks, cost/quota evidence, and story/database effects;
- active runtime identity before and after;
- user-visible behavior before and after;
- actual output samples or immutable evidence paths when quality is relevant;
- unresolved defects, uncertainty, and risks;
- any disagreement with the prior Pro response;
- the preceding Job 4 result after bootstrap, separately identified from the
  current/revised Jobs 1-3 results;
- the next Job 4 task ID, exact independent scope, and pre-publication
  authorization-artifact hash;
- Codex's suggested next work, clearly labeled advisory rather than authorized.

Run `tools/pro_review_cycle.py publish`. It publishes every complete outbox file
individually atomically, records `PUBLISHED.json` as the package commit marker,
and immediately enters
`job4_in_progress` for only the named task. Then use the supported Codex-app
follow-up operation to send the existing ChatGPT Pro review chat one concise
message generated as `TRIGGER_MESSAGE.txt`. Record its exact hash and the
app-returned target/result hashes in `TRIGGER_SENT.json`. This is a local
attestation of the successful app result, not independent delivery proof.
Record the trigger attestation before Job 4 completion. It may not be inserted
retroactively into the receipt chain.
After a trigger, the recoverable in-progress state must point to that receipt.
An exact attestation retry may be idempotent, but any changed target, message,
or app-result identity must fail as a conflict.

Repository publication alone cannot wake ChatGPT. No-user-action trigger claims
require a successful supported app send and its receipt. When direct
repository/chat access is unavailable, stop and report that trigger boundary.
Only if Ted explicitly chooses the manual emergency path may Codex use
`PRO_REVIEW_FILE_BRIDGE.md` through Downloads. The fallback must not call a
provider, change story/database state, alter a route, or grant authority.

The request must not demand an exact acceptance token, frame approval as the
expected answer, hide failed evidence, or pressure ChatGPT Pro to respond early.

After sending the request, perform only the pre-authorized Job 4 recorded by the
cycle. It must be independent of the frozen Jobs 1-3 package and may not be
invented merely to avoid waiting. When it finishes, record its stable result
with `complete-job4`, then use bounded `wait-consume` on the exact repository
path. If the response is not ready, Codex may wait again or perform only another
separately named and pre-authorized independent task. Silence is not authority.
The completion chain must preserve and revalidate both the structured Job 4
result and its human-readable report. Startup discovery must reconstruct the
latest consumed v2 cycle from its immutable outbox, source archive, typed
receipt chain, Job 4 evidence, and accepted response identity; a mutable state
label alone is never a trusted startup source.

## ChatGPT Pro response

ChatGPT Pro writes the identity-bound response through the approved repository
connector, atomically, as:

```text
D:\AIChatBot\Cera\.chatgpt\pro-review\cycles\<cycle-id>\inbox\PRO_RESPONSE.md
```

The cycle tool stable-reads and preserves exact bytes only after Job 4 is
complete. It validates cycle/checkpoint IDs, Git object ID, evidence SHA-256,
task-set SHA-256, Job 4 identity, response nonce, final disposition, and
`review_scope: repository_cycle`. A matching filename alone is never
sufficient. Historical evidence-repair reviews may retain
`PRO_RESPONSE_EVIDENCE_VERIFIED.md` under the emergency V1 contract.

The response should:

- inspect the actual diff, active source, tests, and evidence when accessible;
- distinguish proven behavior from structural or self-reported claims;
- accept, reject, or narrow each progression;
- identify required corrections;
- select the next two or three substantial progressions in priority order;
- name explicit exclusions and live-call ceilings;
- state uncertainty honestly.

Codex must read the latest validated response before beginning the next Jobs
1-3. It may apply corrections only inside existing creator authority. The next
package includes the just-completed Job 4 result and the current/revised Jobs
1-3 with separate provenance. A material expansion still stops for Ted.
Codex may execute only the creator-authorized tranche. If Codex materially
disagrees, it must present repository evidence and stop for resolution rather
than silently ignoring the review.

## Prohibited anti-patterns

Codex must not:

- treat a growing provider-free test total as proof of roleplay quality;
- add global prompt rules from one isolated output;
- introduce another schema, adapter, packet, or prompt version without a necessary contract change;
- run another broad model ladder before the exact current route is coherent and qualified;
- use its own implementation summary as independent verification;
- describe ChatGPT Pro's response as a source-code audit when source or diff access was unavailable;
- leave active v24/v25-style identity conflicts in routes, receipts, sessions, tests, health output, or documentation;
- maintain several manually copied versions of current status;
- mix historical evidence into a current-status section without explicit labeling;
- continue feature work while a P0 authority, change-control, or active-truth defect remains open;
- activate Adult ON/EX, production binding, promotion, or deployment to compensate for ordinary-route quality problems.

## Definition of a clean working model

The cycle continues until the creator and ChatGPT Pro agree that, at minimum:

- source control is complete and the working tree is auditable;
- one canonical active runtime profile agrees with code, routes, sessions, receipts, health output, tests, and current documentation;
- the exact active ordinary route has current end-to-end evidence, including retrieval, restart, branch, regeneration/decline, provisional review, and atomic acceptance;
- no unresolved P0 issue remains;
- interrupted background review/session states recover or fail explicitly;
- current documentation is concise and generated or validated against active configuration;
- a varied corpus of roughly 20–30 creator-resolved turns exists across all characters and key behavior classes;
- first-candidate acceptance, correction, rewrite, replan, decline, factual-invention, continuity, voice, and latency metrics are available;
- model/effort and verifier choices are supported by matched evidence rather than intuition;
- stored-provider retention is accurately documented;
- maintainability work is evidence-driven and does not change behavior silently;
- Adult and production decisions remain separate creator gates.

A clean model does not mean zero future defects. It means the active route is
coherent, auditable, currently tested, honestly measured, and improving from
creator evidence rather than uncontrolled architectural accumulation.
