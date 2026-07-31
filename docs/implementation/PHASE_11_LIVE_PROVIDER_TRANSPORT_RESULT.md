# Phase 11 Live-Provider Transport Result

**Date:** 2026-07-28  
**Status:** accepted by ChatGPT Pro within the transport-only boundary  
**Authorization:** D-054  
**Scope:** current provider discovery, bounded non-story live probes, typed route/receipt contracts, one-call transports, and reproducible qualification tooling

## Outcome

CERA now has provider-neutral, promotion-safe transport primitives for ChatGPT-authenticated Codex and API-authenticated DeepSeek. The milestone proves that the configured accounts can be called through isolated CERA-owned code and that every call can be bounded, timed, hashed, cost/quota-accounted, and failed explicitly without story writes, retry, or fallback.

It does **not** complete `CodexSceneReasonerPort` or `DeepSeekSceneComposerPort`. Codex still needs a required request-bound MCP bridge to `ReasonerEvidenceTools`. Both roles still need typed domain-packet serialization/parsing, development and sealed holdout suites, semantic validation, matched blinded human review, and creator promotion.

## Implemented

- `src/cera/providers/models.py`: live route, dated pricing, model-identity qualification, safe call receipt, result, and transport-error contracts.
- `src/cera/providers/codex.py`: JSON-Schema-only Codex transport with empty-workspace enforcement and a killable subprocess timeout.
- `src/cera/providers/codex_worker.py`: Python Codex SDK/app-server worker using ChatGPT session auth, ephemeral threads, pinned SDK/CLI `0.144.4`, explicit model/effort, disabled web search, and a no-files/no-network permission profile.
- `src/cera/providers/deepseek.py`: one-shot HTTPS Chat Completions transport using `DEEPSEEK_API_KEY`, current returned-model verification, explicit thinking mode, byte/output/cost ceilings, and no retry.
- `src/cera/providers/routes.py`: unpromoted Sol/Terra/Luna and V4 Pro/Flash candidate identities.
- `scripts/run_provider_transport_probe.py`: explicit `--confirm-live` reproducibility tool; never imported or called by tests.
- two registered schemas and two new stable error codes.

## Current-model decision

Official current guidance identifies GPT-5.6 Sol, Terra, and Luna as Codex's supported family: Sol for difficult/open-ended/high-value work, Terra as the everyday workhorse and natural starting replacement for previous GPT-5.5 work, and Luna for clear repeatable workloads. Therefore CERA compares the current family instead of retaining 5.5 as an automatic route.

DeepSeek's current API lists `deepseek-v4-pro` and `deepseek-v4-flash`. The former `deepseek-chat` and `deepseek-reasoner` aliases reached their stated retirement date on 2026-07-24 and are rejected by CERA configuration.

Candidate posture remains:

- Codex Sol/medium: quality-first Scene Reasoner candidate;
- Codex Terra/medium: ordinary-route comparator;
- Codex Luna/medium: repeatable/latency comparator, not an assumed psychology route;
- DeepSeek V4 Pro, non-thinking for prose: quality-first Composer candidate;
- DeepSeek V4 Flash, non-thinking: latency/cost comparator.

No candidate is promoted.

## Live transport evidence

All probes used non-story, non-Genesis, non-adult inputs and produced no authority-store writes.

| Route | Result | Model work latency | Input | Output | Reasoning output | Cost/quota |
|---|---:|---:|---:|---:|---:|---|
| Codex Sol/medium | schema exact | 2,971 ms | 13,059 | 20 | 0 | ChatGPT quota |
| Codex Terra/medium | schema exact | 3,239 ms | 13,059 | 20 | 0 | ChatGPT quota |
| Codex Luna/medium | schema exact | 2,239 ms | 11,866 | 20 | 0 | ChatGPT quota |
| DeepSeek V4 Flash | exact on direct and repeat adapter probes | 832 ms repeat adapter probe | 27 | 6 | not returned | estimated 6 micro-USD |
| DeepSeek V4 Pro | exact | 907 ms | 27 | 6 | not returned | estimated 17 micro-USD |

One first adapter-level Flash probe returned a non-exact three-token response after an earlier direct exact response. A single evidence-gathering repeat returned exact `CERA_PROVIDER_OK` in six tokens. This is retained as minor transport variability; it is not normalized into semantic-quality evidence and reinforces that strict Composer JSON/manifest parsing and actual role suites are required.

The Sol/Terra/Luna probe is intentionally too easy to compare reasoning quality. It shows route availability and baseline overhead only. Luna was fastest in this tiny sample, but that is not evidence that it can replace Sol for character psychology.

Codex input usage includes the app-server/Codex instruction baseline, roughly 12K-13K tokens before real CERA evidence. That quota/context overhead must be measured on representative reasoner packets and is a material optimization concern.

## Model identity caveat

DeepSeek echoes its concrete model and system fingerprint, so the receipt marks its model identity provider-verified.

Codex app-server confirms the OpenAI provider and pinned CLI runtime but the high-level SDK result does not echo the concrete model slug. CERA sends the explicit non-alias model and records `model_identity_source: explicit_request` with `model_identity_verified: false`. This is an honest limitation, not a transport failure. A later lower-level app-server capability may tighten this evidence if the protocol exposes it.

## Validation

Focused tests:

```text
python -m unittest tests.test_provider_qualification -v
Ran 10 tests
OK
```

Complete repository suite:

```text
python -m unittest discover -s tests -p "test_*.py"
Ran 197 tests
OK
```

`compileall` completed cleanly. Tests cover route/provider/role bindings, current model IDs, retry/fallback/production rejection, dated price math, credential absence, network failure, returned-model drift, output parsing, safe receipts, Codex workspace isolation, runtime-version drift, schema registration, and exactly one call with zero story writes.

## Remaining gate

Before any live story-role evaluation:

1. implement a required request-bound MCP evidence bridge exposing only the existing typed search/fetch/character/continuity tools;
2. prove the bridge fails closed on initialization, stale snapshots, privacy, knowledge, branch, and budget violations;
3. implement `CodexSceneReasonerPort` packet/schema mapping and reuse Python's existing outcome validation;
4. implement `DeepSeekSceneComposerPort` packet and candidate/manifest mapping and reuse existing Composer validation;
5. build role-specific development/calibration/holdout cases without Adult EX contamination;
6. run matched Sol/Terra/Luna and Pro/Flash cases;
7. conduct blinded human review;
8. request creator promotion separately.

Adult EX, Adult Mechanics live calls, actual story content, real Genesis/story databases, external-handler work, SillyTavern, production binding, route promotion, and deployment remain closed.

## ChatGPT Pro review

ChatGPT Pro returned `PHASE_11_LIVE_PROVIDER_TRANSPORT_ACCEPTED` after reviewing the evidence packet. No in-scope correction was required. The review accepted provider connectivity, isolation, request/response binding, privacy-safe receipts, bounded execution, explicit failure preservation, and the one-call/no-retry/no-fallback contract.

The review specifically agreed that:

- Codex requested-model identity must remain `explicit_request` and unverified unless a later lower-level protocol returns the served model;
- a separately receipted DeepSeek Flash repeat is a new probe, not an automatic retry, and the earlier strict-probe failure remains evidence;
- DeepSeek cost values are estimates, while ChatGPT-authenticated Codex usage is subscription quota rather than verified zero monetary cost;
- the tiny probes do not compare model quality, qualify either story role, establish production latency/cost, or support promotion.

The required MCP evidence bridge, live Scene Reasoner and Scene Composer qualification, Adult EX, live Adult Mechanics, external-handler work, production data/world binding, SillyTavern, promotion, deployment, and final creator acceptance remain outside this verdict.
