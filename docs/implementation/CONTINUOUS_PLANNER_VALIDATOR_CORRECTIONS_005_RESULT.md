# Continuous Planner/Validator Corrections 005 Result

**Decision:** D-192

**Scope:** provider-free shadow/test correction only

**Active route:** unchanged D-180

## Outcome

Correction cycle 005 closes the ingress-ownership, final-candidate authority,
accepted-context scoping, compatibility, and exact-canary gaps identified by
the consumed correction-cycle-004 repository review.

- Python no longer guesses protected-user ownership from wording or punctuation.
  The continuous request requires exact typed ingress units with an explicit
  actor or speaker and gap-free coverage of the complete source; only Ted-owned
  action, state, or dialogue units create protected-user claims.
- DeepSeek's advisory draft includes an exhaustive gap-free story-segment
  ledger. Each segment retains exact output bytes, actors, subjects, dialogue
  speaker, semantic kind, and protected-user claim ownership. Python rejects
  gaps, overlap, changed bytes, invented or paraphrased Ted authorship, and
  disagreement between protected realization and story-segment ledgers.
- Validator final items bind exact Planner beats and Composer segments, actor
  and subject sets, field visibility/owner scopes, and protected-user claim
  provenance. Every factual field repeats the exact Composer segments and their
  actor/subject/claim custody. A Ted-authored field must equal one supplied claim;
  event text is derived from cited final facts, and protected edits cannot add a
  second action after a valid quote. Event participants equal the justified
  actor/subject union; edits, created fields, and event records retain the exact
  source-field claim set.
- Accepted-session evidence is split into canonical field facts. Public
  projections contain public facts only; one owner projection adds only that
  owner's private facts. Arbitrary event participation cannot create future
  private character authority, and the accepted user message is not reused as
  a character-evidence shortcut.
- Candidate identity includes the evidence registry, claim, realization,
  story-segment, accepted-projection, prompt, and schema identities. Candidate
  storage, creator action, promotion receipt, debug replay, and acceptance
  journal require the same candidate and authority-context hashes.
- Continuous-session reconstruction requires the active compatibility hash.
  Protected-user and session policies advance to v5, preventing old Planner or
  Validator threads from resuming under the corrected contracts.
- The short-canary harness accepts only a newly bound cycle, task,
  authorization hash, checkpoint, and ten-call ceiling; it rejects the
  historical failed identity. Turn 2 omits the repeated Sakura summary. The
  exact `JobHarness` now has a provider-free three-turn/two-scene ten-stage test,
  and an actual subprocess fixture proves the durable `thread_run` marker and
  conservative call accounting.
- The low-level creator-action API now requires the caller's frozen candidate
  and authority-context hashes; reading the candidate directory alone cannot
  bypass that agreement.

## Contract identities

- Planner prompt/adapter: `cera.continuous_planner_prompt.v6` /
  `cera.continuous_planner_adapter.v5`
- Validator prompt/request/adapter/draft:
  `cera.continuous_validator_prompt.v6`,
  `cera.continuous_validator_request.v3`,
  `cera.continuous_validator_adapter.v5`, and
  `cera.continuous_validator_draft.v4`
- DeepSeek prompt/adapter/draft: `cera.continuous_deepseek_prompt.v3` /
  `cera.continuous_deepseek_adapter.v3` /
  `cera.continuous_deepseek_draft.v3`
- Ingress and realization: `cera.continuous_ingress_source_unit.v1`,
  `cera.protected_user_source_claim.v3`,
  `cera.protected_user_realization_span.v1`, and
  `cera.story_realization_segment.v1`
- Finalization and accepted context: `cera.complete_final_sequence.v4`,
  `cera.validator_finalization_package.v4`, and
  `cera.accepted_session_projection.v3`
- Evidence and acceptance: `cera.request_evidence_binding_registry.v6`,
  `cera.continuous_world_promotion_receipt.v2`, and
  `cera.continuous_acceptance_journal.v4`
- Continuous compatibility policies:
  `cera.continuous_protected_user_policy.v5` and
  `cera.continuous_session_policy.v5`

## Verification boundary

The focused correction suite passes 85/85. The final complete provider-free
repository suite passes 735/735 in 289.066 seconds with one expected
environment-dependent skip; the enclosing measured command completed in
291.096 seconds. Documentation/current-profile/source-inventory checks pass
7/7, compilation passes, and `git diff --check` is clean.

An earlier full-suite pass attempt exposed four obsolete test fixtures after
the contract bump: one missing ingress-unit request and three final-item
ownership fixtures. Those fixtures were updated to the shared contracts; the
final uninterrupted 734-test run is the controlling verification.

Progressions 1-3 made zero provider calls, retries, fallbacks, live story or
database writes, active-route changes, service or installed SillyTavern
changes, deployments, merges, remotes, or pushes.

## Remaining boundary

Stage 4 is a new provider-free integration audit under a new checkpoint and
review-cycle identity. No live ten-call canary or other provider call is
authorized.
