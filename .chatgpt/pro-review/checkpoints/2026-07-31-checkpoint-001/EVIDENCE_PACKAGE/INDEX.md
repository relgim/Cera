# CERA Checkpoint 001 Evidence Export

This export repairs the evidence-access gap identified in `PRO_RESPONSE.md`.
It is an operational review artifact for the frozen checkpoint:

- baseline: `fb3eb586f0b68fb65ea5bea5eb93a93d4f83cfd4`
- checkpoint: `248dfbc969a2961338d8f9b35c61bda4f4e6010b`
- active profile: `cera.active_runtime.d180.v1`
- active profile SHA-256: `f74347604d9adb7be0ded2a3c9c62e7076aca4d5d773aa8a3c97f8e7bcee0801`

## Contents

- `request/`: the submitted review request and synchronized advisory response.
- `git/`: exact baseline-to-checkpoint patch, name/status, stats, commit metadata,
  and worktree status.
- `source/`: a ZIP containing every changed tracked file exactly as stored at
  the checkpoint SHA.
- `profile/`: canonical profile output and profile validation evidence.
- `runtime/`: current loopback health, route definitions, session status, and a
  privacy-safe historical receipt sample. No provider was called to create it.
- `tests/`: focused and repository-wide provider-free test output, plus the
  earlier failed-run history disclosed by the checkpoint request.
- `baseline/`: backup, inventory, ignore-rule, and repository-source evidence.
- `effects/`: provider, database, story, branch, and historical-evidence effects.
- `review/`: Codex's independent assessment of the advisory findings.
- `MANIFEST.sha256`: per-file SHA-256 manifest generated after verification.

## Scope

This export makes no implementation change, performs no provider call, and
writes no story or database state. Generated evidence files remain outside the
frozen checkpoint commit. The exact checkpoint SHA is not amended.

