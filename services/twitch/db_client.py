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

    async def link(self, user_id: str, twitch_username: str):
        async with self.session.post(f"{config.DB_SERVICE_URL}/twitch/accounts", json={"user_id": user_id, "twitch_username": twitch_username}) as resp:
            await resp.json()

    async def unlink(self, user_id: str) -> bool:
        async with self.session.delete(f"{config.DB_SERVICE_URL}/twitch/accounts/{user_id}") as resp:
            return resp.status == 200

    async def get_account(self, user_id: str) -> dict | None:
        async with self.session.get(f"{config.DB_SERVICE_URL}/twitch/accounts/{user_id}") as resp:
            if resp.status == 404:
                return None
            return await resp.json()

    async def list_accounts(self) -> list:
        async with self.session.get(f"{config.DB_SERVICE_URL}/twitch/accounts") as resp:
            return await resp.json()

    async def update_status(self, user_id: str, is_live: bool, last_stream_id: str | None):
        async with self.session.post(
            f"{config.DB_SERVICE_URL}/twitch/accounts/{user_id}/status",
            json={"is_live": is_live, "last_stream_id": last_stream_id},
        ) as resp:
            await resp.json()
