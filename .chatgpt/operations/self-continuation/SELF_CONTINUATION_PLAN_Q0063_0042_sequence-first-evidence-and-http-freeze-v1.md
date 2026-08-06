# Self-continuation plan Q0063/0042

## Identity

- Queue: `0063`
- Goal: `2`
- Plan: `sequence-first-evidence-and-http-freeze-v1`
- Provider authority: `0 Codex`, `0 DeepSeek`
- Starting HEAD: `1320c52b682224446e6f887e5665d50c57e928e4`
- Starting tree: `52476cea11b6e8254c7637da88103beb198b021b`

## Bounded correction

1. Add an additive, local-only operation evidence store that writes exact
   pre-dispatch request/schema bytes and terminal raw result or failure evidence
   into one call-scoped directory. Bind it at the existing provider-call ledger
   boundary and prove that later failure cannot erase an earlier call.
2. Add the exact sequence-first status property consumed by `/health`.
3. Add an explicit loopback Stage 6 launcher/config for port `5116`; do not alter
   the shared server default.
4. Extend focused fake/offline tests for evidence atomicity, HTTP endpoints,
   review Accept/Decline, restart/branch custody, route identity, no fallback,
   workspace isolation, and bounded character reads.
5. Run the named presence rollback test, compile/import checks, focused route
   tests, then the complete repository suite through `D:\CP25\source\.venv`.
6. Freeze the passing provider-free source in one local commit and write the
   Goal 2 result and manager notification. Do not push.

## Stop conditions

- Any provider dispatch.
- Any source change outside the bounded sequence-first evidence/HTTP surface.
- Any need to alter the shared server default, production route, installed
  SillyTavern, story database, remote, or historical evidence.
