# SillyTavern Continuous V3 Two-Run Qualification

## Scope

This is a local, non-production qualification route for Continuous Lean Context
V3. It does not replace `cera-alpha`, activate V3 as the default route, mutate
the persistent Hanezawa human-test database, or authorize an ordinary manual
story to become canonical.

The qualifying virtual model is `cera-continuous-v3-test` on
`http://127.0.0.1:5113/v1`. The server rejects non-loopback binds and the V3
adapter rejects the legacy `cera-alpha` model. The legacy adapter likewise
rejects the V3 test model, so route selection cannot silently fall back.

## Exact run

Each immutable run uses a fresh disposable SQLite copy, continuous world,
branch, session, authority root, and provider workspace. It crosses the real
OpenAI-compatible chat endpoint, review lookup, and strict Accept decision for
three turns:

1. `Hello, my name is Ted. Is this the Hanezawa residence?`
2. `I'm the tenant who was supposed to arrive today.`
3. Scene Change, then `Several days later, Ted is in the kitchen with Mia and asks, "Is Sakura always that cautious with visitors?"`

The fixed schedule is three Sol-medium Planner calls, three non-thinking
DeepSeek V4 Flash Composer calls, and four Terra-high Validator calls. There is
no retry, fallback, provider substitution, Detailer, extra verifier, Fast mode,
or automatic False Positive.

The parent campaign starts each run in a new Python process. This supplies the
required controlled adapter-process restart between consecutive passing runs.
The first failure is frozen and stops dispatch so diagnosis and any repair can
remain provider-free before a new immutable run identity is used.

## Provider-free verification

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest `
  tests.test_sillytavern_continuous_v3 `
  tests.test_sillytavern_continuous_v3_integration -v
```

The integration test crosses the actual HTTP and review boundary with the
production-shaped Continuous coordinator and scripted provider transports. It
must report ten local invocations and zero external provider calls.

## Fresh V2 executable

The V1 run names are historical and the executable rejects them. The governed
cycle supplies every authority value; there are no cycle, task, run, or call
budget defaults. Provider-free qualification uses the same parent and child
entrypoints with local fake provider ports:

```powershell
.\.venv\Scripts\python.exe scripts\run_sillytavern_continuous_v3_campaign.py `
  --confirm-v2-campaign `
  --cycle-directory <cycle-directory> `
  --source-database runtime\development\hanezawa_human_test_v1_2.sqlite3 `
  --runtime-root <new-campaign-runtime-root> `
  --historical-v1-campaign-root <immutable-v1-campaign-root> `
  --transport-mode non_network_fake_ports `
  --expected-checkpoint-sha <checkpoint-sha> `
  --expected-cycle-id <cycle-id> `
  --expected-cycle-sequence <cycle-sequence> `
  --expected-job4-task-id <job4-task-id> `
  --expected-authorization-sha256 <authorization-sha256>
```

The runtime root must not already exist. Every run and campaign result is
immutable once written. Fake mode records ten provider-shaped stage
invocations per complete run and zero external calls.

External mode additionally requires `--provider-activation
<activation-receipt>`. That receipt must bind the same published cycle, Job 4,
route, Sol-medium, non-thinking DeepSeek V4 Flash, Terra-high, and remaining
ceilings. The route profile grants no calls by itself. Missing, mismatched, or
tampered activation fails before provider construction or dispatch.

If a terminal run requires an execution-affecting repair, preserve its entire
campaign root and resume in a new root with the next unused run identity:

```powershell
.\.venv\Scripts\python.exe scripts\run_sillytavern_continuous_v3_campaign.py `
  --confirm-v2-campaign `
  --cycle-directory <cycle-directory> `
  --source-database runtime\development\hanezawa_human_test_v1_2.sqlite3 `
  --runtime-root <new-repair-campaign-runtime-root> `
  --historical-v1-campaign-root <immutable-v1-campaign-root> `
  --prior-v2-campaign-root <immutable-prior-v2-campaign-root> `
  --transport-mode <non_network_fake_ports-or-external_provider> `
  --expected-checkpoint-sha <published-cycle-checkpoint-sha> `
  --expected-cycle-id <cycle-id> `
  --expected-cycle-sequence <cycle-sequence> `
  --expected-job4-task-id <job4-task-id> `
  --expected-authorization-sha256 <authorization-sha256>
```

Recovery accepts only the exact canonical V2 campaign configuration. It
verifies each prior V2 run against its immutable parent reconciliation,
provider ledger, child manifest, child configuration, and result. Historical
V1 Run 001 is read only for its exact one-call debit and can never be selected
as a current run.

## Manual-test handoff

After qualification, the campaign evidence identifies the frozen source,
profile, endpoint, model, fixture, prompts, schemas, policies, and database
hashes. A healthy handoff requires:

- `/health` and `/v1/models` passing on loopback;
- no unresolved review;
- no orphan campaign or provider process;
- the persistent human-test database matching its pre-campaign hash; and
- either a clean disposable generation-zero test root or a new unused runtime
  root for the next manual test.

Do not point a normal SillyTavern story at the campaign database. Manual V3
story testing beyond the frozen qualification fixture remains a separate route
activation decision.
