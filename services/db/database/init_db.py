import os

import aiosqlite

from database import db

# Additive columns for tables that may already exist on disk from an earlier
# version of schema.sql. `CREATE TABLE IF NOT EXISTS` (in schema.sql) only
# helps brand-new tables -- it's a no-op against a table that already exists
# with an older, narrower set of columns, so upgrades need an explicit
# ALTER TABLE here. Safe to re-run: a duplicate-column error just means a
# previous run (or a fresh schema.sql CREATE TABLE) already added it.
COLUMN_MIGRATIONS = [
    ("guild_settings", "twitch_announcements_channel_id", "TEXT"),
    ("inhouse_matches", "map", "TEXT"),
    # NOT NULL in schema.sql for fresh installs, but ALTER TABLE ADD COLUMN
    # can't add a NOT NULL constraint to a non-empty table without a
    # DEFAULT -- migrate_guild_id.py backfills real values into any
    # existing rows right after this runs.
    ("inhouse_matches", "guild_id", "TEXT"),
    ("economy_transactions", "guild_id", "TEXT"),
]


async def init_db(db_path: str) -> None:
    await db.connect(db_path)
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r") as f:
        schema = f.read()
    async with db.transaction() as conn:
        await conn.executescript(schema)
        await conn.commit()

    async with db.transaction() as conn:
        for table, column, coltype in COLUMN_MIGRATIONS:
            try:
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            except aiosqlite.OperationalError as e:
                if "duplicate column name" not in str(e):
                    raise
        await conn.commit()
