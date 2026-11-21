# hy4 hand-back — B4

Task: B4 — Runbook: redeploying the worker daemon and the orchestrator
Status: DONE
Assumptions: `STATUS.md:972` and `:979` are the "last STATUS.md entries" the
  task means; `test_program_docs.py` may require new runbook files to be
  registered; no command in the runbook may be executed by me.

## Files changed

* NEW `docs/runbooks/redeploy-worker-and-orchestrator.md` — the only file
  changed by B4. No code, no compose, no corpus, no evidence.

Contents, each item traced:

| Requirement | Where it is in the runbook | Traceable to |
|---|---|---|
| Orchestrator flags `--env-file deploy/.env --env-file .env`, and why | §1 table, §5 | `STATUS.md:979` (rate card + WAL prefix in `deploy/.env`; daemon token in root `.env`; either alone changes the config) |
| Daemon flags `--env-file .env`, and why | §1 table, §0, §5 | `STATUS.md:972` (crash loop `HARNESS_WORKER_DAEMON_TOKEN is required`; "Anyone redeploying the daemon must pass the root `.env`") |
| Resolved environment equals the running container's | §2 | `STATUS.md:979` (51 environment keys, 0 differences); the two read-only building blocks are named from the root `Makefile` (`docker compose ... config`) and `scripts/e2e/m01_native_edit_drill.py` / `scripts/e2e/w27_denial_drill.py:43-53` (`docker inspect ... Env`) |
| Tag rules | §3 | `docs/runbooks/rollback.md` §1, `deploy/docker-compose.yml:4-15`, `:469`, `:492`, `:645`; the actual tags from `STATUS.md:972`, `:979`, `:965` |
| Rollback | §5 | `STATUS.md:972` (`HARNESS_IMAGE_TAG=w27`, `--env-file .env`), `STATUS.md:979` (`HARNESS_IMAGE_TAG=w28s6`, both env files) |
| "No worker running" check | §4 | `STATUS.md:972`, `:979` ("No engine worker was running"), `:965` (a mid-flight run would be interrupted); read-only `docker ps` from `scripts/ops/stack_tags.py` |
| Verification set | §6 | `STATUS.md:972`, `:979` (`run_eval.py --verify-only` `verified: true`; orchestrator healthy / daemon on 8010; `m07_daemon_drill.py --image harness-engine-worker:vw27s2` PASS; `w27_denial_drill.py`; `w27_breaker_drill.py`); `scripts/eval/run_eval.py:466` confirms `--verify-only` exists |

Every command in §5 carries an explicit **TESTED IN THIS REPO: YES / NO**
label based on STATUS alone. Deploys are YES (STATUS reports them performed);
the failed first attempt is marked FAILED; both rollbacks and the §2 equality
check are NO because STATUS records parameters/results, not a performed
command. §5 says plainly that STATUS records parameters but not literal argv,
and that the displayed lines are a reconstruction using `.env.example:14`'s
documented invocation.

**Code wins, recorded:** `STATUS.md:972` says the drills' `--image` defaults
still name `vw27`, but `STATUS.md:977` says they were fixed to read the running
daemon's `HARNESS_ENGINE_IMAGE`. The code agrees with :977
(`scripts/e2e/m07_daemon_drill.py:111`,
`scripts/e2e/w27_denial_drill.py:60` both call `daemon_engine_image(...)`).
The runbook calls the older warning stale and still recommends passing
`--image` explicitly.

## Commands run (verbatim results)

The ONLY command run for B4 is the required check:

```
$ section4/.venv/bin/python -m pytest section4/tests/unit/test_program_docs.py -q
B4_TEST_RC=0
22 passed in 12.82s
```

```
$ ls -l docs/runbooks/redeploy-worker-and-orchestrator.md
-rw-r--r--  1 user staff  10452 2026-11-05 19:40 docs/runbooks/redeploy-worker-and-orchestrator.md

$ git diff --stat docs/briefs/evidence/w21/native-confinement-container.json
(empty — unchanged)

$ grep -n '\[[^]]*\]([^)]*)' docs/briefs/hy4-handback-*.md
(no matches — no hand-back introduces a markdown link the docs test would check)
```

`test_program_docs.py` required **no registration**: its link check scans
`docs/briefs/**/*.md` only, and its artifact checks name three fixed briefs. A
new `docs/runbooks/` file is not in its scope; the 22 tests pass unchanged.

**No command from the runbook was executed.** No `docker compose`, `docker
build`, `docker run`, `up -d`, rollback, `--verify-only`, health probe or drill
was run for B4. No image was built, no container recreated, no volume touched,
no model called, no money spent. (`docker ps`/`docker inspect` read-only calls
in earlier tasks were A1/A2/B2 work, not this runbook's commands.)

## Proof the new tests fail without the change

Not applicable — B4 is a document, no code changed and no new tests were
written. `test_program_docs.py` is a link/matrix/short-brief gate and passed
before and after. The runbook's own falsifiability is its citation table above;
every line number was verified by reading the file (`STATUS.md` entries re-read
verbatim at :958-983, `rollback.md` §1, `deploy/docker-compose.yml:4-15`,
`run_eval.py:466`, the two drills' `--image` defaults).

## What is NOT verified

* The reconstructed argv in §5 has not been run and is not claimed to be the
  exact command STATUS used.
* The §2 env-equality check's shell command is deliberately **not** given,
  because STATUS does not record one and the task forbids inventing facts.
* Rollbacks are untested; the "health" row records STATUS's observation, not a
  health-endpoint command.
* No live drill was run (the reviewer keeps all live/paid verification).

## Unrelated findings (one line each)

* `STATUS.md:972` and `:977` contradict each other about the drills' `--image` defaults; the code agrees with :977 — resolved in the runbook, not hidden.
* `STATUS.md:965` notes the worker image tag being out of step with the backend tag is an existing pattern, not a new fault.

## Risks the reviewer should check first

* Confirm the reconstructed §5 commands against the actual deploy shell history before anyone pastes them; the flags and tags are from STATUS, the `up -d <service>` shape is from `.env.example:14`.
* The mixed-tag table is a 2026-11-05 snapshot; `scripts/ops/stack_tags.py` (B2) is the live authority.
* The runbook deliberately contains no credential values; `deploy/.env` and the root `.env` are named but were not read.
* `--verify-only` is a real flag (`scripts/eval/run_eval.py:466`) but the runbook does not claim this author ran it.
