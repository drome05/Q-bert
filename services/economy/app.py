"""economy-service: owns balances, cooldown claims, transfers, and the
leaderboard. The single choke point for all money movement across the
whole bot -- casino/valorant/inhouse services all call db-service's
/economy/adjust directly for their own domain's rewards/bets (kept there
rather than routed through this service, since that's a data-tier
operation, not a decision only economy-service should make), but this
service owns the actual economy *commands* (claim/give/leaderboard/admin).
"""
import logging
import time
from datetime import datetime, timezone

from aiohttp import web

import config
from db_client import DBClient, InsufficientBalance

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("economy-service")

routes = web.RouteTableDef()
db = DBClient()


def _to_unix(ts: str | None) -> float | None:
    if ts is None:
        return None
    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return dt.timestamp()


@routes.get("/balance/{user_id}")
async def balance(request):
    user_id = request.match_info["user_id"]
    await db.ensure_user(user_id)
    row = await db.get_economy(user_id)
    return web.json_response({"balance": row["balance"]})


@routes.post("/claim")
async def claim(request):
    body = await request.json()
    user_id, period = body["user_id"], body["period"]
    if period not in config.PERIODS:
        return web.json_response({"error": "invalid_period"}, status=400)
    amount, cooldown_seconds, column = config.PERIODS[period]

    await db.ensure_user(user_id)
    row = await db.get_economy(user_id)
    last_unix = _to_unix(row.get(column))
    if last_unix is not None:
        remaining = (last_unix + cooldown_seconds) - time.time()
        if remaining > 0:
            return web.json_response({"claimed": False, "remaining_seconds": remaining})

    new_balance = await db.adjust_balance(user_id, amount, period)
    await db.set_cooldown(user_id, column)
    return web.json_response({"claimed": True, "amount": amount, "new_balance": new_balance})


@routes.post("/give")
async def give(request):
    body = await request.json()
    giver_id, receiver_id, amount = body["giver_id"], body["receiver_id"], body["amount"]

    await db.ensure_user(giver_id)
    await db.ensure_user(receiver_id)
    try:
        await db.adjust_balance(giver_id, -amount, "give")
    except InsufficientBalance:
        return web.json_response({"error": "insufficient_balance"}, status=409)
    await db.adjust_balance(receiver_id, amount, "give")
    return web.json_response({"ok": True})


@routes.get("/leaderboard")
async def leaderboard(request):
    limit = int(request.query.get("limit", "10"))
    rows = await db.leaderboard(limit)
    return web.json_response(rows)


@routes.post("/admin-adjust")
async def admin_adjust(request):
    body = await request.json()
    user_id, amount, reason = body["user_id"], body["amount"], body["reason"]
    await db.ensure_user(user_id)
    new_balance = await db.adjust_balance(user_id, amount, "admin_adjust", allow_negative=True)
    logger.info("Admin-adjusted %s by %d (%s). New balance: %d", user_id, amount, reason, new_balance)
    return web.json_response({"new_balance": new_balance})


async def on_startup(app):
    await db.start()
    logger.info("economy-service ready, DB_SERVICE_URL=%s", config.DB_SERVICE_URL)


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
