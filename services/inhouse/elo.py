"""Standard Elo rating math for inhouse matches.

Per-player MMR change is computed against the *average* MMR of the
opposing team, not 1v1, since these are 5v5 team matches.
"""
import config


def expected_score(own_mmr: int, opponent_avg_mmr: int) -> float:
    return 1 / (1 + 10 ** ((opponent_avg_mmr - own_mmr) / 400))


def mmr_delta(own_mmr: int, opponent_avg_mmr: int, won: bool, k_factor: int = config.INHOUSE_ELO_K_FACTOR) -> int:
    actual = 1.0 if won else 0.0
    expected = expected_score(own_mmr, opponent_avg_mmr)
    return round(k_factor * (actual - expected))
