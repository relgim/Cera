review_schema_version: cera.pro_review_response.v2
review_cycle_id: 2026-07-31-compact-reasoner-v7-cycle-001
reviewed_cycle_sequence: 5
reviewed_repository_identity_sha256: 12e57e11b3c9295615b1e2d5534949f6978329d1cfde2c52ed5d4e892b1b8797
reviewed_checkpoint_id: 2026-07-31-compact-reasoner-v7-001
reviewed_checkpoint_git_sha: 00291ab14b741c4a0974c1500a8bdb56060c4243
reviewed_evidence_sha256: d79f64c51de89e592c513c15ed97b04add7f0e091dc3eb79cd6aed77c7cdcc44
reviewed_task_set_sha256: eb8760fd22d2ec63dad04e9e18eb5c3b6f437ccfd8752e880539afcda27c943e
reviewed_job4_task_id: compact-reasoner-v7-four-variant-sol-medium-comparison-v1
response_nonce: 61a1bc97a62c11a6ef7616dc858fc436e11628ff1230f4179d499def92716421
review_scope: repository_cycle
review_disposition: corrections_required

## Independent findings

1. Progression 1 is a valid observability correction. The stored-turn worker now records each distinct operation-local `usage.last` step while using thread totals only to identify duplicate or backward notifications. The cumulative counters remain content-free, unsupported values remain explicit rather than silently becoming zero, and prompt, output, reasoning, evidence, and tool arguments are not retained. No material telemetry misstatement or authority expansion was found in the inspected implementation.

2. Progression 2 produced a valid scoped-v6 improvement for the exact bound continuation. The accepted generation-2 branch head, rather than a fixture-name rule, supplies Hana and Mia as the active NPCs; Ted remains present and protected, and the remaining household remains world-known and scene-reachable without being eligible responders for the current-only request. Variant B preserved every reported cast, beat, stop, knowledge, branch, and source-isolation check while reducing elapsed time from 113.690476 seconds to 92.845283 seconds, input from 65,217 to 60,706 tokens, output from 4,910 to 3,917 tokens, and reasoning from 1,757 to 1,204 tokens. This is a material matched-fixture improvement, approximately 18.3% lower elapsed time, but it is one no-retry attempt and does not by itself qualify general production promotion.

3. The compact-v7 failures are caused by a provider-contract mismatch, not by stored versus reconstructed ancestry. `reason_code` is nullable in the provider-facing JSON schema, but `CodexReasonerDraftV7Compact.__post_init__` requires it to be non-null for `decision_ready`, and the compact prompt does not state that conditional requirement. Variants C and D both returned `decision_ready` with `reason_code: null`, passed provider structured-output completion, and then failed typed decoding with `ready compact v7 lacks selectors`. Their identical failure across both lineage modes makes the contract surface the supported root cause. Because neither result reached an accepted typed v7-to-v6 plan, compact-v7 semantic quality and latency remain unqualified.

4. The compiler mapping inspected for Progression 3 is otherwise deterministic and shadow-only. Responders, floor ownership, perception, intent, tactic, knowledge limits, source claims, ordered beats, material transitions, protected-user allowances, stop boundaries, future segments, adult routing, and development proposals are carried into the existing v6/domain validation path. No material compiler-added scene choice, active-route activation, Composer change, verifier change, retry, fallback, or story-authority write was found. The compiler's `draft.reason_code or "complete_supported_unit"` fallback is nevertheless inconsistent with the ready-state non-null requirement and should not remain semantically ambiguous.

5. The exact current-only scope is sound, but the shadow adapter's broader explicit-character extraction is lexical. Negative, quoted, hypothetical, or future mentions outside a current-only cue can still enter the explicit set and be treated as active. This does not invalidate Variant B, but it is a general-promotion risk that the current exact-fixture tests do not resolve.

6. Job 4 stayed within its bound: four provider calls, no retries or fallbacks, no DeepSeek or independent-verifier calls, no story-database writes, no active-route changes, and no deployment, service, SillyTavern, remote, or push effect. The D-180 v6 production/default route remains unchanged.

## Required corrections

1. Align `reason_code` across the provider schema, compact prompt, typed domain contract, and compiler. A provider-schema-valid `decision_ready` object must not be allowed to reach Python with `reason_code: null` and fail only at dataclass construction. Preserve null/cleared decision fields for non-ready outcomes. If the accepted provider-schema dialect cannot express the conditional requirement safely, redesign the status/field shape rather than relying only on an instruction sentence.

2. Remove or formally define the compiler fallback for a missing ready-state `reason_code`. There must be one authoritative rule: either runtime Codex supplies the non-empty development obligation, or Python owns a clearly specified mechanical value. The current combination of a required field and an unreachable fallback is internally inconsistent.

3. Add regression coverage reproducing the live C/D failure at the provider boundary. Tests must establish that a projected provider-schema-valid ready payload cannot contain a null `reason_code`, that a compliant ready payload compiles through the unchanged v6/domain path, that non-ready reset still requires null, and that the compact prompt states every conditional field obligation that the schema cannot encode.

4. Before any broad scope promotion, add provider-free cases for negative mentions, quoted dialogue, hypothetical or future entrants, explicit characters outside the current scene, all-cast requests, regeneration, and sibling branches. The exact accepted-head current-only behavior must remain unchanged.

5. Keep compact v7 shadow-only while correcting and verifying these defects. Any further live v7 attempt, matched quality qualification, route promotion, or production activation requires separate creator authorization.

## Approved next work

The technically appropriate next tranche is a provider-free contract correction and regression suite limited to the defects above, followed by a fresh review package. Scoped compact input plus v6 may be retained as a promising qualification candidate because Variant B was valid and materially faster on the frozen fixture. A broader matched latency-and-quality proposal may be prepared for Ted, but this advisory response does not authorize provider calls, implementation, activation, or promotion.

No correction is required to the exact Variant B result or to the cumulative telemetry conclusion, provided both remain described as bounded evidence rather than general qualification.

## Explicitly not authorized

This response does not authorize compact-v7 or scoped-v6 production/default activation; Job 5; any additional Sol, DeepSeek, Composer, verifier, Detailer, retry, fallback, hidden repair, Fast-mode, or speculative provider call; any story acceptance or authority write; any live-database mutation; any deployment, service restart, SillyTavern alteration, merge, remote operation, or push; or any expansion of creator authority. All recommendations remain advisory and require Ted's separate decision where authority is needed.
