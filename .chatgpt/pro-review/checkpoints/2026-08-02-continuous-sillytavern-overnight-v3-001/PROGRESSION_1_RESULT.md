# CERA Continuous SillyTavern Overnight V3 - Progression 1 Result

queue_revision: `0028`
task_id: `continuous-windows-path-budget-and-immutable-snapshot-custody-v3`
status: `completed`
checkpoint_id: `2026-08-02-continuous-sillytavern-overnight-v3-001`
base_git_sha: `13c5c5ae664061a42763dab293bc0b249c44fb7e`
implementation_git_sha: `16e2bab963b8e4e9e7b4c5b67d3df8b267030f1e`
implementation_tree_sha: `a5d5ae7d93d628a9874a900731fea7947d963186`
external_provider_calls: `0`
retry_count: `0`
fallback_count: `0`

## Result

New accepted Planner snapshots use a deterministic compact V2 locator below
`PLANNER_SESSION/ACCEPTED/v2`. The locator contains only fixed-width hash
abbreviations; it is never accepted as authority. The immutable envelope and
typed V2 receipt retain the complete accepted-turn ID and hash, complete
snapshot and encoded-file hashes, accepted-envelope hash, provider-thread
hash, and nested injection receipt.

Python owns a hash-bound 248-character Windows legacy-path policy. It
preflights the mutable-current and immutable final paths and both
same-directory temporary paths before `world.apply_creator_action`. Exact
snapshot paths are revalidated before publication. An over-budget root fails
before world, journal, or session mutation. Exact replay is idempotent;
compact-locator collision, changed bytes, root drift, and symlink custody fail
closed.

Historical V1 receipt and full-hash path decoding remains registered and was
proven from handcrafted historical bytes without rewriting them. Accepted
checkpoint materialization now also preflights the full-SHA receipt and
same-directory temporary path and uses compact staging so its own evidence
cannot recreate the Cycle 22 Windows failure.

## Provider-free verification

- Focused final gate: `70/70 passed` in `11.696` seconds.
- Documentation and repository source-inventory gate: `4/4 passed` in
  `0.218` seconds.
- Compilation of every changed runtime/test module and `git diff --check`:
  passed.
- The long-root tests use a resolved branch root of `133-134` characters,
  equal to or longer than the Cycle 22 branch root.
- The actual inherited scripted-v10 CLI completed all ten local stages with
  accepted-session synchronization, immutable V2 snapshot custody,
  accepted-checkpoint fork, reconstruction, and terminal evidence V5.
- A fresh disposable repository cycle crossed publication, Job 4 completion
  v2, explicit completed-chain validation, and recovery.
- Qualification used the repository `.venv`, which contains `mcp==1.29.0`
  and `openai-codex==0.144.4`. The system Python is not a CERA qualification
  environment because it lacks that pinned provider metadata.
- One read-only PowerShell Git-tree query was malformed by unquoted revision
  syntax; the immediately corrected query returned the tree identity above.
  It was not a product or qualification failure and changed no bytes.

## Cycle 22 non-mutation evidence

The read-only consumed-cycle validator accepted Cycle 22 with current-source
validation disabled, as required for immutable historical recovery. The
Cycle 22 state SHA-256 remained
`e9f70b8c751950b72d744c86a84ac6ced3ff79d8940b6f32cd0d9f62bdffa2f8`
before and after validation.

Exact retained Cycle 22 identities:

- accepted Pro response:
  `2c83c0aeb6c95ea7d5223d16158e11ea0b860cc78369fb48520ec3a4399a0c2a`;
- response-consumption receipt:
  `5ce67e420c47e669ca8a3048165607759c882370735ceec14807b96390c2c615`;
- Job 4 result:
  `2fe8e9cfdfe6d05588314dff1fe2186fe29a5eb196c69c616d01d2df252ed32f`;
- Job 4 report:
  `7f8ccf6254ae339090dc834e6572c666791c6f148c23b3cf36e28083932d0d7c`;
- frozen detail:
  `c0ebac4cd044ec208303518ad3e9ef1005589b0f5b25f9d7aaa2f1ace437dead`;
- terminal evidence:
  `09c9b300318cd47e2d72db1c32ac4468f690212e4b32cb799bf3ed459be8f014`;
- Job 4 completion receipt:
  `f2fb36b66c9ca4469efeb53ebc3123a2bef64659c7edc28ef84713c493d8e51f`.

All result/report/terminal copies agree exactly with the frozen primary
hashes. No Cycle 22 file was staged, committed, rewritten, or recovered into a
new state view.

## Source identities

- `src/cera/continuous/path_policy.py`:
  `a9fc4cf3657b4ed7b382bcf9fe77ac4199bd6b3f4d8e27e045156a2f73a35553`
- `src/cera/continuous/sessions.py`:
  `c8ffedd175e3b66e4bd0ca9654adb8a45f628a9e88ce1f659ff386ecb9ca963e`
- `src/cera/continuous/runtime.py`:
  `d04b260cd304d5b77fde72a736026650d6bd2436ba43b841a0bf3386347be385`
- `src/cera/continuous/world.py`:
  `12374b34c54ce80973c4f524155504a80d64c96bf9620188ea71ce7d5cd9bf42`
- `tests/test_continuous_snapshot_path_custody.py`:
  `5bcb3986a49d9d94cee0c1aa49f59ed8ce2c57a3a1aceeb12cf04533fb1fbc84`
- `tests/test_continuous_job4_harness.py`:
  `1d712a2cd50bc2126128d068000b68fac1b916217e8b704106e2ad78437645e1`
- `tests/test_pro_review_bridge.py`:
  `6d6d61b1330de7fe639b3d4941a37a3e6222a8539a1035f2ba48d9bf064eacc5`

## Effects and next authorized operation

External provider calls, retries, fallbacks, story/database writes, active
route changes, services, installed SillyTavern, deployment, merge, remote,
and push effects were all zero. D-180 remains active.

Continue without waiting to Queue 0028 Progression 2:
`continuous-fresh-v2-executable-and-provider-backed-manual-route-v1`.
