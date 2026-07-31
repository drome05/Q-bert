"""db-service: the only process that touches bot.db / the PVC directly.
Every other service (casino, economy, valorant, inhouse, twitch) and the
gateway's /settings cog call this over HTTP instead of importing
database/db.py in-process, since SQLite can't be written by multiple
processes and our PVC is ReadWriteOnce anyway.

Endpoints return plain JSON. aiosqlite.Row objects are converted via
`_row(...)`/`_rows(...)` before jsonify.
"""
import logging

import aiosqlite
from aiohttp import web

import config
from database import db, settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("db-service")

routes = web.RouteTableDef()


@routes.get("/healthz")
async def healthz(request):
    return web.json_response({"status": "ok"})


def _row(row) -> dict | None:
    return dict(row) if row is not None else None


def _rows(rows) -> list:
    return [dict(r) for r in rows]


# --- users -----------------------------------------------------------------

@routes.post("/users/ensure")
async def users_ensure(request):
    body = await request.json()
    await db.ensure_user(body["user_id"])
    return web.json_response({"ok": True})


# --- economy -----------------------------------------------------------------

@routes.get("/economy/{user_id}")
async def economy_get(request):
    user_id = request.match_info["user_id"]
    await db.ensure_user(user_id)
    await db.execute("INSERT INTO economy (user_id) VALUES (?) ON CONFLICT(user_id) DO NOTHING", (user_id,))
    row = await db.fetchone("SELECT * FROM economy WHERE user_id = ?", (user_id,))
    return web.json_response(_row(row))


@routes.post("/economy/adjust")
async def economy_adjust(request):
    body = await request.json()
    try:
        new_balance = await db.adjust_balance(
            body["user_id"], body["amount"], body["reason"], allow_negative=body.get("allow_negative", False)
        )
    except db.InsufficientBalance:
        return web.json_response({"error": "insufficient_balance"}, status=409)
    return web.json_response({"new_balance": new_balance})


@routes.post("/economy/{user_id}/cooldown")
async def economy_set_cooldown(request):
    user_id = request.match_info["user_id"]
    body = await request.json()
    column = body["column"]
    if column not in ("last_daily", "last_weekly", "last_monthly"):
        return web.json_response({"error": "invalid column"}, status=400)
    await db.execute(f"UPDATE economy SET {column} = CURRENT_TIMESTAMP WHERE user_id = ?", (user_id,))
    return web.json_response({"ok": True})


@routes.get("/economy")
async def economy_leaderboard(request):
    limit = int(request.query.get("limit", "10"))
    rows = await db.fetchall("SELECT user_id, balance FROM economy ORDER BY balance DESC LIMIT ?", (limit,))
    return web.json_response(_rows(rows))


# --- settings ----------------------------------------------------------------

@routes.get("/settings/{guild_id}")
async def settings_get(request):
    row = await settings.get(request.match_info["guild_id"])
    return web.json_response(_row(row))


@routes.post("/settings/{guild_id}/currency")
async def settings_currency(request):
    body = await request.json()
    await settings.set_currency(request.match_info["guild_id"], body["name"], body["emoji"])
    return web.json_response({"ok": True})


@routes.post("/settings/{guild_id}/valorant-channel")
async def settings_valorant_channel(request):
    body = await request.json()
    await settings.set_valorant_channel(request.match_info["guild_id"], body["channel_id"])
    return web.json_response({"ok": True})


@routes.post("/settings/{guild_id}/staff-role")
async def settings_staff_role(request):
    body = await request.json()
    await settings.set_staff_role(request.match_info["guild_id"], body["role_id"])
    return web.json_response({"ok": True})


@routes.post("/settings/{guild_id}/voice-category")
async def settings_voice_category(request):
    body = await request.json()
    await settings.set_voice_category(request.match_info["guild_id"], body["category_id"])
    return web.json_response({"ok": True})


@routes.post("/settings/{guild_id}/twitch-channel")
async def settings_twitch_channel(request):
    body = await request.json()
    await settings.set_twitch_channel(request.match_info["guild_id"], body["channel_id"])
    return web.json_response({"ok": True})


# --- valorant accounts ---------------------------------------------------------

@routes.post("/valorant/accounts")
async def valorant_link(request):
    body = await request.json()
    user_id = body["user_id"]
    await db.ensure_user(user_id)
    await db.execute(
        """INSERT INTO valorant_accounts (user_id, riot_name, riot_tag, region)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET riot_name=excluded.riot_name, riot_tag=excluded.riot_tag, region=excluded.region""",
        (user_id, body["riot_name"], body["riot_tag"], body["region"]),
    )
    return web.json_response({"ok": True})


@routes.get("/valorant/accounts/{user_id}")
async def valorant_get(request):
    row = await db.fetchone("SELECT * FROM valorant_accounts WHERE user_id = ?", (request.match_info["user_id"],))
    if row is None:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response(_row(row))


@routes.get("/valorant/accounts")
async def valorant_list(request):
    rows = await db.fetchall("SELECT * FROM valorant_accounts")
    return web.json_response(_rows(rows))


@routes.post("/valorant/accounts/{user_id}/rank")
async def valorant_update_rank(request):
    body = await request.json()
    await db.execute(
        "UPDATE valorant_accounts SET last_known_rank = ?, last_known_rr = ?, last_checked = CURRENT_TIMESTAMP WHERE user_id = ?",
        (body.get("last_known_rank"), body.get("last_known_rr"), request.match_info["user_id"]),
    )
    return web.json_response({"ok": True})


# --- twitch accounts -----------------------------------------------------------

@routes.post("/twitch/accounts")
async def twitch_link(request):
    body = await request.json()
    user_id = body["user_id"]
    await db.ensure_user(user_id)
    await db.execute(
        """INSERT INTO twitch_accounts (user_id, twitch_username) VALUES (?, ?)
           ON CONFLICT(user_id) DO UPDATE SET twitch_username = excluded.twitch_username""",
        (user_id, body["twitch_username"]),
    )
    return web.json_response({"ok": True})


@routes.delete("/twitch/accounts/{user_id}")
async def twitch_unlink(request):
    user_id = request.match_info["user_id"]
    existing = await db.fetchone("SELECT 1 FROM twitch_accounts WHERE user_id = ?", (user_id,))
    if not existing:
        return web.json_response({"error": "not_found"}, status=404)
    await db.execute("DELETE FROM twitch_accounts WHERE user_id = ?", (user_id,))
    return web.json_response({"ok": True})


@routes.get("/twitch/accounts/{user_id}")
async def twitch_get(request):
    row = await db.fetchone("SELECT * FROM twitch_accounts WHERE user_id = ?", (request.match_info["user_id"],))
    if row is None:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response(_row(row))


@routes.get("/twitch/accounts")
async def twitch_list(request):
    rows = await db.fetchall("SELECT * FROM twitch_accounts ORDER BY twitch_username")
    return web.json_response(_rows(rows))


@routes.post("/twitch/accounts/{user_id}/status")
async def twitch_update_status(request):
    body = await request.json()
    await db.execute(
        "UPDATE twitch_accounts SET is_live = ?, last_stream_id = ?, last_checked = CURRENT_TIMESTAMP WHERE user_id = ?",
        (1 if body["is_live"] else 0, body.get("last_stream_id"), request.match_info["user_id"]),
    )
    return web.json_response({"ok": True})


# --- inhouse: mmr / queue ------------------------------------------------------

@routes.get("/inhouse/mmr/{user_id}")
async def inhouse_get_mmr(request):
    user_id = request.match_info["user_id"]
    await db.ensure_user(user_id)
    await db.execute(
        "INSERT INTO inhouse_mmr (user_id, mmr) VALUES (?, ?) ON CONFLICT(user_id) DO NOTHING",
        (user_id, config.INHOUSE_STARTING_MMR),
    )
    row = await db.fetchone("SELECT * FROM inhouse_mmr WHERE user_id = ?", (user_id,))
    return web.json_response(_row(row))


@routes.get("/inhouse/mmr")
async def inhouse_leaderboard(request):
    limit = int(request.query.get("limit", "10"))
    rows = await db.fetchall("SELECT * FROM inhouse_mmr ORDER BY mmr DESC LIMIT ?", (limit,))
    return web.json_response(_rows(rows))


@routes.get("/inhouse/active-match/{user_id}")
async def inhouse_active_match(request):
    row = await db.fetchone(
        """SELECT m.* FROM inhouse_matches m
           JOIN inhouse_match_players p ON p.match_id = m.match_id
           WHERE p.user_id = ? AND m.status IN ('in_progress', 'voting')""",
        (request.match_info["user_id"],),
    )
    return web.json_response(_row(row))


@routes.post("/inhouse/queue/join")
async def inhouse_queue_join(request):
    body = await request.json()
    user_id = body["user_id"]
    queue_size = body["queue_size"]

    async with db.transaction() as conn:
        cursor = await conn.execute(
            "INSERT INTO inhouse_queue (user_id) VALUES (?) ON CONFLICT(user_id) DO NOTHING", (user_id,)
        )
        if cursor.rowcount == 0:
            await conn.commit()
            return web.json_response({"already_queued": True})

        count_cursor = await conn.execute("SELECT COUNT(*) as c FROM inhouse_queue")
        count = (await count_cursor.fetchone())["c"]

        drained = None
        if count >= queue_size:
            pool_cursor = await conn.execute(
                "SELECT user_id FROM inhouse_queue ORDER BY joined_at LIMIT ?", (queue_size,)
            )
            drained = [r["user_id"] for r in await pool_cursor.fetchall()]
            await conn.execute(
                f"DELETE FROM inhouse_queue WHERE user_id IN ({','.join('?' * len(drained))})", tuple(drained)
            )
        await conn.commit()

    return web.json_response({"already_queued": False, "count": count, "drained_players": drained})


@routes.post("/inhouse/queue/leave")
async def inhouse_queue_leave(request):
    body = await request.json()
    existing = await db.fetchone("SELECT 1 FROM inhouse_queue WHERE user_id = ?", (body["user_id"],))
    if not existing:
        return web.json_response({"error": "not_queued"}, status=404)
    await db.execute("DELETE FROM inhouse_queue WHERE user_id = ?", (body["user_id"],))
    return web.json_response({"ok": True})


@routes.get("/inhouse/queue")
async def inhouse_queue_list(request):
    rows = await db.fetchall("SELECT user_id FROM inhouse_queue ORDER BY joined_at")
    return web.json_response(_rows(rows))


# --- inhouse: matches ------------------------------------------------------

@routes.post("/inhouse/matches")
async def inhouse_create_match(request):
    """Atomically creates the match row + all 10 match_players rows, fetching
    fresh MMR for each player first. Mirrors the original finalize_match()."""
    body = await request.json()
    captain_a, captain_b = body["captain_a"], body["captain_b"]
    draft_method = body["draft_method"]
    team_a, team_b = body["team_a"], body["team_b"]
    map_name = body.get("map")

    mmr_before = {}
    for uid in team_a + team_b:
        await db.ensure_user(uid)
        await db.execute(
            "INSERT INTO inhouse_mmr (user_id, mmr) VALUES (?, ?) ON CONFLICT(user_id) DO NOTHING",
            (uid, config.INHOUSE_STARTING_MMR),
        )
        row = await db.fetchone("SELECT mmr FROM inhouse_mmr WHERE user_id = ?", (uid,))
        mmr_before[uid] = row["mmr"]

    async with db.transaction() as conn:
        cursor = await conn.execute(
            "INSERT INTO inhouse_matches (captain_a, captain_b, draft_method, status, map) VALUES (?, ?, ?, 'in_progress', ?)",
            (captain_a, captain_b, draft_method, map_name),
        )
        match_id = cursor.lastrowid
        for uid in team_a:
            await conn.execute(
                "INSERT INTO inhouse_match_players (match_id, user_id, team, mmr_before) VALUES (?, ?, 'A', ?)",
                (match_id, uid, mmr_before[uid]),
            )
        for uid in team_b:
            await conn.execute(
                "INSERT INTO inhouse_match_players (match_id, user_id, team, mmr_before) VALUES (?, ?, 'B', ?)",
                (match_id, uid, mmr_before[uid]),
            )
        await conn.commit()

    return web.json_response({"match_id": match_id, "mmr_before": mmr_before})


@routes.get("/inhouse/matches/{match_id}")
async def inhouse_get_match(request):
    row = await db.fetchone("SELECT * FROM inhouse_matches WHERE match_id = ?", (request.match_info["match_id"],))
    if row is None:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response(_row(row))


@routes.post("/inhouse/matches/{match_id}/channels")
async def inhouse_set_channels(request):
    body = await request.json()
    await db.execute(
        "UPDATE inhouse_matches SET text_channel_id = ?, voice_channel_a_id = ?, voice_channel_b_id = ? WHERE match_id = ?",
        (body["text_channel_id"], body["voice_channel_a_id"], body["voice_channel_b_id"], request.match_info["match_id"]),
    )
    return web.json_response({"ok": True})


@routes.post("/inhouse/matches/{match_id}/status")
async def inhouse_set_status(request):
    body = await request.json()
    await db.execute(
        "UPDATE inhouse_matches SET status = ? WHERE match_id = ?", (body["status"], request.match_info["match_id"])
    )
    return web.json_response({"ok": True})


@routes.get("/inhouse/matches/{match_id}/players")
async def inhouse_get_match_players(request):
    rows = await db.fetchall(
        "SELECT * FROM inhouse_match_players WHERE match_id = ?", (request.match_info["match_id"],)
    )
    return web.json_response(_rows(rows))


@routes.post("/inhouse/matches/{match_id}/substitute")
async def inhouse_substitute(request):
    match_id = request.match_info["match_id"]
    body = await request.json()
    player_out, player_in, team = body["player_out"], body["player_in"], body["team"]

    await db.ensure_user(player_in)
    await db.execute(
        "INSERT INTO inhouse_mmr (user_id, mmr) VALUES (?, ?) ON CONFLICT(user_id) DO NOTHING",
        (player_in, config.INHOUSE_STARTING_MMR),
    )
    mmr_row = await db.fetchone("SELECT mmr FROM inhouse_mmr WHERE user_id = ?", (player_in,))

    async with db.transaction() as conn:
        await conn.execute(
            "DELETE FROM inhouse_match_players WHERE match_id = ? AND user_id = ?", (match_id, player_out)
        )
        await conn.execute(
            "INSERT INTO inhouse_match_players (match_id, user_id, team, mmr_before) VALUES (?, ?, ?, ?)",
            (match_id, player_in, team, mmr_row["mmr"]),
        )
        await conn.execute(
            "INSERT INTO inhouse_substitutions (match_id, player_out, player_in, subbed_by) VALUES (?, ?, ?, ?)",
            (match_id, player_out, player_in, body["subbed_by"]),
        )
        await conn.commit()
    return web.json_response({"ok": True, "mmr_before": mmr_row["mmr"]})


@routes.post("/inhouse/matches/{match_id}/vote")
async def inhouse_vote(request):
    match_id = request.match_info["match_id"]
    body = await request.json()
    await db.execute(
        """INSERT INTO inhouse_match_votes (match_id, user_id, voted_team) VALUES (?, ?, ?)
           ON CONFLICT(match_id, user_id) DO UPDATE SET voted_team = excluded.voted_team, voted_at = CURRENT_TIMESTAMP""",
        (match_id, body["user_id"], body["team"]),
    )
    votes = await db.fetchall("SELECT voted_team FROM inhouse_match_votes WHERE match_id = ?", (match_id,))
    votes_a = sum(1 for v in votes if v["voted_team"] == "A")
    votes_b = sum(1 for v in votes if v["voted_team"] == "B")
    return web.json_response({"votes_a": votes_a, "votes_b": votes_b, "total": len(votes)})


@routes.get("/inhouse/matches/{match_id}/votes")
async def inhouse_get_votes(request):
    rows = await db.fetchall(
        "SELECT * FROM inhouse_match_votes WHERE match_id = ?", (request.match_info["match_id"],)
    )
    return web.json_response(_rows(rows))


@routes.post("/inhouse/matches/{match_id}/resolve")
async def inhouse_resolve(request):
    """Applies precomputed per-player MMR deltas + coin rewards atomically,
    and marks the match completed. Elo math itself lives in inhouse-service
    (the business-logic owner) -- this endpoint just persists the outcome,
    mirroring the original apply_match_resolution()."""
    match_id = request.match_info["match_id"]
    body = await request.json()
    winning_team, reported_by, player_results = body["winning_team"], body.get("reported_by"), body["player_results"]

    async with db.transaction() as conn:
        for p in player_results:
            new_mmr = p["mmr_before"] + p["delta"]
            await conn.execute(
                "UPDATE inhouse_match_players SET mmr_after = ? WHERE match_id = ? AND user_id = ?",
                (new_mmr, match_id, p["user_id"]),
            )
            counter = "wins" if p["won"] else "losses"
            await conn.execute(
                f"UPDATE inhouse_mmr SET mmr = mmr + ?, {counter} = {counter} + 1 WHERE user_id = ?",
                (p["delta"], p["user_id"]),
            )
            await conn.execute(
                "INSERT INTO economy (user_id, balance) VALUES (?, 0) ON CONFLICT(user_id) DO NOTHING", (p["user_id"],)
            )
            bal_cursor = await conn.execute("SELECT balance FROM economy WHERE user_id = ?", (p["user_id"],))
            current_balance = (await bal_cursor.fetchone())["balance"]
            await conn.execute(
                "UPDATE economy SET balance = balance + ? WHERE user_id = ?", (p["coin_reward"], p["user_id"])
            )
            await conn.execute(
                "INSERT INTO economy_transactions (user_id, amount, reason) VALUES (?, ?, ?)",
                (p["user_id"], p["coin_reward"], "inhouse_win" if p["won"] else "inhouse_loss"),
            )
        await conn.execute(
            "UPDATE inhouse_matches SET status = 'completed', winning_team = ?, reported_by = ? WHERE match_id = ?",
            (winning_team, reported_by, match_id),
        )
        await conn.commit()
    return web.json_response({"ok": True})


@routes.post("/inhouse/matches/{match_id}/revert")
async def inhouse_revert(request):
    """Reverses a previous resolve() using the stored mmr_after - mmr_before
    deltas, mirroring _revert_match_resolution()."""
    match_id = request.match_info["match_id"]
    body = await request.json()
    previous_winning_team = body["previous_winning_team"]
    win_reward, loss_reward = body["win_reward"], body["loss_reward"]

    players = await db.fetchall("SELECT * FROM inhouse_match_players WHERE match_id = ?", (match_id,))
    async with db.transaction() as conn:
        for p in players:
            if p["mmr_after"] is None:
                continue
            delta = p["mmr_after"] - p["mmr_before"]
            won = p["team"] == previous_winning_team
            counter = "wins" if won else "losses"
            await conn.execute(
                f"UPDATE inhouse_mmr SET mmr = mmr - ?, {counter} = {counter} - 1 WHERE user_id = ?",
                (delta, p["user_id"]),
            )
            reward = win_reward if won else loss_reward
            await conn.execute("UPDATE economy SET balance = balance - ? WHERE user_id = ?", (reward, p["user_id"]))
            await conn.execute(
                "INSERT INTO economy_transactions (user_id, amount, reason) VALUES (?, ?, ?)",
                (p["user_id"], -reward, "admin_adjust"),
            )
            await conn.execute(
                "UPDATE inhouse_match_players SET mmr_after = NULL WHERE match_id = ? AND user_id = ?",
                (match_id, p["user_id"]),
            )
        await conn.commit()

    settled = await db.fetchall("SELECT * FROM inhouse_predictions WHERE match_id = ? AND settled = 1", (match_id,))
    async with db.transaction() as conn:
        for r in settled:
            if r["payout"]:
                await conn.execute(
                    "INSERT INTO economy (user_id, balance) VALUES (?, 0) ON CONFLICT(user_id) DO NOTHING",
                    (r["user_id"],),
                )
                await conn.execute(
                    "UPDATE economy SET balance = balance - ? WHERE user_id = ?", (r["payout"], r["user_id"])
                )
                await conn.execute(
                    "INSERT INTO economy_transactions (user_id, amount, reason) VALUES (?, ?, ?)",
                    (r["user_id"], -r["payout"], "admin_adjust"),
                )
            await conn.execute(
                "UPDATE inhouse_predictions SET settled = 0, payout = NULL WHERE match_id = ? AND user_id = ?",
                (match_id, r["user_id"]),
            )
        await conn.commit()
    return web.json_response({"ok": True})


# --- inhouse: predictions ------------------------------------------------------

@routes.post("/inhouse/predictions")
async def inhouse_predict(request):
    body = await request.json()
    match_id, user_id, team, amount = body["match_id"], body["user_id"], body["team"], body["amount"]
    try:
        await db.execute(
            "INSERT INTO inhouse_predictions (match_id, user_id, predicted_team, amount) VALUES (?, ?, ?, ?)",
            (match_id, user_id, team, amount),
        )
    except aiosqlite.IntegrityError:
        return web.json_response({"error": "duplicate"}, status=409)
    return web.json_response({"ok": True})


@routes.get("/inhouse/matches/{match_id}/predictions")
async def inhouse_get_predictions(request):
    rows = await db.fetchall(
        "SELECT * FROM inhouse_predictions WHERE match_id = ?", (request.match_info["match_id"],)
    )
    return web.json_response(_rows(rows))


@routes.post("/inhouse/matches/{match_id}/predictions/settle")
async def inhouse_settle_predictions(request):
    """Applies precomputed parimutuel payouts (computed by inhouse-service)
    atomically and marks every prediction on this match settled."""
    match_id = request.match_info["match_id"]
    body = await request.json()
    payouts = body["payouts"]  # [{user_id, payout}]

    async with db.transaction() as conn:
        for p in payouts:
            if p["payout"]:
                await conn.execute(
                    "INSERT INTO economy (user_id, balance) VALUES (?, 0) ON CONFLICT(user_id) DO NOTHING",
                    (p["user_id"],),
                )
                await conn.execute(
                    "UPDATE economy SET balance = balance + ? WHERE user_id = ?", (p["payout"], p["user_id"])
                )
                await conn.execute(
                    "INSERT INTO economy_transactions (user_id, amount, reason) VALUES (?, ?, ?)",
                    (p["user_id"], p["payout"], "inhouse_predict"),
                )
            await conn.execute(
                "UPDATE inhouse_predictions SET settled = 1, payout = ? WHERE match_id = ? AND user_id = ?",
                (p["payout"], match_id, p["user_id"]),
            )
        await conn.commit()
    return web.json_response({"ok": True})


@routes.post("/inhouse/matches/{match_id}/predictions/unsettle")
async def inhouse_unsettle_predictions(request):
    """Reverses a previous settle() -- refunds each settled payout and
    resets settled=0/payout=NULL, mirroring the original monolith's
    _unsettle_predictions() so a later real settlement isn't left in a
    half-reversed state."""
    match_id = request.match_info["match_id"]
    settled = await db.fetchall("SELECT * FROM inhouse_predictions WHERE match_id = ? AND settled = 1", (match_id,))
    async with db.transaction() as conn:
        for r in settled:
            if r["payout"]:
                await conn.execute(
                    "INSERT INTO economy (user_id, balance) VALUES (?, 0) ON CONFLICT(user_id) DO NOTHING",
                    (r["user_id"],),
                )
                await conn.execute(
                    "UPDATE economy SET balance = balance - ? WHERE user_id = ?", (r["payout"], r["user_id"])
                )
                await conn.execute(
                    "INSERT INTO economy_transactions (user_id, amount, reason) VALUES (?, ?, ?)",
                    (r["user_id"], -r["payout"], "admin_adjust"),
                )
            await conn.execute(
                "UPDATE inhouse_predictions SET settled = 0, payout = NULL WHERE match_id = ? AND user_id = ?",
                (match_id, r["user_id"]),
            )
        await conn.commit()
    return web.json_response({"ok": True})


# --- inhouse: history / flags --------------------------------------------------

@routes.get("/inhouse/matches/{match_id}/flags")
async def inhouse_flags(request):
    match_id = request.match_info["match_id"]
    players = await db.fetchall("SELECT user_id, team FROM inhouse_match_players WHERE match_id = ?", (match_id,))
    team_of = {p["user_id"]: p["team"] for p in players}
    votes = await db.fetchall("SELECT user_id, voted_team FROM inhouse_match_votes WHERE match_id = ?", (match_id,))
    flagged = [dict(v) for v in votes if v["user_id"] in team_of and v["voted_team"] != team_of[v["user_id"]]]
    for f in flagged:
        f["team"] = team_of[f["user_id"]]
    return web.json_response(flagged)


@routes.get("/inhouse/history/{user_id}")
async def inhouse_history(request):
    rows = await db.fetchall(
        """SELECT m.* FROM inhouse_matches m
           JOIN inhouse_match_players p ON p.match_id = m.match_id
           WHERE p.user_id = ? AND m.status IN ('completed', 'cancelled') ORDER BY m.match_id DESC""",
        (request.match_info["user_id"],),
    )
    return web.json_response(_rows(rows))


@routes.get("/inhouse/stats/{user_id}")
async def inhouse_stats(request):
    user_id = request.match_info["user_id"]
    recent = await db.fetchall(
        """SELECT m.match_id, p.team, m.winning_team FROM inhouse_matches m
           JOIN inhouse_match_players p ON p.match_id = m.match_id
           WHERE p.user_id = ? AND m.status = 'completed' ORDER BY m.match_id DESC LIMIT 5""",
        (user_id,),
    )
    return web.json_response(_rows(recent))


@routes.get("/inhouse/sweep-candidates")
async def inhouse_sweep_candidates(request):
    max_age_hours = request.query.get("max_age_hours", "3")
    rows = await db.fetchall(
        """SELECT * FROM inhouse_matches
           WHERE status IN ('completed', 'cancelled')
           AND (text_channel_id IS NOT NULL OR voice_channel_a_id IS NOT NULL OR voice_channel_b_id IS NOT NULL)
           AND datetime(created_at, ? || ' hours') < datetime('now')""",
        (max_age_hours,),
    )
    return web.json_response(_rows(rows))


@routes.post("/inhouse/matches/{match_id}/clear-channels")
async def inhouse_clear_channels(request):
    await db.execute(
        "UPDATE inhouse_matches SET text_channel_id = NULL, voice_channel_a_id = NULL, voice_channel_b_id = NULL WHERE match_id = ?",
        (request.match_info["match_id"],),
    )
    return web.json_response({"ok": True})


# --- lifecycle -----------------------------------------------------------------

async def on_startup(app):
    from database.init_db import init_db
    await init_db(config.DB_PATH)
    logger.info("db-service ready, DB_PATH=%s", config.DB_PATH)


def create_app():
    app = web.Application()
    app.add_routes(routes)
    app.on_startup.append(on_startup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), port=config.PORT)
