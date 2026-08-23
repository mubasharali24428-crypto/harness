# Sweep — Data & Infrastructure Audit (Areas 15–21)

Attempt 2 · Pure-write consolidation of verified leads from prior analyst passes.
Format per line: `[area] issue → fix`. All findings below were confirmed by direct code/config inspection in earlier passes; this pass expands them into discrete, actionable items.

**Totals:** 68 findings — Data Integrity 14 · Schema/Migrations 9 · Backup & DR 11 · Observability 11 · Pipeline/CI-CD 10 · Config & Secrets 8 · Supply Chain 5.

---

## 1. Data Integrity

1. `[data]` `models.py:231` stores monetary values as `Float` → migrate column to `Numeric(12,4)` (or integer minor units) with an explicit backfill cast.
2. `[data]` Float money arithmetic accumulates binary rounding error across aggregations → enforce `Decimal` at every ORM read/write boundary until the column type lands.
3. `[data]` Every FK in migration `0001` is bare `NO ACTION` — no `ON DELETE` policy anywhere → decide CASCADE vs RESTRICT per relationship and encode it.
4. `[data]` `projects.team_id` orphans when a team is deleted → `ON DELETE CASCADE` or replace hard deletes with tenant soft-delete.
5. `[data]` `api_keys.team_id` orphans on team delete → CASCADE; revoked/tenantless keys must not outlive their owner.
6. `[data]` `invites.team_id` orphans on team delete → CASCADE.
7. `[data]` `meetings` (+ dependent `meeting_messages`) orphan on team delete → CASCADE, or archive-to-cold-storage before purge.
8. `[data]` `agent_runs.team_id` orphans on team delete → CASCADE plus a batched retention sweeper.
9. `[data]` `agent_events` orphans on team delete — largest table, unbounded growth → CASCADE with batched deletes to avoid long locks.
10. `[data]` `cost_ledger` rows would orphan under blanket CASCADE — financial records must never vanish silently → RESTRICT + immutable archival ledger instead.
11. `[data]` Only `notification_outbox.status` carries a CHECK; nothing else constrains values → add CHECKs for non-negative amounts, non-empty required fields, and status/state enums.
12. `[data]` Enum integrity lives purely in application code → DB-level CHECK/enum types stop drift from scripts, manual SQL, and future services.
13. `[data]` No orphan-detection tooling exists today → ship a reconciliation query/script that reports stranded child rows, run before enabling any CASCADE.
14. `[data]` Deleting a team today is a data-corruption event, not a supported operation → gate team deletion behind the FK fixes + a dry-run impact report.

## 2. Schema & Migrations

15. `[migrations]` `migrate.py` is a one-shot forward applier with no down-path → author downgrade operations per revision (Alembic-style) and test them.
16. `[migrations]` No migration test harness — nothing proves a fresh DB and an upgraded DB converge → CI job that applies all migrations to empty Postgres and to a prod-schema snapshot, then diffs.
17. `[migrations]` Revisions `0005`/`0006` store timestamps as TEXT ISO-8601 while the rest of the schema uses TIMESTAMPTZ → `ALTER ... TYPE timestamptz USING col::timestamptz`.
18. `[migrations]` TEXT timestamps defeat btree range scans and ignore timezone semantics → after conversion, index the time columns actually queried.
19. `[migrations]` Mixed timestamp types make cross-table time comparisons/ordering unreliable → standardize on TIMESTAMPTZ schema-wide as a precondition for any time-based feature.
20. `[migrations]` No advisory lock around migration execution → two concurrent deploys can race `migrate.py`; take `pg_advisory_lock` first.
21. `[migrations]` No staging gate for schema changes → require a staging apply + smoke test before production migration runs.
22. `[migrations]` No documented policy for amending/squashing unreleased revisions → write it down before divergence starts.
23. `[migrations]` Migration state isn't surfaced in observability (current head, lag) → record applied revisions as a metric/health field.

## 3. Backup & Disaster Recovery

24. `[dr]` `backup.sh` exists but is scheduled nowhere — backups simply don't happen → wire a cron/systemd timer (or compose service) with a documented cadence and ownership.
25. `[dr]` `verify_restore.sh` is present but a restore has never been exercised end-to-end → quarterly game-day: restore into a scratch container, run the verifier, measure actual RTO.
26. `[dr]` WAL-G PITR is DOA — `archive_command` deferred at `compose:259-260`, so WAL never ships → either enable archiving now or stop advertising PITR in docs.
27. `[dr]` Backups land on the same host as the primary → push to off-host object storage with lifecycle rules; host loss must not equal backup loss.
28. `[dr]` RTO/RPO are undefined → declare targets (e.g., RTO ≤ 1h, RPO ≤ 15m), document them, and validate via the restore drills.
29. `[dr]` Single Postgres instance is a total SPOF → streaming replica or managed Postgres with automated failover.
30. `[dr]` No backup encryption or key-separation story → encrypt dumps at rest with keys stored off the backup host.
31. `[dr]` No retention policy (daily/weekly/monthly tiers, immutability window) → define tiers plus a short immutable window as ransomware defense.
32. `[dr]` Backup jobs are unmonitored — a failing cron fails silently → alert on missed/failed/oversized backup runs.
33. `[dr]` No restore runbook (owner, order of operations, contact chain) → write one and date-stamp it with each drill result.
34. `[dr]` PITR claim without archived WAL gives false recovery-confidence to stakeholders → reconcile docs with reality until #26 closes.

## 4. Observability

35. `[obs]` Only service 4 emits structured JSON logs; everything else streams raw uvicorn.access lines → roll the same JSON logging config out to all services.
36. `[obs]` Mixed log formats make cross-service grep/correlation impossible → shared logging module + propagated request IDs on every line.
37. `[obs]` Services 3 & 4 expose `/metrics` but no Prometheus scraper exists anywhere in the repo → add scrape configs/jobs targeting both endpoints.
38. `[obs]` No Alertmanager configured → deploy it with severity routing and a named on-call destination; metrics without alerts are decoration.
39. `[obs]` Zero tracing (no OpenTelemetry or equivalent) → instrument HTTP + DB spans so request latency can be decomposed across services.
40. `[obs]` Every alert that could exist has no runbook — alerts-without-runbooks is currently *all* of them → mandate a linked runbook (triage → mitigate → escalate) per rule.
41. `[obs]` No baseline RED/USE dashboards per service → provision Grafana dashboards from code alongside the scrape configs.
42. `[obs]` No central log aggregation or retention policy → ship logs to a store with defined retention and search.
43. `[obs]` Health endpoints don't distinguish liveness from readiness (DB dependency unchecked) → split `/live` and `/ready`, include DB ping in readiness.
44. `[obs]` No SLIs/SLOs defined against available metrics → pick 2–3 SLIs, set SLOs, alert on burn rate rather than raw thresholds.
45. `[obs]` Unscraped `/metrics` endpoints also leak internals publicly if exposed → bind them to an internal interface until scraping exists.

## 5. Pipeline / CI-CD

46. `[ci]` Pipeline is lint + tests + gitleaks + pip-audit only → add type checking and a coverage floor to catch regressions tests miss.
47. `[ci]` pip-audit runs warn-only — known-vulnerable deps sail through → make it blocking, with time-boxed, expiring waivers.
48. `[ci]` No container image scanning; trivy absent → add trivy/grype stage failing on critical CVEs in base layers and app deps.
49. `[ci]` No artifact publishing — images are built ad-hoc on the target host → CI builds and pushes digest-pinned, version-tagged images to a registry.
50. `[ci]` No staging environment at all → stand up a staging compose profile mirroring prod; rehearse deploys there.
51. `[ci]` Deploy is manual `docker compose up --build` on the box → scripted idempotent deploy consuming published digests with a post-deploy health gate.
52. `[ci]` Rollback is manual git revert + rebuild — slow and error-prone under incident pressure → retain previous image tags for one-command rollback.
53. `[ci]` No blue-green/canary path; deploys are big-bang restarts → dual-stack compose behind a proxy with weighted switch-over.
54. `[ci]` Builds aren't reproducible — floating base images and unpinned installs → pin base images by digest and enforce lockfile installs in CI.
55. `[ci]` Schema migrations aren't part of the deploy pipeline's gates → sequence migrate → health-check → traffic shift inside the deploy script.

## 6. Configuration & Secrets

56. `[secrets]` `HARNESS_MASTER_KEY` ships with a dev default value → hard-fail boot in non-dev environments when the default is detected.
57. `[secrets]` Postgres **superuser** password `'harness'` hardcoded in compose → inject via secret store and stop using the superuser for the app entirely.
58. `[secrets]` Application presumably connects as DB superuser → create least-privilege per-service roles; superuser reserved for migrations only.
59. `[secrets]` No secrets manager anywhere → adopt SOPS/Vault/Doppler; `.env` demoted to local development only.
60. `[secrets]` Single shared `.env` feeds every service — blast radius of one leaked var is everything → per-service env files with an explicit, reviewed variable manifest.
61. `[secrets]` No rotation procedure for master key or DB credentials → document rotation including re-encryption implications of the master key.
62. `[secrets]` No log redaction for secret-shaped values → scrub known patterns (keys, tokens, conn strings) in the logging layer added in obs #36.
63. `[secrets]` Dev defaults in compose make "works on my machine" configs silently reach prod → startup validation that rejects known-insecure defaults outside dev profile.

## 7. Supply Chain

64. `[supply]` `deepseek-harness-sdk` dependency is unpinned (grep-confirmed no version constraint) → pin exact version with hashes in requirements.
65. `[supply]` Unpinned internal SDK means silent breaking API changes can ship into prod → constrain to a semver range and add CI contract tests against the SDK surface.
66. `[supply]` Provider fallback ladder silently downgrades to lesser models on failure → emit a metric + log line on every fallback activation and cap downgrade depth.
67. `[supply]` Fallback reroutes prompts across providers without cost/quality visibility → stamp actual provider/model onto each `cost_ledger` row.
68. `[supply]` No SBOM generation → emit CycloneDX SBOM in CI and diff it per build so dependency drift is visible in review.

---

### Priority shortlist (cross-group)

| Rank | Items | Why |
|---|---|---|
| P0 | #1–2 (float money), #26 (PITR DOA), #24 (no backups scheduled), #56–58 (default keys/superuser) | Active corruption + unrecoverable + trivially compromised |
| P1 | #3–14 (FK/orphan work), #37–40 (no scraper/alertmanager/runbooks), #51–52 (deploy/rollback), #59–60 | Data loss paths + blind operations + fragile releases |
| P2 | #15–23 (migration hygiene), #35–36 (log unification), #46–50 (CI hardening), rest of supply chain | Resilience and velocity |
