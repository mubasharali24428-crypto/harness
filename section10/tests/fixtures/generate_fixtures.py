"""Regenerate golden fixtures from the live app:  python tests/fixtures/generate_fixtures.py

Run from section10/ after intentional contract changes only — goldens are the
frozen contract surface (see AGENTS.md: breaking change => regen + same-commit note).
"""

import json
import sys
from pathlib import Path

import httpx

FIXTURES_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(FIXTURES_DIR.parents[2] / "src"))
sys.path.insert(0, str(FIXTURES_DIR.parent / "conformance"))

from cases import CASES  # noqa: E402

from harness_onboarding.api import Settings, make_app  # noqa: E402
from harness_onboarding.store import StateStore  # noqa: E402


async def run_case(case: dict) -> dict:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(
            state_path=f"{tmp}/state.json",
            identity_url="",
            github_url="",
            meeting_url="",
            orchestrator_url="",
        )
        app = make_app(settings=settings, store=StateStore(settings.state_path))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://golden") as client:
            if case["method"] == "GET":
                resp = await client.get(case["path"])
            elif case["method"] == "POST":
                resp = await client.post(case["path"], json=case.get("body"))
            else:
                resp = await client.put(case["path"], json=case.get("body"))
        return {"status": resp.status_code, "body": resp.json()}


def main() -> None:
    import asyncio

    for case in CASES:
        result = asyncio.run(run_case(case))
        out = FIXTURES_DIR / case["fixture"]
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {out.name} (status {result['status']})")


if __name__ == "__main__":
    main()
