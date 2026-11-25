# Sprint 5 start revision

- Branch: `w28-s2-sprints1-4` (PR #27, origin).
- Start HEAD: `17015624` — "docs: PR 27 handoff SHA and STATUS history after master merge."
  Parent `35f17626` is the master merge after PR 25/26 (owner reported `make gate` GREEN there on Mac).
- Timestamp (UTC): 2026-11-25T03:18:47Z.
- Executor: cloud thread A (Claude Code, remote container). Scope: Tasks 0, 31, 32, 37, 38 only.
- Docker daemon at pre-flight: **down** — `docker info` → `failed to connect to the docker API at
  unix:///var/run/docker.sock ... no such file or directory`. The CLI is installed; no daemon, no
  compose stack. Consequences:
  - Tasks 33–36 (live Playwright on the compose stack) are **owner Mac** work; not attempted here.
  - Task 38 records Docker-dependent groups as **BLOCKED (env)**, never PASS.
- Fresh clone: no `.venv` anywhere; venvs provisioned per section with `make bootstrap`'s own recipe
  (python 3.13, `-e ../contracts` then `-e ".[dev]"`) as each task needs them.
- `checkpoint1-redesign` is on master via PR 26:
  `docs/briefs/evidence/90day/checkpoint1-redesign-2026-11-21.md` (input for thread C, not this thread).
- Out of scope / not touched: owner gates and signatures, payment, global P-D6, P-D7,
  scheduler auto-execute, merging PR 27, deploys.
- Concurrency note: a parallel docs thread may push to this same branch; this thread rebases onto
  `origin/w28-s2-sprints1-4` before every push.
