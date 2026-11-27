#!/usr/bin/env python3
"""OPS-20: the compose-stack end-to-end journey (zero provider keys).

Runs against the RUNNING deployment (`docker compose -f deploy/docker-compose.yml
up -d`), exactly as CI boots it (workflow: .github/workflows/e2e.yml). Covers
the deterministic product path with recorded truth — no live provider is
touched, and nothing here claims one:

  0.    stack shape: every container of the compose project is Running, and
       healthy wherever a healthcheck exists — a sidecar the product path never
       calls still counts (sandbox-egress crash-looped through 708 failed
       healthchecks while every check below passed).
  1-2. readiness of identity/orchestrator/sandbox/frontend (identity /ready
       also gates "migrations applied", so a half-migrated stack fails here);
  3-5. passwordless login for user A (request-login → OTP read from the
       identity container logs — the documented dev delivery — → verify-otp;
       fresh account gets a solo team with the admin role);
  6-7. POST /orchestrator/demo/seed (M19-N4 recorded replay): 201, replay
       marker true, terminal state, and IDEMPOTENT on re-seed;
  8-9. run aggregate + engine result report replay / no live-provider claim;
  10.  event replay for the seeded project: rows carry the demo replay marker,
       seq strictly increasing, enough distinct types to be a real lifecycle;
  11.  admin audit page answers for the team;
  12.  sandbox task lifecycle: create → exec (echo) → destroy → post-destroy
       exec must 404 (no phantom tasks);
  13.  cross-tenant no-oracle law: user B reading A's project/run gets the
       identical 404 shape as a nonexistent id;
  14.  metrics endpoint exposes harness_* families abroad;
  15.  frontend answers 200 with HTML.

Exit 0 = every check passed. Any failure prints the exact step, request and
response evidence and exits 1. Stdlib-only on purpose: the CI runner needs no
extra provisioning, so the journey can never fail for environment reasons
other than the stack itself.

Env overrides (all optional):
  E2E_OTP_CODE        skip log scraping (fixed-code dev setups)
  E2E_COMPOSE_FILE    base file, default <repo>/deploy/docker-compose.yml
  E2E_COMPOSE_FILES   additional comma-separated override files
  E2E_COMPOSE_PROJECT Compose project to inspect; omit for the file/default project
  E2E_COMPOSE_ENV_FILE env file for Compose interpolation, default <repo>/.env if present
  E2E_WAIT_SECONDS    readiness deadline, default 180
  E2E_IDENTITY_URL / E2E_ORCHESTRATOR_URL / E2E_SANDBOX_URL / E2E_FRONTEND_URL
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = os.environ.get(
    "E2E_COMPOSE_FILE", str(ROOT / "deploy" / "docker-compose.yml")
)
COMPOSE_FILES = [COMPOSE_FILE] + [
    path.strip() for path in os.environ.get("E2E_COMPOSE_FILES", "").split(",") if path.strip()
]
COMPOSE_PROJECT = os.environ.get("E2E_COMPOSE_PROJECT")
_env_file = os.environ.get("E2E_COMPOSE_ENV_FILE")
COMPOSE_ENV_FILE = Path(_env_file) if _env_file else ROOT / ".env"


def compose_command(*args: str) -> list[str]:
    command = ["docker", "compose"]
    if COMPOSE_PROJECT:
        command.extend(["-p", COMPOSE_PROJECT])
    if COMPOSE_ENV_FILE.is_file():
        command.extend(["--env-file", str(COMPOSE_ENV_FILE)])
    for path in COMPOSE_FILES:
        command.extend(["-f", path])
    return command + list(args)


IDENTITY = os.environ.get("E2E_IDENTITY_URL", "http://127.0.0.1:8001").rstrip("/")
ORCHESTRATOR = os.environ.get("E2E_ORCHESTRATOR_URL", "http://127.0.0.1:8004").rstrip("/")
SANDBOX = os.environ.get("E2E_SANDBOX_URL", "http://127.0.0.1:8005").rstrip("/")


def _frontend_port() -> str:
    """The port compose publishes the UI on: HARNESS_FRONTEND_PORT from the environment, else the
    port compose itself reports for the target project's running frontend service, else the repo's
    .env knob, else 3000. Only that one key is read."""
    if os.environ.get("HARNESS_FRONTEND_PORT"):
        return os.environ["HARNESS_FRONTEND_PORT"]
    # 10X Wave A (X-03): follow the host. Two recorded failures shaped this
    # order: (1) the old default probed :3000 while a stack booted with a
    # non-default knob published elsewhere (STATUS, W33 readiness pack);
    # (2) a stale .env knob (3010) can disagree with the live stack (3011 on
    # the RC project). `compose port` on the journey's own project — default
    # or E2E_COMPOSE_PROJECT — is the same source of truth the stack used, so
    # it outranks .env; an explicit env override always wins, and any compose
    # failure (docker down, project absent) falls through to .env, then 3000.
    try:
        out = subprocess.run(
            compose_command("port", "frontend", "3000"),
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.strip()
        port = out.rsplit(":", 1)[-1].strip()
        if port.isdigit():
            return port
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("HARNESS_FRONTEND_PORT="):
                return line.split("=", 1)[1].strip().strip("\"'") or "3000"
    except OSError:
        pass
    return "3000"


FRONTEND = os.environ.get("E2E_FRONTEND_URL", f"http://127.0.0.1:{_frontend_port()}").rstrip("/")
WAIT_SECONDS = int(os.environ.get("E2E_WAIT_SECONDS", "180"))

RUN_TAG = str(int(time.time()))
EMAIL_A = f"e2e-a-{RUN_TAG}@example.com"
EMAIL_B = f"e2e-b-{RUN_TAG}@example.com"

#: A clean health read must survive this long before the gate believes it.
HEALTH_SETTLE_SECONDS = 1.5

STEP = {"n": 0}


class Failure(Exception):
    def __init__(self, step: str, detail: str) -> None:
        super().__init__(f"{step}: {detail}")
        self.step = step
        self.detail = detail


def ok(name: str) -> None:
    STEP["n"] += 1
    print(f"[ok] step {STEP['n']:02d} {name}", flush=True)


def http(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict | None = None,
    timeout: float = 20.0,
) -> tuple[int, dict | str]:
    """One request. HTTP error statuses are RESPONSES (returned, not raised)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        status = e.code
    try:
        parsed: dict | str = json.loads(raw) if raw else {}
    except ValueError:
        parsed = raw
    return status, parsed


def expect(step: str, cond: bool, detail: str) -> None:
    if not cond:
        raise Failure(step, detail)


def wait_ready(name: str, url: str, deadline: float) -> tuple[int, dict | str]:
    last: tuple[int, dict | str] = (0, "never attempted")
    while time.time() < deadline:
        try:
            last = http("GET", url)
            if last[0] == 200:
                return last
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = (0, f"{type(exc).__name__}: {exc}")
        time.sleep(2)
    raise Failure(f"readiness of {name}", f"{url} not ready: {last!r}")


_ONE_SHOTS = {"migrate"}  # must exist and exit 0; never "running"
# Image-only services (worker-daemon starts them once per run): with the `worker` profile they are
# "enabled" yet may have no container at all. A missing container is fine; one that exists must
# have exited 0.
_BUILD_ONLY = {"engine-worker"}


def _problems_from_rows(rows: list[dict], enabled: set[str]) -> list[str]:
    """Every ENABLED compose service must have the container it should have.

    ``enabled`` is `docker compose config --services` — the services this
    stack is actually running, profiles applied — so containers left over from
    a drill profile are not held against a normal boot.

    `starting` is not a problem: it is what a healthy container looks like
    inside its start_period. Anything else — exited, restarting, dead,
    unhealthy, missing — is, including sidecars the product path never calls.
    sandbox-egress crash-looped through 708 healthchecks while every journey
    check passed, because the demo replay path does not need it; a sidecar that
    is down is a broken stack, not a pass. (An earlier version of this check
    read `docker compose ps` without `-a`, so a stopped container simply did
    not appear — the gate silently passed the exact case it exists for.)
    """
    by_service: dict[str, dict] = {}
    for row in rows:
        service = row.get("Service") or row.get("Name") or "?"
        by_service.setdefault(str(service), row)

    problems: list[str] = []
    for service in sorted(enabled):
        row = by_service.get(service)
        if row is None:
            if service not in _BUILD_ONLY:
                problems.append(f"{service}: no container")
            continue
        state = str(row.get("State") or "").strip().lower()
        health = str(row.get("Health") or "").strip().lower()
        status = str(row.get("Status") or "").strip()
        suffix = f" ({status})" if status else ""
        if service in _ONE_SHOTS or service in _BUILD_ONLY:
            exit_code = row.get("ExitCode")
            if state == "exited" and exit_code == 0:
                continue
            problems.append(f"{service}: expected a clean one-shot exit, got {state} exit {exit_code}")
        elif state != "running":
            problems.append(f"{service}: {state}{suffix}")
        elif health == "unhealthy":
            problems.append(f"{service}: unhealthy{suffix}")
    return problems


def _compose_lines(args: list[str], what: str) -> list[str]:
    proc = subprocess.run(
        compose_command(*args),
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise Failure("container health", f"{what} failed: {proc.stderr.strip()[:300]}")
    return [line for line in proc.stdout.splitlines() if line.strip()]


def enabled_services() -> set[str]:
    """Services this stack is running (profiles applied) — not every service
    the file defines, or a drill profile that was exercised last month would
    fail today's gate."""
    services = {line.strip() for line in _compose_lines(["config", "--services"], "compose config")}
    if not services:
        raise Failure("container health", "compose config --services listed nothing")
    return services


def container_rows() -> list[dict]:
    """`ps -a` on purpose: a container that stopped is the case to catch."""
    text = "\n".join(_compose_lines(["ps", "-a", "--format", "json"], "docker compose ps"))
    if not text.strip():
        raise Failure("container health", "docker compose ps reported no containers")
    try:  # newer compose emits one JSON array; older emits one object per line
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [row for row in parsed if isinstance(row, dict)]
        if isinstance(parsed, dict):
            return [parsed]
    except ValueError:
        pass
    rows: list[dict] = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def stable_health(
    rows_reader,
    services_reader,
    *,
    deadline: float,
    settle_seconds: float = HEALTH_SETTLE_SECONDS,
    clock=time.time,
    sleep=time.sleep,
):
    """Return the container rows once the stack looks healthy AND stays that way.

    ``docker compose ps -a`` can report a container as running for a second or
    two after its exit is already decided, so a single clean read is not
    evidence — that is how a journey passed with ``sandbox-egress`` stopped and
    then failed the same stack moments later. A clean read is therefore
    re-checked after ``settle_seconds``; only two consecutive clean reads pass.
    Polls until ``deadline`` so a stack still inside its start_period is not
    failed early.
    """
    problems: list[str] = []
    while clock() < deadline:
        problems = _problems_from_rows(rows_reader(), services_reader())
        if problems:
            sleep(2)
            continue
        sleep(settle_seconds)
        rows = rows_reader()
        problems = _problems_from_rows(rows, services_reader())
        if not problems:
            return rows
        sleep(2)
    raise Failure("container health", "; ".join(problems))


def wait_containers_healthy(deadline: float) -> None:
    """Wait for every enabled service to be up (and healthy where probed).

    See ``stable_health`` for why one clean read is not enough.
    """
    stable_health(container_rows, enabled_services, deadline=deadline)


def _identity_log_text() -> str:
    """Recent log text for the identity service, from the authoritative source.

    ``docker compose logs`` is not always the container's own log under Docker
    Desktop: measured 2026-10-27 on this stack, compose served a buffer ~17
    hours behind while ``docker logs <cid>`` was current — which makes dev OTP
    delivery look broken ("code not in identity logs") when it is working.
    Resolve the container id and read that; fall back to compose so a plain
    docker-compose setup still works.
    """
    ident = subprocess.run(
        compose_command("ps", "-q", "identity"),
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    cid = (ident.stdout or "").strip().splitlines()
    if cid and cid[0]:
        direct = subprocess.run(
            ["docker", "logs", "--tail", "2000", cid[0]],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if direct.returncode == 0 and (direct.stdout or direct.stderr):
            return direct.stdout + direct.stderr
    proc = subprocess.run(
        compose_command("logs", "--no-log-prefix", "identity"),
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return (proc.stdout or "") + (proc.stderr or "")


def fetch_otp(email: str) -> str:
    fixed = os.environ.get("E2E_OTP_CODE")
    if fixed:
        return fixed
    pattern = re.compile(rf"HARNESS-OTP login code for {re.escape(email)}: (\d+)")
    deadline = time.time() + 30
    last_err = "no log line seen"
    while time.time() < deadline:
        text = _identity_log_text()
        match = None
        for match in pattern.finditer(text):
            pass  # keep the LAST delivery
        if match:
            return match.group(1)
        last_err = "log read ok, code absent" if text else "identity logs empty"
        time.sleep(2)
    raise Failure("OTP delivery", f"code for {email} not in identity logs ({last_err})")


def authenticate(step: str, email: str) -> tuple[str, dict]:
    status, body = http("POST", f"{IDENTITY}/api/v1/auth/request-login", body={"email": email})
    expect(step, status == 200, f"request-login answered {status}: {body!r}")
    otp = fetch_otp(email)
    status, body = http(
        "POST", f"{IDENTITY}/api/v1/auth/verify-otp", body={"email": email, "code": otp}
    )
    expect(step, status == 200 and isinstance(body, dict), f"verify-otp answered {status}: {body!r}")
    token = body.get("token")
    principal = body.get("principal") or {}
    expect(step, bool(token) and bool(principal.get("teamId")), f"bad principal: {principal!r}")
    return token, principal


def main() -> int:
    t0 = time.time()
    print(f"compose E2E journey against {IDENTITY} (+orchestrator/sandbox/frontend)", flush=True)
    try:
        deadline = t0 + WAIT_SECONDS
        # 0. stack shape: every container running, healthy where probed.
        wait_containers_healthy(deadline)
        ok(f"all {len(container_rows())} containers running/healthy")

        # 1-2. readiness (identity /ready gates db + migrations applied)
        wait_ready("identity /ready", f"{IDENTITY}/ready", deadline)
        wait_ready("orchestrator /ready", f"{ORCHESTRATOR}/ready", deadline)
        wait_ready("sandbox /ready", f"{SANDBOX}/ready", deadline)
        # Next.js compiles on first request; a still-starting listener resets
        # the connection, so the frontend needs the same retry window.
        status, _ = wait_ready("frontend /", f"{FRONTEND}/", deadline)
        expect("readiness", status == 200, f"frontend answered {status}")
        ok("stack ready: identity+orchestrator+sandbox /ready, frontend 200")

        # 3-5. passwordless login, user A
        token_a, principal_a = authenticate("login A", EMAIL_A)
        expect("login A", principal_a.get("role") == "admin", f"expected admin, {principal_a!r}")
        ok(f"passwordless login A → solo team {principal_a['teamId']} (admin)")

        # 6-7. canned demo seed: recorded replay, idempotent
        status, seed = http(
            "POST", f"{ORCHESTRATOR}/api/v1/orchestrator/demo/seed", token=token_a, body={}
        )
        expect("demo seed", status == 201 and isinstance(seed, dict), f"seed answered {status}: {seed!r}")
        project_id, run_id = seed.get("projectId"), seed.get("runId")
        expect(
            "demo seed",
            bool(project_id) and bool(run_id) and seed.get("replay") is True
            and seed.get("state") == "pr_opened" and bool(seed.get("planId"))
            and bool(seed.get("prId")),
            f"seed shape: {seed!r}",
        )
        ok(f"demo seed → project {project_id}, run {run_id}, replay=true, pr_opened")
        status, seed2 = http(
            "POST", f"{ORCHESTRATOR}/api/v1/orchestrator/demo/seed", token=token_a, body={}
        )
        expect(
            "demo seed idempotency",
            status == 201 and seed2.get("runId") == run_id and seed2.get("projectId") == project_id,
            f"re-seed diverged: {seed2!r}",
        )
        ok("demo re-seed idempotent (same project/run)")

        # 8. run aggregate: terminal + steps
        status, run = http("GET", f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}", token=token_a)
        expect(
            "run aggregate",
            status == 200 and run.get("state") == "pr_opened" and len(run.get("steps") or []) >= 8,
            f"aggregate {status}: {run!r}",
        )
        ok(f"run aggregate → pr_opened with {len(run['steps'])} steps")

        # 9. engine result must report replay, never a live claim
        status, engine = http(
            "GET", f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}/engine", token=token_a
        )
        expect("engine truth", status == 200 and isinstance(engine, dict), f"engine {status}: {engine!r}")
        engine_mode = engine.get("engine", engine.get("mode"))
        expect("engine truth", engine_mode in {"replayed", "replay"}, f"engine mode {engine_mode!r}")
        ok(f"engine result truthfully reports {engine_mode!r} (no live claim)")

        # 10. event replay: demo marker on every payload, ordered seqs
        status, replay = http(
            "GET",
            f"{IDENTITY}/api/v1/projects/{project_id}/events/replay?limit=500",
            token=token_a,
        )
        items = (replay or {}).get("items") or []
        expect("event replay", status == 200 and len(items) >= 8, f"replay {status}: {len(items)} items")
        for ev in items:
            payload = ev.get("payload") or {}
            demo = payload.get("demo") or {}
            expect("event replay", demo.get("replay") is True, f"unmarked event: {ev.get('type')!r}")
        seqs = [ev.get("seq") for ev in items]
        expect("event replay", seqs == sorted(set(seqs)), "seqs not strictly increasing/unique")
        distinct = {ev.get("type") for ev in items}
        expect("event replay", len(distinct) >= 4, f"only types {distinct!r}")
        ok(f"event replay → {len(items)} marked events, {len(distinct)} types, ordered")

        # 11. audit trail: mint an invite (audited action), then read the page
        status, inv = http(
            "POST",
            f"{IDENTITY}/api/v1/teams/{principal_a['teamId']}/invites",
            token=token_a,
            body={"email": f"invitee-{RUN_TAG}@example.com", "role": "engineer"},
        )
        expect("audit", status == 201, f"invite mint {status}: {inv!r}")
        status, audit = http("GET", f"{IDENTITY}/api/v1/audit?limit=50", token=token_a)
        items = (audit or {}).get("items") or []
        actions = [i.get("action") for i in items]
        expect(
            "audit",
            status == 200 and any("invite" in (a or "") for a in actions),
            f"audit {status}: {audit!r}",
        )
        ok(f"audit page → {len(items)} team-scoped rows incl. {actions[:3]}")

        # 12. sandbox lifecycle
        task_id = f"e2e-{RUN_TAG}"
        status, handle = http(
            "POST",
            f"{SANDBOX}/api/v1/sandbox/tasks",
            token=token_a,
            body={"taskId": task_id, "repoRef": "local/e2e", "branch": "e2e"},
        )
        expect("sandbox create", status == 201, f"create {status}: {handle!r}")
        status, execd = http(
            "POST", f"{SANDBOX}/api/v1/sandbox/tasks/{task_id}/exec", token=token_a,
            body={"command": "echo e2e-ok"},
        )
        text = json.dumps(execd) if isinstance(execd, dict) else str(execd)
        expect(
            "sandbox exec",
            status == 200 and isinstance(execd, dict) and execd.get("status") == "passed"
            and execd.get("exit_code") == 0 and "e2e-ok" in text,
            f"exec {status}: {execd!r}",
        )
        status, _ = http(
            "POST", f"{SANDBOX}/api/v1/sandbox/tasks/{task_id}/destroy", token=token_a, body={}
        )
        expect("sandbox destroy", status == 200, f"destroy {status}")
        status, ghost = http(
            "POST", f"{SANDBOX}/api/v1/sandbox/tasks/{task_id}/exec", token=token_a,
            body={"command": "echo ghost"},
        )
        expect(
            "sandbox post-destroy",
            status == 404 and (ghost.get("error") or {}).get("code") == "TASK_NOT_FOUND",
            f"post-destroy exec {status}: {ghost!r}",
        )
        ok("sandbox create→exec(passed)→destroy→post-destroy 404")

        # 13. cross-tenant no-oracle: B reads A's rows as if they don't exist
        token_b, _ = authenticate("login B", EMAIL_B)
        status, foreign = http(
            "GET", f"{IDENTITY}/api/v1/projects/{project_id}/events/replay", token=token_b
        )
        expect(
            "no-oracle project",
            status == 404 and (foreign.get("error") or {}).get("code") == "NOT_FOUND",
            f"B read A's events: {status} {foreign!r}",
        )
        status, unknown = http(
            "GET", f"{IDENTITY}/api/v1/projects/does-not-exist-{RUN_TAG}/events/replay", token=token_b
        )
        expect(
            "no-oracle project",
            status == 404 and (unknown.get("error") or {}).get("code") == "NOT_FOUND",
            f"unknown project differs: {status} {unknown!r}",
        )
        status, foreign_run = http(
            "GET", f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}", token=token_b
        )
        expect(
            "no-oracle run",
            status == 404 and (foreign_run.get("error") or {}).get("code") == "NOT_FOUND",
            f"B read A's run: {status} {foreign_run!r}",
        )
        ok("cross-tenant reads answer identical 404 NOT_FOUND (no oracle)")

        # 14. metrics abroad
        status, metrics = http("GET", f"{ORCHESTRATOR}/metrics")
        families = set(re.findall(r"^harness_\w+", metrics if isinstance(metrics, str) else "", re.M))
        expect("metrics", status == 200 and len(families) >= 5, f"families {sorted(families)!r}")
        ok(f"metrics → {len(families)} harness_* families")

        # 15. frontend HTML
        status, html = http("GET", f"{FRONTEND}/")
        expect(
            "frontend",
            status == 200 and isinstance(html, str) and "<html" in html.lower(),
            f"frontend {status}",
        )
        ok("frontend serves HTML")

    except Failure as exc:
        print(f"\n[FAIL] {exc.step}\n       {exc.detail}", file=sys.stderr, flush=True)
        print(f"journey aborted after {STEP['n']} passing step(s); see evidence above.", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — journey must fail loudly with context
        print(f"\n[FAIL] unexpected {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1

    print(f"\nPASS: {STEP['n']} checks green in {time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
