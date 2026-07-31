"""valorant-service: owns HenrikDev integration -- linking, rank lookups,
match history, and the rank-up detection check. The gateway calls
POST /rankup-check on its own hourly tasks.loop and posts the actual
Discord announcement itself (this service has no Discord token/channel
concept -- it only reports "what happened").
"""
import asyncio
import logging

import aiohttp
from aiohttp import web

import config
from db_client import DBClient
from henrikdev_client import AccountNotFoundError, HenrikDevClient, HenrikDevError, rank_index
from stats import compute_grade, extract_player_match

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("valorant-service")

routes = web.RouteTableDef()


@routes.get("/healthz")
async def healthz(request):
    return web.json_response({"status": "ok"})

db = DBClient()
session: aiohttp.ClientSession | None = None
henrik: HenrikDevClient | None = None


@routes.post("/link")
async def link(request):
    body = await request.json()
    user_id, riot_name, riot_tag, region = body["user_id"], body["riot_name"], body["riot_tag"], body.get("region", "na")
    if region not in config.VALORANT_VALID_REGIONS:
        return web.json_response({"error": "invalid_region", "valid_regions": sorted(config.VALORANT_VALID_REGIONS)}, status=400)
    await db.ensure_user(user_id)
    await db.link(user_id, riot_name, riot_tag, region)
    return web.json_response({"ok": True})


@routes.get("/rank/{user_id}")
async def rank(request):
    account = await db.get_account(request.match_info["user_id"])
    if account is None:
        return web.json_response({"found": False, "linked": False})

    try:
        mmr = await henrik.get_mmr(account["region"], account["riot_name"], account["riot_tag"])
    except AccountNotFoundError:
        logger.info("HenrikDev reports no such account for %s#%s (region %s)", account["riot_name"], account["riot_tag"], account["region"])
        return web.json_response({"found": False, "linked": True, "error": "account_not_found"})
    except HenrikDevError as e:
        logger.warning("HenrikDev rank lookup failed for %s#%s: %s", account["riot_name"], account["riot_tag"], e)
        return web.json_response({"found": False, "linked": True, "error": "unavailable"})

    current = mmr.get("current_data", {})
    tier = current.get("currenttierpatched", "Unranked")

    stats = {"games_played": 0, "kd": None, "avg_acs": None, "avg_adr": None, "headshot_pct": None, "grade": None}
    try:
        match_list = await henrik.get_matches(account["region"], account["riot_name"], account["riot_tag"], config.VALORANT_STATS_SAMPLE_SIZE)
    except HenrikDevError as e:
        logger.warning("HenrikDev match fetch for /rank stats failed for %s#%s: %s", account["riot_name"], account["riot_tag"], e)
        match_list = []

    puuid_name = f"{account['riot_name']}#{account['riot_tag']}".lower()
    all_parsed = [m for m in (extract_player_match(match, puuid_name) for match in match_list) if m is not None]
    # Deathmatch and similar non-round modes aren't comparable to
    # round-based K/D and ACS -- excluded from the aggregate entirely
    # rather than skewing it (see extract_player_match's round_based check).
    parsed = [m for m in all_parsed if m["acs"] is not None]
    if parsed:
        total_kills = sum(m["kills"] for m in parsed)
        total_deaths = sum(m["deaths"] for m in parsed)
        kd = total_kills / total_deaths if total_deaths else float(total_kills)
        avg_acs = sum(m["acs"] for m in parsed) / len(parsed)
        avg_adr = sum(m["adr"] for m in parsed) / len(parsed)
        avg_hs = sum(m["headshot_pct"] for m in parsed) / len(parsed)
        stats = {
            "games_played": len(parsed),
            "kd": round(kd, 2),
            "avg_acs": round(avg_acs, 1),
            "avg_adr": round(avg_adr, 1),
            "headshot_pct": round(avg_hs, 1),
            "grade": compute_grade(rank_index(tier), avg_acs, kd),
        }

    return web.json_response({
        "found": True,
        "linked": True,
        "tier": tier,
        "rr": current.get("ranking_in_tier", 0),
        "icon": (current.get("images") or {}).get("small"),
        "peak": (mmr.get("highest_rank") or {}).get("patched_tier"),
        **stats,
    })


@routes.get("/matches/{user_id}")
async def matches(request):
    account = await db.get_account(request.match_info["user_id"])
    if account is None:
        return web.json_response({"found": False, "linked": False})

    count = int(request.query.get("count", config.VALORANT_MATCHES_DEFAULT_COUNT))
    mode = request.query.get("mode")
    try:
        match_list = await henrik.get_matches(account["region"], account["riot_name"], account["riot_tag"], count, mode=mode)
    except AccountNotFoundError:
        logger.info("HenrikDev reports no such account for %s#%s (region %s)", account["riot_name"], account["riot_tag"], account["region"])
        return web.json_response({"found": False, "linked": True, "error": "account_not_found"})
    except HenrikDevError as e:
        logger.warning("HenrikDev match history failed for %s#%s: %s", account["riot_name"], account["riot_tag"], e)
        return web.json_response({"found": False, "linked": True, "error": "unavailable"})

    puuid_name = f"{account['riot_name']}#{account['riot_tag']}".lower()
    parsed = [m for m in (extract_player_match(match, puuid_name) for match in match_list) if m is not None]

    return web.json_response({"found": True, "linked": True, "matches": parsed})


@routes.post("/rankup-check")
async def rankup_check(request):
    accounts = await db.list_accounts()
    events = []
    for account in accounts:
        try:
            mmr = await henrik.get_mmr(account["region"], account["riot_name"], account["riot_tag"])
            current = mmr.get("current_data", {})
            new_tier = current.get("currenttierpatched")
            new_rr = current.get("ranking_in_tier")

            old_index = rank_index(account["last_known_rank"])
            new_index = rank_index(new_tier)

            if old_index is not None and new_index is not None and new_index > old_index:
                tiers_gained = new_index - old_index
                reward = config.VALORANT_RANKUP_REWARD_PER_TIER * tiers_gained
                await db.adjust_balance(account["user_id"], reward, "valorant_rankup")
                events.append({"user_id": account["user_id"], "new_tier": new_tier, "reward": reward})

            await db.update_rank(account["user_id"], new_tier, new_rr)
        except HenrikDevError as e:
            logger.warning("Rank-up poll failed for %s#%s: %s", account["riot_name"], account["riot_tag"], e)
        except Exception:
            logger.exception("Unexpected error polling %s#%s", account["riot_name"], account["riot_tag"])
        await asyncio.sleep(config.VALORANT_RANKUP_REQUEST_DELAY_SECONDS)

    return web.json_response({"events": events})


async def on_startup(app):
    global session, henrik
    await db.start()
    session = aiohttp.ClientSession()
    henrik = HenrikDevClient(session)
    logger.info("valorant-service ready, DB_SERVICE_URL=%s", config.DB_SERVICE_URL)


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
