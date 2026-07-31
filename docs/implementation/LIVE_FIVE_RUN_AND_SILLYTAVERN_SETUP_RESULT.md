# Five-Run Live Qualification and SillyTavern Shell Result

**Date:** 2026-07-28  
**Status:** completed with 0/5 passing cases; live story routes remain unqualified  
**Authority:** D-103 through D-108

## Outcome

The requested five cases were attempted exactly once with no provider retry,
fallback, story commit, production world, or route promotion. The terminal
summary is `evaluation/evidence/live_story_qualification_2026-07-28_v1/summary.json`.

| Case | Result | Terminal boundary |
|---|---|---|
| Hana indirect memory | Failed | Codex returned a valid decision without retrieving/citing the required owner memory `H-M08`; the qualification postcondition rejected it before composition. |
| Mia + Sakura selected cast | Failed qualification assertion | The typed pipeline reached a validated transient result, but the harness required both requested NPCs to remain selected. Core CERA permits a justified responder subset. The result was not retained before that postcondition, so it is not usable prose evidence. Yuuni did not become accepted context. |
| Hana Adult ON | Failed | Python rejected an `AdultCraftNeed` whose beat set did not match the then-exact all-current-beats rule. DeepSeek was not called. |
| Hana Adult EX climax | Failed | Codex made a failed MCP evidence lookup; the transport failed closed before outcome acceptance. DeepSeek was not called. |
| Hana Adult EX toilet/material continuity | Failed | Codex made a failed MCP evidence lookup; the transport failed closed before outcome acceptance. DeepSeek was not called. |

All five records report `story_state_committed=false` and
`story_authority_writes=0`. The batch made no production database or chat
artifact. A terminal summary prevents accidental continuation or overwrite.

The original foreground watcher closed its output pipe after the first two
records, while its provider child completed the second attempt. The strict
resume mode preserved those two terminal records and attempted only cases
three through five. No case was retried.

## Corrections made from the evidence

- Adult craft selection now accepts a non-empty subset of current beats. It
  still rejects empty, future, or invented beat IDs.
- The Reasoner prompt now states the subset/current-beat rule and tells Codex
  never to guess evidence section headings.
- `CodexSceneReasonerPort` can conditionally omit MCP when Python has already
  supplied a sufficient exact dossier. The live provider receipt remains
  required; no fake bridge receipt is created.
- Tool-enabled calls remain request-bound and fail closed. A safe failed-tool
  trace records only call index, tool name, and stable error code.
- The selected-cast qualification criterion now requires a non-empty subset of
  requested present responders and separately forbids inactive-character
  leakage. It no longer confuses an eligible requested cast with mandatory
  dialogue from every member.
- The qualification script preserves existing terminal cases and refuses both
  overwrite and implicit retry.

Focused correction verification passed 53 tests. The complete repository suite
then passed 276/276, compilation was clean, documentation validation passed,
the extension passed `node --check`, and all integration JSON decoded. These
corrections have not been live-rerun; a new five-run attempt requires explicit
creator authorization.

## Advisory review

ChatGPT Pro returned the exact verdict
`CERA_LIVE_FIVE_CORRECTIONS_ACCEPTED` and reported no remaining concrete
in-scope defects. That verdict accepts only the provider-free contract,
harness, diagnostic, and prompt-boundary corrections. It does not change the
terminal 0/5 result, qualify live Reasoner or Composer behavior, establish
adult-scene or prose quality, authorize another live attempt, or promote a
route.

Pro also accepted the installed SillyTavern artifact only as a parse-verified
presentation shell. It remains intentionally non-chat-ready while the
separately gated CERA-native adapter is absent.

## SillyTavern installation

The repository source card is
`integrations/sillytavern/Hanezawa Family - Cera v1.0.json`. It was imported
through SillyTavern 1.17.0's own character import flow and produced:

```text
E:\AIChatBot\SillyTavern\data\default-user\characters\Hanezawa Family - Cera v1.0.png
```

SillyTavern's own card parser verified `chara_card_v3`, exact name/version,
embedded presentation-only system prompt, empty character book, no World Info,
and enabled local CERA extension metadata. Import created no chat/story file.

The active local extension is version `1.14.0`. Its exact CERA profile uses:

```text
name: Hanezawa Family - Cera v1.0
avatar: Hanezawa Family - Cera v1.0.png
model: cera-alpha
endpoint: http://127.0.0.1:5101/v1
fallback: none
```

It does not point to legacy Vera port 5100. No service is currently authorized
or listening as the CERA-native adapter on 5101, so the installed card is not
chat-ready. This is the intended fail-closed state.

## Readiness conclusion

CERA's provider-free contracts and installed client shell remain useful, but
the live story route is not qualified. Do not ask the creator to taste-test in
SillyTavern yet. The correction review is complete. The next story-quality
gate requires separately authorized fresh live qualification; only a passing
retained artifact set should proceed to creator prose review. Implementing and
activating the CERA-native SillyTavern adapter remains a separate authorization.
