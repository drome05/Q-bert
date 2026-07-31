import aiohttp

import config


class InsufficientBalance(Exception):
    pass


class DBClient:
    def __init__(self):
        self.session: aiohttp.ClientSession | None = None

    async def start(self):
        self.session = aiohttp.ClientSession()

    async def close(self):
        if self.session:
            await self.session.close()

    async def ensure_user(self, user_id: str):
        async with self.session.post(f"{config.DB_SERVICE_URL}/users/ensure", json={"user_id": user_id}) as resp:
            await resp.json()

    async def get_economy(self, guild_id: str, user_id: str) -> dict:
        async with self.session.get(f"{config.DB_SERVICE_URL}/economy/{guild_id}/{user_id}") as resp:
            return await resp.json()

    async def adjust_balance(self, guild_id: str, user_id: str, amount: int, reason: str, allow_negative: bool = False) -> int:
        async with self.session.post(
            f"{config.DB_SERVICE_URL}/economy/adjust",
            json={"guild_id": guild_id, "user_id": user_id, "amount": amount, "reason": reason, "allow_negative": allow_negative},
        ) as resp:
            data = await resp.json()
            if resp.status == 409:
                raise InsufficientBalance()
            return data["new_balance"]

    async def set_cooldown(self, guild_id: str, user_id: str, column: str):
        async with self.session.post(f"{config.DB_SERVICE_URL}/economy/{guild_id}/{user_id}/cooldown", json={"column": column}) as resp:
            await resp.json()

    async def leaderboard(self, guild_id: str, limit: int = 10) -> list:
        async with self.session.get(f"{config.DB_SERVICE_URL}/economy/{guild_id}", params={"limit": limit}) as resp:
            return await resp.json()
