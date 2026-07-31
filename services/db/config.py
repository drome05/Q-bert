"""db-service config: the only service that touches the SQLite file/PVC directly."""
import logging
import os

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("db-service.config")


def _optional_id(name: str) -> str | None:
    """Parse an optional Discord-ID-shaped env var, tolerating an unedited
    placeholder (e.g. 'role_id_here') by treating it as unset rather than
    seeding guild_settings with garbage."""
    raw = os.environ.get(name)
    if not raw:
        return None
    if not raw.isdigit():
        logger.warning("%s is set to a non-numeric value (%r) — treating as unset.", name, raw)
        return None
    return raw


DB_PATH = os.environ.get("DB_PATH", "/app/data/bot.db")
PORT = int(os.environ.get("PORT", "8080"))

# Seed defaults for a brand-new guild_settings row (first time a guild is seen).
CURRENCY_NAME = "Pink Slips"
CURRENCY_EMOJI = "\U0001f3c1"  # 🏁

INHOUSE_STARTING_MMR = 1000

# Optional first-run seed values, same semantics as the old monolith's config.py:
# only matter the very first time a guild's settings row is created.
VALORANT_UPDATES_CHANNEL_ID = _optional_id("VALORANT_UPDATES_CHANNEL_ID")
INHOUSE_STAFF_ROLE_ID = _optional_id("INHOUSE_STAFF_ROLE_ID")
INHOUSE_VOICE_CATEGORY_ID = _optional_id("INHOUSE_VOICE_CATEGORY_ID")
