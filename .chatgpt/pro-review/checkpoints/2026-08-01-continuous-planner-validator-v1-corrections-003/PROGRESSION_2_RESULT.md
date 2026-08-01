# Progression 2 Result

task_id: `continuous-accepted-context-and-protected-user-authority-v3`

status: completed

## Outcome

Python now projects exact protected-user action/state and dialogue spans from
the current source. Each claim binds its source handle, source hash, exact
offsets, exact text, and kind. Rich Planner beats cite claim keys and the
underlying current-source binding. Whole-beat validation rejects protected-user
semantics without an exact supplied span whether or not Ted appears in
`actor_ids`; the mechanical connective cannot carry semantic claims.

Recent accepted same-scene context now has a request-local, receipt-bound
evidence class. It binds world, branch, accepted turn, promotion receipt,
accepted envelope, stored Planner thread, persisted session snapshot, final
synchronization receipt, and optional exact character owner. A single-NPC beat
may use its owner-bound accepted-session evidence without resending the same
complete character card. ACTIVE remains required for durable card facts,
rules, older events, and private material absent from the accepted context.

Direct MCP reads of historical `DERIVED/CharacterSummaries/...` files are
always classified as character-private and owner-bound. Provider debug may
import only exact world-read bindings, never current-source or accepted-session
handles.

## Verification

- Turn 2 from Turn 1 accepted context with no repeated character summary: passed;
- exact protected-user claim acceptance and actor/non-actor invention rejection: passed;
- owner-bound private accepted-session enforcement: passed;
- derived character-summary MCP privacy: passed;
- provider calls: 0.
