"""twitch-service: owns Twitch Helix integration -- linking and live-status
checks. Like valorant-service, this has no Discord token/channel concept;
the gateway calls POST /live-check on its own scheduled loop and posts the
actual Discord announcement itself.

Auth is a server-to-server app access token (client_credentials) -- no
per-user OAuth or password handling.
"""
import logging

import aiohttp
from aiohttp import web

import config
from db_client import DBClient
from twitch_client import TwitchClient, TwitchError, thumbnail_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("twitch-service")

routes = web.RouteTableDef()


@routes.get("/healthz")
async def healthz(request):
    return web.json_response({"status": "ok"})

db = DBClient()
session: aiohttp.ClientSession | None = None
twitch: TwitchClient | None = None


def _not_configured():
    return web.json_response({"error": "not_configured"}, status=503)


def _stream_payload(stream: dict) -> dict:
    return {
        "title": stream.get("title") or "Live on Twitch!",
        "game_name": stream.get("game_name") or "something",
        "user_login": stream.get("user_login", ""),
        "viewer_count": stream.get("viewer_count", 0),
        "thumbnail_url": thumbnail_url(stream["thumbnail_url"]) if stream.get("thumbnail_url") else None,
    }


@routes.post("/link")
async def link(request):
    body = await request.json()
    guild_id, user_id, username = body["guild_id"], body["user_id"], body["twitch_username"].lstrip("@").strip().lower()
    await db.ensure_user(user_id)
    await db.link(guild_id, user_id, username)
    return web.json_response({"ok": True})


@routes.delete("/unlink/{guild_id}/{user_id}")
async def unlink(request):
    ok = await db.unlink(request.match_info["guild_id"], request.match_info["user_id"])
    if not ok:
        return web.json_response({"error": "not_linked"}, status=404)
    return web.json_response({"ok": True})


@routes.get("/accounts/{guild_id}")
async def accounts(request):
    return web.json_response(await db.list_accounts(request.match_info["guild_id"]))


@routes.get("/live-status/{guild_id}/{user_id}")
async def live_status(request):
    if not (config.TWITCH_CLIENT_ID and config.TWITCH_CLIENT_SECRET):
        return _not_configured()
    guild_id = request.match_info["guild_id"]
    account = await db.get_account(guild_id, request.match_info["user_id"])
    if account is None:
        return web.json_response({"linked": False})

    try:
        streams = await twitch.get_streams([account["twitch_username"]])
    except TwitchError as e:
        logger.warning("Twitch live-status lookup failed for %s: %s", account["twitch_username"], e)
        return web.json_response({"linked": True, "error": "unavailable"})

    if not streams:
        return web.json_response({"linked": True, "live": False})

    stream = streams[0]
    await db.update_status(guild_id, account["user_id"], True, stream["id"])
    return web.json_response({"linked": True, "live": True, "stream": _stream_payload(stream)})


@routes.post("/live-check")
async def live_check(request):
    if not (config.TWITCH_CLIENT_ID and config.TWITCH_CLIENT_SECRET):
        return _not_configured()

    body = await request.json()
    guild_id = body["guild_id"]
    accounts_list = await db.list_accounts(guild_id)
    if not accounts_list:
        return web.json_response({"events": []})

    try:
        streams = await twitch.get_streams([a["twitch_username"] for a in accounts_list])
    except TwitchError as e:
        logger.warning("Twitch live-check poll failed: %s", e)
        return web.json_response({"events": [], "error": "unavailable"})

    live_by_login = {s["user_login"].lower(): s for s in streams}
    events = []
    for account in accounts_list:
        stream = live_by_login.get(account["twitch_username"].lower())
        if stream:
            if stream["id"] != account["last_stream_id"]:
                events.append({"user_id": account["user_id"], "stream": _stream_payload(stream)})
            await db.update_status(guild_id, account["user_id"], True, stream["id"])
        else:
            await db.update_status(guild_id, account["user_id"], False, account["last_stream_id"])

    return web.json_response({"events": events})


async def on_startup(app):
    global session, twitch
    await db.start()
    session = aiohttp.ClientSession()
    twitch = TwitchClient(session)
    if not (config.TWITCH_CLIENT_ID and config.TWITCH_CLIENT_SECRET):
        logger.warning("TWITCH_CLIENT_ID/TWITCH_CLIENT_SECRET not set -- live-status endpoints will return 503 until configured.")
    logger.info("twitch-service ready, DB_SERVICE_URL=%s", config.DB_SERVICE_URL)


async def on_cleanup(app):
    await db.close()
    if session:
        await session.close()


def create_app():
    app = web.Application()
    app.add_routes(routes)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), port=config.PORT)
