# SillyTavern CERA shell

The additive Continuous V3 ordinary manual-test profile and exact provider-free
operations are documented in
[`CONTINUOUS_V3_MANUAL_TEST_ROUTE.md`](../../docs/operations/CONTINUOUS_V3_MANUAL_TEST_ROUTE.md).
It uses a separate Custom Endpoint preset on loopback port 5114 and does not
replace or mutate the installed `cera-alpha`/D-180 preset.

`Hanezawa Family - Cera v1.0.json` is the repository-owned character-card
source. It is intentionally a thin presentation shell: typed Genesis, selected
participants, memory retrieval, decisions, adult routing, and accepted prose
remain CERA-owned and are not duplicated into SillyTavern lore or World Info.

## Installed development state

- SillyTavern root: `E:\AIChatBot\SillyTavern`
- Imported card: `data\default-user\characters\Hanezawa Family - Cera v1.0.png`
- Card SHA-256: `3779ecf0096a30b88fdc1ddcbf185842c268a149c87ec1f6a48a824815fe2fce`
- Source JSON SHA-256: `f467b2e8921fc6ab16933fa51fd8f70b9d668b23eb6b15dc9e8a20110bb81eb5`
- Extension profile: `Seraphina-Development-Memory` version `1.14.0`
- CERA story endpoint: `http://127.0.0.1:5101/v1`, used only by the
  SillyTavern server
- Browser review endpoint: same-origin `/api/plugins/cera-review/v1`
- Virtual model: `cera-alpha`

The profile matches the exact card name or imported PNG avatar. It never points
at Vera's port 5100 and has no automatic fallback. The host memory prompt is
cleared while CERA is selected so Sera/Vera summaries cannot become CERA truth.

The card is installed and parse-verified as `chara_card_v3`. The CERA-native
OpenAI-compatible development adapter is implemented and qualified for local
ordinary/relationship human testing on port 5101. CERA remains loopback-only,
uses virtual model `cera-alpha`, and has no Vera or provider fallback.
SillyTavern's authenticated, CSRF-protected server owns a narrow review relay,
so the same UI works at `127.0.0.1:8000`, `192.168.0.202:8000`, and from a
same-network phone without publishing port 5101 to the LAN. The relay exposes
only health, exact review lookup, and typed creator-decision paths; it cannot
proxy an arbitrary CERA URL.

For `cera-alpha`, the installed SillyTavern request path forwards typed
`cera_session_id`, `cera_scene_depth`, and optional
`cera_regeneration_key` fields. CERA treats these fields as the primary
transport contract. Hidden `[[CERA_*]]` prompt markers remain a legacy
compatibility path only; the current client does not inject them into story
prompts, and conflicting typed and legacy-marker values fail closed.

The installed extension preserves a regeneration identity from
`GENERATION_STARTED` through request-settings assembly even if SillyTavern
emits `CHAT_CHANGED` in between. It clears the identity immediately after the
request is assembled, so a later append cannot inherit a stale regeneration
key. CERA independently excludes the candidate head being replaced from the
new sibling's seed continuity.

Each SillyTavern chat identity resolves to its own deterministic CERA root
branch. Starting `New Chat` therefore starts from Genesis without inheriting
another chat's accepted artifacts or memory. Exact accepted-head transcript
matching preserves an existing chat if an older client session-key format
changes, but ambiguous transcript matches fail closed rather than merging
branches.

Start or reset the clean non-production world with:

```powershell
.\.venv\Scripts\python.exe scripts\reset_human_test_world.py --replace
```

Start the adapter with:

```powershell
.\.venv\Scripts\python.exe scripts\run_cera_sillytavern_server.py
```

Start SillyTavern normally. Server plugins must remain enabled with automatic
plugin updates disabled in the development installation. On another device on
the same trusted network, open:

```text
http://192.168.0.202:8000/
```

The PC's LAN address can change after a router or DHCP change. When it does,
use the PC's current IPv4 address with port 8000; the browser review transport
itself is same-origin and needs no CERA endpoint change.

The live development adapter currently supports the validated ordinary route.
Adult ON/EX catalog and routing contracts remain provider-free and are not yet
published through this human-test adapter. This is a manual-development gate,
not a production route, public deployment, or route-promotion claim.

The current client also installs the CERA creator-review extension. DeepSeek's
structurally valid reply is shown as provisional while Sol performs an
independent review. The panel then presents the Codex sequence, Sol
severity/reason, and Accept, Correction/Adjustment, DeepSeek rewrite, Codex
replan, and Decline actions. Provisional text is excluded from exports and
durable story state. Accept makes no provider call and publishes the prepared
package atomically.

## Verification

The installed PNG was parsed with SillyTavern's own
`src/character-card-parser.js`. Verification established:

- exact name and version;
- empty character book and no attached legacy World Info;
- the CERA extension flag is enabled;
- the presentation-only system prompt is embedded;
- importing the card did not create a chat/story file.

The 2026-07-29 v3 UI smoke additionally established:

- the raw user cue crossed the actual SillyTavern extension and port-5101
  adapter into the live Reasoner/Composer/verifier chain;
- each of two turns used one Sol-medium Reasoner call, one DeepSeek V4 Pro
  Composer call, and one independent Sol-medium verifier call;
- the first accepted reply was published atomically with a direct event and a
  system-private exact accepted-reply material record;
- after stopping and restarting the adapter, the second turn retrieved and
  repeated the first reply's exact placement details;
- the disposable smoke world ended at generation 2 with two accepted artifacts,
  two commit receipts, no failure evidence, `integrity_check=ok`, and no
  foreign-key findings;
- the user-facing chat and persistent human-test database were reset to the
  original V1.2 doorway state after evidence capture.

The creator's first manual turn later exposed and verified the typed-control
compatibility correction. The original saved cue completed through the real UI
with no API error and atomically advanced the persistent human-test world to
generation 1. See
[`SILLYTAVERN_API_ERROR_CORRECTION_RESULT.md`](../../docs/implementation/SILLYTAVERN_API_ERROR_CORRECTION_RESULT.md).

The later New Chat probe exposed and corrected cross-chat branch reuse. The
original and second creator chats now occupy independent generation-1 branches,
and the complete provider-free suite passes 444/444. See
[`SILLYTAVERN_NEW_CHAT_BRANCH_CORRECTION_RESULT.md`](../../docs/implementation/SILLYTAVERN_NEW_CHAT_BRANCH_CORRECTION_RESULT.md).

The D-157 sequence-depth correction carries typed `cera_scene_depth` through
raw ingress and the Scene Reasoner request. Auto remains adaptive; Long is a
full-supported-chain obligation; Epic is a maximum-supported-progression
obligation. These modes do not create word or beat quotas and never authorize
padding or protected-user invention. Runtime Codex owns the causal sequence;
DeepSeek realizes it through Composer DTO v6, while Python derives realization
owners from validated authorities.

The card remains deliberately thin. On an empty branch, Python seeds the exact
creator-owned V1.2 story-start scenario. After participant selection, CERA
selects the relevant character's internal speech-system evidence and gives it
to DeepSeek as decoded structured context; illustrative lines are voice
references rather than scripts. Inactive characters are still omitted.

The earlier live comparison accepted a 274-character one-beat greeting and a
2,540-character four-beat Mia/Sakura sequence. See
[`SEQUENCE_DEPTH_PROMPT_CORRECTION_RESULT.md`](../../docs/implementation/SEQUENCE_DEPTH_PROMPT_CORRECTION_RESULT.md).

The D-165 UI smoke corrected a shared render-lifecycle issue in which metadata
arrived before the assistant message DOM existed. The client now keeps a
bounded provisional-only metadata queue, stores completion metadata at message
receipt, renders after the character message exists, and recovers after reload.
A provider-free lifecycle probe and a real Sol/Flash/Sol UI turn passed. The
real Accept advanced the disposable branch exactly once with zero provider
calls/retries. The full repository suite passed 497/497, including installed
bridge equivalence and provisional-export checks. See
[`BEHAVIORAL_CONSOLIDATION_AND_HUMAN_TEST_GATE_RESULT.md`](../../docs/implementation/BEHAVIORAL_CONSOLIDATION_AND_HUMAN_TEST_GATE_RESULT.md).

See
[`SILLYTAVERN_HUMAN_TEST_GATE_RESULT.md`](../../docs/implementation/SILLYTAVERN_HUMAN_TEST_GATE_RESULT.md).

## Native stored Reasoner and Sol effort control

Extension v1.3.0 adds a `Sol` selector to the CERA control bar:

| UI | Request value | Owner |
|---|---|---|
| M | `medium` | Scene Reasoner only |
| H | `high` | Scene Reasoner only |
| Ex | `xhigh` | Scene Reasoner only |

The independent realization verifier remains Sol-medium. The selector is sent
as `cera_reasoning_effort` through SillyTavern's same-origin relay. CERA rejects
unknown values. Effort is part of the stored-session compatibility identity, so
changing it rotates/reconstructs from the accepted Python checkpoint rather
than continuing a thread created under a different effort.

The local health response must report `reasoner_session.active=true` and
`reasoner_session.mode=branch_bound_native_stored_v1` before a human test. See
[`NATIVE_STORED_REASONER_ACTIVATION_RESULT.md`](../../docs/implementation/NATIVE_STORED_REASONER_ACTIVATION_RESULT.md).
