# Job 1 Result - Exact Status-Aware Review Identity

task_id: `review-status-aware-source-identity-v3`
status: completed

## Progression

The trusted-root review snapshot now distinguishes protected generated root
state from legitimate tracked source. Root `runtime/` has a typed exclusion,
while `src/cera/runtime/**` is reviewable. External paths, traversal,
links/junctions, protected root state, credentials/secrets, databases, and key
material still fail closed.

Git porcelain status is preserved per change. Additions, modifications,
deletions, copies, and renames retain the two-character status, old/new paths,
content state, and deletion tombstones. Only present bytes enter the
deterministic archive. The source manifest records and enforces aggregate
file-count and uncompressed-byte ceilings, and later validation reconstructs
the exact status-aware current identity.

Each progression Markdown file must declare the exact spec-bound `task_id` and
one final `status: completed | blocked`. The task-set binds that parsed status
as well as the document hash, preventing a stale hash-valid result from being
silently relabeled.

## Primary implementation evidence

- `tools/pro_review_cycle.py` SHA-256:
  `b8c38696b4f1a53ae4a05bd94495a0a966573d92aa340174c35a065b48674f27`
- `tools/pro_review_cycle_core.py` SHA-256:
  `8a18ad8036b433ccd0c80f321115087d095706174df327ddf648afee6b85e0f0`

The generated v2 changed-source manifest and source archive, not these prose
hashes alone, are the authoritative frozen identity.
