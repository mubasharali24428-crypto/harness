"""B2/B3/B4: confined killable worker + staging change-set gate."""

import json
import os
import time
from pathlib import Path

import pytest

from harness_orchestrator import confine, staging
from harness_orchestrator.engine import DeepSeekHarnessWrapper

HERE = Path(__file__).resolve().parent
SECTION4 = HERE.parents[1]
needs_seatbelt = pytest.mark.skipif(not confine.seatbelt_available(), reason="macOS seatbelt only")


@pytest.fixture()
def worker_env(monkeypatch):
    monkeypatch.setenv("HARNESS_ENGINE_WORKER_MODULE", "standin_worker")
    monkeypatch.setenv("PYTHONPATH", f"{HERE}:{SECTION4}/src:{SECTION4.parent}/contracts/src")
    monkeypatch.setenv("HARNESS_ENGINE_CONFINEMENT", "off")
    monkeypatch.setenv("HARNESS_MASTER_KEY", "mk-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws")


def engine(tmp_path):
    root = Path(os.path.realpath(tmp_path))
    return DeepSeekHarnessWrapper(
        workspace_dir=str(root / "ws"),
        session_root=str(root / "sess"),
        api_key="sk-realistic-0123456789abcdef",
        base_url="http://127.0.0.1:9/v1",
        model="deepseek-chat",
        live_mode="live",
        isolated=True,
    )


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# ------------------------------------------------------------------ B4: kill


def test_cancel_kills_worker_and_its_children(tmp_path, worker_env):
    e = engine(tmp_path)
    box = {}

    import threading

    def work():
        box["r"] = e._run_live_events("slow", "run-k", "m")

    t = threading.Thread(target=work)
    t.start()
    pidfile = Path(e.run_workspace("run-k")) / "child.pid"
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text():
            break
        time.sleep(0.05)
    child = int(pidfile.read_text())
    assert alive(child) and e.is_worker_running("run-k")
    t0 = time.monotonic()
    assert e.cancel("run-k") is True  # confirmed gone
    assert time.monotonic() - t0 < 5
    t.join(5)
    assert not t.is_alive()
    assert box["r"][1] == "cancelled"
    time.sleep(0.2)
    assert not alive(child), "grandchild must die with the process group"
    assert not e.is_worker_running("run-k")
    assert e.cancel("run-k") is True  # idempotent


def test_cancel_is_sticky_until_reset(tmp_path, worker_env):
    e = engine(tmp_path)
    e.cancel("run-s")  # nothing running yet
    assert e._run_live_events("write:a.txt", "run-s", "m")[1] == "cancelled"
    assert not (Path(e.run_workspace("run-s")) / "a.txt").exists()  # worker never started
    e.reset_cancel("run-s")
    assert e._run_live_events("write:a.txt", "run-s", "m")[1] == "completed"


# ------------------------------------------------------ B2: env + confinement


def test_worker_env_is_allowlisted(tmp_path, worker_env):
    e = engine(tmp_path)
    res = json.loads(e._run_live_events("probe", "run-e", "m")[0])
    assert res["env"] == []  # no HARNESS_* / AWS_* secrets
    assert res["has_key"] is True  # only the provider key crosses


def test_required_confinement_fails_closed(tmp_path, worker_env, monkeypatch):
    monkeypatch.setenv("HARNESS_ENGINE_CONFINEMENT", "required")
    monkeypatch.setattr(confine, "seatbelt_available", lambda: False)
    text, status, *_ = engine(tmp_path)._run_live_events("write:x.txt", "run-r", "m")
    assert status == "error" and "unavailable" in text
    assert not (Path(engine(tmp_path).run_workspace("run-r")) / "x.txt").exists()


@needs_seatbelt
def test_seatbelt_isolates_concurrent_runs(tmp_path, worker_env, monkeypatch):
    monkeypatch.setenv("HARNESS_ENGINE_CONFINEMENT", "seatbelt")
    e = engine(tmp_path)
    other = Path(e.workspace) / "run-other"
    other.mkdir(parents=True)
    (other / "secret.txt").write_text("TOPSECRET")
    home_probe = Path.home() / "pwn_from_run.txt"
    home_probe.unlink(missing_ok=True)
    res = json.loads(e._run_live_events("probe", "run-A", "m")[0])
    assert res["write_own"] == "ALLOWED"
    assert res["write_other"].startswith("DENIED")
    assert res["read_other"].startswith("DENIED")
    assert res["write_outside"].startswith("DENIED")
    assert not home_probe.exists() and not (other / "pwn.txt").exists()


@needs_seatbelt
def test_two_concurrent_runs_cannot_touch_each_other(tmp_path, worker_env, monkeypatch):
    monkeypatch.setenv("HARNESS_ENGINE_CONFINEMENT", "seatbelt")
    e = engine(tmp_path)
    import concurrent.futures as cf

    with cf.ThreadPoolExecutor(2) as ex:
        a = ex.submit(e._run_live_events, "write:mine.txt", "run-1", "m")
        b = ex.submit(e._run_live_events, "write:mine.txt", "run-2", "m")
        assert a.result()[1] == b.result()[1] == "completed"
    assert (Path(e.run_workspace("run-1")) / "mine.txt").exists()
    assert (Path(e.run_workspace("run-2")) / "mine.txt").exists()
    assert not list(Path(e.run_workspace("run-1")).glob("*run-2*"))


# ---------------------------------------------------------------- B3: staging


def mk(tmp_path):
    root = str(Path(os.path.realpath(tmp_path)) / "ws")
    ws = Path(root) / "run-1"
    ws.mkdir(parents=True)
    (ws / "big.py").write_text("".join(f"line {i}\n" for i in range(80)))
    (ws / "small.txt").write_text("a\nb\nc\n")
    (ws / "gone.txt").write_text("delete me\n")
    staging.ensure_baseline("run-1", str(ws), root)
    return root, ws


def agent_edits(ws):
    (ws / "big.py").write_text("".join(f"CHANGED {i}\n" for i in range(80)))  # substantial
    (ws / "small.txt").write_text("a\nB\nc\n")  # small edit
    (ws / "gone.txt").unlink()  # deletion
    (ws / "new.txt").write_text("fresh\n")  # addition
    os.symlink("/etc/passwd", ws / "evil")  # symlink out of the workspace


def test_stage_withholds_high_impact_and_keeps_low(tmp_path):
    root, ws = mk(tmp_path)
    agent_edits(ws)
    rep = staging.stage("run-1", str(ws), root)
    assert sorted(rep.low_impact) == ["new.txt", "small.txt"]
    assert (ws / "small.txt").read_text() == "a\nB\nc\n"  # low-impact kept
    assert (ws / "gone.txt").read_text() == "delete me\n"  # deletion withheld -> restored
    assert (ws / "big.py").read_text().startswith("line 0")  # big edit withheld -> baseline
    assert not (ws / "evil").exists()  # symlink withheld
    by = {g.action: g for g in rep.groups}
    assert by["delete"].paths == ["gone.txt"]
    assert by["modify"].paths == ["big.py", "evil"]
    assert "CHANGED 0" in by["modify"].diff


def test_apply_requires_exact_digest_and_lands_change(tmp_path):
    root, ws = mk(tmp_path)
    (ws / "gone.txt").unlink()
    (ws / "big.py").write_text("".join(f"CHANGED {i}\n" for i in range(80)))
    rep = staging.stage("run-1", str(ws), root)
    d = {g.action: g for g in rep.groups}
    with pytest.raises(staging.StagingError):
        staging.apply_group("run-1", str(ws), root, "delete", ["gone.txt"], "0" * 64)
    assert (ws / "gone.txt").exists()
    staging.apply_group("run-1", str(ws), root, "delete", ["gone.txt"], d["delete"].digest)
    staging.apply_group("run-1", str(ws), root, "modify", ["big.py"], d["modify"].digest)
    assert not (ws / "gone.txt").exists()
    assert (ws / "big.py").read_text().startswith("CHANGED 0")
    assert staging.stage("run-1", str(ws), root).groups == []  # landed state is the new baseline


def test_tampered_staged_copy_or_wrong_paths_refused(tmp_path):
    root, ws = mk(tmp_path)
    (ws / "big.py").write_text("".join(f"CHANGED {i}\n" for i in range(80)))
    g = staging.stage("run-1", str(ws), root).groups[0]
    staged = Path(root) / ".staged" / "run-1" / "files" / "big.py"
    staged.write_text("malicious\n")
    with pytest.raises(staging.StagingError):
        staging.apply_group("run-1", str(ws), root, "modify", ["big.py"], g.digest)
    for bad in (["../../etc/passwd"], ["small.txt"]):
        with pytest.raises(staging.StagingError):
            staging.apply_group("run-1", str(ws), root, "modify", bad, g.digest)
    assert (ws / "big.py").read_text().startswith("line 0")


def test_escaping_symlink_cannot_be_applied_even_if_approved(tmp_path):
    root, ws = mk(tmp_path)
    os.symlink("/etc/passwd", ws / "evil")
    g = staging.stage("run-1", str(ws), root).groups[0]
    with pytest.raises(staging.StagingError, match="symlink"):
        staging.apply_group("run-1", str(ws), root, "modify", g.paths, g.digest)
    assert not (ws / "evil").exists()


def test_restage_replaces_changed_proposal_and_missing_baseline_fails_closed(tmp_path):
    root, ws = mk(tmp_path)
    (ws / "gone.txt").unlink()
    d1 = staging.stage("run-1", str(ws), root).groups[0].digest
    (ws / "big.py").write_text("".join(f"X{i}\n" for i in range(80)))
    rep = staging.stage("run-1", str(ws), root)
    assert {g.action for g in rep.groups} == {"delete", "modify"}
    assert [g for g in rep.groups if g.action == "delete"][0].digest == d1  # unchanged group stable
    with pytest.raises(staging.StagingError):
        staging.stage("run-x", str(ws), root)
