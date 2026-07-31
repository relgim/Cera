# Phase 1 Repository Foundation Result

**Status:** complete; gate passed  
**Completed:** 2026-07-28  
**Authorization:** creator-approved sequential Phase 1–7 work, subject to phase inputs and explicit no-live-provider boundary

## Delivered

- Python 3.12 `src`-layout package with no runtime dependencies.
- Opaque typed IDs with random and deterministic creation plus kind validation.
- Strict canonical JSON, UTF-8 bytes, text hashing, and SHA-256 helpers.
- Closed dataclass decoding that rejects unknown/missing fields and invalid enum/ID values.
- Versioned schema registry containing 13 controlling contracts.
- Phase-safe configuration that rejects live provider calls and reference-repository runtime dependencies.
- Stable error-code and retry-mode vocabulary.
- Typed turn, evidence, decision/SequencePlan, accepted-artifact, blocker, projection, external-interface/receipt, memory, and commit contracts.
- Repository documentation validator covering required files, relative links, encoding markers, and forbidden reference paths in runtime source.
- Deterministic `unittest` coverage for the foundation and authority-critical invariants.

## Verification

```text
python -m compileall -q src tests
PASS

python -m unittest discover -s tests -t . -v
Ran 16 tests
OK

python -m cera.documentation D:\AIChatBot\Cera
ok=true
findings=[]
```

Registered schemas:

```text
cera.accepted_story_artifact.v1
cera.blocked_turn_checkpoint.v1
cera.character_memory.v1
cera.commit_receipt.v1
cera.error.v1
cera.evidence_hit.v1
cera.external_completion_receipt.v1
cera.external_event_request.v1
cera.foundation_config.v1
cera.rejected_turn_receipt.v1
cera.scene_decision.v1
cera.temporary_aftermath_projection.v1
cera.turn_request.v1
```

## Findings resolved during implementation

1. The first documentation negative-test fixture escaped a reference path differently from runtime source. The validator and fixture were corrected to detect raw, slash-separated, and escaped Windows paths.
2. The initial strict decoder allowed malformed typed IDs to surface as an identity exception rather than a contract error. It now converts malformed decoded IDs into `ContractValidationError` with field context.

Both fixes were followed by the complete passing verification above.

## Scope confirmation

- No provider SDK, connection, credential, or model call.
- No SQLite/story database yet.
- No Genesis data or Adult EX files.
- No SillyTavern integration, deployment, or external-handler implementation.
- No runtime imports or data paths from Vera/V6 or E-drive references.

## Gate decision

Phase 1 satisfies its documented gate. Phase 2 may begin after the required Pro phase review is considered. SQLite with WAL is creator-approved.

## Pro review and entry tightening

The pinned Pro chat returned `CONTINUE_TO_PHASE_2`. Pro could not independently read the repository because its repository connector was unavailable, so Codex retained test/diff ownership.

Before opening Phase 2, Codex accepted and implemented the substantive entry checks:

- `TurnRequest` now binds world, immutable snapshot, and Genesis revision IDs.
- `EvidenceHit` now carries stable record/version, truth status, world/branch/generation/snapshot/perspective/knowledge/content/Genesis scopes, validity interval, and retrieval reason.
- `CommitReceipt` now binds source, accepted-artifact hash, generation transition, validated deltas, and lookup/provider/validation receipts.
- Error vocabulary now distinguishes invalid intake, unavailable evidence service, verifier failure, and delivery failure after commit.
- Domain-separated hashing is available for semantic artifact classes while prose SHA-256 remains a content hash.
- Class documentation makes accepted prose presentation-neutral, decisions non-mutating, projections non-authoritative/regenerable, and external envelopes provider-neutral.

The complete 16-test suite passed again after these changes.
