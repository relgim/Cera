# CERA Continuous Planner/Validator Live Canary 001 Preflight Failure

checkpoint_id: `2026-08-01-continuous-planner-validator-v1-live-canary-001`
cycle_id: `2026-08-01-continuous-planner-validator-v1-live-canary-cycle-001`
job4_task_id: `continuous-planner-validator-three-turn-scene-change-live-canary-v1`
status: blocked_before_publication
provider_calls: 0

## Verified predecessor

The repository review-cycle tool reported Cycle 008 as the latest valid
consumed review with exact accepted-response SHA-256
`9e84e4994256a3663ee551ba99b8f6cd6f9fd049b6615beb6030efadb704635b`.
The accepted checkpoint is
`34d44b5f4717d59153a4e38999f29489f979d6fc`. The proposed checkpoint,
cycle, and Job 4 identities were absent before preparation.

## Terminal provider-free preflight defect

`scripts/run_continuous_planner_validator_job4.py` emits a typed
`cera.pro_review_job4_result.v1` object containing an additional top-level
`authorization_sha256` field and an additional
`effects.scripted_transport_invocations` field.

The production repository-cycle decoder in `tools/pro_review_cycle_core.py`
uses closed exact-key validation. Direct validation produced:

```text
CycleError: Job 4 result fields do not match contract; missing=[], unexpected=['authorization_sha256']
CycleError: Job 4 effects fields do not match contract; missing=[], unexpected=['scripted_transport_invocations']
```

Therefore a completed or terminal live canary result could not pass
`complete-job4`. Spending provider calls before correcting and separately
reviewing this deterministic transport mismatch would leave the authorized
review cycle unable to complete its receipt chain.

## Test gap

The exact scripted-V8 CLI test passes and explicitly expects
`scripted_transport_invocations` in the Job 4 result, but it does not submit
that result to `validate_job4_result_contract`. Documentation and active-profile
checks also pass. This explains why Cycle 008 provider-free qualification did
not expose the production decoder mismatch.

## Evidence identities

- Frozen source HEAD before this evidence-only checkpoint:
  `62af0b10ef0df64a3fff76b8140776045bb1108b`
- Live canary harness SHA-256:
  `6a4c64827db04108b6804099c8e49ac0386062abc1783e214b1250d9a3a7ad22`
- Review-cycle decoder SHA-256:
  `99d2ff9ef7239bf669cb42c044111892ee7b0f2dda98147caccec2380acc29fa`
- Cycle 008 accepted response SHA-256:
  `9e84e4994256a3663ee551ba99b8f6cd6f9fd049b6615beb6030efadb704635b`

## Effects

- External provider calls: **0 / 10**
- Retry, fallback, hidden repair, provider substitution, or extra verifier: **0**
- Live-story, production database, installed SillyTavern, service, deployment,
  merge, remote, push, or active-route effects: **0**
- New live-canary cycle publication: **not performed**
- Job 4 execution: **not started**

The creator authorization requires preservation, notification of the exact Pro
review conversation, and a complete stop rather than an implementation patch.

