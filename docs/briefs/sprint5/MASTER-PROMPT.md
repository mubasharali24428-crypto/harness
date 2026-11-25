# Sprint 5 — Master execution prompt (Claude Opus 5.5)

Repository: `/Users/user/untitled folder 2/harness` (GitHub: `mubasharali24428-crypto/harness`)  
Branch: PR branch (e.g. `w28-s2-sprints1-4`)  
Baseline: Sprint 4 closure `e4519ae4` or later CI-green SHA on that branch.

Authority: `FUTURE PLAN 2.0.md` §0, `docs/briefs/evidence/sprint4/END-report-2026-11-24.md`, `docs/briefs/evidence/sprint4/task-27-collab-viewed-detail.md`, `docs/briefs/evidence/10x/x07-backend-preference-2026-11-24.md`, `Makefile`, `AGENTS.md`.

---

## Sprint 4 closed — do not redo

Compose migrated to 0081; `make gate` green; live approval e2e; multiplayer pilot spec; C-05/C-07; client `viewed_detail`. Owner gates still BLOCKED.

## Open gaps (Sprint 5)

| ID | Gap | Tasks |
|----|-----|-------|
| G-X07-3 | Collab `viewed_detail` not in API/KPI | 31–32 |
| G-X07-4 | No live blind KPI proof | 33 (Mac if no Docker) |
| G-E2E-4 | Planning e2e mock-only | 34 (Mac) |
| G-E2E-5 | similarCases not live-proven | 35 (Mac) |
| G-B09-1 | Invite→revoke journey missing | 36 (Mac) |
| G-I08-1 | Denial-learning UI | 37 |
| G-X02-1 | test-all evidence | 38 |
| G-OPS-2 | ci-local snapshot + C-07 template | 39 |

Cloud default: **no Docker** → Tasks 33–36 BLOCKED with logs unless environment has compose.

## Engineering standards

1. Task 40 requires `make gate` GREEN on Mac if cloud cannot run Docker jobs.
2. OpenAPI change → `make check-ts-dist` + `make static-gates`.
3. One task = one commit: `Task <N>: <title>.`
4. Evidence: `docs/briefs/evidence/sprint5/task-<N>-*.md`
5. No owner signatures, payment, global P-D6/P-D7, scheduler auto-execute.
6. Push after each task.

---

### Task 0 — Pre-flight

Create `docs/briefs/evidence/sprint5/START-rev.md`; commit `Sprint 5 pre-flight: START revision ledger.`

---

### Task 31 — Collab API: optional `viewed_detail`

- Extend `ApprovalDecision` in `section4/src/harness_orchestrator/api.py`.
- Thread to `collab.decide_approval`; update `section4/openapi.json` + SDK per repo convention.
- Tests: `make test-section4 -k "collab or approval"`

---

### Task 32 — Audit + blind KPI (collab)

- On approve: audit detail includes `teamId`, `rendering`, `viewedDetail` (use section1 member rendering lookup).
- Extend `section8/src/harness_review/kpi.py` to include `approval.approved` rows with same semantics as `review.approved`.
- Tests: `make test-section8`; document delta in `docs/briefs/evidence/10x/x07-backend-preference-2026-11-24.md`.

---

### Task 33 — Live blind KPI e2e (Mac / Docker)

- Extend or add live Playwright; real admin KPI fetch.
- **BLOCKED** on cloud without stack.

---

### Task 34 — Live planning e2e (Mac / Docker)

- `planning-live.e2e.ts`; keep mocked `planning.e2e.ts` green.

---

### Task 35 — Live similarCases (Mac / Docker)

- Two denials → `similarCases.length >= 1` on live stack.

---

### Task 36 — B-09 invite→revoke (Mac / Docker)

- One journey; update `docs/briefs/evidence/multiplayer/B-09-checklist-2026-11-24.md`; P-D7 still DEFERRED.

---

### Task 37 — Denial-learning UI (I-08)

- Read-only rule offer on stop/denial UX; no auto-apply (CD-02).

---

### Task 38 — `make test-all` evidence

- Run documented groups; `docs/briefs/evidence/sprint5/task-38-test-all.md` with PASS/FAIL per group.

---

### Task 39 — ci-local + C-07 template

- One `make ci-local` run summary (if feasible); add `docs/briefs/evidence/sprint5/c07-pilot-feedback-template.md`.

---

### Task 40 — Closure

- `docs/briefs/evidence/sprint5/END-report-2026-11-24.md`; STATUS paragraph; day90 engineering deltas only; `make gate` result honest.

---

## Cloud scope (this deployment)

**In cloud Opus thread:** 0, 31, 32, 37, 38  
**Owner Mac:** 33–36, final `make gate`, Task 40 gate line

## Anti-patterns

No second audit DB; no KPI counting missing signal as blind; no fake live PASS; no r2 PNG commits.