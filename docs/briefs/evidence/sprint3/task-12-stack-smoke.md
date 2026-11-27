# Task 12 — Stack smoke: identity + orchestrator health on compose

- Date (UTC): 2026-11-24
- SHA at check: `1189d27e` (HEAD after Task 11)
- Status: **BLOCKED (Docker daemon unavailable)**

## Blocker

The compose stack cannot be brought up because the Docker daemon is not running.

```
$ docker info
Client: ... Version 29.5.3 ...
failed to connect to the docker API at unix:///Users/user/.docker/run/docker.sock;
check if the path is correct and if the daemon is running: dial unix /Users/user/.docker/run/docker.sock: connect: no such file or directory

$ docker compose -f deploy/docker-compose.yml ps
failed to connect to the docker API at unix:///Users/user/.docker/run/docker.sock ...
```

Port `5499` (compose `db`) is closed: `nc -z 127.0.0.1 5499` → not open.

Per the task rule ("If Docker daemon down → write task-12 BLOCKED; skip to Task 13
— do not fake curl"), no health endpoints were hit and no curl output is recorded
here. No responses are claimed.

## Dependency note

Task 11 verified migrations 0001→0081 on a real PostgreSQL 18.4 via the documented
caller-supplied-PG path (`HARNESS_TEST_PG_EXTERNAL=1`), not on compose `:5499`.
That satisfies the migration requirement but does not make the compose stack
available for this health smoke.

## Not done (blocked, skipped intentionally)

- Bring up minimal stack per docs (no `-v`).
- Hit `/health?deep=0` (or documented health) on identity + orchestrator ports.
- One authenticated read (dev OTP).

## Unblock condition

Start Docker Desktop / the daemon, then run the compose stack and repeat the two
health checks. Deferred to the stretch path; minimum Sprint 3 success does not
depend on this task.
