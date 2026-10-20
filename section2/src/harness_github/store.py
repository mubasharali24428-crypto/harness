import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

GET_REPO = text(
    "SELECT id, team_id, name, repo_full_name FROM projects "
    "WHERE id = :id AND team_id = :team"
)
SET_REPO = text("UPDATE projects SET repo_full_name = :repo WHERE id = :id AND team_id = :team")
GET_WRITES = text("SELECT github_writes_enabled FROM projects WHERE id = :id AND team_id = :team")
SET_WRITES = text(
    "UPDATE projects SET github_writes_enabled = :enabled WHERE id = :id AND team_id = :team"
)


async def get_repo_full_name(db: AsyncSession, project_id: str, team_id: str) -> str:
    row = (
        await db.execute(GET_REPO, {"id": project_id, "team": team_id})
    ).mappings().first()
    if row is None:
        raise LookupError(f"project {project_id} not found")
    if not row["repo_full_name"]:
        raise LookupError(f"project {project_id} has no connected repo; call connectRepo first")
    return row["repo_full_name"]


async def connect_repo(
    db: AsyncSession, project_id: str, team_id: str, repo_full_name: str
) -> None:
    result = await db.execute(
        SET_REPO, {"repo": repo_full_name, "id": project_id, "team": team_id}
    )
    if result.rowcount == 0:
        raise LookupError(f"project {project_id} not found in team {team_id}")
    await db.commit()


async def writes_enabled(db: AsyncSession, project_id: str, team_id: str) -> bool:
    row = (await db.execute(GET_WRITES, {"id": project_id, "team": team_id})).mappings().first()
    if row is None:
        raise LookupError(f"project {project_id} not found")
    return bool(row["github_writes_enabled"])


async def set_writes_enabled(
    db: AsyncSession, project_id: str, team_id: str, enabled: bool
) -> None:
    result = await db.execute(
        SET_WRITES, {"enabled": enabled, "id": project_id, "team": team_id}
    )
    if result.rowcount == 0:
        raise LookupError(f"project {project_id} not found in team {team_id}")
    await db.commit()


RESERVE_WRITE_KEY = text(
    "INSERT INTO github_write_keys (project_id, idem_key, operation, created_at) "
    "VALUES (:p, :k, :op, CURRENT_TIMESTAMP) ON CONFLICT (project_id, idem_key) DO NOTHING"
)
GET_WRITE_KEY = text("SELECT result FROM github_write_keys WHERE project_id = :p AND idem_key = :k")
STORE_WRITE_KEY = text(
    "UPDATE github_write_keys SET result = :r WHERE project_id = :p AND idem_key = :k"
)
RELEASE_WRITE_KEY = text(
    "DELETE FROM github_write_keys WHERE project_id = :p AND idem_key = :k AND result IS NULL"
)


async def reserve_write_key(db: AsyncSession, project_id: str, key: str, operation: str) -> bool:
    """True when this request owns the key; False when an earlier request already holds it."""
    result = await db.execute(
        RESERVE_WRITE_KEY, {"p": project_id, "k": key, "op": operation}
    )
    await db.commit()
    return result.rowcount == 1


async def read_write_key(db: AsyncSession, project_id: str, key: str) -> str | None:
    """Stored JSON result of an earlier write; None while it is in flight or was released."""
    row = (await db.execute(GET_WRITE_KEY, {"p": project_id, "k": key})).mappings().first()
    return None if row is None else row["result"]


async def store_write_result(db: AsyncSession, project_id: str, key: str, result_json: str) -> None:
    await db.execute(STORE_WRITE_KEY, {"r": result_json, "p": project_id, "k": key})
    await db.commit()


async def release_write_key(db: AsyncSession, project_id: str, key: str) -> None:
    await db.execute(RELEASE_WRITE_KEY, {"p": project_id, "k": key})
    await db.commit()


async def get_user_token(db: AsyncSession, vault, user_id: str) -> str:
    row = (
        await db.execute(
            text(
                "SELECT access_token_encrypted FROM github_connections "
                "WHERE user_id = :u ORDER BY created_at DESC LIMIT 1"
            ),
            {"u": user_id},
        )
    ).mappings().first()
    if row is None:
        raise PermissionError("github not connected for this user; complete the OAuth flow first")
    return vault.decrypt(row["access_token_encrypted"])


def new_state() -> str:
    return uuid.uuid4().hex
