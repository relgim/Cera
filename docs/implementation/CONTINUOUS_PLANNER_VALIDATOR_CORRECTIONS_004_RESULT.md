# Continuous Planner/Validator Corrections 004 Result

**Decision:** D-191

**Scope:** provider-free shadow/test correction only

**Active route:** unchanged D-180

## Outcome

Correction cycle 004 closes the source-attribution, private-context,
realization-ownership, immutable-snapshot, and true-submission gaps identified
by the exact consumed correction-cycle-003 review.

- Python projects protected-user authority only from exact source spans whose
  attribution grammar proves Ted owns the action or dialogue. NPC-attributed
  and unattributed quoted text cannot become protected-user authority.
- Each final sequence item carries the exact protected-user claim keys it uses.
  DeepSeek must return exact occurrence spans for every realized claim, and
  Python validates those spans before the Validator can assess the candidate.
- Accepted-session context is represented by typed, scene-bound projections.
  Public projections contain public material only; an owner projection contains
  public material plus that one owner's private items. Pair, event, envelope,
  item, revision, and hash agreement are revalidated from exact stored bytes.
- Acceptance saves an immutable per-turn Planner snapshot and returns a typed
  receipt. The acceptance journal recomputes the snapshot before it may mark
  synchronization complete; pointer replacement cannot rewrite accepted proof.
- Codex call accounting distinguishes worker start, local preflight, and the
  actual `thread_run` submission boundary. A local failure before submission
  consumes zero calls; a failure at or after submission consumes one.
- The provider-free Job 4 harness now invokes the shared runtime coordinator for
  all Planner, Composer, Validator, acceptance, and Scene Change behavior rather
  than maintaining a second manual implementation.

## Contract identities

- Planner prompt/adapter: `cera.continuous_planner_prompt.v5` /
  `cera.continuous_planner_adapter.v4`
- Validator prompt/adapter/draft: `cera.continuous_validator_prompt.v4` /
  `cera.continuous_validator_adapter.v3` /
  `cera.continuous_validator_draft.v2`
- DeepSeek prompt/adapter/draft: `cera.continuous_deepseek_prompt.v2` /
  `cera.continuous_deepseek_adapter.v2` /
  `cera.continuous_deepseek_draft.v2`
- Evidence: `cera.request_evidence_binding.v4`,
  `cera.request_evidence_binding_registry.v4`, and
  `cera.accepted_session_projection.v1`
- Source/finalization: `cera.protected_user_source_claim.v2`,
  `cera.rich_planner_sequence.v3`, `cera.complete_final_sequence.v2`, and
  `cera.validator_finalization_package.v2`
- Proof: `cera.protected_user_realization_span.v1`,
  `cera.continuous_session_snapshot_receipt.v1`, and
  `cera.continuous_provider_call_ledger_event.v3`

## Verification boundary

Focused continuous tests pass 91/91. The complete provider-free repository
suite passes 727/727 in 297.090 seconds with one expected environment-dependent
skip; the enclosing measured command completed in 298.182 seconds. Compilation
also passes. The suite uses fake/recorded local transports only and made zero
live provider calls.

Progressions 1-3 make zero retries, fallbacks, live story or database writes,
active-route changes, service or installed SillyTavern changes, deployments,
merges, remotes, or pushes.

## Remaining boundary

Stage 4 is a new provider-free integration audit using fake/recorded provider
results and the exact shared coordinator. No live ten-call canary or other
provider call is authorized.
