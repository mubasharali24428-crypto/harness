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
| `POST /api/v1/runner/jobs/{runId}/heartbeat` | runner token | renews lease + run claim |
| `POST /api/v1/runner/jobs/{runId}/finish` | runner token | deletes only its own claim |

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

## Outcome (fill after the live run)

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
| A run executes on a customer-controlled host | **OPEN** — the control plane and lease are real; the runner does not yet execute the approved plan or stream tool events into the orchestrator (`docs/m2-w13-plan.md` §Remaining) |
| Runner UI / token rotation / mTLS | **not shipped** |

## Remaining (explicitly open)

* **Execution leg.** The runner must execute the approved plan through Section
  5's shell gate and stream bounded, scrubbed tool events into the orchestrator's
  state machine, then finish the run. The seam is `start_execution`
  (`section4/.../service.py`): a BYO dispatch must skip the local spawner and
  let the runner's `finish` drive the run to a terminal state. Until that lands,
  a BYO runner can *hold and complete a lease*, not *execute the work*.
* **Token rotation.** Revoke-and-re-mint is the only path today; a rotation
  endpoint with an overlap window is the next step.
* **Transport hardening.** mTLS or request signing on top of the token, plus
  per-runner rate limits (the identity rate limiter is not yet wired here).
* **Nonce retention as a job.** Pruning happens inline on the auth path; a purge
  job is the right home if the table ever grows beyond the skew window.
* **UI.** Registration, revocation and lease status are API/SDK only.

## Limitations

* Cross-team proof uses two real teams on one stack, one host, one database.
* Attestation storage is last-write-wins; history is the audit log, not a
  separate timeline table.
* `runner_jobs` has no retention story yet; completed rows stay until the
  project/runner is deleted.
* The drill seeds a queued `run_claims` row directly because no BYO dispatch
  endpoint exists; that is recorded in the evidence rather than hidden.
* The customer host is simulated by the drill process, so the drill proves the
  credential/lifecycle boundary, not remote tool execution.

## Carry-over

* **W14:** the execution leg above, then a real remote run with before/after
  artifacts; only after that may the roadmap gate be marked pass.
* **M01:** native-tool confinement remains open; nothing here widened it — the
  new surface is a control plane plus a lease over the existing queue.
