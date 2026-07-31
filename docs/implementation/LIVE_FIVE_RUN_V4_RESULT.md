# Live Five-Run V4 Result

**Status:** terminal qualification evidence; 1/5 structurally passed  
**Qualification ID:** `five-run-live-v4-provider-schema`  
**Evidence:** `evaluation/evidence/live_story_qualification_2026-07-29_v4_provider_schema`  
**Routes:** Sol-medium Reasoner; DeepSeek V4 Pro Composer

## Gate sequence

The provider-free compatibility correction passed 291/291 tests and ChatGPT
Pro returned `CERA_PROVIDER_SCHEMA_COMPATIBILITY_ACCEPTED`. The separately
authorized non-story probe then submitted the complete projected
`CodexReasonerDraftV2` schema to Sol-medium. It passed provider schema
acceptance and authoritative Python decoding with one call, zero retry, zero
story write, and no retained prompt/raw output.

Only after that pass did v4 begin. The controller's five-minute shell timeout
ended immediately after case 4 wrote its terminal evidence. The runner's
guarded `--resume-incomplete` mode validated and preserved cases 1-4, found no
terminal summary or case-5 record, and ran only missing case 5. It did not
repeat a completed case. The final summary truthfully records this recovery
and the four preserved IDs.

## Terminal matrix

| Case | Result | Exact boundary |
|---|---|---|
| Hana indirect memory | Failed | Codex returned `decision_ready` but the typed draft lacked required semantic content |
| Mia + Sakura selected cast | Passed structurally | Both selected NPCs were realized, Yuuni remained excluded, DeepSeek returned accepted in-memory prose |
| Hana Adult ON | Failed | A DeepSeek quote anchor did not resolve to its declared zero-based occurrence |
| Hana Adult EX climax | Failed | AdultCraftNeed scene-channel `required_concepts` contained a duplicate; Python rejected the union |
| Hana Adult EX toilet | Failed | Same duplicate scene-channel concept failure class; Python rejected it independently |

Final summary:

```text
Attempted cases: 5
Structurally passed: 1
Failed: 4
Automatic retries: 0
Fallback: false
Story state committed: false
Story authority writes: 0
Existing cases repeated during recovery: 0
```

## What advanced

- V3's provider-schema handshake defect is corrected. All five Sol calls
  reached model output rather than being rejected for nested `oneOf`.
- The Hana indirect-memory case successfully performed one bounded search and
  one exact fetch under the request snapshot. Retrieval reached the required
  evidence; failure moved to Reasoner semantic draft completeness.
- The selected-cast case completed the full Sol-to-DeepSeek path and excluded
  Yuuni as required.
- Both adult EX failures passed provider schema acceptance and reached the
  unchanged Python AdultCraftNeedV2 validator. The validator correctly refused
  duplicated semantic-set values rather than accepting them for convenience.
- The Adult ON case reached DeepSeek and preserved its safe provider receipt
  after typed failure.

## Material limitations exposed

1. The Reasoner prompt does not teach the complete status-dependent semantic
   matrix. JSON Schema can require fields syntactically while still allowing
   null/empty values that Python correctly rejects for `decision_ready`.
2. OpenAI's supported schema dialect cannot express array uniqueness with
   `uniqueItems`. The prompt currently teaches channel ownership but does not
   explicitly teach uniqueness for every semantic-set array, so two adult
   cases returned duplicate `required_concepts`.
3. DeepSeek still owns zero-based quote occurrence bookkeeping and produced an
   invalid occurrence. Python could safely derive an occurrence when a quote
   is unique and require provider disambiguation only when repeated; this
   ownership should be reevaluated provider-free.
4. The sole structural pass is not protected-user semantic proof. Its prose
   includes unsupplied narration of Ted turning and an observable expression,
   despite the no-new-protected-user-action boundary. The configured
   echo-accepting realization verifier is explicitly non-proving, so the case
   must not be treated as product-quality or promotion evidence.
5. The runtime stage journal preserves privacy-safe prior-stage handles, but
   the qualification JSON exporter records only the exception's latest
   provider/bridge/lookup objects. In the Adult ON Composer failure, the prior
   Reasoner and context receipt handles are therefore absent from the durable
   case file even though those stages necessarily completed. Qualification
   failure evidence remains less complete than the runtime journal contract.
6. The generic `decision-ready draft lacks required semantic content` message
   does not identify which required semantic fields were null/empty. With raw
   output intentionally omitted, the safe diagnostic needs a field-level
   violation code to support efficient provider-free correction.

## Interpretation

V4 proves the provider projection works; it does not qualify the live route.
The 1/5 count is a structural harness result, and even that accepted prose
exposes a protected-user semantic concern that the fake verifier cannot prove
or reject. No candidate was committed, promoted, published, or delivered to
SillyTavern.

## Next authorization gate

The next useful action is a provider-free v4 diagnostic/correction phase. It
should address the failure classes at their owning abstractions: status-matrix
prompt semantics and field-level diagnostics; uniqueness guidance for all
model-authored semantic sets; safer quote-anchor occurrence ownership;
qualification export of all safe prior-stage receipt handles; and a real
protected-user semantic verification design with fake/adversarial tests. It
must not hard-code case IDs, accept invalid output, add retries/fallback, or
weaken any existing boundary. Another live batch, product activation,
production binding, promotion, publication, handler, or deployment remains a
later creator decision.

Suggested exact authorization:

> I authorize Codex to begin a provider-free V4 diagnostic and structural
> correction phase exclusively in `D:\AIChatBot\Cera`. Treat the terminal v4
> cases as diagnostic probes, not IDs to hard-code. Correct shared
> status-dependent Reasoner semantics and field-level safe diagnostics;
> model-authored semantic-set uniqueness guidance and validation;
> quote-anchor occurrence ownership; qualification export of all privacy-safe
> prior-stage receipt handles; and protected-user semantic realization
> verification. You may update provider-neutral DTOs, versioned provider
> projections, prompts, adapters, validators, fake verifier contracts,
> evidence exporters, tests, and controlling documentation where the shared
> root causes require it. Preserve all schema-probe and v1-v4 qualification
> evidence unchanged. Use fake adapters and disposable databases only. Do not
> call live providers, rerun qualification cases, add retry or fallback,
> weaken domain/privacy/evidence/consent/participant/protected-user/branch/
> publication boundaries, commit story state, activate SillyTavern, bind
> production, publish, promote a route, attach a handler, or deploy. Run the
> complete provider-free suite, review the correction with ChatGPT Pro, and
> stop before requesting any later live qualification authority.
