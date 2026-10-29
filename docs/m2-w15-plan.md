# Marathon 2.0 W15 — multi-tenant hardening (M2-W15)

Follows the 2026-10-29 W14 close (`fa59fd7`). The roadmap row in one line:
*per-team resource ceilings (partly done), noisy-neighbor tests, rate limits per
surface, secrets rotation runbook, backup/restore drill against real object
storage (the PITR drill exists — make it scheduled)*
(`docs/roadmap-2026-10-25.md:213`).

Scope approved 2026-10-29: all four slices, in the order below.

## Purpose

W14 made spend visible and models comparable. W15 asks the next question: what
stops one tenant, one surface or one operator mistake from hurting the rest of
the platform? Four claims, each with evidence:

1. **A busy team cannot starve another.** A per-team ceiling exists, but it is
   also the size of the whole worker pool, and claims are global FIFO.
2. **Every expensive surface is rate limited, per team as well as per IP.**
   The limiter is real; two of its surfaces are never installed, and no budget
   is keyed by team.
3. **The master key can actually be rotated.** The re-encryption script only
   converts derivations under the *same* key; a new key leaves every stored
   secret undecryptable.
4. **Restore is proven on a schedule, not a memory.** The PITR drill is real
   and manual.

## Audit (2026-10-29, source inspected)

| Area | Observation | Where |
|---|---|---|
| Per-team concurrency | `_CLAIM_SQL` counts `running` rows per team against `:max_per_team`, overridable per plan. `HARNESS_RUN_CONCURRENCY` (default 4) is both the per-team cap and the pool size, so one team can hold every slot | `section4/.../run_queue.py:250-273`, `deploy/runners/services.py:416` |
| Claim order | `ORDER BY rc.claimed_at ASC` — global FIFO. A team with a long queue is served strictly by age, so a second team's first run waits behind all of it | `run_queue.py:264` |
| Isolation drills | Three overlapping runs in ONE team prove workspace/network/checkpoint isolation; quotas cover depth/children/pool. **No test runs two teams against one pool** | `scripts/e2e/w8_concurrency_drill.py`, `w8_agents_quota_drill.py` |
| Rate limiter | Postgres fixed-window, keyed per normalized IP + route template, fails open, bucket GC. Installed on identity and orchestrator only | `section1/.../ratelimit.py`, `deploy/runners/services.py:267,436` |
| Unwired surfaces | S3 `POST /api/v1/models/call` and S6 `POST /api/v1/meetings/{id}/brief` have hook modules but no install call in the service factories | `section3/.../ratelimit_hooks.py`, `section6/.../ratelimit_hooks.py`, `services.py:316-...` |
| Team-keyed limits | None. Every limited route keys on IP, so one team's scripted traffic is indistinguishable from its NAT neighbours | `ratelimit.py` `_bucket_key` |
| Vault encryption | Salted-KDF `KeyVault`, dual-read (salted then legacy), KDF params row stores the salt in cleartext (so a new master key can re-derive) | `section1/.../crypto.py:120-213` |
| Re-encryption | `reencrypt_all_payloads` re-wraps `api_keys` only, deriving **both** read and write keys from the CURRENT master key. It converts SHA-256→salted derivation under one key; it cannot rotate the key | `crypto.py:281-333`, `section1/scripts/reencrypt_vault.py` |
| Other encrypted at rest | `webhooks.secret_encrypted`, `github_connections.access_token_encrypted`, SSO client secrets | `webhooks.py:370,498`, `models.py:114` |
| Token rotation | Runner tokens rotate over the API; webhook secrets rotate on update; the worker-daemon token is one shared env value with no rotation path | `runner_api.py:429-470`, `webhooks.py:483`, `worker_daemon.py:14` |
| PITR drill | Real MinIO S3 API, versioned bucket, separate `walg-db`, asserts recovery + row set, promotes, writes evidence | `scripts/pitr_drill.sh`, `Makefile:225` |
| Scheduling | Documented host-cron example for backups; the drill is "run monthly" prose. Nothing runs it | `docs/runbooks/backup-and-pitr.md:44-59` |

## Slice 1 — fair admission, and a two-team drill

**Change.** Keep the per-team ceiling; change how a queued run is chosen.
`_CLAIM_SQL` gains a fairness key: order by the number of `running` claims its
team already holds, then by queue age. With both variants (plain and
`billing_plans`-aware) sharing one ordering, a team holding zero or one slot is
served before a team holding three, so a saturated team cannot push another
team's work behind its whole queue. The per-team cap remains the hard ceiling;
fairness only decides order within it.

**Why SQL and not a worker-side scheduler.** The queue is the only shared state,
and the claim is one atomic statement — adding a second decision point would
introduce the exact race the claim SQL exists to avoid. No new table, no new
env knob.

**Evidence.**
* Unit tests over SQLite for both SQL variants: with five queued runs for team A
  and one for team B, four claims from an empty pool give B its run and leave at
  most three slots to A (today A takes all four).
* A PostgreSQL-gated test pinning the same behaviour on the real dialect.
* `scripts/e2e/w15_fairness_drill.py`: against the live database, enqueue real
  `run_claims` rows for two teams and drive `claim_next_run` — no engine, no
  model spend — then assert the claim order and that A never exceeds its cap.
  Writes dated evidence JSON.

## Slice 2 — limits on every expensive surface, per team as well as per IP

**Change.**
* Install the existing hooks: `app_models()` gets `install_router_rate_limit`,
  `app_meetings()` gets the meeting hook. Both already resolve the shared
  session factory; no new storage.
* Add a team-keyed budget for the two spend surfaces. The limiter middleware
  runs before authentication and must not parse tokens, so the team check lives
  where the team is known: a small `check_team_budget(session, team_id, route,
  limit)` helper in `harness_contracts.ratelimit` using the same `rate_buckets`
  table and upsert, called from the orchestrator run-start route and the S3
  model-call route after the principal/team lookup. Keys are namespaced
  (`team:<id>:<route>`) so an IP bucket can never collide with a team bucket.
* Defaults are generous abuse guards, not UX blockers: a team budget above what
  a human workflow can reach and below what a runaway script reaches. Names:
  `HARNESS_TEAM_RUN_START_LIMIT` / `HARNESS_TEAM_MODEL_CALL_LIMIT`, both per
  minute, both optional overrides.

**Evidence.** Unit tests for the helper (keying, window reset, store failure
fails open with a warning), route tests for the 429 contract on both surfaces,
hook-installation assertions for both apps, and a live check that a rapid
model-call burst answers 429 with the `Retry-After` contract while a normal call
still succeeds.

## Slice 3 — master-key rotation, proven on a copy

**Change.**
* `rotate_master_key(session, old_key, new_key)` in `crypto.py`: derive the old
  read keys (salted + legacy) and the new write key from the SAME cleartext salt
  row, decrypt every payload, re-encrypt, verify the round-trip, and return
  counts only. A row that cannot be read under the old key is counted `failed`
  and left untouched — rotation must never destroy a secret it cannot read.
* Walk every encrypted column, not just `api_keys`: `api_keys.key_encrypted`,
  `webhooks.secret_encrypted`, `github_connections.access_token_encrypted`, and
  the SSO client secret column. The column list is declared in one place so a
  new encrypted column must be added there (a test pins the list against the
  models).
* `section1/scripts/rotate_master_key.py`: `--old-key-env` / `--new-key-env`,
  `--dry-run` (decrypt-only, counts, no writes), refuses to run when the two
  keys are equal, prints counts only.
* Runbook `docs/runbooks/secret-rotation.md`: order of operations (deploy reads
  old key → run rotation → deploy new key), runner-token and webhook rotation
  that already exist, the worker-daemon token (restart both sides), and the
  verification queries. States plainly that rotation is offline-safe only if
  the script's `failed=0`.
* Unit test on SQLite: seed rows under the old key, rotate, prove every row
  decrypts under the new key, old key now fails, a corrupted row is counted and
  untouched, and the dry-run writes nothing.

**Not in scope.** Key versions coexisting indefinitely (a per-row key id) —
that is a schema change with its own migration and is not needed to rotate.
`HARNESS_MASTER_KEY` remains a single active key.

## Slice 4 — a restore drill that runs without being remembered

**Change.**
* A scheduled platform automation (QoderWake) runs `make drill-pitr` on a
  monthly cadence and records the evidence JSON with its timestamp. Failure is
  visible: the automation's run status is the signal, and the script's non-zero
  exit is preserved.
* Evidence retention: the drill already writes into
  `docs/briefs/evidence/pilot-prep/`; the automation keeps the dated file and
  the plan records the last run.
* `docs/runbooks/backup-and-pitr.md` gains the drill cadence and the host-cron /
  launchd alternative for operators who do not run a QoderWake automation, so
  the claim does not depend on our control plane.

**Evidence.** One scheduled run observed end to end (trigger → evidence file →
status), plus the existing manual drill artifact as the baseline.

## Outcome (2026-10-29)

* **Fair admission — shipped.** `_CLAIM_SQL` orders candidates by their team's
  running count, then queue age. SQLite tests cover both SQL variants, a
  PostgreSQL-gated test repeats the order on PG 18.6, and
  `scripts/e2e/w15_fairness_drill.py` **PASSED** (claim order
  `busy-1, idle-1, busy-2, busy-3`; busy team held 3 slots, idle team 1; a
  saturated team was skipped): `docs/briefs/evidence/eval/w15-fairness-1793279165.json`.
* **Rate limits — shipped and live-proven.** S3 and S6 hooks are installed by
  their factories; run start and model call consume a team bucket in
  `rate_buckets` under `team:<id>|<route>`. Models went from unthrottled to
  exactly-N-then-429 with `Retry-After` on a live burst, while a second team
  kept its budget: `docs/briefs/evidence/eval/w15-rate-limit-1793281659.json`.
  Fixing the 429 header exposed an envelope bug: `HTTPException` headers were
  dropped when the body was normalized, so `Retry-After` (and
  `WWW-Authenticate`) never reached clients. Fixed, with a regression test.
* **Master-key rotation — shipped (offline proof).** `rotate_master_key` walks
  all four encrypted columns, re-wraps from the old key's read set to the new
  key, verifies the read-back, counts unreadable rows and leaves them
  untouched; `section1/scripts/rotate_master_key.py` adds dry-run and reads keys
  only from env vars. Runbook: `docs/runbooks/secret-rotation.md`. Live rotation
  was deliberately not run against the dev stack (it would invalidate its
  secrets); the four offline tests are the proof.
* **Scheduled restore — automation created, trigger blocked by account
  credits.** The automation `tr_a4315e8e62b81c7819aca10d` ("Monthly PITR restore
  drill") is enabled with next run 2026-11-01T12:00Z. Its immediate `run-now`
  failed before doing any work with the platform message "You've reached your
  credit usage limit" — an account limit, not a drill failure. The exact
  scheduled command was therefore run from the repo:
  `docs/briefs/evidence/pilot-prep/pitr-drill-20261029T140816Z.json` **PASS**
  (rows at target `1,2`, after disaster `1`, promoted, 4 s restore). The
  scheduled trigger itself is not yet observed; that leg stays open below.

* **Ledger and router now price from one card (gap found while auditing).** The
  operator rate card lived in Section 3, so Section 4's engine metering resolved
  rates from the frozen catalog only: an operator-priced model was quoted its
  card rate and booked at the invented 0.50/1.50 fallback. `rate_card` moved to
  `harness_contracts` (Section 3 keeps a re-export shim), `PricingEngine`
  resolves card → catalog → fallback, and a fallback logs a warning naming the
  model. Live proof on the deployed stack: with a card for
  `qwen/qwen3.7-flash` (0.03/0.13 per million, read from OpenRouter's `/models`),
  run `w2-code-search-default-1793287437` booked $0.000721 for 22,240/417 tokens
  — the card rate; the same row before the change booked $0.011746 at the
  fallback (`code-search-default-1793287217.json`). An earlier attempt with a
  malformed card was refused loudly (`1793287359.json`:
  `operator rate card is malformed; pricing from the catalog only` plus the
  named-model fallback warning), which is the intended posture.

## Gate position

| W15 exit claim | Evidence required |
|---|---|
| A saturated team cannot starve another | SQLite unit, PG-gated test, live fairness drill evidence |
| Expensive surfaces are limited, per team too | Hook-installation tests, 429 route tests, live burst check |
| The master key can be rotated | Rotation unit test on SQLite (all columns), runbook, `failed=0` rule |
| Restore is proven on a schedule | **partial** — automation enabled (next 2026-11-01T12:00Z) and the exact command PASSED today with dated evidence; the scheduled trigger did not run because the account hit its credit limit, so "trigger observed" stays open |

## Limitations

* Fairness is claim ordering, not preemption: a team already holding `max_per_team`
  slots keeps them until its runs finish. W15 does not add eviction.
* The fairness drill drives the queue, not the engine. It proves admission, not
  end-to-end latency under load; a load test at scale is W17.
* Rate limits are per (IP, template) and per (team, route); they are not a
  global load shedder, and the store still fails open by design.
* Rotation is a one-key, offline-window operation. It cannot guarantee that no
  write lands between the decrypt pass and the new-key deploy; the runbook
  states the window and how to detect a straggler (`failed` rows on re-run).
* The scheduled drill runs on one host against MinIO, not offsite cloud storage.
  Offsite copies remain an operator responsibility (W16).
* The automation could not be observed executing: `run-now` was refused by the
  account credit limit before the session started. The command and its evidence
  are proven; the scheduler's first firing is 2026-11-01T12:00Z.
* The live rate-limit check ran with a deliberately tiny limit (3/min) on a
  keyless deployment, so allowed calls answered 503 from the provider path. The
  limiter transition and team isolation are what the evidence shows; a 200 from
  the provider was not part of it.
