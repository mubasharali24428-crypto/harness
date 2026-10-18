"""Build and run P1 on a fresh disposable PG18 database, then clean up.

Ports 8101/8104/8105/3107 must be unused. No existing services are stopped.
Run with section4/.venv/bin/python deploy/pilot/run_smoke.py.

``--real`` swaps the two stand-ins for the real thing: ownership is enforced so sign-in is the
OTP form (the dev notifier writes the code to the identity log), and the engine is the real
worker on a free OpenRouter model. Needs HARNESS_OPENROUTER_KEY_FILE and
HARNESS_TEST_OPENROUTER_MODEL (a ``:free`` model); the key is never printed or copied.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = str(ROOT / "section4/.venv/bin/python")
REAL = "--real" in sys.argv
OTP = "--otp" in sys.argv  # stand-in worker, but real OTP sign-in (ownership enforced)
IMAGE = "harness-engine-worker:real" if REAL else "harness-engine-worker:p1-tests"


def command(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, check=True, **kwargs)


def main():
    for port in (8101, 8104, 8105, 3107):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as exc:
                raise SystemExit(
                    f"Port {port} is occupied; stop your own scratch stack first"
                ) from exc
    if REAL:
        for var in ("HARNESS_OPENROUTER_KEY_FILE", "HARNESS_TEST_OPENROUTER_MODEL"):
            if not os.environ.get(var):
                raise SystemExit(f"--real needs {var}")
    output = ROOT / "docs/briefs/evidence" / ("pilot-prep" if REAL else "p1")
    output.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="harness-p1-proof-")).resolve()
    name = "harness-p1-proof-" + uuid.uuid4().hex[:12]
    children = []
    logs = ExitStack()
    created = False
    try:
        command(
            [
                "docker",
                "build",
                "-f",
                "section4/worker.Dockerfile",
                "--target",
                "worker" if REAL else "isolation-tests",
                "-t",
                IMAGE,
                ".",
            ],
            stdout=subprocess.DEVNULL,
        )
        command(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "-p",
                "127.0.0.1::5432",
                "-e",
                "POSTGRES_USER=pilot",
                "-e",
                "POSTGRES_PASSWORD=pilot",
                "-e",
                "POSTGRES_DB=pilot",
                "postgres:18",
            ],
            stdout=subprocess.DEVNULL,
        )
        created = True
        endpoint = subprocess.check_output(
            ["docker", "port", name, "5432/tcp"], text=True
        ).strip()
        db_port = endpoint.rsplit(":", 1)[1]
        for _ in range(100):
            if (
                subprocess.run(
                    ["docker", "exec", name, "pg_isready", "-U", "pilot"],
                    capture_output=True,
                    check=False,
                ).returncode
                == 0
            ):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("scratch database did not become ready")
        env = {
            **os.environ,
            "HARNESS_DATABASE_URL": f"postgresql+asyncpg://pilot:pilot@127.0.0.1:{db_port}/pilot",
            "HARNESS_ALLOW_DEV_MASTER_KEY": "1",
            "HARNESS_REQUIRE_OWNERSHIP": "true" if REAL or OTP else "false",
            "HARNESS_AUTHZ_TEST_MODE": "0",
            "HARNESS_ENGINE_ISOLATED": "1",
            "HARNESS_CORS_ORIGINS": '["http://localhost:3107","http://127.0.0.1:3107"]',
            "HARNESS_ENGINE_CONFINEMENT": "container",
            "HARNESS_ENGINE_IMAGE": IMAGE,
            "PYTHONPATH": str(ROOT / "deploy/pilot"),
            "P1_WORKSPACE": str(work / "runs"),
            "P1_SESSION_ROOT": str(work / "sessions"),
            "NEXT_PUBLIC_IDENTITY_BASE_URL": "http://localhost:8101",
            "NEXT_PUBLIC_IDENTITY_WS_URL": "ws://localhost:8101",
            "NEXT_PUBLIC_ORCHESTRATOR_BASE_URL": "http://localhost:8104",
            "NEXT_PUBLIC_SANDBOX_BASE_URL": "http://localhost:8105",
            "E2E_BASE_URL": "http://localhost:3107",
            "E2E_API_URL": "http://localhost:8101",
            "E2E_IDENTITY_LOG": str(work / "identity.log"),
            "E2E_TEST_TIMEOUT_MS": "300000",
            "E2E_OTP_SIGNIN": "1" if OTP else "0",
        }
        if not REAL:
            env["HARNESS_ENGINE_WORKER_MODULE"] = "pilot_worker"
        command([PYTHON, "deploy/pilot/stack.py", "init"], env=env)
        for service, port in (
            ("identity", 8101),
            ("orchestrator", 8104),
            ("sandbox", 8105),
        ):
            log = logs.enter_context(open(work / f"{service}.log", "w"))  # noqa: SIM115 - ExitStack owns it
            children.append(
                subprocess.Popen(
                    [
                        PYTHON,
                        "-m",
                        "uvicorn",
                        f"stack:{service}_real" if REAL and service == "orchestrator" else f"stack:{service}",
                        "--factory",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--no-access-log",
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            )
        log = logs.enter_context(open(work / "frontend.log", "w"))  # noqa: SIM115 - ExitStack owns it
        children.append(
            subprocess.Popen(
                [
                    "npm",
                    "run",
                    "dev",
                    "--",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    "3107",
                ],
                cwd=ROOT / "section7",
                env=env,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        )
        for url in (
            "http://localhost:8101/health",
            "http://localhost:8104/health",
            "http://localhost:8105/health",
            "http://localhost:3107/login",
        ):
            deadline = time.monotonic() + 60
            while True:
                try:
                    with urllib.request.urlopen(url, timeout=3) as response:
                        assert response.status == 200
                    break
                except (OSError, urllib.error.URLError):
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            f"service did not become ready: {url}"
                        ) from None
                    time.sleep(0.3)
        env["PLAYWRIGHT_JUNIT_OUTPUT_FILE"] = str(output / "browser.junit.xml")
        try:
            command(
                [
                    "npm",
                    "--prefix",
                    "section7",
                    "run",
                    "e2e",
                    "--",
                    "real.e2e.ts" if REAL else "multiplayer.e2e.ts",
                    "--retries=0",
                    "--reporter=list,junit",
                ],
                env=env,
            )
        except subprocess.CalledProcessError:
            subprocess.run(
                ["docker", "exec", name, "psql", "-U", "pilot", "-c",
                 ("SELECT type, left(payload::text, 600) FROM agent_events"
                  " WHERE type IN ('engine_failed','engine_completed') ORDER BY ts")],
                check=False,
            )
            raise
        if REAL:
            made = [p.read_text().strip() for p in (work / "runs").glob("*/engine_ok.txt")]
            assert made == ["engine ok"], f"real worker did not create the file: {made}"
            artifact = ROOT / "section7/test-results/real-complete.png"
            (output / "real-complete.png").write_bytes(artifact.read_bytes())
            (output / "real-result.json").write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "freshDatabase": True,
                        "signIn": "OTP form, HARNESS_REQUIRE_OWNERSHIP=true, code read from identity log (dev notifier)",
                        "worker": "real engine in container, free OpenRouter model",
                        "model": os.environ["HARNESS_TEST_OPENROUTER_MODEL"],
                        "fileCreatedByModel": "engine_ok.txt == 'engine ok'",
                    },
                    indent=2,
                )
                + "\n"
            )
            return
        # Two runs exist: run 1 had its deletion approved and applied (gone.txt removed); run 2 never
        # asked to delete, so its own gone.txt must still be there (file-level run isolation).
        workspaces = sorted(p.name for p in (work / "runs").glob("run-*") if p.is_dir())
        kept = sorted(p.parent.name for p in (work / "runs").glob("run-*/gone.txt"))
        assert len(workspaces) == 2, f"expected two run workspaces, found {workspaces}"
        assert len(kept) == 1, "run 1's approved deletion did not land, or it deleted run 2's file"
        metrics_env = {
            **env,
            "PYTHONPATH": f"{ROOT / 'section4/src'}:{ROOT / 'contracts/src'}",
        }
        snapshot = subprocess.check_output(
            [PYTHON, "-m", "harness_orchestrator.pilot_metrics", "p1-team"],
            cwd=ROOT,
            env=metrics_env,
            text=True,
        )
        metrics_out = ROOT / "docs/briefs/evidence/p3"
        metrics_out.mkdir(parents=True, exist_ok=True)
        (metrics_out / "pilot-metrics-synthetic-run.json").write_text(snapshot)
        artifact = ROOT / "section7/test-results/multiplayer-complete.png"
        (output / "multiplayer-complete.png").write_bytes(artifact.read_bytes())
        (output / "live-result.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "freshDatabase": True,
                    "realPostgres": 18,
                    "apiPort": 8104,
                    "separateBrowserContexts": 2,
                    "consoleOrNetworkErrors": 0,
                    "deletedFileApplied": True,
                    "secondRunFileUntouched": True,
                    "legs": ["changed proposal re-requires approval", "cross-run isolation", "refresh and reconnect survival"],
                    "worker": "deterministic Docker stand-in",
                    "workspaces": str(work / "runs"),
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        import signal

        for child in reversed(children):
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
        logs.close()
        if created:
            subprocess.run(
                ["docker", "rm", "-f", "-v", name], capture_output=True, check=False
            )


if __name__ == "__main__":
    main()
