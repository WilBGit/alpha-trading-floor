from __future__ import annotations

import re
from dataclasses import dataclass


BANNED_PATTERNS = [
    r"buy\s+\$?[A-Z]{1,6}\s+now",
    r"sell\s+everything",
    r"guaranteed\s+breakout",
    r"will\s+double",
    r"easy\s+money",
    r"can['’]?t\s+lose",
    r"100%\s+winner",
    r"my\s+position",
    r"i\s+bought",
    r"i\s+am\s+holding",
    r"i'm\s+holding",
]


@dataclass(slots=True)
class SafetyResult:
    status: str
    reasons: list[str]


def safety_check(content: str) -> SafetyResult:
    normalized = content.lower()
    reasons = ["matches financial advice / position-claim ban"] if any(re.search(pattern, normalized) for pattern in BANNED_PATTERNS) else []
    if reasons:
        return SafetyResult(status="FAIL", reasons=reasons)
    return SafetyResult(status="PASS", reasons=[])
