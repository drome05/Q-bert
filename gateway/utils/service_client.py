"""Shared HTTP client for the gateway calling into backend services.
The gateway is the only process holding the Discord token/connection --
every cog now talks to its backend service over plain JSON instead of
touching a database or business logic directly.
"""
import aiohttp


class ServiceError(Exception):
    def __init__(self, status: int, body: dict):
        self.status = status
        self.body = body
        super().__init__(f"service call failed ({status}): {body}")


class ServiceClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def start(self):
        self._session = aiohttp.ClientSession()

    async def close(self):
        if self._session:
            await self._session.close()

    async def request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        async with self._session.request(method, url, **kwargs) as resp:
            body = await resp.json()
            if resp.status >= 400:
                raise ServiceError(resp.status, body)
            return body

    async def get(self, path: str, **kwargs) -> dict:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, json: dict | None = None, **kwargs) -> dict:
        return await self.request("POST", path, json=json or {}, **kwargs)

    async def delete(self, path: str, **kwargs) -> dict:
        return await self.request("DELETE", path, **kwargs)
