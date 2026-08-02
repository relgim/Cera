# Continuous V3 provider-backed manual route

## Boundary

This is a second, isolated ordinary-turn route. It does not replace the
provider-free manual profile on port 5114 and it does not modify an installed
SillyTavern preset. Its exact identity is:

- endpoint `http://127.0.0.1:5115/v1`;
- model `cera-continuous-v3-manual-provider-backed`;
- profile `cera.continuous_v3.manual.provider_backed.v1`;
- launcher `scripts/run_cera_sillytavern_continuous_provider_manual.py`;
- default root `runtime/manual/continuous_v3_provider_backed`;
- Sol Planner `gpt-5.6-sol`, medium, Fast disabled;
- Composer `deepseek-v4-flash`, thinking disabled;
- Validator `gpt-5.6-terra`, high, Fast disabled.

The profile is descriptive and authorizes zero external calls. External mode
requires a self-bound activation receipt plus the complete published-cycle
authority arguments. The activation source must equal the validated cycle
manifest SHA-256. Authority is revalidated before every manual turn.

## Provider-free construction test

This mode constructs the provider-backed profile through non-network fake
ports. It makes zero external calls:

```powershell
$Root = 'D:\AIChatBot\Cera\runtime\manual\continuous_v3_provider_backed'
$Launcher = '.\scripts\run_cera_sillytavern_continuous_provider_manual.py'
& .\.venv\Scripts\python.exe $Launcher --transport-mode non_network_fake_ports reset --root $Root --session-id cera-continuous-provider-manual --confirm-reset
& .\.venv\Scripts\python.exe $Launcher --transport-mode non_network_fake_ports start --root $Root
& .\.venv\Scripts\python.exe $Launcher --transport-mode non_network_fake_ports status --root $Root
& .\.venv\Scripts\python.exe $Launcher --transport-mode non_network_fake_ports stop --root $Root
```

The launcher is directly executable as shown. Its background `serve` child
inherits the provider-backed route selection, and local status and review HTTP
calls resolve the selected port dynamically instead of retaining port 5114 as
a Python default argument.

The integrated provider-free readiness gate exercises typed submissions,
exact Validator review hashes, strict Accept and Decline, durable current cast,
explicit Scene Change, stop/restart, recovered unresolved-review handling,
pending-decision fail-closed recovery, verified thread terminalization, and
root isolation through this port-5115 route.

Profile, model, port, root identity, manifest, and process records are bound.
The provider-free profile or port cannot be substituted into this route, and
this provider profile cannot be supplied to the provider-free launcher.

## External activation form

External execution is unavailable unless every placeholder below is supplied
from a newer accepted and consumed queue:

```powershell
& .\.venv\Scripts\python.exe $Launcher `
  --transport-mode external_provider `
  --provider-activation <activation-receipt.json> `
  --cycle-directory <published-cycle-directory> `
  --expected-checkpoint-sha <checkpoint-sha> `
  --expected-cycle-id <cycle-id> `
  --expected-cycle-sequence <sequence> `
  --expected-job4-task-id <task-id> `
  --expected-authorization-sha256 <authorization-sha256> `
  start --root $Root
```

Do not manufacture these values from the profile. A missing or conflicting
activation, source drift, model drift, exhausted family ceiling, unresolved
review, or route substitution fails closed. There is no retry, fallback,
automatic acceptance, Automatic False Positive, or hidden provider repair.
