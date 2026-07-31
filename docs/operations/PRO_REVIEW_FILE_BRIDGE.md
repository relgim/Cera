# CERA ChatGPT Pro Review File Bridge

**Status:** creator-authorized provider-free transport
**Owner:** deterministic local PowerShell tooling
**Authority:** transport only; imported recommendations remain advisory

Use this bridge when desktop Codex and ChatGPT Pro cannot directly exchange
repository files. It copies one hash-verified evidence ZIP through Ted's
Downloads folder and imports one identity-bound Markdown response without
changing its bytes.

## Commands

From `D:\AIChatBot\Cera`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File ".\tools\pro_review_bridge.ps1" -Mode Export `
    -CheckpointDirectory "D:\AIChatBot\Cera\.chatgpt\pro-review\checkpoints\2026-07-31-checkpoint-001"

powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File ".\tools\pro_review_bridge.ps1" -Mode Status `
    -CheckpointDirectory "D:\AIChatBot\Cera\.chatgpt\pro-review\checkpoints\2026-07-31-checkpoint-001"

powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File ".\tools\pro_review_bridge.ps1" -Mode Wait `
    -CheckpointDirectory "D:\AIChatBot\Cera\.chatgpt\pro-review\checkpoints\2026-07-31-checkpoint-001" `
    -PollSeconds 30

powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File ".\tools\pro_review_bridge.ps1" -Mode Import `
    -CheckpointDirectory "D:\AIChatBot\Cera\.chatgpt\pro-review\checkpoints\2026-07-31-checkpoint-001"
```

The process-local bypass is required by Ted's current Windows execution policy;
it does not change the machine-wide policy.

`Wait` polls only the exact response filename in Downloads. `-MaxPolls` is a
test/operator escape hatch; zero means stationary indefinite waiting. The
bridge has no network, provider, browser, database, story, route, promotion, or
deployment capability.

## Export contract

Export reads `REQUEST.md`, `EVIDENCE_PACKAGE/INDEX.md`, the evidence ZIP/hash,
and optional `TASK4_RESULT.md`. It verifies the ZIP before and after copying,
uses a deterministic filename, writes an upload message, and records
`BRIDGE_EXPORT_RECEIPT.json`. A conflicting existing Downloads ZIP fails closed.

## Import contract

The downloaded response filename and its near-beginning identity block must
match the export receipt exactly:

```yaml
reviewed_checkpoint_id: <checkpoint directory leaf>
reviewed_checkpoint_sha: <frozen Git object ID>
reviewed_evidence_zip_sha256: <exported ZIP SHA-256>
review_scope: evidence_verified
```

Import rejects missing, unstable, incomplete, placeholder, mismatched, or
conflicting responses. It copies bytes atomically to
`PRO_RESPONSE_EVIDENCE_VERIFIED.md`, preserves `PRO_RESPONSE.md`, leaves the
Downloads file in place, and writes a privacy-safe hash/path receipt. An
identical repeat is idempotent; a different existing destination requires Ted.

## Authority and review-cycle rules

An exported package does not authorize Pro. An imported review does not
authorize Codex. After import, Codex reconciles any previously authorized
isolated bridge progression and stops for Ted's explicit decision. No fourth
task may be invented while waiting.
