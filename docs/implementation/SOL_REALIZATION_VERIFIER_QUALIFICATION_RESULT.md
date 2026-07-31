# Sol-medium Scene Realization Verifier Qualification Result

**Date:** 2026-07-29  
**Authority:** D-128  
**Status:** implementation, one-shot live probe, independent evaluation, and ChatGPT Pro advisory review complete

## Outcome

CERA now has a provider-neutral, qualification-eligible
`CodexSceneRealizationVerifierPort` pinned to `gpt-5.6-sol` at medium reasoning
effort. It is an independent post-composition semantic inspector. It cannot
generate, continue, revise, repair, improve, or replace story prose. Python
continues to own authoritative decoding, semantic and cross-field validation,
quote resolution, offsets, hashes, receipt construction, and the final
accept/reject gate.

The mandatory provider-free gate passed **304/304** tests in 216.280 seconds.
The focused integration set passed **75/75**, bytecode compilation passed, the
live script refused execution without `--confirm-live`, and the new evidence
identity was confirmed absent before dispatch.

The one authorized non-story probe then passed with exactly one Sol-medium
call. The synthetic candidate contained one protected-user dialogue action
authorized by the source and one additional protected-user physical action not
present in that source. Sol returned `rejected` with
`protected_user_unsupplied_realization`; Python uniquely resolved the supplied
quote, derived its occurrence, offsets, and hash, validated the semantic sets,
and produced a v3 verification receipt.

No retry, fallback, second dispatch, story-authority write, story database,
five-case batch, product/production activation, publication, promotion,
external-handler action, SillyTavern action, or deployment occurred.

## Implemented contract

- `SceneRealizationVerifierPort` remains provider-neutral.
- `CodexSceneRealizationVerifierPort` accepts only the approved
  `scene_realization_verifier` Sol-medium route.
- The verifier receives the transient candidate, semantic expected beats,
  selected participants, exact protected-user source authorities and allowed
  kinds, hard boundaries, and Composer anchors.
- It receives no Genesis/retrieval tools, MCP bridge, filesystem/database
  access, Writer instructions, or craft examples.
- The provider draft owns advisory semantic classification and one unique exact
  quote per violation. It owns no canonical IDs, hashes, offsets, occurrence,
  route flags, acceptance, prose, or authority write.
- Python rejects malformed status combinations, duplicate semantic sets,
  incomplete beat/participant/boundary coverage, absent or ambiguous quotes,
  unsupported violation codes, and receipt-binding mismatches.
- Valid provider/context receipts survive a later typed or semantic failure.
- Unavailable, malformed, inconclusive, or rejected verification fails closed
  with no retry or fallback.

## One-shot evidence

Evidence directory:

`evaluation/evidence/codex_realization_verifier_probe_2026-07-29_v1`

Only `summary.json` is retained:

| Property | Value |
|---|---|
| Evidence file SHA-256 | `5debbc3ec668671bb535c27e79043767aa247d30a0dfc296f4ba3ac53e352298` |
| Probe status | `passed` |
| Verification status | `rejected` (expected) |
| Violation | `protected_user_unsupplied_realization` |
| Provider/model/effort | `openai_codex` / `gpt-5.6-sol` / `medium` |
| Dispatch attempts / provider calls | `1 / 1` |
| Automatic retries / fallbacks | `0 / 0` |
| Provider duration | `6530 ms` |
| Input / output / reasoning-output tokens | `9891 / 225 / 67` |
| Story-authority writes | `0` |
| Retained prompt/raw output/story prose | `false / false / false` |
| Provider receipt ID | `provider_receipt:0b70b3c7-97c1-5fef-9b29-5537bee540af` |
| Verification receipt ID | `realization_verification:7b6b240e-f9c3-52ff-ad2a-7e6029c3467e` |

The transport recorded the requested and returned model as
`gpt-5.6-sol`, but `model_identity_verified` remains `false` because the Codex
resolver supplies requested/returned identity rather than an independent model
attestation. This is disclosed evidence, not silently converted to a stronger
claim.

## Validation after the live call

The verifier/schema/qualification/documentation smoke passed **27/27**, and
bytecode compilation passed. No second provider call was made.

The earlier live-evidence inventories remain byte-for-byte consistent with
their recorded aggregate hashes:

| Evidence directory | Files | Aggregate SHA-256 |
|---|---:|---|
| `live_story_qualification_2026-07-28_v1` | 12 | `53354856da0dea168729e894b5033395680c22b792d2e47ce1295ce85ba5eec1` |
| `live_story_qualification_2026-07-29_v2` | 6 | `00e5ef73ba4b0256065dbc5d7608b6d2701a2c88c66eec3e7b4a151a0b39578b` |
| `live_story_qualification_2026-07-29_v3_structural_contract_v2` | 6 | `c4326756db4b44ac7dcffb9df32d9d477b05c26ab6e64bea0cc272b4bcbeda4f` |
| `live_story_qualification_2026-07-29_v4_provider_schema` | 6 | `e6a1606c53d0b023a6708674b102e5ba413bb3707d3fbbc266d7f426b322ae94` |
| `codex_schema_acceptance_2026-07-29_v1` | 1 | `3abd29b642e44aebd55368cd523ffae0019d89f24b2c5661635ea881e876d317` |

## What this qualifies and what it does not

This is positive minimum evidence for the Sol-medium adapter's provider-schema
acceptance, one-call transport, semantic protected-user-invention detection,
Python-owned anchor compilation, safe receipts, and fail-closed gate. It does
not establish arbitrary paraphrase coverage, accepted-candidate precision,
all beat/participant omission classes, adversarial robustness, story quality,
human preference, full live pipeline reliability, production readiness, or
route promotion. Another live probe or five-case story batch is not authorized
by D-128.

## ChatGPT Pro advisory review

ChatGPT Pro returned:

```text
CERA_SOL_REALIZATION_VERIFIER_QUALIFICATION_ACCEPTED
```

Pro found no required correction. It accepted the result only as a D-128
structural adapter qualification and one negative semantic canary. It agreed
that the verifier is procedurally independent and non-generative, the
model/Python field ownership is correct, the one-call fail-closed policy is
sound, the claim is appropriately narrow, and `model_identity_verified=false`
must remain explicit. It also confirmed that this acceptance grants no further
live or story-batch authority.

Codex independently accepts the advisory verdict. It matches the implementation
and evidence. In particular, `qualification_eligible=true` means this adapter
can satisfy a future explicitly authorized qualification-dispatch contract; it
does not mean general semantic accuracy, story-route qualification, production
readiness, publication authority, or promotion. A later story batch still
requires a separate creator authorization.

Pro's optional suggestions do not require D-128 rework:

- procedural independence is already established by the separate call and
  bounded packet, while no model-diversity claim is made;
- the evidence identity and `probe_kind` already scope this as a versioned
  non-story protected-user invention canary;
- durable receipts already retain finding hashes rather than the quote or
  story prose;
- safe codes are enums bound to versioned v1/v3 provider and receipt schemas.
