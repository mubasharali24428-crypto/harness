#!/usr/bin/env python3
"""OPS-20: the compose-stack end-to-end journey (zero provider keys).

Runs against the RUNNING deployment (`docker compose -f deploy/docker-compose.yml
up -d`), exactly as CI boots it (workflow: .github/workflows/e2e.yml). Covers
the deterministic product path with recorded truth — no live provider is
touched, and nothing here claims one:

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
  E2E_COMPOSE_FILE    default <repo>/deploy/docker-compose.yml
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
IDENTITY = os.environ.get("E2E_IDENTITY_URL", "http://127.0.0.1:8001").rstrip("/")
ORCHESTRATOR = os.environ.get("E2E_ORCHESTRATOR_URL", "http://127.0.0.1:8004").rstrip("/")
SANDBOX = os.environ.get("E2E_SANDBOX_URL", "http://127.0.0.1:8005").rstrip("/")
FRONTEND = os.environ.get("E2E_FRONTEND_URL", "http://127.0.0.1:3000").rstrip("/")
WAIT_SECONDS = int(os.environ.get("E2E_WAIT_SECONDS", "180"))

RUN_TAG = str(int(time.time()))
EMAIL_A = f"e2e-a-{RUN_TAG}@example.com"
EMAIL_B = f"e2e-b-{RUN_TAG}@example.com"

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


def fetch_otp(email: str) -> str:
    fixed = os.environ.get("E2E_OTP_CODE")
    if fixed:
        return fixed
    pattern = re.compile(rf"HARNESS-OTP login code for {re.escape(email)}: (\d+)")
    deadline = time.time() + 30
    last_err = "no log line seen"
    while time.time() < deadline:
        proc = subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "logs", "--no-log-prefix", "identity"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        match = None
        for match in pattern.finditer(proc.stdout + proc.stderr):
            pass  # keep the LAST delivery
        if match:
            return match.group(1)
        last_err = proc.stderr.strip()[-200:] or "no log line seen"
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
        # 1-2. readiness (identity /ready gates db + migrations applied)
        deadline = t0 + WAIT_SECONDS
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
