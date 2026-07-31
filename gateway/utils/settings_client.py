"""Same call signatures as the old database.settings module, now backed by
an HTTP call to db-service instead of an in-process import.
"""
from utils.clients import db_client


async def get(guild_id: int) -> dict:
    return await db_client.get(f"/settings/{guild_id}")


async def set_currency(guild_id: int, name: str, emoji: str) -> None:
    await db_client.post(f"/settings/{guild_id}/currency", {"name": name, "emoji": emoji})


async def set_valorant_channel(guild_id: int, channel_id: int) -> None:
    await db_client.post(f"/settings/{guild_id}/valorant-channel", {"channel_id": channel_id})


async def set_staff_role(guild_id: int, role_id: int) -> None:
    await db_client.post(f"/settings/{guild_id}/staff-role", {"role_id": role_id})


async def set_voice_category(guild_id: int, category_id: int) -> None:
    await db_client.post(f"/settings/{guild_id}/voice-category", {"category_id": category_id})


async def set_twitch_channel(guild_id: int, channel_id: int) -> None:
    await db_client.post(f"/settings/{guild_id}/twitch-channel", {"channel_id": channel_id})
