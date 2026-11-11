from __future__ import annotations


def engagement_rate(impressions: int | None, likes: int | None, replies: int | None, reposts: int | None, bookmarks: int | None) -> float | None:
    if not impressions or impressions <= 0:
        return None
    total = sum(v or 0 for v in [likes, replies, reposts, bookmarks])
    return round((total / impressions) * 100, 2)
