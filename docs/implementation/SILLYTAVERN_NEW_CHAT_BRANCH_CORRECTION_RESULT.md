# CERA SillyTavern new-chat branch correction result

**Date:** 2026-07-29  
**Authority:** D-146 and D-150  
**Result:** corrected and verified through the actual SillyTavern UI

## Reported failure

The creator selected SillyTavern's `New Chat`, then sent
`Hello, hanezawa residence?` from the new doorway transcript. The transport
reached CERA, but the Reasoner returned an insufficient-evidence outcome and
composition did not run. Request
`request:8e81c1b7-7ae4-5a5d-bed0-3bd8f7f77716` consumed one Sol-medium call,
made no DeepSeek or verifier call, and committed no story artifact.

## Root cause

The adapter routed every SillyTavern chat to `branch:main`. The new chat's
doorway-only transcript was therefore assembled with the first chat's already
accepted Sakura reply. The Reasoner correctly refused that contradictory
packet. The defect was client-chat-to-story-branch ownership, not missing
Genesis evidence and not a reason to weaken the Reasoner.

The non-decision path also exposed an audit gap: it recorded a completed
Reasoner stage but did not append a terminal privacy-safe failure bundle with
the valid Reasoner/provider receipt payloads.

## Shared corrections

- A CERA SillyTavern session now resolves to a deterministic root story branch.
- A fresh chat cannot inherit accepted artifacts, events, reply material, or
  derived continuity from another chat.
- The historical `branch:main` chat remains usable. Exact accepted-head prose
  in the client transcript may rebind that chat after a client session-key
  format change; zero or multiple branch matches do not silently merge state.
- The current SillyTavern client sends CERA controls only as typed transport
  metadata. It no longer injects hidden CERA control markers into the story
  prompt. The adapter retains conflict-checked legacy-marker parsing only for
  compatibility.
- A valid non-decision Reasoner outcome now terminates at the explicit
  `reasoner_decision_gate` stage and retains privacy-safe Reasoner, provider,
  MCP, and lookup receipts when available. Raw prompts, source, private
  evidence, secrets, and story prose remain excluded.

No retry, fallback, special greeting rule, or relaxed evidence validator was
added.

## Live verification

The exact failed second chat was reopened and replayed once after the shared
correction.

- request: `request:1efa512a-31af-526a-8f0b-d3f70f578b8d`;
- branch: `branch:923baf9e-f8e2-5648-9ad5-e9f4a265d99a`;
- accepted artifact: `artifact:d4f50646-ea89-5cfc-89ff-01f3b6c90a92`;
- generation: 1;
- provider stages: one Sol-medium Reasoner, one DeepSeek V4 Pro Composer, and
  one independent Sol-medium verifier;
- automatic retries/fallbacks: 0/0.

The original chat remains independently at generation 1 on `branch:main` with
artifact `artifact:afaa036d-40d1-585b-8365-bffcf7cfc3a0`. The persistent
human-test database now has two branches and two artifacts. Each branch sees
only its own artifact. SQLite integrity is `ok` and foreign-key findings are
zero.

## Regression result

- focused branch, adapter, server, pipeline-failure, and SQLite tests: 33/33;
- complete provider-free repository suite: 444/444 in 301.073 seconds;
- installed SillyTavern JavaScript syntax checks: passed.

This is still the local ordinary manual route. Live Adult ON/EX publication,
production binding, promotion, deployment, and an external handler remain
outside the active gate.
