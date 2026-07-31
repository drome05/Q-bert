"""One-off migration: adds guild_id to the primary key of tables that were
previously keyed by user_id alone, backfilling every existing row with the
one guild this bot has run in so far. Ahead of per-guild data isolation --
see README's "Known limitations" / the multi-guild round for why.

Run manually once against the live db-service pod (NOT part of app startup,
unlike the plain-additive-column migrations in database/init_db.py -- this
one changes a PRIMARY KEY, which SQLite can't do via ALTER TABLE):

    kubectl exec -n data deploy/db-service -- python3 migrate_guild_id.py <guild_id>

Uses autocommit (isolation_level=None) throughout -- Python's sqlite3 module
has genuinely ambiguous transaction semantics around DDL (ALTER/CREATE/DROP
TABLE) that make a single deferred commit() across a rename+create+insert+
drop sequence unsafe (confirmed the hard way in testing: a batched version of
this script silently dropped a table's data). Every statement here commits
immediately and independently, and the original table is only ever dropped
*after* verifying the new table's row count matches exactly -- if it doesn't,
the script stops immediately and leaves the renamed original table
(`{table}_old_migration`) in place for manual inspection rather than guessing.

Safe to re-run: each table is checked for an existing guild_id column before
being touched, so anything already migrated is skipped.
"""
import sqlite3
import sys

DB_PATH = "/app/data/bot.db"

# (table, new CREATE TABLE statement, columns to copy from the old table in order)
TABLES = [
    (
        "economy",
        """CREATE TABLE economy (
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL REFERENCES users(user_id),
            balance INTEGER NOT NULL DEFAULT 0,
            last_daily TIMESTAMP,
            last_weekly TIMESTAMP,
            last_monthly TIMESTAMP,
            PRIMARY KEY (guild_id, user_id)
        )""",
        ["user_id", "balance", "last_daily", "last_weekly", "last_monthly"],
    ),
    (
        "valorant_accounts",
        """CREATE TABLE valorant_accounts (
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL REFERENCES users(user_id),
            riot_name TEXT NOT NULL,
            riot_tag TEXT NOT NULL,
            region TEXT NOT NULL DEFAULT 'na',
            last_known_rank TEXT,
            last_known_rr INTEGER,
            last_checked TIMESTAMP,
            PRIMARY KEY (guild_id, user_id)
        )""",
        ["user_id", "riot_name", "riot_tag", "region", "last_known_rank", "last_known_rr", "last_checked"],
    ),
    (
        "inhouse_mmr",
        """CREATE TABLE inhouse_mmr (
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL REFERENCES users(user_id),
            mmr INTEGER NOT NULL DEFAULT 1000,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )""",
        ["user_id", "mmr", "wins", "losses"],
    ),
    (
        "inhouse_queue",
        """CREATE TABLE inhouse_queue (
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL REFERENCES users(user_id),
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, user_id)
        )""",
        ["user_id", "joined_at"],
    ),
    (
        "twitch_accounts",
        """CREATE TABLE twitch_accounts (
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL REFERENCES users(user_id),
            twitch_username TEXT NOT NULL,
            is_live INTEGER NOT NULL DEFAULT 0,
            last_stream_id TEXT,
            last_checked TIMESTAMP,
            PRIMARY KEY (guild_id, user_id)
        )""",
        ["user_id", "twitch_username", "is_live", "last_stream_id", "last_checked"],
    ),
]


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)).fetchone() is not None


def migrate_table(conn: sqlite3.Connection, table: str, create_sql: str, old_columns: list[str], guild_id: str):
    if has_column(conn, table, "guild_id"):
        print(f"{table}: already migrated, skipping")
        return

    old_table = f"{table}_old_migration"
    if table_exists(conn, old_table):
        print(f"{table}: found leftover {old_table} from a previous incomplete run -- resolve manually (inspect + drop or restore) before re-running")
        raise SystemExit(1)

    before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    conn.execute(f"ALTER TABLE {table} RENAME TO {old_table}")
    conn.execute(create_sql)
    cols = ", ".join(old_columns)
    conn.execute(f"INSERT INTO {table} (guild_id, {cols}) SELECT ?, {cols} FROM {old_table}", (guild_id,))

    after = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if before != after:
        print(f"{table}: ROW COUNT MISMATCH ({before} -> {after}) -- NOT dropping {old_table}. Both tables left in place -- stopping.")
        raise SystemExit(1)

    # Only drop the renamed original once the copy is verified exact.
    conn.execute(f"DROP TABLE {old_table}")
    print(f"{table}: {before} -> {after} rows [OK]")


def main():
    if len(sys.argv) != 2:
        print("usage: migrate_guild_id.py <guild_id>")
        sys.exit(1)
    guild_id = sys.argv[1]

    # Autocommit: every statement lands immediately and independently, no
    # ambiguous transaction boundary around the DDL sequence above.
    conn = sqlite3.connect(DB_PATH, isolation_level=None)

    for table, create_sql, old_columns in TABLES:
        migrate_table(conn, table, create_sql, old_columns, guild_id)

    cur = conn.execute("UPDATE inhouse_matches SET guild_id = ? WHERE guild_id IS NULL", (guild_id,))
    print(f"inhouse_matches: backfilled {cur.rowcount} row(s) with no guild_id")

    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    main()
