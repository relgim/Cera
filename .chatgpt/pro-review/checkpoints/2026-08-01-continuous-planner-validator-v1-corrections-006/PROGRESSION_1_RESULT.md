# Progression 1 Result

task_id: `continuous-ingress-receipt-and-protected-role-authority-v6`

status: completed

## Outcome

Continuous turns no longer accept caller-authored source classifications. A
Python-owned `ContinuousIngressAuthorityStore` issues an immutable receipt
bound to world, branch, session, request, turn, idempotency hash, exact raw
source hash, protected-user identity, authority-adapter identity, and the
ordered source-unit ledger. Runtime requests carry only the receipt reference;
the coordinator resolves and revalidates the authoritative bytes before any
provider boundary.

The assertion model now separates action, state, and dialogue owners from
affected, addressed, observing, and referenced characters. Ted in any owning
role requires an exact supplied claim. An NPC may act toward or address Ted
without creating Ted's response, thought, feeling, consent, or decision.

## Verification

- receipt, raw-source, branch, session, request, turn, idempotency, and hash
  substitution rejection: passed;
- protected action, private-state, consent/decision, and dialogue ownership
  rejection without an exact claim: passed;
- NPC action addressed toward Ted through a non-owning role: passed;
- duplicate or mislabeled owner/non-owner roles: rejected;
- provider calls: 0.
