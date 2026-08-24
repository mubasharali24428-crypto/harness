# MISSION 13 — WAVE 3 (hardening & performance) — CAMPAIGN GRAPH

> ATLAS-authored. Baseline: Wave-2 close HEAD (CI 32685579474 SUCCESS).
> Consumes part4 §1 WAVE-3 sketch + W2 leftovers.
>
> TOPOLOGY: same as Wave 2 — 5 squads × 10 = 50 builders, 5 verifier teams
> staggered per vertical. Platform runs them as sequential batches ≤10.

## W2 CARRYOVERS into W3 scope

1. Docker DNS crash-loop on harness-backend-1 (VT-5 flag) → G2 lane owns.
2. `invokeAi` dead export prune in section7/src/lib/api.ts + its test (VT-2 MINOR) → G5 lane.
3. S6 datetime DeprecationWarnings cleanup → G3 lane.
4. STATUS/docs gate-number refresh after this wave's suites change counts → G4 lane.
5. Migration numbering law: next free is **0020** (0001–0019 used; verify ls before naming).

## WAVE 3 LANES

| Lane | Mission | Owned paths | Squad |
|---|---|---|---|
| H1a | Pool math: pool_size=8 max_overflow=4 ×7 services documented + enforced via shared engine factory; PG max_connections aligned | deploy/runners/db.py or services.py engine-factory region · docs/pool-math.md · tests | ALPHA |
| H1b | Heartbeat comparison fix + run_claims SKIP LOCKED + zombie-claim reaper (heartbeat loss cancels engines) | section4/src/harness_orchestrator/{run_queue.py,service.py heartbeat paths} · tests | ALPHA |
| H2 | WAL-G PITR end-to-end: archive_command configured, credentialed restore drill runbook with timed RTO/RPO, compose profile validated | deploy/docker-compose.yml walg profile · deploy/runners/backup.sh · docs/runbooks/backup-and-pitr.md | BETA |
| H3a | E2E hero-journey suite (Playwright) against compose stack: login→project→meeting→brief→plan→approve→run→review | section7/e2e/ (NEW) · playwright.config.ts · CI recipe | ALPHA |
| H3b | CI/local gate convergence: one make target runs exactly what CI runs; revive version-pin conformance test; flake quarantine list | Makefile (NEW) · .github/workflows/ci.yml edits · section1/tests conformance revival | BETA |
| H4a | NUMERIC money migration: cost columns → NUMERIC(12,6), ORM Decimal sweep, metering tests updated (migration 0020) | section1/migrations/0020_money_numeric.sql · section{1,3}/models+ledger Decimal typing · tests | ALPHA |
| H4b | TIMESTAMPTZ normalization: audit every timestamptz write path for naive datetimes; asyncpg-safe helpers shared; fix str-TIMESTAMPTZ footguns | shared time helper module NEW in harness_identity (timeutil.py) · call sites in S4/S6 · tests | BETA |
| G5a | API hardening: oracle-consistent 403/404 sweep across sections; OpenAPI response-model typing islands for the top 20 routes | per-section route files (registry-pinned list) · tests | ALPHA |
| G5b | invokeAi dead-export prune + frontend lint debt batch (raw-14 top items) | section7/src/lib/api.ts · raw-14-pinned files | BETA |

## PAIR CONTRACTS

H-P1 (H1a↔H4a): both touch migrations — H1a takes none (config only);
H4a owns 0020 exclusively. Pool-math doc references NUMERIC change.
H-P2 (H3a↔H3b): E2E suite MUST be runnable by the same make target CI uses;
H3b defines the target, H3a consumes it (`make e2e`).

## VERIFIER TEAMS

W3-VT1 → DB INFRA (H1a+H1b): live-PG pool exhaustion probe, SKIP LOCKED race,
zombie reaper walk. W3-VT2 → BACKUP/PITR (H2): actual restore into scratch DB,
timed RTO/RPO evidence. W3-VT3 → QA INFRA (H3a+H3b): make e2e from clean
checkout, CI recipe diff parity. W3-VT4 → MONEY/TIME (H4a+H4b): Decimal
round-trip, no float comparisons survive grep, naive-datetime scan clean.
W3-VT5 → API HARDENING (G5a+G5b): OpenAPI schema spot-checks, oracle probe.

## MERGE LAW

Same as W2: ATLAS commits per gated lane group, single mission push at close,
verifiers staggered as verticals gate. Sole-owner: compose walg profile = H2;
ci.yml = H3b; migration slots pre-assigned.
