# Blocked Turn and Resumption Architecture

**Status:** controlling blocker/external-event contract

## 1. Boundary

When Python classifies the requested continuation as blocked non-consensual sexual content:

- no Codex or DeepSeek adult generation continues through the event;
- no Adult Mechanics enrichment is called;
- no Composer is called for the blocked event;
- the rejected turn does not become story truth;
- durable character, relationship, memory, event, and material state do not change.

This is an orchestration boundary, not a semantic rewrite. Python must not make deliberate conduct accidental or change consent to route around it.

## 2. Artifacts remain separate

| Artifact | Purpose | Canonical? |
|---|---|---:|
| Protected source | Exact user input, access-controlled | No, until accepted through an authorized story transaction |
| `RejectedTurnReceipt` | Operational proof that generation was rejected | No story authority |
| `BlockedTurnCheckpoint` | Last validated branch/source state and neutral boundary classification | No new event authority |
| `TemporaryAftermathProjection` | Hypothetical effects from facts through checkpoint | Never canonical |
| `ExternalEventRequest` | Neutral correlation envelope for a user-owned handler | No |
| `ExternalCompletionReceipt` | Non-graphic claim of independently completed events | Not until validated/committed |
| Objective event record | Validated committed classification and sequence | Yes, branch-scoped |
| Character memory | Owner-specific recollection/meaning | Yes after validation, branch/private scoped |
| Relationship/material/development records | Validated consequences | Yes after commit |
| Genesis | Creator seed | Immutable; never rewritten by this flow |

## 3. Checkpoint and response

Python emits a machine-readable `BlockedTurnCheckpoint` plus a stable user-facing error such as:

```text
CERA_BLOCKED_NONCONSENSUAL_EVENT: generation stopped at the consent boundary.
No story or memory state was committed. Trace: <trace_id>
```

The message must not claim the blocked event occurred.

When the reasoner is available, Python may automatically request a temporary projection based only on:

- committed branch state before the rejected turn;
- neutral facts established up to the boundary;
- the fact that a blocked boundary was detected;
- current character evidence.

The projection must type:

- `established_facts`;
- `character_knowledge`;
- `derived_interpretations`;
- `uncertainties`;
- `prohibited_assumptions`.

It may describe possible fear, confusion, trust disruption, safety concerns, or follow-up needs as hypotheses. It may not fill the blocked interval, assert an outcome, become a memory, or be supplied to ordinary retrieval as truth. It lives in a branch/request-bound scratch namespace and is deleted after successful reconciliation or expiry.

## 4. External interface boundary

CERA may expose an `ExternalEventRequest` containing world, Genesis revision, protected-user identity, correlation IDs, hashes, last accepted artifact (or paired null ID/hash on an empty root branch), checkpoint, controlled-registry version, expiry, opaque single-use callback token, and the required non-graphic receipt schema.

CERA must not implement, inspect, prompt, test, or document the internal generation behavior of the user-owned handler. It does not receive or store the handler's raw generated prose.

## 5. Completion receipt content

The receipt preserves continuity-significant facts without graphic prose:

- controlled event/act classifications;
- ordered neutral steps and completion state;
- actual consent/capacity status, refusal, withdrawal, pressure, and freedom;
- resistance, freezing, calls for help, and observed response or lack of response;
- defensive actions;
- material object/location/condition changes;
- injury status or uncertainty;
- reproductive exposure status;
- conception status separately as `not_applicable`, `unknown`, `not_established`, or `established`;
- event ending and current safety;
- observer and knowledge ownership;
- immediate non-graphic physical/emotional state;
- world/Genesis/protected-user/external-request/source/request/branch/generation/start-artifact/checkpoint/registry bindings and integrity hash.

Physiological/vocal reaction, silence, freezing, compliance under force, and failure to resist never become consent or enjoyment fields.

Reproductive exposure is continuity-significant. Record direct exposure separately from pregnancy risk and conception. A directly affected character may know the exposure while conception remains unknown. Facts and fears remain separate.

## 6. Validation

Python rejects with no mutation when a receipt is:

- missing or malformed;
- hash-invalid;
- bound to a different world, Genesis revision, protected user, external request, registry, request, source, branch, generation, checkpoint, or starting artifact;
- stale relative to the branch head;
- a duplicate with conflicting content;
- internally contradictory;
- graphically detailed beyond the controlled schema;
- missing required event order, consent, safety, knowledge, material, injury, or reproductive fields.

An identical duplicate returns the prior receipt/commit result idempotently and makes no new write.

The receipt is a claim, not authority. Python validates schema/binding/invariants. Codex then checks non-graphic causal coherence and reconciles character meaning. Every accepted objective event, owner-private memory, relationship record, material/reproductive state, and development overlay is represented as a complete access-filterable `EvidenceDocument`. Only the final transaction promotes validated facts.

## 7. Resumption

For a valid receipt:

1. Python locks the branch/generation and verifies the starting artifact.
2. Codex compares the receipt with the temporary projection.
3. Projection statements are marked confirmed, contradicted, still unknown, or discarded.
4. Codex creates a non-graphic `AftermathDecision` covering current safety, character knowledge, immediate emotion/action, relationship meaning, material/reproductive concerns, and open threads.
5. The regular DeepSeek Scene Composer may realize the aftermath. Adult Mechanics enrichment is not used.
6. Python validates prose against the receipt, accepted plan, protected-user/cast/privacy rules, and presentation neutrality.
7. One transaction commits:
   - accepted aftermath prose;
   - objective event record;
   - material and reproductive results;
   - character knowledge;
   - relationship changes;
   - private trauma memory when supported;
   - branch-local development overlay;
   - generation and external-receipt commit records.
8. The temporary projection is deleted inside that same transaction.
9. The accepted prose is rendered for SillyTavern.

If composition or commit fails, the prior branch head remains active. The validated receipt may remain pending and restart-safe; it is not partially applied.

## 8. Memory after resumption

The objective record may classify a non-consensual event directly and neutrally. The affected character's private memory may preserve:

- perceived sequence and calls for help;
- defensive actions and observable response;
- knowledge of material/reproductive exposure;
- fear, confusion, betrayal, shame, anger, or self-blame as owner-specific states;
- uncertainty about injury, conception, others' knowledge, or future safety.

Self-blame never changes objective responsibility. The system does not automatically diagnose PTSD, assign a permanent trait, or rewrite the base character card. Later validated evidence may update a branch-local overlay.

## 9. Restart and idempotency

All blocker artifacts use a single correlation chain:

```text
request_id
source_hash
branch_id
generation_id
starting_artifact_id/hash
checkpoint_id/hash
external_receipt_id/hash
transaction_id
```

After restart, Python can determine whether it is awaiting a receipt, validating one, composing aftermath, ready to commit, or already committed. It never repeats a provider call automatically or applies a receipt twice.
