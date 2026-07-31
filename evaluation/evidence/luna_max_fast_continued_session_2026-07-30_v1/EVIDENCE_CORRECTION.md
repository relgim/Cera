# Evidence interpretation correction

The generated JSON artifacts are preserved unchanged.

Only turn 1 was dispatched. It failed after 279.851 seconds with
`SceneReasonerPortFailure: Codex output exceeded the configured token budget`.
Turns 2 and 3 were not dispatched because `--stop-on-failure` terminated the
continued-session policy after the invalid first result. Therefore:

- actual Luna provider calls: 1;
- actual continued warm turns: 0;
- retries and fallbacks: 0;
- story-state commits: 0.

The original `summary.json` incorrectly marks all three planned provider stages
as `dispatch_started: true`, and both generated JSON files report zero provider
calls because the first harness version counted only calls that produced a
provider receipt. The per-turn record and console evidence correctly contain
only turn 1. The diagnostic harness was corrected after this run to count a
dispatch before decoding and to mark only actually dispatched stages. The test
was not rerun.

Preserved artifact hashes:

- `continued_thread.json`: `8c1a2207a19e4aa11ec65c02437b4fc0516fcf85ea20092874b0fc6dc8fb4d1c`
- `summary.json`: `657767ba5d3b1988ae98f922f39ccbd526c70a36f87b69f84039e1a5f7e6ef5e`
