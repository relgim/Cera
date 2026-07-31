# Phase 10 Provider-Free Evaluation Foundation Result

**Date:** 2026-07-28  
**Status:** accepted; ChatGPT Pro returned `PHASE_10_OFFLINE_EVALUATION_ACCEPTED`  
**Authorization:** D-052  
**Scope:** offline evaluation contracts, sealed-manifest integrity, privacy-safe telemetry, blinded matched review, route-promotion assessment, and deployment-readiness gates

## Outcome

Phase 10 now has a provider-neutral evaluation layer that can compare a concrete route identity by runtime role without letting test output promote that route, mutate story authority, or deploy CERA. The layer distinguishes deterministic/local, scripted-fake, and live-provider evidence. Passing every offline case yields only `offline_evidence_only`; it cannot qualify Codex, DeepSeek, or another provider.

The offline foundation is complete within D-052. Live route execution, real human ballots, SillyTavern integration, production-world binding, creator fresh-chat acceptance, and deployment cannot be completed from the current authorization and remain explicit blockers.

## Implemented components

- `src/cera/evaluation/models.py`: role, partition, route, criterion, case, suite, result, finding, run-report, telemetry, human-review, promotion-policy/assessment, and deployment-readiness contracts.
- `src/cera/evaluation/harness.py`: exact case/result binding, evidence-class enforcement, deterministic findings and hashes, blinded A/B packet construction, separate unblinding keys, human preference summaries, and promotion assessments.
- `src/cera/evaluation/readiness.py`: deterministic readiness blockers that can return only `blocked` or `eligible_for_creator_decision`; it never deploys.
- `src/cera/evaluation/real_genesis.py`: existing auto-deleting real-Genesis sandbox reused as provenance evidence without production binding.
- `src/cera/registry.py`: all public versioned Phase 10 records registered in the closed schema registry.
- `src/cera/errors.py`: stable evaluation, contamination, promotion, and deployment error vocabulary.

## Boundaries enforced

- Each evaluation run is one runtime role, one route identity, one suite manifest, and one partition.
- Development/calibration/holdout cases have unique IDs, hashes, and input hashes. Holdout cases must be sealed; non-holdout cases cannot claim sealed status.
- Known craft-asset hashes are excluded from case inputs, expectations, and provenance. This prevents an Adult EX or other craft example from being counted as evaluation evidence.
- A run must cover exactly the selected manifest cases and every declared criterion. Duplicate or unknown cases fail closed.
- Authority, privacy, identity, consent/capacity, cast, branch, and protected-user criteria are always hard defects. They cannot be converted to a lower severity or averaged away.
- Offline routes record zero provider calls and cannot claim live-provider evidence. Live routes require provider identity/revision, at least one call per case, live evidence class, and matching privacy-safe telemetry.
- Evaluation results and telemetry cannot write story authority. The real-Genesis test database is allocated internally under a temporary directory and removed automatically.
- Telemetry contains bounded machine tokens, route hashes, counters, and stable error codes only; flags for raw source, prose, private evidence, prompts, and secrets must all be false.
- A/B packets contain shared presentation-neutral context and candidate text but no route/provider identity. Route mapping and nonce hash live in a separate key.
- Promotion requires role-specific case/holdout/risk-tag coverage, pass rate, human case/ballot coverage, matched human preference, zero hard defects, and genuine live-provider evidence.
- An eligible assessment still sets `creator_approval_required: true` and `route_promoted: false`.
- Deployment readiness still cannot deploy. Even with every input gate true it returns only `eligible_for_creator_decision` and `deployment_executed: false`.

## Honest evidence limit

The contracts validate structure, bindings, hashes, counters, and declared deterministic observations. They do not themselves prove that an evaluator's semantic judgment is correct. Future role-specific suites must bind each criterion to a named deterministic validator or to blinded human review, and live provider outputs must be evaluated without using provider self-reported pass/fail claims.

No live provider qualification or real human preference study was run. Therefore this phase does not establish Codex judgment quality, DeepSeek prose quality, adult-mechanics quality, provider latency/cost/reliability, or creator acceptance.

## Validation

Focused Phase 10 tests:

```text
python -m unittest tests.test_evaluation_foundation -v
Ran 18 tests
OK
```

Coverage includes sealed holdout enforcement, craft contamination rejection, deterministic report reproduction, exact observation coverage, offline/live evidence separation, privacy-safe telemetry, blinded review, separate unblinding, hard-defect non-averaging, matched human preference, report-integrity rejection, deployment blockers, all-gates creator-decision behavior, and disposable real-Genesis provenance.

Pre-review complete repository suite:

```text
python -m unittest discover -s tests -p "test_*.py"
Ran 187 tests
OK
```

`compileall` passed, and the two documentation-validation tests passed with zero findings. After ChatGPT Pro's accepted review, the same gates were rerun: 18/18 focused tests, 187/187 complete tests in 45.519 seconds, clean compilation, and 2/2 documentation tests all passed.

## Remaining Phase 10 gates

Separate creator authorization/input is required before any of the following:

1. select and configure runtime Codex transport/model/effort routes;
2. select and configure DeepSeek Composer and optional Adult Mechanics routes;
3. execute live provider evaluation and collect latency/token/cost/reliability receipts;
4. define/import Adult EX craft assets, which remain excluded from evaluation evidence;
5. conduct creator/blinded human review using actual candidate prose;
6. approve credential storage and production-world binding;
7. integrate and validate the SillyTavern route;
8. perform creator fresh-chat acceptance;
9. authorize deployment.

There is no automatic fallback, silent promotion, production binding, or deployment at this gate.

## ChatGPT Pro review

ChatGPT Pro returned `PHASE_10_OFFLINE_EVALUATION_ACCEPTED` and required no in-scope correction. Its acceptance specifically confirmed offline/live evidence separation, non-averagable hard defects, holdout/craft isolation, report integrity, privacy-safe telemetry, route-blinded matched review, human preference as a real promotion threshold, and the distinction between structural evidence and semantic truth.

The review was an advisory evidence-packet review rather than an independent source-code audit. It does not qualify or authorize live Codex/DeepSeek, semantic evaluator correctness, Adult EX, handler work, credentials, production binding, SillyTavern, route promotion, final creator acceptance, or deployment.
