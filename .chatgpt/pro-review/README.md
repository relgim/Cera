# CERA ChatGPT Pro Checkpoint Reviews

This tree contains checkpoint review handoffs governed by
`docs/authority/CODEX_PROGRESS_REVIEW_PROTOCOL.md`.

The primary workflow uses deterministic directories under `cycles/`. Each
cycle contains an immutable manifest/outbox, one exact connector response path,
append-only receipts, and a recoverable state view. The package distinguishes
current or revised Jobs 1-3 from the preceding Job 4 result, and it proves that
the next named Job 4 was creator-authorized before publication.
The source identity is status-aware: tracked runtime source is reviewable,
generated root runtime state is excluded, and deletions/renames are represented
with tombstones and old/new paths. V1/V2 direct-predecessor cycles remain
readable. V3 additionally permits only an exact contiguous sequence gap whose
every intervening identity has an immutable failed-pre-manifest receipt copy
and typed tombstone. Those tombstones convey sequence custody only; they are
not consumed cycles or Job 4 authority. Modern predecessor and latest-consumed
lookup revalidate the full outbox, source archive, typed receipt chain, Job 4
result and report, accepted response, and any published V3 gap custody.

Codex activates the existing ChatGPT Pro chat through the supported app thread
operation, then performs only that pre-authorized Job 4. After Job 4 it consumes
only the matching stable repository response. Ted does not relay files or
messages during an ordinary cycle. Repository publication by itself is not a
ChatGPT trigger. `TRIGGER_SENT.json` binds the exact generated message and
caller-supplied successful app result by hash but is not independent delivery
proof; the app task audit plus a matching Pro repository response establishes a
tested no-user-action cycle.

Historical checkpoint directories remain immutable evidence. The Downloads
bridge remains a manual emergency fallback. ChatGPT Pro reviews independently;
neither its response nor Codex's recommendation grants creator authority.

Read `docs/operations/PRO_REVIEW_REPOSITORY_CYCLE.md` and use
`CYCLE_SPEC_TEMPLATE.json` for new cycle specifications.
