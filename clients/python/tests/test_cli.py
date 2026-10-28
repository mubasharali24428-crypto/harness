"""W6 CLI: credential storage, the plan gate, and scriptable output.

No network: the SDK is replaced by a recorder. The point of these tests is the
contract the CLI must not break — a refusal rejects the plan, `--json` is one
object, and the token file is owner-only.
"""

from __future__ import annotations

import io
import json
import stat

import pytest
from harness_sdk.cli import config
from harness_sdk.cli import session as session_store
from harness_sdk.cli.commands import CliError, execute_run, format_diff
from harness_sdk.cli.main import main
from harness_sdk.errors import ApiError


@pytest.fixture()
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("HARNESS_TOKEN", raising=False)
    return tmp_path / "cfg"


def test_credentials_are_owner_only_and_round_trip(config_dir):
    path = config.save(config.Credentials("tok", "http://id", "http://orch"))
    assert path == config_dir / "credentials.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700
    stored = config.load()
    assert stored.token == "tok" and stored.identityUrl == "http://id"
    assert config.clear() is True
    assert config.load() is None
    assert config.clear() is False


def test_env_token_wins_over_the_file(config_dir):
    config.save(config.Credentials("file-token"))
    assert config.resolve_token({"HARNESS_TOKEN": "env-token"}) == "env-token"
    assert config.resolve_token({}) == "file-token"


class _Orch:
    def __init__(self, states=("done",)):
        self.calls: list[tuple] = []
        self.states = list(states)

    def propose_plan(self, body):
        # Real wire shape: the plan id only. The run binding comes from approve.
        self.calls.append(("propose", body))
        return {"planId": "plan-1"}

    def approve_plan(self, plan_id):
        self.calls.append(("approve", plan_id))
        return {"runId": "run-1"}

    def reject_plan(self, plan_id):
        self.calls.append(("reject", plan_id))
        return {}

    def start_run(self, run_id):
        self.calls.append(("start", run_id))
        return {}

    def get_run(self, run_id):
        self.calls.append(("get", run_id))
        state = self.states[0] if len(self.states) == 1 else self.states.pop(0)
        return {
            "runId": run_id,
            "state": state,
            "steps": [{"seq": 1, "type": "plan_approved", "ts": "2026-01-01T00:00:00Z"}],
        }

    def get_engine_result(self, run_id):
        return {"finalResponse": "fixed it", "toolCalls": [{"tool": "bash"}]}

    def run_diff(self, run_id, limit=100):
        return {
            "runId": run_id,
            "totalFiles": 1,
            "files": [{"path": "a.py", "additions": 1, "deletions": 0, "patch": "+x"}],
        }


class _Identity:
    def __init__(self, projects):
        self.projects = projects

    def list_projects(self, limit=None):
        return {"items": self.projects}


class _Sdk:
    def __init__(self, projects=None, states=("done",)):
        self.identity = _Identity(projects or [{"id": "proj-1", "name": "one"}])
        self.orchestrator = _Orch(states)


def test_approved_run_uses_the_plan_gate_and_reads_the_answer():
    sdk = _Sdk()
    result = execute_run(sdk, "fix it", assume_yes=True)
    calls = [name for name, *_ in sdk.orchestrator.calls]
    assert calls[:3] == ["propose", "approve", "start"]
    assert "reject" not in calls
    assert result["state"] == "done" and result["answer"] == "fixed it"
    assert result["toolCalls"][0]["tool"] == "bash"


def test_a_refused_prompt_rejects_and_starts_nothing():
    sdk = _Sdk()
    result = execute_run(sdk, "fix it", confirm=lambda _prompt: "n")
    calls = [name for name, *_ in sdk.orchestrator.calls]
    assert calls == ["propose", "reject"]
    assert result["state"] == "rejected" and result["answer"] == ""


def test_ambiguous_projects_are_refused_with_the_list():
    sdk = _Sdk(projects=[{"id": "a", "name": "one"}, {"id": "b", "name": "two"}])
    with pytest.raises(CliError) as exc:
        execute_run(sdk, "fix it", assume_yes=True)
    assert "a" in str(exc.value) and "b" in str(exc.value)
    assert sdk.orchestrator.calls == []


def test_json_mode_prints_exactly_one_object():
    sdk = _Sdk()
    stdout, stderr = io.StringIO(), io.StringIO()
    code = main(["run", "fix it", "--yes", "--json"], sdk=sdk, stdout=stdout, stderr=stderr)
    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["runId"] == "run-1" and payload["answer"] == "fixed it"
    assert stdout.getvalue().count("\n{") == 0  # a single object, not JSONL
    assert "plan plan-1" in stderr.getvalue()


def test_missing_token_fails_with_an_actionable_message(config_dir):
    stdout, stderr = io.StringIO(), io.StringIO()
    code = main(["status", "run-1"], stdout=stdout, stderr=stderr)
    assert code == 3 and "not logged in" in stderr.getvalue()


def test_status_and_diff_render_the_server_view():
    sdk = _Sdk()
    stdout = io.StringIO()
    assert main(["status", "run-1"], sdk=sdk, stdout=stdout) == 0
    assert "run run-1: done" in stdout.getvalue()
    assert "plan_approved" in stdout.getvalue()

    stdout = io.StringIO()
    assert main(["diff", "run-1"], sdk=sdk, stdout=stdout) == 0
    assert "a.py" in stdout.getvalue() and "+x" in stdout.getvalue()


def test_logout_removes_the_store(config_dir):
    config.save(config.Credentials("tok"))
    stderr = io.StringIO()
    assert main(["logout"], stderr=stderr) == 0
    assert config.load() is None and "removed" in stderr.getvalue()


def test_diff_formatter_marks_omitted_patches():
    text = format_diff(
        {"totalFiles": 1, "files": [{"path": "big.bin", "additions": 0, "deletions": 0,
                                    "patch": None, "patchOmitted": "binary"}]}
    )
    assert "big.bin" in text and "binary" in text


def test_every_command_accepts_the_service_url_flags(config_dir, monkeypatch):
    """Regression: only `login` used to define --identity-url/--orchestrator-url,
    so `harness run` crashed with AttributeError before any request. The parity
    drill found it because unit tests inject a client and never parse argv."""
    from harness_sdk.cli.main import build_parser

    parser = build_parser()
    for argv in (
        ["login", "--token", "t"],
        ["logout"],
        ["run", "goal", "--project", "p", "--yes", "--json"],
        ["status", "r1", "--json"],
        ["diff", "r1", "--json"],
    ):
        args = parser.parse_args([*argv, "--orchestrator-url", "http://orch"])
        assert args.orchestrator_url == "http://orch", argv
        assert args.identity_url is None


def test_run_without_a_client_uses_the_stored_token_and_env_urls(config_dir, monkeypatch):
    """The path the drill exercises: no injected sdk, a stored token, URLs from
    the environment. It must build a client rather than raise."""
    monkeypatch.setenv("HARNESS_ORCHESTRATOR_URL", "http://orch")
    monkeypatch.setenv("HARNESS_IDENTITY_URL", "http://id")
    config.save(config.Credentials("tok"))
    seen = {}

    class FakeHarness:
        def __init__(self, token=None, **kwargs):
            seen["token"] = token
            seen["urls"] = (kwargs.get("identity_url"), kwargs.get("orchestrator_url"))
            self.orchestrator = object()

    # `harness_sdk.cli.main` the ATTRIBUTE is the function (re-exported by the
    # package), so resolve the module object explicitly.
    import importlib

    main_module = importlib.import_module("harness_sdk.cli.main")
    monkeypatch.setattr(main_module, "Harness", FakeHarness)
    # main.py binds execute_run at import time, so patch it there.
    monkeypatch.setattr(
        main_module,
        "execute_run",
        lambda *a, **k: {"runId": "r1", "planId": "p1", "state": "done", "answer": "ok"},
    )
    out, err = io.StringIO(), io.StringIO()
    code = main(["run", "goal", "--project", "p", "--yes", "--json"],
                stdout=out, stderr=err, prompt=lambda _p: "y")
    assert code == 0, err.getvalue()
    assert seen["token"] == "tok"
    assert json.loads(out.getvalue())["runId"] == "r1"


# ---------------------------------------------------------------- chat (slice 3)


def _chat_argv(*extra: str, message: str = "hello"):
    return ["chat", "--message", message, *extra]


def test_chat_single_turn_goes_through_the_gate_and_persists(config_dir):
    sdk = _Sdk()
    out, err = io.StringIO(), io.StringIO()
    code = main(_chat_argv("--yes", "--json"), sdk=sdk, stdout=out, stderr=err,
                prompt=lambda _p: "y")
    assert code == 0, err.getvalue()
    calls = [name for name, *_ in sdk.orchestrator.calls]
    assert calls[:3] == ["propose", "approve", "start"]
    payload = json.loads(out.getvalue())
    (turn,) = payload["exchanges"]
    assert turn["message"] == "hello" and turn["answer"] == "fixed it"
    stored = session_store.load_session(payload["sessionId"])
    assert stored.exchanges[0]["runId"] == turn["runId"]
    path = session_store.session_path(payload["sessionId"])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_chat_followup_replays_plain_messages_not_compounded_goals(config_dir):
    session = session_store.new_session("proj-1")
    session.add("first", "first", {"runId": "r1", "planId": "p1", "state": "done", "answer": "a1"})
    goal = session.compose_goal("second")
    assert goal.startswith(session_store.HISTORY_HEADER)
    assert "- first\n  → a1" in goal
    assert "Current message: second" in goal
    # The rendered goal is what the server sees; the transcript stays plain.
    session.add("second", goal, {"runId": "r2", "planId": "p2", "state": "done", "answer": "a2"})
    assert session.exchanges[1]["message"] == "second"
    again = session.compose_goal("third")
    assert again.count("- first") == 1 and "- second" in again


def test_chat_refusal_rejects_that_turn_and_stops_the_loop(config_dir):
    sdk = _Sdk()
    out, err = io.StringIO(), io.StringIO()
    code = main(_chat_argv("--json"), sdk=sdk, stdout=out, stderr=err,
                prompt=lambda _p: "n")
    assert code == 0
    calls = [name for name, *_ in sdk.orchestrator.calls]
    assert calls == ["propose", "reject"]
    payload = json.loads(out.getvalue())
    assert payload["exchanges"][0]["state"] == "rejected"


def test_chat_resume_finds_the_stored_session_by_id(config_dir):
    sdk = _Sdk()
    out, err = io.StringIO(), io.StringIO()
    main(_chat_argv("--yes", "--json"), sdk=sdk, stdout=out, stderr=err)
    session_id = json.loads(out.getvalue())["sessionId"]

    sdk2 = _Sdk()
    out2, err2 = io.StringIO(), io.StringIO()
    code = main(_chat_argv("--resume", session_id, "--yes", "--json"),
                sdk=sdk2, stdout=out2, stderr=err2)
    assert code == 0, err2.getvalue()
    payload = json.loads(out2.getvalue())
    assert payload["sessionId"] == session_id
    assert len(payload["exchanges"]) == 2
    # The second turn's wire goal carried the first turn's history.
    second_goal = sdk2.orchestrator.calls[0][1]["goal"]
    assert "hello" in second_goal and "Current message: hello" in second_goal


def test_chat_resume_of_an_unknown_session_is_actionable(config_dir):
    config.save(config.Credentials("tok"))
    err = io.StringIO()
    code = main(_chat_argv("--resume", "chat-nope", "--yes"),
                stdout=io.StringIO(), stderr=err)
    assert code == 1 and "no session" in err.getvalue()


def test_chat_json_prints_exactly_one_object(config_dir):
    sdk = _Sdk()
    out, err = io.StringIO(), io.StringIO()
    assert main(_chat_argv("--yes", "--json"), sdk=sdk, stdout=out, stderr=err) == 0
    text = out.getvalue()
    payload = json.loads(text)
    assert payload["sessionId"].startswith("chat-")
    assert text.rstrip().endswith("}") and text.count("}{") == 0


def test_chat_continue_picks_the_newest_session(config_dir):
    first = session_store.new_session("proj-1")
    second = session_store.new_session("proj-1")
    assert session_store.latest_session_id() == second.session_id
    second.add("m", "m", {"runId": "r", "planId": "p", "state": "done", "answer": "x"})
    second.save()
    assert session_store.latest_session_id() == second.session_id
    assert first.session_id != second.session_id


# ---------------------------------------------------------------- W5 diagnostics


class _FlakyOrch(_Orch):
    """Fails the first N polls with a transport-class error, then answers."""

    def __init__(self, failures=2, status=503, code="UNAVAILABLE"):
        super().__init__(states=("done",))
        self.failures = failures
        self.status = status
        self.code = code

    def get_run(self, run_id):
        if self.failures:
            self.failures -= 1
            raise ApiError("temporarily unavailable", self.code, self.status)
        return super().get_run(run_id)


def _error_sdk(exc):
    sdk = _Sdk()
    orch = _Orch()

    def boom(_run_id):
        raise exc

    orch.get_run = boom
    sdk.orchestrator = orch
    return sdk


def test_missing_token_json_is_one_auth_object(config_dir):
    out, err = io.StringIO(), io.StringIO()
    code = main(["status", "r1", "--json"], stdout=out, stderr=err)
    payload = json.loads(out.getvalue())
    assert code == 3 and payload["exitCode"] == 3 and payload["ok"] is False
    assert payload["error"] == {"code": "NOT_LOGGED_IN", "message":
                                "not logged in; run `harness login --email …` or set "
                                "HARNESS_TOKEN", "retryable": False}


def test_policy_refusal_maps_to_policy_exit(config_dir):
    config.save(config.Credentials("tok"))
    sdk = _error_sdk(ApiError("blocked", "PLAN_NOT_APPROVED", 403))
    out, err = io.StringIO(), io.StringIO()
    code = main(["status", "r1", "--json"], sdk=sdk, stdout=out, stderr=err)
    payload = json.loads(out.getvalue())
    assert code == 4 and payload["error"]["code"] == "PLAN_NOT_APPROVED"
    assert payload["error"]["status"] == 403 and payload["error"]["retryable"] is False


def test_transport_failure_maps_to_transport_exit(config_dir):
    config.save(config.Credentials("tok"))
    sdk = _error_sdk(ApiError("down", "UNAVAILABLE", 503))
    out, err = io.StringIO(), io.StringIO()
    code = main(["status", "r1", "--json"], sdk=sdk, stdout=out, stderr=err)
    payload = json.loads(out.getvalue())
    assert code == 6 and payload["error"]["retryable"] is True


def test_usage_error_is_two_and_json_when_requested(capsys):
    out, err = io.StringIO(), io.StringIO()
    code = main(["bogus", "--json"], stdout=out, stderr=err)
    payload = json.loads(out.getvalue())
    assert code == 2 and payload["error"]["code"] == "USAGE" and payload["exitCode"] == 2


def test_capture_writes_the_same_object_owner_only(config_dir, tmp_path):
    sdk = _Sdk()
    out, err = io.StringIO(), io.StringIO()
    target = tmp_path / "capture.json"
    code = main(["run", "fix it", "--yes", "--json", "--capture", str(target)],
                sdk=sdk, stdout=out, stderr=err)
    assert code == 0
    piped = json.loads(out.getvalue())
    captured = json.loads(target.read_text())
    assert captured == piped
    assert captured["schemaVersion"] == 1 and captured["command"] == "run"
    assert captured["ok"] is True and captured["exitCode"] == 0
    assert captured["durationSeconds"] >= 0
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_a_failure_is_captured_too(config_dir, tmp_path):
    target = tmp_path / "capture.json"
    code = main(["status", "r1", "--json", "--capture", str(target)],
                stdout=io.StringIO(), stderr=io.StringIO())
    captured = json.loads(target.read_text())
    assert code == 3 and captured["ok"] is False and captured["error"]["code"] == "NOT_LOGGED_IN"


def test_transport_reconnects_then_completes():
    sdk = _Sdk()
    sdk.orchestrator = _FlakyOrch(failures=2)
    notes: list[str] = []
    result = execute_run(sdk, "fix it", assume_yes=True, sleep=lambda _s: None,
                         backoff=lambda _n: 0, note=notes.append)
    assert result["state"] == "done" and result["reconnects"] == 2
    assert result["lastTransportError"] == "UNAVAILABLE"
    assert len([n for n in notes if "reconnecting" in n]) == 2


def test_a_non_transport_refusal_is_not_retried():
    sdk = _Sdk()
    sdk.orchestrator = _FlakyOrch(failures=1, status=403, code="PLAN_NOT_APPROVED")
    with pytest.raises(ApiError):
        execute_run(sdk, "fix it", assume_yes=True, sleep=lambda _s: None,
                    backoff=lambda _n: 0)
    assert sdk.orchestrator.calls.count(("get", "run-1")) == 0


def test_the_token_never_appears_in_output_or_capture(config_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_TOKEN", "tok-super-secret")
    target = tmp_path / "capture.json"
    out, err = io.StringIO(), io.StringIO()
    code = main(["run", "fix it", "--yes", "--json", "--capture", str(target)],
                sdk=_Sdk(), stdout=out, stderr=err)
    assert code == 0
    assert "tok-super-secret" not in out.getvalue()
    assert "tok-super-secret" not in err.getvalue()
    assert "tok-super-secret" not in target.read_text()
