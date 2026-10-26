"""W2 (N02) at the engine seam: the baseline actually reaches the model.

`test_repo_context.py` pins what the block contains. This file pins the two things
that module cannot see: that the goal handed to the SDK carries it, and that a
resumed session does not get it twice. The SDK session continues across a resume,
so a second copy is not free context — it is a duplicate the model has to read
again and a prompt that grows with every retry.
"""

from harness_orchestrator.engine import DeepSeekHarnessWrapper


def _wrapper(tmp_path, with_repo=True):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    if with_repo:
        (ws / "src").mkdir()
        (ws / "src" / "app.py").write_text("print('hi')\n")
        (ws / "README.md").write_text("# Widget\n\nDoes widget things.\n")
        (ws / "pyproject.toml").write_text('[project]\nname = "widget"\n')
    return DeepSeekHarnessWrapper(
        workspace_dir=str(ws),
        session_root=str(tmp_path / "sessions"),
        api_key="sk-test",
        base_url="http://engine.test/v1",
        model="m1",
        live_mode="live",
        isolated=False,
    )


def _capture(monkeypatch, w):
    goals: list[str] = []

    def fake(goal, session_id, **kwargs):
        goals.append(goal)
        return "ok", "completed", "m1", []

    monkeypatch.setattr(w, "_run_live_events", fake)
    return goals


def test_the_goal_carries_the_repo_baseline(tmp_path, monkeypatch):
    w = _wrapper(tmp_path)
    goals = _capture(monkeypatch, w)
    w.execute_goal("fix the login bug", session_id="s-1")
    assert goals, "expected the live path to run"
    assert goals[0].startswith("fix the login bug")
    assert "Repository context" in goals[0]
    assert "Does widget things." in goals[0]


def test_an_operator_can_switch_it_off(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_REPO_CONTEXT", "0")
    w = _wrapper(tmp_path)
    goals = _capture(monkeypatch, w)
    w.execute_goal("fix the login bug", session_id="s-1")
    assert goals[0] == "fix the login bug"


def test_a_resumed_session_does_not_re_send_it(tmp_path, monkeypatch):
    w = _wrapper(tmp_path)
    goals = _capture(monkeypatch, w)
    w.execute_goal("fix the login bug", session_id="s-1")
    w.execute_goal("still fixing it", session_id="s-1")
    assert "Repository context" in goals[0]
    assert goals[1] == "still fixing it"


def test_a_second_session_gets_its_own_copy(tmp_path, monkeypatch):
    """The marker is per session, not per process: a different run must not be
    starved of context because another one already had it."""
    w = _wrapper(tmp_path)
    goals = _capture(monkeypatch, w)
    w.execute_goal("task one", session_id="s-1")
    w.execute_goal("task two", session_id="s-2")
    assert "Repository context" in goals[1]


def test_a_bare_workspace_leaves_the_goal_untouched(tmp_path, monkeypatch):
    """Nothing to say, so nothing added: an empty scaffold in the prompt would
    teach the model the repo is empty."""
    w = _wrapper(tmp_path, with_repo=False)
    goals = _capture(monkeypatch, w)
    w.execute_goal("do the thing", session_id="s-1")
    assert "Repository context" not in goals[0]
    assert goals[0].startswith("do the thing")


def test_a_files_only_workspace_still_gets_a_tree(tmp_path, monkeypatch):
    """The opposite edge: no README, no manifest, no git — but files worth
    naming. A file list is the one part of the baseline that is still useful."""
    w = _wrapper(tmp_path, with_repo=False)
    (tmp_path / "ws" / "main.go").write_text("package main\n")
    goals = _capture(monkeypatch, w)
    w.execute_goal("do the thing", session_id="s-1")
    assert "Repository context" in goals[0]
    assert "main.go" in goals[0]
