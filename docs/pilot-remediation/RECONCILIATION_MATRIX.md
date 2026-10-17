# Reconciliation Matrix — every cited audit item, deduplicated

Created: 2026-10-17 (session 3). Sources reconciled:

| Ref | Source | IDs |
|-----|--------|-----|
| SRC-R | `RISK_REGISTER.md` | R-01 … R-50 |
| SRC-S1 | `session1/S1-R0x-*.md` | 5 risks (sub-findings stay inside R-01…R-05 rows) |
| SRC-S2 | `session2/README.md` | 6 defect species → `S2-D1 … S2-D6` |
| SRC-G | `docs/GAP_ANALYSIS_AND_FIXES.md` | F1-F9, H1-H9, M1-M6, D1-D6 |
| SRC-M17 | `docs/briefs/m17-findings-*.md` | 80 IDs (see per-seat tables) |
| SRC-NEW | this session's late findings | L1-L6 + `M23-A`, `M23-B` |

Status vocabulary: **FIXED-V** = fixed, re-verified this session with a command
or direct file read; **FIXED** = fix present in tree, evidence in the cited
doc/test (not re-run now); **FIXED-PC** = closed per `program-close.md`;
**PARTIAL** = partially addressed, residual stated; **ACCEPTED** = explicit
pilot decision / residual; **OPEN** = verified missing, still needs work;
**AS-REG** = state stands as registered in the source; not re-verified now.
Every original ID is preserved. Where two sources describe one root cause the
entry lists both IDs in one row.

---

## A. Register R-01 … R-50 (SRC-R, SRC-S1)

R-01 … R-05: MITIGATED & VERIFIED in session 1; session-2 adversarial review
confirmed the at-drafting HEAD gaps, all since fixed at HEAD — evidence:
`session1/S1-R0x-*.md` §Verification + register rows. Key re-checks
2026-10-17: S3 bearer dep installed (`deploy/runners/services.py:263-267`),
S4 authz dep (`services.py:420` F2 comment), plan_gate team-scoped, invite
bind lowercased-email compare.

| ID | Dedup note | Status | Evidence pointer |
|----|-----------|--------|------------------|
| R-01 | incl. S2 README adversarial items (R-01a/b/d at drafting HEAD) | FIXED-V (was MITIGATED & VERIFIED) | `session1/S1-R01-*.md`; services.py:263-267,420 |
| R-02 | incl. M17-PBETA C-1 (isolation) + L6 (guard revision) | FIXED-V + revision doc | `S1-R02-*.md` §R-02a revision (contracts KDF, guard green 163 files/0 edges) |
| R-03 | incl. GAP F6r | FIXED-V (R-03c residual accepted) | `S1-R03-*.md`; sandbox env explicit |
| R-04 | incl. GAP R-04c/d/e deliverables | FIXED-V; R-04f residual (inherent) | `S1-R04-*.md` |
| R-05 | incl. R-30 (audit read API) | FIXED-V; R-05b residual (ORM-only append guard) | `S1-R05-*.md`; `GET /api/v1/audit` admin-bounded |
| R-06 | — | ACCEPTED (ports open for pilot; TLS opt-in) | as-registered `VERIFIED-SPOT` |
| R-07 | overlaps MALPHA F-3 (caps posture) partly | PARTIAL (boot gate tested S10; per-section dev defaults standalone-only) | `S10/tests/unit/test_deploy_boot_gate.py` |
| R-08 | overlaps PBC1 F1 (stateless work) | FIXED-PC (CAS approvals + exactly-once; publish chains) | `program-close.md`; m19-n1 notes |
| R-09 | — | AS-REG | — |
| R-10 | overlaps PBC1 F12 | MITIGATED-V (heartbeat env override, default 90s) | `run_queue.py:29-38` |
| R-11 | budget warning is M23-B scope; cost-honesty review-path test unverified | PARTIAL → see M23-B | budget.py; new tests |
| R-12 | — | AS-REG | — |
| R-13 | — | ACCEPTED (scrape target, live-probed unauthenticated 2026-10-17) | — |
| R-14 | overlaps SGAMMA OPS-13 | PARTIAL (prune windows M19-N3; operator table → R-34) | `compose:678` |
| R-15 | overlaps OPS-12 | FIXED (PITR rehearsed) | `m18-c6-rehearsal-evidence.md` |
| R-16 | SSRF lead | LEAD (unverified; client pinned api.github.com) | as-registered |
| R-17 | — | AS-REG | — |
| R-18 | — | AS-REG | — |
| R-19 | — | AS-REG | — |
| R-20 | overlaps GAP R-04d (fixed: PROPOSE_GOAL_MAX) | PARTIAL (goal bounded; cap census → PBETA M-4) | `limits.py:38` |
| R-21 | fail-open limiter | ACCEPTED (documented tradeoff) | as-registered |
| R-22 | — | AS-REG | — |
| R-23 | — | AS-REG | — |
| R-24 | — | AS-REG | — |
| R-25 | dev literals in loadtest profile | ACCEPTED (isolated profile) | as-registered |
| R-26 | overlaps PBETA M-3 | AS-REG | — |
| R-27 | dedupe by seq | ACCEPTED | — |
| R-28 | incl. R-02f | FIXED-V (`POST /github/disconnect`) | services/S2 tree; S1-R02 note |
| R-29 | — | AS-REG | — |
| R-30 | = R-05e | FIXED-V | `S1-R05-*.md`; audit API route |
| R-31 | overlaps ODELTA K2 | AS-REG (counsel review pending) | `PRIVACY.md` |
| R-32 | overlaps ODELTA K2 | AS-REG (counsel + MIT decision) | `TERMS.md` |
| R-33 | — | AS-REG | — |
| R-34 | overlaps OPS-13 | AS-REG | — |
| R-35 | — | AS-REG | — |
| R-36 | — | FIXED (tutorial + hero journey live; E2E-in-CI shipped 2026-10-17 → OPS-20 closed) | `TUTORIAL.md`; `ci.yml` `e2e-compose` |
| R-37 | — | LEAD | — |
| R-38 | overlaps ODELTA K2 | FIXED-PC (Stripe adapter + signed idempotent webhooks) | `program-close.md` |
| R-39 | overlaps ODELTA K3 | FIXED-PC (MCP server) | `program-close.md`; mcp suites |
| R-40 | — | FIXED-V (heuristic verdicts; live-conflict FAIL reproduced) | test_budget seeds; reviewer.py |
| R-41 | overlaps ODELTA K1 + L2 | PARTIAL (pillar shipped; rules/context stored-not-injected residual) | `program-close.md` vs `extensions.md:126-152` |
| R-42 | overlaps ODELTA L2 | AS-REG | — |
| R-43 | — | AS-REG | — |
| R-44 | — | AS-REG | — |
| R-45 | overlaps ODELTA K3 + M22-M4 | FIXED-V (SSO edges rate-limited; PKCE tests) | `ratelimit.py` SSO_START/COMPLETE |
| R-46 | — | AS-REG | — |
| R-47 | frontend in-flight work | CLOSED — committed `923711f`, tree clean | git cat-file/HEAD check 2026-10-17 |
| R-48 | — | AS-REG | — |
| R-49 | — | AS-REG | — |
| R-50 | — | AS-REG | — |

## B. Session-2 defect species (SRC-S2, finalized in `session2/README.md`)

| ID | Defect | Status | Evidence |
|----|--------|--------|----------|
| S2-D1 | domain-metrics poller crash (sync ctx mgr on async session) | FIXED | `fe7c69c`; live: 0 poll failures, metric populated |
| S2-D2 | engine/service error text unredacted into events | FIXED | `afce55e` + 2 tests |
| S2-D3 | frontend image build break (`node:crypto` client) | FIXED (committed) | `923711f`; sha256.ts tracked at HEAD |
| S2-D4 | area 16 — no approval expiry enforcement | FIXED → M23-A | `test_plan_approval_ttl.py` (11 tests) |
| S2-D5 | area 9 — no budget warning machinery | FIXED → M23-B | `test_budget.py` (+10 tests) |
| S2-D6 | ops.md stale nightly-pg_dump/walg claims | FIXED | ops.md re-grep clean 2026-10-17 |

## C. GAP doc (SRC-G)

F1-F9 + H1-H9 live in "Part 2 — FATAL gaps (all fixed)" and "Part 3 — HIGH
gaps (fixed unless noted)"; H2r (login ownership), F6r (confinement), H8 (WS
ticket) closed in waves 2-3 (Part 10 ledger lines 944-946). Mediums: M1-M6 all
FIXED/MITIGATED (`GAP:496-505`). D1-D6 all FIXED/DONE (`GAP:960-966`).
`G-LIVE` (Part 5 runtime log): historical-first verification, superseded by
later sessions. No open item remains in SRC-G as of 2026-08-23; two residuals:
M3 (dual import paths → MITIGATED) and the H-part "unless noted" items
(re-checked where they overlap M17 rows below).

## D. M17 briefs (SRC-M17) — all 80 IDs

### MAC1 (DX; 11)
| ID | Status | Evidence |
|----|--------|----------|
| F-1 contributor path unbootable | FIXED-V | `Makefile:63` `bootstrap:`; `README.md:93-96` |
| F-2 envelope false on most of stack | FIXED | M17-B3 single middleware + `docs/envelope-install-points.md` (install matrix verified) |
| F-3 no SDK | FIXED-PC | Python+TS SDKs from one generator; `generate.py --check` clean |
| F-4 first run dry-mode dead end | FIXED-PC | canned zero-key demo delivered |
| F-5 learning docs/gallery | PARTIAL-V | `docs/TUTORIAL.md` + hero journey reproduced live; gallery not re-checked |
| F-6 extension surface | PARTIAL | `docs/extensions.md` shipped; see R-41 overstatement residual |
| F-7 stale schema command | AS-REG | — |
| F-8 wire shapes discoverability | AS-REG | api-versioning + typegen exist |
| F-9 glossary thin | FIXED-V | `docs/GLOSSARY.md` present |
| F-10 adoption blockers | PARTIAL | bootstrap + quickstart fixed; remainder AS-REG |
| F-11 GitHub OAuth wall | AS-REG | — |

### MAC2 (authz/WS/rate-limit; 9)
| ID | Status | Evidence |
|----|--------|----------|
| F1 run-guard fails open | FIXED-V | M18-C1 fail-closed sweep (`test_authz_failclosed.py`) |
| F2 plan_gate no team | FIXED-V | M18-C2; 27 `authorize_run|team` matches |
| F3 invite binds to token not identity | FIXED-V | invites.py:467 lowercased-email compare |
| F4 redeem/ws-ticket unthrottled | **FIXED** | `REDEEM_ROUTE` + `WS_TICKET_ROUTE` (limit 60) in `ratelimit.py`; 6 tests (`test_ratelimit_mac2f4.py`); S1 639/10 |
| F5 UA-equality fingerprint spoofable | FIXED-V | `X-WS-Fingerprint` REQUIRED; grace path deleted (app.py:448,716-720) |
| F6 ticket TTL nit | CLOSED (no defect) | as-registered |
| F7 ticket as URL query param | PARTIAL-ACCEPTED | header required; `?fingerprint=`/`?token=` remain for non-browser clients (app.py:60-61,688) |
| F8 IPv6 rotation divides budgets | FIXED-V | `normalize_client_ip` |
| F9 bucket table grows forever | FIXED-V | `BUCKET_GC_EVERY_WINDOWS`, `sweep_expired_buckets` |

### MALPHA (money/metering; 10)
| ID | Status | Evidence |
|----|--------|----------|
| F-1 reserve() fails on prod driver | FIXED | positional params + live-PG test (`test_money_numeric.py`) |
| F-2 reserve not atomic on PG | FIXED | advisory xact lock; 8-concurrent/5-winner live test |
| F-3 three caps disagree | AS-REG | caps posture overlaps R-07/R-11 residuals |
| F-4 NUMERIC overflow drops rows | FIXED | `CostOutOfRangeError` envelope both writers |
| F-5 keyset cursor NULL | AS-REG | — |
| F-6 meeting messageSeq volatile | AS-REG | — |
| F-7 WS replay overflow silent | AS-REG | — |
| F-8 done-signal double-fire 500 | AS-REG | CAS existed per register; not re-verified |
| F-9 run-cost attribution race | AS-REG | — |
| F-10 two clocks in orphan sweep | AS-REG | — |

### ODELTA (10x lens; 3 headliners — lenses L1-L5 fold into R-41/42/43/44)
| ID | Status | Evidence |
|----|--------|----------|
| K1 no codebase context layer | PARTIAL | pgvector/AST/@refs/RAG shipped (program-close); R-41 residual |
| K2 commercial skeleton missing | FIXED-PC | license/terms/pricing + Stripe + caps + OIDC (skeletons pending counsel) |
| K3 no MCP/SSO | FIXED-PC | MCP server; OIDC SSO (M22-M4) + rate limits verified |

### PBC1 (performance; 12)
| ID | Status | Evidence |
|----|--------|----------|
| F1 stateful in-process singleton | FIXED-PC | stateless orchestrator, CAS approvals, exactly-once (m19 notes) |
| F2 engine pool invisibly bounded | FIXED-V | `engine_runtime.py` used at service.py:945, worker.py:58 |
| F3 per-row pg_notify fan-out | FIXED-PC | slim bus |
| F4 no agent_events partitioning | FIXED | `0028_partitioned_retention.sql` + program-close |
| F5 WS unbounded conns/fan-out | AS-REG | not re-verified |
| F6 unindexed ledger aggregations | FIXED | M18-C3 daily per-team rollup (`compose:689`) |
| F7 no per-team quota on expensive paths | PARTIAL-ACCEPTED | per-team spend (fail-closed budget gate) + run-concurrency cap; per-IP G2 templates are the abuse guard |
| F8 outbox/audit unbounded retention | FIXED | M19-N3 prune windows (`compose:678`) |
| F9 payload duplication on bus | FIXED-PC | slim bus; done-signal diff shipping not re-verified |
| F10 cost model | N/A (analysis) | input to docs/benchmarks |
| F11 single-PG blast radius | ACCEPTED-ARCH | pilot scope |
| F12 zombie reaper vs long engines | MITIGATED-V | env override + documented default 90s |

### PBETA (contracts/test-integrity; 9)
| ID | Status | Evidence |
|----|--------|----------|
| C-1 isolation law violated | FIXED-V | contracts KDF; `make guard-isolation` OK (163 files, 0 edges) |
| C-2 envelope forked ×4, installed once | FIXED | M17-B3; `envelope-install-points.md` verified |
| H-1 TS mirror optionality wrong | FIXED-V | `generate.py --check` clean; defaults serialized |
| H-2 tautological asserts | FIXED-V | `or True` only in comments now |
| H-3 authz degrade-to-allow | FIXED-V | M18-C1 |
| M-1 orphaned helpers | AS-REG | census not re-run |
| M-2 fixtures not shared | AS-REG | — |
| M-3 S8 envelope bypass leaks | AS-REG | (envelope install covered by C-2; leak-scrub not re-verified) |
| M-4 blanket-except census (118/8 silent) | AS-REG | census not re-run |

### SGAMMA (ops; 26)
| ID | Status | Evidence |
|----|--------|----------|
| OPS-01 PITR only sidecar | FIXED | live db archives WAL on postgres:18 (S2-D6 note) |
| OPS-02 no scheduling | PARTIAL | compose maintenance loop (`compose:753`); cron still absent locally |
| OPS-03 zero resource limits | FIXED-V | M20-T3 envelopes on every service (compose annotations) |
| OPS-04 unbounded logs | FIXED-V | `logging-caps` rotation all services (`compose:98`) |
| OPS-05 frontend no healthcheck | FIXED-V | M20-T3 (`compose:429`) |
| OPS-06 liveness-only probes | FIXED-V | `/ready` readiness probes (identity/orchestrator verified) |
| OPS-07 pool-math omits models engine | AS-REG | — |
| OPS-10 roles/grants unrestorable | AS-REG | — |
| OPS-11 master key escrow | OPEN (accepted) | R-02d residual; key-versioning not implemented |
| OPS-12 drills prove traffic-serving | FIXED | `m18-c6-rehearsal-evidence.md` |
| OPS-13 retention keeps nothing | FIXED | 0028 + M19-N3; operator table → R-34 |
| OPS-14 large-object invariant | ACCEPTED | note only |
| OPS-15 migration CI never executed | AS-REG | — |
| OPS-16 schema/image drift | FIXED | drift gate + rollback runbook (program-close) |
| OPS-17 PG version matrix | AS-REG | — |
| OPS-18 concurrent migrate race | AS-REG | — |
| OPS-19 pip-audit warn posture | ACCEPTED-DOCUMENTED | `ci.yml:189-191` (gitleaks blocks) |
| OPS-20 E2E never in CI | **FIXED** | `e2e-compose` job in `ci.yml` (2026-10-17): 12-check journey, migrate exit gate, failure artifacts; journey green live twice |
| OPS-21 metrics on 1/9 services | FIXED-V | M20-T2 one scrape target (`compose:858`); live 14 families, 0 poll failures |
| OPS-22 no log aggregation | PARTIAL | structured logs + caps; aggregation not verified |
| OPS-23 PG self-monitoring | PARTIAL | maintenance doc/loop; pg_stat_statements not verified |
| OPS-24 no zero-downtime/rollback | FIXED | `docs/runbooks/rollback.md` + immutable tags (M18-C6) |
| OPS-25 migrate/backends race | AS-REG | — |
| OPS-26 entrypoint supervisor legacy blast | ACCEPTED-DOCUMENTED | ops.md §3; split mode is the shipped default |

## E. Late findings (SRC-NEW) + fixes

| ID | Finding | Status | Evidence |
|----|---------|--------|----------|
| L1 | plan approval replayable forever (area 16) | FIXED → **M23-A** | TTL 24 h (`HARNESS_PLAN_APPROVAL_TTL_SECONDS`); SM + durable service check; `approvedAt` payload; 11 tests; S4 503/5 |
| L2 | no budget warning machinery (area 9) | FIXED → **M23-B** | `HARNESS_TEAM_BUDGET_WARN_PCT` =80; `BudgetStatus.warned`; per-run log once; API `warned`; 10 tests; S4 503/5 |
| L3 | fresh-checkout frontend build broken | SUPERSEDED | fixed + committed `923711f`; sha256.ts tracked at HEAD |
| L4 | session README stale ledger "0030/32" + junk tail | FIXED | `session2/README.md` finalized; live ledger 32 rows / max 0033 / 0014 skipped |
| L5 | session-2 closure artifacts incomplete | FIXED | finalized README + register closure + this matrix |
| L6 | R-02 original verification skipped isolation gate | FIXED (documented) | `S1-R02-*.md` §R-02a revision (guard red→green, contracts promotion) |

## Coverage arithmetic

R-01…R-50 (50) · S2-D1…D6 (6) · GAP F/H/M/D (30) · M17 (80) · ODELTA headliners
(3) · late (6) + M23-A/B (2) = **177 original IDs**, deduplicated to 174 rows
(R-30 = R-05e dedup; S2-D4/D5 = L1/L2 dedup; ODELTA lenses folded into
register rows). Verified this session with a command or file read: R-01,
R-02, R-40, R-47, S2-D3/4/5/6, PBETA C-1/C-2/H-1/H-2/H-3, MAC1 F-1/F-5/F-9,
MAC2 F1-F3/F5/F8/F9, PBC1 F2/F6/F7/F12, OPS-03/04/05/06/13/21/24, GAP
D3/N3-adjacent rows. The two OPEN code items listed at drafting — MAC2 **F4**
(redeem/ws-ticket rate limits) and OPS-20 (E2E in CI) — are **FIXED** (wave of
2026-10-17; see RISK_REGISTER close block). Section-B adversarial wave added:
replay terminal sealing fix (post-terminal rows can no longer resurrect DONE),
20-test transition matrix, demo fixture re-curated to an H-008-legal sequence
(S4 526/7); journey re-verified 12/12. Everything else is FIXED, ACCEPTED
(recorded decision), or AS-REG (register state stands, decay-check scheduled
per session plan).
