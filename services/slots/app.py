"""slots-service: owns the slot machine's rules/RNG (split out of the
former casino-service so each game is its own pod)."""
import logging
import random

from aiohttp import web

import config
from db_client import DBClient, InsufficientBalance

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("slots-service")

routes = web.RouteTableDef()
db = DBClient()


@routes.post("/spin")
async def spin(request):
    body = await request.json()
    user_id, bet = body["user_id"], body["bet"]
    try:
        await db.adjust_balance(user_id, -bet, "slots")
    except InsufficientBalance:
        return web.json_response({"error": "insufficient_balance"}, status=409)

    symbols = list(config.SLOTS_SYMBOLS.keys())
    weights = list(config.SLOTS_SYMBOLS.values())
    reels = random.choices(symbols, weights=weights, k=3)

    if reels[0] == reels[1] == reels[2]:
        if reels[0] == "7️⃣":
            multiplier = config.SLOTS_PAYOUTS["triple_seven"]
        elif reels[0] == "\U0001f48e":
            multiplier = config.SLOTS_PAYOUTS["triple_diamond"]
        else:
            multiplier = config.SLOTS_PAYOUTS["triple_other"]
    elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
        multiplier = config.SLOTS_PAYOUTS["any_pair"]
    else:
        multiplier = 0.0

    payout = round(bet * multiplier)
    new_balance = await db.get_balance(user_id)
    if payout > 0:
        new_balance = await db.adjust_balance(user_id, payout, "slots")
    return web.json_response({"reels": reels, "payout": payout, "new_balance": new_balance})


async def on_startup(app):
    await db.start()
    logger.info("slots-service ready, DB_SERVICE_URL=%s", config.DB_SERVICE_URL)


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
