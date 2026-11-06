# hy4 hand-back — B3

Task: B3 — Drill hygiene and corpus entries
Status: DONE
Assumptions: `docs/eval/corpus.json`'s `notes` field is where the corpus
  documents a blocked task's unmet prerequisite (its top-level `notes` says
  exactly that), and `run_eval.py --check` is the structural gate; the two W27
  drills are live/paid and must not be run in this lane.

## Files changed

* `scripts/e2e/m07_daemon_drill.py` — mode `100644` → `100755` (EXE001).
* `scripts/e2e/w27_denial_drill.py` — mode `100644` → `100755` (EXE001); one
  line changed at `:39`, `not p.relative_to(root).parts[0] == "src"` →
  `p.relative_to(root).parts[0] != "src"` (SIM201). No other content changed.
* `docs/eval/corpus.json` — appended two entries, `w27-denial` and
  `w27-breaker`, modelled on the neighbouring `script` tasks (same 15 keys:
  `id`, `title`, `category`, `kind`, `cost`, `heldOut`, `status`, `driver`,
  `fixture`, `budget`, `model`, `verifier`, `expected`, `evidence`, `notes`).
  Both have `"status": "blocked"`, `"notes": "not yet run through the runner"`,
  `"evidence": "none yet; the runner has not executed this drill"`, and no
  `implemented` key. Driver paths point at the real files; `args` is empty so
  no hosted model invocation is encoded in the corpus.

**Neither drill was run.** `run_eval.py --check` is the only runner command used;
no `--include-live`, no model call, no spend, no evidence file produced.

## Commands run (verbatim results)

Baseline ruff (before the fix):
```
$ section4/.venv/bin/ruff check --no-cache scripts/e2e/m07_daemon_drill.py scripts/e2e/w27_denial_drill.py
EXE001 Shebang is present but file is not executable --> scripts/e2e/m07_daemon_drill.py:1:1
EXE001 Shebang is present but file is not executable --> scripts/e2e/w27_denial_drill.py:1:1
SIM201 Use `p.relative_to(root).parts[0] != "src"` instead of `not ... == "src"`
  --> scripts/e2e/w27_denial_drill.py:39:28
Found 3 errors.   RUFF_RC=1
```

After `chmod +x` on both and the one-line SIM201 edit:
```
$ section4/.venv/bin/ruff check --no-cache scripts/e2e/m07_daemon_drill.py scripts/e2e/w27_denial_drill.py
All checks passed!   RUFF_RC=0
```

```
$ python3 scripts/eval/run_eval.py --check
CHECK_RC=0
{"ok": true, "tasks": 41, "heldOut": 6, "implemented": 39}
```
Baseline before this task was `{"ok": true, "tasks": 39, "heldOut": 6,
"implemented": 39}` (captured by `make static-gates` during A3/A4). Task count
went up by exactly two; `implemented` stayed at 39, i.e. the new entries are
**not** counted as passing.

```
$ make static-gates
STATIC_GATES_RC=0
... orchestrator specs and UI calls match the router.
python3 scripts/eval/run_eval.py --check
{"ok": true, "tasks": 41, "heldOut": 6, "implemented": 39}
python3 scripts/check_env_knobs.py
env knob catalogue complete: 62 runtime reads, all in .env.example
```

```
$ python3 - <<'PY'   # shape check of the two new entries
task count: 41
{"id": "w27-denial", "status": "blocked", "notes": "not yet run through the runner", "driver": {"type": "script", "path": "scripts/e2e/w27_denial_drill.py", "args": [], "env": {}}, "cost": "live", "model": "anthropic/claude-haiku-5.5"}
  implemented key present? False
{"id": "w27-breaker", "status": "blocked", "notes": "not yet run through the runner", "driver": {"type": "script", "path": "scripts/e2e/w27_breaker_drill.py", "args": [], "env": {}}, "cost": "live", "model": "stack-configured live model"}
  implemented key present? False
```

Negative control (real): reverted the SIM201 line and cleared both executable
bits, then ruff reproduced **all three** original findings:
```
NEGATIVE_CONTROL_RUFF_RC=1
EXE001 Shebang is present but file is not executable
EXE001 Shebang is present but file is not executable
SIM201 Use `p.relative_to(root).parts[0] != "src"` instead of `not ... == "src"`
Found 3 errors.
```
Restored both:
```
$ section4/.venv/bin/ruff check --no-cache <both drills>
All checks passed!   RESTORED_RUFF_RC=0
$ git diff -- <both drills>
old mode 100644 / new mode 100755  (both files)
-        if p.is_file() and not p.relative_to(root).parts[0] == "src"
+        if p.is_file() and p.relative_to(root).parts[0] != "src"
$ git diff --stat docs/briefs/evidence/w21/native-confinement-container.json
(empty — unchanged)
```

## Proof the new tests fail without the change

B3 adds no tests. Its acceptance is structural, and the negative control above
is a real run: reverting the two fixes reproduces the exact three findings, so
the fixes are falsifiable. For the corpus, `run_eval.py --check` output moved
from `tasks: 39` to `tasks: 41` with `implemented` still 39 — the entries are
registered and explicitly not claimed as passing. `select()` in
`scripts/eval/run_eval.py:428-432` skips `blocked` tasks before any cost/model
selection, so registering them cannot trigger a live run.

## What is NOT verified

* Neither W27 drill was executed; no PASS/FAIL is claimed for either. The
  reviewer keeps those runs (and their cost).
* The `budget.maxCostUsd: 0.01` and timeout/tool-call numbers on the new
  entries are metadata copied from the drills' own docstrings ("about a cent",
  `--deadline 540`, five denied commands) — they were not measured, and they are
  inert while the tasks are blocked.
* `w27-breaker`'s `model` is the literal string `"stack-configured live model"`
  because the drill's docstring says "the stack model" and names no concrete
  model (`scripts/e2e/w27_breaker_drill.py:25`). `check_corpus` only requires a
  non-empty model for `cost: live`.

## Unrelated findings (one line each)

* `scripts/e2e/w27_breaker_drill.py` has no shebang, so it is not subject to EXE001 and was left alone.
* The corpus's top-level `notes` says blocked tasks record their unmet prerequisite in `notes`; that is why the one-line reason lives there rather than in a new `reason` key.

## Risks the reviewer should check first

* Confirm `notes: "not yet run through the runner"` is the one-line reason the reviewer expected, and flip `status` to `implemented` only after running each drill through the runner with the right `--image`/`--model`/`--base-url` (the entries deliberately carry empty `args`).
* Confirm `maxCostUsd` / `model` metadata before flipping: these are live, paid drills and the reviewer owns the spend decision.
* The two executable-bit changes are tracked mode changes; if the reviewer's checkout normalises file modes they will need re-applying.
