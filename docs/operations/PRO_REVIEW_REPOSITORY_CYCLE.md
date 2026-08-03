# CERA Repository-Local Overlapped Pro Review Cycle

**Status:** primary creator-authorized review transport
**Owner:** Codex plus deterministic local Python tooling
**Authority:** transport and review state only; ChatGPT Pro remains advisory

The primary review workflow uses the shared CERA repository. Ted gives the
initial bounded authorization and does not upload, download, rename, copy,
start `Wait`/`Import`, or relay ordinary cycle messages.

```text
Codex completes current/revised Jobs 1-3
-> publishes one immutable repository package
-> activates the existing ChatGPT Pro review chat through the supported app tool
-> immediately performs the exact pre-authorized Job 4
-> records Job 4 completion
-> consumes only the matching repository response
-> reconciles that advisory review inside existing creator authority
-> prepares the next Jobs 1-3 package with the preceding Job 4 result
```

`tools/pro_review_cycle.py` is the single entry point;
`tools/pro_review_cycle_core.py` owns the repository mailbox and state machine.
`tools/pro_review_bridge.ps1` remains functional only as a manual emergency
fallback when the app trigger or shared-repository connector is unavailable.

## Deterministic cycle tree

```text
.chatgpt/pro-review/cycles/<cycle-id>/
  CYCLE_SPEC.json                  # authored input, retained by the checkpoint
  source/
    JOB4_AUTHORIZATION.json        # structured pre-publication authority
    JOB4_RESULT.json               # structured completion/effect declaration
    JOB4_REPORT.md
  CYCLE_MANIFEST.json              # immutable validated identity
  outbox/
    REVIEW_REQUEST.md
    PRO_RESPONSE_TEMPLATE.md
    TRIGGER_MESSAGE.txt
    CHECKPOINT_EVIDENCE.zip
    JOB_1_RESULT.md                 # one through three current progressions
    PRIOR_JOB4_RESULT.*             # receipt-bound, required after bootstrap
    JOB4_AUTHORIZATION.json
    CHANGED_SOURCE_MANIFEST.json
    SOURCE_SNAPSHOT.zip
  inbox/
    PRO_RESPONSE.md                 # exact connector write target
  artifacts/
    JOB4_RESULT.json                # current Job 4, used by the next cycle
    JOB4_REPORT.md
  accepted/
    PRO_RESPONSE.md                 # exact stable bytes after validation
  receipts/
    PUBLISHED.json
    JOB4_STARTED.json
    TRIGGER_SENT.json
    JOB4_COMPLETED.json
    RESPONSE_CONSUMED.json
    rejections/*.json
  state/
    CYCLE_STATE.json
```

ChatGPT Pro writes only `inbox/PRO_RESPONSE.md`, using an atomic connector
write. It must not edit the manifest, outbox, receipts, state, accepted response,
or Job 4 result.

## State contract

```text
publish
  -> job4_in_progress
complete-job4
  -> job4_complete_response_pending
consume / wait-consume
  -> review_consumed_advisory
```

`publish` succeeds only when one through three result hashes, an exact existing
40- or 64-character CERA Git object, a valid bounded evidence ZIP, the preceding
cycle completion/consumption chain (after bootstrap), and the structured new
Job 4 authorization record are valid. Publication and
`JOB4_STARTED.json` bind the same exact task set. The tool has no operation that
creates an unnamed task.

Every source path resolves inside the trusted CERA Git root and rejects
symlinks/junctions, traversal, protected root state (`.git`, `.venv`, `.tmp`),
credential/secret areas, databases, and key material. Root `runtime/` is typed
generated state and is excluded; tracked source such as `src/cera/runtime/**`
is reviewable. Publication automatically inventories every nonexcluded Git
change with its exact porcelain status, old/new rename paths, deletion
tombstones, and current bytes. Present bytes go into a deterministic ZIP; the
complete status-aware manifest and archive root are bound into the task set.
The snapshot fails closed above fixed aggregate file-count and uncompressed-byte
ceilings as well as the per-artifact ceiling.
Only generated root runtime state, current-cycle transport state,
repository-global sequence-authority transport state, and
`.chatgpt/operations/` connector metadata have fixed typed exclusions. V4
binds the excluded sequence-authority bytes independently in its task set,
manifest, outbox copies, disposition, and start receipt.

Each progression Markdown file must itself declare the exact bound `task_id`
and one final `status: completed | blocked`; the spec cannot relabel a stale
hash-valid result. Every modern predecessor is accepted only after its immutable
outbox, source archive, publication/start/optional-trigger/completion/
consumption receipt chain, structured Job 4 result and report, and accepted
response identity are revalidated. Legacy cycle 002 is the single explicit
compatibility boundary.

Spec/manifest V3 retains that consumed predecessor and adds
`failed_pre_manifest_predecessors`. For consumed sequence `P` and current
sequence `C`, the list must prove exactly `P+1` through `C-1`, in order, with no
duplicates or omissions. Every entry binds an exact source-local copy of the
authoritative failure receipt plus a canonical
`cera.pro_review_failed_pre_manifest_tombstone.v1`. Pre-publication validates
the original receipt, attempted spec/authorization when present, zero effects,
the failed directory inventory, and absence of publication/completion/
consumption artifacts. Publication places the exact receipt and tombstone bytes
in the immutable outbox; completion and recovery validate those published
copies. A tombstone provides sequence custody only and never substitutes for a
consumed predecessor, accepted response, Job 4 result, or execution authority.

Spec/manifest V4 activates repository-global sequence ownership at sequence
29. The fixed namespace is
`.chatgpt/pro-review/sequence-authority/ACTIVATION.json` and one
`000000NN/CLAIM.json` plus `DISPOSITION.json` pair per sequence. Activation
adopts exactly consumed sequences 25 and 28 and Cycle 28's already-published
failed-pre-manifest custody for 26 and 27. Historical adoption is derived from
validated consumed-cycle receipts, manifests, accepted responses, and Cycle
28 outbox tombstones; mutable failed directories and successor staging are not
historical authority.

Claims use canonical deterministic bytes and exclusive non-replacing writes.
An exact retry is idempotent; a different claimant loses without overwrite.
Claims form a predecessor-hash chain and cannot be deleted, reassigned, reused,
or replaced. Before and after manifest creation V4 scans both manifests and
claims for conflicting occupancy. After activation, V1-V3 cannot publish at or
above the frontier. A successful V4 publication records a global `published`
disposition binding its manifest root and `PUBLISHED.json` hash before
`JOB4_STARTED.json`; the start receipt binds both claim and disposition. A
pre-manifest failure may instead receive the terminal failed disposition, which
retires that sequence without granting Job 4 or execution authority.

`complete-job4` stable-reads the exact expected result and preserves its bytes
and hash. `consume` is unavailable before that transition. It stable-reads only
`inbox/PRO_RESPONSE.md` and validates:

- review cycle ID;
- checkpoint ID and Git object ID;
- evidence SHA-256;
- complete task-set SHA-256;
- named Job 4 task ID;
- deterministic response nonce;
- `review_scope: repository_cycle`;
- a final review disposition.

Wrong, placeholder, bodyless, partial, unstable, stale, duplicate-conflicting,
or identity-mismatched
responses leave progression state unchanged and create content-free rejection
receipts. An identical consumed response is idempotent; conflicting immutable
writes fail closed. Manifest and receipt root/predecessor hashes form the
transition chain. Each chained receipt has an exact event-specific field set
and value contract; a partial publication outbox map is invalid. Every later
command revalidates the frozen outbox and status-aware source archive. Current
source identity remains mandatory while a cycle is active or unconsumed.

After a valid `RESPONSE_CONSUMED.json` exists, response authority moves
exclusively to `accepted/PRO_RESPONSE.md` as bound by that receipt. The staging
`inbox/PRO_RESPONSE.md` may later be absent, identical, or conflicting without
changing accepted authority, disposition, predecessor identity, or the receipt
chain. A conflicting inbox still cannot be consumed over accepted bytes.
Status and latest-consumed output may expose the inbox state and hash, but mark
that information explicitly non-authoritative.

`recover` reconstructs an active cycle only after validating current source.
For a fully consumed historical cycle it instead validates the immutable
published archive, manifest, Job 4 artifacts, completion chain, accepted
response, and consumption receipt with `require_current_source=False`. An
already-valid mutable state view is returned byte-for-byte; only a missing or
invalid mutable view is rebuilt. Later repository development therefore cannot
invalidate or silently mutate a completed historical cycle.

`latest-consumed` does not trust a mutable state label. It fully reconstructs
modern V2/V3/V4 candidates from immutable evidence and the receipt-bound accepted response,
skips invalid higher-sequence candidates with an explicit diagnostic, and
returns only the newest valid response. More than one valid candidate at the
highest sequence is an explicit conflict; directory-name ordering is never a
tiebreaker. Post-consumption inbox state is
diagnostic only.
By contrast, `status` is deliberately labeled
`state_view_validation: not_performed_status_only`; use `recover` for validated
state reconstruction.

Package publication is individually atomic with `PUBLISHED.json` as the commit
marker; it is not described as one filesystem-wide atomic directory rename.
Conflicting concurrent immutable writes fail without overwriting. Exhausted
waits receive sequence-named append-only receipts.

## Primary commands

Codex creates `CYCLE_SPEC.json` from the authorized task and runs this single
entry point to start the next cycle:

```powershell
python .\tools\pro_review_cycle.py publish `
  --cycle-directory "D:\AIChatBot\Cera\.chatgpt\pro-review\cycles\<cycle-id>" `
  --spec "D:\AIChatBot\Cera\.chatgpt\pro-review\cycles\<cycle-id>\CYCLE_SPEC.json"
```

The remaining noninteractive lifecycle commands are:

```powershell
python .\tools\pro_review_cycle.py complete-job4 --cycle-directory "<cycle-path>"
python .\tools\pro_review_cycle.py wait-consume --cycle-directory "<cycle-path>" --max-polls 20 --poll-seconds 15
python .\tools\pro_review_cycle.py recover --cycle-directory "<cycle-path>"
python .\tools\pro_review_cycle.py status --cycle-directory "<cycle-path>"
python .\tools\pro_review_cycle.py latest-consumed
python .\tools\pro_review_cycle.py activate-sequence-authority
python .\tools\pro_review_cycle.py acquire-sequence-claim --claim "<claim-json>"
python .\tools\pro_review_cycle.py record-sequence-disposition --disposition "<disposition-json>"
```

The app-result trigger receipt must be recorded while state is exactly
`job4_in_progress`; it cannot be inserted after Job 4 completion. The bounded
wait observes only the exact repository response. If it expires,
state stays response-pending. Codex may wait again or perform another separately
named and pre-authorized independent task; it may not invent work or ask Ted to
operate the bridge.

Recording a trigger updates the mutable in-progress view to the validated
`TRIGGER_SENT.json` receipt. `recover` reconstructs the same view after a
restart, and `complete-job4` derives its expected predecessor from the immutable
receipt chain: `JOB4_STARTED.json` without a trigger or `TRIGGER_SENT.json` with
one. Re-recording the exact same attestation is idempotent; a different target,
message, or app-result hash conflicts and cannot overwrite the first receipt.
If mutable state is missing after a restart, use `recover`; do not use
`publish` as a substitute for missing-state recovery.

## Trigger boundary

Repository publication alone does not wake an inactive ChatGPT conversation.
The installed Codex app supplies a supported `send_message_to_thread` operation
for an existing ChatGPT chat. Codex sends the exact generated
`TRIGGER_MESSAGE.txt`, then `record-trigger` validates its hash and the exact
successful app-return JSON. The receipt retains only message, target, and app
result hashes.

This trigger is an app capability, not a filesystem property and not part of
the Python script. A cycle may be described as no-user-action only when the
actual app send succeeds and `TRIGGER_SENT.json` records its caller-supplied app
result attestation. The receipt is not independent delivery proof; the app task
audit plus the matching Pro repository response provides the end-to-end proof
for a tested cycle. If the chat is
missing, unauthenticated, or the app operation is unavailable, the repository
handoff remains valid but automatic activation is unresolved for that cycle.
Do not claim end-to-end autonomy, silently substitute another reviewer, or use
browser automation. The Downloads bridge may be used only after an explicit
creator emergency-fallback choice.

## Authority boundary

The accepted response is immutable advisory evidence. It does not approve its
own recommendations, change runtime/story/database state, or authorize a later
tranche. Codex may apply corrections only when they fit the creator's already
granted scope. A material expansion, new live-call ceiling, provider change,
deployment, destructive action, or other creator decision still stops for Ted.

Historical Job 4 completion decodes `cera.pro_review_job4_result.v1` and v2.
New exact-diagnostic audits use additive
`cera.pro_review_job4_result.v3`. Provider/story/database, route, and
deployment/remote effects remain structured declarations from the stable
result and are labeled with their source; the transport never manufactures
zero-effect facts.

Every Job 4 producer, including a provider-free scripted harness, must first
project its terminal detail into the exact canonical result shape and pass the
same strict decoder used by `complete-job4`. The canonical result does not
duplicate `authorization_sha256`; authorization is already bound by the
manifest, task set, and receipt chain. Scripted transport invocation counts are
diagnostic execution detail, so they remain in the detailed result, report, or
privacy-safe verification summary rather than the canonical `effects` object.
Unknown fields fail before publication. Completed and failed live-shaped and
scripted results are tested through both the decoder and the real
`complete-job4` transition.

Result v3 embeds the closed `cera.continuous_job4_test_diagnostics.v1`
artifact. Its independently bound selected-test identity sequence must match
the exact ordered terminal records. Every record has a self-hash; the result,
terminal-evidence v6 wrapper, publication bytes, `job4_completed_v3` receipt,
completed-chain validator, and recovery validator bind the aggregate and
ordered-record roots. A missing, malformed, duplicated, reordered,
substituted, or hash-drifted record fails closed. Failure messages are retained
only as SHA-256 values with bounded repository-relative source frames; raw
provider output, story prose, private character values, secrets, absolute
paths, and unbounded tracebacks are prohibited.

Every continuous-canary Job 4 producer must also bind
`cera.continuous_job4_terminal_evidence.v1` and its SHA-256 into detailed
evidence before generating the canonical result or report. That closed record
owns the exact provider-call ledger, operational counters, source/disposable
database identities and checks, active-profile before/after identities, thread
archival, accepted-session synchronization, accepted-sequence injection, and
call-ledger reconciliation. The projector derives all four canonical effects
from this record without defaults. Missing, malformed, contradictory, or
unverified evidence fails closed. Any mandatory postcondition failure forces a
top-level failed result, including active-profile inspection failure, which may
never be represented as zero route changes.
