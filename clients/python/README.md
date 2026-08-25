# harness_sdk — Python client for the AI Engineering Harness

Script the whole platform from Python: authenticate once, call any of the
seven backend sections, and stream project events off the live bus.

```
pip install -e clients/python          # from the repo root (or -e for dev)
```

- **Generated surface** — `harness_sdk/<service>.py` is produced from each
  section's OpenAPI spec by `scripts/sdk-gen/generate.py` (104 operations
  across identity, github, router, orchestrator, sandbox, meeting, review).
  Regenerate after API changes; never edit by hand.
- **Handwritten runtime** — bearer auth, the frozen error envelope, retries
  with exponential backoff + jitter, and WS streaming (ws-ticket +
  X-WS-Fingerprint + replay/dedupe) extracted from section7's battle-tested
  browser client (`section7/src/lib/api.ts`, `useEventStream.ts`,
  `backoff.ts`).

Requires Python ≥ 3.11, `httpx`, `websockets`.

## Authenticate in under a minute

```python
from harness_sdk import Harness, ApiError

try:
    sdk = Harness.login("you@example.com")          # legacy passwordless login
except ApiError as err:
    assert err.code == "OWNERSHIP_REQUIRED"         # default since H2r:
    sdk = Harness()                                 # fall back to inbox OTP
    sdk.request_otp("you@example.com")
    code = input("code from email (dev: identity logs): ")
    sdk.verify_otp("you@example.com", code)

print(sdk.principal)   # {'userId': …, 'teamId': …, 'role': 'admin'}
```

Dev stacks don't send mail: read the code from the identity container logs —
`docker logs <identity> | grep HARNESS-OTP`. Already have a token? Skip all of
this: `sdk = Harness(os.environ["HARNESS_TOKEN"])`.

## One sync call per section

```python
projects  = sdk.identity.list_projects(5)            # :8001
conn      = sdk.github.connection_status()           # :8002
models    = sdk.router.list_models()                 # :8003
spend     = sdk.router.model_spend(team_id)
runs      = sdk.orchestrator.list_runs(project_id=pid, limit=10)   # :8004
tree      = sdk.sandbox.get_project_tree(pid)        # :8005
meeting   = sdk.meeting.create_meeting({"projectId": pid, "title": "sync"})  # :8006
thresholds = sdk.review.get_reviewer_threshold_settings()             # :8008
```

Every method returns the decoded JSON body and raises `ApiError` on any
non-2xx. The frozen error envelope (`{"error": {"code", "message"}}`) is
parsed into `err.code` / `err.status`, including FastAPI-wrapped variants:

```python
from harness_sdk import ApiError

try:
    sdk.orchestrator.start_run(run_id)
except ApiError as err:
    print(err.code, err.status, err)   # PLAN_NOT_APPROVED 400 This work can't start…
```

Transient failures (connection errors, 429/502/503/504 on idempotent calls,
plus 429 anywhere; `Retry-After` honored) are retried automatically with
exponential backoff and full jitter (1 s doubling, capped at 30 s). Request /
response shapes are documented as `TypedDict`s in the generated modules
(`from harness_sdk.identity import CreateProjectBody`).

## Stream events (ws-ticket → fingerprint → replay/dedupe)

```python
import threading

stop = threading.Event()
for env in sdk.events.stream(project_id, sdk.token):
    print(env["seq"], env["type"], env.get("runId"))
    if stop.is_set():
        break
```

The stream reproduces everything the S7 browser client does — you never touch
tickets or fingerprints yourself:

1. mints a **fresh single-use ws-ticket per connect** (`POST /api/v1/ws-ticket`
   — long-lived tokens never appear in upgrade URLs);
2. presents the server's echoed fingerprint as **`X-WS-Fingerprint`**
   (M18-C5 presentation law; both requests pin the same User-Agent so the
   sha256(UA+IP) binding validates — pre-M18-C5 servers without an echo are
   handled too);
3. connects with `afterSeq=<highest seq seen>`, so the **server replays**
   missed history before switching to live push;
4. **dedupes by seq**, drops malformed frames instead of dying, reconnects
   with backoff + full jitter (counter resets on successful open), treats bus
   overflow close `1013` as "reconnect to replay", and surfaces permanent
   rejections (`4401 UNAUTHORIZED`, `4403 TICKET_FINGERPRINT_MISMATCH`,
   `4404 NOT_FOUND`) as `ApiError`;
5. recognizes the M16-X3 `{"replayTruncated": true}` marker — when a huge gap
   was capped, backfill via REST: `sdk.replay_events(project_id, after_seq)`.

Async flavor:

```python
async for env in sdk.events.astream(project_id, token):
    ...
```

## Pointing at other hosts / ports

```python
sdk = Harness(token,
              identity_url="http://staging.internal:8001",
              orchestrator_url="http://staging.internal:8004",
              timeout=30.0, max_retries=2)
# or export HARNESS_IDENTITY_URL=… etc. (one var per service)
```

## Tests & smoke

```sh
cd clients/python && ./.venv/bin/python -m pytest tests/ -q   # unit tests (mock transport)
./.venv/bin/python ../../scripts/sdk-gen/smoke_sdk.py          # live journey vs compose stack
```

The smoke script runs the full path — auth (including the honest
`OWNERSHIP_REQUIRED` dead end), one call per section, ticket mint, live event
stream triggered by a real plan proposal — and prints what the API returned.
