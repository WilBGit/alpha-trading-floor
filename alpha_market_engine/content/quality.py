from __future__ import annotations

import re


def compute_quality_score(content: str, *, fact_confidence: int = 100, duplicate_score: int = 0) -> int:
    length = len(content)
    lines = content.count("\n") + 1
    emojis = len(re.findall(r"[\U00010000-\U0010ffff]", content))
    hooks = 1 if any(phrase in content for phrase in ["But here’s the thing", "The interesting part", "I’m watching"]) else 0
    cashtags = len(re.findall(r"\$[A-Z]{1,6}", content))
    readability = 100 if length <= 280 else max(60, 100 - (length - 280) // 2)
    structure = 100 if lines <= 5 else max(70, 100 - (lines - 5) * 6)
    voice = 90 if hooks else 72
    emoji_penalty = min(20, emojis * 6)
    duplicate_penalty = min(60, duplicate_score)
    fact_bonus = int(fact_confidence * 0.2)
    raw = int((readability * 0.25) + (structure * 0.2) + (voice * 0.2) + (fact_bonus * 0.25) + (min(100, cashtags * 12) * 0.1))
    raw = max(0, raw - emoji_penalty - duplicate_penalty // 2)
    return min(100, raw)
