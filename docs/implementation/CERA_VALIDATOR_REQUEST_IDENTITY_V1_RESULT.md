# CERA Validator request identity V1 result

**Queue:** 0052

**Status:** provider-free correction qualified

Python now derives a deterministic package identity before every turn or scene
summary Validator dispatch. Validator request V9 supplies exact `package_id`,
`world_id`, `branch_id`, and candidate identity. Adapter V18 const-binds the
three output identities in the submitted provider schema and verifies their
exact equality again before DTO compilation.

This closes the request defect that caused a grounded Pro Writer candidate to
return `missing_package_id` / `request_contract_defect`. It does not change the
V9 semantic decision DTO, Writer bytes, story authority, active route, or
production state.

The bounded recovery replays the exact frozen Planner and Writer bytes with no
provider call, then permits one Sol-medium Validator and a Reader only after
Validator acceptance. No DeepSeek call is permitted.

Provider-free verification:

- Python compilation passed for `src`, `scripts`, and `tests`.
- 102 focused tests passed, including request V9 custody, provider-schema
  constant projection, post-provider identity mismatch rejection, runtime
  forwarding, and the closed ten-stage Job 4 harness.
- No provider call, story/database mutation, active-route change, deployment,
  merge, remote operation, or push occurred during the correction.
