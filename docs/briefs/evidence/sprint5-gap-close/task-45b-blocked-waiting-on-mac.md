# Task 45b: BLOCKED, waiting on owner Mac (2026-11-25)

`task-45-readiness-drift.md` lives on PR 27's branch (`w28-s2-sprints1-4`), not on master. To avoid an add/add conflict, this note is a separate file; fold it into `task-45-readiness-drift.md` when PR 27 lands, or paste it there if the owner wants it on the branch.

## 45b status: BLOCKED
Needs the owner's Mac: Docker, RC stack `harness-rc`, redeploy at schema head **0082** (dev compose is already `80|0082`; that is not the RC manifest). The manifest `docs/briefs/evidence/90day/image-manifest-vv90r6-deployed.json` still reflects the 0076 era. Not edited. No migration field may move without a redeploy. Forward-only; no `schema_migrations` edits.

Commands and the handback block are in `task-45-readiness-drift.md` § "Owner commands" on the PR 27 branch. Cloud records the result only after the owner pastes the handback:

```
Mac truth at SHA <sha>:
- stack_tags Agreement: TBD
- schema_migrations: TBD
- make readiness-drift (CI) rc: TBD
- readiness-manifest written: TBD (path)
```

Cloud follow-up after the paste: commit manifest plus narrative fields, link the green `readiness drift (manifest vs HEAD)` run, flip this note to PASS with timestamps. Until then: **BLOCKED, not PASS**.

## Open decisions (documented only, nothing un-xfailed)

| Item | State | Rule |
|---|---|---|
| F-05 (read semantics / shell-gate) | **OPEN**, blocks the 7 shell-gate strict xfails | No un-xfail without a decision record in `docs/decisions/`. |
| Blind-approval rate | **UNPROVEN**, no pilot data | No invented metric. ReviewPane `diffSeen` semantics under R4 also undecided (see `pr-27-merge-options.md`). |
| PR 28 checkpoint 1 | draft, base PR 27 | Wait for the PR 27 strategy. |
| P-D7 | DEFERRED | Unchanged. |

Verdict: **NOT READY**.
