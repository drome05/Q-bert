"""coinflip-service: resolves 1v1 coin wagers (split out of the former
casino-service so each game is its own pod). The pending-challenge state
(who challenged whom, before Accept is clicked) stays in the gateway's
Discord View -- this service only handles the atomic "resolve" step."""
import logging
import random

from aiohttp import web

from db_client import DBClient, InsufficientBalance
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("coinflip-service")

routes = web.RouteTableDef()


@routes.get("/healthz")
async def healthz(request):
    return web.json_response({"status": "ok"})

db = DBClient()


@routes.post("/resolve")
async def resolve(request):
    body = await request.json()
    challenger_id, opponent_id, amount = body["challenger_id"], body["opponent_id"], body["amount"]

    try:
        await db.adjust_balance(challenger_id, -amount, "coinflip")
    except InsufficientBalance:
        return web.json_response({"error": "challenger_insufficient_balance"}, status=409)
    try:
        await db.adjust_balance(opponent_id, -amount, "coinflip")
    except InsufficientBalance:
        await db.adjust_balance(challenger_id, amount, "coinflip")
        return web.json_response({"error": "opponent_insufficient_balance"}, status=409)

    winner_id, loser_id = random.choice([(challenger_id, opponent_id), (opponent_id, challenger_id)])
    await db.adjust_balance(winner_id, amount * 2, "coinflip")
    return web.json_response({"winner_id": winner_id, "loser_id": loser_id, "payout": amount * 2})


async def on_startup(app):
    await db.start()
    logger.info("coinflip-service ready, DB_SERVICE_URL=%s", config.DB_SERVICE_URL)


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
