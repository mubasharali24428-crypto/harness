# hy4-b4-handback-D2

Task: D2 — Real-boundary tests for the shell gate (test-only)
Status: **BLOCKED** — not started. No test file was created, no canary was created, no real
command was executed under the gate, nothing under `section5/src/` was touched.

**BLOCKED: the working tree changed underneath this batch in files I did not touch and that were
not listed in the `git status` from the first hand-back (`hy4-b4-handback-D1.md`).**

The batch 4 STOP CONDITIONS say to stop and write `BLOCKED:` when that happens. Batch 2's
STOP CONDITIONS (still in force) also say to stop when "the tree has changes you do not recognise
in files you are about to edit ... list anything else and wait". D4 is about `STATUS.md` and
`PROJECT-STATE.md`; both changed. I stopped at the D1→D2 boundary and started no new work.

## Evidence (all read-only git commands; I made no git write)

At session start (00:29, saved verbatim in the D1 hand-back):

```
git log --oneline -3
f5b298a Master prompt for hy4: zero-cost engineering tasks, reviewer keeps spend and deploys
4fd8b3a W33: same-task comparison with opencode, decision briefs for people-owned rows, SLO row corrected
9b5e353 Deploy W27 slice 3 on vw27s3; live breaker drill passes

git rev-parse --abbrev-ref HEAD -> w28-s1-delegate
git rev-parse --short HEAD     -> f5b298a
git status --short             -> 66 lines (see D1 hand-back)
```

Later in the same session, without any commit/merge/checkout/reset by me:

```
git log --oneline -3
0f55edb (HEAD -> w28-s1-delegate, origin/w28-s1-delegate) Docs: reconcile STATUS and PROJECT-STATE with git, GitHub and the live stack
277f007 Merge remote-tracking branch 'origin/master' into w28-s1-delegate
7cdd365 Docs: decision notes, redeploy runbook, hy4 hand-backs and master prompts (batches 1-4)

git log -1 --format='%h %ci %s' -> 0f55edb 2026-11-06 00:41:13 -0800 Mubashar Ali | Docs: reconcile STATUS and PROJECT-STATE with git, GitHub and the live stack
```

`git reflog -8 --date=iso` (all entries are commits/merge by another author, none is a git write
of mine — my git commands in this session were `status`, `log`, `rev-parse`, `diff`, `show`,
`reflog`, all read-only):

```
0f55edb HEAD@{0} commit: Docs: reconcile STATUS and PROJECT-STATE with git, GitHub and the live stack
277f007 HEAD@{1} merge origin/master: Merge made by the 'ort' strategy.
7cdd365 HEAD@{2} commit: Docs: decision notes, redeploy runbook, hy4 hand-backs and master prompts (batches 1-4)
0c57564 HEAD@{3} commit: Add stack-tag drift test, M01 native-edit drill and shell-gate scope edge tests
e59f7ab HEAD@{4} commit: Frontend: synthesis panel, scope-request editor with a11y, tool-activity and aggregate updates
4d1cfbe HEAD@{5} commit: Delegation: treat ./, trailing / and doubled / as noise in _covered
bcf79d7 HEAD@{6} commit: Test: make the reclaimed-run fence test deterministic under CPU load
57b466e HEAD@{7} commit: W28: repeated-denial stops in the orchestrator loop guard
```

Committed between 00:29:17 and 00:41:13 (my D1 work ran 00:36–00:47, so the two overlapped).
`git diff --name-only f5b298a..HEAD` → **69 files**. In particular:

* `STATUS.md` and `docs/briefs/PROJECT-STATE.md` were **clean at my baseline** (absent from the
  66-line `git status --short`) and now have content changes: `STATUS.md` +15 lines (new
  `## 2026-11-06 — Reconciliation: what was local-only, what is deployed, what is open` entry),
  `PROJECT-STATE.md` +16/-5 lines. These are D4's subject files.
* `docs/briefs/2026-11-05-w28-delegation-gate.md` is a new committed file not in my baseline.
* The 62 other baseline entries (batch 1–3 work, including the previously untracked
  `hy4-*-handback-*.md`, `scripts/ops/`, `section5/tests/unit/test_shell_gate_scope_edges.py`)
  were committed; they no longer appear in `git status --short`.
* `git status --short` now has 4 entries: the 3 files I edited in D1 and `?? docs/yc/`.

The new `STATUS.md` entry also **contradicts the premise of this batch's prompt**: it says
"hy4 batches 1 to 4 are committed", that the batch-4 prompt exists "with no hand-back yet", and
that W27 slices 1–3 are deployed; the batch 4 master prompt starts from "HEAD f5b298a,
nothing committed unless the reviewer says so in the first line of this message" and D4 must keep
"uncommitted in the working tree" and "deployed" apart. Writing D4 against a stale read of
`STATUS.md` / `PROJECT-STATE.md` would produce a wrong draft. This needs a person's decision,
which is a second, independent BLOCKED trigger.

## What I did instead of starting D2

* Did not create `section5/tests/unit/test_shell_gate_real_boundary.py`.
* Did not create any canary directory or file, did not set `HOME`, did not run any real command
  through `shell_gate.run`, did not read or list any real private/home file.
* Did not read `shell_gate.py`, `confinement.py`, `policy.py`, the runner files or
  `requires_confinement` for D2 purposes (only the already-read `shell_gate.py` lines cited in the
  D1 hand-back, from the earlier probe for the `.`-scope meaning).
* Did not touch any file under `section5/src/`, the JS plugin, profiles or Docker.
* Did not weaken any skip. **NEEDS-REVIEWER-RUN**: the whole of D2, with the exact commands in the
  batch 4 prompt, plus the `write_roots=None` negative control and its `cmp` proof.

Note on the new rule 1 ("never touch real private files"): the repository itself is at
`/Users/user/untitled folder 2/harness`, which is under `~`, and the controlling prompts require
working there. I read rule 1 as applying to private home content and to any path used as a test
target: no home/private file was read, listed, printed, or used as a command target in this
session, and no D2 test exists to point at one. The only `/Users/user/...` path used was the
repository itself, as mandated by the batch 2 prompt. Flag it if the reviewer reads the rule more
strictly.

## Commands run / files changed / negative control / NOT verified

* Commands run: the read-only git commands above. `git status --short` saved to
  `/private/tmp/hy4-b4/git-status-after-d1.txt` and diffed against the baseline → `DIFF_RC=1`
  (the drift). No D2 pytest, no `make lint` for D2, no real gate execution.
* Files changed by me in this step: none (only this hand-back).
* Negative control: not applicable — D2 was not started, so no behaviour was added and no control
  is owed. The `write_roots=None` control is **NEEDS-REVIEWER-RUN**.
* What is NOT verified: everything in D2, D3, D4, D5. Whether the new HEAD is intended. Whether
  `_LINUX_READ_ROOTS` / the Seatbelt `(allow file-read*)` hypothesis holds (unread, not guessed).
  Whether confinement is available in this environment (not probed).
* Unrelated findings (one line each):
  - The new `STATUS.md` entry says "hy4 batches 1 to 4 are committed" and lists six commit hashes;
    it also records that the frontend does not include the synthesis panel and that the live
    orchestrator image predates `57b466e`.
  - The batch 3 hand-backs are now committed files; D5's append-only instruction would edit
    tracked files that were untracked when those hand-backs were written.
  - `docs/yc/` remains untracked and untouched (batch 2 hard limit 5: not mine).
* Risks the reviewer should check first: (1) confirm the eight commits / `origin/master` merge were
  intentional; (2) re-baseline the batch against `0f55edb` before resuming; (3) decide whether
  D4's "uncommitted in the working tree" wording still fits the new STATUS.md; (4) decide whether
  D5 appending to now-tracked files is acceptable.
* Scratch files: see `hy4-b4-handback-D1.md` § Scratch files; none added by this hand-back.
