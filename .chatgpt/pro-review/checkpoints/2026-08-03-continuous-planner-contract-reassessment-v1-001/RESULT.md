# Continuous Planner contract correction and reassessment result

Status: passed provider-free gates.

The exact Cycle 25 non-null current-output `accepted_turn_id` failure is now closed without output repair or validation weakening. The provider schema requires `provisional: true` and `accepted_turn_id: null`; the domain decoder enforces the same invariants; and the existing finalize check remains intact. Prior accepted IDs remain available as explicitly labeled prior/reference identities in the prompt.

The exact failed Cycle 25 payload is retained as a regression fixture. It is rejected unchanged. Changing only `accepted_turn_id` to `null` passes domain decoding and request-local evidence validation.

The two-call result has also been reclassified so raw transport observations are separate from strict validity and concept-pass predicates. The evidence confirms same-thread custody, lean omissions, an 82.283% variable-prompt reduction, and provider completion on both calls. It separately records that Call 2 was not strict-valid and the two-call concept therefore did not pass.

Focused gates: 36 of 36 passed across the current-output contract, Planner/Validator, lean-context, and protected-ingress regression modules. No complete repository suite was run. Provider calls: 0.

The stored-thread performance reassessment concludes that the compact variable prompt did not reduce observed input or latency. Exact cache eligibility and internal token attribution are unavailable, so no caching cause is claimed. One fresh physical thread using the same compact validated context is the minimum materially different control.
