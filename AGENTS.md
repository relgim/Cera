# CERA Codex Working Instructions

These instructions apply to every future Codex session working in `D:\AIChatBot\Cera`.

## Mandatory start

1. Read `docs/START_HERE.md`.
2. Read `docs/authority/CODEX_PROGRESS_REVIEW_PROTOCOL.md`.
3. Run `python tools/pro_review_cycle.py latest-consumed`; when a consumed cycle
   exists, read its exact accepted response before editing.
4. Read `docs/handoff/CURRENT.md`.
5. Read every document marked required for the current phase.
6. Confirm that the requested action is authorized by `docs/implementation/ROADMAP_AND_GATE.md`.
7. Inspect the worktree before editing and preserve unrelated user changes.

Do not infer implementation authority from the existence of documentation, a roadmap, examples, or an earlier repository.

## Repository authority

- `D:\AIChatBot\Cera` is the sole active CERA implementation repository.
- `D:\AIChatBot\Vera_v2_d3`, Vera/V6 variants, `E:\AIChatBot\RPProxy_DeepSeek_Direct`, and `E:\AIChatBot\Sera` are references and provenance only.
- Never create a runtime dependency on a reference repository.
- Never copy reference material without an explicit task, source-hash verification, and a provenance record.

## Engineering ownership

- Python owns deterministic authority, routing, validation, storage, transactions, recovery, branch isolation, and publication.
- `SceneReasonerPort` is the provider-neutral reasoning boundary. `CodexSceneReasonerPort` is the first intended adapter, not a core-code dependency.
- Runtime Codex owns non-graphic causal reasoning, character decisions, evidence-query expansion, participation, SequencePlans, and consequences.
- DeepSeek Scene Composer owns complete visible prose.
- No model output, example, temporary projection, or external receipt becomes truth without Python validation and a successful transaction.

## Hard rules

- Preserve creator authority, identity, privacy, knowledge, branch, consent/capacity, protected-user, and evidence boundaries.
- Do not silently fall back to another provider, an older pipeline, or a lower-quality route.
- Do not recursively retry. Any later permission for one bounded repair must be explicit, typed, traced, and promotion-gated.
- Never publish raw provider output. Publish only a validated `AcceptedStoryArtifact`, then render it for SillyTavern.
- Do not treat physiological response, vocalization, freezing, silence, compliance under force, or failure to resist as consent or enjoyment.
- Do not let examples establish canon, character knowledge, preference, consent, or an event.
- Do not rewrite immutable Genesis facts through runtime character development. Use branch-local overlays.

## Blocked-event boundary

Do not implement, inspect, prompt, test, or document the internal generation behavior of a user-owned external handler. CERA may expose a neutral request/checkpoint interface and accept a validated, non-graphic completion receipt only as specified in `docs/architecture/BLOCKED_TURN_AND_RESUMPTION.md`.

## Documentation discipline

When a decision changes:

1. update the controlling contract;
2. add or update the decision/supersession record;
3. update schemas, workflows, roadmap, and handoff if affected;
4. keep generated or advisory views clearly labeled;
5. record evidence and the creator authorization.

Prefer small, typed, composable modules and contracts. If a proposed design conflicts with the owner architecture, identify the conflict rather than preserving an obsolete component for convenience.

## Blocker and review cadence

Focused tests that produce new diagnostic evidence or a plausible narrowing result are progress; they do not count as twenty minutes of being blocked. Repeating the same failure without new evidence does.

After roughly twenty focused minutes without tangible progress, or three equivalent failures:

1. stop repeating the approach;
2. record the first failing evidence and owning abstraction;
3. pivot to the simplest contract-preserving alternative;
4. if still blocked and ChatGPT Pro access is available, send one concise technical review request with evidence and the exact open question.

Do not message Pro after every edit. Request review at a genuine blocker or substantial architecture/prompt milestone.

## Governed progression checkpoints

When the creator authorizes a Codex-to-ChatGPT-Pro progression tranche, follow
`docs/authority/CODEX_PROGRESS_REVIEW_PROTOCOL.md` in addition to the ordinary
roadmap gate.

- Treat three substantial progressions as the maximum, never a quota. A
  separately named Job 4 may overlap Pro review only when its independent scope
  and authorization were frozen before publication.
- Stop early when a terminal blocker makes the remaining progression unsafe or
  irrelevant; preserve the useful diagnostic evidence in the checkpoint.
- Establish the required lossless backup and safe local Git baseline before the
  first governed tranche changes the repository.
- Freeze the actual Git object, evidence, task-result, and task-set hashes before
  review; complete the authorized local commit at the task's required boundary.
- Publish the primary request under `.chatgpt/pro-review/cycles/` with
  `tools/pro_review_cycle.py`, then activate the existing Pro chat through the
  supported app follow-up operation and record a privacy-safe, hash-bound app
  result attestation. The receipt alone is not independent delivery proof.
- Immediately perform only the pre-authorized Job 4. After it finishes, consume
  only the exact identity-bound repository response. Never invent work merely
  to remain busy.
- Repository publication alone is not a ChatGPT trigger. If the supported app
  trigger is unavailable, report that exact boundary. Use
  `tools/pro_review_bridge.ps1` only when Ted explicitly chooses the manual
  emergency fallback.
- Consuming a response never authorizes its recommendations. Apply only
  corrections already inside creator authority; stop for any material expansion.
- For a creator-authorized iterative 3+1 workflow, `corrections_required` is a
  planning boundary rather than a terminal status: preserve the completed
  cycle, derive no more than three in-scope provider-free progressions from the
  exact response, assign new checkpoint/cycle identities, and return to Stage
  4 again. Stop only for `blocked`, a new creator-policy decision, an excluded
  effect, or a concrete tool/repository blocker.
- ChatGPT Pro reviews the real checkpoint and recommends the next one to three
  progressions. Those recommendations are executable only when a creator
  command has already granted the applicable standing bounded authority.
