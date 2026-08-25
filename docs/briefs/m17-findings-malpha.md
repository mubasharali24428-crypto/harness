# M17 PHASE A — M-ALPHA FINDINGS (correctness/math lens)

Seat: M-ALPHA · ELITE-FORGE TEAM 1 · repo @ `3c794b2` · 2026-08-24
Lens: money math, pagination cursors, concurrency CAS/claims, replay seq-window.
Method: code read of S1/S3/S4/S6 hot paths + adversarial probes run live
against PostgreSQL 18 (`malpha_*` scratch DBs) and SQLite mirrors.
Probes preserved at `/tmp/malpha_probe/` (probe_pg2.py, probe_pg4_race.py,
probe_pg3_diag.py, probe5_edges.py, probe1_money.py). No fixes applied.

Backbrief (U7): mission = find what six waves missed in numeric/concurrent
claims. Constraints: no git writes, findings only. Done: 4 lens areas probed,
2 CRITICAL + 2 HIGH + 5 MEDIUM below.

---

## F-1 [CRITICAL] — budget_guard.reserve() cannot execute on its production driver; every reserve fails closed

Evidence: `section3/src/harness_router/budget_guard.py:155` passes
`RESERVE_SQL` (named `:bind` style) straight to `pool.fetchrow`. The module
ships `to_positional_params()` ("asyncpg only accepts positional arguments")
but **never calls it** — grep: referenced 1x (its own def).

Probe (real asyncpg pool, fresh PG 18 DB, migrations 0001/0007/0010 applied):

```
A> single stock reserve(): allowed=False reason=ledger unavailable
A> to_positional_params referenced 1x in module (def + docstring only => never invoked by reserve())
```
Traceback: `asyncpg.exceptions.PostgresSyntaxError: syntax error at or near ":"`.

Impact: the designated money-path enforcement point (W1-A3) is dead on the
production driver — 100% fail-closed rejections (or silent no-enforcement if a
caller treats "ledger unavailable" as soft). Its concurrency property was
therefore **never exercised on Postgres** — which is how F-2 survived six
waves. Today `reserve()` has zero src callers (tests only), so this ships as a
loaded trap for Phase B wiring.

## F-2 [CRITICAL] — the "atomic" reserve does not serialize on Postgres: concurrent reserves overspend

`RESERVE_SQL` is a single INSERT..SELECT..WHERE..RETURNING whose docstring
claims "concurrent reserves serialize … each sees all previously COMMITTED
ledger rows". False under READ COMMITTED: the SUM subquery snapshots past
other transactions' **uncommitted** inserts and no lock is taken, so N racing
claims each see the same pre-race total.

Probe (translated SQL — i.e. with F-1 fixed — raw asyncpg pool min/max=8,
fresh DB per round, 8 × $1.000000 claims vs monthly_budget_usd = 5.0):

```
E r0> winners=8 over-budget=0 errors=0 sum=8.000000 rows=8 -> VIOLATION
E r1> winners=8 ... sum=8.000000 -> VIOLATION
E r2> winners=8 ... sum=8.000000 -> VIOLATION
E r3> winners=8 ... sum=8.000000 -> VIOLATION
E r4> winners=7 ... sum=7.000000 -> VIOLATION   (scheduling luck, still >5)
E r5> winners=8 ... sum=8.000000 -> VIOLATION
```
Up to **60% budget overrun**, silently, on the happy path.

Why the shipped test can't see it:
`section1/tests/test_budget_atomic.py::test_eight_threads_dollar_claims_against_five_dollar_budget`
passes only because its harness class `_RawConn.reserve_once` opens
`BEGIN IMMEDIATE` before running the production SQL (line 125). Writer
serialization lives in the **test fixture**, not in any production code path
(`_SessionPool` adapter and real asyncpg pools have none). The 5-winner
assertion proves SQLite's write lock, not the guard.

Related mislabel (same probe series, probe_pg3_diag.py): when the losing path
raises instead (FK violation on an unresolvable `run_id`), `reserve()` reports
`reason="ledger unavailable"` for a pure data error — operators will chase
phantom outages.

Fix shape for Phase B (not applied): serialize on a team-level lock —
`SELECT ... FROM billing_plans WHERE team_id=$1 FOR UPDATE` inside one
transaction around check+insert, or `pg_advisory_xact_lock(hashtext(team_id))`,
then re-run the predicate. One owner: section3/budget_guard.

## F-3 [HIGH] — three disagreeing budget caps + opposite failure postures; the fail-open one is the live path

- Live orchestration gate: `section4/src/harness_orchestrator/service.py:_budget_gate`
  → `section4/.../budget.py:check_budget`. Cap resolution: `teams.budget_usd`
  → env `HARNESS_TEAM_BUDGET_USD` → **unlimited**. `billing_plans.monthly_budget_usd`
  is never consulted. On ANY DB error it degrades to `allowed=True`
  (budget.py:144 — fail OPEN).
- Atomic guard: `section3/.../budget_guard.py:reserve` caps on
  `billing_plans.monthly_budget_usd` ONLY (ignores `teams.budget_usd`, env),
  and fails CLOSED.
- Billing UI: `section1/.../billing.py:_quota_payload` uses
  `max(billing_plans, teams.budget)`.

One team, three truths. Probe (seeded: billing_plans=$0, teams.budget=NULL,
env unset):

```
PROBE3> check_budget(billing_plans=0, teams.budget=NULL, env unset): allowed=True cap=None
PROBE3> reserve(same team, $0.05 claim): allowed=False reason=over budget
```

The TOCTOU the guard was built to kill is still the production behavior: two
concurrent engine completions both pass `check_budget` (read-then-decide) and
both spend; the only serialized component is one nobody calls. Month-boundary
drift bonus: `check_budget` sums **all-time** spend (no month filter) while
`reserve`/quota filter `created_at >= date_trunc('month', now())` (DB clock)
and billing quota uses an app-computed month start (`_month_start_utc`) —
three windows over the same ledger.

## F-4 [HIGH] — NUMERIC(12,6) overflow silently drops ledger rows (metering/revenue loss)

`_to_money` (S1 cost_ledger.py:40, S3 ledger.py:95) quantizes scale but never
range-checks precision; `record_usage` swallows every exception into
`return None` + warning. Any value ≥ 1,000,000.000000 (or any FK/length
failure) ⇒ row vanishes, caller proceeds as if metered:

```
I> _to_money(123456.123456) = 123456.123456  digits=12 (NUMERIC(12,6) max is 999999.999999)
I> record_usage(cost=123456.123456) returned None (None == row silently dropped, caller never knows)
```

H-023 fixed float *drift* end-to-end (verified: repr-based conversion +
text binds + Decimal aggregation are sound; probes PROBE5 casts/bind-order
intact) but left *magnitude* unguarded. Phase B should clamp-or-reject at
`_to_money` and surface non-metering distinctly from transient outage.

## F-5 [MEDIUM] — runs-index keyset cursor loses rows if `agent_runs.created_at` is ever NULL

`(r.created_at, r.id) < (:c,:r)` + `ORDER BY created_at DESC, id DESC`
(runs_index.py:82-109): NULLs sort LAST on DESC, so a NULL-created run lands
at the newest page tail; its encoded cursor carries `c=null` and the next-page
predicate `tuple < (NULL, id)` matches nothing — everything older becomes
unreachable by cursor walking.

```
H> page1 order (NULL created_at sorts): ['run-A', 'run-C', 'run-B']
H> page2 after cursor(None,run-B): []
```

Frozen DDL declares `DEFAULT now() NOT NULL` (0001:137-138) so prod writes via
the frozen seam are safe today; the exposure is any ORM/raw insert omitting
the column (SQLite accepts NULL; nothing in the layer re-asserts it). Cheap
Phase-B hardening: `COALESCE` sentinel in the keyset pair or a CHECK/NOT NULL
re-assertion migration. No dup/skip otherwise: strict `>`/`<` bounds verified,
limit+1 peek is correct off-by-one hygiene, garbage cursor → 400 (good).

## F-6 [MEDIUM] — meeting messageSeq is positional over volatile memory: renumbered by restart, forked per worker

`MeetingRoom.messages` is a plain list; `messageSeq` = index+1 minted at read
time (api.py get_messages `(first_seq or 0)+i`). Docstrings promise "stable
and monotonic" transcript positions, but:
- process restart ⇒ transcript empties ⇒ seq space resets ⇒ old `?before=`
  cursors alias onto DIFFERENT messages (the "honest empty page" contract
  only covers unknown ids);
- multi-process deploy (uvicorn workers / replicas, no sticky session) ⇒ each
  worker owns disjoint rooms; pagination and posting hop between workers ⇒
  404s/garbled order;
- append-only holds only within one process lifetime; nothing persists S6
  chat (grep: no meeting message table).

Boundary math itself is clean (probes G): strict `before` exclusion,
tail-clamped future cursors, correct empty pages — the defect is durability,
not arithmetic. At "1000 users" this is the first S6 thing to break (PBC1
will feel it too).

## F-7 [MEDIUM] — WS replay: overflow during replay burst stays silent until the NEXT event arrives

project_ws (app.py:656-673) checks `queue.overflowed` only after delivering an
envelope. Sequence with no self-heal: subscriber queue (maxsize 1000)
overflows during the replay/live overlap ⇒ drops flagged ⇒ project goes quiet
⇒ loop parks in `queue.get()` forever ⇒ client sits on a silent seq hole with
a healthy socket. The 1013 reconnect only fires on the next publish. Replay
itself is genuinely strong — verified no-dup/no-gap under concurrent publish
(test_ws_replay_cap isolation proof is real; truncation marker + oldestSeqSent
+ REST backfill compose correctly; strict `after_seq` confirmed) — this is the
one residual window.

## F-8 [MEDIUM] — concurrent done-signal double-fire turns the loser into HTTP 500 (should be 409)

Route (api.py:622-650): `gate_done_signal` (liveness check) then
`service.signal_done` → `sm.signal_done()`. Two concurrent POSTs on the same
run: T2 gates while T1 is mid-review (state REVIEWING — non-terminal ⇒ gate
passes), then T1's verdict lands DONE, then T2's `sm.signal_done()` raises
`InvalidStateTransitionError` from the terminal guard — uncaught by the route
(which maps only DoneSignalRejectedError subclasses) ⇒ 500. The gate/act
window is exactly the TOCTOU H-007 was meant to close; liveness needs
re-validation inside the transition path or a typed 409 mapping. Also: double
reviewer spend when both arrive pre-verdict (evaluate_changes runs twice).

## F-9 [MEDIUM] — run-cost attribution races the resolver: spend can land on the WRONG run

`cost_ledger.record_usage(run_id=None)` resolves
"team's most recent run ORDER BY created_at DESC LIMIT 1" (cost_ledger.py:59-64)
in its own transaction, separate from the insert. Concurrent run creation ⇒
usage meters onto whichever run is newest-at-insert-time, not the incurring
run. All current S4 call sites pass explicit run_ids (good), so exposure is
the None-path API itself — one schema-shaped foot-gun away from corrupt
per-run `costUsd` aggregates (`_RUN_ENRICH_SQL`).

## F-10 [LOW] — orphan sweep uses two different clocks

`release_timed_out_claims` judges staleness by DB clock
(`heartbeat_at::timestamptz <= now()-interval`), `reclaim_orphans` by the app
clock (`now_utc() - stale` compared as TEXT). Between them a claim can be
orphaned-but-not-reaped or vice versa under clock skew; TEXT compare survives
only because both sides emit identical `+00:00` ISO formatting — one timezone
format change away from breaking. Consolidate on the DB-clock predicate.

---

### Verified-sound (adversarially probed, no finding)

- Money typing end-to-end: repr-based float→Decimal, text binds, SQL-side
  NUMERIC sums, Decimal fold-then-wire (ledger.py/cost_ledger.py/pricing.py).
  CAST preservation + duplicate-bind folding in `to_positional_params` correct
  (PROBE5) — it's just never wired (F-1).
- Replay seq-window: strict `>` watermark, ascending batches, capped deque,
  honest truncation marker; WS+REST composition proven no-dup/no-gap under a
  live publisher (existing fapp test reproduces independently here).
- Runs-index peek-one pagination arithmetic; guard release() compensation
  incl. idempotent double-release (True/False, 0 rows left).
- Meeting page boundary math (exclusion, clamps, empty pages) — see F-6 for
  the durability caveat.

### Phase B dispatch suggestion
1. F-1+F-2 together (same module): translate binds + team-level lock; port
   the 8-thread test to real asyncpg WITHOUT BEGIN IMMEDIATE.
2. F-3: pick one cap source (recommend teams.budget > billing_plans > env)
   and one posture; route S4 through the fixed guard.
3. F-4/F-8: small, independent, low-risk.
