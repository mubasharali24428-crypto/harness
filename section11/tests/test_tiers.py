"""M22-M2 — context-index tier cap + upgrade CTA over the frozen search API.

Proves, over real HTTP with the REAL contracts bearer dep:

1. free-tier defaults enforce the index-size cap even with ZERO billing
   infrastructure present (absent schema ⇒ free defaults, honest degrade);
2. the 402 body carries {error envelope, upgrade {tier, cap, capField,
   used, upgradeTo, cta}} — an upgrade action, never a dead end;
3. per-team 0031 override columns beat the published default;
4. UNLIMITED stays unlimited: a deploy-wide env knob cannot introduce an
   index cap on pro (no silent downgrade of what the tier buys).

conftest.py is FROZEN — extra tables (projects/teams/billing_plans) are
created per-test on the shared fixture engine, mirroring migration 0031.
"""

from conftest import AUTH_HEADERS
import pytest
from sqlalchemy import text

from harness_contracts import ENV_TIER_CONTEXT_CHUNKS

PROJECT = "p-cap"

_CHUNK_INSERT = """
INSERT INTO context_chunks (project_id, path, lang, start_line, end_line,
                            content, embedding, chunk_hash)
VALUES (:pid, :path, 'py', :start, :end, 'x', '[0.1]', 'h'
)
"""


async def _seed_chunks(db, project_id: str, n: int) -> None:
    """Deterministically fill the index so usage math needs no async job."""
    async with db() as session:
        for i in range(n):
            await session.execute(
                text(_CHUNK_INSERT),
                {"pid": project_id, "path": f"a{i}.py", "start": i * 10, "end": i * 10 + 5},
            )
        await session.commit()


async def _add_tables(db, *, plan: str | None, chunks_override: int | None) -> None:
    """Mirror projects/teams/billing_plans (+0031 columns) on the fixture DB."""
    async with db() as session:
        await session.execute(
            text("CREATE TABLE IF NOT EXISTS teams (id VARCHAR(36) PRIMARY KEY)")
        )
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS projects ("
                "id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL)"
            )
        )
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS billing_plans ("
                "id VARCHAR(36) PRIMARY KEY,"
                " team_id VARCHAR(36) UNIQUE NOT NULL,"
                " plan VARCHAR(30) NOT NULL,"
                " monthly_budget_usd NUMERIC(12,6) NOT NULL DEFAULT 0,"
                " runs_per_day_override INTEGER,"
                " context_index_max_chunks_override INTEGER)"
            )
        )
        if plan is not None:
            await session.execute(
                text("INSERT INTO teams (id) VALUES ('t-cap')")
            )
            await session.execute(
                text("INSERT INTO projects (id, team_id) VALUES (:p, 't-cap')"),
                {"p": PROJECT},
            )
            await session.execute(
                text(
                    "INSERT INTO billing_plans (id, team_id, plan,"
                    " context_index_max_chunks_override)"
                    " VALUES ('bp', 't-cap', :plan, :ovr)"
                ),
                {"plan": plan, "ovr": chunks_override},
            )
        await session.commit()


def _post_index(client, repo):
    return client.post(
        "/api/v1/context/index",
        json={"repoPath": str(repo), "projectId": PROJECT},
        headers=AUTH_HEADERS,
    )


@pytest.fixture
def empty_repo(tmp_path):
    """A real (empty) directory: passes path validation, indexes zero files."""
    root = tmp_path / "caprepo"
    root.mkdir()
    return root


async def test_free_defaults_enforce_cap_without_any_billing_rows(client, db, monkeypatch, empty_repo):
    """No billing infra AT ALL: free cap applies honestly (used >= limit)."""
    monkeypatch.setenv(ENV_TIER_CONTEXT_CHUNKS, "3")
    await _seed_chunks(db, PROJECT, 3)

    res = await _post_index(client, empty_repo)
    assert res.status_code == 402, res.text
    body = res.json()
    # Frozen envelope PLUS structured upgrade block:
    assert body["error"]["code"] == "TIER_CAP_REACHED"
    up = body["upgrade"]
    assert set(up) == {"tier", "cap", "capField", "used", "upgradeTo", "cta"}
    assert up["tier"] == "free"
    assert up["cap"] == 3 and up["used"] == 3
    assert up["capField"] == "contextIndexMaxChunks"
    assert up["upgradeTo"] == "pro"
    assert up["cta"]["action"] == "upgrade" and "Pro" in up["cta"]["label"]


async def test_under_cap_still_indexes(client, db, monkeypatch, empty_repo):
    """Below the cap the endpoint behaves exactly as before (200 + pending)."""
    monkeypatch.delenv(ENV_TIER_CONTEXT_CHUNKS, raising=False)
    res = await _post_index(client, empty_repo)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "pending"


async def test_team_override_column_beats_tier_default(client, db, monkeypatch, empty_repo):
    """0031 override wins over defaults — even caps a paid tier explicitly."""
    monkeypatch.delenv(ENV_TIER_CONTEXT_CHUNKS, raising=False)
    await _add_tables(db, plan="pro", chunks_override=2)
    await _seed_chunks(db, PROJECT, 2)

    res = await _post_index(client, empty_repo)
    assert res.status_code == 402, res.text
    up = res.json()["upgrade"]
    assert up["tier"] == "pro"
    assert up["cap"] == 2 and up["used"] == 2


async def test_env_knob_cannot_introduce_cap_on_pro(client, db, monkeypatch, empty_repo):
    """UNLIMITED stays unlimited: env tunes caps, never downgrades pro."""
    monkeypatch.setenv(ENV_TIER_CONTEXT_CHUNKS, "1")
    await _add_tables(db, plan="pro", chunks_override=None)
    await _seed_chunks(db, PROJECT, 9)

    res = await _post_index(client, empty_repo)
    assert res.status_code == 200, res.text  # pro is uncapped regardless of env


async def test_missing_project_row_falls_back_to_free_defaults(client, db, monkeypatch, empty_repo):
    """Unknown projectId (no projects row): free defaults, not an error."""
    monkeypatch.setenv(ENV_TIER_CONTEXT_CHUNKS, "0")
    await _seed_chunks(db, PROJECT, 1)

    res = await _post_index(client, empty_repo)
    assert res.status_code == 402, res.text
    assert res.json()["upgrade"]["cap"] == 0
