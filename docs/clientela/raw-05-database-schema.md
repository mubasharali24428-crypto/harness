# Database/Schema Audit — AI Engineering Harness (`harness/`)

**Scope:** `section1/migrations/0001–0012`, Section 1–4 & 8 source, `deploy/` backup/compose wiring.
**Method:** confirm-and-write from pre-verified leads; read-only. All `file:line` verified against working tree.
**Verdict:** 14 findings — 1 CRITICAL, 6 HIGH, 5 MEDIUM, 3 LOW. Schema is clean for a demo; several paths degrade badly under sustained load.

**Lead corrections (evidence-based):**
- Lead #1 partially wrong: `0002_indexes.sql:3` creates `idx_agent_events_project_seq (project_id, seq)` — its leftmost prefix *does* serve replay-by-project. The real problems are growth/retention and per-row NOTIFY (Finding 1).
- Lead #10 corrected: zero JSONB-operator queries exist anywhere in source → no GIN index is warranted today (Finding 10).
- Lead #2 nuance: `audit_log` has **no `team_id` column at all** (0001:89–100) — per-team audit scoping isn't just unindexed, it's impossible without joining `team_members`.

---

## Summary table

| # | Sev | Finding | Location |
|---|-----|---------|----------|
| 1 | HIGH | `agent_events`: unbounded append log, no retention/partitioning, NOTIFY per row | 0001:155–165, 0003:19–22 |
| 2 | CRITICAL | Sensitive actions **not audited**: key mint/revoke, invite mint/redeem, plan change have zero `write_audit` call sites | api_keys.py:68–98, invites.py:77+, billing.py:225+ |
| 3 | MEDIUM | `rate_buckets`: no TTL sweep — one row per IP+route forever | 0011:7–11, ratelimit.py:28–67 |
| 4 | HIGH | `cost_ledger`: single writer (router), silent failure swallow, direct dsh runs bypass metering | ledger.py:120–148, api.py:77 |
| 5 | MEDIUM | `meeting_messages`: no index on `meeting_id`; unbounded per meeting | 0001:130–140 |
| 6 | MEDIUM | Every FK defaults to NO ACTION — team/project deletion blocked or manual; orphan risk | 0001:30,39–40,63,73,85–86,109,125–127,138–139,151–152,163–164,181,195,209–211,223–224 |
| 7 | MEDIUM | ORM types `cost_usd` as Python `float` over `NUMERIC(12,6)` — Decimal→float drift | models.py:156, 231 |
| 8 | HIGH | TIMESTAMPTZ vs TEXT-ISO split across schema — two time conventions | 0005:12–13,20–21 vs rest |
| 9 | LOW | CHECK constraints exist on only 3 columns; status/state columns elsewhere unchecked | 0008:18, 0009:13, 0010:14 |
| 10 | LOW | `notification_outbox.payload` JSONB never queried with JSON ops → GIN correctly absent; keep it that way | 0009:12 |
| 11 | HIGH | Default pool sizing ×7 services ≈ 105 potential connections vs PG default 100 | deploy/runners/services.py:51–55 |
| 12 | LOW | No vacuum/analyze/maintenance story; autovacuum defaults on append-heavy tables | repo-wide |
| 13 | HIGH | `run_claims` heartbeats compared as ISO strings — lexicographic, not chronological | 0006:9–10, run_queue.py:129 |
| 14 | HIGH | WAL-G PITR unusable as deployed: `archive_command` not configured in db image | docker-compose.yml:259–260 |

---

## Detailed findings

### 1. HIGH — `agent_events` grows forever; every insert fires a NOTIFY
**Where:** `section1/migrations/0001_initial.sql:155–165` (table), `0003_pg_notify.sql:19–22` (per-row trigger), `0012_agent_events_run_id_index.sql:6`.
Replay-by-project is indexed (`0002_indexes.sql:3`), and `(run_id, type)` probes are indexed (0012). What is missing is any lifecycle: no partitioning, no retention job, no archival. Every insert also executes `pg_notify` (0003:20–22); with no live listener attached, notifications queue server-side and the notify queue grows with the table.
**At scale:** an active fleet emitting tens of events/second reaches hundreds of millions of rows; every index insert slows, `VACUUM` falls behind on the append-heavy heap, and the async notify queue pressures memory. Replay of an old project degrades from index scan to scanning years of cold rows.
**Fix (new migration 0013 + scheduled job):**
```sql
-- near-term retention (batched, e.g. pg_cron every hour):
DELETE FROM agent_events
WHERE seq IN (SELECT seq FROM agent_events WHERE ts < now() - INTERVAL '30 days' LIMIT 10000);

-- structural fix: declare-range partitioning by month on ts
-- (requires rebuilding table since PK must include partition key):
--   CREATE TABLE agent_events (...) PARTITION BY RANGE (ts);
-- monitor: SELECT pg_notification_queue_usage();
```
Cap the trigger cost by coalescing fan-out through the existing `notification_outbox` pattern instead of per-row NOTIFY if throughput rises further.

### 2. CRITICAL — Sensitive actions are not audited (allowlist exists, call sites don't)
**Where:** allowlist `section1/src/harness_identity/audit.py:7–24`; route bodies `api_keys.py:68–98` (`mint_key`), `api_keys.py:132–145+` (`revoke_key`), `invites.py:77` (invite mint), `invites.py:185/206` (revoke), `invites.py:211` (redeem), `billing.py:225–249` (`PUT /billing/plan`). The only production `write_audit` invocations are Section 8 review events (`section8/src/harness_review/service.py:223,260,291`, wired at `deploy/runners/services.py:362`). A repo-wide grep confirms `apikey.added` / `apikey.removed` (defined at `audit.py:20–21`) are referenced **nowhere** outside tests.
So: minting a provider API key, revoking one, minting/redeeming org invites, and changing a team's billing plan all mutate security-relevant state with **no audit trail**, even though the action vocabulary anticipates them.
Secondary: `audit_log` has no `team_id` column (`0001_initial.sql:89–100`) and its only index is `idx_audit_log_ts` on `(ts)` (`0002_indexes.sql:4`) — filtering by action/target_type is a seq scan, and per-team forensics require a join through `team_members.actor_member_id`.
**Fix:**
```python
# in mint_key(), after db.commit():
await write_audit(db, "apikey.added", actor_member_id=principal.memberId,
                  target_type="api_key", target_id=row.id, detail={"provider": body.provider})
# analogous: "apikey.removed" in revoke_key; add "invite.minted"/"invite.redeemed"/
# "invite.revoked" and "billing.plan_changed" to APPEND_ONLY_ACTIONS + call sites.
```
```sql
CREATE INDEX idx_audit_log_action_ts ON audit_log (action, ts DESC);
CREATE INDEX idx_audit_log_target ON audit_log (target_type, target_id);
ALTER TABLE audit_log ADD COLUMN team_id VARCHAR(36) REFERENCES teams(id); -- backfill via join
```

### 3. MEDIUM — `rate_buckets` never shrinks: one row per IP+route, forever
**Where:** `section1/migrations/0011_rate_buckets.sql:7–11`; writers `section1/src/harness_identity/ratelimit.py:28–32, 63–67`. Repo-wide deletion grep finds sweeps only for `ws_tickets` (`ephemeral.py:44`) and `oauth_states` (`state_store.py:32`) — none for `rate_buckets`. The window-reset upsert updates counters but never removes expired buckets.
**At scale:** every unique client IP behind the auth edge mints a permanent row; a scanner sweeping /24s creates millions of dead rows that slow bucket lookups (PK probe stays O(1), but the table bloats backups, dumps, and replication traffic indefinitely).
**Fix:** scheduled sweep (pg_cron or the existing notifier loop):
```sql
DELETE FROM rate_buckets WHERE window_start < now() - INTERVAL '1 hour';
```

### 4. HIGH — `cost_ledger` metering: one writer, silent swallow, easy bypass
**Where:** sole producer `section3/src/harness_router/ledger.py:37` (INSERT) invoked via `record_usage` (`ledger.py:110–148`) from the router path only (`section3/src/harness_router/api.py:77`); consumer/enforcer `section4/src/harness_orchestrator/budget.py:28–35` (`SUM(cost_ledger)` per team) and `section1/src/harness_identity/billing.py:147+` (monthly quota).
Two compounding defects: (a) `record_usage` converts **every** failure into `logger.warning(...); return None` (`ledger.py:146–147`) — lost metering is invisible, so `check_budget` under-reads spend and caps stop enforcing precisely when the ledger is unhealthy; (b) any invocation path that skips the section-3 router (direct dsh/Ollama runs, future providers) writes nothing, so budgets see partial spend by design.
**At scale:** a transient asyncpg outage during a burst of LLM calls erases thousands of dollars of usage records while teams keep running under "under-budget" verdicts.
**Fix:** enqueue-to-outbox on failure (reuse the 0009 durable-outbox pattern) instead of dropping; add a reconciliation job comparing provider-reported usage vs ledger per run and alert on drift > ε; assert in the orchestrator that a run's model calls went through the metering path before honoring `check_budget` results.

### 5. MEDIUM — `meeting_messages`: no `meeting_id` index, unbounded per meeting
**Where:** `section1/migrations/0001_initial.sql:130–140`. Only the `id` PK exists; the `meeting_id` FK (line 138) and `sender_member_id` (139) are unindexed. `meetings.status` (106) is also CHECK-less (see Finding 9).
**At scale:** loading a meeting's transcript scans the whole table once transcripts dominate; long-running projects accumulate thousands of messages per meeting with no cap, and deletes of meetings (Finding 6) scan linearly.
**Fix:**
```sql
CREATE INDEX ix_meeting_messages_meeting_created ON meeting_messages (meeting_id, created_at);
CREATE INDEX ix_meeting_messages_sender ON meeting_messages (sender_member_id);
```
plus an application-level per-meeting message cap / transcript compaction policy.

### 6. MEDIUM — FK graph defaults to NO ACTION: no deletion story for teams/projects/users
**Where:** all fourteen FKs in `0001_initial.sql` — e.g. `api_keys.team_id→teams` (:30), `auth_sessions` (:39–40), `projects.team_id→teams` (:73), `team_members` (:85–86), `meetings.project_id` (:109), `agent_runs.project_id` (:151), `agent_events.project_id` (:163), `cost_ledger.run_id` (:195), `pull_requests` (:209–211), `review_comments` (:223–224). The single exception in the whole schema is `notification_outbox.team_id … ON DELETE SET NULL` (`0009_notification_outbox.sql:9`). Note `run_claims` deliberately has **no FK at all** (`0006_run_claims.sql:3–4`).
**At scale/consequence:** `DELETE FROM teams WHERE id=…` raises FK violations until ~a dozen child tables are manually purged in the right order; conversely, ad-hoc SQL cleanup (or a future "delete my account" feature) that removes users/teams without touching children strands orphaned `projects`, `api_keys`, `agent_runs` rows that ownership checks can no longer reach but backups/GDPR exports still surface.
**Fix:** pick a policy explicitly and encode it in migration 0013, e.g. team deletion cascades through owned resources but preserves financial/audit history:
```sql
ALTER TABLE projects        DROP CONSTRAINT projects_team_id_fkey,
    ADD CONSTRAINT projects_team_id_fkey FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE;
ALTER TABLE api_keys        ... ON DELETE CASCADE;   -- team-owned secrets die with team
ALTER TABLE cost_ledger     ... ON DELETE SET NULL;  -- keep spend history (already nullable run_id? no: keep RESTRICT)
ALTER TABLE audit_log       ... ON DELETE SET NULL;  -- actor_member_id nullable already
```
…and document that `teams` deletion is a supported operation with a tested order, or forbid it at the API layer until then.

### 7. MEDIUM — `float` ORM typing over `NUMERIC(12,6)`: silent precision drift
**Where:** `section1/src/harness_identity/models.py:231` (`CostLedger.cost_usd: Mapped[float] = mapped_column(Numeric(12,6))`) and `models.py:156` (`AgentPlan.estimated_cost_usd`, same pattern). Producers already juggle both worlds: `section3/src/harness_router/ledger.py:105` rounds to 6 dp then `:142` converts back to `float`.
**Consequence:** asyncpg returns `Decimal`; SQLAlchemy coerces to binary float. Summing thousands of sub-cent costs (exactly what `budget.py:29` and `billing.py` do) accumulates representation error against the exact decimal stored in PG; boundary decisions ("is the team $0.01 under cap?") become nondeterministic, and test assertions like `pytest.approx` paper over the drift.
**Fix:**
```python
from decimal import Decimal
cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
```
…or sidestep floats entirely: store integer micro-dollars (`BIGINT`) and format at the edge.

### 8. HIGH — Two timestamp conventions coexist: TIMESTAMPTZ vs TEXT-ISO
**Where:** TEXT-ISO: `ws_tickets.expires_at/created_at` (`0005_ephemeral_state.sql:12–13, 20–21`), `run_claims.claimed_at/heartbeat_at` (`0006_run_claims.sql:9–10`). TIMESTAMPTZ: every 0001 table plus `login_otps` (0004:7), `team_invites` (0008:14), `notification_outbox` (0009:16–17), `rate_buckets.window_start` (0011:9). The rationale is documented in-code (0005:2–5: shared PG/SQLite code path) but the hazard is real: chronological correctness depends on every writer emitting byte-identical normalized UTC ISO strings; one writer using `"Z"` vs `"+00:00"` or truncated microseconds makes `<` comparisons wrong **silently**, with indexes (`0005:16,23`; `0006:13–14`) happily accelerating the wrong answer.
**At scale:** expiry sweeps (`ephemeral.py:44`, `state_store.py:32`) leak or prematurely kill sessions after any producer change; cross-convention joins/compares (e.g., correlating `ws_tickets.created_at` TEXT with `auth_sessions` TIMESTAMPTZ in forensics) require parsing on both sides.
**Fix:** converge in migration 0013 (SQLite tests can keep their adapter):
```sql
ALTER TABLE ws_tickets   ALTER COLUMN expires_at TYPE timestamptz USING expires_at::timestamptz;
ALTER TABLE ws_tickets   ALTER COLUMN created_at TYPE timestamptz USING created_at::timestamptz;
ALTER TABLE oauth_states ALTER COLUMN expires_at TYPE timestamptz USING expires_at::timestamptz;
ALTER TABLE oauth_states ALTER COLUMN created_at TYPE timestamptz USING created_at::timestamptz;
-- run_claims columns likewise (see Finding 13)
```

### 9. LOW — CHECK constraints applied inconsistently
**Where:** present: `team_invites.role` (0008:18), `notification_outbox.status` (0009:13), `billing_plans.plan` (0010:14). Absent despite documented vocabularies: `run_claims.state` ('queued'|'running'|'orphaned', documented only as a comment at 0006:8), `meetings.status` (0001:106), `engineering_briefs.status` (0001:121), `agent_runs.state` (0001:147), `agent_plans.status` (0001:178), `pull_requests.status` (0001:205), `meeting_messages.kind` (0001:134).
**Consequence:** typos and drifted enum values land in the DB and surface as logic bugs far away, instead of failing at write time.
**Fix:**
```sql
ALTER TABLE run_claims ADD CONSTRAINT ck_run_claims_state CHECK (state IN ('queued','running','orphaned'));
ALTER TABLE agent_runs ADD CONSTRAINT ck_agent_runs_state CHECK (state IN (<contract states>));
ALTER TABLE meetings   ADD CONSTRAINT ck_meetings_status CHECK (status IN (<contract statuses>));
-- repeat per column using harness_contracts enumerations as the source of truth
```

### 10. LOW — `notification_outbox.payload` JSONB: no JSON operators anywhere (GIN not needed)
**Where:** column at `0009_notification_outbox.sql:12`; consumers treat rows opaquely (drain/mark-sent in `notifier.py`). Repo-wide grep for `payload->>`, `jsonb_*` returns nothing.
**Verdict:** the lead's question resolves to "no": adding a GIN index would be pure write amplification today. Revisit only if payload-shape queries appear (e.g., "all notifications for run X"):
```sql
-- only IF such queries ship:
CREATE INDEX idx_notification_outbox_payload ON notification_outbox USING GIN (payload jsonb_path_ops);
```

### 11. HIGH — Connection-pool math: 7 services × default engine vs `max_connections`
**Where:** `deploy/runners/services.py:51–55`: `get_factory()` = bare `create_async_engine(DATABASE_URL)` — no pool arguments — wrapped in `async_sessionmaker`. This factory pattern backs every deployed backend (identity/github/models/orchestrator/sandbox/meeting/review per `docker-compose.yml:233–246` dependency list); standalone entrypoints do the same (`section1/scripts/bootstrap_db.py:13`).
SQLAlchemy defaults are `pool_size=5, max_overflow=10` ⇒ **15 connections/engine**. Seven services fully loaded = 105 concurrent connection attempts against PostgreSQL's stock `max_connections=100` — before counting the EventBus LISTEN socket, notifier workers, migration runner, and the walg sidecar. Under a burst, later services fail to acquire connections with `too many clients already`, which (per Finding 4) silently kills metering too.
**Fix:** size pools deliberately per service and share one engine per process:
```python
engine = create_async_engine(
    DATABASE_URL,
    pool_size=4, max_overflow=6,      # 10/service ⇒ ≤70 across 7 services
    pool_recycle=1800, pool_pre_ping=True,
)
```
…and raise the server side in compose (`command: postgres -c max_connections=200`) or front it with PgBouncer (transaction mode) once service count grows.

### 12. LOW — No vacuum/analyze/maintenance story beyond autovacuum defaults
**Where:** repo-wide grep finds no VACUUM/ANALYZE/autovacuum configuration anywhere (migrations, compose, scripts). Three append-heavy tables (`agent_events`, `audit_log`, `cost_ledger`) plus the per-row NOTIFY trigger (Finding 1) generate steady dead-tuple and WAL pressure; `deploy/backup/backup.sh` covers snapshots but nothing addresses bloat, wraparound monitoring, or statistics freshness after bulk loads (e.g., seed_demo).
**At scale:** default autovacuum (20% threshold) lets million-row tables bloat for hours between passes; xid age creeps unnoticed.
**Fix:** document + configure: `ALTER TABLE agent_events SET (autovacuum_vacuum_scale_factor = 0.02);` for hot tables, a weekly `VACUUM (ANALYZE)` maintenance job alongside `backup.sh`, and a monitoring check on `pg_stat_user_tables.n_dead_tup` / `age(relfrozenxid)`.

### 13. HIGH — `run_claims`: crash-recovery heartbeats compared as strings
**Where:** `section4/src/harness_orchestrator/run_queue.py:129` — `WHERE state='running' AND heartbeat_at < :cutoff` — with writers at `:43–44, :56–57, :101` binding `:now` as pre-formatted ISO text (schema `0006_run_claims.sql:9–10`; ordering also string-based at `:68, :143`).
This is Finding 8's sharpest edge: orphan detection decides whether a crashed worker's run gets requeued. If any writer formats `heartbeat_at` differently (offset form, missing microseconds — trivially caused by swapping `datetime.now(UTC).isoformat()` for a utility returning `"…Z"`), the lexicographic comparison flips: live claims look expired (double execution of a run) or dead claims look alive (queue stalls forever). Nothing in the schema or the code normalizes the format; correctness rests on convention.
**At scale:** with multiple worker processes/hosts and clock skew, stale-claim reaping is the mechanism that keeps the queue moving — a formatting divergence turns it into either duplicate PRs (expensive, user-visible) or a silently wedged queue.
**Fix:** migrate the two columns to `timestamptz` (same DDL as Finding 8) and bind `datetime` objects — the codebase already follows this discipline elsewhere (`invites.py:13` notes "asyncpg rejects strings" for TIMESTAMPTZ binds). Interim hardening: centralize a single `utc_iso()` helper and assert format in tests.

### 14. HIGH — PITR path is dead-on-arrival as deployed: `archive_command` never configured
**Where:** `deploy/docker-compose.yml:259–260` states WAL archiving is "done by archive_command inside the db container **once the db image carries the wal-g binary** (build deploy/walg/Dockerfile.walg; documented as a [follow-up])" — i.e., the shipped `db` image has no wal-g and `archive_mode` off. The restore tooling itself is complete (`deploy/walg/restore-walg.sh:2–32`, `backup-walg.sh`, profile wiring at compose `:262–266`), and logical snapshots exist (`deploy/backup/backup.sh` — custom-format dumps, 7-deep retention at `:31`, verified restore via `verify_restore.sh`).
**Consequence:** the advertised recovery point objective is whatever the last cron'd `backup.sh` produced (default: nightly if anyone installed the cron). Everything between snapshot and incident — including the `agent_events`/`cost_ledger` firehose — is unrecoverable; `restore-walg.sh --target-time` cannot work because there are no archived WAL segments to replay.
**At scale:** an accidental `DROP TABLE` or bad migration at 17:59 loses up to ~24h of writes.
**Fix (pick one, document the other as unsupported):**
```sql
-- build Dockerfile.walg INTO the db image, then:
ALTER SYSTEM SET archive_mode = on;
ALTER SYSTEM SET archive_command =
  'wal-g wal-push %p && test $(pg_last_wal_replay_lsn...) ';  -- standard wal-g stanza
ALTER SYSTEM SET archive_timeout = '60 s';
```
…or accept the RPO honestly: run `backup.sh` hourly via cron/compose service and remove the PITR claim from STATUS.md until the image ships.

---

## Cross-cutting observations
- **The frozen-schema rule works against hygiene here:** 0001 is untouchable by policy (AGENTS.md), so Fixes land as 0013+ ALTERs — all SQL above is written migration-compatible (additive, no edits to 0001).
- **Silent degradation is a systemic motif:** metering swallows errors (F4), string timestamps fail semantically rather than loudly (F8/F13), pool exhaustion manifests as distant 500s (F11). A shared "data-plane health" probe (ledger write canary, claim-reap lag, pool saturation) would catch most of these before users do.
- **Priority order if fixing top-down:** #2 (audit gaps — compliance exposure today, cheap fix), #13/#8 (timestamp convergence — correctness), #4 (metering durability — revenue), #11 (pool sizing — one config change), #14 (decide the backup story), then the rest.
