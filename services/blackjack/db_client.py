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

    async def get_balance(self, guild_id: str, user_id: str) -> int:
        async with self.session.get(f"{config.DB_SERVICE_URL}/economy/{guild_id}/{user_id}") as resp:
            data = await resp.json()
            return data["balance"]

    async def adjust_balance(self, guild_id: str, user_id: str, amount: int, reason: str) -> int:
        async with self.session.post(
            f"{config.DB_SERVICE_URL}/economy/adjust",
            json={"guild_id": guild_id, "user_id": user_id, "amount": amount, "reason": reason},
        ) as resp:
            data = await resp.json()
            if resp.status == 409:
                raise InsufficientBalance()
            return data["new_balance"]
