from harness_contracts import KeyVault
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ApiKey

__all__ = ["KeyVault", "store_api_key", "read_api_key"]


async def store_api_key(
    session: AsyncSession,
    vault: KeyVault,
    team_id: str,
    provider: str,
    label: str,
    secret: str,
) -> ApiKey:
    row = ApiKey(
        team_id=team_id,
        provider=provider,
        label=label,
        key_encrypted=vault.encrypt(secret),
        key_hint=KeyVault.hint(secret),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def read_api_key(session: AsyncSession, vault: KeyVault, api_key_id: str) -> str:
    row = await session.get(ApiKey, api_key_id)
    if row is None:
        raise LookupError(f"api key {api_key_id} not found")
    return vault.decrypt(row.key_encrypted)
