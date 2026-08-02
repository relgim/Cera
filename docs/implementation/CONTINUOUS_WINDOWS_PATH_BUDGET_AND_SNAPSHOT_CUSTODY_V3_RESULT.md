# Continuous Windows Path Budget and Snapshot Custody V3 Result

## Outcome

Progression 1 of Queue 0028 is complete provider-free. Accepted Planner
snapshots now use a compact deterministic locator that remains inside a
repository-owned 248-character resolved-path budget on Windows hosts where
legacy path handling is active. Complete identities and hashes remain in the
immutable envelope and receipt; abbreviated path components are locators only.

The fresh inherited scripted-v10 base transaction completed all ten local
stages under a 133-134-character branch root and crossed publication, Job 4
completion, completed-chain validation, and recovery with zero external
provider calls.

## Accepted snapshot contract

New accepted snapshots use:

```text
PLANNER_SESSION/ACCEPTED/v2/
<accepted-turn-sha256[0:16]>-<snapshot-sha256[0:24]>.json
```

The same-directory temporary path appends `.tmp`. The path plan binds the
resolved lengths of both immutable paths and both mutable-current paths to the
exact path-policy hash. Python preflights the fixed-width capacity before
`world.apply_creator_action`, so an over-budget root cannot begin creator
acceptance or mutate WORLD_STATE, the acceptance journal, or session files.
The exact snapshot hash is preflighted again before publication.

`cera.continuous_session_snapshot_receipt.v2` retains the complete:

- accepted-turn ID and its full SHA-256;
- accepted-final envelope SHA-256;
- provider-thread SHA-256;
- snapshot SHA-256;
- nested exact context-injection receipt and operation SHA-256;
- canonical encoded inner snapshot-envelope SHA-256;
- immutable outer-file SHA-256;
- compact relative locator, mutable-current path, and complete path plan.

The two encoded-byte hashes avoid a self-hash cycle: the inner-file hash binds
the canonical V1 snapshot envelope embedded in the V2 authority envelope, and
the immutable-file hash binds the exact physical V2 outer bytes. Load verifies
both layers, all complete identities, the injection receipt, and the current
resolved root.

Exact replay is idempotent. Different full identity at the same compact
locator is an explicit collision; different bytes for the same identity are
immutable tamper. Both fail closed. Historical V1 receipt and path decoding is
unchanged and no completed snapshot is rewritten.

## Accepted-checkpoint fork path

The equal-length executable test exposed the prior verbose materialization
staging directory as a second legacy-path risk. New staging roots use the
compact same-parent `.m-` prefix. Python preflights the final child and staging
full-SHA materialization receipt plus each same-directory temporary path. An
over-budget child fails before the child or staging materialization is created;
the parent tree remains unchanged.

## Provider-free verification

The focused gate passes 70/70 and covers:

- long-root save, immutable load, mutable-current restart, reconstruction, and
  accepted-checkpoint session fork;
- exact replay, compact-locator collision, immutable tamper, and pre-mutation
  path-budget failure;
- historical V1 receipt/path decoding and V1/V2 registry coexistence;
- real world materialization success, adversarial tamper, and over-budget
  child failure before mutation;
- the actual scripted-v10 CLI with ten local invocations, accepted-session
  synchronization, immutable V2 snapshot custody, branch fork,
  reconstruction, and terminal evidence V5; and
- a fresh disposable repository cycle crossing publication, Job 4 completion
  v2, explicit completed-chain validation, and recovery.

Compilation of every changed runtime module passes. The system Python lacks
CERA's pinned provider SDK metadata; all executable qualification uses the
repository `.venv`, which contains `mcp==1.29.0` and
`openai-codex==0.144.4`.

## Effects and boundary

External provider calls, provider retries, fallbacks, story database writes,
active-route changes, service changes, installed SillyTavern changes,
deployment, merge, remote operations, and pushes were all zero. D-180 remains
the active runtime route. Progression 2 remains the next Queue 0028 item; this
result does not authorize live dispatch.
