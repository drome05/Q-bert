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

    async def adjust_balance(self, user_id: str, amount: int, reason: str) -> int:
        async with self.session.post(
            f"{config.DB_SERVICE_URL}/economy/adjust",
            json={"user_id": user_id, "amount": amount, "reason": reason},
        ) as resp:
            data = await resp.json()
            if resp.status == 409:
                raise InsufficientBalance()
            return data["new_balance"]
