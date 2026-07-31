"""blackjack-service: owns Blackjack's rules and in-memory hand state
(split out of the former casino-service so each game is its own pod)."""
import logging
import random

from aiohttp import web

import config
from db_client import DBClient, InsufficientBalance

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("blackjack-service")

routes = web.RouteTableDef()


@routes.get("/healthz")
async def healthz(request):
    return web.json_response({"status": "ok"})

db = DBClient()

RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["♠", "♥", "♦", "♣"]

# user_id -> game state dict, single-replica in-memory.
_games: dict[str, dict] = {}


def draw_card():
    return [random.choice(RANKS), random.choice(SUITS)]


def hand_value(cards: list[list[str]]) -> tuple[int, bool]:
    total, aces = 0, 0
    for rank, _ in cards:
        if rank == "A":
            total += 11
            aces += 1
        elif rank in ("J", "Q", "K"):
            total += 10
        else:
            total += int(rank)
    soft = aces > 0
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
        soft = aces > 0
    return total, soft


def is_blackjack(cards: list[list[str]]) -> bool:
    return len(cards) == 2 and hand_value(cards)[0] == 21


def _state_response(game: dict, finished: bool, result_text: str | None = None, new_balance: int | None = None):
    player_total, _ = hand_value(game["player_hand"])
    dealer_total, _ = hand_value(game["dealer_hand"])
    return {
        "finished": finished,
        "bet": game["bet"],
        "player_hand": game["player_hand"],
        "player_total": player_total,
        "dealer_hand": game["dealer_hand"] if finished else [game["dealer_hand"][0]],
        "dealer_total": dealer_total if finished else None,
        "can_double": game["can_double"] and not finished,
        "result_text": result_text,
        "new_balance": new_balance,
    }


async def _finish(user_id: str, outcome: str) -> dict:
    game = _games.pop(user_id)
    player_total, _ = hand_value(game["player_hand"])
    dealer_total, _ = hand_value(game["dealer_hand"])
    bet = game["bet"]
    new_balance = None

    if outcome == "player_bust":
        result_text = f"💥 Bust! You lose your **{bet:,}** bet."
    elif outcome == "player_blackjack":
        payout = round(bet * config.BLACKJACK_NATURAL_PAYOUT_MULTIPLIER)
        new_balance = await db.adjust_balance(user_id, payout, "blackjack")
        result_text = f"🂡 Blackjack! You win **{payout:,}**."
    elif outcome == "dealer_blackjack":
        result_text = "Dealer has blackjack. You lose your bet."
    elif outcome == "push":
        new_balance = await db.adjust_balance(user_id, bet, "blackjack")
        result_text = f"Push — your **{bet:,}** bet is returned."
    elif outcome == "dealer_bust":
        payout = round(bet * config.BLACKJACK_PAYOUT_MULTIPLIER)
        new_balance = await db.adjust_balance(user_id, payout, "blackjack")
        result_text = f"Dealer busts! You win **{payout:,}**."
    else:  # compare
        if player_total > dealer_total:
            payout = round(bet * config.BLACKJACK_PAYOUT_MULTIPLIER)
            new_balance = await db.adjust_balance(user_id, payout, "blackjack")
            result_text = f"You win **{payout:,}**!"
        elif player_total < dealer_total:
            result_text = f"You lose your **{bet:,}** bet."
        else:
            new_balance = await db.adjust_balance(user_id, bet, "blackjack")
            result_text = f"Push — your **{bet:,}** bet is returned."

    if new_balance is None:
        new_balance = await db.get_balance(user_id)
    return _state_response(game, finished=True, result_text=result_text, new_balance=new_balance)


async def _dealer_play_and_resolve(user_id: str) -> dict:
    game = _games[user_id]
    while True:
        total, _ = hand_value(game["dealer_hand"])
        if total >= config.BLACKJACK_DEALER_STANDS_ON:
            break
        game["dealer_hand"].append(draw_card())
    dealer_total, _ = hand_value(game["dealer_hand"])
    return await _finish(user_id, "dealer_bust" if dealer_total > 21 else "compare")


@routes.post("/start")
async def start(request):
    body = await request.json()
    user_id, bet = body["user_id"], body["bet"]

    if user_id in _games:
        return web.json_response({"error": "game_in_progress"}, status=409)
    try:
        await db.adjust_balance(user_id, -bet, "blackjack")
    except InsufficientBalance:
        return web.json_response({"error": "insufficient_balance"}, status=409)

    game = {"bet": bet, "player_hand": [draw_card(), draw_card()], "dealer_hand": [draw_card(), draw_card()], "can_double": True}
    _games[user_id] = game

    player_bj, dealer_bj = is_blackjack(game["player_hand"]), is_blackjack(game["dealer_hand"])
    if player_bj and dealer_bj:
        return web.json_response(await _finish(user_id, "push"))
    if player_bj:
        return web.json_response(await _finish(user_id, "player_blackjack"))
    if dealer_bj:
        return web.json_response(await _finish(user_id, "dealer_blackjack"))
    return web.json_response(_state_response(game, finished=False))


@routes.post("/hit")
async def hit(request):
    body = await request.json()
    user_id = body["user_id"]
    game = _games.get(user_id)
    if game is None:
        return web.json_response({"error": "no_game"}, status=404)
    game["can_double"] = False
    game["player_hand"].append(draw_card())
    total, _ = hand_value(game["player_hand"])
    if total > 21:
        return web.json_response(await _finish(user_id, "player_bust"))
    return web.json_response(_state_response(game, finished=False))


@routes.post("/stand")
async def stand(request):
    body = await request.json()
    user_id = body["user_id"]
    if user_id not in _games:
        return web.json_response({"error": "no_game"}, status=404)
    return web.json_response(await _dealer_play_and_resolve(user_id))


@routes.post("/double")
async def double(request):
    body = await request.json()
    user_id = body["user_id"]
    game = _games.get(user_id)
    if game is None:
        return web.json_response({"error": "no_game"}, status=404)
    if not game["can_double"]:
        return web.json_response({"error": "cannot_double"}, status=400)
    try:
        await db.adjust_balance(user_id, -game["bet"], "blackjack")
    except InsufficientBalance:
        return web.json_response({"error": "insufficient_balance"}, status=409)
    game["bet"] *= 2
    game["can_double"] = False
    game["player_hand"].append(draw_card())
    total, _ = hand_value(game["player_hand"])
    if total > 21:
        return web.json_response(await _finish(user_id, "player_bust"))
    return web.json_response(await _dealer_play_and_resolve(user_id))


async def on_startup(app):
    await db.start()
    logger.info("blackjack-service ready, DB_SERVICE_URL=%s", config.DB_SERVICE_URL)


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
