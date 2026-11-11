from __future__ import annotations

from .validator import ClaimResult


def compute_fact_confidence(results: list[ClaimResult]) -> int:
    if not results:
        return 0
    passed = sum(1 for result in results if result.passed)
    return int(round((passed / len(results)) * 100))
