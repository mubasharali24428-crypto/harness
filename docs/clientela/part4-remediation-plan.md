# Part 4 — Remediation Campaign Plan (CLIENTELA)

> Assembly part 4 of 4. Consumes `part1a-critical-high.md` (31 findings) +
> `part2-solutions.md` (~120–180 solution clusters) + raw audits. This file is
> the **execution plan**: how ALEX_TEAM + VERIFIER_TEAM actually fix everything
> without stepping on each other.
>
> Method: ALEX_TEAM three-tier org — ATLAS authors graphs and gates every
> delivery personally (footprint audit → own suite rerun → live probe), one
> lead per squad runs the review ladder, N lane subagents with exactly-one-owner
> per file. VERIFIER_TEAM PRIME+SUB pairs re-audit contract verticals after
> each wave, git-read-only, two-pass (mechanical SUB → judgment PRIME).
>
> Standing rules inherited from AGENTS.md / mission10 practice:
> contracts/ FROZEN · conftest.py FROZEN · migrations append-only numbered ·
> no section imports outside harness_contracts · breaking a contract requires
> version bump + mock update in the same commit · siblings' dirty files are
> expected — ignore them.

---

## 1. Wave plan

Five waves, strictly sequential at the campaign level. Within a wave, lanes run
in parallel; cross-lane seams inside a wave are pinned by ATLAS-authored pair
contracts (both sides implemented by the pair in the same wave, same pattern as
M10-C1/C2/C3). Nothing merges between waves until every gate + verifier verdict
for that wave is green.

### WAVE 1 — Security & flow-blockers (P0) — "the product becomes real"

**Goal:** kill every CRITICAL and make the hero journey physically possible:
no fabricated identities (C-001), audit trail exists (C-002), money limits bind
(C-003), engine can't silently downgrade (C-004), plan gate reachable from UI
(C-005), login lands on a real project (C-006).

| Lane | Mission | Owned paths (verbatim) | Est. agents |
|---|---|---|---|
| W1-A1 | Verified ownership: OTP-enforced team creation, delete legacy direct-add path, rate-limit real auth routes (C-001, H-004) | section1/src/harness_identity/{auth.py,invites.py} (edit), section1/src/harness_identity/ratelimit.py (extend), section1/migrations/0012_auth_hardening.sql, section1/tests/test_auth_verification.py | 1 (+1 fix-loop reserve) |
| W1-A2 | Audit wiring: emit write_audit at every sensitive call site (key mint/revoke, invite lifecycle, billing change, plan reject); extend APPEND_ONLY_ACTIONS (C-002) | section1/src/harness_identity/audit_emit.py (NEW), edits at call sites in section1/src/harness_identity/*.py (registry-pinned list), section1/tests/test_audit_emission.py | 1 |
| W1-A3 | Budget enforcement atomic + fail-closed: wire dsh usage writers into S3 router path, single-statement atomic check-and-reserve, fail-closed on money paths only (C-003) | section3/src/harness_router/budget_guard.py (NEW), section1/src/harness_identity/cost_ledger.py (extend writers), section1/tests/test_budget_atomic.py | 1 |
| W1-B1 | Engine boot integrity: required-env validation at compose entrypoint, remove bool(key)-style live_mode placeholder logic, explicit live/dry mode enum surfaced via status endpoint (C-004, H-005, H-006) | deploy/runners/engine_boot.py (NEW), deploy/docker-compose.yml (engine env block ONLY), section4/src/harness_orchestrator/live_mode.py (NEW), section4/tests/test_live_mode.py | 1 |
| W1-C1 | Plan-gate API surface: proposePlan/approvePlan/startRun endpoints + clients (C-005 backend half) | section4/src/harness_orchestrator/plan_gate.py (NEW), section4/tests/test_plan_gate.py, deploy/runners/services.py app_orchestrator include block ONLY | 1 |
| W1-C2 | Plan-gate UI + real login routing: PlanGate card driven off status.state, kill fabricated proj/run ids, post-login fetch-or-create project (C-005 UI half, C-006) | section7/app/dashboard/planGate.{tsx,planGateApi.ts} (NEW), section7/app/login/page.tsx (edit), section7/lib/session.ts (extend) | 1 |
| PAIR CONTRACT W1-C1↔W1-C2 | ATLAS-authored before dispatch: `POST /api/v1/orchestrator/plans` `{goal}` → 201 {planId, plan}; `POST .../plans/{id}/approve` → {runId}; `POST .../plans/{id}/reject` → 202 (audited via W1-A2); errors use error_body() envelope; both lanes implement BOTH sides of the DTO | — | — |
| W1-D1 | Done-signal & state machine hygiene: reader-role gate, non-empty-diff requirement, terminal DONE state, reject_plan persisted+audited (H-007, H-008) | section4/src/harness_orchestrator/state_machine.py (edit), section4/src/harness_orchestrator/signals.py (edit), section4/tests/test_done_signal.py | 1 |

**Gate strategy:** per-lane footprint==owned-paths → suite rerun by ATLAS →
live probes (real OTP round-trip on scratch app; budget overrun rejected under
concurrency; plan propose→approve→run leaves idle from real UI session).
Sole-owner conflicts resolved up front: services.py include block = W1-C1 only;
compose engine env block = W1-B1 only.

**Estimated agents:** 8 primary (+ up to 3 fix-loop re-dispatches) = **8–11**.

### WAVE 2 — Core UX completion — "the product stops lying"

**Goal:** close HIGH findings that break trust in daily use: real @ai (H-002),
401 handling + session hygiene (H-003, H-015 frontend half), clickable runs +
roster management (H-009, H-010), review page wired to real prIds (H-001),
honest live pill over WS (H-012), meeting persistence (raw-03 #15),
error envelope unification API side (H-019, H-020 idempotency keys,
F10 concurrency control).

Lanes (sketch — briefs authored when part2 lands): W2-E1 @ai LLM wiring
(section6/meeting + section7 client, kills redundant invokeAi);
W2-E2 apiFetch 401 interceptor + sign-out state wipe + NEXT_PUBLIC runtime
config; W2-E3 runs table click-through + roster removal/role-change
(PAIR across S1 API ↔ S7 UI, contract W2-C1); W2-E4 review prId producer
(S8 writes harness.prId on PR open) ↔ review page consumer (PAIR, W2-C2);
W2-E5 WS live pill + debounced refetch + guarded JSON.parse/reconnect backoff;
W2-F1 error envelope consolidation + Idempotency-Key middleware on mutating
POSTs (S3 router-level, one owner); W2-F2 approve/reject optimistic-concurrency
(version column migration 0013_*.sql).

Gate strategy: same as W1 plus seam probes for each PAIR contract (DTO parity
field-by-field, enum equality). Estimated agents: **9–12**.

### WAVE 3 — Hardening & performance — "the product survives contact"

**Goal:** reliability/data integrity under load and failure: pool math fix
(H-017), WAL-G PITR archive_command actually configured end-to-end (H-018),
migration test harness + migrate.py coverage (H-013), E2E hero-journey suite
(H-014), CI/local gate convergence (H-024, H-025), float→NUMERIC(12,6)
(H-023), timestamp convention unification, agent_events retention policy,
heartbeat comparison fix, DSH_WORKSPACE durable volume (H-016), rate-limit
protection rebalance (H-021), billing flag discipline (H-022).

Lanes: G1 DB infra (pool math, retention, heartbeats — migrations 0014–0016,
one owner per migration); G2 backup/PITR credentialed-path completion + restore
drill runbook; G3 QA infra (E2E suite in section7 e2e/, CI recipe fix, revive
version-pin conformance test); G4 numeric/time integrity (NUMERIC migration +
ORM typing sweep, TIMESTAMPTZ normalization — PAIR with G1 on migration
numbering); G5 API hardening (request body caps, oracle-consistent 403/404,
OpenAPI typing islands).

Gate strategy: chaos-adjacent probes (kill -9 mid-run → recovery; DB restart →
fail-open warnings; restore drill to point-in-time on scratch volume). CI must
be green on the exact recipe documented for local. Estimated agents: **8–11**.

### WAVE 4 — Product features — "the product earns its keep"

**Goal:** convert audited gaps (part1a MEDIUMs + part2 clusters flagged
product-facing) into shippable features: cost dashboard live metering end-to-end
(H-011), keyboard support + focus management + responsive layout (raw-03 P2s),
optimistic UI, notification outbox consumers, invite lifecycle admin UI
(raw-01b M2), aggregate run read endpoint (F9), WS reconnect protocol (F7).

Lanes: feature pairs modeled on mission10 (API lane ↔ page lane per contract),
plus a design-systems lane owning section7/components/** exclusively so UI
lanes don't collide. Estimated agents: **10–14**.

### WAVE 5+ — Roadmap items — "the backlog with owners"

**Goal:** everything part2 classifies as roadmap: security headers CSP
enforce-mode rollout (L2), WS ticket client-binding (L3), salted KDF upgrade
with re-encryption migration (M4), FK deletion story (DB#6), vacuum/maintenance
schedule (DB#12), reviewer-heuristic tuning (raw-02 L4), docs/compat debt
(raw-12), DX polish batch (raw-14), data-infra maturation (raw-15), perf/scale
program (raw-16), UI element systemization (raw-17), edge-case corpus (raw-18).

Shape: quarterly-scale program of missions, each mission10-sized (≤10 lanes,
≤2 squads), prioritized by part2 cluster severity. No fixed agent count;
budget **~10 agents per mission**, 1–2 missions/month steady state.

### Dependency notes (why this order)

- C-002 audit wiring (W1-A2) precedes W2-F1/F2 because approve/reject auditing
  depends on emission infrastructure existing.
- Budget atomicity (W1-A3) precedes any Wave-4 metering UI — dashboards on top
  of inert caps would be decorative.
- State machine hygiene (W1-D1) precedes E2E suite (G3): E2E asserts terminal
  semantics that don't exist yet otherwise.
- Error envelope unification (W2-F1) precedes OpenAPI typing cleanup (W5):
  you can't type islands of an envelope that's about to change.
- NUMERIC migration (G4) must land before Wave-4 cost dashboard consumes money
  values as decimals end-to-end.

---

## 2. Wave 1 lane briefs (dispatch-ready skeletons)

Shared preamble pasted into every dispatch (the **baseline rule**):

> BASELINE: `<wave-1 sha>` at dispatch. Work from a clean checkout; siblings'
> dirty files are expected — ignore them. PURE-WRITE LANE: if your provider is
> degraded, keep writing owned files from what you already read and say so
> honestly in the report — NEVER fabricate test output, probe results, or file
> contents you did not produce. ZERO `git` state changes (no commit/branch/
> stash); coordinator merges. NO package installs, NO conftest.py edits,
> NO edits outside owned paths (footprint audit will catch it).
> REPORT FORMAT: per-deliverable status · full suite command + pasted output ·
> footprint list · honest-unknowns section. Provider outage protocol: on first
> provider error, finish the current file write, then continue best-effort and
> mark every unverified claim `UNVERIFIED`.

### BRIEF W1-A1 — Verified ownership & auth hardening

- **Mission:** C-001 + H-004. Team creation requires OTP-verified email
  ownership by default; delete the legacy direct-add invite path entirely
  (no flag, no shadow mode); rate limiter moved onto real auth routes
  (`/auth/login`, `/auth/verify-otp`, invite mint) with per-IP+route keying,
  fail-open on DB-down (availability > strictness) with warning log.
- **Deliverables:** edited `auth.py` / `invites.py`; extended
  `ratelimit.py::install_rate_limit` coverage; migration
  `0012_auth_hardening.sql` (rate buckets index additions only — additive);
  `tests/test_auth_verification.py`: unverified-email team-create → 403;
  verified round-trip → 201; legacy endpoint → 404/410; burst blocked →
  window reset → allowed; OTP double-redeem rejected (L5 while you're there).
- **HARD RULES:** do NOT touch `conftest.py` or existing auth tests except to
  UPDATE expectations broken by removing the legacy path — list every such
  edit in the report. Never log OTP codes or tokens. Migration numbered 0012,
  never edit earlier migrations.
- **VERIFY-CONTRACT:** ATLAS reruns `test_auth_verification.py` + full S1 suite
  green + ruff clean on owned files; live probe = scratch-app OTP round-trip
  (request → code from test fixture → verify → create team) plus burst→429
  with Retry-After header present.

### BRIEF W1-A2 — Audit emission wiring

- **Mission:** C-002. Every allowlisted sensitive action emits `write_audit`.
  Call-site registry (pin this list): apikey.added / apikey.removed in key
  mint+revoke handlers, invite.minted / invite.redeemed / invite.revoked,
  billing.plan_changed, plan.rejected (consumed by W1-D1), run.started /
  run.stopped. Extend APPEND_ONLY_ACTIONS to match.
- **Deliverables:** NEW `audit_emit.py` (thin typed helpers so call sites stay
  one-liners); call-site edits at exactly the pinned registry locations;
  `tests/test_audit_emission.py` asserting one row per action with correct
  actor/team/target ids and that append-only guard still holds.
- **HARD RULES:** emission must never throw into the request path — wrap and
  warn. No audit content may include secrets (keys, tokens, OTPs). If a pinned
  handler doesn't exist under that name, STOP and report the drift — do not
  guess.
- **VERIFY-CONTRACT:** grep-probe proves zero remaining "allowlist without
  call site" gaps; ATLAS rerun green; live probe mints+revokes a key via
  scratch app then reads audit rows back.

### BRIEF W1-A3 — Atomic budget enforcement

- **Mission:** C-003. Budget caps bind: usage writers wired on the S3 router
  spend path into `cost_ledger`; check-and-reserve made atomic
  (single-statement upsert/row-lock, TOCTOU closed); money paths fail-CLOSED,
  non-money paths unchanged.
- **Deliverables:** NEW `section3/src/harness_router/budget_guard.py`
  (reserve/release API over ledger); writer hooks in S3 route handlers;
  `section1/tests/test_budget_atomic.py`: N concurrent claims against budget B
  → exactly ⌊B/cost⌋ succeed; overrun attempt → 402-class rejection using
  contract error envelope; ledger rows reconcile to accepted work exactly.
- **HARD RULES:** no new packages; parameterized SQL only; do not modify
  frozen contracts/ — if an envelope shape is missing, report it as a
  contract-bump candidate instead.
- **VERIFY-CONTRACT:** concurrency probe under thread pool at gate; ATLAS
  reruns both sections' suites; fail-closed proven by simulating ledger
  unavailability (exception injection in test) → request rejected, not
  allowed.

### BRIEF W1-B1 — Engine boot integrity & live-mode honesty

- **Mission:** C-004 + H-005 + H-006. Boot fails loudly when required engine
  env is missing/degenerate (no silent Ollama fallback, no master-key dev
  default in compose defaults, ALLOW_DEV removed from default compose);
  `live_mode` becomes explicit enum {live,dry} from validated config, not
  bool(key); forced-downgrade ladder deleted per AGENTS.md law.
- **Deliverables:** NEW `deploy/runners/engine_boot.py` (validate-or-exit with
  actionable message); NEW `section4/src/harness_orchestrator/live_mode.py`
  + tests proving placeholder keys are rejected; compose engine env block
  edit ONLY; downgrade-ladder removal with result attribution kept honest
  (raw-02 L1 note in report).
- **HARD RULES:** compose edit limited to engine env block (W1-B1 sole owner);
  boot validator must not print secret VALUES, only names; any behavior change
  to model selection must be listed explicitly for lead review.
- **VERIFY-CONTRACT:** compose config valid both modes; boot script tested
  with missing env → exit≠0 + clean message; ATLAS greps compose for
  `ollama` default absence; live probe hits status endpoint showing
  explicit mode.

### BRIEF W1-C1 — Plan-gate orchestrator API

- **Mission:** C-005 backend half. Endpoints per PAIR CONTRACT W1-C1↔W1-C2:
  propose → approve → startRun reachable, reject persisted AND audited
  (emits via W1-A2's helper — import through harness_contracts-compliant
  seam, coordinate exact symbol with ATLAS at merge).
- **Deliverables:** NEW `plan_gate.py` (own APIRouter) +
  `tests/test_plan_gate.py` (happy path, reject path persists event+audit,
  role gates: admin/engineer approve, viewer none, idempotent re-approve
  returns same runId); services.py app_orchestrator include block ONLY.
- **HARD RULES:** sole owner of services.py this wave — one include line, no
  other edits; state transitions must go through state_machine vocabulary
  (coordinate with W1-D1 who owns that file — do NOT edit it yourself).
- **VERIFY-CONTRACT:** OpenAPI shows three routes with contracted shapes;
  ATLAS reruns S4 suite; seam probe mounts router on scratch app and walks
  propose→approve→run-start leaving idle.

### BRIEF W1-C2 — Plan-gate UI & real login routing

- **Mission:** C-005 UI half + C-006. Dashboard PlanGate card driven off
  `status.state` (propose/approve/start/reject buttons per state); login
  fetches the caller's real project post-auth or routes to project-create
  flow; fabricated `proj-test-1`/`run-timestamp` literals deleted everywhere.
- **Deliverables:** NEW `section7/app/dashboard/planGate.tsx` +
  `planGateApi.ts` implementing BOTH sides of the W1 DTO (client types mirror
  response models field-by-field); edited `login/page.tsx` + minimal
  `lib/session.ts` extension; component tests for state-driven rendering.
- **HARD RULES:** DTO fields exactly per contract — no invented endpoints;
  no global busy-flag introduction (raw-03 #18 stays out of scope); tsc must
  pass whole-project.
- **VERIFY-CONTRACT:** vitest+tsc green; seam probe = planGateApi client
  shapes diffed against W1-C1's response models field-by-field (ATLAS runs
  after both land).

### BRIEF W1-D1 — Done-signal & terminal-state hygiene

- **Mission:** H-007 + H-008. done-signal gated to executor role, requires
  non-empty diff, cannot resurrect stopped runs; REVIEW_PASSED reaches a
  terminal DONE state; reject_plan writes event + audit row (W1-A2 helper)
  and survives restart without resurrection.
- **Deliverables:** edited `state_machine.py` + `signals.py`;
  `tests/test_done_signal.py` covering: reader-role rejection, empty-diff
  rejection, stopped-run resurrection rejection, zombie-EXECUTING walk
  reaching DONE, restart-persistence of rejected plans.
- **HARD RULES:** state_machine.py edits are W1-D1-exclusive — W1-C1 reads
  but never writes it; no migration unless a state value must be backfilled
  (then 0013, coordinate with ATLAS).
- **VERIFY-CONTRACT:** ATLAS reruns S4 suite; live probe drives a fake run
  through propose→approve→execute→done→DONE-terminal and asserts no further
  transitions accepted.

**Pair-contract note:** W1-C1↔W1-C2 is the wave's single cross-squad seam;
both lanes receive the identical contract text above and ATLAS diffs the two
implementations at gate before either merges.

---

## 3. Verifier plan

VERIFIER_TEAM deploys after every wave gate, never during lane work. Each
PRIME+SUB pair = one delegated worker, two passes in order (SUB mechanical:
footprint/suite/lint/anti-synthesis greps with NO builder self-reports read
first; PRIME judgment: contract conformance, security lens, cross-seam probe).
Writes only inside vertical owned paths for MAJOR/MINOR findings; BLOCKERs are
report-only. Verdicts: `READY-TO-MERGE` | `FIX-LOOP:<lane-id>`.

### Per-wave verticals

| After wave | Pairs | Vertical (contract) | Why this vertical |
|---|---|---|---|
| W1 | V-Pair-1 | AUTH vertical — W1-A1 ↔ W1-A2 seam (verification state → audit rows; rate-limit coverage map vs real route table) | identity chain is the campaign's trust root |
| W1 | V-Pair-2 | PLAN-GATE vertical — contract W1-C1↔W1-C2 (DTO parity field-by-field, enum equality, reject-path audit+persist) | the wave's only cross-squad seam |
| W1 | V-Pair-3 | MONEY/STATE vertical — W1-A3 budget atomicity + W1-B1 boot integrity + W1-D1 terminal states | money and lifecycle invariants need one judgment pass |
| W2 | V-Pair-4 | API-ENVELOPE vertical — error envelope + Idempotency-Key middleware + concurrency columns across all touched routes | envelope change fans out widest here |
| W2 | V-Pair-5 | MEETING/REVIEW vertical — @ai wiring + prId producer/consumer pair | LLM-output honesty is anti-synthesis-critical |
| W3 | V-Pair-6 | DATA-INTEGRITY vertical — migrations 0013–0016 chain, NUMERIC sweep, timestamp unification | migration mistakes are unrecoverable at scale |
| W3 | V-Pair-7 | RECOVERY vertical — PITR restore drill evidence, E2E suite honesty, CI/local parity | disaster paths are unverifiable by builders alone |
| W4 | V-Pair-8 | FEATURE vertical(s) — one pair per PAIR contract shipped that wave (naming per mission graph) | feature pairs get the M10 treatment |

### Standing seam probes (run every applicable wave)

1. **DTO parity:** client DTO fields vs handler response models field-by-field,
   including nullability.
2. **Route-table census:** OpenAPI route set == contracted set exactly (no
   invented endpoints, none missing).
3. **Enum equality:** status/state vocabularies match 1:1 across S4 source,
   TS mirror, and client constants.
4. **Audit completeness walk:** perform each sensitive action via scratch app;
   assert one audit row each; assert append-only guard holds.
5. **Authz matrix probe:** admin/engineer/viewer × write/read per new endpoint.
6. **Anti-synthesis scan:** hardcoded returns, new skips/xfails, mocks left in
   prod paths, TODO/FIXME reported done, invented config names.
7. **Restart-persistence probe:** stop app mid-flow, restart, assert no state
   resurrection beyond contracted semantics.

Wave-specific additions: W1 adds OTP round-trip + burst→429 probes (Pair-1)
and concurrent-budget probe (Pair-3); W3 adds point-in-time restore drill on a
scratch volume (Pair-7); W2 adds idempotent-replay probe (same Idempotency-Key
twice → same result, second not double-charged).

---

## 4. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Provider outage mid-wave strands lanes with half-written files | HIGH (observed: A4 first attempt died at 43m, zero writes) | schedule slip, temptation to fabricate | PURE-WRITE briefs everywhere (keep writing from what's already read); provider-outage protocol in shared preamble (`UNVERIFIED` marking); fix-loop reserve agents per wave; ATLAS re-dispatch is cheap because footprints make partial work resumable |
| R2 | Cross-lane seam drift (two lanes implement "the same" DTO differently) | MED-HIGH | merge-day integration failures | ATLAS authors contracts BEFORE dispatch; both paired lanes implement BOTH sides; seam probes are standing verifier items (#1–#3); ATLAS diffs both implementations at gate pre-merge |
| R3 | Regression risk on frozen contracts (contracts/, conftest.py, migration 0001) | LOW freq / CRITICAL impact | unforgivable-sin territory | hard rules in every brief; footprint audit catches any touch; verifiers re-audit frozen-file mtimes/content hashes per wave; contract changes require version bump + mock update same commit — routed to ATLAS, never lanes |
| R4 | Sole-owner collisions on shared files (services.py, compose, login/page.tsx) | MED | clobbered edits, wasted loops | exactly-one-owner table per wave published with the graph; include-line-only rules; compose block-scoped ownership (W1-B1 engine env ONLY) |
| R5 | Migration numbering collisions across parallel DB lanes | MED | broken migration chain | ATLAS pre-assigns numbers in the graph (0012=W1-A1, 0013=W1-D1-if-needed, 0013–0016=G-lanes); append-only rule restated in every DB brief |
| R6 | Anti-synthesis under deadline pressure (stubs marked done) | MED | false completion, verifier churn | anti-synthesis greps are PASS-1 mechanical (SUB) before any judgment; verdict table requires pasted suite output, not claims; honest-unknowns section normalized in report format |
| R7 | Scope creep from ~150 part2 clusters into waves | HIGH | waves balloon, gates starve | wave goals are finding-ID-pinned; anything not pinned goes to Wave 5+ backlog explicitly; lead ladder rejects unbriefed deliverables |
| R8 | Verifier bottleneck at wave end (all pairs land simultaneously) | MED | days lost idle | pairs dispatched the moment their vertical's lanes pass ATLAS gate, not wave-end batched; W1's three verticals gate independently |
| R9 | Test-env divergence masks real failures (H-024 class) until late | MED | green-but-broken merges | G3 (CI/local recipe convergence) pulled into Wave 3 ahead of feature waves; ATLAS reruns use the documented local recipe verbatim |
| R10 | Security fixes break legit flows (rate-limit fail-closed by mistake, OTP lockout) | MED | availability incidents | fail-open-on-DB-down specified where strictness < availability; burst tests include window-reset recovery; authz matrix probe catches over-blocking |

---

## 5. Effort totals

### Agents × waves (primary + fix-loop reserve)

| Wave | Primary lanes | Est. agents (incl. reserves) | Verifier pairs |
|---|---|---|---|
| W1 security/flow | 8 | 8–11 | 3 |
| W2 core UX | 9–12 | 9–14 | 2 |
| W3 hardening/perf | 8–11 | 8–13 | 2 |
| W4 product features | 10–14 | 10–16 | 1–2 |
| **Campaign total** | **35–45 lanes** | **≈ 40–55 agent-runs** | **8–9 pairs** |

Plus standing overhead: ATLAS coordinator (full-time), 2 squad leads
(part-time, review ladders), ≈ 8–9 verifier agent-runs. Realistic campaign
envelope: **~50–65 total agent dispatches**.

### Calendar estimate at 10-agent concurrency

Assumptions: lane ≈ 0.5–1 day wall-clock typical (mission10 lanes ran minutes
to hours; A4-class stalls happen); gate + lead review ≈ 0.25 day per lane
batch; verifier pass ≈ 0.5 day per pair; one fix-loop round priced into each
wave.

| Phase | Wall-clock |
|---|---|
| Wave 1 (8 lanes, 3 verifier pairs, contract-first day) | 2–3 days |
| Wave 2 (9–12 lanes) | 2–3 days |
| Wave 3 (8–11 lanes, restore drill) | 2–3 days |
| Wave 4 (10–14 lanes, design-system lane serializes some UI work) | 3–4 days |
| Final full-sweep + docs/ledger close | 0.5 day |
| **Campaign** | **≈ 10–14 calendar days** at 10-agent concurrency |

Concurrency note: 10 slots cover any single wave fully; waves remain strictly
sequential because each consumes the previous wave's merged baseline. The
binding constraint is not agents but ATLAS gate throughput (~6–8 personal
gates/day sustainable) — if that saturates, promote one lead to delegate
mechanical reruns.

---

## Appendix — part2 ingestion hook

When `part2-solutions.md` lands: (1) map each solution cluster to its part1a
finding IDs; (2) clusters fixing C-*/H-* IDs named above slot into W1/W2 lanes
as additional deliverables WITHOUT changing owned paths; (3) unmapped clusters
default to W4 or W5+ by severity; (4) update §5 totals; (5) re-run the
dependency-notes check. No other edits to this file should be needed.

