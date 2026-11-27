"""Bring-your-own runner (Marathon 2.0 W13): the customer-host half of the execution leg.

    HARNESS_RUNNER_TOKEN=hrn_... python -I byo_runner.py --url https://harness.example \\
        --workspace /srv/checkout --confinement process [--once]

The runner polls its own jobs, claims one, reads the commands the *server* derived from
the approved plan, runs each through Section 5's ``shell_gate.py`` (allow-list, forbidden
and scope paths, Seatbelt/Landlock - none available means refused), reports a bounded,
scrubbed call/result event per command, and finishes. The server re-checks every command
against the approved plan, re-scrubs, and settles the run from the recorded results, so
the host is trusted to run and report honestly but cannot widen what may run.

Stdlib only; the three gate files (``GATE_FILES``) are the only Section 5 code needed on
the host, and their digest is what the signed attestation binds. The token is read from
the environment, never argv, and is redacted from anything the runner sends.
"""

import argparse
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "0.1.0"
HERE = Path(__file__).resolve().parent
GATE_FILES = ("policy", "confinement", "shell_gate")
API = "/api/v1"
ATTESTATION_LABEL = b"harness-runner-attestation-v1"
MAX_OUTPUT_CHARS = 2000
DEFAULT_TIMEOUT = 120
_SHAPES = re.compile(
    r"(?:hrn_|sk-|ghp_|gho_|github_pat_|xox[abp]-|AKIA)[A-Za-z0-9_\-]{12,}|Bearer\s+\S+"
)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = "") -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code = status, code


def scrub(text: str, token: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    return _SHAPES.sub("[redacted]", str(text or "").replace(token, "[redacted]"))[:limit]


def urllib_http(base_url: str):
    def call(method, path, body, headers):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            base_url.rstrip("/") + path, data=data, method=method,
            headers={"Content-Type": "application/json", **headers},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - operator-given URL
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read() or b"{}")
            except ValueError:
                return exc.code, {}

    return call


def _load_sanitize_environment():
    """Load policy.sanitize_environment by path (the gate trio is path-loaded)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "harness_gate_policy", HERE / "policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.sanitize_environment


def subprocess_gate(req: dict, tick) -> dict:
    """Run one command through ``shell_gate.py`` in its own process; ``tick`` is polled
    about once a second and returns True to cancel (the gate then kills the command group)."""
    # G2 (p04-sandbox-review §5): the gate process inherited the runner's full
    # environment, so on Linux /proc/<gate-pid>/environ exposed the runner
    # token to confined code (the gate's CHILD was already sanitized; the gate
    # process itself was the leak). The gate needs PATH/HOME basics only.
    proc = subprocess.Popen(
        [sys.executable, "-I", str(HERE / "shell_gate.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=_load_sanitize_environment()(os.environ),
        start_new_session=True,
    )
    payload: str | None = json.dumps(req)
    deadline = time.monotonic() + (req.get("timeoutSeconds") or DEFAULT_TIMEOUT) + 10
    while True:
        try:
            out, err = proc.communicate(payload, timeout=1)
            break
        except subprocess.TimeoutExpired:
            payload = None
            if time.monotonic() > deadline:  # the gate enforces timeoutSeconds itself
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
                return {"exitCode": None, "output": "[gate did not return]"}
            if tick():
                proc.terminate()
                proc.communicate()
                return {"cancelled": True}
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return {"denied": f"shell gate failed: {err.strip()[-300:] or 'no output'}"}


class Runner:
    def __init__(self, base_url, token, workspace, *, confinement="process", http=None,
                 gate=subprocess_gate, heartbeat_every=15.0) -> None:
        self.token = token
        self.workspace = str(workspace)
        self.confinement = confinement
        self.http = http or urllib_http(base_url)
        self.gate = gate
        self.heartbeat_every = heartbeat_every
        self._last_beat = float("-inf")

    # ----------------------------------------------------------------- wire

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Runner-Nonce": secrets.token_hex(16),
            "X-Runner-Timestamp": str(int(time.time())),
        }
        status, payload = self.http(method, API + path, body, headers)
        if status >= 400:
            detail = (payload.get("detail") or {}).get("error") or {}
            raise ApiError(status, detail.get("code", "ERROR"), detail.get("message", ""))
        return payload

    def attest(self) -> None:
        files = b"".join((HERE / f"{name}.py").read_bytes() for name in GATE_FILES)
        attestation = {
            "schema": 1,
            "confinement": self.confinement,
            "os": platform.system().lower()[:32] or "unknown",
            "hostIdSha256": hashlib.sha256(platform.node().encode()).hexdigest(),
            "runnerVersion": VERSION,
            "policyDigest": hashlib.sha256(files).hexdigest(),
        }
        canonical = json.dumps(attestation, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False)
        key = bytes.fromhex(
            hmac.new(self.token.encode(), ATTESTATION_LABEL, hashlib.sha256).hexdigest()
        )
        signature = hmac.new(key, canonical.encode(), hashlib.sha256).hexdigest()
        self.call("POST", "/runner/heartbeat", {"attestation": attestation, "signature": signature})

    # ------------------------------------------------------------------ job

    def poll_once(self) -> list[str]:
        """Attest, then take every offered job once. Returns the run ids this call executed."""
        self.attest()
        ran = []
        for job in self.call("GET", "/runner/jobs")["jobs"]:
            if job["state"] != "offered":
                continue
            try:
                self.run_job(job["runId"])
            except ApiError as exc:
                if exc.code in ("NOT_QUEUED", "JOB_NOT_OFFERED"):
                    continue  # raced: the run is no longer ours to take
                raise
            ran.append(job["runId"])
        return ran

    def _beat(self, run_id: str, force: bool = False) -> bool:
        """Renew the lease; True when the server says the run was stopped."""
        if not force and time.monotonic() - self._last_beat < self.heartbeat_every:
            return False
        self._last_beat = time.monotonic()
        return bool(self.call("POST", f"/runner/jobs/{run_id}/heartbeat").get("cancel"))

    def _stopped(self, run_id: str) -> bool:
        """Gate tick: a lost lease or a stop both mean 'kill the running command'."""
        try:
            return self._beat(run_id)
        except ApiError:
            return True

    def run_job(self, run_id: str) -> str:
        self.call("POST", f"/runner/jobs/{run_id}/claim")
        try:
            return self._execute(run_id)
        except ApiError:
            # Settle what we hold rather than leave the lease to expire (which orphans
            # the run); if the lease is already gone this refusal is expected.
            try:
                self.call("POST", f"/runner/jobs/{run_id}/finish", {"status": "failed"})
            except ApiError:
                pass
            raise

    def _execute(self, run_id: str) -> str:
        spec = self.call("GET", f"/runner/jobs/{run_id}/spec")
        timeout = int(spec["limits"].get("commandTimeoutSeconds") or DEFAULT_TIMEOUT)
        status = "completed"
        for index, command in enumerate(spec["commands"]):
            if self._beat(run_id, force=True):
                status = "failed"
                break
            call_id = f"c{index + 1}-{secrets.token_hex(4)}"
            self.call("POST", f"/runner/jobs/{run_id}/events", {"events": [
                {"phase": "call", "callId": call_id, "command": command}]})
            started = time.monotonic()
            result = self.gate(
                {"command": command, "cwd": self.workspace, "timeoutSeconds": timeout,
                 "forbiddenPaths": spec["forbiddenPaths"], "scopePaths": spec["scopePaths"]},
                lambda: self._stopped(run_id),
            )
            event = {"phase": "result", "callId": call_id, "ok": False, "exitCode": None,
                     "durationMs": int((time.monotonic() - started) * 1000)}
            if "denied" in result:
                event["error"] = scrub(result["denied"], self.token, 500)
            elif result.get("cancelled"):
                event["error"] = "cancelled by the server"
            else:
                event["exitCode"] = result["exitCode"]
                event["output"] = scrub(result.get("output", ""), self.token)
                event["ok"] = result["exitCode"] == 0
                if result["exitCode"] is None:
                    event["error"] = "timed out"
            self.call("POST", f"/runner/jobs/{run_id}/events", {"events": [event]})
            if not event["ok"]:
                status = "failed"
                break
        self.call("POST", f"/runner/jobs/{run_id}/finish", {"status": status})
        return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harness bring-your-own runner")
    parser.add_argument("--url", required=True, help="harness identity service base URL")
    parser.add_argument("--workspace", required=True, help="checkout the commands run in")
    parser.add_argument("--confinement", choices=("docker", "process", "none"), default="process")
    parser.add_argument("--interval", type=float, default=10.0, help="seconds between polls")
    parser.add_argument("--once", action="store_true", help="poll once and exit")
    args = parser.parse_args(argv)
    token = os.environ.get("HARNESS_RUNNER_TOKEN", "")
    if not token.startswith("hrn_"):
        print("set HARNESS_RUNNER_TOKEN to the hrn_... token shown once at registration",
              file=sys.stderr)
        return 2
    runner = Runner(args.url, token, args.workspace, confinement=args.confinement)
    while True:
        try:
            for run_id in runner.poll_once():
                print(f"executed {run_id}", flush=True)
        except (ApiError, OSError) as exc:
            print(f"poll failed: {scrub(str(exc), token, 300)}", file=sys.stderr, flush=True)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
