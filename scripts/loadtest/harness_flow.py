"""Sync client for the real Harness user journey — the flow under load test.

One `run_flow()` call = the full lifecycle a human drives in the S7 UI
(section7/e2e/journey.e2e.ts driveLegacyPlanLifecycle plus the done-signal
the run page sends when accepting engine output):

    login -> create project -> propose plan -> approve plan -> start run
          -> poll status until AWAITING_DONE_SIGNAL (engine done-gate)
          -> POST done-signal -> poll until terminal (done | blocked)

Deliberately synchronous (requests + blocking sleep): locust schedules users
as gevent greenlets, so blocking calls ARE the cooperative scheduler. The same
client drives smoke_probe.py standalone.

Env knobs:
    HARNESS_IDENTITY_URL      default http://localhost:8001
    HARNESS_ORCHESTRATOR_URL  default http://localhost:8004
    HARNESS_POLL_INTERVAL_S   status poll cadence, default 0.5 (UI-like)
    HARNESS_RUN_TIMEOUT_S     give up on a non-terminal run, default 180
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import requests

IDENTITY_URL = os.environ.get("HARNESS_IDENTITY_URL", "http://localhost:8001").rstrip("/")
ORCHESTRATOR_URL = os.environ.get(
    "HARNESS_ORCHESTRATOR_URL", "http://localhost:8004"
).rstrip("/")

POLL_INTERVAL_S = float(os.environ.get("HARNESS_POLL_INTERVAL_S", "0.5"))
# Engine deadline inside section4 is ENGINE_RUN_TIMEOUT_SECONDS (default 600s).
# The probe gives up before that so one wedged slot cannot stall a whole sweep.
RUN_TIMEOUT_S = float(os.environ.get("HARNESS_RUN_TIMEOUT_S", "180"))

TERMINAL_STATES = {"done", "blocked"}


class FlowError(RuntimeError):
    """A step of the journey failed hard (non-2xx where 2xx is required)."""


def mint_run_id() -> str:
    """Client-minted run id, exactly like the S7 e2e helper."""
    return f"run-{uuid.uuid4().hex[:12]}"


class HarnessFlowClient:
    """Thin sync REST client over the two public backends (one VU = one)."""

    def __init__(self, timeout_s: float = 30.0) -> None:
        self._timeout = timeout_s
        self._session = requests.Session()

    def close(self) -> None:
        self._session.close()

    # ------------------------------------------------------------- primitives
    def _call(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        headers = {"content-type": "application/json"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        resp = self._session.request(
            method,
            url,
            json=body,
            headers=headers,
            timeout=self._timeout,
        )
        try:
            data: Any = resp.json() if resp.content else None
        except ValueError:
            data = None
        return resp.status_code, data

    @staticmethod
    def _require(status: int, data: Any, what: str) -> Any:
        if not 200 <= status < 300:
            raise FlowError(f"{what} failed: HTTP {status} {data}")
        return data

    # ----------------------------------------------------------------- steps
    def login(self, email: str) -> dict[str, Any]:
        """Passwordless login mints team + user + admin session."""
        status, data = self._call(
            "POST", f"{IDENTITY_URL}/api/v1/auth/login", body={"email": email}
        )
        if status == 403 and isinstance(data, dict):
            detail = data.get("detail")
            code = None
            if isinstance(detail, dict):
                code = (detail.get("error") or {}).get("code")
            elif isinstance(data.get("error"), dict):
                code = data["error"].get("code")
            if code == "OWNERSHIP_REQUIRED":
                raise FlowError(
                    "OWNERSHIP_REQUIRED: target stack runs with "
                    "HARNESS_REQUIRE_OWNERSHIP=on; point the probe at an isolated "
                    "stack with it off (run_probe.sh sets this)."
                )
        return dict(self._require(status, data, "login"))

    def create_project(self, token: str, name: str) -> str:
        status, data = self._call(
            "POST",
            f"{IDENTITY_URL}/api/v1/projects",
            token=token,
            body={"name": name},
        )
        return str(self._require(status, data, "create project")["id"])

    def propose_plan(self, token: str, run_id: str, goal: str, project_id: str) -> str:
        status, data = self._call(
            "POST",
            f"{ORCHESTRATOR_URL}/api/v1/orchestrator/runs/{run_id}/plan",
            token=token,
            body={"goal": goal, "projectId": project_id},
        )
        return str(self._require(status, data, "propose plan")["planId"])

    def approve_plan(self, token: str, run_id: str, plan_id: str) -> None:
        status, data = self._call(
            "POST",
            f"{ORCHESTRATOR_URL}/api/v1/orchestrator/runs/{run_id}/plan/approve",
            token=token,
            body={"planId": plan_id},
        )
        self._require(status, data, "approve plan")

    def start_run(self, token: str, run_id: str) -> None:
        status, data = self._call(
            "POST",
            f"{ORCHESTRATOR_URL}/api/v1/orchestrator/runs/{run_id}/start",
            token=token,
            body={},
        )
        self._require(status, data, "start run")

    def get_status(self, token: str, run_id: str) -> dict[str, Any]:
        status, data = self._call(
            "GET",
            f"{ORCHESTRATOR_URL}/api/v1/orchestrator/runs/{run_id}/status",
            token=token,
        )
        return dict(self._require(status, data, "get status"))

    def signal_done(self, token: str, run_id: str) -> None:
        """Accept engine output at the human done-gate (what the UI's accept
        button does). The deterministic reviewer passes clean diffs; a passed
        review on an engine-opened gate drives AWAITING_DONE_SIGNAL -> DONE."""
        status, data = self._call(
            "POST",
            f"{ORCHESTRATOR_URL}/api/v1/orchestrator/runs/{run_id}/done-signal",
            token=token,
            body={"diffText": "probe diff: no conflicts, no syntax errors"},
        )
        self._require(status, data, "done-signal")


def run_flow(client: HarnessFlowClient, email: str, goal: str) -> dict[str, Any]:
    """Drive the full journey once. Returns timing/result telemetry.

    Timings captured (all wall clock, seconds):
        login / project / plan / approve / start : per-step API latency
        wait_terminal : start -> terminal-state latency (engine queue+exec)
        total         : login -> terminal
    """
    timings: dict[str, float] = {}
    t_total0 = time.perf_counter()

    t0 = time.perf_counter()
    auth = client.login(email)
    token = str(auth["token"])
    timings["login"] = time.perf_counter() - t0

    project_name = f"probe-{uuid.uuid4().hex[:10]}"
    t0 = time.perf_counter()
    project_id = client.create_project(token, project_name)
    timings["project"] = time.perf_counter() - t0

    run_id = mint_run_id()
    goal_text = f"{goal} [{project_name}]"

    t0 = time.perf_counter()
    plan_id = client.propose_plan(token, run_id, goal_text, project_id)
    timings["plan"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    client.approve_plan(token, run_id, plan_id)
    timings["approve"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    client.start_run(token, run_id)
    timings["start"] = time.perf_counter() - t0

    t_wait0 = time.perf_counter()
    # Leg 1: engine execution until the human done-gate opens.
    while True:
        snap = client.get_status(token, run_id)
        state = str(snap.get("state") or "").lower()
        if state in ("awaiting_done_signal",) or state in TERMINAL_STATES:
            break
        if time.perf_counter() - t_wait0 > RUN_TIMEOUT_S:
            raise TimeoutError(
                f"run {run_id} never opened its done-gate within {RUN_TIMEOUT_S}s "
                f"(last state: {state or '?'})"
            )
        time.sleep(POLL_INTERVAL_S)
    timings["engine_wait"] = time.perf_counter() - t_wait0

    final_state = ""
    if snap["state"].lower() in TERMINAL_STATES:
        # Failed/blocked before the gate (e.g. engine error): no done-signal.
        timings["review"] = 0.0
        final_state = str(snap.get("state") or "").lower()
    else:
        # Leg 2: human accepts + deterministic review closes the run.
        t1 = time.perf_counter()
        client.signal_done(token, run_id)
        while True:
            snap = client.get_status(token, run_id)
            state = str(snap.get("state") or "").lower()
            if state in TERMINAL_STATES:
                final_state = state
                break
            if time.perf_counter() - t1 > RUN_TIMEOUT_S:
                raise TimeoutError(
                    f"run {run_id} never reached terminal after done-signal "
                    f"within {RUN_TIMEOUT_S}s (last state: {state or '?'})"
                )
            time.sleep(POLL_INTERVAL_S)
        timings["review"] = time.perf_counter() - t1

    timings["wait_terminal"] = (
        timings["engine_wait"] + timings["review"]
    )
    timings["total"] = time.perf_counter() - t_total0

    return {
        "run_id": run_id,
        "project_id": project_id,
        "final_state": final_state,
        "timings": timings,
    }
