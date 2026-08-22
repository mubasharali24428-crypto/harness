import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

GET_REPO = text("SELECT id, team_id, name, repo_full_name FROM projects WHERE id = :id")
SET_REPO = text("UPDATE projects SET repo_full_name = :repo WHERE id = :id AND team_id = :team")


async def get_repo_full_name(db: AsyncSession, project_id: str) -> str:
    row = (await db.execute(GET_REPO, {"id": project_id})).mappings().first()
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
