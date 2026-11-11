from __future__ import annotations


def score_pattern(times_used: int, engagement_rate: float | None) -> float:
    if engagement_rate is None:
        return 0.0
    return round(min(100.0, engagement_rate * 10 + min(times_used, 20) * 1.5), 2)
