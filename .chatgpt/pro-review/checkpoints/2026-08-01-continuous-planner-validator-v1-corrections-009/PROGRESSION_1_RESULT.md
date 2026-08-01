# Progression 1 Result

task_id: `continuous-job4-result-schema-alignment-v9`
status: completed
provider_calls: `0`

The live-shaped and scripted continuous Job 4 harnesses now use one
`build_canonical_job4_result` projection before writing the repository result.
The output is the exact closed `cera.pro_review_job4_result.v1` shape accepted
by the strict repository-cycle decoder.

The canonical result no longer duplicates `authorization_sha256`, which stays
bound through the manifest, task set, and immutable receipt chain. Scripted
transport invocation counts remain in `JOB4_DETAIL.json`, the Markdown report,
and the privacy-safe verification summary rather than canonical `effects`.
External provider dispatch accounting remains a canonical effect.

The projector rejects nonterminal states and invalid call-count types. No
domain validator, authority boundary, active route, or historical evidence was
weakened or changed.
