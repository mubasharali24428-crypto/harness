# hy4 batch 8 hand-back - SUMMARY

Snapshot for every task: `7c1acf4 (HEAD -> w28-s1-delegate) D1: a workspace-root scope covers every relative in-workspace path`, created with `mkdir -p /private/tmp/hy4-b8/snap && git -C "$REPO" archive 7c1acf4 | tar -x -C "$SNAP"`. Import-path checks passed: `harness_orchestrator` resolved to `/private/tmp/hy4-b8/snap/section4/src/harness_orchestrator/__init__.py` and `harness_contracts` to `/private/tmp/hy4-b8/snap/contracts/src/harness_contracts/__init__.py`. All runs used the main checkout's venvs, PYTHONPATH pointed at the snapshot, `-p no:cacheprovider` and a private `--basetemp` from `mktemp -d /private/tmp/hy4-b8-tmp.XXXX`. No deploys, no docker commands, no git writes, no secrets, no .env reads, no production change.

Batch: pinned commit `7c1acf4`; no deploys, no docker commands, no git writes, no secrets, no production change. All four tasks ran to completion; no task was BLOCKED. Times below are wall-clock from `date` and second-precision file mtimes; the session was interrupted and resumed several times, so K1/K2 include long idle gaps. `date` at end of batch: 2026-11-07 08:06:27 PST. Batch span 2026-11-06 23:42:34 to 2026-11-07 08:06:27 = 08:23:53.

The one table:

| Task | Status | Duration | Least sure of |
|-|-|-|-|
| K1 - test and skip ledger | DONE | 00:58:05 | The FLAG/ENV boundary: 121 of 159 skips are env-var gated but also need a real PostgreSQL/Docker service, so a resource-first rule would make ENV the largest class instead of FLAG. |
| K2 - sourced known-limits draft | DONE | 07:12:41 (includes session interruptions) | The Unverified bucket: the seven §4.8-style items may have evidence outside `docs/briefs/evidence` that my greps missed, so 7 may over-count. |
| K3 - shell-gate real-boundary review | DONE | 00:03:40 | The assertion-failability column, because all 31 cases skip on this host and no assertion was actually exercised. |
| K4 - W28 synthesis conflicts | DONE | 00:09:08 | The negative-control mechanism depends on pytest's `pythonpath` ini inserting `src` first, so a different pytest version could import a different `delegation.py`. |

Files written in the main checkout (all untracked, all under `docs/briefs/`): `evidence/rc/skip-ledger-2026-11-06.md`, `evidence/rc/known-limits-draft-2026-11-06.md`, `evidence/rc/shell-gate-real-boundary-review-2026-11-06.md`, `evidence/rc/test_delegation_synthesis_conflicts.py`, and the five hand-backs `hy4-b8-handback-K1.md` ... `-K4.md` and `hy4-b8-handback-SUMMARY.md`. `docs/known-limits.md` was not created; no file under `*/src/*` was changed; no test file in the main checkout's tests tree was added or modified.
