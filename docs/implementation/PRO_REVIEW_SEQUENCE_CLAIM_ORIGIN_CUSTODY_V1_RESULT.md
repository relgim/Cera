# Pro Review Sequence Claim and Origin Custody V1 Result

task_id: `pro-review-global-sequence-claim-and-origin-custody-v1`
status: completed

## Outcome

The repository review protocol now has additive V4 sequence authority. A fixed
repository-global namespace owns each sequence before manifest publication.
Canonical claims are created with exclusive non-replacing writes, form an exact
predecessor-hash chain, and are retired by one immutable disposition. Exact
claim retries are idempotent; conflicting claims, dispositions, or manifest
occupancy fail closed.

V1, V2, and V3 remain historical readers. After V4 activation, they cannot
publish at or above sequence 29. A V4 success disposition binds the current
claim, manifest root, and `PUBLISHED.json` hash before `JOB4_STARTED.json`.
Pre-manifest failure uses a fixed global failure record and terminal failed
disposition instead. Neither form grants execution or creator authority.

`latest_consumed_cycle()` now validates all candidates before selection. More
than one valid cycle at the highest sequence is an explicit conflict; directory
name ordering cannot select authority.

## Historical adoption and origin custody

The read-only real-repository adoption preflight uniquely reconstructed:

- consumed sequence 25 from cycle
  `2026-08-02-continuous-lean-two-call-concept-v2-cycle-001`, consumption
  receipt `be865a059c5b36a4c9ac6e2fbd2bc9ec3edaebb12b092927fd2eae68039f0cc5`;
- failed sequence 26 from the consumed Cycle 28 published tombstone
  `14e08404daea78a326b064a0587a9fe5f43f23ace0e58b5f9c76390b12b8e229`;
- failed sequence 27 from the consumed Cycle 28 published tombstone
  `06cadad8b602a37d06f8b17b455898f8324165c0e51327c7c569528596f31c22`;
- consumed sequence 28 from cycle
  `2026-08-03-pro-review-failed-pre-manifest-gap-v1-cycle-001`, manifest root
  `b96f4c3c5599e4b73d28dfdc0914ea82eeeace892336d4e0810500b10d2d250c`
  and consumption receipt
  `13901bb4faa54bc698a5ce08011b1cc21f6b52a958a05a51a57246dd6af2ba15`.

Historical 26/27 recovery uses only Cycle 28's validated manifest, outbox
tombstones, accepted response, and receipt chain. The validator rejects
successor-staged originals, self/same-file aliases, mutable fabricated empty
directories, current-cycle substitution, cross-sequence evidence, traversal,
symlink, junction, and hard-link substitution.

The previously mislabeled V3 evidence now reports the canonical
manifest-bound residual-inventory roots:

- sequence 26:
  `72094c8b2dcf0bfb4f1d7ef1e19f4be87352e3165051f0a9f28b3169215a5896`;
- sequence 27:
  `13d07d4e5aba16bb20e4c8175c8638631607978c7ec293a1eb5dbb3e9e2586dd`.

## Focused verification

- `ProReviewSequenceAuthorityTests`: `10/10` passed.
- `ProReviewFailedPreManifestGapTests`: `21/21` passed.
- Existing predecessor, latest-consumed, forged-higher-sequence, and
  predecessor-response-chain gates: `4/4` passed.
- Documentation tests: `3/3` passed.
- Affected Python compilation: passed.
- Real-repository 25-28 adoption build: passed read-only.
- Complete repository suite: not run; Queue 0042 prohibits it.

## Effects and exclusions

- Codex/Sol provider calls: `0`
- DeepSeek calls: `0`
- Terra calls: `0`
- Story/database/route/service/installed-SillyTavern effects: `0`
- Deployment/merge/remote/push effects: `0`
- Historical Cycle 28 and original 26/27 evidence: unchanged

The fresh Planner control remains closed and unspent. V4 sequence records own
review-cycle identity only; they do not authorize providers, story execution,
runtime activation, or creator-policy decisions.
