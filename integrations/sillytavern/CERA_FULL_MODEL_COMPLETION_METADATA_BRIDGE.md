# CERA full-model completion metadata bridge

This is the repository-owned contract for the narrow CERA dispatch block in
SillyTavern's `public/scripts/openai.js`. It is intentionally separate from
the installed SillyTavern tree so a provider-free source freeze does not
silently mutate the creator's installation.

The existing provisional-only block must be replaced, at an explicitly
authorized installation-sync boundary, with:

```javascript
if (data?.cera && typeof window.ceraCaptureCompletionMetadata === 'function') {
    window.ceraCaptureCompletionMetadata(data.cera);
}
```

The same authorized sync must add a transport-failure capture call immediately
before SillyTavern handles an error body:

```javascript
if (data.error) {
    if (typeof window.ceraCaptureTransportFailure === 'function') {
        window.ceraCaptureTransportFailure(data);
    }
    // Existing SillyTavern error handling continues here.
}
```

SillyTavern's Custom backend normally discards upstream error bodies. For CERA
models only, the server bridge must therefore recognize the complete
`cera.error.v1` zero-effect transport-failure proof and forward a closed
projection with its original HTTP status. The projection contains only the
fixed public error code and message, request ID, exact effect booleans, and the
seven-field `cera.pi_scene.transport_retry.v1` object. It must not forward or
log the raw error body, trace, debug path, provider fragment, prompt, or prose.
All unrelated errors retain SillyTavern's existing behavior.

That same sync must copy `index.js`, `completion-metadata.js`,
`creator-trace-panel.js`, `review-actions.js`, `style.css`, and `manifest.json`
from the repository-owned creator-review extension directory. The small
modules keep backend-data projection, typed review outcomes, and DOM
presentation separate.

The bridge must not clone, log, reshape, or persist the raw `data.cera`
object. The creator-review extension owns a closed, bounded projection before
it queues or stores anything. This is important for adult completions: exact
protected prose, exact quotes, raw prompts, full adult records, and private
provider artifacts are not UI metadata.

The extension accepts both automatically accepted and provisional CERA
completions. A rejected candidate receives durable buttons only when the
backend supplies a review ID matching `review-[a-f0-9]{28}`. The client never
derives or repairs a review ID.

`Retry transport` is exposed only when the closed error projection has
`retry_transport_enabled === true`, `retry_mode === "manual_transport"`, a
valid backend-issued request ID, and an exact eligible retry object whose URL
matches its stable retry ID. One click posts the exact empty JSON object to the
same-origin review relay. There is no automatic retry, fallback, result merge,
or client-derived retry identity. Before that click, the extension persists a
closed receipt containing only the current chat key, request/retry identifiers,
effect proof, action fields, and lifecycle phase. It stores no prompt, response
prose, raw error, provider detail, or debug path. The current chat's normal send
controls remain disabled until the receipt reaches a terminal state; switching
chats neither leaks nor discards another chat's receipt.

After a POST transport failure or exact relay `cera_loopback_unavailable` 502,
the outcome is unknown. The client never interprets that as a terminal failure
and never posts the action again. It reconciles the same retry ID with an
authenticated, provider-free and idempotent GET. That GET never dispatches a
model operation, though the CERA backend may use it to repair derived local
custody projections. Only the exact
`cera.pi_scene.transport_retry_status.v1` states `eligible`, `in_progress`,
`succeeded`, `superseded`, and `blocked` are accepted. `eligible` and
`superseded` may expose one backend-issued manual action; every other state has
no provider-dispatch button. A `succeeded` completion is usable only when its
nested CERA request ID exactly matches the durable receipt/status request ID;
an identity mismatch remains unresolved and never appends story output.

The exact authenticated not-found envelope may contain a null or bounded local
debug-log path. The relay validates that field as part of the closed envelope
but always projects it away, together with the trace and all other local
diagnostic details.

A successful retry is inserted as one normal completion. Its stable retry,
request, and completion identities are saved beside the assistant message. The
receipt is retained until the chat save succeeds, and replayed status is
deduplicated before any message push. Another proven zero-effect transport
failure may supply a new manual retry ID. Ambiguous, pending, generic, or
accepted-effect failures never receive the button.

The relay applies the same closed projection to an error returned by the retry
itself. A normal OpenAI-compatible completion passes through unchanged. An
ineligible or ambiguous error is reduced to a fixed no-retry envelope, so raw
debug paths and provider details do not become browser data.

`Accept as Provisional` is exposed only when the fetched review payload sets
`provisional_accept_enabled` to the Boolean value `true`. An adult
`reprojection_required` result remains visibly provisional and explicitly
states that no story state was accepted or committed. It never reuses the
ordinary committed-provisional path.

The backend's optional `cera.creator_trace` projection has this safe shape:

```text
logic_owner
decision_records[]                 concise DecisionRecord/AdultDecisionStep values
autonomy                           mode and applied precedence summaries
route_transition                   ordinary/adult handoff and next route
validation                         Luna or Adult Filter result, without exact quote
recording                          Recorder/projection/protected-record state
provisional_dependencies[]         IDs, assumed values, concise dependency
provider_operations                non-negative counts by role
debug_log_path                     creator-readable local path
```

All fields are optional so stored historical messages remain readable. The
extension derives only display fallbacks from already returned safe fields;
it does not infer narrative meaning or authority.
