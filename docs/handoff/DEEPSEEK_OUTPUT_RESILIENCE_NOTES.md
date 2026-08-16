# DeepSeek output resilience notes

These notes guide future fixes for inconsistent, partial, or malformed DeepSeek
responses. They are design guidance, not a new runtime contract.

## Lessons retained from Vera and Plan Spine V3

- DeepSeek proposes creative and semantic material; Python owns identity,
  authority, state transitions, validation, and durable truth.
- Request a compact required spine before optional detail. Lock the few fields
  needed to preserve actor, action, intended result, stopping point, and route.
- Treat richer detail as recoverable or optional when the compact spine and
  visible prose are usable.
- Normalize known harmless transport variation before schema validation. Keep
  the normalized inner contract strict and retain hashes of the raw response.
- Recover only model-supplied fields that are actually present. Never invent a
  missing story fact merely to make a response pass.
- Resolve missing context in this order: current evidence, Python-selected
  existing context, explicit unresolved state, omission of low-impact detail,
  then a bounded resolver only for material missing facts.
- Derive event and memory candidates from the audited visible output, not from
  the model's intended plan alone.

## Recommended CERA handling

Classify a failed DeepSeek response before spending another call:

1. **Harmless envelope variation** — unwrap a single known wrapper such as a
   JSON Markdown fence or one analysis preface followed by a single complete
   trailing JSON object, then apply the unchanged strict schema. An invalid
   analysis/example block does not compete with the one valid final object;
   multiple valid objects, ambiguous objects, and truncated outputs remain invalid.
2. **Recoverable metadata omission** — retain usable prose and spine; let
   Python bind or omit non-story bookkeeping fields when deterministic. Adult
   Filter summaries may aggregate adjacent decisions when their event keys are
   a valid ordered subset and the protected/public projections still match. A
   redundant private-owner field on an explicitly public effect is cleared;
   Python does not infer a missing owner for a private effect. A character that
   DeepSeek explicitly names as an event knowledge owner is included in that
   event's participant metadata.
3. **Incomplete visible prose** — if the core scene is present but truncated,
   use one custody-bound continuation from the last complete paragraph rather
   than regenerating the entire scene.
4. **Missing central action or unusable output** — only then use a fresh Writer
   call. Preserve the same frozen authority and record why recovery was unsafe.
5. **Protected-fact or consent failure** — fail closed. Formatting recovery
   must never weaken Ted protection, immutable identity/age/kinship, consent,
   withdrawal, or accepted-event existence.

## Implementation threshold

Do not add a new parser exception for every novel one-off response. Record the
raw failure shape. Generalize only when the repair is demonstrably safe or the
same failure pattern recurs. Prefer one stage-level normalization/recovery seam
over scattered scenario-specific patches.
