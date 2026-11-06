# Runbook: redeploying the worker daemon and the orchestrator

Written 2026-11-05 by an engineer who **did not run any command in this file**.
Every fact below is traceable to one of the two last STATUS entries —
`STATUS.md:972` ("W27 slice 2 deployed") and `STATUS.md:979` ("W27 slice 3
deployed") — or to a file named in the text. Nothing is inferred from a
successful deploy that STATUS did not report.

**Source of truth:** [`STATUS.md`](../STATUS.md):972, :979.
**Related:** [`rollback.md`](rollback.md) §1 (tag scheme),
[`ops.md`](ops.md) (bring-up env), `deploy/docker-compose.yml:4-15`,
`.env.example:8-15`, `scripts/ops/stack_tags.py` (read-only tag report),
`scripts/eval/run_eval.py:466` (`--verify-only`).

This runbook contains no SLO, on-call, pricing, security-review or credential
decisions. The owner keeps those.

---

## 0. Why this runbook exists: two failed attempts

* **Daemon, first recreate crash-looped** with `HARNESS_WORKER_DAEMON_TOKEN is
  required`. Reason: compose was run against `deploy/docker-compose.yml`, which
  reads `deploy/.env`, **not** the root `.env` that holds the token. The fix is
  `--env-file .env`; the daemon then came up and listened on 8010.
  — `STATUS.md:972`
* **Orchestrator needed both env files.** `deploy/.env` carries the rate card and
  the WAL prefix; the root `.env` carries the daemon token. STATUS says either
  file alone "would have changed the orchestrator's config". — `STATUS.md:979`
* Both deploys note **"No engine worker was running"** at the time
  (`STATUS.md:972`, `:979`). `STATUS.md:965` records that a run mid-flight on
  the old daemon would have been interrupted (none was running that was
  checked).

## 1. Compose env files — which ones, and why

| Service | Env-file flags | Why | Source |
|---|---|---|---|
| `orchestrator` | `--env-file deploy/.env --env-file .env` (both) | `deploy/.env` = rate card + WAL prefix; root `.env` = daemon token. Either alone changes the orchestrator's config. | `STATUS.md:979` |
| `worker-daemon` | `--env-file .env` (root only) | The root `.env` holds `HARNESS_WORKER_DAEMON_TOKEN`; using `deploy/.env`'s defaults instead produced the crash loop. | `STATUS.md:972` |

`.env.example:8-15` documents the repo-root invocation form
`docker compose --env-file .env -f deploy/docker-compose.yml up -d` and warns
that some Compose versions do not load the root `.env` automatically.
`STATUS.md:972` says plainly: "Anyone redeploying the daemon must pass the root
`.env`."

## 2. The check that the resolved environment equals the running container's

Before recreating the orchestrator, STATUS says the operator checked **that the
two env files resolve the orchestrator's 51 environment keys identically to the
running container — 0 differences** (`STATUS.md:979`). That is the acceptance
criterion: 51 keys, zero differences. Do not skip it: it is what caught the
"either file alone changes the config" problem.

`STATUS.md:979` does **not** record the exact shell command used for that
comparison, so this runbook does not assert one. The two read-only building
blocks named elsewhere in this repo are:

* `docker compose -f deploy/docker-compose.yml config` — the `config` form used
  by the `static-gates` target in the root `Makefile` (with `-q`).
* `docker inspect <container> --format '{{range .Config.Env}}{{println .}}{{end}}'`
  — the read-only env read used in `scripts/e2e/m01_native_edit_drill.py` and
  `scripts/e2e/w27_denial_drill.py:43-53`.

**Tested in this repo: NO** (as a combined equality check — STATUS records the
result and the criterion, not a command).

## 3. Tag rules

* Compose pins an explicit tag per build; no floating `:latest` in the app tier.
  `<TAG>` = `YYYYMMDD-HHMM` UTC, set once per deploy via `HARNESS_IMAGE_TAG`
  in `deploy/.env` or the shell env; a `latest` alias is emitted for convenience
  but compose never resolves through it. **A rollback needs the previous tag to
  still exist by name.** — `docs/runbooks/rollback.md` §1 and
  `deploy/docker-compose.yml:4-15`.
* `HARNESS_IMAGE_TAG` drives both `harness-backend:v<TAG>` and the worker images
  (`deploy/docker-compose.yml:15`, `:469`, `:492`, `:645`).
* Tags at the two deploys, from `STATUS.md:972` and `:979`:

  | Component | Tag |
  |---|---|
  | `worker-daemon` and its `HARNESS_ENGINE_IMAGE` | `vw27s2` |
  | `orchestrator` | `vw27s3` (slice 3 built `harness-backend:vw27s3`) |
  | Other backend services | `vw28s6` |
  | Frontend | `vw24b2` |

  The backend tag is therefore **mixed** by design at that moment: "the other
  backend services stay on `vw28s6` … nothing they share changed"
  (`STATUS.md:979`). The worker image tag being out of step with the backend tag
  is explicitly noted as an existing pattern in `STATUS.md:965`.
* **Recreate only the service the slice changed**: `worker-daemon` for slice 2
  (`STATUS.md:972`), `orchestrator` for slice 3 (`STATUS.md:979`).
* `scripts/ops/stack_tags.py` prints the current mix read-only and exits 0; use
  it before and after a redeploy to see whether agreement was restored.

## 4. "No worker running" check

Both deploys state no engine worker was running (`STATUS.md:972`, `:979`), and
`STATUS.md:965` says a mid-flight run on the old daemon would have been
interrupted. Confirm this with a **read-only** `docker ps` before recreating:
`docker ps --format '{{.Names}}\t{{.Image}}'` is the read-only call behind
`scripts/ops/stack_tags.py`. If any engine worker container is listed, stop and
get an operator decision — do not recreate underneath a live run.

**Tested in this repo: NO** as a formal pre-deploy gate; STATUS records the
observation, not a gate command.

## 5. Deploy and rollback (reconstructed argv — read the labels)

> STATUS records the **parameters** of these recreates, not their literal
> argv. The lines below assemble those parameters with the invocation form
> documented in `.env.example:14`. Substitute the tag you actually built; never
> paste a tag from this file into a live deploy without checking §3.

**Daemon deploy — TESTED IN THIS REPO: YES** (`STATUS.md:972`: built
`harness-engine-worker:vw27s2` / `harness-worker-daemon:vw27s2` with
`HARNESS_IMAGE_TAG=w27s2`, recreated only `worker-daemon`, daemon came up on
8010):

```
HARNESS_IMAGE_TAG=w27s2 docker compose --env-file .env -f deploy/docker-compose.yml up -d worker-daemon
```

**Daemon first attempt — FAILED, do not repeat** (`STATUS.md:972`: omitted the
root `.env`, crash-looped on `HARNESS_WORKER_DAEMON_TOKEN is required`): compose
against `deploy/docker-compose.yml` with only its default `deploy/.env`.

**Orchestrator deploy — TESTED IN THIS REPO: YES** (`STATUS.md:979`: built
`harness-backend:vw27s3`, recreated only `orchestrator`, both env files, healthy):

```
HARNESS_IMAGE_TAG=vw27s3 docker compose --env-file deploy/.env --env-file .env -f deploy/docker-compose.yml up -d orchestrator
```

**Daemon rollback — TESTED IN THIS REPO: NO** (STATUS gives the parameters, not
a performed rollback). `STATUS.md:972`: "recreate `worker-daemon` with
`HARNESS_IMAGE_TAG=w27` and `--env-file .env`":

```
HARNESS_IMAGE_TAG=w27 docker compose --env-file .env -f deploy/docker-compose.yml up -d worker-daemon
```

**Orchestrator rollback — TESTED IN THIS REPO: NO** (`STATUS.md:979`: "recreate
`orchestrator` with `HARNESS_IMAGE_TAG=w28s6` and the same two `--env-file`
flags"):

```
HARNESS_IMAGE_TAG=w28s6 docker compose --env-file deploy/.env --env-file .env -f deploy/docker-compose.yml up -d orchestrator
```

Both rollbacks depend on the previous image tag still existing by name
(`docs/runbooks/rollback.md` §1). `docs/runbooks/rollback.md` §0 is the decision
layer for whether an image-only rollback is safe; this file does not replace it.

## 6. Verification set

| Check | Result STATUS reports | Tested in this repo? |
|---|---|---|
| `python3 scripts/eval/run_eval.py --verify-only` | `verified: true` after both deploys (`STATUS.md:972`, `:979`); the flag exists at `scripts/eval/run_eval.py:466` | **YES** (STATUS) |
| Health | Orchestrator "healthy" (`STATUS.md:979`); daemon "came up and listens on 8010" (`STATUS.md:972`) | **YES** (STATUS observation; STATUS names no health command) |
| Drill with the right `--image` | `m07_daemon_drill.py --image harness-engine-worker:vw27s2` PASS on Haiku: 2 workers, worker user 999:999, read-only rootfs, `calc.py` fixed, README unchanged, no Docker socket in the orchestrator, 14 s (`STATUS.md:972`) | **YES** (STATUS) |
| Denial drill on the new image | `w27_denial_drill.py` on the new image: denial with reason + add-to-scope alternative, marker not leaked, workspace unchanged, 9.3 s (`STATUS.md:972`) | **YES** (STATUS) |
| Breaker drill through the API | `scripts/e2e/w27_breaker_drill.py` through the HTTP API on the stack model: `engine_failed` reason `denials_repeated after 3 tool calls`, fixture untouched, PASS, evidence `pilot-prep/w27-breaker.json` (`STATUS.md:979`) | **YES** (STATUS; this drill is live/paid and is the reviewer's to run) |

**Pass `--image` explicitly.** `STATUS.md:972` says the drills' default
`--image` "still names `vw27`, so they report no workers unless given the new
image" — but the later `STATUS.md:977` says those defaults were fixed to read
the running daemon's `HARNESS_ENGINE_IMAGE`, with the old tag as fallback. The
code agrees with the later entry: `scripts/e2e/m07_daemon_drill.py:111` and
`scripts/e2e/w27_denial_drill.py:60` both default to `daemon_engine_image(...)`.
**The code wins** — the old warning is stale; passing `--image` explicitly is
still the safest way to prove you drilled the image you just deployed.

**What STATUS says was NOT verified:** the breaker's 3-in-a-row behaviour live
(the slice-2 denial drill makes only one denial; the no-model test covers it,
`STATUS.md:972`); `scopeRequest` live (the slice-3 drill uses refused commands,
not scope misses, `STATUS.md:979`); and any UI display of scope requests or the
two costs (`STATUS.md:979`).

## 7. Scope of this document

No command here was run while writing this runbook; no image was built, no
container was recreated, no volume was touched, no model was called and no
money was spent. Commands labelled "TESTED IN THIS REPO: YES" are tested
**according to STATUS only**, not by this author. Commands labelled "NO" are
reconstructions or parameters that STATUS records without a performed run —
verify them on the target host before use.
