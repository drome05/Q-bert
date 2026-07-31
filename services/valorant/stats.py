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

    for grade, threshold in config.GRADE_THRESHOLDS:
        if composite >= threshold:
            return grade
    return "F"
