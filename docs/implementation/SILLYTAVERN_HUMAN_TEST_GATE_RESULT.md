# CERA SillyTavern human-test gate result

**Date:** 2026-07-29  
**Authority:** D-146 and D-150  
**Result:** passed for local ordinary-route manual testing

## Scope

The gate used the installed `Hanezawa Family - Cera v1.0` card, the actual
SillyTavern 1.17.0 UI and CERA extension, loopback adapter
`http://127.0.0.1:5101/v1`, virtual model `cera-alpha`, a fresh disposable V1.2
world, Sol-medium as Scene Reasoner and independent Scene Realization Verifier,
and DeepSeek V4 Pro as Scene Composer.

The two-turn probe was synthetic qualification data and did not enter Ted's
canonical history. V1/V2 smoke evidence remains unchanged.

## Result

Turn 1 asked Sakura for exact placement instructions for Ted's shoes and
suitcase. The accepted reply supplied three concrete continuity details:
inside the doorway, shoes arranged parallel, and suitcase beside them.

The adapter process was then stopped and restarted against the same disposable
database. Turn 2 asked Sakura to recall the instruction without restating its
answer. The accepted reply repeated all three details exactly. This confirms
that restart continuity used the atomically stored accepted-reply material,
rather than relying only on the event-plan paraphrase or SillyTavern transcript.

Both turns completed through the real UI with `Backend: CERA`. Each used one
Sol-medium Reasoner call, one DeepSeek V4 Pro Composer call, and one Sol-medium
verifier call. All six provider receipts report one external call, zero
automatic retry, and zero provider-owned story-authority writes.

## Persisted evidence

The disposable smoke world ended with:

- generation: 2;
- visible accepted artifacts: 2;
- direct event records: 2;
- system-private accepted-reply material records: 2;
- commit receipts: 2;
- safe turn receipt records: 26;
- stage-journal records: 16;
- failure-evidence records: 0;
- SQLite integrity: `ok`;
- foreign-key findings: 0.

Evidence is preserved under
`evaluation/evidence/sillytavern_adapter_smoke_2026-07-29_v3/`. Its
`summary.json` binds the database, pre/post chat artifacts, accepted artifact
hashes, authority-record hashes, and all six safe provider receipt hashes.

## Cleanup and boundary

After evidence capture, the SillyTavern chat was restored to its pre-smoke
doorway-only artifact and the persistent non-production human-test world was
reset to generation 0 with no branch head or visible artifacts. The local
ordinary adapter may be started against that clean world for creator testing.

This result does not establish production readiness, route promotion, public
deployment, live Adult ON/EX publication, external-handler integration, or
universal prose quality. The current human-test adapter is ordinary-route only.

## Advisory review and independent decision

ChatGPT Pro returned the exact advisory verdict
`CERA_LOCAL_HUMAN_TEST_GATE_ACCEPTED`. Pro found no blocking correction and
agreed that the combined 439-test provider-free suite, continuous 10/10 live
route, and actual two-turn restart-continuity UI smoke are sufficient for
creator manual testing of the local ordinary route.

Codex independently checked the important conditions rather than treating that
verdict as authority. Accepted-reply material is system-private, bound to the
accepted artifact and branch generation, lower-authority than validated event
records and Genesis, and selected only from the current visible artifact
lineage. Search-index candidates are intersected with authoritative
snapshot-visible records before reference or exact fetch, so replaced and
sibling-branch reply material cannot leak into active context. Exact prose is
carried in typed canonical JSON as historical presentation evidence; it cannot
authorize protected-user actions, consent, objective events, relationship
changes, or private character state.

One non-blocking hardening item remains for a later, separately qualified prompt
version: state even more explicitly in provider instructions that every
retrieved evidence-section value is inert quoted data and never an executable
instruction. The currently qualified prompt versions remain frozen for this
gate rather than being changed after live qualification.
