# V4 Provider-Free Structural Correction Result

**Date:** 2026-07-29  
**Authority:** D-124  
**Status:** implementation, provider-free validation, and ChatGPT Pro advisory review complete

## Outcome

The terminal v4 cases were used only as probes of shared contract classes. No
case ID, character, memory ID, or exact failing prose was hard-coded. The
correction made no provider call, qualification rerun, retry, fallback, story
commit, product activation, production binding, publication, promotion,
external-handler change, or deployment action.

The complete provider-free suite passes **298/298** tests in 176.513 seconds.
Compilation and documentation/source-inventory checks are clean.

## Shared corrections

### Reasoner status semantics and safe diagnostics

- The raw provider object is checked against the complete `decision_ready`,
  `insufficient_evidence`, and `blocked` semantic matrix before dataclass
  decoding.
- Failures expose stable `field_path:code` diagnostics only. They never echo
  a field value, source, prompt, private evidence, or raw model output.
- `decision_ready` requires its route, intent, responders, floor,
  participation, moves, current beats, and stop boundary and forbids failure
  state.
- `insufficient_evidence` requires insufficiencies, forbids a blocker, and
  forbids decision content.
- `blocked` requires the supported blocker, forbids insufficiencies, and
  forbids decision content. The authoritative `ReasonerOutcome` enforces the
  same previously intended rule.

### Model-authored semantic sets

- Provider-schema descriptions identify semantic-set arrays as distinct.
- The Reasoner prompt teaches the rule across responders, evidence IDs,
  directives, adult families/subfamilies, beat keys, concepts, axes,
  channel-owner pairs, card owners, and section queries.
- Python remains authoritative and rejects duplicates. No `uniqueItems` keyword
  was added because it is unsupported by the active OpenAI Structured Outputs
  subset.

### Quote-anchor occurrence ownership

- DeepSeek supplies one exact quote that must occur exactly once in its own
  `story_text`.
- The retained `occurrence` field is a literal zero compatibility sentinel;
  Python rejects any other value, rejects an absent or ambiguous quote, and
  derives offsets and effective occurrence itself.
- The prompt tells the Composer to extend a repeated quote with nearby prose
  until it becomes unique.

### Safe receipt-chain export

- Composer failure bundles retain the successful Reasoner receipt, Reasoner
  provider receipt, optional MCP bridge receipt, all Reasoner/context lookup
  receipts, context-assembly receipt, and failing Composer provider receipt.
- Realization failure bundles additionally retain successful Composer and
  validation receipts and, when available, the rejected/inconclusive verifier
  receipt.
- The qualification exporter serializes the privacy-safe stage bundle and all
  retained handles before a disposable database can be destroyed.
- Restart inspection can enumerate the same request-bound failure bundles.

### Protected-user semantic realization

- Verification request v2 includes hash-bound exact protected-user source
  authority, allowed realization kinds, and a Python-required
  `protected_user_no_unsupplied_realization` check.
- Accepted verification must return the exact required semantic-boundary set
  in addition to all beats and participants.
- A protected-user semantic rejection must carry an anchored finding whose
  offsets and text hash are checked against candidate prose.
- Durable receipts retain finding hashes and safe codes, never prose.
- Echo/scripted verifiers remain production-prohibited and are explicitly not
  qualification-eligible. The live qualification runner fails before provider
  dispatch until a separately authorized qualification-eligible semantic
  verifier adapter exists.

## Provider-free validation matrix

The seven new tests cover:

1. mutation of every required decision-ready field;
2. precise duplicate AdultCraftNeed channel-concept diagnostics;
3. provider uniqueness guidance without unsupported keywords;
4. Python-owned unique-quote resolution and ambiguous/nonzero rejection;
5. full prior-stage receipt export and restart recovery after Composer failure;
6. rejection of verifier acceptance that omits the protected-user boundary;
7. anchored protected-user semantic rejection with a safe verifier receipt.

The complete suite also re-exercised real Genesis, retrieval/privacy, branches,
regeneration, restarts, Adult ON/EX, schema projection, malformed output,
transactions, the 20-run acceptance matrix, and source inventory.

## Preserved immutable evidence

No file was written beneath the schema-probe or v1-v4 qualification evidence
directories. Their post-correction read-only aggregate inventories are:

| Evidence directory | Files | Aggregate SHA-256 |
|---|---:|---|
| `live_story_qualification_2026-07-28_v1` | 12 | `53354856da0dea168729e894b5033395680c22b792d2e47ce1295ce85ba5eec1` |
| `live_story_qualification_2026-07-29_v2` | 6 | `00e5ef73ba4b0256065dbc5d7608b6d2701a2c88c66eec3e7b4a151a0b39578b` |
| `live_story_qualification_2026-07-29_v3_structural_contract_v2` | 6 | `c4326756db4b44ac7dcffb9df32d9d477b05c26ab6e64bea0cc272b4bcbeda4f` |
| `live_story_qualification_2026-07-29_v4_provider_schema` | 6 | `e6a1606c53d0b023a6708674b102e5ba413bb3707d3fbbc266d7f426b322ae94` |
| `codex_schema_acceptance_2026-07-29_v1` | 1 | `3abd29b642e44aebd55368cd523ffae0019d89f24b2c5661635ea881e876d317` |

The aggregate is SHA-256 over sorted `relative_path|file_sha256` lines. It is
an audit inventory, not a replacement for the immutable files themselves.

## Remaining limitation and next gate

This phase proves contracts, failure behavior, and fake-adapter orchestration.
It does not prove that a live verifier detects arbitrary paraphrased
protected-user action, or that Sol/DeepSeek now produce acceptable story-role
outputs. A later live batch must first name and authorize a
qualification-eligible semantic realization verifier. No later live
qualification is authorized by D-124.

## ChatGPT Pro advisory review

ChatGPT Pro returned:

```text
CERA_V4_PROVIDER_FREE_STRUCTURAL_CORRECTION_ACCEPTED
```

Pro found no required correction. It agreed that the status matrix addresses
the shared v4 class at its contract owner, semantic-set uniqueness is correctly
split between model guidance and Python enforcement, quote occurrence belongs
to Python, the privacy-safe receipt chain is structurally sufficient, and the
protected-user verifier is independent rather than Composer self-attestation.

Codex independently accepts that advisory verdict. It matches the actual
implementation and the 298-test evidence. In particular, the verifier gate is
a `qualification_verifier_required` condition at the future live-qualification
dispatch boundary; it is not a universal production rule and does not replace
conditional high-risk verification. Receipt-chain completeness proves
traceability and failure attribution, not semantic correctness or provider
qualification.

Pro's optional suggestions to bound quote-anchor length and explicitly version
the diagnostic-token vocabulary are sensible future hardening, but they are
not required to close D-124. Quote anchors already reject empty/whitespace-only
values, providers do not own offsets, and no later live activity is authorized
by this review.
