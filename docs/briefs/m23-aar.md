# MISSION 23 / Z1 — PROGRAM AFTER-ACTION REPORT, MISSIONS 11–23

> Program historian + auditor lane (TEAM-2). Baseline HEAD 3c87665 (finale
> graph). Every number below was cross-checked against `STATUS.md` history,
> `git log` (264 commits total at close; 168 in the campaign window
> `d5d9619^..HEAD`), and the mission execution records archived in
> `docs/briefs/`. Nothing is reconstructed from memory; where the record has a
> hole, the hole is stated as a hole. Commander's intent honored: this is an
> honest, calibrated record — not a highlight reel.

## 0. Program shape

- **Span:** M11 graph authored 2026-08-23 (d5d9619) → finale graph
  2026-08-26 (3c87665). Four calendar days, thirteen missions (M11–M23),
  dual ELITE-FORCE teams, parallel lanes per wave.
- **Commits:** 264 total on the repo at close; 168 inside the campaign
  window. Per-mission tagged commits (git log, subject-tag match):
  M11 14 · M12 41 · M13 22 · M14 9 · M15 14 · M16 8 · M17 9 · M18 4 ·
  M19 5 · M20 5 · M21 6 · M22 4 (=141 tagged; remainder is untagged
  close/glue/graph commits).
- **Pre-campaign state:** baseline caecff3 (2026-08-22) already carried waves
  1–9 (FATAL/HIGH gap fixes, OTP ownership, OS confinement, split containers,
  durable run queue) — the campaign hardened and productized a working
  platform; it did not start from zero.
- **Exit criteria (finale graph):** AAR with calibrated verdicts (this
  document) · one bet executed (Z2) · zero dead links (Z3) · CHANGELOG covers
  M11–23 (Z3) · v1.0.0-candidate tag ready (Z4) · suites green at HEAD
  (numbers in §5 and STATUS program-close section).

## 1. Mission-by-mission: planned vs landed

| Mission | Planned | Landed | Verdict |
|---|---|---|---|
| **M11** W1 clientela remediation | 6 CRITICALs + flow-blocking HIGHs, 7 lanes | All lanes landed (dc9b75f, 7a6b2b3, 281d769, c2b487c, db9e333 + glue c86a03e); V-Pair seam probes READY (21/21) | **LANDED** — via mass provider kill + rescue lanes (§3) |
| **M12** W2 core UX | 20 lanes (E/F/G families) + ATLAS merge wiring + VT-1..5 | "All 20 lanes landed and gated"; VT fix-loops repaired (e5dc56a apiKey seam, 96edf35 JSONB reserve) and re-verified READY | **LANDED + VERIFIED** |
| **M13** W3 hardening | 10 lanes: pool math, PITR executed live, Playwright E2E, Makefile/CI convergence, NUMERIC money, tz normalization, oracle-404s, SKIP LOCKED | All committed; VT-1..5 READY (VT-5 MINOR fixed in-lane d539b87); CI 32694440516 SUCCESS | **LANDED + VERIFIED** |
| **M14** W4 product | 5 lanes (metering UI, a11y/responsive, invites/optimistic, aggregates, design-system/infra) | CLOSED; all five VT teams READY; CI 32701744809 SUCCESS | **LANDED + VERIFIED** |
| **M15** W5 roadmap-1 | D1–D4, S1–S4, G6a/G6b/G7 incl. architectural-law CI guards | ALEX-FORCE FORCE-COMMITMENT: PROCEED TO CLOSE; guards green (15 routes / 374 files) | **LANDED + VERIFIED** |
| **M16** Wave 6 perf/UI | X1–X5 (runs-index tuning, meeting pagination, WS replay cap, UI systemization, edge-case corpus ×19) | LANDED + CLOSED via ALEX-FORCE re-verification; migration chain 0001→0026 unique | **LANDED + VERIFIED** |
| **M17** elite audit + fixes | 7-seat ELITE audit (one lens each) → Phase B fix lanes B1–B6 → Phase C 10x plan | Audit produced ~1.9k lines of findings (m17-findings-*.md); B1–B6 all landed incl. contracts-promotion pattern (798fdbc) and budget advisory-lock race fix; 10x plan authored @ 70b217c | **LANDED** — see §4 calibration note: no STATUS close entry was ever written |
| **M18** Horizon NOW | C1–C6 per 10x plan §NOW | All six landed (f2fbded + ad76676); rollback drift gate rehearsed LIVE (old image refused vs 0027 DB); CI repair e08ec7d; CI 32843200308 SUCCESS | **LANDED** — one carried item: BASELINE numbers → M19-B1; C3's literal `(team_id, created_at)` index was unrealizable (frozen cost_ledger has NO team_id) and was honestly reshaped to PK + created_at index + rollup |
| **M19** BASELINE + NEXT p1 | B1 sweep + N1–N5 | All six landed (3805ca7, 0bd0d3c, 1ec49e7); ceiling ~1.9 journeys/s published with the wall named (single-claim poll cycle) | **LANDED** — first B1 sweep measured NOTHING (158k requests, all rate-limited); republished from a valid ladder (§4) |
| **M20** NEXT part 2 | T1–T5 | All five landed; T1 claim batching 1.69 → 37.58 journeys/s @ 32 VU (~22×, 4521 journeys, zero failures); TS SDK v0 (4cc7458); CI 32922623925 SUCCESS | **LANDED + VERIFIED** — the program's headline 10x lever |
| **M21** context pillar | X1–X4 (indexing core, @refs, RAG planner, UI surface) | All four landed (826ed9b, 76fd58c); plans demonstrably cite indexed path:start–end chunks (pinned by test); CI repair cb9af7e; flake eliminated b83dbfd (30/30 stability sweep); formal close 8bf1e61, CI 32948436346 SUCCESS | **LANDED + VERIFIED** |
| **M22** commercial skeleton | M1 legal, M2 tier enforcement, M3 Stripe, M4 OIDC SSO, M5 MCP server | Commits f78a52e (legal), 00cfc8e (M2/M3 + M4 in flight), 72cf901 ("MCP server + SSO convergence — final Horizon LATER fixes (M4/M5 complete)"); lint repair bdc71ad | **LANDED-UNVERIFIED** — no independent verifier pass and no STATUS close entry (§4); the strongest claims rest on commit messages only |
| **M23** finale | Z1 AAR+scoreboard, Z2 bet, Z3 polish, Z4 release readiness | In flight at AAR authoring time; Z2 evaluating AST-chunking vs multi-replica proof; Z4 tag not yet applied (`git tag` empty) | **IN FLIGHT** |

### Planned in the 10x plan but NOT shipped (the honest column)

From mission17-10x-plan.md horizons vs. repo reality at close:

- **C16 Editor surface v1** — inline hunk accept/reject, snapshot checkpoints,
  restore-any-point UI: **not built** (tree page remains read-only; M21 added
  the context *preview* panel, which is adjacent but not the editor).
- **C17 MCP *client* + tool registry** — harness consuming external tools
  through the sandbox allow-list: **not built**. What shipped in M22-M5 is the
  inverse direction (harness exposed AS an MCP tool-server). Buyers reading
  "MCP support" should know which half they get.
- **C19 Rules-files promotion to first-class UX** — loader exists (M12-G10);
  precedence-chain UI did not visibly land.
- **Horizon BETs C20–C23** (tab completion, marketplace/plugins, managed
  cloud, meeting-media) — all decision-gated; none triggered. Z2's in-flight
  evaluation (AST-aware chunking upgrade OR multi-replica live proof) is the
  only bet work attempted, correctly gated on proof cost.

## 2. Scoreboard

Lane-by-lane scoreboard rows for missions 18–23 live in
`evolution/ledgers/` (one ledger file per mission, scoreboard format:
lane · team · scope · status · commits · evidence). Earlier missions are
covered by the existing per-wave records (`docs/briefs/mission11-ledger.csv`
and STATUS.md wave sections); this campaign's ledger directory begins at
M18, matching the Z1 brief.

## 3. Incidents

### Provider deaths (all footprint-audited before re-dispatch; no data loss after salvage law)

- **M11 mid-wave mass kill:** provider returned `HTTP 401: Model is not
  supported`, killing ~5 lanes at once. Dead runs had already written most
  deliverables — recovery started from `git status --short`, split survivors
  into ownership-boundary rescue lanes, gated and committed each as it landed
  (five separate commits). Defect class found: killed runs left a test
  importing a nonexistent `BillingPlan`, breaking S1 collection suite-wide.
- **Pre-campaign baseline rate (waves 9–10):** three first-attempt deaths in
  wave 9 alone (503 ×2, 300s timeout ×1), two in M10 — all zero-write.
- **M17 fan-out:** 3 of 7 seats died mid-run (HTTP 500/503/timeouts). MAC2
  died at 250 iterations with zero writes; compacted-scope salvage succeeded
  in 14 calls. This event is the origin of the incremental-write salvage law
  (§6).
- **TOPSPOT wave (2026-08-25, between-mission ops):** 3× consecutive 300s API
  timeouts, all recovered with ZERO loss under the checkpoint-writes
  amendment ("land each file immediately after drafting"). Separately, an
  entire remediation wave was lost to `git worktree remove` with uncommitted
  changes — only a mid-run stash survived. Law since: commit-per-fix on a
  lane branch; never leave bare working-tree changes under teardown.

### CI repairs (27 fix/test-type commits in the campaign window; ~17
repair-class — CI/lint/verifier/gate repairs and flake eliminations, the rest
lane test deliverables; the load-bearing ones)

- **WS hang trio** (b1fb041 handler race, b583fc0 engine disposal,
  4a3f83e single-loop tests): hung CI 15m+, race-style — each test passed
  alone. Root cause: starlette TestClient's portal runs app code on a
  different loop than async fixtures. Permanent fix: MiniWs raw-ASGI driver;
  TestClient banned for WS tests.
- **Lint/gitleaks class** (72bd255 F841/F401, 31f5fd6 dev extras,
  582c044 missing golden + fake-key flag, 33c6406 sk- prefix, 70532c0 UP042/
  I001, d6265f4 M17 matrix incl. scripts-dir detection, e08ec7d, bdc71ad):
  recurring theme — local gate recipes must byte-match CI scope.
- **Migration-numbering collisions** (df53c2d: ws_fingerprint 0024→0025 after
  slot collision with team_settings; M12-G1's retention migration pushed
  0014→0018 mid-flight). Mechanism proven empirically at W5 close-out:
  duplicate filename prefixes both execute on fresh DBs, second INSERT into
  schema_migrations violates PK, single transaction rolls back the whole
  chain ⇒ every fresh install broken. pytest suites cannot catch this (they
  own SQLite fixtures and never run migrate()).
- **M21 pair** (cb9af7e): postgres:18-alpine ships no pgvector ⇒ 0030 now
  gates CREATE EXTENSION on pg_available_extensions (feature honestly OFF
  without it); sibling-venv interpreter boot skipped when absent in CI's
  one-venv-per-job matrix; N1 fire-and-forget emit assertion made
  deterministic. Follow-up b83dbfd: replay-cap flake eliminated, proven by
  30/30 stability sweep.
- **Verifier-driven repairs** (e5dc56a, 96edf35, d539b87, 3435ddb expensive-op
  limiter 9-red→green, dc2fa92/0d40beb/1d77316 cross-section import seams):
  all caught by VT teams or gates, none by self-report.

### Environment/host quirks worth remembering

- Host PG on 127.0.0.1:**5499** (5432 taken locally) — every integration
  command encodes this.
- `HARNESS_CORS_ORIGINS` must be a JSON array (96c1ffb).
- Homebrew ada-url upgrade broke node once (compat symlink fix, M10 era).
- gitleakens flags even FAKE `sk-` keys — inline `# gitleaks:allow` with
  justification is the sanctioned escape.

## 4. Calibration notes — where self-reports were wrong or the record lied

This is the section the commander asked for. Each item: what was claimed,
what was true, what changed.

1. **P1a's false green (pre-campaign, codified during it).** Delegation lane
   reported "50 passed" while an expiry test was failing on the
   timestamp-format bug (space-vs-T separator making expired tickets compare
   valid). Consequence: independent rerun of every lane's suite became gate
   law (`verify_lane.py`; "never trust self-reports" is now charter text).
2. **M19-B1's first sweep measured nothing.** 158,064 requests → 158,034
   RATE_LIMITED + 30 OWNERSHIP_REQUIRED. The limiter was working exactly as
   designed against a single load-gen IP; the "baseline" would have been
   fabricated telemetry. Caught because a zero-percentile locust row means
   zero successful journeys. Procedure change: per-container env verification
   (`docker exec <c> env | grep HARNESS`) + methodology documentation whenever
   a limit is raised for measurement.
3. **M18-C2's 11 reds were the feature working.** Fail-closed budget posture
   denied every run in SQLite scratch fixtures lacking teams/cost_ledger
   mirrors — looked like a regression wave, was the posture doing its job.
   Lesson: attribute reds BEFORE fixing; fixture-vs-prod divergence is the
   #1 missed-bug class (confirmed independently by M17's audit finding that
   the shipped "5 winners" concurrency test passed only via BEGIN IMMEDIATE
   in its own fixture).
4. **Shared-session env leakage.** During M16 gate repair, a sibling lane's
   `HARNESS_TEST_PG=1` export leaked into the persistent terminal session;
   my next full run showed 3 phantom failures + 4 errors that were PG-mode
   artifacts, not regressions. Procedure: `env | grep HARNESS_TEST_PG`
   before trusting any red run.
5. **Collection-error masking.** One truncated test stub (leftover placeholder
   import) aborted WHOLE-suite collection and masked every real failure count
   — failure numbers that look impossible get diagnosed at collection first.
6. **STATUS.md record holes (found by this audit).** Missions 17 and 22 have
   NO close entries in STATUS.md — both missions' completions are attested
   only by commits and side records. M22 additionally has no independent
   verifier pass: its "complete" rests on commit-message claims
   (72cf901 "M4/M5 complete"). Flagged rather than papered over; a closing
   entry for both eras is recorded in §5.
7. **ATLAS merge traffic silently reverting uncommitted work** (M11+ standing
   hazard): lanes lost edits to tracked files mid-flight when merge traffic
   stashed/popped the tree. Countermeasure that held: re-check `git status`
   + line counts of owned files before every edit batch and before verify;
   rewrite from last known-good content when a file shrank.
8. **Numbers that needed re-counting:** STATUS itself carries one correction
   inline (section10's "15 tests" historical figure re-counted 65/65 during
   M12-G4). This audit's rule — recount from source, cite the recount —
   comes from watching that happen.

## 5. Verification state at close

Live re-runs executed by THIS lane at finale HEAD, 2026-08-26 (independent
rerun law applied to the close-out lane itself):

- **section1: 628 passed / 10 skipped** (first sweep: 1 load-dependent
  failure in the replay-cap concurrency test — passed isolated ×6 and on
  full rerun; recorded as residual risk #1 in STATUS program-close).
- **section3: 91 passed · section4: 465 passed / 5 skipped ·
  section11: 50 passed · mcp: 17 passed** (mcp via throwaway venv;
  pydantic_settings missing from its declared deps — flagged to Z3).
- S7 vitest/tsc: not re-run by this lane; last verified green at Mission 21
  formal close (500 vitest + tsc clean, CI 32948436346 SUCCESS).
- Standing rule honored: every historical number in this AAR cites STATUS
  history or git; nothing is averaged, extrapolated, or invented.

Program-close suite matrix, campaign totals (264/168 commits), and the
buyer-facing residual-risk list are maintained in `STATUS.md`
§"PROGRAM CLOSE" so the operational record stays in the operational file.

## 6. Lessons that changed procedure (the durable yield)

1. **Checkpoint-salvage law** (origin: M17 fan-out deaths; hardened TOPSPOT
   2026-08-25): lanes write each deliverable to disk immediately after
   drafting, never holding more than one section unwritten; provider death
   then costs seconds, not waves. Paired with footprint-audit-first recovery
   and identical-brief re-dispatch.
2. **Independent rerun gate** (origin: P1a false green): no lane's "green"
   enters the record without the orchestrator rerunning the suite.
3. **Commit-per-fix on lane branches** (origin: worktree-teardown loss):
   bare working-tree changes under a teardown target are forbidden.
4. **Migration-slot law**: `ls migrations/` immediately before naming a new
   migration, re-check for duplicate prefixes after saving (two independent
   collision incidents proved the race).
5. **Timestamp twin-rule**: ISO TEXT only for text-compared columns;
   real datetime objects for TIMESTAMPTZ binds (asyncpg rejects strings —
   verified live). SQLite/PG behavioral divergence is a standing trap.
6. **Money-typing law**: Decimal params bound as TEXT via
   `Decimal(repr(x)).quantize(...)`; aggregate exactness provable ONLY on
   real PG (HARNESS_TEST_PG=1).
7. **Fresh-workspace law** for dsh engine tests: reused session roots poison
   reruns (completed-with-0-chars lies).
8. **MiniWs single-loop driver** replaces TestClient for WS tests (CI-hang
   trio).
9. **Stability sweeps ≥25 runs** before any flake is declared dead (M21).
10. **PRICING-before-enforcement inversion** (M22): docs pin intended cap
    values as a contract; enforcement implements to the written spec.
11. **Isolated-compose rehearsal pattern** (M18-C6): destructive drills run
    on throwaway projects with up-time evidence that the live stack was
    untouched.
12. **Event-driven collection over sleep-polling**, batch-first dispatch,
    root-session diet — adopted from the delegation meta-audit (~488 lanes
    measured elsewhere) and validated in-repo across M12–M23 waves.

## 7. Residual risk (summary)

Full buyer-facing list lives in STATUS.md program-close section. Headline
items: pgvector-dependent features ship honestly-OFF on the default compose
image; Stripe/SSO are code-complete but never drilled against live
providers; counsel-review placeholders remain in TERMS/PRIVACY; the editor
surface (C16) and MCP-client direction (C17) are unbuilt; M22 lacks an
independent verification pass; single-host scale envelope is measured
(~37.6 journeys/s ceiling, dry engine) but multi-replica production operation
is unproven.

---
*Z1 close-out · TEAM-2 · 2026-08-26 · sources: git log @ 3c87665, STATUS.md
history, docs/briefs/*, skill execution records M18–M22. No git writes
performed by this lane.*
