# Continuous Branch Materialization V3 Result

## Outcome

The continuous shadow route now requires a Python-owned immutable branch
materialization receipt before either a physical accepted-checkpoint provider
fork or a cross-branch reconstruction. Matching accepted envelopes alone no
longer qualify a child branch.

## Materialization contract

`ContinuousWorldStore.materialize_branch_from_checkpoint` atomically creates a
previously nonexistent child branch from the exact parent cutoff. It binds:

- parent and child world, branch, physical-directory, accepted checkpoint,
  accepted ancestry, parent-thread, and branch-cutoff identities;
- complete deterministic parent and child ACTIVE manifests covering Character,
  Relationship, Rule, Location, Event, Scene, WORLD_STATE, and WORLD_INDEX;
- the parent and child ACTIVE tree hashes, WORLD_STATE hashes/revision/current
  scene/ordered accepted turns/head, and accepted promotion artifacts;
- authority, privacy, protected-user, session, and persistence policies; and
- exact current character-summary source path, revision, bytes, owner,
  authority classification, and derived-envelope identity.

The copied child WORLD_STATE changes only its branch identity. Python rebuilds
the child index deterministically from that state. Every other ACTIVE record
and accepted checkpoint artifact remains byte-identical to the parent cutoff.

## Pre-transport enforcement

The runtime compares provider and policy compatibility field by field and
checks each physical directory identity directly. It no longer normalizes the
target branch or directory identity to the parent for comparison.

Immediately before `fork_branch` or cross-branch thread reconstruction, Python
revalidates the immutable on-disk receipt, both manifests and trees, semantic
WORLD_STATE, accepted checkpoint artifacts, and summary sources. Empty,
partial, stale, foreign, replayed, or modified branches stop before transport.

Only summary deliveries still exact at the parent cutoff are inherited. A
delivery made stale by an accepted character-record update is dropped, so it
cannot suppress the child’s required summary refresh.

## Provider-free verification

Focused tests cover:

- successful complete materialization and the first lean child fork;
- empty and partial children;
- independent tamper of Character, Relationship, Rule, Location, Event, Scene,
  WORLD_STATE, and WORLD_INDEX;
- scene, head, accepted-order, checkpoint-artifact, and directory changes;
- stale parent snapshots and foreign or replayed receipts;
- missing, stale, changed-revision, and wrong-owner summary sources; and
- proof that every invalid case stops before the in-memory provider fork seam.

No external provider, story database, active route, service, deployment,
remote, or production resource was used or changed.
