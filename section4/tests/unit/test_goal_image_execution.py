"""M06 at the service seam: images reach the engine call, and what does not
reach it is reported. The resolution rules are pinned in test_goal_images.py;
this file pins the plumbing that module cannot see — the keyword actually being
passed, the resume that must not re-attach, and the refusal that must happen
before any model call."""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.service import OrchestratorService

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10

DDL = """
CREATE TABLE teams (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) NOT NULL, budget_usd REAL NULL);
CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL);
CREATE TABLE run_claims (
    run_id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL, state VARCHAR(20) NOT NULL,
    claimed_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, worker_id VARCHAR(80) NOT NULL);
CREATE TABLE cost_ledger (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR(36),
    agent_role VARCHAR(20) DEFAULT 'primary', model VARCHAR(120) DEFAULT 'stub',
    input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
    cost_usd NUMERIC(12,6) DEFAULT 0, latency_ms INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00');
CREATE TABLE agent_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR(36) NOT NULL, type VARCHAR(80) NOT NULL,
    payload TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00');
CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL);
CREATE TABLE agent_plans (
    id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL, goal TEXT NOT NULL,
    files_likely_to_change TEXT DEFAULT '[]', steps TEXT DEFAULT '[]', tests TEXT DEFAULT '[]',
    risks TEXT DEFAULT '[]', estimated_cost_usd REAL DEFAULT 0, forbidden_paths TEXT DEFAULT '[]',
    expected_result TEXT DEFAULT '', status VARCHAR(20) NOT NULL,
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00');
CREATE TABLE artifacts (
    id VARCHAR(64) PRIMARY KEY, team_id VARCHAR(64) NOT NULL, run_id VARCHAR(64) NOT NULL,
    name VARCHAR(200) NOT NULL, media_type VARCHAR(120) NOT NULL, size_bytes INTEGER NOT NULL,
    sha256 VARCHAR(64) NOT NULL, storage_ref VARCHAR(200) NOT NULL, kind VARCHAR(20) NOT NULL,
    state VARCHAR(20) NOT NULL, scan TEXT DEFAULT '{}', request_id VARCHAR(64) NULL,
    created_by VARCHAR(64) NOT NULL, created_at TEXT NOT NULL, retention_until TEXT NULL,
    deleted_at TEXT NULL);
"""


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class CapturingEngine:
    """Modern signature; records exactly what the service handed the call."""

    api_key = None

    def __init__(self):
        self.calls = []

    def execute_goal(self, goal=None, session_id=None, model=None, on_progress=None, images=None):
        self.calls.append({"goal": goal, "images": images, "progress": on_progress is not None})
        return {
            "sessionId": session_id,
            "engine": "capture",
            "status": "completed",
            "finalResponse": "done",
        }


class NarrowEngine:
    """The historical signature: no on_progress, no images."""

    api_key = None

    def __init__(self):
        self.goals = []

    def execute_goal(self, goal=None, session_id=None):
        self.goals.append(goal)
        return {"sessionId": session_id, "status": "completed", "finalResponse": "ok"}


def _factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/gi.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as db:
            for stmt in DDL.split(";"):
                if stmt.strip():
                    await db.execute(text(stmt))
            await db.execute(text("INSERT INTO projects (id, team_id) VALUES ('p1','t1')"))
            await db.execute(
                text(
                    "INSERT INTO artifacts (id, team_id, run_id, name, media_type, size_bytes,"
                    " sha256, storage_ref, kind, state, created_by, created_at)"
                    " VALUES ('a1','t1','r1','shot.png','image/png',:sz,'x','t1/a1','upload',"
                    " 'available','u','2026-01-01T00:00:00+00:00')"
                ),
                {"sz": len(PNG_BYTES)},
            )
            await db.commit()

    run(_setup())
    return factory


def _started(tmp_path, engine):
    """propose → approve, and back with (service, run_data, plan) ready to execute.

    The stored bytes are patched in through ``read`` injection at the module
    seam rather than the real store root, so the test says nothing about disk
    layout and everything about what the engine receives.
    """
    import harness_orchestrator.goal_images as gi

    factory = _factory(tmp_path)
    svc = OrchestratorService(engine=engine, session_factory=factory)

    async def _go():
        await svc.propose_plan("r1", goal="why does this dialog look wrong", project_id="p1")
        plan_id = svc.runs["r1"]["plan_id"]
        await svc.approve_plan("r1", plan_id, "u-test")

    run(_go())
    run_data = svc.runs["r1"]
    run_data["teamId"] = "t1"
    plan = svc.planner.get_plan(run_data["plan_id"])

    async def fake_read(factory_, run_id, team_id, model=None, *, read=None):
        return await gi.resolve_goal_images(
            factory_, run_id, team_id, model, read=lambda _ref: PNG_BYTES
        )

    return svc, run_data, plan, factory, fake_read


def test_the_image_reaches_the_engine_call_and_the_result_reports_it(tmp_path, monkeypatch):
    import harness_orchestrator.service as service_module

    svc, run_data, plan, _f, fake_read = _started(tmp_path, CapturingEngine())
    eng = svc.engine

    async def resolve(factory_, run_id, team_id, model=None):
        return await fake_read(factory_, run_id, team_id, model)

    monkeypatch.setattr(service_module, "resolve_goal_images", resolve)
    # artifact_store.get would read the real store root; the fixture row points elsewhere
    monkeypatch.setattr(
        "harness_orchestrator.artifact_store.get", lambda ref: PNG_BYTES, raising=True
    )
    run(svc._execute(run_data, plan))
    call = eng.calls[-1]
    assert call["images"] is not None and len(call["images"]) == 1
    assert call["images"][0]["mimeType"] == "image/png"
    assert call["progress"] is True
    summary = run_data["engine_result"]["goalImages"]
    assert summary["attached"] == 1 and summary["requested"] == 1 and summary["refused"] is None


def test_a_resumed_attempt_does_not_re_attach_the_same_image(tmp_path, monkeypatch):
    import harness_orchestrator.service as service_module

    svc, run_data, plan, _f, fake_read = _started(tmp_path, CapturingEngine())
    eng = svc.engine

    async def resolve(factory_, run_id, team_id, model=None):
        return await fake_read(factory_, run_id, team_id, model)

    monkeypatch.setattr(service_module, "resolve_goal_images", resolve)
    monkeypatch.setattr("harness_orchestrator.artifact_store.get", lambda ref: PNG_BYTES)
    run(svc._execute(run_data, plan))
    assert len(eng.calls[-1]["images"] or []) == 1
    run(svc._execute(run_data, plan))  # second spawn: the session already has the image
    assert eng.calls[-1]["images"] is None


def test_an_engine_without_the_images_keyword_is_never_handed_one(tmp_path, monkeypatch):
    import harness_orchestrator.service as service_module

    narrow = NarrowEngine()
    called = {"n": 0}

    async def never(*a, **k):
        called["n"] += 1
        raise AssertionError("resolution must not run for an engine that cannot use it")

    monkeypatch.setattr(service_module, "resolve_goal_images", never)
    svc, run_data, plan, _f, _r = _started(tmp_path, narrow)
    run(svc._execute(run_data, plan))
    assert called["n"] == 0
    assert narrow.goals and narrow.goals[0].startswith("why does this dialog")


def test_a_non_vision_model_refuses_before_any_model_call(tmp_path, monkeypatch):
    import harness_orchestrator.service as service_module

    svc, run_data, plan, _f, fake_read = _started(tmp_path, CapturingEngine())
    eng = svc.engine
    monkeypatch.setenv("HARNESS_VISION_MODELS", "some-vision-model")

    async def resolve(factory_, run_id, team_id, model=None):
        return await fake_read(factory_, run_id, team_id, model)

    monkeypatch.setattr(service_module, "resolve_goal_images", resolve)
    run(svc._execute(run_data, plan))
    assert eng.calls == [], "a refusal must not cost a request"
    assert run_data["engine_result"]["engine"] == "image_policy"
    assert "not in HARNESS_VISION_MODELS" in run_data["engine_result"]["finalResponse"]


def test_no_artifacts_means_a_plain_text_call_with_no_summary(tmp_path, monkeypatch):
    import harness_orchestrator.service as service_module
    from harness_orchestrator.goal_images import GoalImages

    svc, run_data, plan, _f, _r = _started(tmp_path, CapturingEngine())
    eng = svc.engine

    async def empty(*a, **k):
        return GoalImages()

    monkeypatch.setattr(service_module, "resolve_goal_images", empty)
    run(svc._execute(run_data, plan))
    assert eng.calls[-1]["images"] is None
    assert "goalImages" not in run_data["engine_result"]
