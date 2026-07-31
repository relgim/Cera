# Job 3 Result - Protocol, Trigger, and Adversarial Proof

task_id: `review-protocol-trigger-tests-v2`
status: completed

## Progression

The cycle accepts one through three substantial progression results and
packages the full controlling review context, source archive, changed-file
manifest, historical checkpoint evidence, predecessor proof, and precise
questions and exclusions. Startup can locate the latest validated consumed
repository response as well as historical checkpoint reviews.

The generated trigger message is hash-bound. `record-trigger` accepts only an
app-returned result whose thread identity matches the target and records it as
an attested successful app result, never as independent proof of delivery.
Publication is documented accurately as individually atomic immutable writes
with a final commit marker and crash recovery.

The focused bridge suite contains 42 tests: all 17 V1 fallback cases plus 25
repository-cycle cases. It covers one/two/three progression counts, external
and protected paths, confined historical-runtime evidence through a later
transition, Git object existence, ZIP validity, source and outbox mutation,
semantic authorization, structured effects, placeholder responses, identity
conflicts, unstable writes, interrupted recovery, append-only waits, concurrent
immutable writes, predecessor chaining, full request context, and
latest-consumed startup discovery. The symlink-escape test skips only when the
host Windows policy does not permit creation of the test symlink; production
reparse-point rejection remains active.

## Primary implementation evidence

- `tests/test_pro_review_bridge.py` SHA-256:
  `babb30fd27a59d5712fec08869e6ae735eecbc53d7464387e35572c840d1e62c`
- `docs/operations/PRO_REVIEW_REPOSITORY_CYCLE.md` SHA-256:
  `1492d347bbf6ac09ff23f25ca0c1ad01024f7852408823070a084556530e775f`
- `docs/authority/CODEX_PROGRESS_REVIEW_PROTOCOL.md` SHA-256:
  `6f5e8f8f893da1064153f8054e2c71ff9362a1511d26fe3380a9b4fa46c8feed`
- `AGENTS.md` SHA-256:
  `a8a2761c935ce58cd7f4fadbc0b7c276b949cf6d5c52dcb84a2b5e0cfec66bb1`

Focused verification before publication: 42/42 passed in 21.666 seconds, with
the one environment-dependent symlink creation skip described above.
