# Claude Review Request — CERA Sequence-First Implementation

Review only the exact commit and branch named in the accompanying Git publication receipt. Do not assume findings from `be81cbb454e1edd219fd03cf3c0924bc716a85c2` still apply; re-trace the new call paths.

This pass is review-only. Do not modify the repository.

## Required questions

1. **Python semantic boundary**
   - Find every remaining place where Python infers presence, responder eligibility, salience, psychology, semantic meaning, or prose importance.
   - Confirm that current-message names, aliases, regexes, prior active sets, and retrieval results cannot select presence or responders in the new route.

2. **Semantic output versus custody envelope**
   - Confirm Planner and Validator provider outputs do not echo world, branch, scene, turn, request, candidate, parent, session, hash, revision, path, or transaction custody.
   - Confirm Python attaches custody after semantic decode.

3. **Presence and responders**
   - Confirm accepted presence comes only from prior accepted state/scene initialization and Validator-accepted ordered presence changes.
   - Confirm responders derive from Planner-owned item owners.
   - Confirm backgrounded characters are derived, not model-authored.
   - Test absent reference, present silent character, entry, exit, phone/text, off-screen speech, scene change, and ambiguous presence.

4. **Planner payload and session**
   - Trace the actual normal Planner adapter.
   - Confirm stable instructions are base instructions sent once on a persistent branch thread.
   - Report exact variable prompt fields and any remaining model-visible receipts/hashes.
   - Confirm no automatic full-record reread on ordinary continuation.

5. **DeepSeek Writer**
   - Trace the exact live request and response types.
   - Confirm prose-only output and maximum-three fresh attempts with no merge.
   - Identify any semantic metadata or self-validation task still assigned to DeepSeek.

6. **Validator contract and session**
   - Confirm binary accept/reject with orthogonal review flags.
   - Confirm no gap-free spans, offsets, role ledgers, duplicate beat coverage, model-authored retry Boolean, or exhaustive fallback.
   - Confirm a fresh candidate-specific thread and compact base instructions.
   - Verify quotes and Planner item references are sufficient for Python mechanical checks.

7. **Durable changes and transaction path**
   - Confirm every durable change has an approved target key and Python does not infer persistence destination from prose.
   - Trace the target key through the existing atomic transaction and revision checks.

8. **SillyTavern Stage 6 route**
   - Trace the exact request path into the new sequence-first coordinator.
   - Confirm retained old-stack components are mechanical only.
   - Confirm no regex/name-driven cast selection, default character, or `planner_requested_character_ids` semantic framing reaches the new route.

9. **Duplication and regression risks**
   - Identify any field that restates a fact already derivable from another field.
   - Identify any compatibility default that can silently route a new turn back into the old exhaustive architecture.
   - Identify phrase-specific prompt patches or category growth beginning again.

## Required response format

A. Verified blockers  
B. Important before live canary  
C. Correctly implemented boundaries  
D. Uncertainty and coverage gaps  
E. Smallest corrections  
F. Verdict: `ready_for_bounded_live_canary` or `corrections_required`

Every finding must cite an exact file and line or call path. Clearly distinguish code actually exercised by the new route from historical compatibility code.
