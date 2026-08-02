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
Only generated root runtime state, current-cycle transport state, and
`.chatgpt/operations/` connector metadata have fixed typed exclusions.

Each progression Markdown file must itself declare the exact bound `task_id`
and one final `status: completed | blocked`; the spec cannot relabel a stale
hash-valid result. Every v2 predecessor is accepted only after its immutable
outbox, source archive, publication/start/optional-trigger/completion/
consumption receipt chain, structured Job 4 result and report, and accepted
response identity are revalidated. Legacy cycle 002 is the single explicit
compatibility boundary.

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
v2 candidates from immutable evidence and the receipt-bound accepted response,
skips invalid higher-sequence candidates with an explicit diagnostic, and
returns only the newest valid response. Post-consumption inbox state is
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

Job 4 completion uses `cera.pro_review_job4_result.v1`. Provider/story/database,
route, and deployment/remote effects are preserved as structured declarations
from that stable result and labeled with their source; the transport never
manufactures zero-effect facts.

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
