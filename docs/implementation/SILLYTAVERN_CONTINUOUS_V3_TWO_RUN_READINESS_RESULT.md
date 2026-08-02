# SillyTavern Continuous V3 Two-Run Readiness Result

## Status

Provider-free implementation is complete for the separately governed
`2026-08-02-continuous-sillytavern-two-run-v1` campaign. No provider was called
while building or qualifying this bridge. Live dispatch remains impossible
until the exact checkpoint, Cycle sequence 21, and Job 4 authorization are
published and hash-bound.

## Implemented boundary

- Added virtual model `cera-continuous-v3-test` and a test-only adapter that
  rejects `cera-alpha`.
- Made the ordinary D-180 adapter explicitly reject the V3 test model.
- Generalized the loopback server configuration without changing its default
  `cera-alpha` identity.
- Crossed real `/v1/chat/completions`, review lookup, and review decision
  endpoints while keeping strict Accept separate from candidate preparation.
- Reused the accepted `ContinuousShadowTurnCoordinator`; no second Planner,
  Composer, Validator, persistence, or scene-change implementation was added.
- Added the fixed three-turn/two-scene fixture and exact ten-stage schedule.
- Added a fresh-process-per-run campaign runner, immutable four-run identity
  ceiling, 40-call ceiling, exact order checks, streak reset, and mandatory
  process restart between consecutive passes.
- Kept every run on a fresh disposable SQLite copy, filesystem world, branch,
  session, authority root, and provider workspace.
- Added a dedicated loopback profile for `127.0.0.1:5113`; LAN binding remains
  rejected by the server contract.

## Provider-free evidence

The focused integration gate passed **155/155** tests. It included one complete
production-shaped HTTP/review run using scripted transports:

- 3 chat-completion requests;
- 3 review lookups;
- 3 strict Accept decisions;
- 10 local stage invocations in the exact schedule;
- 0 external provider calls;
- first-turn, lean-continuation, and scene-change packets in order;
- valid Scene Summary before Turn 3;
- exact-once accepted-context synchronization; and
- unchanged ordinary SillyTavern, active-profile, branch-materialization,
  thread-lineage, continuous Job 4, documentation, and installation gates.

Compilation and `git diff --check` passed. Repository source inventory passed.
The persistent human-test database remained SHA-256
`bfbf23eb2fa7199fad38e8fb3f1ae0d6b547467f17ed68e84b7115275a2fc555`,
with SQLite integrity `ok` and zero foreign-key findings. The new test profile
SHA-256 is
`b233c03584c9ecb669af17e580972bc437975b93887aa9041c2c8e6e44731789`.

One preliminary source-inventory command used an incorrect unittest class
selector and therefore resolved to `_FailedTest`; the corrected exact selector
then passed. This was an operator command-selection error, not a product or
provider failure, and it caused no state change.

The complete provider-free repository suite is run only after the tracked
source and documentation bytes stop changing. Its exact count, duration, and
command are frozen in the checkpoint result and Cycle 21 review request, which
avoids editing tracked source after that final gate.

## Unchanged authority

- The active route remains `cera.active_runtime.d180.v1` / `cera-alpha`.
- The persistent Hanezawa human-test database is not a campaign acceptance
  target.
- V3 is not production/default activated.
- No automatic False Positive, retry, fallback, provider substitution,
  Detailer, extra verifier, or Fast mode was added.
- Completed V3 and predecessor evidence remains immutable.
- No deployment, merge, remote operation, or push is authorized.

## Live boundary

After the provider-free checkpoint is committed and Cycle 21 is published, the
campaign runner may use up to four immutable run identities and 40 calls. The
expected success path is Run 001 and Run 002, ten calls each. A failed run is
frozen immediately and cannot be retried; any repair is provider-free and a
new identity is required.

Success requires two consecutive passing runs with identical execution hashes
and a controlled adapter-process restart between them. The campaign then
leaves the persistent database unchanged, records a clean test handoff, and
stops before the 20-turn/63-call qualification.

## Live repair record

Run 001 terminally failed after its first Sol-medium Planner transport call.
Python rejected a Planner beat because one character occupied multiple role
arrays in the same scoped assertion. No Composer, Validator, story acceptance,
or persistent-database write occurred. The immutable ledger proves one call
was submitted even though the original parent campaign result omitted it.

The provider-free repair keeps that validator unchanged and makes its existing
mutual-exclusion rule explicit in Planner prompt version v12: a character that
acts, changes state, and/or speaks must use separate causally ordered beats.
It also records failed call entries before parent accounting, terminalizes
physical threads while their Codex context is still open, and can resume from
hashed immutable prior evidence at the next unused run identity. Any resumed
execution starts with a zero consecutive-pass streak and includes the earlier
call in the 40-call campaign ceiling.
