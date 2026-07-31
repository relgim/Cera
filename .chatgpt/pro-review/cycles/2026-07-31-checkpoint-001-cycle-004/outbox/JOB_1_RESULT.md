# Job 1 Result - Confined and Frozen Review Identity

task_id: `review-source-confinement-and-snapshot-v2`
status: completed

## Progression

The repository-cycle implementation derives the trusted CERA root from its own
checked-in location (or verifies the Git root), confines cycle directories to
`.chatgpt/pro-review/cycles/<cycle-id>`, and rejects artifacts outside CERA,
protected active paths, database/key material, traversal, and reparse-point
escapes.

Publication verifies an exact existing 40- or 64-character Git object,
validates the bounded checkpoint ZIP and UTF-8 result artifacts, inventories all
non-excluded Git changes, and writes a deterministic `SOURCE_SNAPSHOT.zip` plus
`CHANGED_SOURCE_MANIFEST.json`. The task-set identity binds the source-root
hash, complete changed-file inventory, expected Job 4 result location, and
predecessor-cycle identity. Later transitions reject source changes after
publication.

The preserved checkpoint's confined
`.chatgpt/pro-review/checkpoints/<id>/EVIDENCE_PACKAGE/runtime/` evidence is
classified as historical evidence rather than active runtime. The exception is
narrow, remains repository-confined and link/suffix/name checked, and is applied
symmetrically during publication and every later transition. Actual runtime
paths remain forbidden.

## Primary implementation evidence

- `tools/pro_review_cycle.py` SHA-256:
  `b8c38696b4f1a53ae4a05bd94495a0a966573d92aa340174c35a065b48674f27`
- `tools/pro_review_cycle_core.py` SHA-256:
  `8abc25ba4f4d8cc02a8b94a93c1bd4c11cc4cf11731fc0b1aaacb6d00b4db894`

The generated cycle package, rather than these prose hashes alone, is the
authoritative frozen review identity.
