# Progression 1 Result

task_id: `continuous-ingress-claims-and-final-candidate-enforcement-v5`

status: completed

## Outcome

The continuous request now requires an ingress-owned, gap-free exact source-unit
ledger. Action/state units have one explicit actor, dialogue units one explicit
speaker, and narration/instruction units grant no character authority. Only
exact Ted-owned units become protected-user claims; the previous bounded wording
heuristic has been removed.

DeepSeek returns exhaustive gap-free story segments with exact output bytes,
actors, subjects, dialogue speaker, semantic kind, and claim ownership. Python
rejects invented or paraphrased Ted authorship, incomplete segment coverage,
changed bytes, and disagreement between protected realization and segment
ledgers.

Validator final fields cite exact Composer segments and retain their derived
actors, subjects, visibility/owner, and claim sets. A Ted-authored final field
must equal an exact ingress claim. Deterministic tests prove that a valid quote
cannot mask an added Ted movement in the final sequence, event summary, or
protected edit.

## Verification

- explicit positive/negative ingress classifications and gap rejection: passed;
- Composer exact occurrence, paraphrase, hidden-Ted, gap, and byte checks: passed;
- final/event/edit protected-user extension rejection: passed;
- provider calls: 0.
