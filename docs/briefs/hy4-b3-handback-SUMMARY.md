# hy4 batch 3 hand-back — SUMMARY

Branch `w28-s1-delegate`, HEAD `f5b298a`. `git log --oneline -3` is unchanged
from the start of the batch (byte-for-byte diff rc=0), and the first line of the
batch 3 prompt does not say anything is committed: **all work is uncommitted in
the working tree, nothing is deployed.** No commit, push, reset, stash, branch
change or `git checkout`; no `docker run`, build, compose up/down, deploy,
rollback or volume change; no paid or hosted model call, no drill that starts a
model run, no secret read or set. `STATUS.md`, `PROJECT-STATE.md`, the W33
documents, `docs/yc/`, `deploy/.env` and the root `.env` were left alone. The
W21 evidence file is unchanged, so the one permitted restore was not needed.

Start-state `git status --short` (59 entries) and `git log --oneline -3` were
saved before any edit and are pasted verbatim in `hy4-b3-handback-C1.md`.

## Task table — nothing tidied out

| Task | Status | The one thing I am least sure of |
|---|---|---|
| C1 — `./src` vs `src`: reachability, then a narrow fix | **DONE (reachable, narrow fix)** | The reachability table was built by reading the writers and their validators, not by an HTTP request; a writer outside `section1`/`section4` could in principle store a scope path I did not find. The shipped `_covered` keeps the batch 2 answer and adds noise-cleaned comparisons; a first draft that normalised both sides was discarded after a 784-pair differential found it lost coverage for the `./` prefix and newly covered `/etc/passwd`. |
| C2 — shell gate: scope-bypass edge tests | **DONE (4 strict xfails, no production change)** | Whether `ls ..` counts as a fail-open: the gate deliberately skips paths outside the workspace because confinement stops writes there, but the human test is "touch a file outside `src`", which a read satisfies. Also, the tests stub execution, so they pin the decision layer and not the OS boundary. |
| C3 — say the denial-knob footgun out loud | **DONE (comment only)** | Whether the one-sentence wording is plain enough to stop an operator reading `0` as "stop at once"; the semantics themselves remain a reviewer ruling. |
| SUMMARY (this file) | DONE | — |

## What was delivered (one line each)

- **C1** — `agent_tree._covered` (+20 / −1) now treats a leading `./`, trailing `/` and doubled `/` as noise while keeping a leading `/`, `..`, internal `.` and symlinks significant; it keeps the batch 2 answer so no coverage is removed. Three strict xfails became plain passing tests, the trailing-slash characterisation became an asserted rule, and new tests cover noise equivalence, `..`, absolute paths, `src2`, empty strings and an old-vs-new differential. Reachability table with file:line in the C1 hand-back. Checks: 159 passed; `make lint` rc=0; `make static-gates` rc=0; negative control 8 failed / restored with `cmp` rc=0.
- **C2** — `section5/tests/unit/test_shell_gate_scope_edges.py`: 25 table-driven cases pinning the gate's current `denied`/`code`/`_scope_hit` for the listed command spellings, 3 `scope_violation` cross-checks, 1 `scope_roots` assertion, 4 strict xfails. Checks: `44 passed, 4 xfailed` (and `38 passed, 6 skipped, 4 xfailed` in the sandboxed run), `make lint` rc=0; negative control 18 failed / restored with `cmp` rc=0. `shell_gate.py` unchanged.
- **C3** — `.env.example`: one plain sentence under each denial knob stating that `0`, empty and `off` turn the stop off, a positive number is the limit and a non-number falls back to the default. `make static-gates` rc=0 (`62 runtime reads, all in .env.example`); `git diff -- .env.example` contains only comment lines.

## Security findings

Recorded only in this SUMMARY and in `hy4-b3-handback-C2.md`. The test file
carries only the required one-line xfail reason (exact command plus wrong
output), no analysis. **The gate was not fixed.** All four are
`xfail(strict=True)` and will flip to XPASS-and-fail when the gate starts
denying them. Wrong output in every case: `denied=False`, `code=None`,
`_scope_hit=None` — the command is allowed.

1. **`cat --file=docs/y.txt`** — `--flag=path` is skipped by `_scope_hit:73-77`, so `docs/y.txt` (outside `src`) is never checked.
2. **`cat $HOME/x`** — the env-var spelling is never expanded; the literal token neither exists nor has an existing parent, so the scope check skips it.
3. **`cat ~/x`** — tilde is not expanded; skipped by the same branch as finding 2.
4. **`ls ..`** — `..` resolves outside the workspace root, and `_scope_hit:80-81` deliberately skips it (confinement stops writes there, not reads).

Fail-closed notes, **not** findings: the empty command is refused by
`policy.validate_command:319-320`; redirection/allow-list/chaining cases are
refused before the scope layer; `cat /etc/passwd` is refused by
`BLOCKED_PATTERNS`.

## Every `NEEDS-REVIEWER-RUN` item

1. **C1 literal check on a normal host**: `cd "/Users/user/untitled folder 2/harness/section4" && .venv/bin/python -m pytest tests/unit/test_delegation.py tests/unit/test_agent_tree.py tests/unit/test_loop_guard.py -q` — expected `159 passed`, rc=0. Here it returns rc=1 with 61 `FileExistsError` fixture errors on the shared pytest temp root; the sanctioned `--basetemp="$(mktemp -d /private/tmp/hy4.XXXX)"` form is rc=0.
2. **C2 literal check on a normal host**: `cd section5 && .venv/bin/python -m pytest tests/unit/test_shell_gate.py tests/unit/test_shell_gate_scope_edges.py -q` — expected rc=0 with 4 xfailed. Here it was run with `--basetemp` only; two runs gave `38 passed, 6 skipped, 4 xfailed` and `44 passed, 4 xfailed` depending on whether the sandbox exposed Seatbelt/Landlock.
3. **C2 live/OS-boundary check of the four fail-opens** against a deployed worker — not run (no deploy, no `docker run`); the tests stub execution deliberately. The decision to change `shell_gate.py` is the reviewer's.
4. **C1 live/HTTP exercise of the reachability table** — not run; no live stack, no delegated child, no intent PUT. Reachability is established by reading the writers and validators.
5. **Full section4 suite / `make test`** — not run (it rewrites the tracked W21 evidence file); regression outside the three named test files is not excluded here. `NEEDS-REVIEWER-RUN` if the reviewer wants the whole suite.
6. **Your own `make lint`** — I ran it three times (C1, C1 final, C2), all rc=0; a reviewer on another host may want their own run, especially because plain `make lint` worked here while earlier batches sometimes needed `RUFF_NO_CACHE=true`.

## Every file created or changed that is not named in a task

In the repository:

- `docs/briefs/hy4-b3-handback-C1.md` (new) — required by the hand-back format; no task names it.
- `docs/briefs/hy4-b3-handback-C2.md` (new) — required by the hand-back format; no task names it.
- `docs/briefs/hy4-b3-handback-C3.md` (new) — required by the hand-back format; no task names it.
- `docs/briefs/hy4-b3-handback-SUMMARY.md` (new, this file) — explicitly named by FINISH.
- `section5/tests/unit/test_shell_gate_scope_edges.py` (new) — C2 says "add table-driven tests" and "plus the new file" but names no path; listed for completeness.

Task-named files (for completeness): `section4/src/harness_orchestrator/agent_tree.py` (C1 fix), `section4/tests/unit/test_agent_tree.py`, `section4/tests/unit/test_delegation.py` (C1 tests), `.env.example` (C3 comment block; it was **already** modified in the baseline by batch 1 A4, so C3 added no new `git status` entry). `section5/src/harness_sandbox/shell_gate.py` was temporarily mutated for the C2 negative control and restored byte-for-byte (`cmp` rc=0, `git status --short` empty): it is not a changed file.

Final `git status --short` delta against the batch 3 baseline (taken just before
this SUMMARY was written): exactly five entries — one new modified file
(`agent_tree.py`) and four new untracked files (`hy4-b3-handback-C1.md`,
`-C2.md`, `-C3.md`, `test_shell_gate_scope_edges.py`). With this SUMMARY it is
six. No change in a file I did not touch; `git log --oneline -3` unchanged.

## `/private/tmp/hy4-*` files left (reviewer will remove the earlier ones)

Created by this batch, all left in place:

- `/private/tmp/hy4-b3/` — `git-status-baseline.txt`, `git-log-baseline.txt`, `git-status-final.txt`, `agent_tree.mine` (the discarded first implementation, kept as history), `agent_tree.final.mine`, `shell_gate.mine`, `make-lint-c1.txt`, `static-gates-c1.txt`, `make-lint-c1-final.txt`, `static-gates-c1-final.txt`, `make-lint-c2.txt`, `static-gates-c3.txt`.
- Eleven `mktemp -d /private/tmp/hy4.XXXX` pytest basetemp directories from this batch: `hy4.yBKf`, `hy4.l5Wt`, `hy4.3BhA`, `hy4.vAEw`, `hy4.bKBm`, `hy4.dsBy`, `hy4.FpwL`, `hy4.RmF1`, `hy4.319j`, `hy4.vzDF`, `hy4.pjee`.
- One probe workspace outside `/private/tmp`: `/var/folders/6s/6cr386jx5nz7y95nkjxy8t300000gn/T/hy4-b3-probe-f47vu34b` (created by Python `tempfile.mkdtemp(prefix="hy4-b3-probe-")` for the 25-command C2 probe), left in place.

Earlier batches' directories (`hy4-b2-a1`, `hy4-b2-a2`, `hy4-b2-a3`,
`hy4-b2-b2`, `hy4-b2c`, `hy4-ruff-cache` and the older `hy4.*` basetemps) were
not touched.

## Guardrails observed

$0 model spend; no paid/hosted API, no `--include-live`, no drill that starts a
model run. No `docker run`, `docker build`, `docker compose up/down/restart` or
any change to the running stack; `make static-gates` ran only the read-only
`docker compose -f deploy/docker-compose.yml config -q`. No git write of any
kind, including the permitted W21 `git checkout`. No `OPENROUTER_API_KEY`,
`DEEPSEEK_API_KEY`, `~/.harness-secrets/`, `deploy/.env` or root `.env` read.
No person decisions taken: B1's delegated-scope recommendation, the
`permissions` note, the `process.reallyExit` question, the denial-knob
semantics, the `stack_tags` exit code, and the decision to change
`shell_gate.py` all remain the reviewer's.

## For the reviewer, in order

1. Rule on the four C2 fail-opens and whether `shell_gate.py` should change; the strict xfails will fail loudly when it does.
2. Rule on the B1 delegated-scope recommendation (still a product decision, not built here).
3. Rule on the denial-knob `0`/empty/`off` semantics; C3 only documented them.
4. Check C1's reachability table and the shipped union form of `_covered` (including the discarded first draft) before committing.
5. Re-run the two literal `pytest` commands and your own `make lint` on a normal host (items 1, 2 and 6 above).
6. Decide whether the `ls ..` finding is in scope — it is the judgement call most open to disagreement.
