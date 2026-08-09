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
