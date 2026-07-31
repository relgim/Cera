# CERA Codex Working Instructions

These instructions apply to every future Codex session working in `D:\AIChatBot\Cera`.

## Mandatory start

1. Read `docs/START_HERE.md`.
2. Read `docs/handoff/CURRENT.md`.
3. Read every document marked required for the current phase.
4. Confirm that the requested action is authorized by `docs/implementation/ROADMAP_AND_GATE.md`.
5. Inspect the worktree before editing and preserve unrelated user changes.

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
