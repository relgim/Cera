# CERA Sequence-First Release-Gate Fix V1 Result

**Status:** provider-free source freeze passed; Git publication recorded separately

**Queue:** 0061

**Predecessor commit:** `0553d02efd8e800ca562b1246d445a06944a21d6`

**Provider calls:** 0

## Validator schema

`validator_decision_json_schema()` now returns one strict, non-null object with
exactly `verdict`, `realized_sequence`, `review_flags`, and `conflict`. The
closed conflict enum matches `ConflictClass`, the Reader schema is independent,
and no Validator schema code remains after an unreachable Reader return.

Provider-free tests call the function directly, inspect the positive shape,
capture the exact schema supplied by the real Validator adapter to transport,
decode both accepted and rejected provider-shaped payloads, reject mixed branch
payloads, and retain the model-visible custody-field exclusion checks.

## Reader policy

The runtime now documents and tests the intended asymmetric policy:

- `REJECTED` is a visible Writer-candidate quality result and may open a fresh
  bounded Writer attempt.
- `INCONCLUSIVE` is a review/setup ambiguity and terminates the current run
  identity without Writer retry, Reader retry, fallback, or hidden repair.

## Stage 6 bridge

`SequenceFirstStage6Bridge` is the dedicated raw-chat entrypoint. It parses and
freezes a strict SillyTavern request, loads the branch head through
`SequenceFirstWorldTransaction.load_accepted_head()`, and constructs the typed
input to `SequenceFirstSillyTavernAdapter`.

Physical presence originates only from the accepted head or
`ExplicitSceneInitializationV1`. Raw scene-change controls without that typed
authority fail closed. Names, aliases, regexes, mentions, retrieval results,
prior responder sets, default characters, and historical cast helpers do not
establish presence or responders on this route.

The bridge executes the provider-free path:

```text
raw SillyTavern request
-> exact ingress freeze
-> accepted-head/state construction
-> SequenceFirstSillyTavernAdapter
-> persistent Planner fake
-> prose-only Writer fake
-> fresh Validator fake
-> Reader fake
-> creator-gated atomic commit
-> accepted-head reload/restart
```

Branch reconstruction accepts an inherited parent-scoped story artifact only
when an immutable, fully decoded V2 branch-materialization receipt authorizes
the exact parent, child, and accepted checkpoint. Missing or invalid fork
custody remains rejected.

## Verification

```text
exact Validator schema/transport/decode tests: 4/4 passed
focused sequence-first plus Stage 6 tests: 53/53 passed
compile/import gate: passed
complete repository suite:
  D:\CP25\source\.venv\Scripts\python.exe -m unittest discover -s tests -q
  1,180 tests
  0 failures
  0 errors
  3 skipped
  883.676 seconds
```

The integration cases cover absent-Mia mention and retrieval, silent present
Mia, ordered entry/exit, remote communication without physical presence,
ambiguous presence, explicit scene reinitialization, rejection, failed commit,
restart, and accepted-checkpoint branch materialization.

No live canary, external provider call, active-route promotion, installed
SillyTavern change, production story/database mutation, deployment, merge,
force push, or history rewrite occurred.
