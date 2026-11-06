# hy4 hand-back — B2

Shorter alternative: **yes, marginally** — the report could be one shell alias
(`docker ps --format …`), but that cannot satisfy the required canned-output unit
tests (all agree / one differs / service missing), cannot name a missing
service, and cannot state agreement explicitly. The pure `parse_ps` /
`image_tag` / `stack_report` split is what makes those tests Docker-free. The
`EXPECTED_BACKEND_SERVICES` tuple is the only "extra": without a fixed expected
set, "a service missing" is not observable.

Task: B2 — A read-only stack tag report
Status: DONE
Assumptions: `deploy/docker-compose.yml` service names are authoritative; the
  live daemon image tag is visible from `docker ps`; "backend services" means
  the long-running services that inherit `<<: *backend-build`
  (`image: harness-backend:v${HARNESS_IMAGE_TAG}`), with `worker-daemon` called
  out separately because it is a different image.

## Files changed

* NEW `scripts/ops/stack_tags.py` (executable) — the only external command is
  `docker ps --format '{{.Label "com.docker.compose.service"}}|{{.Names}}|{{.Image}}'`.
  Pure `parse_ps`, `image_tag`, `stack_report`; `main()` prints and always
  returns 0. No `docker compose`, `docker inspect`, `docker run`, build, restart,
  stop, rm, or volume operation anywhere in the file.
* NEW `scripts/ops/test_stack_tags.py` — 7 tests fed canned `docker ps` text;
  no Docker daemon is contacted (including in the `main()` test, which stubs
  `subprocess.run`).

The expected backend set was taken from `deploy/docker-compose.yml:158` onwards
(`<<: *backend-build` at `:15`), excluding one-shot `migrate`/`backend` and the
`*-loadtest` profiles; the 11 names are the ones actually running.

## Commands run (verbatim results)

```
$ cd "/Users/user/untitled folder 2/harness" && rm -rf /tmp/b2-bt && section4/.venv/bin/python -m pytest scripts/ops/test_stack_tags.py -q --basetemp=/tmp/b2-bt
B2_TEST_RC=0
7 passed in 0.14s
```

First root-ruff run found two issues in MY new files (fixed, not left behind):
```
$ section4/.venv/bin/ruff check --no-cache scripts/ops/stack_tags.py scripts/ops/test_stack_tags.py
EXE001 Shebang is present but file is not executable --> scripts/ops/stack_tags.py:1:1
I001 [*] Import block is un-sorted or un-formatted --> scripts/ops/test_stack_tags.py:2:1
```
`chmod +x scripts/ops/stack_tags.py` + formatting the import block:
```
$ section4/.venv/bin/ruff check --no-cache scripts/ops/stack_tags.py scripts/ops/test_stack_tags.py
All checks passed!   RUFF_RC=0
```

```
$ RUFF_NO_CACHE=true make lint
MAKE_LINT_RC=0   (all sections "All checks passed!")
```

One real read-only run against the live stack (`REAL_RUN_RC=0`), full output:

```
Stack tag report (read-only: docker ps)

Running services:
  alertmanager           prom/alertmanager:v0.28.1                tag=v0.28.1  (harness-alertmanager-1)
  db                     harness-db-walg:v3.0.9                   tag=v3.0.9  (harness-db-1)
  events-retention       harness-backend:vw28s6                   tag=vw28s6  (harness-events-retention-1)
  frontend               harness-frontend:vw24b2                  tag=vw24b2  (harness-frontend-1)
  github                 harness-backend:vw28s6                   tag=vw28s6  (harness-github-1)
  harness-ci-scratch-pg  pgvector/pgvector:pg18                   tag=pg18  (harness-ci-scratch-pg)
  identity               harness-backend:vw28s6                   tag=vw28s6  (harness-identity-1)
  meeting                harness-backend:vw28s6                   tag=vw28s6  (harness-meeting-1)
  models                 harness-backend:vw28s6                   tag=vw28s6  (harness-models-1)
  orchestrator           harness-backend:vw27s3                   tag=vw27s3  (harness-orchestrator-1)
  prometheus             prom/prometheus:v2.53.0                  tag=v2.53.0  (harness-prometheus-1)
  review                 harness-backend:vw28s6                   tag=vw28s6  (harness-review-1)
  sandbox                harness-backend:vw28s6                   tag=vw28s6  (harness-sandbox-1)
  sandbox-egress         harness-backend:vw28s6                   tag=vw28s6  (harness-sandbox-egress-1)
  siem-push              harness-backend:vw28s6                   tag=vw28s6  (harness-siem-push-1)
  sso-idp                quay.io/keycloak/keycloak:26.1           tag=26.1  (harness-sso-idp-1)
  webhook-delivery       harness-backend:vw28s6                   tag=vw28s6  (harness-webhook-delivery-1)
  worker-daemon          harness-worker-daemon:vw27s2             tag=vw27s2  (harness-worker-daemon-1)

Backend services: 11 of 11 expected running
Missing: none
Agreement: MIXED
  vw28s6       10 service(s): events-retention, github, identity, meeting, models, review, sandbox, sandbox-egress, siem-push, webhook-delivery
  vw27s3        1 service(s): orchestrator
Worker daemon: harness-worker-daemon:vw27s2 (tag=vw27s2); separate image, not part of the backend group above.
Read-only: no container, image, volume or compose state was changed.
```

Negative control (real): temporarily made `image_tag` always return `vw28s6`,
then:
```
$ section4/.venv/bin/python -m pytest scripts/ops/test_stack_tags.py -q --basetemp=/tmp/b2-negctl-bt
NEGATIVE_CONTROL_RC=1
FAILED ...::test_image_tag_handles_registry_ports_digests_and_untagged_refs
FAILED ...::test_one_service_on_a_different_tag_is_reported_as_mixed
FAILED ...::test_main_exits_zero_and_prints_the_mix_without_docker
3 failed, 4 passed in 0.23s
```
Restored the two lines; `RESTORED_TEST_RC=0  (7 passed)`, `RUFF_RC=0`, and a
second real run again printed `Agreement: MIXED` with `REAL_RUN2_RC=0`.

## Proof the new tests fail without the change

The negative control above is a real run: 3 tests fail when the report stops
reading real image tags. Additionally, without the change the module
`stack_tags` does not exist, so the whole test file cannot be collected.

## What is NOT verified

* The "all agree" and "missing" cases were exercised only with canned output;
  the live stack is mixed, so those two branches were not observed against real
  Docker (by design — this task must not change the stack).
* `docker inspect` was not used (the task allows it but `docker ps` labels were
  sufficient). No env value or secret was read.
* No redeploy was performed; the report says nothing about how to redeploy
  beyond showing the mix (that is B4's runbook).

## Unrelated findings (one line each)

* `harness-ci-scratch-pg` has no `com.docker.compose.service` label, so the report keys it by container name; harmless but worth knowing when reading the output.
* Root-ruff flags `EXE001` on any new script with a shebang until it is `chmod +x` — the same class of finding B3 fixes in two older drills.

## Risks the reviewer should check first

* `EXPECTED_BACKEND_SERVICES` is a hand-written snapshot of the running backend set; if compose adds/removes a long-running backend service, the "Missing:" line drifts. Consider deriving it from compose in a later task.
* The script exits 0 even when `docker ps` fails (it prints `Agreement: UNKNOWN`). That follows "must exit 0", but means a monitoring wrapper cannot use the exit code to detect a broken daemon.
* `image_tag` reports `<digest>` for digest-pinned images; a reviewer may prefer the short digest.
