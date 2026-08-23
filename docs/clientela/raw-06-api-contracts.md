# API / Contracts Audit — Harness Platform (S1–S8)

**Scope:** REST/WS contract health across identity (:8001), github (:8002), models (:8003), orchestrator (:8004), meetings (:8006), review (:8008). All under `/api/v1`.
**Method:** Write-from-map against the pre-verified audit map; claims marked for spot-check were verified against source (cited).
**Verdict:** The platform has good bones — a frozen contracts package, camelCase convention, WS seq-gap replay — but enforcement is manual and uneven. The highest-risk items are the protection inversion (cheap endpoints limited, expensive LLM-spending endpoints unlimited), missing idempotency on money-adjacent POSTs, and error-shape divergence that every frontend consumer must absorb.

---

## Findings

### F1. Two competing error envelopes platform-wide — HIGH
**Dimension:** Envelope consistency.
`error_body(code, message)` exists in the frozen package (`contracts/src/harness_contracts/api.py:6`) and several modules use it correctly (`invites.py:63,122,168,204`; the rate limiter imports it too). But other modules raise bare strings (`app.py:58,311,313,338`, `auth.py:55` → `{detail: "admin only"}`) or leak exception text (`detail=str(exc)` in `section2/src/harness_github/app.py:254` and five sites in `section8/src/harness_review/api.py:128-202`). Consumers must parse two shapes: `detail: {code, message}` vs `detail: "<string>"`.
**Consumer impact:** Every apiFetch caller needs shape-sniffing; error codes can't be matched reliably; TS types for errors are lies.
**Fix:** Register one FastAPI `exception_handler(HTTPException)` + `exception_handler(RequestValidationError)` per app factory (helper owned by `harness_contracts`), normalizing everything to `error_body`. Sweep all `HTTPException(detail="<str>")` call sites mechanically.

### F2. FastAPI default 422 validation shape escapes the envelope — HIGH
**Dimension:** Envelope consistency.
Request-validation failures return FastAPI's default `{detail: [{loc, msg, type}, ...]}` everywhere — no service wraps them in `error_body`. This is the single most commonly triggered error (bad payloads) and it's the one shape with no code field.
**Consumer impact:** Form validation UI can't render a consistent error card; automated retry/alert logic keyed on `code` misses validation failures entirely.
**Fix:** Same global handler as F1: map `RequestValidationError` → 422 `error_body("VALIDATION_ERROR", flattened message)` with field details under a stable `fields` key.

### F3. ReviewFindings emits snake_case — documented drift, never fixed — HIGH
**Dimension:** Envelope/contract consistency. *(Spot-checked: `section7/src/lib/api.ts:141-152`.)*
The `done-signal` response body carries `syntax_ok`, `conflicts_detected`, `brief_aligned` — snake_case, unlike every other resource body. The drift is *documented in a frontend comment* ("Typed here to match what the endpoint actually returns, not what the convention says it should") but the contract stays broken. It's outside the frozen `contracts` package, so nothing enforces convergence.
**Consumer impact:** Every consumer of `signalDone` special-cases key casing; any future codegen from conventions produces wrong types.
**Fix:** `model_dump(by_alias=True)` with camelCase aliases on Section 4's `ReviewFindings`, ship both keys for one release, update `api.ts`, then drop the compat shim. Add the model to `harness_contracts` so golden-fixture tests pin it.

### F4. Only the runs index paginates; every other list is unbounded — MEDIUM-HIGH
**Dimension:** Pagination.
Runs index takes `limit`/`offset`. Meeting messages, invites list, API-keys list, projects list, teams list: unbounded SELECT-all. Message history on an active meeting grows without bound; an org with hundreds of keys/projects serializes them all per request.
**Consumer impact:** Linear latency/UI degradation as data grows; mobile clients download entire histories; no way to window a busy meeting feed.
**Fix:** Shared `paginate()` dependency in `harness_contracts` (default 50, hard cap 100, cursor or limit/offset) applied to all collection routes; return `{items, total}` so the frontend can render pagers.

### F5. Zero idempotency on mutating POSTs — HIGH
**Dimension:** Idempotency.
No endpoint accepts an `Idempotency-Key`; no route is naturally idempotent. Network-retried requests (flaky link, double-tap before disable, proxy retry) can: create duplicate meetings, mint duplicate API keys (an **orphaned credential** the owner may never see — security issue, not just UX), start a run twice (double LLM spend), or double-apply human-edit content.
**Consumer impact:** Silent duplicates with real cost/spend consequences; key list pollution; confusing run history.
**Fix:** Accept `Idempotency-Key` on `POST /meetings`, `/api-keys`, `/runs/{id}/start|approve`; store key→(status, body) in Postgres with 24h TTL inside the same transaction, replay on hit. Cheaper interim step: require client-supplied unique IDs (clientMeetingId, clientKeyId) with unique-index dedupe returning the original entity.

### F6. Versioning is a URL prefix, not a policy; the pin test is dead code — MEDIUM
**Dimension:** Versioning. *(Spot-checked: `section1/tests/conftest.py:43-46`.)*
`/api/v1` everywhere, but no `Deprecation`/`Sunset` headers, no deprecation policy in `contracts/api-conventions.md`, no version-negotiation story. Worse, `test_contracts_version_frozen()` asserting `harness_contracts.__version__ == "0.1.0"` is defined **in conftest.py — pytest never collects tests from conftest**, so the "frozen version" assertion has never run. The one guard on the monorepo's most sacred invariant is theater.
**Consumer impact:** Nothing detects a contracts bump that breaks consumers; clients can't detect upcoming breakage.
**Fix:** Move the test to `tests/test_contracts_version.py` (it will immediately become meaningful). Add a `Sunset` header middleware stub reading a per-route deprecation registry; write the 90-day deprecation policy into api-conventions.md.

### F7. WS protocol gaps: mid-stream auth expiry and reconnect storms unspecified — MEDIUM
**Dimension:** WS vs REST.
Single-use tickets and seq-gap replay are solid. Unspecified: what happens when the JWT expires while the socket is open (does the stream outlive its auth?); and reconnect behavior after an S1 restart — every client's ticket fetch fires simultaneously (stampede on :8001), then all sockets reconnect at once.
**Consumer impact:** Either a live socket keeps streaming with a dead identity (authz drift mid-stream) or users get dropped mid-meeting; S1 restarts cause self-inflicted thundering herd.
**Fix:** Server-initiated close `4401` on token expiry forcing re-auth handshake; ticket endpoint gets per-client jittered backoff guidance; document reconnect semantics (resume-from-seq is already there — wire it to skip replayed frames' side effects).

### F8. Protection inversion: login is rate-limited, LLM spend is not — HIGH
**Dimension:** Rate-limit coverage. *(Spot-checked: `section1/src/harness_identity/ratelimit.py:1-4`.)*
`DEFAULT_ROUTES` covers exactly `/api/v1/auth/login` + `/api/v1/auth/otp/request` — the cheapest operations on the platform. Unlimited: `POST /runs/{id}/start` (spawns real LLM runs = real money), key mint, brief generate (LLM call), `call_model`, spend/metrics reads. An attacker (or a buggy frontend loop) can burn the model budget indefinitely while being unable to brute-force a password. Compounding: the limiter fails **open** on DB failure (ratelimit.py:9-13) — correct for login availability, wrong as a default for money paths.
**Consumer impact:** One bad retry loop = unbounded invoice; abuse of brief/model endpoints invisible to any guardrail.
**Fix:** Extend `DEFAULT_ROUTES` with per-user (not per-IP) quotas on `/runs/*/start`, `/api-keys`, `/brief`, `/ai/*`; introduce a fail-closed profile for spend-classified routes; emit `X-RateLimit-*` headers so well-behaved clients can back off.

### F9. No aggregate run read; three round trips per poll — MEDIUM
**Dimension:** Missing resources.
There is no `GET /runs/{id}` returning the full aggregate. Status, engine, and plan are separate calls; the frontend fans them out through `Promise.allSettled`, which silently swallows a `getRunStatus` 404 — UI shows a stale "running" badge forever for purged runs. Also missing: `DELETE /projects/{id}` (projects are immortal) and **any member-removal endpoint** — the team page cannot remove anyone; offboarding requires direct DB surgery.
**Consumer impact:** 3× request amplification per run view; ghost runs in UI; blocked offboarding workflow.
**Fix:** `GET /runs/{id}?include=engine,plan` composite (single authorization check, single round trip); `DELETE /projects/{id}` as soft-delete + audit event; `DELETE /teams/{id}/members/{userId}` with last-admin guard. Stop swallowing allSettled rejections — discriminate and surface terminal states.

### F10. No concurrency control: approve/reject races resolve last-write-wins — MEDIUM-HIGH
**Dimension:** Concurrency controls.
No ETag/If-Match/version column on any resource. Two admins can approve and reject the same planned run concurrently; both succeed; final state depends on arrival order. Same exposure for human-edit: two editors on one path, or a human-edit racing the done-signal diff, silently lose updates.
**Consumer impact:** Contradictory approvals on irreversible actions (run starts); silent data loss in collaborative editing.
**Fix:** Add monotonic `version` to runs/files; reads return `ETag`, transitions require `If-Match`; enforce in SQL (`UPDATE ... WHERE status='planned' AND version=N` → 409 `CONFLICT` on zero rows). State-transition guards alone kill the worst race (approve/reject) even without full ETag plumbing.

### F11. Unbounded request bodies: diffText and human-edit content — MEDIUM-HIGH
**Dimension:** Request size caps.
No Content-Length cap anywhere (uvicorn default: none). `done-signal.diffText` is unbounded — a runaway agent can post a multi-hundred-MB diff that gets persisted and shipped to reviewers. `human-edit.content` likewise. Meeting messages unbounded. DoS vector plus DB bloat plus review-pipeline memory spikes.
**Consumer impact:** One malformed run can degrade the whole orchestrator and Postgres storage; review UI chokes rendering megabyte diffs.
**Fix:** Pure-ASGI body-cap middleware (mirroring security_headers.py pattern): 1–2 MB default, 10 MB on the diff route → 413 `error_body("PAYLOAD_TOO_LARGE")`. Validate diffText plausibility in the reviewer before persistence.

### F12. Cross-team oracle leaks: 403 vs 404 used inconsistently — within the same module — MEDIUM
**Dimension:** Information-leak consistency. *(Spot-checked across api_keys/invites/app.py.)*
Three coexisting policies: (a) api_keys uses 403-no-oracle for admin ops but 404 for a missing key (`api_keys.py:77,114,141` vs `:153`); (b) invites answers 403 "not a member of that team" (`invites.py:122,168` — safe, reveals nothing about the invite) yet 404 "invite not found" (`:204`) for the invite itself; (c) worst, `app.py:58` returns 403 `"project not found for this team"` — confirming to a cross-team actor that the project **exists**, a textbook enumeration oracle. S2/S8 blanket-404 everything (`detail=str(exc)`), which is safe but opaque.
**Consumer impact:** Legitimate users get confusing "not found" for permission problems; attackers get an existence oracle for projects; frontend can't distinguish "retry as admin" from "gone".
**Fix:** Write the decision table into api-conventions.md: resource outside your visibility → 404; resource visible but action gated → 403; standardize keys/invites to it; fix app.py:58 to 404-no-oracle. Golden-fixture tests per module assert the table.

### F13. OpenAPI: six islands, partially untyped responses — MEDIUM
**Dimension:** OpenAPI quality.
Each service exposes `/openapi.json`, but there's no aggregated reference and the hand-maintained TS mirror (`contracts/ts/types.d.ts` v0.2.0) drifts from reality — ReviewFindings (F3) being the proof. S4 orchestrator and S6 meetings return `dict[str, Any]` on several routes, so their specs describe amorphous objects; client generation is impossible for those endpoints.
**Consumer impact:** Frontend types are maintained by vibes; new sections can't codegen clients; integration bugs surface only at runtime.
**Fix:** Type every route with `response_model` (kill `dict[str,Any]` in S4/S6); CI job fetching all six openapi.json files into `contracts/openapi/` with a diff gate against `types.d.ts`.

### F14. Internal error strings and service topology leak to end users — MEDIUM-HIGH
**Dimension:** Error message quality.
`detail=str(exc)` at `section2/src/harness_github/app.py:254` and `section8/src/harness_review/api.py:128,145,161,183,202` passes raw exception text straight to browsers. Downstream `httpx` failures surface messages like "is S6 up on :8006?" — leaking internal hostnames, ports, and service topology to end-user UIs. Bare-string details elsewhere ("wrong team", "admin only") are at least benign. This cuts against the monorepo rule that secrets/topology never leave Section 1/3 storage and never enter user-facing surfaces.
**Consumer impact:** Attackers map the internal architecture for free; users see engineer-facing gibberish.
**Fix:** Global handler (F1/F2) maps unexpected exceptions → 500 `error_body("INTERNAL", "unexpected error")` + server-side log with a correlation ID echoed to the client; audit every `str(exc)` site.

### F15. Orchestrator client functions don't exist; no 401 handling in apiFetch — HIGH
**Dimension:** Missing resources / frontend-contract gap (from frontend audit, consistent with backend map).
`api.ts` ships no `proposePlan`, `approvePlan`, or `startRun` — the core orchestrator loop is either unwired or done via ad-hoc fetches bypassing the shared token/error path. Separately, `apiFetch` has no 401 branch: an expired token produces whatever the endpoint's error mapping yields (often a misleading 403/404-style failure) instead of a re-login redirect.
**Consumer impact:** Approval flow fragile or absent; session expiry looks like random failures; support burden.
**Fix:** Implement the three clients on the existing `apiFetch` pattern; centralize a 401 interceptor (clear token → redirect to login, preserving return URL).

---

## Severity summary

| # | Finding | Severity |
|---|---------|----------|
| F5 | No idempotency on mutating POSTs | HIGH |
| F8 | Rate-limit inversion (login limited, LLM spend not) | HIGH |
| F1 | Dual error envelopes | HIGH |
| F2 | Default 422 shape escapes envelope | HIGH |
| F3 | ReviewFindings snake_case drift | HIGH |
| F15 | Missing orchestrator clients / no 401 branch | HIGH |
| F4 | Unbounded lists | MEDIUM-HIGH |
| F10 | No concurrency control (approve/reject race) | MEDIUM-HIGH |
| F11 | Unbounded request bodies | MEDIUM-HIGH |
| F14 | Topology leaks via str(exc) | MEDIUM-HIGH |
| F6 | Versioning theater + dead pin test | MEDIUM |
| F7 | WS expiry/reconnect unspecified | MEDIUM |
| F9 | No run aggregate / DELETE project / member removal | MEDIUM |
| F12 | 403/404 oracle inconsistency | MEDIUM |
| F13 | Untyped responses, no aggregated OpenAPI | MEDIUM |

## Recommended order of attack

1. **Global exception handlers (F1/F2/F14)** — one PR per app factory, fixes three findings and stops active information leakage.
2. **Idempotency on money paths (F5)** + **rate-limit extension (F8)** — closes the financial exposure.
3. **Contract hygiene (F3, F6)** — fix the dead test first (five-minute change guarding the repo's core invariant), then camelCase the ReviewFindings body with a compat window.
4. **Resource/API completeness (F9, F15)** — unblocks the approval UX and offboarding.
5. **Scale hardening (F4, F10, F11)** — pagination, optimistic concurrency, body caps.
