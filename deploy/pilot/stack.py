"""Disposable P1 assembly. Run only against a dedicated scratch database.

init applies the real migration ledger and seeds two engineers in one team.
The API uses real auth, Postgres and routes with a deterministic Docker worker.
"""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "deploy/runners"), str(ROOT / "contracts/src")]
for source in ROOT.glob("section*/src"):
    sys.path.insert(0, str(source))


def _openrouter_key() -> str:
    """Read the key from HARNESS_OPENROUTER_KEY_FILE; the value is never logged or echoed."""
    with open(os.environ["HARNESS_OPENROUTER_KEY_FILE"]) as fh:
        for line in fh:
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("OPENROUTER_API_KEY not found in HARNESS_OPENROUTER_KEY_FILE")


def orchestrator_real():
    """Real engine (no stand-in worker) on a catalog-confirmed free OpenRouter model."""
    import services
    from harness_orchestrator import api
    from harness_orchestrator.engine import DeepSeekHarnessWrapper

    model = os.environ["HARNESS_TEST_OPENROUTER_MODEL"]
    if not model.endswith(":free"):
        raise SystemExit("real-worker proof only runs :free models")
    patch_src = ROOT / "section4/tests/fixtures/openrouter.patch.yml"

    class RealEngine(DeepSeekHarnessWrapper):
        def execute_goal(self, goal, session_id="session-001"):
            patch = Path(self.run_session_dir(session_id)) / "openrouter.patch.yml"
            patch.parent.mkdir(parents=True, exist_ok=True)
            patch.write_text(patch_src.read_text())
            os.environ["DSH_MODEL"] = model
            os.environ["DSH_PATCH_FILES"] = str(patch)
            return super().execute_goal(goal, session_id)

    app = services.app_orchestrator()
    api._service.engine = RealEngine(
        workspace_dir=os.environ["P1_WORKSPACE"],
        session_root=os.environ["P1_SESSION_ROOT"],
        live_mode="live",
        isolated=True,
        api_key=_openrouter_key(),
        base_url="https://openrouter.ai/api/v1",
        model=model,
    )
    return app


def orchestrator():
    import services
    from harness_orchestrator import api
    from harness_orchestrator.engine import DeepSeekHarnessWrapper

    class PilotEngine(DeepSeekHarnessWrapper):
        def run_workspace(self, session_id):
            path = super().run_workspace(session_id)
            ws = Path(path)
            if not ws.exists():
                ws.mkdir(parents=True)
                (ws / "gone.txt").write_text("delete me only after approval\n")
            return path

    app = services.app_orchestrator()
    api._service.engine = PilotEngine(
        workspace_dir=os.environ.get("P1_WORKSPACE", "/tmp/harness-p1/runs"),
        session_root=os.environ.get("P1_SESSION_ROOT", "/tmp/harness-p1/sessions"),
        live_mode="live",
        isolated=True,
        api_key="sk-pilot-synthetic-only",
        base_url="http://127.0.0.1:11434/v1",
        model="pilot-standin",
    )
    return app


def sandbox():
    import services

    return services.app_sandbox()


def identity():
    import services

    return services.app_identity()


async def init():
    import services
    from migrate import migrate
    from sqlalchemy import text

    factory = services.get_factory()
    engine = factory.kw["bind"]
    await migrate(engine)
    async with factory() as db:
        await db.execute(
            text("INSERT INTO teams (id,name) VALUES ('p1-team','P1 Pilot')")
        )
        for letter in ("a", "b"):
            await db.execute(
                text(
                    "INSERT INTO users (id,email,name,is_active) VALUES (:id,:email,:name,true)"
                ),
                {
                    "id": f"p1-{letter}",
                    "email": f"pilot-{letter}@harness.test",
                    "name": f"Pilot {letter}",
                },
            )
            await db.execute(
                text(
                    "INSERT INTO team_members (id,team_id,user_id,display_name,member_type,role) "
                    "VALUES (:id,'p1-team',:u,:name,'HUMAN','ENGINEER')"
                ),
                {
                    "id": f"p1-member-{letter}",
                    "u": f"p1-{letter}",
                    "name": f"Pilot {letter}",
                },
            )
        await db.execute(
            text(
                "INSERT INTO projects (id,team_id,name,repo_full_name) "
                "VALUES ('p1-project','p1-team','Pilot workspace','local/pilot')"
            )
        )
        await db.commit()
    await engine.dispose()
    print("P1 scratch database migrated and two engineers seeded")


if __name__ == "__main__":
    if sys.argv[1:] == ["init"]:
        asyncio.run(init())
    else:
        raise SystemExit(
            "Use init or uvicorn stack:identity / stack:orchestrator --factory"
        )
