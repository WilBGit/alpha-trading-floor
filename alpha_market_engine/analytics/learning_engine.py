from __future__ import annotations


def best_windows(rows: list[dict]) -> list[str]:
    return [row.get("window", "") for row in sorted(rows, key=lambda r: r.get("engagement_rate", 0), reverse=True)[:3]]
