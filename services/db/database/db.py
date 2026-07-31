"""Singleton aiosqlite connection guarded by a single asyncio.Lock.

SQLite allows only one writer at a time; funneling every write through this
lock avoids "database is locked" errors under concurrent slash commands.
Each helper here commits its own short transaction so the DB stays
consistent even if the process is killed mid-operation (relevant on K8s,
where the pod can be rescheduled at any time).
"""
import asyncio
import os
from contextlib import asynccontextmanager

import aiosqlite

_connection: aiosqlite.Connection | None = None
_lock = asyncio.Lock()


async def connect(db_path: str) -> None:
    global _connection
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    _connection = await aiosqlite.connect(db_path)
    _connection.row_factory = aiosqlite.Row
    await _connection.execute("PRAGMA foreign_keys = ON")
    await _connection.commit()


async def close() -> None:
    if _connection is not None:
        await _connection.close()


@asynccontextmanager
async def transaction():
    """Acquire the write lock and yield the shared connection.

    Caller is responsible for calling commit() itself before the
    context exits (or rollback() on error) so partial writes never
    escape the lock.
    """
    async with _lock:
        try:
            yield _connection
        except Exception:
            await _connection.rollback()
            raise


async def ensure_user(user_id: str) -> None:
    """Insert the parent `users` row if it doesn't exist yet.

    Every other table FKs to users(user_id), so this must run before any
    first-time write for a given user.
    """
    async with transaction() as db:
        await db.execute(
            "INSERT INTO users (user_id) VALUES (?) ON CONFLICT(user_id) DO NOTHING",
            (user_id,),
        )
        await db.commit()


async def fetchone(query: str, params: tuple = ()) -> aiosqlite.Row | None:
    async with transaction() as db:
        cursor = await db.execute(query, params)
        return await cursor.fetchone()


async def fetchall(query: str, params: tuple = ()) -> list[aiosqlite.Row]:
    async with transaction() as db:
        cursor = await db.execute(query, params)
        return await cursor.fetchall()


async def execute(query: str, params: tuple = ()) -> None:
    async with transaction() as db:
        await db.execute(query, params)
        await db.commit()


class InsufficientBalance(Exception):
    pass


async def get_balance(user_id: str) -> int:
    row = await fetchone("SELECT balance FROM economy WHERE user_id = ?", (user_id,))
    return row["balance"] if row else 0


async def adjust_balance(user_id: str, amount: int, reason: str, *, allow_negative: bool = False) -> int:
    """Atomically adjust a user's balance and log the transaction.

    This is the single choke point every cog (economy, casino, inhouse) must
    use to touch coin balances, so every mutation is guaranteed to also
    produce an economy_transactions audit row. Returns the new balance.
    Raises InsufficientBalance if amount is negative and would drop the
    balance below zero (unless allow_negative is set, e.g. admin-adjust).
    """
    await ensure_user(user_id)
    async with transaction() as conn:
        await conn.execute(
            "INSERT INTO economy (user_id, balance) VALUES (?, 0) ON CONFLICT(user_id) DO NOTHING",
            (user_id,),
        )
        cursor = await conn.execute("SELECT balance FROM economy WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        current = row["balance"]
        new_balance = current + amount
        if new_balance < 0 and not allow_negative:
            raise InsufficientBalance(f"user {user_id} has {current}, cannot apply {amount}")
        await conn.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (new_balance, user_id))
        await conn.execute(
            "INSERT INTO economy_transactions (user_id, amount, reason) VALUES (?, ?, ?)",
            (user_id, amount, reason),
        )
        await conn.commit()
        return new_balance
