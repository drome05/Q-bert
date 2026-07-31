"""inhouse-service: owns the queue, MMR, match persistence, Elo math,
voting/resolution decisions, and parimutuel prediction settlement.

Discord-specific side effects (creating match channels, moving members,
posting embeds, the interactive captain-select/draft UI) stay in the
gateway's cogs/inhouse.py, since only the gateway holds the Discord
connection. The gateway calls into this service at each decision point
and acts on the plain-JSON result.
"""
import logging

from aiohttp import web

import config
import elo
from db_client import DBClient, InsufficientBalance

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("inhouse-service")

routes = web.RouteTableDef()


@routes.get("/healthz")
async def healthz(request):
    return web.json_response({"status": "ok"})

db = DBClient()


# --- queue -------------------------------------------------------------------

@routes.get("/mmr/{guild_id}/{user_id}")
async def get_mmr(request):
    guild_id, user_id = request.match_info["guild_id"], request.match_info["user_id"]
    await db.ensure_user(user_id)
    return web.json_response(await db.get_mmr(guild_id, user_id))


@routes.get("/mmr/{guild_id}")
async def mmr_leaderboard(request):
    guild_id = request.match_info["guild_id"]
    limit = int(request.query.get("limit", "10"))
    return web.json_response(await db.mmr_leaderboard(guild_id, limit))


@routes.get("/active-match/{guild_id}/{user_id}")
async def active_match(request):
    return web.json_response(await db.active_match(request.match_info["guild_id"], request.match_info["user_id"]))


@routes.post("/queue/join")
async def queue_join(request):
    body = await request.json()
    guild_id, user_id = body["guild_id"], body["user_id"]
    await db.ensure_user(user_id)

    if await db.active_match(guild_id, user_id):
        return web.json_response({"error": "in_active_match"}, status=409)

    result = await db.queue_join(guild_id, user_id, config.INHOUSE_SIZE)
    if result["already_queued"]:
        return web.json_response({"error": "already_queued"}, status=409)
    return web.json_response(result)


@routes.post("/queue/leave")
async def queue_leave(request):
    body = await request.json()
    ok = await db.queue_leave(body["guild_id"], body["user_id"])
    if not ok:
        return web.json_response({"error": "not_queued"}, status=404)
    return web.json_response({"ok": True})


@routes.get("/queue/{guild_id}")
async def queue_list(request):
    return web.json_response(await db.queue_list(request.match_info["guild_id"]))


# --- captain selection / draft helpers -----------------------------------------

@routes.post("/captains/highest-mmr")
async def highest_mmr_captains(request):
    body = await request.json()
    guild_id, pool = body["guild_id"], body["pool"]
    mmrs = []
    for uid in pool:
        row = await db.get_mmr(guild_id, uid)
        mmrs.append((uid, row["mmr"]))
    mmrs.sort(key=lambda t: t[1], reverse=True)
    return web.json_response({"captains": [mmrs[0][0], mmrs[1][0]]})


@routes.post("/draft/balanced-mmr")
async def balanced_mmr_draft(request):
    """Sorts the pool by MMR descending and snake-assigns using
    SNAKE_PICK_ORDER, so the two teams end up close in total MMR without a
    full combinatorial search over all possible splits."""
    body = await request.json()
    guild_id, pool = body["guild_id"], body["pool"]
    mmrs = {}
    for uid in pool:
        row = await db.get_mmr(guild_id, uid)
        mmrs[uid] = row["mmr"]
    ranked = sorted(pool, key=lambda p: mmrs[p], reverse=True)
    team_a, team_b = [], []
    for player, team in zip(ranked, config.SNAKE_PICK_ORDER):
        (team_a if team == "A" else team_b).append(player)
    return web.json_response({"team_a": team_a, "team_b": team_b})


# --- match creation ------------------------------------------------------------

@routes.post("/matches")
async def create_match(request):
    body = await request.json()
    result = await db.create_match(body["guild_id"], body["captain_a"], body["captain_b"], body["draft_method"], body["team_a"], body["team_b"], body.get("map"))
    return web.json_response(result)


@routes.get("/matches/{match_id}")
async def get_match(request):
    match = await db.get_match(request.match_info["match_id"])
    if match is None:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response(match)


@routes.post("/matches/{match_id}/channels")
async def set_channels(request):
    body = await request.json()
    await db.set_channels(request.match_info["match_id"], body["text_channel_id"], body["voice_channel_a_id"], body["voice_channel_b_id"])
    return web.json_response({"ok": True})


@routes.get("/matches/{match_id}/players")
async def get_players(request):
    return web.json_response(await db.get_match_players(request.match_info["match_id"]))


# --- substitutions ---------------------------------------------------------

@routes.post("/matches/{match_id}/substitute")
async def substitute(request):
    match_id = request.match_info["match_id"]
    body = await request.json()
    match = await db.get_match(match_id)
    if match is None:
        return web.json_response({"error": "not_found"}, status=404)
    if match["status"] != "in_progress":
        return web.json_response({"error": "not_in_progress"}, status=409)

    caller_id, player_out, player_in = body["caller_id"], body["player_out"], body["player_in"]
    if caller_id not in (match["captain_a"], match["captain_b"]) and not body.get("caller_is_staff"):
        return web.json_response({"error": "not_authorized"}, status=403)

    players = await db.get_match_players(match_id)
    out_row = next((p for p in players if p["user_id"] == player_out), None)
    if out_row is None:
        return web.json_response({"error": "player_out_not_on_roster"}, status=404)
    if any(p["user_id"] == player_in for p in players):
        return web.json_response({"error": "player_in_already_on_roster"}, status=409)
    if await db.active_match(match["guild_id"], player_in):
        return web.json_response({"error": "player_in_already_active"}, status=409)

    await db.ensure_user(player_in)
    result = await db.substitute(match_id, player_out, player_in, out_row["team"], caller_id)
    return web.json_response({"ok": True, "team": out_row["team"], "mmr_before": result["mmr_before"]})


# --- predictions -------------------------------------------------------------

@routes.post("/matches/{match_id}/predict")
async def predict(request):
    match_id = request.match_info["match_id"]
    body = await request.json()
    user_id, team, amount = body["user_id"], body["team"], body["amount"]

    match = await db.get_match(match_id)
    if match is None:
        return web.json_response({"error": "not_found"}, status=404)
    if match["status"] != "in_progress":
        return web.json_response({"error": "predictions_closed"}, status=409)

    players = await db.get_match_players(match_id)
    if any(p["user_id"] == user_id for p in players):
        return web.json_response({"error": "cannot_predict_own_match"}, status=403)

    await db.ensure_user(user_id)
    try:
        await db.adjust_balance(match["guild_id"], user_id, -amount, "inhouse_predict")
    except InsufficientBalance:
        return web.json_response({"error": "insufficient_balance"}, status=409)

    ok = await db.predict(match_id, user_id, team, amount)
    if not ok:
        await db.adjust_balance(match["guild_id"], user_id, amount, "inhouse_predict")
        return web.json_response({"error": "duplicate_prediction"}, status=409)
    return web.json_response({"ok": True})


async def _settle_predictions(match_id, winning_team):
    losing_team = "B" if winning_team == "A" else "A"
    predictions = await db.get_predictions(match_id)
    winners = [p for p in predictions if p["predicted_team"] == winning_team]
    losers = [p for p in predictions if p["predicted_team"] == losing_team]
    winner_pool = sum(p["amount"] for p in winners)
    loser_pool = sum(p["amount"] for p in losers)

    payouts = []
    if winner_pool > 0:
        for p in winners:
            share = p["amount"] + round(loser_pool * (p["amount"] / winner_pool))
            payouts.append({"user_id": p["user_id"], "payout": share})
        for p in losers:
            payouts.append({"user_id": p["user_id"], "payout": 0})
    else:
        # Nobody predicted the winning side -- refund losers so no coins are
        # created or destroyed (not covered explicitly by the original spec).
        for p in losers:
            payouts.append({"user_id": p["user_id"], "payout": p["amount"]})

    if payouts:
        await db.settle_predictions(match_id, payouts)


async def _unsettle_predictions(match_id):
    await db.unsettle_predictions(match_id)


# --- finish / voting -----------------------------------------------------------

@routes.post("/matches/{match_id}/start-voting")
async def start_voting(request):
    match_id = request.match_info["match_id"]
    body = await request.json()
    match = await db.get_match(match_id)
    if match is None:
        return web.json_response({"error": "not_found"}, status=404)
    if match["status"] != "in_progress":
        return web.json_response({"error": "not_in_progress"}, status=409)

    players = await db.get_match_players(match_id)
    roster = {p["user_id"]: p["team"] for p in players}
    if body["user_id"] not in roster:
        return web.json_response({"error": "not_on_roster"}, status=403)

    await db.set_status(match_id, "voting")
    return web.json_response({"roster": roster})


async def _apply_resolution(match_id, winning_team, reported_by):
    players = await db.get_match_players(match_id)
    team_a = [p for p in players if p["team"] == "A"]
    team_b = [p for p in players if p["team"] == "B"]
    avg_a = sum(p["mmr_before"] for p in team_a) / len(team_a)
    avg_b = sum(p["mmr_before"] for p in team_b) / len(team_b)

    player_results = []
    for p in team_a + team_b:
        opp_avg = avg_b if p["team"] == "A" else avg_a
        won = p["team"] == winning_team
        delta = elo.mmr_delta(p["mmr_before"], opp_avg, won, config.INHOUSE_ELO_K_FACTOR)
        reward = config.INHOUSE_WIN_REWARD if won else config.INHOUSE_LOSS_REWARD
        player_results.append({"user_id": p["user_id"], "mmr_before": p["mmr_before"], "delta": delta, "won": won, "coin_reward": reward})

    await db.resolve(match_id, winning_team, reported_by, player_results)
    await _settle_predictions(match_id, winning_team)


@routes.post("/matches/{match_id}/vote")
async def vote(request):
    match_id = request.match_info["match_id"]
    body = await request.json()
    user_id, team = body["user_id"], body["team"]

    match = await db.get_match(match_id)
    if match is None or match["status"] != "voting":
        return web.json_response({"error": "not_voting"}, status=409)

    players = await db.get_match_players(match_id)
    roster = {p["user_id"]: p["team"] for p in players}
    if user_id not in roster:
        return web.json_response({"error": "not_on_roster"}, status=403)

    tally = await db.vote(match_id, user_id, team)
    votes_a, votes_b, total = tally["votes_a"], tally["votes_b"], tally["total"]

    if votes_a >= config.INHOUSE_FINISH_MAJORITY_VOTES or votes_b >= config.INHOUSE_FINISH_MAJORITY_VOTES:
        winning_team = "A" if votes_a > votes_b else "B"
        await _apply_resolution(match_id, winning_team, user_id)
        return web.json_response({"outcome": "resolved", "winning_team": winning_team, "votes_a": votes_a, "votes_b": votes_b})

    if total >= len(roster):
        await db.set_status(match_id, "disputed")
        return web.json_response({"outcome": "disputed", "votes_a": votes_a, "votes_b": votes_b})

    return web.json_response({"outcome": "pending", "votes_a": votes_a, "votes_b": votes_b})


@routes.post("/matches/{match_id}/timeout-check")
async def timeout_check(request):
    match_id = request.match_info["match_id"]
    match = await db.get_match(match_id)
    if match and match["status"] == "voting":
        await db.set_status(match_id, "disputed")
        return web.json_response({"disputed": True})
    return web.json_response({"disputed": False})


@routes.post("/matches/{match_id}/override")
async def override(request):
    match_id = request.match_info["match_id"]
    body = await request.json()
    winning_team, reported_by = body["winning_team"], body["reported_by"]

    match = await db.get_match(match_id)
    if match is None:
        return web.json_response({"error": "not_found"}, status=404)

    if match["status"] == "completed":
        await db.revert(match_id, match["winning_team"], config.INHOUSE_WIN_REWARD, config.INHOUSE_LOSS_REWARD)
        await _unsettle_predictions(match_id)

    await _apply_resolution(match_id, winning_team, reported_by)
    return web.json_response({"ok": True})


@routes.post("/matches/{match_id}/cancel")
async def cancel(request):
    match_id = request.match_info["match_id"]
    match = await db.get_match(match_id)
    if match is None:
        return web.json_response({"error": "not_found"}, status=404)
    if match["status"] == "completed":
        return web.json_response({"error": "already_completed"}, status=409)
    await db.set_status(match_id, "cancelled")
    return web.json_response({"ok": True})


# --- stats / history / flags / sweep --------------------------------------------

@routes.get("/matches/{match_id}/flags")
async def flags(request):
    return web.json_response(await db.get_flags(request.match_info["match_id"]))


@routes.get("/leaderboard/{guild_id}")
async def leaderboard(request):
    guild_id = request.match_info["guild_id"]
    limit = int(request.query.get("limit", "10"))
    return web.json_response(await db.mmr_leaderboard(guild_id, limit))


@routes.get("/stats/{guild_id}/{user_id}")
async def stats(request):
    guild_id, user_id = request.match_info["guild_id"], request.match_info["user_id"]
    mmr_row = await db.get_mmr(guild_id, user_id)
    recent = await db.get_stats(guild_id, user_id)
    return web.json_response({"mmr": mmr_row, "recent": recent})


@routes.get("/history/{guild_id}/{user_id}")
async def history(request):
    return web.json_response(await db.get_history(request.match_info["guild_id"], request.match_info["user_id"]))


@routes.get("/sweep-candidates")
async def sweep_candidates(request):
    max_age_hours = int(request.query.get("max_age_hours", "3"))
    return web.json_response(await db.sweep_candidates(max_age_hours))


@routes.post("/matches/{match_id}/clear-channels")
async def clear_channels(request):
    await db.clear_channels(request.match_info["match_id"])
    return web.json_response({"ok": True})


async def on_startup(app):
    await db.start()
    logger.info("inhouse-service ready, DB_SERVICE_URL=%s", config.DB_SERVICE_URL)


async def on_cleanup(app):
    await db.close()


def create_app():
    app = web.Application()
    app.add_routes(routes)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), port=config.PORT)
