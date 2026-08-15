# C78 targeted policy and 10/10/5/5 run-risk review

## Scope

Reviewed the live Planner, ordinary Writer, Python review/custody, Luna, Reader,
Adult Scene/Filter, Recorder, provider-stage retry, qualification, manifest, and
restart paths used by the fixed 10/10/5/5 campaign. Historical unused
orchestration and unrelated cleanup were excluded.

## Findings fixed

1. **Ordinary false hard stop.** Luna's broad `unauthorized_consequence` label
   could force Regenerate for localized continuity drift. The standing policy
   now uses D-223 v2: only the two Ted protections, protected consent or major
   choice, immutable locked facts, and unusable output are hard.
2. **Missing protected-boundary class.** Consent, withdrawal, and a major lasting
   protected-user choice formerly shared an overbroad consequence class. They
   now have the exact hard `protected_boundary_conflict` class.
3. **Historical manifest drift.** V45 validation inherited the live policy
   object, so a policy update could make a prior frozen manifest unreadable.
   V45 is now an exact historical v1-policy snapshot; new roots use V46.
4. **Historical policy-audit reload.** Policy v1 acceptance audits now resolve
   against their own frozen policy version rather than the current v2 object.
5. **Recorder Windows path length.** Fixed earlier at commit `b9d1343` by using
   a short atomic recording-bundle staging name; run-02 scene 1 proved the
   Recorder can publish successfully at the qualification root.
6. **Fresh-run fixture dead end.** The manifest builder rejected an exact replay
   of the fixed 10/10/5/5 campaign after a code correction. V46 now permits only
   a byte-identical frozen fixture-set replay; partial, renamed, and mixed reuse
   remain rejected.
7. **Outer wait could abandon valid work.** Provider processes were already
   configured not to cancel on elapsed time, but the qualification HTTP caller
   still had a 30-minute socket cutoff. Current V46 declares
   `observe_until_terminal` and uses a one-day infrastructure wait. Historical
   V43-V45 timeout metadata remains unchanged.

## Reviewed and retained hard stops

- Planner schema, evidence, actor, decision-owner, route-transition, and causal
  ordering failures.
- Python candidate/request/custody hashes, branch head, privacy projection, and
  exact Reader/Luna binding failures.
- Reader inconclusive or whole-candidate unusable output.
- Adult identity, adult capacity, current consent, withdrawal, freedom to stop,
  privacy, protected projection, and route-transition failures.
- Provider authentication, request/custody mismatch, malformed completed output,
  budget exhaustion, ambiguous dispatch, and corrupted durable state.

These are integrity or safety failures rather than harmless prose variation.
Provider elapsed-time labels remain observations in the live composition;
Planner, Luna, Reader, and Pi process execution are configured not to cancel a
valid in-flight result solely because the observation threshold elapsed.
The qualification caller now waits for that terminal result rather than
imposing the former 30-minute campaign-turn cutoff.

## Focused evidence

- Exact ordinary scene-2 shape: Luna `unauthorized_consequence` plus Reader
  `exact_quote` -> provisional accepted, one Writer, Recorder runs, no Regenerate.
- All current Luna classes exhaustively partition into five hard and eight
  provisional classes.
- Historical policy v1 audit construction/reload remains valid.
- V3 Python/JavaScript/schema generated artifacts accept current v2 and retain
  the exact historical v1 version/hash pair.
- Historical qualification V45 and current V46 execution policies validate
  independently.
- Exact V41 replay is accepted, while renamed or partially overlapping fixture
  reuse still fails closed.

No provider call was made during this review. After commit/push, start a fresh
V46 10/10/5/5 root from scene 1; do not resume the spent/paused run-02 root.
