# SillyTavern LAN and Phone Review Relay Result

**Status:** D-175 implemented and locally qualified

## Outcome

The browser-facing CERA creator-review extension no longer contacts
`127.0.0.1:5101` directly. It uses the same-origin SillyTavern route:

```text
/api/plugins/cera-review
```

SillyTavern's server relays only three route shapes to the loopback-only CERA
service:

- `GET /health`;
- `GET /v1/cera/reviews/:reviewId`;
- `POST /v1/cera/reviews/:reviewId/decision`.

The plugin cannot proxy an arbitrary URL. Review IDs, decision actions, fields,
and feedback size are validated. Upstream responses are size-bounded JSON.
Failures remain explicit, non-retryable, and fallback-free.

## Security and authority

- Port 5101 remains bound to `127.0.0.1` and is not reachable from the LAN.
- The relay loads after SillyTavern authentication and CSRF middleware.
- The browser sends SillyTavern's current CSRF token for review decisions.
- No provider prompt, response, credential, database path, or raw evidence is
  added to browser state.
- The relay changes transport reachability only. Python review, validation,
  creator action, and atomic-publication authority remain unchanged.

## Supported access

- `http://127.0.0.1:8000/` on the host PC;
- `http://192.168.0.202:8000/` on the host PC;
- the same LAN URL from a phone on the same trusted network.

If DHCP changes the PC address, the SillyTavern URL changes with it. The CERA
review endpoint remains same-origin and requires no per-device endpoint edit.

## Verification

- Node relay contract: 3/3 passed;
- focused CERA/SillyTavern tests: 13/13 passed;
- JavaScript syntax checks: clean;
- repository and installed extension/plugin bytes: exact matches.
- restarted SillyTavern loaded the plugin successfully;
- same-origin relay health through `127.0.0.1:8000`: HTTP 200;
- LAN relay health through `192.168.0.202:8000`: HTTP 200 in 0.006 seconds;
- existing persisted review lookup through the LAN route: HTTP 200 in 0.009
  seconds;
- direct `192.168.0.202:5101` connection: blocked as designed;
- complete provider-free repository suite: 560/560 passed in 255.224 seconds.

No provider call, story write, creator decision, retry, fallback, route
promotion, or CERA listener expansion occurred.
