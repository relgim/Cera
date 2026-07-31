# Live Five-Case V5 Sol-Verifier Result

**Date:** 2026-07-29  
**Authority:** D-131  
**Evidence:** `evaluation/evidence/live_story_qualification_2026-07-29_v5_sol_verifier`  
**Status:** terminal at 1/5 structurally passed; ChatGPT Pro advisory review and independent Codex evaluation complete

## Outcome

The fresh v5 batch ran each case exactly once with new request, generation,
qualification, and evidence identities. The live stack was Sol-medium Scene
Reasoner, DeepSeek V4 Pro Scene Composer, and the independently invoked
Sol-medium Scene Realization Verifier. No retry, fallback, resume, story commit,
production binding, publication, promotion, SillyTavern activation, external
handler, or deployment occurred.

The batch is terminal at **1/5 passed** in 493.871 seconds.

| Case | Result | Terminal stage | Evidence |
|---|---|---|---|
| Hana indirect missed-calls memory | Passed | complete live-shaped validation | 3 provider calls; Reasoner made 2 evidence-tool calls and cited 2 hard records; verifier accepted |
| Mia + Sakura selected cast | Failed | scene realization verification | `protected_user_unsupplied_realization` |
| Hana Adult ON | Failed | Composer typed decoding | provider quote anchor did not occur in `story_text` |
| Hana Adult EX climax | Failed | adult craft selection | catalog could not cover `lexicon:aftermath`, `lexicon:buildup`, or `lexicon:material_continuity` |
| Hana Adult EX toilet | Failed | adult craft selection | catalog could not cover `lexicon:buildup` or `lexicon:material_continuity` |

Every case records zero retry, zero fallback, zero story-authority writes, and no
story commit. The one passing case took 86.323 seconds. Its DeepSeek call took
11.564 seconds and its independent verifier call took 9.921 seconds.

## Immutable evidence inventory

The terminal directory contains six files. Its aggregate SHA-256 over sorted
`relative_path|file_sha256` lines is:

`f4e835fe5b3cbadafd4457aa901acafc3939b1395bcb90489cd24714cdf5ef65`

| File | SHA-256 |
|---|---|
| `01_hana_indirect_memory.json` | `2ed14935897598412f11d8bf5b8eff42d73284682716114e85e8277a0be91896` |
| `02_mia_sakura_selected_cast.json` | `505f50665de5fc7f56981fc6046fb308af36bdd60b470677e72e50def7a3104a` |
| `03_hana_adult_on.json` | `9084fc8e2ca9740e4781e075e2589b09e42cd2d64c892d7fc83cb8d2f89c53ba` |
| `04_hana_adult_ex_climax.json` | `920e089f4e6218adca18bc56670987ba07ae6fc26d3f7fc9e1e1476adfc792e3` |
| `05_hana_adult_ex_toilet.json` | `68f3672f49134ed87faa13b25db99bf1fc7811c3aec7d81835fb1e02f884b104` |
| `summary.json` | `ca190057bfb8cf47949737bdc2347cbe1bd55083cb6ee8516355f8d1a40bfce2` |

## Structural diagnosis

### Passing indirect-memory path

Case 1 is useful positive evidence. The Reasoner performed the required bounded
lookup, cited the owner-authorized missed-calls evidence, produced a
decision-ready outcome, selected Hana only, DeepSeek returned typed composition,
and the live verifier accepted the complete candidate. This establishes one
ordinary indirect-memory full-stack path, not general route reliability or
human preference.

### Selected-cast verifier rejection

Case 2 reached and passed Reasoner, context assembly, Composer typed decoding,
and Python structural validation. The independent verifier then rejected the
candidate for an unsupplied protected-user realization. This may be a correct
catch of DeepSeek authoring Ted beyond the source, or a verifier false positive.
The terminal qualification artifact cannot distinguish those possibilities:
it retains hashes, the violation code, and receipt handles, but neither the
rejected candidate nor a human-readable bounded finding. No claim about Yuuni
exclusion can be made for this failed case because the runner's assertion occurs
only after verifier acceptance.

### Composer quote-anchor failure

Case 3 reached DeepSeek and received a structurally parseable response, but one
provider-authored quote anchor was not a literal substring of its own
`story_text`. Python correctly rejected it. This is a recurring ownership
pressure point: asking a model to copy exact evidence from a simultaneously
generated prose field creates avoidable mismatch risk. Validation must not be
weakened or fuzzy-matched merely because the prose may be usable.

A provider-neutral future correction should evaluate returning ordered,
model-labeled story segments that Python concatenates and hashes, so Python can
derive exact spans without trusting copied quotes. That is a contract redesign,
not a v5 retry or case-specific repair.

### Adult EX selector failure

Cases 4 and 5 expose one shared selector-contract defect. `AdultCraftNeedV2`
uses `required_concepts` for both semantic coverage and required vocabulary.
`AdultCraftSelector._requirements` therefore emits `lexicon:*` for every
non-indirect channel concept. Abstract structural concepts such as `buildup`,
`aftermath`, and `material_continuity` are valid semantic/craft concepts but do
not necessarily own vocabulary groups. The catalog can cover their concept and
axis requirements while still failing the incorrectly implied lexicon demand.

The correction should split semantic concepts from explicitly lexicalized
concepts, or define an authoritative lexicalizable-concept set in the domain
contract. It should not add empty token lists or one-off fragments merely to
silence these exact errors.

### Failure-evidence gaps

The Adult EX Reasoner calls happened before `LiveShapedTurnPipeline.execute`,
but craft-selection failure exits through a generic failure envelope without
auditing the supplied precomputed Reasoner result. Cases 4 and 5 therefore
export no prior-stage receipt handles despite a crossed live Reasoner stage.
That violates the intended complete safe receipt-chain invariant.

Case 2 exports the complete set of safe receipt handles, including the verifier
receipt and provider receipt, but the disposable database that held the payloads
is gone. IDs and hashes prove binding but are insufficient for later receipt
inspection or human classification of a semantic rejection. A future correction
should export the actual privacy-safe receipt payloads for every crossed stage
and define a separately governed qualification-review artifact for rejected
candidate evidence. Runtime operational receipts should remain prose-free.

## What must not be concluded

V5 does not qualify or promote the complete live route. It does not establish
adult quality, selected-cast reliability, verifier false-positive rate,
arbitrary paraphrase detection, production readiness, or human preference. It
does show that the live verifier can accept one ordinary candidate and reject
another, and that the no-retry/no-write boundary remained intact.

No v5 correction or rerun is authorized by D-131. The terminal evidence must
remain unchanged.

## ChatGPT Pro advisory review

ChatGPT Pro returned:

```text
CERA_LIVE_FIVE_V5_DIAGNOSIS_ACCEPTED
```

Pro accepted all four diagnosis classes and confirmed that v5 is terminal
evidence of a non-qualified route. It agreed that:

- Adult EX failures belong to semantic-concept versus lexical-requirement
  ownership, not missing case-specific vocabulary;
- Composer self-copy anchors should be replaced by ordered labeled segments
  that Python concatenates and hashes;
- qualification failure evidence must export all crossed privacy-safe receipt
  payloads before disposable state is destroyed;
- a separately governed rejected-candidate review artifact is needed to
  adjudicate verifier findings while operational receipts stay prose-free;
- case 2 is failed but diagnostically indeterminate;
- no rerun, correction, provider call, or product/production action is
  authorized by the review.

Codex independently accepts the advisory verdict. It matches the code path,
terminal evidence, and narrow claims. The minimum provider-free correction
package remains a future creator authorization: semantic/lexical separation,
ordered story-segment Composer DTO, universal stage-failure evidence export,
governed rejected-candidate review evidence, and broad failure-path tests. None
of that package was implemented under D-131.
