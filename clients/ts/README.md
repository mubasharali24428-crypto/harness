# @harness/ts-sdk — TypeScript client for the AI Engineering Harness

Script the whole platform from TypeScript or plain JavaScript (Node ≥ 22):
authenticate once, call any of the seven backend sections, and stream project
events off the live bus.

```sh
cd clients/ts && npm install && npm run build   # zero runtime dependencies
```

- **Generated surface** — `src/<service>.ts` is produced from each section's
  OpenAPI spec by `scripts/sdk-gen/generate.py --target ts` — the SAME specs
  that drive `clients/python` (104 operations across identity, github, router,
  orchestrator, sandbox, meeting, review). Regenerate after API changes; never
  edit by hand.
- **Handwritten runtime** — bearer auth, the frozen error envelope, retries
  with exponential backoff + jitter, and WS streaming (ws-ticket +
  fingerprint presentation + replay/dedupe) extracted from section7's
  battle-tested browser client (`section7/src/lib/api.ts`, `useEventStream.ts`,
  `backoff.ts`) — the same cited-extraction law as the Python SDK.

## Authenticate in under a minute

```ts
import { Harness } from "@harness/ts-sdk";

let harness: Harness;
try {
  harness = await Harness.login("you@example.com"); // legacy passwordless login
} catch (err) {
  // default since H2r: deployments require inbox ownership
  harness = new Harness();
  await harness.request_otp("you@example.com");
  const code = await ask("code from email (dev: identity logs): ");
  await harness.verify_otp("you@example.com", code);
}

console.log(harness.principal); // { userId: …, teamId: …, role: 'admin' }
```

Dev stacks don't send mail: read the code from the identity container logs —
`docker logs <identity> | grep HARNESS-OTP`. Already have a token? Skip all of
this: `new Harness({ token: process.env.HARNESS_TOKEN })`.

## One sync call per section

```ts
const projects   = await harness.identity.list_projects(5);          // :8001
const conn       = await harness.github.connection_status();         // :8002
const models     = await harness.router.list_models();               // :8003
const spend      = await harness.router.model_spend(teamId);
const runs       = await harness.orchestrator.list_runs(pid, undefined, 10); // :8004
const tree       = await harness.sandbox.get_project_tree(pid);      // :8005
const meeting    = await harness.meeting.create_meeting({ projectId: pid, title: "sync" }); // :8006
const thresholds = await harness.review.get_reviewer_threshold_settings();                  // :8008
```

Every method returns the decoded JSON body and throws `ApiError` on any
non-2xx. The frozen error envelope (`{"error": {"code", "message"}}`) is
parsed into `err.code` / `err.status`, including FastAPI-wrapped variants:

```ts
import { ApiError } from "@harness/ts-sdk";

try {
  await harness.orchestrator.start_run(runId);
} catch (err) {
  if (err instanceof ApiError) {
    console.log(err.code, err.status, err.message); // PLAN_NOT_APPROVED 400 This work can't start…
  }
}
```

Transient failures (connection errors, 429/502/503/504 on idempotent calls,
plus 429 anywhere; `Retry-After` honored) are retried automatically with
exponential backoff and full jitter (1 s doubling, capped at 30 s). Request /
response shapes are documented as typed interfaces in the generated modules
(`import type { CreateProjectBody } from "@harness/ts-sdk/…" via
`identityTypes`).

## Stream events (ws-ticket → fingerprint → replay/dedupe)

```ts
const stop = new AbortController();
for await (const env of harness.events.stream(projectId, harness.token ?? undefined, stop.signal)) {
  console.log(env.seq, env.type);
}
stop.abort(); // when done
```

The stream reproduces everything the S7 browser client does — you never touch
tickets or fingerprints yourself:

1. mints a **fresh single-use ws-ticket per connect** (`POST /api/v1/ws-ticket`
   — long-lived tokens never appear in upgrade URLs);
2. presents the server's echoed **fingerprint** on the upgrade (M18-C5
   presentation law). Node's global WebSocket cannot set custom headers, so —
   exactly like the S7 browser client — the value rides as the `?fingerprint=`
   query param, and the mint request pins the same User-Agent the upgrade will
   send so the server's sha256(UA+IP) binding validates;
3. connects with `afterSeq=<highest seq seen>`, so the **server replays**
   missed history before switching to live push;
4. **dedupes by seq**, drops malformed frames instead of dying, reconnects
   with backoff + full jitter (counter resets on successful open), treats bus
   overflow close `1013` as "reconnect to replay", and surfaces permanent
   rejections (`4401 UNAUTHORIZED`, `4403 TICKET_FINGERPRINT_MISMATCH`,
   `4404 NOT_FOUND`) as `ApiError`;
5. recognizes the M16-X3 `{"replayTruncated": true}` marker — when a huge gap
   was capped, backfill via REST:
   `harness.identity.replay_events(projectId, afterSeq)`.

Headless lifecycle control (React-free twin of section7's
`EventStreamController`; callbacks are passed as constructor options):

```ts
import { EventStreamController } from "@harness/ts-sdk";

const controller = new EventStreamController({
  http: harness.http,
  onEvent: (env) => render(env),
  onStateChange: (s) => show(s.state), // connecting | open | reconnecting | closed
});
controller.start(projectId, token);
controller.stop();
```

## Pointing at other hosts / ports

```ts
const harness = new Harness({
  token,
  identityUrl: "http://staging.internal:8001",
  orchestratorUrl: "http://staging.internal:8004",
  timeoutMs: 30_000,
  maxRetries: 2,
});
// or export HARNESS_IDENTITY_URL=… etc. (one var per service)
```

## Tests & smoke

```sh
cd clients/ts
npx tsc --noEmit            # strict typecheck over generated + handwritten code
npm test                    # unit tests (mocked fetch / fake sockets, no stack needed)
npm run smoke               # live journey vs compose stack
```

The smoke script runs the full path — auth (including the honest
`OWNERSHIP_REQUIRED` dead end), one call per section, ticket mint, live event
stream triggered by a real plan proposal — and prints what the API returned.
