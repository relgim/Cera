# Progression 1 Result

task_id: `continuous-summary-and-actor-evidence-authority-v2`

status: completed

## Outcome

Character Summary v2 is now a Python-derived, exact-source contract rather
than free summary text. An ACTIVE summary binds stable source fields by exact
JSON pointer, revision, content hash, character, authority classification,
and derivation receipt. A Validator-derived summary must additionally bind an
exact retained Validator package and the exact ACTIVE source from which it was
derived. Stale, wrong-character, fabricated, or package-detached summaries
fail before Planner dispatch.

Evidence bindings now distinguish current source, Python mechanical
allowance, ACTIVE authority, and DERIVED retrieval context. DERIVED evidence
cannot independently support a hard decision. A beat using private evidence
must have exactly one NPC actor, and every private binding must belong to that
actor. Ted can appear as a beat actor only through exact current-user-source
authority; the mechanical connective allowance cannot create an action,
dialogue, thought, decision, movement, consent state, or new fact.

Scene Summary v2 records exact accepted-pair provenance separately for every
accepted turn. Accepted-event hashes remain optional cross-checks rather than
substitutes for the user-message/final-sequence pair.

## Verification

- ACTIVE and package-bound derived summary acceptance: passed;
- stale, fabricated, wrong-character, wrong-field, and stale-package summary
  rejection: passed;
- multi-NPC private-evidence and derived-only hard-decision rejection: passed;
- exact-source and mechanical protected-user boundary tests: passed;
- per-turn Scene Summary provenance and unchanged ACTIVE authority: passed;
- provider calls: 0.
