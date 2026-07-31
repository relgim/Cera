# Job 1 Result - Confined and Frozen Review Identity

task_id: `review-source-confinement-and-snapshot-v2`
status: completed

## Progression

The repository-cycle implementation now derives the trusted CERA root from its
own checked-in location (or verifies the Git root), confines cycle directories
to `.chatgpt/pro-review/cycles/<cycle-id>`, and rejects artifacts outside CERA,
protected paths, database/key material, traversal, and reparse-point escapes.

Publication now verifies an exact existing 40- or 64-character Git object,
validates the bounded checkpoint ZIP and UTF-8 result artifacts, inventories all
non-excluded Git changes, and writes a deterministic `SOURCE_SNAPSHOT.zip` plus
`CHANGED_SOURCE_MANIFEST.json`. The task-set identity binds the source-root
hash, complete changed-file inventory, expected Job 4 result location, and
predecessor-cycle identity. Later transitions reject source changes after
publication.

## Primary implementation evidence

- `tools/pro_review_cycle.py` SHA-256:
  `b8c38696b4f1a53ae4a05bd94495a0a966573d92aa340174c35a065b48674f27`
- `tools/pro_review_cycle_core.py` SHA-256:
  `56fc2575cae9d0d65de74f5a4ebf32109c2b636067d07e49fc1c80901ac6dc1b`

The generated cycle package, rather than these prose hashes alone, is the
authoritative frozen review identity.
