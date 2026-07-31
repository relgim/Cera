# Codex Task
Run ID: 2026-07-31T211941Z-cera-shared-repository-overlapped-pro-codex-review-loop-v2
## Objective
Continue from the current CERA branch and replace the Downloads/manual-relay design as the primary workflow with a shared-repository, overlapped ChatGPT Pro–Codex review cycle. Codex must publish the completed results of authorized progressions 1–3 into the repository, immediately perform an already-defined and already-authorized fourth job while ChatGPT reviews, then read ChatGPT's identity-bound response directly from the repository when job 4 completes. Codex must apply that review to the next 1–3 cycle and publish a new package containing both the previous job-4 result and the current/revised 1–3 results. Ted must not upload, download, rename, copy, start Wait/Import, or relay messages between the agents during an ordinary cycle.
## Context Summary
Current repository: D:\AIChatBot\Cera. Current branch: feature/pro-review-file-bridge-v1. Current HEAD: 3fd392d942c6c3d796a244c4cd679405142497ea. The existing V1 bridge is verified but is only a manual emergency fallback: it exports to Downloads and requires Ted to transport the response. The creator clarified the intended cadence: Codex finishes jobs 1–3, makes their review package available to ChatGPT, and immediately starts its pre-authorized job 4. ChatGPT normally finishes reviewing before Codex finishes job 4 and writes the response directly into the shared CERA repository through the repository connector. At the end of job 4, Codex checks and consumes that response, then works on the next jobs 1–3. At the following handoff, Codex supplies the completed previous job 4 plus the new/revised jobs 1–3, and begins the next pre-authorized job 4. The fourth job is a latency-hiding buffer, not a task invented while waiting. My earlier interpretation requiring direct real-time orchestration was wrong. Do not require Ted in the middle. Also do not falsely claim that filesystem transport itself can wake an inactive ChatGPT conversation: inspect the actual available trigger/integration. If no genuine no-user-action trigger exists, identify that exact platform boundary and do not label the whole loop fully autonomous; still complete the robust shared-repository handoff and preserve a clean seam for a supported trigger.
## Inspect First

- tools/pro_review_bridge.ps1
- tests/test_pro_review_bridge.py
- docs/operations/PRO_REVIEW_FILE_BRIDGE.md
- docs/implementation/PRO_REVIEW_FILE_BRIDGE_V1_RESULT.md
- docs/authority/CODEX_PROGRESS_REVIEW_PROTOCOL.md
- .chatgpt/pro-review/README.md
- .chatgpt/pro-review/REQUEST_TEMPLATE.md
- .chatgpt/pro-review/checkpoints/2026-07-31-checkpoint-001/REQUEST.md
- .chatgpt/pro-review/checkpoints/2026-07-31-checkpoint-001/TASK4_RESULT.md
- AGENTS.md
- docs/START_HERE.md
- docs/handoff/CURRENT.md
- docs/implementation/ROADMAP_AND_GATE.md

## Allowed Paths

- tools/**
- tests/**
- docs/**
- .chatgpt/pro-review/**
- .chatgpt/codex-runs/**
- AGENTS.md
- README.md

## Forbidden Paths

- runtime/**
- .tmp/**
- .venv/**
- **/*.sqlite
- **/*.db
- **/credentials/**
- **/*secret*
- src/cera/**
- evaluation/evidence/**
- adult/provenance/**
- genesis/provenance/**
- genesis/cera_authority/**

## Implementation Scope

Include:
- Treat the current V1 Downloads bridge as a clearly labelled manual emergency fallback, not the primary workflow.
- Define and implement a repository-local mailbox/state-machine contract for review requests and responses, with deterministic per-cycle paths, checkpoint/cycle IDs, Git object IDs, evidence hashes, task identities, and exact expected filenames.
- Use atomic publication and stable-read checks, fail-closed identity validation, idempotent retries, stale/duplicate/conflicting-response rejection, crash recovery, and privacy-safe receipts.
- Represent the overlap explicitly: publish jobs 1–3; transition immediately to the already-authorized job 4; after job 4, consume the matching Pro response; then transition to the next jobs 1–3. Never invent a fourth job while waiting.
- Require every job 4 to be named, scoped, and authorized before the preceding 1–3 package is published. If no authorized job 4 exists, stop instead of fabricating work.
- Make the next review package contain the result/evidence of the preceding job 4 plus the current or revised jobs 1–3, with provenance that distinguishes them.
- Allow ChatGPT Pro to write the response directly into the repository through the approved connector. The primary flow must not depend on Downloads, manual renaming, or Ted running Wait/Import.
- Provide a bounded safe behavior when the response is unexpectedly not ready after job 4: automatically poll the exact repository path or perform only another separately pre-authorized independent task. Do not request ordinary relay work from Ted and do not invent work.
- Inspect whether a supported mechanism in the installed environment can actually trigger or keep the ChatGPT review active after Codex publishes. Prove any such mechanism before relying on it. If none exists, document the precise remaining trigger limitation without weakening or misrepresenting the repository handoff.
- Add adversarial tests and update all active governance, operations, handoff, and result documentation so the stated workflow matches actual behavior.
- Preserve current untracked checkpoint evidence, do not rewrite historical evidence, and keep all provider/story/database/route/deployment effects at zero.
- Create a local commit on the existing feature branch after review and verification; do not merge, push, deploy, or change remotes.

Exclude:
- No browser/UI automation against ChatGPT.
- No OpenAI API/provider call merely to simulate ChatGPT Pro.
- No substitution of a Codex persona, subagent, local model, static canned response, or test fixture for the actual ChatGPT review in claims about production behavior.
- No active CERA route, model, prompt, schema, story, database, Adult, Genesis, SillyTavern, or deployment changes.
- No automatic approval of ChatGPT recommendations and no conversion of an imported review into creator authority.
- No deletion or mutation of Checkpoint 001 evidence artifacts except additive state/receipt files explicitly required by the new protocol.

## Acceptance Criteria

- The documented primary workflow requires only one initial creator instruction and no Ted-operated upload, download, rename, copy/paste, Wait, or Import action during an ordinary cycle.
- Codex can publish an identity-bound jobs-1–3 package, enter a pre-authorized job-4 state, and later consume only the exact matching repository response.
- A response for the wrong checkpoint, cycle, Git SHA, evidence hash, or task set is rejected without changing progression state.
- Partial writes, unstable files, stale responses, duplicate identical responses, conflicting responses, interrupted consumption, and restart recovery are tested.
- The state machine proves that job 4 was authorized before publication and that no unrelated work can be invented while waiting.
- The next package demonstrably includes both the prior job-4 result and the current/revised jobs-1–3 result with separate provenance.
- The manual Downloads bridge remains functional as an emergency fallback but is not presented as the normal operating path.
- Any claim of fully autonomous no-user-action ChatGPT triggering is backed by an actual isolated end-to-end proof. If that trigger cannot be implemented with the installed supported interfaces, the result clearly labels only that trigger as unresolved and does not falsely call the system end-to-end autonomous.
- PowerShell parsing, Python compilation, focused tests, full provider-free suite, and git diff checks pass.
- Provider calls, story/database writes, active route changes, deployment, remote Git operations, and pushes remain zero.
- Codex records the final branch, local commit, changed files, tests, exact remaining limitations, and the single command/entry point for the next cycle in RESULT.md.

## Verification Commands

- python -m compileall -q src tests scripts tools
- python -m unittest tests.test_pro_review_bridge -v
- python -m unittest discover -s tests -q
- powershell.exe -NoProfile -Command "$errors = $null; [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path '.\tools\pro_review_bridge.ps1'), [ref]$null, [ref]$errors) | Out-Null; if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
- git diff --check

## Completion Contract
Before your final chat response, write this file:
`.chatgpt/codex-runs/2026-07-31T211941Z-cera-shared-repository-overlapped-pro-codex-review-loop-v2/RESULT.md`
Use this exact structure:
```md
# CODEX_RESULT
status: completed | blocked
summary: <one-line summary>
changed_files:
commands_run:
tests:
acceptance_criteria:
blockers:
followups:
```
Then print the same result in the Codex chat.
You are authorized to edit `.chatgpt/pro-review/**` and this run's `RESULT.md` as specified above. Do not edit other `.chatgpt/**` paths.
After verification and diff review, create one local-only commit on the existing feature branch containing only the authorized task changes. Do not merge, push, deploy, change remotes, or edit unrelated files.