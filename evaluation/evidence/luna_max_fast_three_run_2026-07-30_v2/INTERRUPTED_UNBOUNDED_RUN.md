# Interrupted unbounded diagnostic run

The v2 capability preflight passed and Sample 1 dispatched one
`gpt-5.6-luna` request with `max` effort and the `priority` Fast tier. The
in-process diagnostic runner failed to enforce CERA's 240-second route timeout.
After the request remained non-terminal beyond 600 seconds, Codex stopped the
harness and explicitly terminated its Python/app-server process tree.

No DeepSeek Composer or Sol verifier call started. No sample output, provider
receipt, retry, fallback, publication, or story-state write exists. The Luna
dispatch is conservatively counted as one consumed Codex call.

The corrected v3 harness uses CERA's supervised subprocess runner. It passes
the same explicit Fast tier through the private worker protocol, enforces the
240-second route timeout, and kills the full worker tree on timeout.
