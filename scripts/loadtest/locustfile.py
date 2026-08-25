"""Harness throughput probe — locust profile for the real user journey.

Flow per iteration (matches the S7 UI / e2e journey):
    login -> create project -> propose plan -> approve -> start -> poll to terminal

Run it (see run_probe.sh which boots an ISOLATED stack first):

    .venv/bin/locust -f scripts/loadtest/locustfile.py --headless \
        -u 8 -r 2 --run-time 60s --csv results/sweep_8 --only-summary \
        --stop-timeout 120

Env knobs:
    HARNESS_IDENTITY_URL      default http://localhost:8001
    HARNESS_ORCHESTRATOR_URL  default http://localhost:8004
    HARNESS_POLL_INTERVAL_S   status poll cadence, default 0.5 (UI-like)
    HARNESS_RUN_TIMEOUT_S     give up on a non-terminal run, default 180
    HARNESS_TASK_PACING_S     optional min seconds between VU journeys

Locust stats mapping:
    control-plane  : propose+approve+start  -> the human-gate API path latencies
    engine-wait    : start->terminal        -> queueing + engine execution
    journey-total  : login->terminal        -> whole journey
    step-*         : per-leg detail kept out of the headline percentiles

Concurrency model note: every VU logs in once (on_start); passwordless login
mints one TEAM per VU, so VU count == team count and the durable queue's
per-team running cap (HARNESS_RUN_CONCURRENCY, default 4) admits up to 4*VU
claims. The process-wide wall this probe measures is therefore the dedicated
engine executor (ENGINE_MAX_CONCURRENCY, default 8) plus whatever the single
uvicorn process does under load.
"""

from __future__ import annotations

import os
import uuid

from locust import HttpUser, between, task

import harness_flow as flow


def _env_float(name: str, default: str) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


POLL_INTERVAL_S = _env_float("HARNESS_POLL_INTERVAL_S", "0.5")
RUN_TIMEOUT_S = _env_float("HARNESS_RUN_TIMEOUT_S", "180")
TASK_PACING_S = _env_float("HARNESS_TASK_PACING_S", "0")

GOAL = "Add input validation to the signup endpoint and cover it with tests."


class HarnessUser(HttpUser):
    """One virtual user looping over the full journey against the live stack."""

    # Requests below go through harness_flow's own session (identity + two
    # orchestrator bases), so the locust-abstract host never sees traffic.
    host = "http://localhost:8001"
    wait_time = between(max(TASK_PACING_S, 0.0), max(TASK_PACING_S, 0.0))

    def on_start(self) -> None:
        self.flow_client = flow.HarnessFlowClient()
        # One identity per VU; each login mints its own team (passwordless).
        self.email = f"vu-{uuid.uuid4().hex[:8]}@loadtest.local"

    def on_stop(self) -> None:
        self.flow_client.close()

    @task
    def full_journey(self) -> None:
        def record(name: str, seconds_ms: float, outcome: str | None = None) -> None:
            self.environment.events.request.fire(
                request_type="FLOW",
                name=name,
                response_time=seconds_ms,
                response_length=0,
                exception=None if outcome is None else RuntimeError(outcome),
            )

        try:
            result = flow.run_flow(self.flow_client, self.email, GOAL)
        except Exception as exc:  # noqa: BLE001 — any failure is a failed sample
            record("journey", 0.0, outcome=str(exc))
            return

        timings = result["timings"]
        final_state = result["final_state"]

        record("login", timings["login"] * 1000)
        record("project", timings["project"] * 1000)
        # Control plane: propose + approve + start (the synchronous human-gate path).
        record(
            "control-plane",
            (timings["plan"] + timings["approve"] + timings["start"]) * 1000,
        )
        # Engine leg: queued time + dry execution until terminal state.
        record(
            "engine-wait",
            timings["wait_terminal"] * 1000,
            outcome=None if final_state == "done" else final_state or "unknown",
        )
        record("journey-total", timings["total"] * 1000)

        # Per-leg detail (report material, not headline percentiles).
        record("step-plan", timings["plan"] * 1000)
        record("step-approve", timings["approve"] * 1000)
        record("step-start", timings["start"] * 1000)


def describe() -> dict[str, object]:  # pragma: no cover - introspection helper
    return {
        "flow": "login->project->plan->approve->start->poll-to-terminal",
        "terminal_states": sorted(flow.TERMINAL_STATES),
        "poll_interval_s": POLL_INTERVAL_S,
        "run_timeout_s": RUN_TIMEOUT_S,
    }
