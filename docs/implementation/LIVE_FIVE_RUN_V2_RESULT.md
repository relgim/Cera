# Fresh Corrected Five-Run Live Qualification V2 Result

**Date:** 2026-07-29  
**Status:** completed with 0/5 passing cases; live story routes remain unqualified  
**Authority:** D-109 through D-111

## Authorized boundary

The creator authorized one fresh five-case batch using the Sol-medium Reasoner
and DeepSeek V4 Pro Composer. The run used a new qualification identity and
evidence directory, preserved v1, attempted every case exactly once, and made
no story commit, retry, fallback, product activation, production binding,
promotion, or deployment.

```text
qualification_id: five-run-live-v2
evidence: evaluation/evidence/live_story_qualification_2026-07-29_v2/
duration_seconds: 441.668
attempted: 5
passed: 0
failed: 5
automatic_retry_count: 0
fallback_enabled: false
story_state_committed: false
story_authority_writes: 0
preserved_existing_case_ids: []
```

The v1 summary remained unchanged at SHA-256
`2154410A40906FA9DAF67F20E4FF6242CEE058B7846BF2EF744FABA04F624A02`.
The terminal v2 summary SHA-256 is
`61D93A90AB2CF17ABD3AB31CD688C861703152BD5EBDCBFFDA4D5A5999D7F376`.

## Case results

| Case | Terminal result | Progress relative to v1 |
|---|---|---|
| Hana indirect memory | DeepSeek typed decoding rejected unsupported `SemanticInference.target=trust`. | The required `H-M08` retrieval/citation postcondition passed and the case reached composition. |
| Mia + Sakura selected cast | DeepSeek declared a realized participant with both `speaks=false` and `visibly_acts=false`; Python rejected it. | The former overstrict qualification assertion was not the failure. The rejected result cannot establish selected-context or prose quality. |
| Hana Adult ON | Codex `AdultCraftNeed` attached `character_id` to a non-character channel; typed decoding rejected it. | The corrected current-beat subset rule passed, and the exact-seed route did not fail MCP. |
| Hana Adult EX climax | Same non-character-channel owner defect. | The previous MCP failure did not recur. |
| Hana Adult EX toilet/material continuity | Same non-character-channel owner defect. | The previous MCP failure did not recur. |

No accepted prose artifact was retained. Cases one and two reached the Composer;
cases three through five stopped at Reasoner decoding. The execution path
therefore entails five Codex dispatches and two DeepSeek dispatches, but failed
typed-decoding paths currently discard the already-created safe provider
receipt. Exact per-case model, latency, token, and call receipt evidence is not
independently auditable from the terminal artifacts. This is a correction
requirement, not permission to retain raw prompts, outputs, prose, secrets, or
private evidence.

## Accepted diagnosis and correction boundary

The technical diagnosis is:

1. Keep `SemanticCategory` closed. Tighten the DeepSeek contract guidance so
   `semantic_inferences` uses exact enum values and is empty when no supported
   cross-category assertion is needed; do not add `trust` merely to accept the
   output.
2. Keep the participant validator. Every selected responding participant must
   speak or visibly act, and the provider-facing schema/prompt must state that
   boolean disjunction explicitly.
3. Keep the `AdultCraftNeed` owner validator. Dialogue and inner voice require
   a character owner; narration, sound effect, and physiology require null.
   Encode and explain that mapping provider-side.
4. Preserve privacy-safe provider receipt identity/hash/route/call evidence
   when typed decoding fails. Preserve the already-created safe context receipt
   where applicable. Retain no raw provider content or private evidence.
5. Make and validate these changes provider-free. Do not rerun v2 or weaken a
   validator to obtain a pass.

ChatGPT Pro returned the exact advisory verdict
`CERA_LIVE_FIVE_V2_DIAGNOSIS_ACCEPTED`. That verdict approves the diagnosis and
boundaries only. It grants no implementation, another live run, route
promotion, SillyTavern adapter, production binding, publication, or deployment
authority.

## Final provider-free verification

After recording the terminal evidence and verdict:

- `py_compile` passed for the qualification runner;
- documentation validation passed 2/2;
- the complete provider-free repository suite passed 276/276 in 236.778
  seconds;
- the suite's disposable 20-run fake-adapter matrix again reported zero
  failures, clean integrity/foreign keys, and zero replay adapter calls.

These checks establish only deterministic repository stability. They do not
convert a v2 live failure into a pass or establish provider prose quality.

## Readiness conclusion

The live route remains unqualified at 0/5, and the installed SillyTavern card
remains a non-chat-ready presentation shell. The next safe gate is separately
authorized provider-free correction and deterministic testing. A later fresh
live batch requires another explicit creator authorization.
