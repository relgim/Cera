# Provider-Free Verification Results

## Final results

- Repository-wide suite: **575/575 passed** in **293.331 seconds**.
- Changed-surface focused suite: **46/46 passed** in **4.091 seconds**.
- Post-package documentation check: **3/3 passed** in **0.128 seconds**.
- `python -m compileall -q src tests scripts`: passed.
- Evidence generator `py_compile`: passed.
- `git diff --check` from baseline to checkpoint: passed.

## Preserved diagnostic attempt

The first evidence-export run executed 575 tests in 329.167 seconds and failed
only because the newly synchronized Pro response contained a chat-only
`sandbox:/mnt/data/...` Markdown link. That failure is retained in
`full_suite_attempt_1.stderr.log`; the review artifact was normalized without
changing runtime source, and both focused and complete reruns passed.

## Runtime effects

- Live provider calls: 0.
- Provider retries or fallbacks: 0.
- Production/story database writes: 0.
- Active route changes: 0.
- SillyTavern behavior changes: 0.
