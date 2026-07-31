# Native Stored Reasoner Activation Result

**Date:** 2026-07-31  
**Authority:** D-178, D-179  
**Status:** active for local SillyTavern human testing

## Outcome

CERA now uses one branch-bound native stored Codex Reasoner lineage. Each raw
turn creates a candidate fork from the exact accepted checkpoint. Creator
acceptance plus Python's atomic publication promotes that candidate. Decline or
failure archives only the candidate; it cannot be resumed or enter accepted
ancestry.

SillyTavern extension v1.3.0 exposes `Sol: M/H/Ex`, mapped to Reasoner efforts
`medium/high/xhigh`. The Sol realization verifier remains fixed at medium.
Changing effort changes the compatibility hash and reconstructs/rotates from
the accepted Python branch state.

## Corrections established by evidence

1. An empty non-ephemeral app-server thread was not durable across process
   exit. Naming a newly allocated root or fork materializes the rollout before
   the allocator exits, after which a new process can resume it.
2. The installed app-server's generated delete path is unreliable in this
   environment. Archive is supported and makes a thread non-resumable, so
   rejected/failed leaves are archived. Historical deletion custody values
   remain readable but are no longer emitted.
3. The first v2 canary reached the request-bound tool but its synthetic fixture
   omitted `evidence_budget_status`. This was a canary-fixture defect; its
   immutable failure evidence remains preserved.
4. V3 passed the canary and two stored story turns. Turn 3 exposed that the
   qualification harness passed raw `EvidenceService` where the production
   pipeline passes `ReasonerEvidenceTools` through `ReasonerCoordinator`. V4
   exercises the real production seam.

No hard authority, privacy, branch, consent/capacity, protected-user,
participant, evidence, or atomic-publication boundary was weakened.

## Provider-free verification

- Focused post-correction checks: 17/17 passed.
- Complete repository suite: 569/569 passed in 398.054 seconds.
- JavaScript syntax, installed-extension equivalence, request relay, effort
  validation, effort rotation, restart reconstruction, rejection/archive,
  sibling isolation, and creator-review promotion are covered.

## Live qualification

The v4 non-story lifecycle/MCP canary passed in 7.403 seconds. It made one Sol
call, one request-bound snapshot lookup, resumed the exact candidate from a new
process, and archived the disposable lineage leaf-first.

The fresh five-turn Sol-medium qualification then passed 5/5 on one advancing
stored branch:

| Turn | Seconds | Input | Cached input | Uncached input | Output | Reasoning | MCP calls |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 172.640 | 22,133 | 19,200 | 2,933 | 3,331 | 0 | 0 |
| 2 | 83.722 | 31,432 | 0 | 31,432 | 4,526 | 870 | 0 |
| 3 | 105.440 | 41,925 | 0 | 41,925 | 5,506 | 1,736 | 0 |
| 4 | 97.999 | 63,121 | 60,160 | 2,961 | 3,179 | 750 | 4 |
| 5 | 113.108 | 72,306 | 30,464 | 41,842 | 5,477 | 1,783 | 0 |

Batch facts: five Sol calls; zero automatic retry, fallback, DeepSeek call,
story commit, production binding, or route promotion; successful leaf-first
archive cleanup; 595.504 seconds total.

Evidence:

- `evaluation/evidence/native_stored_reasoner_live_five_2026-07-31_v4/canary.json`  
  SHA-256 `58d3702d3b7a8163bee6f3063ed9f399e90f4a27ee6d806ac05bf9b34c827c61`
- `evaluation/evidence/native_stored_reasoner_live_five_2026-07-31_v4/five_run.json`  
  SHA-256 `45709c49a03048a4ffd5f6659158124c696d458d11c09584e7b3f5e1043e682d`

## Active-service verification

After qualification, CERA and SillyTavern were restarted from the updated
files. Current checks established:

- CERA `127.0.0.1:5101` health is `ok`;
- `reasoner_session.mode` is `branch_bound_native_stored_v1`;
- `reasoner_session.active` is `true`;
- advertised efforts are `medium`, `high`, and `xhigh`;
- verifier effort is `medium`;
- SillyTavern `0.0.0.0:8000` returns HTTP 200;
- installed extension source and manifest hashes match the repository copies;
- the visible Sol selector has exactly `M`, `H`, and `Ex`; all three values
  were exercised in the browser and the control was restored to `M`.

## Codex assessment

Persistent conversation identity is now real: turns resume/fork stored Codex
rollouts rather than creating unrelated fresh conversations. Provider prompt
caching is separate and intermittent. The qualification proves persistence and
correct lineage, but it does not prove the previously estimated 80% latency
reduction; measured Reasoner latency remained 83.722-172.640 seconds. Latency
optimization should remain a later task after human-test correctness.

Python remains the only durable story authority. Stored Codex context is
advisory, branch-bound, compatibility-bound, reconstructable, and unable to
publish without the existing validation and creator-acceptance path.
