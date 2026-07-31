# CODEX TASK — CERA governed stabilization cycle, checkpoint 1

Repository: `D:\AIChatBot\Cera`

## Creator instruction

Begin a repeating Codex ↔ ChatGPT Pro repair cycle. Work in small tranches of no more than three substantial progressions. After this tranche, create a tested local Git checkpoint, send ChatGPT Pro the exact review packet, and stop. Do not begin the next tranche until ChatGPT Pro has completed its review and selected the next two or three progressions.

ChatGPT Pro may reply in the connected conversation and/or write `PRO_RESPONSE.md` beside the checkpoint request. Do not demand an immediate answer, an exact acceptance token, or approval. Do not continue while waiting.

## Governing objective

Stabilize CERA before further prompt architecture, Adult activation, production binding, deployment, or broad model ladders. The present priority is truthful change control and one coherent active runtime identity.

This task authorizes **zero live provider calls**, zero production/story-world migration, zero Adult ON/EX publication, zero deployment, zero remote Git operations, zero push, zero fallback, and zero automatic retry.

## Mandatory first reading

Read, in order:

1. `D:\AIChatBot\Cera\AGENTS.md`
2. `D:\AIChatBot\Cera\docs\START_HERE.md`
3. `D:\AIChatBot\Cera\docs\handoff\CURRENT.md`
4. `D:\AIChatBot\Cera\docs\authority\CERA_OWNER_ARCHITECTURE.md`
5. `D:\AIChatBot\Cera\docs\authority\DECISIONS_AND_SUPERSESSIONS.md`
6. `D:\AIChatBot\Cera\docs\implementation\ROADMAP_AND_GATE.md`
7. `D:\AIChatBot\Cera\src\cera\providers\routes.py`
8. `D:\AIChatBot\Cera\src\cera\reasoner\codex.py`
9. `D:\AIChatBot\Cera\src\cera\reasoner_session\runtime.py`
10. `D:\AIChatBot\Cera\src\cera\reasoner\mcp_bridge.py`
11. Related provider/session/documentation tests
12. The supplied protocol text, then install it at `D:\AIChatBot\Cera\docs\authority\CODEX_PROGRESS_REVIEW_PROTOCOL.md`

Inspect all current files before editing. Preserve unrelated changes and immutable evidence.

## Prerequisite — safe Git bootstrap

CERA currently lacks Git metadata. Establish auditable local source control before the three progressions.

1. Create and verify a lossless sibling backup, outside `D:\AIChatBot\Cera`.
2. Inventory every file and directory. Classify source/authority, immutable evidence, generated output, runtime/database/log state, secret-risk material, and intentionally untracked material.
3. Do not use blind `git add .` before reviewing the inventory.
4. Do not delete, relocate, rewrite, truncate, or compress historical evidence to simplify Git.
5. Update `.gitignore` only from the explicit inventory. Preserve a tracked manifest for intentionally ignored/untracked evidence and runtime paths, including preservation location and rationale.
6. Initialize local Git. Add no remote. Do not push.
7. Create a clean baseline commit representing the pre-stabilization D-180 implementation.
8. Record the backup path, inventory/manifest path, baseline commit SHA, included paths, excluded paths, and secret scan result.

Forbidden during bootstrap: `git clean`, destructive reset, stash, checkout-overwrite, force operations, remote operations, or destructive cleanup.

If a safe baseline cannot be established, write a blocker request to ChatGPT Pro and stop. Do not continue unversioned.

## Progression 1 — strengthen the standing Codex governance document

Review `AGENTS.md` against the failures already observed in CERA: architecture churn, self-review, test-count overclaiming, stale current documentation, active identity drift, broad ladders before exact-route qualification, and feature work before creator-quality evidence.

Implement the smallest durable governance correction:

1. Install `docs/authority/CODEX_PROGRESS_REVIEW_PROTOCOL.md` using the supplied protocol, correcting only what actual repository contracts require.
2. Update `AGENTS.md` so every Codex session must read the protocol and obey the maximum-three-progression checkpoint cycle.
3. Add the protocol to `docs/START_HERE.md` mandatory every-session reading and document roles.
4. Create `.chatgpt/pro-review/README.md` and a reusable `REQUEST_TEMPLATE.md` matching the protocol.
5. Preserve Codex as technical implementer and ChatGPT Pro as an independent checkpoint reviewer. Do not let either reviewer silently grant creator authority.
6. State that a tranche may stop after one or two progressions on a terminal blocker; “three” is a maximum, not a reason to bundle unrelated work.
7. Require a clean local checkpoint commit, a real diff, and a full stop after the review request.

Acceptance:

- the protocol is mandatory and discoverable;
- no conflict exists with owner architecture or current authority precedence;
- no exact acceptance token is demanded from Pro;
- no fourth progression is permitted;
- documentation tests cover the mandatory protocol path.

## Progression 2 — eliminate the active v24/v25 and MCP identity conflict

Treat this as an active correctness defect, not documentation cleanup.

Current evidence includes:

- `src/cera/reasoner/codex.py` declares Reasoner adapter/prompt v25 and packet v14;
- `src/cera/providers/routes.py::codex_reasoner_candidate()` still reports adapter/prompt v24;
- stored-session compatibility imports v25 constants but dispatches through the v24-labeled route;
- some tests and current documentation still assert v24 and MCP v6 while D-180 describes v25 aliases and MCP v7.

Create one canonical typed active-runtime identity source, or an equally strong smallest seam, for the active local ordinary route. Codex chooses the exact implementation after inspection, but it must avoid adding another duplicate status object.

The source must bind at least:

- Reasoner model and allowed efforts;
- Reasoner adapter, packet, prompt, provider-schema, MCP/tool-contract, transport, and base-instruction identity;
- stored-session mode and compatibility inputs;
- Composer model, adapter, packet, prompt, DTO, thinking mode, and transport identity;
- verifier model, effort, adapter, prompt, request/schema, and transport identity;
- owner-architecture/privacy-profile identity where compatibility depends on them.

Make routes, stored-session compatibility, receipts/evaluation identities, health/status output, active tests, and current-status generation/validation consume the same source.

Acceptance:

- an active call cannot be labeled v24 in one layer and v25 in another;
- MCP v6/v7 drift is impossible or caught before dispatch;
- changing an active identity component changes the compatibility/route hash deterministically;
- historical v24 evidence remains immutable and readable as historical evidence;
- tests deliberately mutate one identity field and prove drift is rejected;
- no live provider call occurs.

Do not bump versions again unless the contract itself changes. Correcting stale route metadata to the already-active v25 identity is not a reason to invent v26.

## Progression 3 — make current status truthful and machine-validated

Repair only the current sources of truth. Do not rewrite historical result/evidence documents merely because they describe an older route.

At minimum inspect and correct:

- `README.md` current status;
- `docs/START_HERE.md` phase/current gate;
- the active/current section of `docs/handoff/CURRENT.md`;
- the header/current-state section of `docs/implementation/ROADMAP_AND_GATE.md`;
- health/status metadata;
- documentation validation.

Requirements:

1. Current sections must identify D-180 and the canonical active profile rather than D-165/D-177/D-179 or v24/MCP v6.
2. Current sections must state Flash non-thinking, stored-session status, current test evidence, unresolved limitations, and the absence of current full-route D-180 end-to-end qualification.
3. Historical results such as v27 Flash-thinking qualification remain unchanged and clearly historical.
4. `CURRENT.md` must lead with concise current truth. Move or clearly segregate superseded history; do not leave contradictory “current versions” later in the same active section.
5. Extend documentation/current-status validation so code/config identity drift, stale phase ID, stale thinking mode, and stale active version assertions fail tests. Link checking alone is insufficient.
6. Prefer generating or validating current status from the canonical profile rather than manually copying strings into several files.

Acceptance:

- one command produces the canonical active identity and status hash;
- current docs, health output, routes, session compatibility, and tests agree exactly;
- stale current values cause a deterministic test failure;
- historical documents remain intact;
- no live provider call occurs.

## Verification

Run focused tests after each progression. After all three pass, run at minimum:

```powershell
Set-Location "D:\AIChatBot\Cera"
& ".\.venv\Scripts\python.exe" --version
& ".\.venv\Scripts\python.exe" -m compileall -q src tests scripts
& ".\.venv\Scripts\python.exe" -m unittest tests.test_documentation -q
& ".\.venv\Scripts\python.exe" -m unittest tests.test_provider_qualification tests.test_codex_scene_reasoner tests.test_codex_stored_thread_session tests.test_native_stored_reasoner_runtime tests.test_mcp_evidence_bridge -q
& ".\.venv\Scripts\python.exe" -m unittest discover -s tests -q

git diff --check
git status --short --branch
git diff --stat <BASELINE_SHA>..HEAD
git diff <BASELINE_SHA>..HEAD -- AGENTS.md docs README.md src tests
```

Adapt test module names only when actual repository structure requires it. Record exact commands and results. Do not hide a skipped or failing test behind a total.

## Checkpoint commit and review handoff

After the three progressions:

1. Inspect the complete diff.
2. Confirm no secret, runtime database, unrelated log, or unintended historical evidence entered Git.
3. Create one local checkpoint commit:

```text
cera(checkpoint 1): establish governed stabilization baseline
```

4. Create:

```text
D:\AIChatBot\Cera\.chatgpt\pro-review\checkpoints\2026-07-31-checkpoint-001\REQUEST.md
```

5. Include:

```md
# CERA ChatGPT Pro Checkpoint Review Request
checkpoint: 001
creator_goal:
starting_baseline_sha:
ending_checkpoint_sha:
backup_and_inventory:
progression_1_governance:
progression_2_active_identity:
progression_3_current_truth:
changed_files:
diff_summary:
focused_tests:
complete_suite:
active_profile_before:
active_profile_after:
provider_calls_and_cost:
retry_and_fallback:
story_database_and_branch_effects:
user_visible_effect:
historical_evidence_integrity:
unresolved_defects:
uncertainty_and_risks:
codex_advisory_next_candidates:
questions_for_chatgpt_pro:
```

6. Message ChatGPT Pro with the exact request path and ending commit SHA. Ask it to inspect the actual diff/source/tests, judge each progression independently, write `PRO_RESPONSE.md` if useful, and choose the next two or three progressions.
7. Stop. Do not begin current-route live qualification, verifier recovery, corpus work, latency work, refactoring, Adult work, or any other fourth progression until ChatGPT Pro responds.

## Likely future work, not authorized in this tranche

These are context only, not permission:

- exact D-180 full-route qualification;
- interrupted background-verifier recovery;
- participant realization metadata correction;
- stored-thread retention/privacy policy;
- creator-labeled 20–30-turn quality corpus;
- model/effort and verifier-value comparisons;
- maintainability refactoring and CI;
- Adult ON/EX or production work.

ChatGPT Pro will select and bound the next tranche after checkpoint 001.

## Completion message

Return only a concise checkpoint summary containing:

- baseline SHA;
- checkpoint SHA;
- the three completed progressions;
- focused/full test results;
- provider/story/database effects;
- review request path;
- confirmation that work stopped pending ChatGPT Pro review.
