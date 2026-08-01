# Continuous Planner/Validator Corrections 006 Result

**Decision:** D-193

**Status:** provider-free Progressions 1-3 complete; governed Stage 4 review and Job 4 not yet executed
**Active route:** unchanged `cera.active_runtime.d180.v1`

## Completed corrections

### Trusted continuous ingress

- `ContinuousTurnRequestV1` carries only a receipt ID/hash plus the exact
  session, request, turn, branch, raw-source, and idempotency identities.
- `ContinuousIngressAuthorityStore` issues either a prepared-ingress receipt
  bound to existing envelope/classification receipts or a named frozen Python
  fixture receipt. The coordinator resolves the stored receipt and rejects
  caller substitution before any Planner call.
- The receipt binds every ordered source-unit hash, actor/speaker
  classification, adapter identity, and protected-user identity. The receipt
  hash is included in candidate and authority-context identity.

### Role, final-field, event, and accepted-fact authority

- `CharacterRoleLedgerV1` separates action owners, state/thought/emotion/
  consent/decision owners, and dialogue speakers from affected, addressed,
  observing, and referenced characters.
- A Ted-owned assertion requires exact supplied claim keys. An NPC action may
  affect or address Ted without assigning Ted a reaction.
- Planner beats, exhaustive Composer segments, final field scopes/items,
  per-item event role ledgers, participants, and owner-scoped accepted facts
  use the same versioned role contract.
- Event participants and item-role custody are Python-derived from the complete
  final sequence rather than repeated provider bookkeeping.
- Every world edit and created field, protected or unprotected, must equal one
  exact cited final-field value and the fixed persistence reason. No open
  semantic transform is enabled in V6.
- Candidate, authority, event, accepted-fact, promotion, and acceptance-journal
  identities include the corrected ingress and role-bearing package bytes.
  Pre-v6 continuous Planner/Validator sessions fail compatibility checks.

### Complete provider-free harness and worker evidence

- Declared unittest IDs are resolved to exactly one real test before
  publication; the stale Cycle 005 class name is rejected and the actual
  `ContinuousSessionTests` v6 compatibility test resolves.
- The complete ten-stage JobHarness path now supports injected scripted
  transports behind the actual `CodexContinuousPlannerPort`,
  `DeepSeekContinuousComposerPort`, and `CodexContinuousValidatorPort`.
  The shared wrapper still exercises Pro polling, call-ledger stages, exact
  stored-thread telemetry, creator acceptance, Scene Change, immutable
  snapshots, and cleanup. The provider-free proof records ten scripted
  transport invocations and zero external provider calls.
- The real parent/child protocol is covered at worker launch, SDK import,
  account check, thread resume, `thread_run`, post-return, success, malformed
  envelope, pre-submit timeout, post-submit timeout, and stranded in-flight
  accounting. The ledger proves zero-call versus one-call classification and
  no double counting.

## Version changes

- Planner: `cera.rich_planner_sequence.v4`, Planner adapter v6, prompt v7.
- Composer: `cera.story_realization_segment.v2`, DeepSeek draft/adapter/prompt
  v4.
- Validator/final: `cera.complete_final_sequence.v5`, Validator draft v6,
  adapter v7, finalization package v6, prompt v7.
- Authority: `cera.continuous_ingress_receipt.v1`,
  `cera.character_role_ledger.v1`,
  `cera.event_item_role_ledger.v1`,
  `cera.accepted_session_fact.v1`,
  `cera.accepted_session_projection.v4`, registry v7, and acceptance journal
  v5.
- Session policy identities advance to
  `cera.continuous_protected_user_policy.v6` and
  `cera.continuous_session_policy.v6`.

## Verification

- Focused continuous suite: **106/106 passed** in **11.711 seconds**.
- Complete repository suite: **742/742 passed** in **297.803 seconds**, with
  one expected environment-dependent skip; measured command wall time
  **298.081 seconds**.
- Compilation and `git diff --check`: clean.
- Live provider calls, retries, fallbacks, live story acceptance, production
  writes, active-route changes, service/SillyTavern mutation, deployment,
  merge, remote operations, and push: **zero**.

## Preserved limits

- Cycle 005 and its failed 13/14 Job 4 evidence remain immutable and are not
  rerun or reinterpreted.
- Scripted transports establish orchestration and contract behavior, not live
  provider schema acceptance, session behavior, latency, prose quality, or a
  live ten-call qualification.
- D-180 remains the active route. A live Stage 4 remains separately gated.
