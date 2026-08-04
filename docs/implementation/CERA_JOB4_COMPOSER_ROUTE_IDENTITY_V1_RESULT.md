# Job 4 Composer route-identity correction result

**Queue:** 0052

**Checkpoint:** `2026-08-03-cera-job4-composer-route-identity-v1-001`

**Status:** `passed_frozen`

## Outcome

The provider qualification harness no longer hardcodes Flash after every
DeepSeek Composer call. It now receives an explicit expected Composer model,
defaults to `deepseek-v4-flash`, and permits only the closed Flash/Pro V4 set.
The post-call receipt must match that exact expected model and external-call
count.

This changes test/canary route verification only. It does not promote Pro,
change the active/default Composer route, or alter runtime story authority.

## Trigger evidence

The first conditional Pro canary call completed and decoded successfully, but
the old harness rejected its `deepseek-v4-pro` receipt against the hardcoded
Flash value. The failed Pro V1 identity and its single DeepSeek debit remain
immutable. No Validator or Reader call was made for it.

## Qualification

- Job 4 harness suite: 34 tests passed.
- Adjacent correction, Writer-boundary, and Runtime Model V3 suites: 73 tests passed.
- Total focused tests: 107 passed.
- `git diff --check`: passed.
- `python -m compileall -q scripts tests`: passed.
- Provider calls during the correction: Codex 0; DeepSeek 0.

The next authorized operation is a fresh Pro V2 non-thinking canary identity
with at most two additional DeepSeek calls and four Codex calls remaining from
the original Pro canary ceiling.
