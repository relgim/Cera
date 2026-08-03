# Continuous No-Follow Path Custody V4

## Outcome

Queue 0033 Progression 2 replaces resolve-then-check authority on continuous
accepted snapshots and accepted-checkpoint materialization with lexical,
component-by-component no-follow custody. The compact
`PLANNER_SESSION/ACCEPTED/v2` locator and repository-owned 248-character
Windows legacy-path budget remain unchanged. New writes use additive typed
plans and receipts; historical snapshot receipt V1/V2 and branch
materialization receipt V1 remain readable without rewriting their bytes.

## Windows protocol

Windows does not expose one `openat(..., O_NOFOLLOW)` primitive spanning
directory creation, file creation, writing, and replacement. CERA therefore
uses a bounded fail-closed repository protocol:

1. Construct the target lexically beneath an absolute trusted root. Never use
   `Path.resolve()` to establish custody.
2. Open every existing component with
   `CreateFileW(FILE_FLAG_OPEN_REPARSE_POINT)` and obtain attributes, reparse
   tag, volume serial, file index, and link count through handle-based file
   information APIs.
3. Reject every symbolic link, junction, mount-point reparse record, other
   reparse point, unsupported component, and multiply linked file.
4. Open the trusted root and parent chain without `FILE_SHARE_DELETE` while a
   publication is active. This pins those directory identities against
   replacement on Windows.
5. Create same-directory temporary files exclusively, write and flush them,
   revalidate the complete parent plan and final/temp identities, atomically
   replace, and require the promoted final to retain the exact temporary-file
   identity.
6. Revalidate final identities and temporary absence before emitting a typed
   receipt. Any unavailable identity primitive or race fails closed; there is
   no resolve/is-symlink fallback.

POSIX uses `lstat`, directory descriptors, and `O_NOFOLLOW` where available,
with device/inode identity and explicit nested mount rejection.

## Typed authority

`cera.continuous_accepted_snapshot_path_plan.v2` binds the trusted branch-root
identity, verified current and immutable parent identities, all four lexical
final/temp paths, component manifests, verification mode, path-policy hash,
248-character length plan, and deterministic race generation.

`cera.continuous_session_snapshot_receipt.v3` retains all V2 accepted-turn,
envelope, thread, injection, inner snapshot, outer file, and compact-locator
authority. It adds the V2 path plan, exact immutable/current filesystem object
identities, and a post-publication generation. Exact replay is permitted only
when bytes and verified identities are unchanged.

`cera.continuous_branch_materialization_path_plan.v1` applies the same custody
to parent, child, exclusive sibling staging, full-SHA receipt, and receipt-temp
paths. `cera.continuous_branch_materialization_receipt.v2` adds that plan to
the complete V1 semantic, ACTIVE-manifest, accepted-checkpoint, branch-cutoff,
policy, and summary-source authority. The receipt locator uses the complete
branch-cutoff SHA-256 to avoid a receipt self-hash cycle.

## Adversarial coverage

Provider-free tests exercise ordinary and long-root save/load/restart,
reconstruction, branch fork, exact replay, compact collision, changed bytes,
occupied final/temp paths, file symlink capability, in-root and outside-root
hard-link aliases, parent alias insertion after preflight, parent replacement
during publication, accepted-checkpoint source aliases, and staging
substitution before promotion.

On the Windows qualification host, `mklink /J` is exercised directly for both
in-root and outside-root parent aliases, accepted-source aliases, and staging
substitution. The handle inspection rejects the junction reparse tag. File
symlink cases may be skipped only with an explicit capability receipt when the
host lacks unprivileged file-symlink creation; that skip is not used as the
required Windows reparse evidence.

## Preserved boundary

The implementation is provider-free and additive. It does not change D-180,
provider models, prompts, Composer or Validator behavior, story authority,
the persistent story database, installed SillyTavern, services, deployment,
remotes, or branches. Progression 3 and Cycle 24 remain separate governed
work.
