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

    async def _get(self, path: str, params: dict | None = None) -> dict:
        async with self.session.get(f"{config.DB_SERVICE_URL}{path}", params=params) as resp:
            return await resp.json(), resp.status

    async def _post(self, path: str, json: dict | None = None) -> tuple[dict, int]:
        async with self.session.post(f"{config.DB_SERVICE_URL}{path}", json=json or {}) as resp:
            return await resp.json(), resp.status

    async def _delete(self, path: str) -> tuple[dict, int]:
        async with self.session.delete(f"{config.DB_SERVICE_URL}{path}") as resp:
            return await resp.json(), resp.status

    async def ensure_user(self, user_id: str):
        await self._post("/users/ensure", {"user_id": user_id})

    async def get_mmr(self, guild_id: str, user_id: str) -> dict:
        data, _ = await self._get(f"/inhouse/mmr/{guild_id}/{user_id}")
        return data

    async def mmr_leaderboard(self, guild_id: str, limit: int = 10) -> list:
        data, _ = await self._get(f"/inhouse/mmr/{guild_id}", {"limit": limit})
        return data

    async def active_match(self, guild_id: str, user_id: str) -> dict | None:
        data, _ = await self._get(f"/inhouse/active-match/{guild_id}/{user_id}")
        return data

    async def queue_join(self, guild_id: str, user_id: str, queue_size: int) -> dict:
        data, _ = await self._post("/inhouse/queue/join", {"guild_id": guild_id, "user_id": user_id, "queue_size": queue_size})
        return data

    async def queue_leave(self, guild_id: str, user_id: str) -> bool:
        _, status = await self._post("/inhouse/queue/leave", {"guild_id": guild_id, "user_id": user_id})
        return status == 200

    async def queue_list(self, guild_id: str) -> list:
        data, _ = await self._get(f"/inhouse/queue/{guild_id}")
        return data

    async def create_match(self, guild_id: str, captain_a: str, captain_b: str, draft_method: str, team_a: list, team_b: list, map_name: str | None = None) -> dict:
        data, _ = await self._post("/inhouse/matches", {
            "guild_id": guild_id, "captain_a": captain_a, "captain_b": captain_b, "draft_method": draft_method,
            "team_a": team_a, "team_b": team_b, "map": map_name,
        })
        return data

    async def get_match(self, match_id) -> dict | None:
        data, status = await self._get(f"/inhouse/matches/{match_id}")
        return None if status == 404 else data

    async def set_channels(self, match_id, text_channel_id, voice_channel_a_id, voice_channel_b_id):
        await self._post(f"/inhouse/matches/{match_id}/channels", {
            "text_channel_id": text_channel_id, "voice_channel_a_id": voice_channel_a_id, "voice_channel_b_id": voice_channel_b_id,
        })

    async def set_status(self, match_id, status: str):
        await self._post(f"/inhouse/matches/{match_id}/status", {"status": status})

    async def get_match_players(self, match_id) -> list:
        data, _ = await self._get(f"/inhouse/matches/{match_id}/players")
        return data

    async def substitute(self, match_id, player_out, player_in, team, subbed_by) -> dict:
        data, _ = await self._post(f"/inhouse/matches/{match_id}/substitute", {
            "player_out": player_out, "player_in": player_in, "team": team, "subbed_by": subbed_by,
        })
        return data

    async def vote(self, match_id, user_id, team) -> dict:
        data, _ = await self._post(f"/inhouse/matches/{match_id}/vote", {"user_id": user_id, "team": team})
        return data

    async def get_votes(self, match_id) -> list:
        data, _ = await self._get(f"/inhouse/matches/{match_id}/votes")
        return data

    async def resolve(self, match_id, winning_team, reported_by, player_results):
        await self._post(f"/inhouse/matches/{match_id}/resolve", {
            "winning_team": winning_team, "reported_by": reported_by, "player_results": player_results,
        })

    async def revert(self, match_id, previous_winning_team, win_reward, loss_reward):
        await self._post(f"/inhouse/matches/{match_id}/revert", {
            "previous_winning_team": previous_winning_team, "win_reward": win_reward, "loss_reward": loss_reward,
        })

    async def predict(self, match_id, user_id, team, amount) -> bool:
        _, status = await self._post("/inhouse/predictions", {"match_id": match_id, "user_id": user_id, "team": team, "amount": amount})
        return status == 200

    async def get_predictions(self, match_id) -> list:
        data, _ = await self._get(f"/inhouse/matches/{match_id}/predictions")
        return data

    async def settle_predictions(self, match_id, payouts: list):
        await self._post(f"/inhouse/matches/{match_id}/predictions/settle", {"payouts": payouts})

    async def unsettle_predictions(self, match_id):
        await self._post(f"/inhouse/matches/{match_id}/predictions/unsettle", {})

    async def get_flags(self, match_id) -> list:
        data, _ = await self._get(f"/inhouse/matches/{match_id}/flags")
        return data

    async def get_history(self, guild_id, user_id) -> list:
        data, _ = await self._get(f"/inhouse/history/{guild_id}/{user_id}")
        return data

    async def get_stats(self, guild_id, user_id) -> list:
        data, _ = await self._get(f"/inhouse/stats/{guild_id}/{user_id}")
        return data

    async def sweep_candidates(self, max_age_hours: int) -> list:
        data, _ = await self._get("/inhouse/sweep-candidates", {"max_age_hours": max_age_hours})
        return data

    async def clear_channels(self, match_id):
        await self._post(f"/inhouse/matches/{match_id}/clear-channels")

    async def adjust_balance(self, guild_id: str, user_id: str, amount: int, reason: str):
        data, status = await self._post("/economy/adjust", {"guild_id": guild_id, "user_id": user_id, "amount": amount, "reason": reason})
        if status == 409:
            raise InsufficientBalance()
        return data["new_balance"]
