"""Per-guild settings, editable at runtime via /settings instead of .env.

On first access for a guild, a row is seeded from config.py's env-backed
values (so upgrading an already-running bot doesn't reset anything it was
already configured with) or from config.py's hardcoded defaults for fields
that have no env equivalent (currency name/emoji).
"""
import config
from database import db


async def get(guild_id: int):
    guild_id = str(guild_id)
    row = await db.fetchone("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
    if row is not None:
        return row

    await db.execute(
        """INSERT INTO guild_settings
               (guild_id, currency_name, currency_emoji, valorant_updates_channel_id, inhouse_staff_role_id, inhouse_voice_category_id)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(guild_id) DO NOTHING""",
        (
            guild_id,
            config.CURRENCY_NAME,
            config.CURRENCY_EMOJI,
            str(config.VALORANT_UPDATES_CHANNEL_ID) if config.VALORANT_UPDATES_CHANNEL_ID else None,
            str(config.INHOUSE_STAFF_ROLE_ID) if config.INHOUSE_STAFF_ROLE_ID else None,
            str(config.INHOUSE_VOICE_CATEGORY_ID) if config.INHOUSE_VOICE_CATEGORY_ID else None,
        ),
    )
    return await db.fetchone("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))


async def set_currency(guild_id: int, name: str, emoji: str) -> None:
    await get(guild_id)
    await db.execute(
        "UPDATE guild_settings SET currency_name = ?, currency_emoji = ? WHERE guild_id = ?",
        (name, emoji, str(guild_id)),
    )


async def set_valorant_channel(guild_id: int, channel_id: int) -> None:
    await get(guild_id)
    await db.execute(
        "UPDATE guild_settings SET valorant_updates_channel_id = ? WHERE guild_id = ?",
        (str(channel_id), str(guild_id)),
    )


async def set_staff_role(guild_id: int, role_id: int) -> None:
    await get(guild_id)
    await db.execute(
        "UPDATE guild_settings SET inhouse_staff_role_id = ? WHERE guild_id = ?",
        (str(role_id), str(guild_id)),
    )


async def set_voice_category(guild_id: int, category_id: int) -> None:
    await get(guild_id)
    await db.execute(
        "UPDATE guild_settings SET inhouse_voice_category_id = ? WHERE guild_id = ?",
        (str(category_id), str(guild_id)),
    )


async def set_twitch_channel(guild_id: int, channel_id: int) -> None:
    await get(guild_id)
    await db.execute(
        "UPDATE guild_settings SET twitch_announcements_channel_id = ? WHERE guild_id = ?",
        (str(channel_id), str(guild_id)),
    )
