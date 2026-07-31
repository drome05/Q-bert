import aiohttp

import config


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

    async def link(self, guild_id: str, user_id: str, riot_name: str, riot_tag: str, region: str):
        async with self.session.post(
            f"{config.DB_SERVICE_URL}/valorant/accounts",
            json={"guild_id": guild_id, "user_id": user_id, "riot_name": riot_name, "riot_tag": riot_tag, "region": region},
        ) as resp:
            await resp.json()

    async def get_account(self, guild_id: str, user_id: str) -> dict | None:
        async with self.session.get(f"{config.DB_SERVICE_URL}/valorant/accounts/{guild_id}/{user_id}") as resp:
            if resp.status == 404:
                return None
            return await resp.json()

    async def list_accounts(self, guild_id: str) -> list:
        async with self.session.get(f"{config.DB_SERVICE_URL}/valorant/accounts/{guild_id}") as resp:
            return await resp.json()

    async def update_rank(self, guild_id: str, user_id: str, last_known_rank: str | None, last_known_rr: int | None):
        async with self.session.post(
            f"{config.DB_SERVICE_URL}/valorant/accounts/{guild_id}/{user_id}/rank",
            json={"last_known_rank": last_known_rank, "last_known_rr": last_known_rr},
        ) as resp:
            await resp.json()

    async def adjust_balance(self, guild_id: str, user_id: str, amount: int, reason: str):
        async with self.session.post(
            f"{config.DB_SERVICE_URL}/economy/adjust", json={"guild_id": guild_id, "user_id": user_id, "amount": amount, "reason": reason}
        ) as resp:
            return await resp.json()
