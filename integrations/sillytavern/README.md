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

The card is installed and parse-verified as `chara_card_v3`. The current
repository entrypoint exposes the full-model CERA route on loopback port 5101
through virtual model `cera-alpha`. Python resolves the accepted branch's
ordinary/adult logic owner before any provider dispatch; the two explicit
route model IDs remain compatibility/test surfaces only. There is no Vera or
provider fallback.
SillyTavern's authenticated, CSRF-protected server owns a narrow review relay,
so the same UI works at `127.0.0.1:8000`, `192.168.0.202:8000`, and from a
same-network phone without publishing port 5101 to the LAN. The relay exposes
only health, exact review lookup, and typed creator-decision paths; it cannot
proxy an arbitrary CERA URL.

For `cera-alpha` and the two explicit Pi Scene compatibility model IDs, the
installed SillyTavern request path forwards typed `cera_profile_id`,
`cera_session_id`, scene depth, character autonomy, prompt handling, reasoning
effort, optional regeneration identity, `cera_adult_craft_mode`, and per-chat
ordinary `cera_review_mode` (`automatic` or `manual`, default `automatic`). Adult
craft mode selects retrieval breadth only; it never chooses the ordinary or
adult route. CERA treats these fields as the primary transport contract.
Hidden `[[CERA_*]]` prompt markers remain a legacy
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

Start the full-model adapter from a clean runtime directory with a local bearer
token of at least 24 characters:

```powershell
$env:CERA_PI_SCENE_TOKEN = '<local-random-token>'
.\.venv\Scripts\python.exe scripts\run_pi_scene_lean_server.py serve `
  --runtime-root D:\Cera\runtime\human-test `
  --port 5101 `
  --sol-ceiling <authorized-sol-limit> `
  --deepseek-ceiling <authorized-deepseek-limit>
```

At startup the adapter prints the endpoint, active model/profile, and the exact
human-readable debug directory. The normal path is
`<runtime-root>\debug\readable`; open `LATEST.md` for the newest operation or
`INDEX.md` for the chronological list. Exact adult diagnostics remain locally
isolated under `PROTECTED_ADULT` and must not enter ordinary model context,
Git, or ordinary log exports.

Start SillyTavern normally. Server plugins must remain enabled with automatic
plugin updates disabled in the development installation. On another device on
the same trusted network, open:

```text
http://192.168.0.202:8000/
```

The PC's LAN address can change after a router or DHCP change. When it does,
use the PC's current IPv4 address with port 8000; the browser review transport
itself is same-origin and needs no CERA endpoint change.

Adult Off/On/Ex controls select only the breadth of craft/example retrieval.
They never select the logic owner. Accepted branch state controls automatic
ordinary/adult routing; the adult route uses the protected Adult Scene and
Adult Filter pipeline. This remains a manual-development gate, not a public
deployment or production route-promotion claim.

The current client also installs the CERA creator-review extension. Ordinary
Writer prose is displayed immediately with a provisional badge. Ordinary
candidates then require independent Luna, Codex Reader, and deterministic Python
custody/privacy checks. Adult stays on its synchronous Adult Filter path, with
the deterministic Python gate enforced by the backend, and never runs or
exposes Codex Reader. In the default Automatic mode, only
the backend may mark the message canonical after every route-required check
passes and a durable acceptance receipt is returned. Manual Review pauses a
fully passing candidate for creator Accept, Regenerate, or Decline. The setting is stored per
SillyTavern chat. This toggle does not change Adult behavior: Adult Filter stays
synchronous and its existing creator-review rejection path remains in force.

Hard-rejected V3 candidates remain visible and noncanonical with only concise
frozen failures and backend-authorized Regenerate, Decline, and auditable
Override actions. Legacy v1 keeps its existing Replan behavior; V3
does not expose Replan in this release. A blocked technical lane or an
inconclusive gate exposes no creator acceptance/override action in this release.
If Luna or Reader rejects while another required lane is still pending, the
known concise failure is visible but all creator actions remain disabled until
the backend publishes the joined `review_ready` result.
Override is never available for a failed Python gate. An accepted override
retains the original Luna/Reader verdicts in the message audit panel. Adult
Filter override remains disabled unless the backend explicitly authorizes it.
Rejected/provisional text is excluded from accepted continuity and ordinary
exports except for an exact D-220 standing-policy provisional acceptance. That
ordinary-only output uses the first candidate, makes no second Writer call or
creator POST, retains the rejected Luna/Reader evidence, and carries a
hash-bound `cera.pi_scene.ordinary_policy_acceptance_audit.v1`. Any hard signal
wins, and Adult remains strict. Creator decisions make no hidden
scene-regeneration call. Review GET exposes creator
guidance only as the exact hash-bound
`cera.pi_scene.creator_guidance_projection.v1`; raw feedback is sent once in
the explicit decision POST and is never stored or replayed by SillyTavern.
Auditable Override requires a nonempty creator reason; an empty editor performs
no POST, and only the backend's hash projection may return to the chat.
The output-only V3 `automatic_accept` identity is rendered only from a durable
backend decision; the browser never exposes or submits it as a creator action.
The same applies to V3 `standing_policy_accept_provisional`: the browser may
render/reconcile it but cannot originate, alter, or replay it.

The readable ordinary-review V3 JSON schemas are the current contract source;
V2 is historical. The
deterministic generator produces the Python and staged JavaScript validators;
the extension and relay import those generated modules instead of maintaining
separate handwritten V3 contract copies.

The full-model UI metadata dispatch contract is documented in
[`CERA_FULL_MODEL_COMPLETION_METADATA_BRIDGE.md`](CERA_FULL_MODEL_COMPLETION_METADATA_BRIDGE.md).
The extension projects all CERA completions into a collapsed decision panel,
but an installed `openai.js` receives the widened bridge only at an explicitly
authorized installation-sync boundary. Raw protected adult fields are never
queued or duplicated by that bridge.

The repository extension and review relay implement the generic provider-stage
Retry contract for `eligible`, `in_progress`, `succeeded`,
`blocked_ambiguous`, `attempts_exhausted`, and
`recording_repair_required`, plus the distinct non-Retry terminal
`recovery_required`. The same-origin boundary is the closed
`cera.provider_stage_retry_status_envelope.v1`: it pairs the generated status
DTO with zero or one generated backend-issued action DTO. The action identity,
chain identity, expected chain hash, and Retry ordinal must all match before a
control is rendered; the browser never derives them.

The compact panel keeps hashes, schema versions, provider-operation accounting,
correlation identity, and browser-observed advisory counts behind the literal
`Technical details` disclosure. `blocked_ambiguous` shows only `Check Status`,
which performs the provider-free GET. `recording_repair_required` preserves the
accepted story, but does not by itself authorize a button: the accepted
review's exact `actions.repair_recording_enabled` authority must also be true.
That parent-authenticated, non-Retry repair may return a generic Recorder-stage
status for its one successor occurrence; the UI then preserves that exact
status/action authority and never invents another repair chain. A normal
Retry click posts the exact backend-issued action DTO once. Regenerate retains
its separate v2 review DTO and counter; Replan remains a separate legacy v1
review action rather than a provider Retry.
`recovery_required` is read-only and offers no generic action; it never offers
Retry or redispatch. Recording repair clears its immutable terminal
envelope only after the separate review authority returns recording status
`complete`; it does not synthesize a generic Retry success.

The fixed relay paths are GET
`/api/plugins/cera-review/v1/cera/provider-stage-retries/:chainId` and POST
`/api/plugins/cera-review/v1/cera/provider-stage-retries/:chainId/actions/:actionId`.
Check Status uses GET only. The backend owns the three-attempt/two-action limit;
browser counts are advisory defense-in-depth and never create authority. No raw
prompt, response, provider exception, protected prose, or local path is retained
in the persisted UI status.

Review lifecycle GET uses the closed `cera.pi_scene.review.v3` projection. Its
`checks` object has independent `luna`, `reader`, `adult_filter`, and `python`
lanes. This V3 resource is ordinary-only: Luna, Reader, and Python are required,
and Adult Filter is explicitly not applicable. Adult stays on the existing
synchronous v1 creator-review projection and has no Reader lane. Each
provider-backed required lane may carry its own complete generated
status envelope and backend-issued action, so concurrent Luna and Reader
failures cannot overwrite each other. Python never carries a provider action.
The extension persists only the closed review projection, action identities,
hashes, and a hash of the displayed message. On reload it reconciles with GET,
blocks on review/candidate/message drift, and never derives acceptance from
client state. The Adult path does not create a second browser copy of protected
story prose.

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
