# SillyTavern Zero-Call Handoff Correction Result

**Status:** implemented, provider-free verified, and creator-authorized live retry verified
**Decision:** D-183
**Live validation:** D-184
**Active runtime identity effect:** none; D-180 remains active

## Observed failure

The fresh SillyTavern chat request at `2026-07-31T23:43:27.863653Z` failed in
`scene_reasoner` after approximately 0.356 seconds. Its failure bundle records:

- `CERA_REASONER_CONTRACT_INVALID`;
- zero external provider calls;
- zero authoritative store writes;
- no output hash, artifact, accepted reply, or story-state commit;
- an archived failed candidate checkpoint; and
- an unchanged generation-zero branch.

The exact user request was reconstructed on a disposable SQLite backup without
replaying it to a provider. Preparation produced the expected 14 exact seed
records. Prompt splitting, stable-instruction identity, provider-schema
projection, request sizing, and authenticated loopback MCP startup all passed.
The accepted stored root also resumed under the repository virtual environment
without submitting a model turn.

The original exact exception subtype is not recoverable: the old pipeline
persisted the generic failure but omitted the value-free Reasoner detail tokens.
The old running process did not expose an auditable parent/child Python-
environment identity, while CERA's pinned `openai-codex==0.144.4` and
`mcp==1.29.0` dependencies live in the repository `.venv`. This result makes
that environment binding explicit and fail-closed without claiming that it
proves the unknowable historical subtype.

## Correction

1. Reasoner envelope details are filtered to a strict value-free grammar,
   normalized into the durable uppercase audit vocabulary, and persisted in
   `TurnFailureEvidenceBundleV3`.
2. The loopback SillyTavern error response includes those safe diagnostic codes
   both structurally and in its visible error message.
3. Stored prompt split and stable-prefix mismatches now raise typed
   zero-provider transport failures with exact safe diagnostic tokens.
4. The development server refuses to start outside
   `D:\AIChatBot\Cera\.venv`, keeping the parent runtime and spawned workers on
   the same pinned dependency environment.
5. No automatic retry, fallback, story replay, provider call, branch commit, or
   route/prompt/schema change was added.

## Verification

- Focused SillyTavern/session/pipeline suite: 30/30 passed in 65.761 seconds.
- Focused provider/receipt/Reasoner suite: 52/52 passed in 11.569 seconds.
- Complete provider-free repository suite: 634/634 passed in 353.478 seconds;
  one optional live test skipped.
- Python compilation and `git diff --check`: passed.
- Exact failed story request: not sent to any provider and not committed.
- Replacement adapter health: `ok`, D-180 profile valid, stored Reasoner mode
  `branch_bound_native_stored_v1` active, Sol model present, v39 stderr clean.
- Restart verification sent no chat-completion request.

## Creator-authorized live retry

At Ted's explicit request, the pending SillyTavern message was manually
resubmitted through the real UI at `2026-08-01T00:38:43.011064Z`. This was one
creator-authorized test, not an automatic replay or fallback. The same
deterministic request ID was reused because the source turn and branch were
unchanged: `request:8c913db9-def9-520a-a67c-69c79e32ff76`.

- Scene Reasoner completed with provider-call count 1.
- Composer context assembly completed without another provider call.
- DeepSeek Scene Composer completed with provider-call count 2.
- Sol realization verification completed with provider-call count 3.
- The complete reply rendered in SillyTavern with its Codex sequence plan and
  reached `review_ready` as
  `review_packet:664d5e0a-7e31-5d4d-8e9c-0286403879be`.
- No retry, fallback, provider substitution, or recursive repair occurred.
- The creator did not accept the candidate. Branch
  `branch:1adc9177-cf6d-5a09-ab11-ea155442832f` remains active at generation
  zero with a null head artifact, so the test wrote no accepted story canon.

The correction is therefore verified across the actual SillyTavern-to-Codex-
to-DeepSeek-to-verifier route. This proves the specific repaired ordinary turn,
not universal route reliability, production readiness, or creator acceptance
of the prose.
