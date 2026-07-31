import time

import aiohttp

import config


class TwitchError(Exception):
    pass


class TwitchClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self._token: str | None = None
        self._token_expires_at: float = 0

    async def _get_token(self, force_refresh: bool = False) -> str:
        if not force_refresh and self._token and time.time() < self._token_expires_at:
            return self._token
        async with self.session.post(
            config.TWITCH_OAUTH_TOKEN_URL,
            params={"client_id": config.TWITCH_CLIENT_ID, "client_secret": config.TWITCH_CLIENT_SECRET, "grant_type": "client_credentials"},
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise TwitchError(f"Twitch token fetch failed ({resp.status}): {body[:200]}")
            data = await resp.json()
        self._token = data["access_token"]
        self._token_expires_at = time.time() + data.get("expires_in", 3600) - 60
        return self._token

    async def _get_streams_page(self, user_logins: list[str], retry: bool = True) -> list[dict]:
        token = await self._get_token()
        headers = {"Client-Id": config.TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}"}
        params = [("user_login", u) for u in user_logins] + [("first", "100")]
        async with self.session.get(f"{config.TWITCH_HELIX_BASE_URL}/streams", headers=headers, params=params) as resp:
            if resp.status == 401 and retry:
                await self._get_token(force_refresh=True)
                return await self._get_streams_page(user_logins, retry=False)
            if resp.status != 200:
                body = await resp.text()
                raise TwitchError(f"Twitch streams fetch failed ({resp.status}): {body[:200]}")
            data = await resp.json()
            return data.get("data", [])

    async def get_streams(self, user_logins: list[str]) -> list[dict]:
        if not user_logins:
            return []
        results = []
        for i in range(0, len(user_logins), 100):
            results.extend(await self._get_streams_page(user_logins[i:i + 100]))
        return results


def thumbnail_url(template: str, width: int = 440, height: int = 248) -> str:
    return template.replace("{width}", str(width)).replace("{height}", str(height))
