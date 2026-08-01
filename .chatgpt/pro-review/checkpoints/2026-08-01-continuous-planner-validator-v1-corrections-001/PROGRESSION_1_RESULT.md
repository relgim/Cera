# Progression 1 Result

task_id: `continuous-authoritative-evidence-and-call-accounting-v1`

status: completed

## Outcome

Python now allocates every request-local evidence handle. The current user
source is bound before prompting. Exact world-record handles are created only
by `cera_world_read` or a revision/hash-bound deterministic initial character
projection. Each handle binds world, branch, turn, exact path, revision,
content SHA-256, record type, visibility, knowledge owner, and read operation.

Planner beats fail on invented handles, stale content/revisions, sibling scope,
unread records, or private-knowledge transfer. Final sequence items and edits
remain traceable through Planner beat keys to the resolved registry.

The continuous provider adapters now require a durable append-only call ledger.
`dispatch_initiated` is recorded before transport, so all later transport,
bridge, decoding, schema/domain, or world-MCP failures count conservatively.
Safe receipt, failure-receipt, operation-telemetry, tool-sequence, and stored-
thread hashes are retained when available.

Provider-free reproduction proved that the failed Job 4 compatibility path
called absent `ContinuousWorldStore.world_directory_identity`; the implemented
contract is `world_identity_sha256`. The script now uses the real method. A
root diagnostic exists before account/backend/session/world/prompt setup and
preserves safe stage, operation, attribute name, exception type, and frames.
The historical failed report remains unchanged.

## Verification

- evidence-binding positive, invented, stale, sibling, unread, and private-
  transfer cases: passed;
- final-sequence/edit traceability: passed;
- eight post-dispatch failure classes plus transport failure accounting:
  passed;
- exact compatibility failure reproduction and corrected path: passed;
- provider calls: 0.
