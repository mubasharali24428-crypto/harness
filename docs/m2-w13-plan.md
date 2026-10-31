# Marathon 2.0 W13 — bring-your-own runners (M2-W13)

Follows the 2026-10-28 Marathon 2.0 roadmap, the W13 row, and
[the W12 outcome](m2-w12-plan.md). The roadmap's gate is: *a run executes on a
customer-controlled host, the confinement attestation is verifiable, and a
stolen runner token cannot reach other teams.* The marathon's prerequisite row
narrows the design work: *auth/token threat model, worker lifecycle, attestation
design*; its exit evidence is *revocation, replay protection and team isolation
verified; documented trust assumptions for customer-controlled host*.

Governing rules: *every capability ships with boundary checks, resource limits,
cancellation, audit and tenant isolation*; *secrets never enter logs or model
context*; *an unchecked gate stays open when a dependent week begins*.

## Purpose

W13 asks for one thing in three parts:

1. a **runner control plane** — registration, one-time tokens, revocation,
   replay resistance, team scoping, signed confinement attestations;
2. a **worker lifecycle** — offer → claim → lease heartbeat → finish, with
   expiry that never double-executes;
3. exit evidence: **revocation, replay protection and team isolation verified**,
   and the **documented trust assumptions** of a customer-controlled host.

## Audit (2026-10-28)

| Area | Observation | Where |
|---|---|---|
| Execution host today | one shared-token daemon that holds the Docker socket; runs are claimed from `run_claims` by the orchestrator's own worker | `section4/.../worker_daemon.py`, `run_queue.py` |
| Durable queue | `run_claims` is frozen (TEXT instants), team caps, heartbeats, zombie reaper, `worker_id` fencing | `0006_run_claims.sql`, `run_queue.py` |
| Auth surface | per-route `get_principal` (JWT session); no machine-credential auth existed | `auth.py` |
| Policy gate | every `/api/` route must be classified in `IDENTITY_RULES`; unclassified routes fail the suite | `contracts/.../policy_enforce.py`, `test_policy_gate.py` |
| FK law | every FK pinned in the deletion map and counted (inventory 49 at HEAD) | `test_fk_deletion_story.py` |
| Secrets in audit | `_scrub` drops token/key-shaped detail keys; token is shown once and hashed | `audit_emit.py` |

There was **no runner registry, no runner credential, no replay guard and no
attestation record**. A shared daemon token also cannot be scoped to a team, so
it could never be handed to a customer.

## Threat model

Assets: the harness database, every team's runs and artifacts, model/provider
credentials, the audit log.

Actors: a team admin (mints and revokes runners), a customer-host runner (holds
the token and executes work), a network attacker (captures requests), a hostile
customer host (controls its own machine), other tenants (hold their own tokens).

| Threat | Consequence | Mitigation in this slice | Residual |
|---|---|---|---|
| Stolen runner token | read/claim another team's work | token is bound to one team; every job lookup joins the runner's team; foreign/unknown ids answer the same 404 | none for cross-team; the token can act *within* its own team until revoked |
| Replayed capture | duplicate claim/finish, double execution | `X-Runner-Nonce` is single-use per runner (composite PK insert), `X-Runner-Timestamp` must be within ±300 s; the nonce is committed before job state is read | a live man-in-the-middle can forward a fresh request once; mTLS is not shipped |
| Revoked runner keeps working | unbounded access after offboarding | revocation is a row update checked on every request, before nonces or jobs; idempotent 204 | in-flight leased work must still be released by lease expiry |
| Token leaks into logs/audit/model | credential reuse | token returned once, only sha256 stored, hint (last 4) in projections; audit scrub drops key/token-shaped detail | the customer must store it; no rotation endpoint yet |
| Hostile customer host lies about confinement | false sense of isolation | attestation is **signed with a token-derived key and digest-bound**, but it proves *who claimed what*, not host integrity | by design: the customer owns the machine; the harness protects the server and keeps approval/audit server-side |
| Double execution after lease loss | two workers run one run | the run_claims lease is owned by `runner:<id>`; finish deletes only that worker's row; an expired lease flips the job to `released` and the run to **orphaned**, never `queued` | orphaned runs need a human re-dispatch |
| Nonce table growth | storage exhaustion | nonces older than 2× the skew window are pruned on the auth path | steady-state size is bounded by request rate × window |

**Trust assumptions for a customer-controlled host** (explicit):

1. *The host is trusted to execute what it is told and to report honestly.*
   Nothing here makes a hostile host safe; plan approval, policy decisions,
   metering and audit remain server-side and are not re-derived from runner
   claims.
2. *The token is the identity.* Anyone holding it acts as that runner inside its
   team; there is no second factor (mTLS, hardware attestation) yet.
3. *Attestation is a configuration claim.* The signature proves the holder
   produced the claim and the digest is immutable afterwards; it does not prove
   the kernel, container runtime or policy file are what was claimed.
4. *One database, one clock window.* Replay resistance assumes the server's
   clock is the arbiter (consistent with the existing DB-clock staleness law).

## What shipped

### Storage (Section 1, new numbered migration)

**`0068_team_runners.sql`** — three tables, no existing migration edited:

* `team_runners` — one row per `(team_id, name)`; `team_id` → teams
  `ON DELETE CASCADE`; `token_sha256` (unique) and `token_hint` only;
  `attestation_key` = `HMAC-SHA256(token, "harness-runner-attestation-v1")`
  (one-way derivative, safe to store); `confinement` in `docker|process|none`;
  `attestation_json` ≤ 8 KiB with `attestation_sha256`/`attestation_at`;
  `last_seen_at`; `revoked_by/at`; `created_by`/`revoked_by` `SET NULL` so
  deleting an account cannot delete a team's runner rows.
  **There is no column for a command, tool, path or permission.**
* `runner_nonces` — `(runner_id, nonce)` primary key, `runner_id` CASCADE. The
  composite key is the atomic replay arbiter.
* `runner_jobs` — `run_id` primary key, `runner_id`/`project_id` FKs CASCADE,
  state `offered|leased|running|completed|failed|released`, lease expiry and
  result digest. **No FK to `run_claims`/`agent_runs`**: the queue creates rows
  lazily by design (0006), so an offer can precede the first persisted event.

### HTTP surface (Section 1)

| route | auth | role |
|---|---|---|
| `GET /api/v1/team-runners` | session | any member of the team (viewers/guests read) |
| `POST /api/v1/team-runners` | session | **admin** — returns the token **once** |
| `POST /api/v1/team-runners/{id}/revoke` | session | **admin**, idempotent 204 |
| `POST /api/v1/team-runners/{id}/jobs` | session | admin/engineer — offer a **queued** run |
| `POST /api/v1/runner/heartbeat` | runner token | signed confinement attestation |
| `GET /api/v1/runner/jobs` | runner token | that runner's open jobs only |
| `POST /api/v1/runner/jobs/{runId}/claim` | runner token | holds the `run_claims` lease as `runner:<id>` |
| `POST /api/v1/runner/jobs/{runId}/heartbeat` | runner token | renews lease + run claim; answers `cancel` once the log shows the run stopped |
| `GET /api/v1/runner/jobs/{runId}/spec` | runner token | the approved plan's commands, forbidden/scope paths and limits (execution leg) |
| `POST /api/v1/runner/jobs/{runId}/events` | runner token | bounded, scrubbed `tool_call`/`tool_result` events (execution leg) |
| `POST /api/v1/runner/jobs/{runId}/finish` | runner token | deletes only its own claim and settles the run (execution leg) |

Refusals: unknown/revoked token → **401** with no distinction beyond the message;
replayed nonce → **409** `REPLAY_REJECTED`; stale timestamp → **401**
`STALE_REQUEST`; wrong confinement or bad signature → **409**
`ATTESTATION_MISMATCH`; unknown attestation keys → **422**; foreign/unknown
runner or run → **404 with no existence oracle**; assigning a non-queued run →
**409** `NOT_QUEUED`; a revoked runner takes no jobs → **409**
`RUNNER_REVOKED`; lost lease → **409** `LEASE_LOST`.

`audit.py` gained five allowlisted actions (`runner.registered`, `runner.
revoked`, `runner.attested`, `runner.job_assigned`, `runner.job_completed`);
detail carries ids, names, hints, states and digests — never the token, the
attestation key or attestation content. `contracts/.../policy_enforce.py`
classifies all nine routes (the route-classification gate fails otherwise).

### Tests

`section1/tests/test_runner_api.py` — 10 offline tests on aiosqlite: one-time
token and stored hashes, role matrix, duplicate name, revocation killing the
token on the next request without burning a nonce, replay/staleness, signature
and confinement verification with unknown-key refusal, stolen-token isolation
(list empty, claim/finish/assign/revoke all 404 with identical bodies), the full
lease lifecycle, non-queued refusal, and expiry → `released` + `orphaned`.
`test_fk_deletion_story.py` pins the six new FKs (inventory 49 → 55).

### Live drill

`scripts/e2e/w13_runner_drill.py` runs against the compose stack (PostgreSQL):
registration, signed attestation, tamper/mismatch/unknown-key refusals,
replay and staleness, cross-team 404 equivalence, claim/heartbeat/finish with
the lease read back from `run_claims`, expiry orphaning, and revocation.

## Outcome (2026-10-29)

**Live drill `scripts/e2e/w13_runner_drill.py`: 42/42 checks PASS.**
`docs/briefs/evidence/eval/w13-runner-1793260362.json` — live compose stack on
PostgreSQL, backend images built from this tree, migration 0068 applied by the
new migrate image, no model call.

Judged from the durable rows the API wrote, not from the runner's own echo:

* **one-time secret:** the minted `hrn_…` token appears in exactly one response;
  the stored row keeps `sha256(token)` and the derived attestation key, and the
  list projection has no token field;
* **attestation verifies:** the signed claim is accepted, its digest equals the
  sha256 of the canonical JSON and matches the stored column; a forged signature,
  a confinement that differs from registration, and an unknown key are refused;
* **replay resistance:** the same nonce twice answers 409 `REPLAY_REJECTED`; a
  timestamp one hour old answers 401 `STALE_REQUEST`;
* **team isolation:** team B's token lists nothing and its claim/finish/assign/
  revoke of team A's ids produce 404 bodies **byte-identical** to an unknown id;
* **lease lifecycle:** the offer is accepted only for a queued run; the claim
  flips `run_claims` to `running` with `worker_id = runner:<id>`; a second claim
  is refused; the lease heartbeat renews; finish deletes exactly that runner's
  claim (row gone) and marks the job completed;
* **expiry:** a lease backdated an hour releases the job and orphans the run —
  never re-queues it, so the server cannot execute work the host may still hold;
* **revocation and rotation:** revocation is idempotent 204 and the very next
  request with the old token is 401 **without burning a nonce**; a revoked runner
  accepts no further offers. Rotation returns a new one-time token, kills the old
  token on the next request, and its new attestation key verifies;
* **anonymous body:** a request with no credentials and a malformed body answers
  401, never 422 — authentication runs before body validation.
  Residual live claims for the drill's projects: 0.

**Tests.** `test_runner_api.py` 10 (Section 1, offline). **Full Section 1 suite:
1009 passed, 54 skipped** — including `test_policy_gate.py` (all nine new routes
classified) and `test_fk_deletion_story.py` (six new FKs pinned, inventory
49 → 55). Sections 2/3/4/5/6/8/10/11 and the MCP server all pass
(1858 / 436 / 123 / 91 / 91 / 50 / 18, 48). `make lint` is green for all nine
sections and `make static-gates` passes after regenerating
`identity.openapi.json` (133 paths) and the Python SDK client (9 operations).

## Findings

1. **Auth order is a design decision, not a detail.** The first cut checked the
   token inside the handler, so an anonymous caller with a malformed body got
   422 plus the schema. Authentication is now a FastAPI **dependency**, which
   runs before body validation: an anonymous malformed request answers 401 and
   the body is never parsed (test: `test_anonymous_malformed_body_is_401_not_422`).
2. **The queue needs a real BYO dispatch seam.** The orchestrator polls
   `run_claims` every 0.5 s and would claim (then requeue) a seeded row mid-drill;
   the drill therefore saturates the team's cap first. That workaround is exactly
   the missing product seam named in §Remaining.
3. **Nonce burn after the status check is the right order.** A revoked token
   cannot grow `runner_nonces` (verified: one nonce row after the accepted
   heartbeat, none added by the post-revocation attempt).
4. **"Verifiable attestation" has a precise limit.** The derived key proves the
   token holder made these claims and the digest freezes them; it cannot prove
   the host is honest. The evidence therefore reports verification of a
   *configuration claim*, never of a *machine*.
5. **Spec regeneration has two different sources.** `identity.openapi.json` must
   come from the live service (its `/ready` route exists only with a DB
   factory), while `orchestrator.openapi.json` is the router fragment
   `spec_drift.py` compares against the app — running `fetch_specs.py` blindly
   overwrites the latter with the live superset and reddens the gate.

## Gate position

| W13 exit claim | State |
|---|---|
| Auth/token threat model defined | **pass** — table above, incl. trust assumptions |
| Worker lifecycle defined | **pass** — offer → claim → lease heartbeat → finish, expiry orphans |
| Attestation design defined and verifiable | **pass** — token-derived HMAC, digest-bound, mismatch refused |
| Revocation verified | **pass** — live drill |
| Replay protection verified | **pass** — live drill (nonce reuse + stale timestamp) |
| Team isolation verified | **pass** — live drill (stolen token, identical 404s) |
| Worker lifecycle verified | **pass** — live drill |
| Trust assumptions documented | **pass** — this document |
| A run executes on a customer-controlled host | **PARTIAL** — a real runner process executes the approved plan's commands through the Section 5 shell gate and the run settles from its recorded results (isolated drill, 16/16). Same machine as the services, test commands only, no agent loop (§Execution leg, §Remaining) |
| Runner UI / mTLS / rate limits | **not shipped** |

## Execution leg (2026-10-30)

**What a BYO run does now.** The admin may offer an approved run to a runner *before* it
starts. `start_execution` still announces and queues the run (the queued `run_claims` row is
what the runner claims), then applies the same budget/tier gate the local worker applies
before a spawn, and returns: `claim_next_run` skips any run that has a `runner_jobs` row, so
the orchestrator never spawns an engine for it. The runner then:

1. attests, lists its jobs, claims one;
2. `GET …/spec` — the commands are the approved plan's `tests`, read from `agent_plans`
   (status `approved`) on the server, with its forbidden paths, the intent's scope paths and
   the limits. More than 20 commands (`PLAN_TOO_LARGE`) or none (`NO_COMMANDS`) is refused,
   never truncated;
3. runs each command through `shell_gate.py` (allow-list, forbidden and scope paths,
   Seatbelt/Landlock — no confinement means refused), fail-fast, heartbeating while a
   command runs and killing it on `cancel` or a lost lease;
4. `POST …/events` — one `call` and one `result` per command. The server names the tool
   (`bash`), rebuilds the payload, refuses any command that is not in the approved plan
   (`COMMAND_NOT_IN_PLAN`), re-scrubs token-shaped strings and the runner's own bearer token,
   bounds output to 2000 characters, caps a job at 80 events and a batch at 50, and writes
   the batch only if every event validates;
5. `POST …/finish` — releases the lease **and settles the run exactly as a local engine
   does**: `engine_completed` (→ the human done-gate, `awaiting_done_signal`) or
   `engine_failed` (→ `blocked`). The verdict is re-derived from the recorded results: a
   runner that says `completed` without a passing result for every approved command is
   settled `failed`. A run the log no longer shows as executing (a human stop) only loses its
   lease and never gets an `engine_*` event.

**Why the log, not a call into the orchestrator.** Sections may not import each other and the
runner routes live in Section 1, but the run lifecycle already replays from `agent_events`
(M19-N1/Part 54). Section 1 appends to the same log through its `EventBus`; the orchestrator
drops its stale cached copy and replays. The event is published *before* the claim is deleted,
so a failed publish leaves the lease held and `finish` retryable instead of stranding a run
`executing` with no owner.

**Trust assumptions, unchanged.** The host is trusted to run what it is told and report
honestly. Approval, the command allow-list, caps, the budget gate and the terminal verdict
stay server-side. Nothing here proves the host is honest: a host that runs nothing and
fabricates a passing `tool_result` is indistinguishable from one that ran it.

**Decisions worth knowing.**

* *Any* `runner_jobs` row excludes a run from the local worker — including `released`,
  `completed` and `failed` — so a lost lease, or a retry after a finished job, can never move
  customer code onto the harness host. The cost is that such a run needs a human to re-dispatch
  it, and today there is no endpoint for that (§Remaining).
* Plans proposed through the API always list `pytest tests/` as their test command, so that
  is what a BYO runner runs. The approver sees that list at plan approval.
* A run with no plan row (the lease-only jobs of the control-plane tests) keeps the old
  behaviour: `finish` releases the lease and emits nothing.

**Evidence.** `docs/briefs/evidence/w13/` — an isolated drill on a throwaway PostgreSQL with
the real identity and orchestrator services, before (a `git archive` of `e9e5589`) and after.
See its README for what is simulated. Model spend: **$0, 0 model calls** (the orchestrator's
engine was the provider-less echo backend).

**Tests.** `section1/tests/test_runner_exec.py` (15, offline), `section4/tests/unit/
test_byo_dispatch.py` (10), `section5/tests/unit/test_byo_runner.py` (15, including the real
shell gate). Full suites: Section 1 1222 passed / 63 skipped, Section 4 1910 / 59, Section 5
455 / 8. `make static-gates` passes after recording the two new routes (`identity.openapi.json`,
the Python and TypeScript SDKs and `clients/ts/dist`) and the `HARNESS_RUNNER_TOKEN` knob.

## Hardening pass (2026-10-29, same day)

Closed after the first live gate:

* **Auth before body.** The runner token is a route dependency, so the token,
  revocation and nonce checks run before Pydantic sees the body. New test:
  anonymous `POST /runner/heartbeat` with `{}` → 401 `UNAUTHORIZED`; an
  authenticated malformed body still gets 422 with detail.
* **Token rotation.** `POST /team-runners/{id}/rotate` (admin, active runners
  only) rewrites `token_sha256`, `token_hint` **and** the derived attestation
  key, is audited as `runner.rotated`, and returns the new token once. No
  overlap window: the old token and its signatures are dead immediately; an
  in-flight lease survives until expiry. Rotation then revocation is the
  offboarding path; rotating a revoked runner answers 409 `RUNNER_REVOKED`.
* **Nonce purge.** The auth path prunes inline; `purge_runner_nonces()` plus a
  background sweep (`HARNESS_RUNNER_NONCE_PURGE_SECONDS`, default 600, 0 off)
  is the backstop for quiet runners. Test proves only expired rows go.
* **Spec-fetch footgun.** `fetch_specs.py --only identity` scopes the refresh;
  the docstring states that `orchestrator.openapi.json` is a router fragment and
  must not be overwritten with the live superset.

## Remaining (explicitly open)

* **Agent loop and a real remote host.** The execution leg (below) runs the approved plan's
  *test commands* on the host. A model-driven agent loop on the customer host (editing code,
  producing a diff/PR) is not built, and no run has yet crossed a real network to a machine
  that is not the services' own host.
* **Accounting and notifications for BYO runs.** The runner's tool calls are not counted
  against the Part 44 per-run tool-call limit (the per-job event cap bounds them instead),
  `finish` records no cost (no model call is made on the harness side), and the
  `_RUN_PROBLEMS` in-app notification fires only for events Section 4 itself emits, so a
  BYO run that settles `engine_failed` is `blocked` without telling the run's participants.
* **Re-dispatch after a lost lease.** A lost lease orphans the run, and `runner_jobs.run_id`
  is the primary key, so there is no endpoint to offer the same run to another runner.
  The local worker never takes such a run either (see "Execution leg").
* **Mid-command cancellation in the drill.** `cancel` on the job heartbeat and the runner
  killing the command are unit-tested (fake server, real gate); the isolated drill does not
  exercise a human stop.
* **Transport hardening.** mTLS or request signing on top of the token, plus
  per-runner rate limits (the identity rate limiter is not yet wired here).
* **UI.** Registration, revocation, rotation and lease status are API/SDK only.

## Limitations

* Cross-team proof uses two real teams on one stack, one host, one database.
* Attestation storage is last-write-wins; history is the audit log, not a
  separate timeline table.
* `runner_jobs` has no retention story yet; completed rows stay until the
  project/runner is deleted.
* The drill seeds a queued `run_claims` row directly because no BYO dispatch
  endpoint exists; that is recorded in the evidence rather than hidden.
* The execution drill runs the real runner as a separate process on the services' own
  machine: it proves the protocol, the shell gate and the log path, not a WAN or a hostile
  host.

## Carry-over

* **W14:** a real remote run with before/after artifacts (and the agent loop on the host,
  if that is wanted); only after that may the roadmap gate be marked pass.
* **M01:** native-tool confinement remains open; nothing here widened it — the
  new surface is a control plane plus a lease over the existing queue.
