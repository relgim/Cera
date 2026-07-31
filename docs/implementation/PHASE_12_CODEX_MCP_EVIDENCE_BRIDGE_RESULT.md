# Phase 12 Codex MCP Evidence Bridge Result

**Date:** 2026-07-28  
**Status:** accepted by ChatGPT Pro within the bridge-infrastructure boundary  
**Authorization:** D-059  
**Scope:** request-bound Codex access to the existing typed evidence tools, one synthetic non-story live probe, and fail-closed receipt reconciliation

## Outcome

CERA now has a request-scoped MCP projection of `ReasonerEvidenceTools`. A runtime Codex worker can investigate beyond its seed dossier without receiving filesystem, SQLite, raw SQL, or arbitrary network access. Python still owns the immutable evidence snapshot, branch/privacy/knowledge policy, budgets, exact expansion, and every authority write.

This milestone is infrastructure qualification only. It does not implement the final typed `CodexSceneReasonerPort`, run Codex on a story or Hanezawa Genesis packet, establish character judgment, qualify a model tier, promote a route, call DeepSeek, or publish anything.

## Implemented boundary

Each reasoner invocation may create one authenticated loopback Streamable HTTP MCP server wrapping one fresh `ReasonerEvidenceTools` instance and therefore one immutable snapshot and one cumulative evidence budget. The bridge exposes exactly:

1. `cera_get_turn_snapshot`;
2. `cera_resolve_entities`;
3. `cera_search_evidence`;
4. `cera_fetch_evidence`;
5. `cera_get_character_sections`;
6. `cera_get_continuity`.

The bearer token is random and request-local, passed to the isolated Codex subprocess only through a dedicated environment-variable binding, hidden from object representations, and absent from provider and bridge receipts. The server binds only to loopback. Codex starts from a cleared global MCP configuration, receives only the request server, uses an explicit tool allow-list, cannot call tools in parallel, and fails when the required server cannot initialize.

The bridge delegates all request decoding and evidence work to existing typed contracts and service policy. It adds no alternate repository path and makes no authority decision. Search still returns references; hard evidence still requires exact fetch.

## Independent reconciliation

The bridge records an ordered in-memory dispatch trace containing tool name, request/result hashes, stable failure code, lookup receipt ID, and returned-byte count. The Codex worker independently reports observed MCP server/tool names and call status from SDK thread items. `McpEvidenceBridgeReceipt` is emitted only when the two sequences agree.

The receipt binds the provider receipt, reasoner request, snapshot, snapshot binding, public bridge binding, exact evidence IDs, cumulative byte use, fixed tool contract, and zero authority writes. It explicitly records that no credential, raw source, story prose, or private evidence was retained.

## Fail-closed behavior tested

- non-loopback MCP URLs, an unapproved token environment variable, duplicate/empty tools, and malformed binding hashes are rejected;
- unauthenticated HTTP receives `401`;
- only the six allow-listed tools are advertised;
- unknown fields, wrong typed IDs, non-object arguments, query-budget violations, stale/privacy/knowledge rules delegated to the existing evidence service, and failed MCP calls do not become model evidence;
- a Codex tool call without a binding, a missing required lookup, an unbound server, an unapproved tool, a failed call, provider/bridge sequence mismatch, model/runtime drift, and output-token excess are rejected;
- a total request MCP-call ceiling complements the existing query/fetch/depth/byte budgets and rejects repeated snapshot/tool amplification;
- no fallback or automatic retry is enabled.

## Runtime dependencies

The optional qualification extra pins:

```text
openai-codex==0.144.4
mcp==1.29.0
```

The bridge uses the MCP Python SDK's Streamable HTTP server/client implementation and the Codex app-server MCP configuration fields for a required URL server, environment-backed bearer token, enabled-tool allow-list, startup/tool timeouts, and disabled parallel calls. These dependencies remain optional rather than enlarging the provider-free base installation.

## Live probe evidence

Both attempts used only a synthetic snapshot identity and policy metadata. They contained no story, character, Genesis, adult, private-memory, or production-database content and made zero authority writes.

The first call listed the bridge tools but made no tool call. CERA rejected it because `minimum_tool_calls=1`. The probe schema had exposed the exact expected snapshot token, so Codex could satisfy the output without retrieval. This failed attempt remains evidence and was not relabeled as success.

The corrected probe removed the leaked answer and was executed as a new, separately observable call rather than an automatic retry:

| Measure | Result |
|---|---:|
| Model request | `gpt-5.6-sol`, medium |
| Provider calls in corrected probe | 1 |
| Automatic retries | 0 |
| MCP calls | 1 |
| MCP tool | `cera_get_turn_snapshot` |
| Failed MCP calls | 0 |
| Model work latency | 8,514 ms |
| Input tokens | 9,168 |
| Cached input tokens | 0 |
| Output tokens | 48 |
| Reasoning output tokens | 0 |
| Story-authority writes | 0 |
| Exact output contract | passed |

Across the milestone there were two separately initiated Codex quota calls: one preserved fail-closed probe and one corrected success. There was no recursive retry or fallback.

## Validation

With the pinned optional MCP/Codex runtime available:

```text
python -m unittest tests.test_provider_qualification tests.test_mcp_evidence_bridge -v
Ran 19 tests
OK

python -m unittest discover -s tests -p "test_*.py"
Ran 206 tests
OK
```

The full suite includes real local MCP negotiation at protocol version `2025-11-25`, unauthenticated denial, allow-list inspection, and an authenticated tool call. `compileall` completed cleanly.

## Remaining gate

Before any story-role call:

1. implement the typed `CodexSceneReasonerPort` prompt/packet and `ReasonerOutcome` parser;
2. reuse Python's existing hard citation, cast, privacy, knowledge-owner, protected-user, consent, and decision validation;
3. add deterministic adapter tests for ordinary, indirect-memory, multi-character, adult-safe-ledger, blocked, regeneration, restart, and fork cases;
4. run live development/calibration cases before touching sealed holdouts;
5. separately implement and qualify the typed DeepSeek Composer adapter;
6. conduct matched model comparisons and blinded human review before creator promotion.

Adult EX, live adult mechanics, external-handler work, production data/world binding, SillyTavern, route promotion, deployment, and final creator acceptance remain closed.

## ChatGPT Pro review

ChatGPT Pro returned `PHASE_12_CODEX_MCP_EVIDENCE_BRIDGE_ACCEPTED` with no in-scope correction. The verdict accepts the request-bound MCP evidence boundary and its evidence packet only. It does not complete the typed Scene Reasoner adapter, establish character judgment or semantic quality, qualify/promote a model, authorize story/Genesis/adult provider inputs, activate DeepSeek, bind production data, integrate SillyTavern, or authorize deployment.
