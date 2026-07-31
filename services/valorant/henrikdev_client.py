import json

import aiohttp

import config


class HenrikDevError(Exception):
    pass


class AccountNotFoundError(HenrikDevError):
    """The Riot ID genuinely doesn't exist (HenrikDev 404, error code 22) --
    distinct from a real outage/rate-limit, since retrying later can never
    fix this: the fix is re-linking with the correct name/tag."""
    pass


class HenrikDevClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.headers = {"Authorization": config.HENRIKDEV_API_KEY}

    async def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{config.HENRIKDEV_BASE_URL}{path}"
        async with self.session.get(url, headers=self.headers, params=params) as resp:
            if resp.status != 200:
                body = await resp.text()
                if resp.status == 404:
                    # HenrikDev's error code 22 specifically means "Account
                    # not found" -- other 404s (bad path, etc.) shouldn't be
                    # confused with a genuinely-nonexistent Riot ID.
                    try:
                        error_code = json.loads(body)["errors"][0]["code"]
                    except Exception:
                        error_code = None
                    if error_code == 22:
                        raise AccountNotFoundError(f"HenrikDev 404 for {path}: {body[:200]}")
                raise HenrikDevError(f"HenrikDev {resp.status} for {path}: {body[:200]}")
            return await resp.json()

    async def get_mmr(self, region: str, name: str, tag: str) -> dict:
        data = await self._get(f"/valorant/v2/mmr/{region}/{name}/{tag}")
        return data.get("data", {})

    async def get_matches(self, region: str, name: str, tag: str, size: int, mode: str | None = None) -> list:
        params = {"size": size}
        if mode:
            params["mode"] = mode
        data = await self._get(f"/valorant/v3/matches/{region}/{name}/{tag}", params=params)
        return data.get("data", [])


def rank_index(patched_tier: str | None) -> int | None:
    if not patched_tier:
        return None
    try:
        return config.RANK_ORDER.index(patched_tier)
    except ValueError:
        return None
