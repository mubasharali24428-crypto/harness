# Week 01 evidence ledger — 90-day plan (Nov 11–17, 2026)

Plan: `docs/plans/90-day-execution-plan-2026-11-11.md`. Baseline: `w28-s1-delegate` @ `7c1acf4`.
Ledger opened 2026-11-11 (Day 1). States per FUTURE PLAN §2. Fleet writes handbacks; only R (owner)
marks anything Verified. **Testing is executed by the test agent, not the build fleet** — commands
and recording slots are in §4.

## 1. Task states

| Task | Lane | State | Evidence / blocker |
|---|---|---|---|
| P-02 branch/worktree inventory (F-02) | Fleet | **Implemented—unverified** (owner review pending) | `docs/plans/branch-inventory-2026-11-11.md` (delta over 2026-11-06 base) |
| F-03 env manifest + drift report | Fleet | **Implemented—unverified** (compose manifest) / **Blocked** (live capture: Docker daemon down) | `docs/briefs/evidence/90day/env-manifest-2026-11-11.md` |
| P-01 20 outreach messages | **Owner (R)** | **Not started** | Owner lane — cannot be executed by fleet. Pitch + ledger template: plan §2. Accelerator materials already exist (`docs/yc/`, Techstars demo) — that lane is separate from design-partner recruitment. |
| P-03 SOW template + pricing band | Owner (R), fleet drafts | **Implemented—unverified** (fleet draft delivered early, W2 item) | `docs/legal/PILOT_SOW_TEMPLATE.md` — plugs into existing legal packet + PILOT_SOW_DPA_CHECKLIST; inherits macOS-only/local-provider pilot limits; dollars = P-D4, rail = P-D2 (owner + counsel). Legal README table updated. |
| P-04 sandbox review + drill prep | Fleet | **Implemented—unverified** (review delivered; drills = test agent) | `docs/briefs/evidence/90day/p04-sandbox-review-2026-11-11.md` — 5-backend boundary table, 14 gaps, drill handoff in §8. Top gaps: Landlock network no-op (`confinement.py:446`, spot-checked by R-lane fleet), process-mode `/proc` read exposure, worker-daemon host-root-equivalent. October Landlock fixes present in code (`confinement.py:480-498, 348-352`). |

## 2. Findings the owner must see (Day 1)

1. **`key-pool-s1`: 49 unique commits, no upstream, nothing pushed.** Exists only in this repo +
   clean worktree `harness-wb-kp`. Loss risk if ever cleaned. Review + push or record disposition.
2. **Active uncommitted fleet work**: `wb-hy4` 44 dirty, `wb-qwen` 35, `wb-oc` 15, `wb-codex` 7,
   `harness-baseline` 19. Do not clean/reset any worktree (F-02 is non-destructive).
3. **PR #24 open, 15/15 CI green** (run 42195189977); 1 unpushed commit on `w28-s1-delegate`
   (`7c1acf4` D1 workspace-root scope). Merge decision is the owner's; A-06 image set follows it.
4. **Binding decision untracked**: `docs/decisions/2026-11-06-scale-tiers.md` is cited by FUTURE PLAN
   E-04 but not committed. Commit it (inventory §C).
5. **Docker daemon down** → no live stack; drift unverifiable until owner starts it (manifest §5).
6. **Two untracked new tests** (`test_shell_gate_real_boundary.py`, `test_stack_tags_exit.py`) —
   routed to test agent below; A-01-adjacent.
7. **YC W27 application + video plans exist** (`docs/yc/`, prepared 2026-11-01, untracked) and a
   Techstars local demo (`docs/demo/techstars-local-demo.md` + fixture). Accelerator deadlines are
   owner-lane calendar items; the 90-day gate evidence (partners, benchmark, revenue) feeds them.

## 3. Decisions requested from owner (this week)

- P-D1 (self-host vs cloud first) — recommendation on record: self-host first.
- P-D2 payment rail verification start (Paddle/FastSpring PK eligibility vs US LLC + Stripe).
- P-D3 rename shortlist (Harness Inc collision).
- Disposition: `key-pool-s1` push/review; PR #24 merge; untracked-docs commit (inventory §C).

## 4. Test-agent handoff (W1) — run, record, do not fix

Record results in §5 with date, host, exact revision, and raw output link. Build fleet does not
run these.

| # | Command (from repo root unless noted) | Host | Pass criteria |
|---|---|---|---|
| T1 | `python3 scripts/ops/stack_tags.py` | macOS, **after owner starts Docker** | Prints running services; agreement ONE tag or explicit mix list (capture either) |
| T2 | `cd section5 && ./.venv/bin/python -m pytest tests/unit/test_shell_gate_real_boundary.py -q` | macOS + Linux if available | Record pass/xfail/fail per case; this test is untracked — results inform A-01 disposition |
| T3 | `cd scripts/ops && ../../section1/.venv/bin/python -m pytest test_stack_tags_exit.py -q` (or any section venv with pytest) | macOS | Exit-code contract of stack_tags holds (always 0) |
| T4 | `make gate` | macOS, at the revision owner names after PR #24 decision | Green; record skips/flakes with reasons |
| T5 | `HARNESS_TEST_PG=1 make pg-probe` | macOS, Postgres reachable at `localhost:5499` | Real-PG probe green |

## 5. Test-agent results (paste below)

_(empty — awaiting test agent)_

## 6. Friday close (owner)

- [ ] Ledger states reviewed; Verified marks applied only with linked evidence
- [ ] Outreach count: ___ / 20 (P-01)
- [ ] Decisions §3 recorded or explicitly deferred
- [ ] Next-week brief issued to fleet