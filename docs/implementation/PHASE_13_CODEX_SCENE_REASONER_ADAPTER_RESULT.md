# Phase 13 Codex Scene Reasoner Adapter Result

**Date:** 2026-07-28  
**Status:** accepted by ChatGPT Pro within the provider-free adapter boundary  
**Authorization:** D-062  
**Scope:** typed Codex Scene Reasoner packet/result mapping, bridge lifecycle integration, provider-neutral receipts, and deterministic adapter tests without a live story-role call

## Outcome

CERA now has an executable `CodexSceneReasonerPort` adapter that joins the accepted Codex transport and request-bound MCP evidence bridge to the existing provider-neutral Reasoner and Python validation contracts.

For one invocation it:

1. deterministically serializes a `SceneReasonerRequest` into `cera.codex_scene_reasoner_packet.v1`;
2. binds request, source-view, and immutable snapshot hashes;
3. opens one request-bound evidence bridge;
4. makes exactly one Codex transport call with a closed JSON Schema;
5. reconciles the bridge/provider tool traces;
6. strictly decodes `cera.reasoner_outcome.v1`;
7. returns an advisory adapter call to the existing `ReasonerCoordinator`;
8. reuses Python's hard cast, floor, citation, record-version, exact-fetch, privacy, knowledge-owner, protected-user, route, consent, and state-delta validation;
9. emits a provider-neutral `cera.scene_reasoner_receipt.v2` while retaining the matched safe provider and bridge receipts for later transactional integration.

No provider is allowed to create story truth. The adapter output remains advisory until Python validation, later composition, reply validation, and an atomic commit.

## Packet boundary

The packet contains only the already authorized Reasoner request: prepared-turn identities/authority, safe source view, exact seed evidence, scene anchors, explicit unknowns, prohibited inferences, and hard boundaries. It contains no raw protected source handle resolution, filesystem path, SQL/database access, Composer-only restricted source envelope, or prompt examples.

The prompt instructs Codex to:

- choose only eligible aware NPCs;
- leave the protected user's next unsupplied choice open;
- produce operational causal beats rather than story prose;
- treat search as reference-only and exact-fetch any non-seed evidence used in a hard decision;
- cite exact evidence ID, record ID, and version;
- preserve branch, privacy, knowledge, identity, consent/capacity, source-state, cast, floor, and stop boundaries;
- return typed insufficiency rather than guess;
- treat future segments as conditional plans, never committed events.

Consent-valid adult requests remain on `adult_non_graphic_ledger`. A deterministic test confirms that a restricted exact-source marker does not enter the Codex packet.

## Closed result schema

The output schema closes every object with `additionalProperties: false`, bounds arrays and operational text, constrains typed-ID kinds, excludes the aftermath route from ordinary/adult Scene Reasoning, allows no Genesis mutation proposal, and requires explicit protected-user-boundary acknowledgement.

Python then performs semantic/authority checks that JSON Schema cannot prove. A structurally valid Codex result is rejected when it authors a protected-user beat, transfers another character's private evidence, cites unfetched/stale evidence, changes the route/cast/floor, or violates an existing state boundary.

## Receipt evolution

`cera.scene_reasoner_receipt.v2` replaces the fake-only v1 shape with a provider-neutral receipt. It binds:

- request, snapshot, source, source-view, and outcome hashes;
- adapter role/version and fixture-or-route evidence identity/hash;
- provider receipt ID/hash;
- optional required bridge receipt ID/hash for Codex;
- exact lookup receipt IDs, total tool calls/bytes, outcome status, and external-call count.

Fake calls require zero external calls and no live supporting receipts. Codex calls require exactly one provider receipt and one matching bridge receipt. The transient `ReasonerExecutionResult` retains those two safe receipts instead of discarding the audit evidence before future persistence work.

## Additional bridge hardening

Phase 13 adds a maximum MCP-call budget, default 12 and bounded to 1–32. It is part of the public bridge binding hash, enforced by the Python dispatcher, checked again against SDK observations, and paired with the existing query/fetch/depth/byte budgets. This closes repeated snapshot/tool-call amplification that byte/search limits alone would not stop.

## Deterministic coverage

The nine adapter tests cover:

- deterministic packet/prompt/schema construction;
- ordinary indirect search then exact fetch;
- valid multi-character floor/participation and owner-private evidence use;
- protected-user beat rejection;
- cross-character private-evidence transfer rejection;
- `insufficient_evidence` and blocker decoding without invented decisions;
- unknown output-field rejection;
- explicit unretried provider failure;
- consent-valid adult non-graphic-ledger isolation.

Existing tests continue to cover stale snapshots, missing exact fetch, record-version drift, knowledge-owner policy, source-state/route/cast validation, advisory-only state deltas, adult preflight blockers, regeneration, restart, sibling branches, fork isolation, and no partial authority writes. The adapter adds no new restart or branch authority; it consumes the already bound request/snapshot.

## Validation

```text
python -m unittest tests.test_codex_scene_reasoner -v
Ran 9 tests
OK

python -m unittest discover -s tests -p "test_*.py"
Ran 216 tests
OK
```

The full run uses the optional MCP runtime so the real local protocol test executes. `compileall` and documentation validation complete cleanly.

## Deliberate non-claims

Phase 13 made no provider call. It does not prove:

- that the live Codex service accepts the complete Reasoner JSON Schema;
- that Codex chooses correct queries or character actions;
- psychological realism, multi-scene quality, or long-context reliability;
- model-tier superiority or route promotion;
- DeepSeek composition quality;
- publication, persistence, or production readiness.

No story, Hanezawa Genesis, adult, private-memory, or production-database packet was sent to a provider.

## Next gate

The next safe provider-free milestone is the typed `DeepSeekSceneComposerPort` packet/candidate/manifest adapter and deterministic reuse of existing Composer validation. A live synthetic Reasoner role probe, any live story/Genesis/adult packet, role calibration, holdout use, human review, or promotion requires its own explicit evidence/authorization boundary.

Adult EX, live Adult Mechanics, external-handler work, production data/world binding, SillyTavern, promotion, deployment, and final creator acceptance remain closed.

## ChatGPT Pro review

ChatGPT Pro returned `PHASE_13_CODEX_SCENE_REASONER_ADAPTER_ACCEPTED` with no in-scope correction. The verdict accepts typed mapping, bridge lifecycle, receipt structure, and deterministic validator integration only. It does not establish live schema acceptance, Codex judgment, model qualification, story/Genesis/adult provider authorization, DeepSeek composition, production binding, SillyTavern, promotion, or deployment.
