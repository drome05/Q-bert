"""Shared per-match stat extraction, used by both /rank (aggregate
performance stats) and /matches (per-match display) so the two don't
drift out of sync.
"""
import config


def extract_player_match(match: dict, puuid_name: str) -> dict | None:
    # HenrikDev sometimes returns a stub record (is_available=False,
    # metadata/players/teams all null) for a match it hasn't fully
    # indexed yet -- treat it the same as "not found in this match".
    if not match.get("is_available", True):
        return None

    meta = match.get("metadata") or {}
    rounds_played = meta.get("rounds_played") or 0
    players = (match.get("players") or {}).get("all_players", [])
    me = next((p for p in players if f"{p.get('name')}#{p.get('tag')}".lower() == puuid_name), None)
    if me is None:
        return None

    stats = me.get("stats", {})
    kills = stats.get("kills", 0)
    deaths = stats.get("deaths", 0)
    assists = stats.get("assists", 0)
    score = stats.get("score", 0)
    headshots = stats.get("headshots", 0)
    bodyshots = stats.get("bodyshots", 0)
    legshots = stats.get("legshots", 0)
    damage_made = me.get("damage_made", 0)

    team_name = me.get("team", "").lower()
    teams = match.get("teams") or {}
    team_score = (teams.get(team_name) or {}).get("rounds_won", "?")
    other_team = "blue" if team_name == "red" else "red"
    other_score = (teams.get(other_team) or {}).get("rounds_won", "?")
    won = (teams.get(team_name) or {}).get("has_won", False)
    agent_icon = (me.get("assets") or {}).get("agent", {}).get("small")

    # Deathmatch (and similar non-round modes) report rounds_played=1 --
    # dividing a whole-game score by that produces a meaningless ACS/ADR,
    # so those are only meaningful for actual round-based modes.
    round_based = rounds_played > 1

    first_bloods = None
    first_deaths = None
    if round_based:
        my_puuid = me.get("puuid")
        kills_by_round: dict = {}
        for k in match.get("kills") or []:
            kills_by_round.setdefault(k.get("round"), []).append(k)
        first_bloods = 0
        first_deaths = 0
        for events in kills_by_round.values():
            first_kill = min(events, key=lambda e: e.get("kill_time_in_round", 0))
            if first_kill.get("killer_puuid") == my_puuid:
                first_bloods += 1
            if first_kill.get("victim_puuid") == my_puuid:
                first_deaths += 1

    return {
        "map": meta.get("map", "?"),
        "mode": meta.get("mode", "?"),
        "won": won,
        "team_score": team_score,
        "other_score": other_score,
        "character": me.get("character", "?"),
        "agent_icon": agent_icon,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "score": score,
        "rounds_played": rounds_played,
        "acs": (score / rounds_played) if round_based else None,
        "adr": (damage_made / rounds_played) if round_based else None,
        "headshot_pct": (headshots / max(1, headshots + bodyshots + legshots)) * 100,
        "first_blood_pct": (first_bloods / rounds_played * 100) if round_based else None,
        "first_death_pct": (first_deaths / rounds_played * 100) if round_based else None,
    }


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _normalize(value: float, lo: float, hi: float) -> float:
    return _clamp((value - lo) / (hi - lo) * 100, 0, 100)


def compute_grade(rank_idx: int | None, avg_acs: float, kd: float) -> str:
    rank_norm = _normalize(rank_idx, 0, len(config.RANK_ORDER) - 1) if rank_idx is not None else 0
    acs_norm = _normalize(avg_acs, *config.ACS_NORM_RANGE)
    kd_norm = _normalize(kd, *config.KD_NORM_RANGE)

    w = config.GRADE_WEIGHTS
    composite = rank_norm * w["rank"] + acs_norm * w["acs"] + kd_norm * w["kd"]
    return _grade_from_composite(composite)


def compute_match_grade(acs: float, kd: float) -> str:
    acs_norm = _normalize(acs, *config.ACS_NORM_RANGE)
    kd_norm = _normalize(kd, *config.KD_NORM_RANGE)

    w = config.MATCH_GRADE_WEIGHTS
    composite = acs_norm * w["acs"] + kd_norm * w["kd"]
    return _grade_from_composite(composite)


def _grade_from_composite(composite: float) -> str:
    for grade, threshold in config.GRADE_THRESHOLDS:
        if composite >= threshold:
            return grade
    return "F"


def derive_tips(m: dict) -> list[str]:
    """Small rule-based tips derived from a single match's stats. Our own
    heuristics (not official advice) -- thresholds live in config.py.
    Capped to 2 so the embed stays readable; evaluated in priority order.
    """
    kd = (m["kills"] / m["deaths"]) if m["deaths"] else float(m["kills"])
    tips = []

    if m["first_death_pct"] is not None and m["first_death_pct"] > config.TIP_FIRST_DEATH_PCT_HIGH:
        tips.append(
            f"🩸 Dying first in {m['first_death_pct']:.0f}% of rounds -- try holding an angle instead of "
            "pushing into unclear space, or let a teammate take the opening duel."
        )
    if m["kills"] >= config.TIP_MIN_KILLS_FOR_HS_TIP and m["headshot_pct"] < config.TIP_HEADSHOT_PCT_LOW:
        tips.append(f"🎯 Headshot rate was low ({m['headshot_pct']:.0f}%) -- work on crosshair placement at head height.")
    if kd < config.TIP_KD_LOW:
        tips.append(f"📉 Rough K/D this match ({kd:.2f}) -- consider playing a bit more passively for picks instead of forcing duels.")
    if (
        m["first_blood_pct"] is not None and m["first_blood_pct"] > config.TIP_FIRST_BLOOD_PCT_HIGH
        and kd >= config.TIP_KD_GOOD
    ):
        tips.append(f"🔥 Strong entry fragging ({m['first_blood_pct']:.0f}% first bloods) -- keep taking those early picks.")
    if m["adr"] is not None and m["adr"] < config.TIP_ADR_LOW:
        tips.append(f"💥 Low damage per round ({m['adr']:.0f} ADR) -- look for trade opportunities or utility usage to chip damage.")

    return tips[:2]
