from __future__ import annotations

from alpha_market_engine.content.duplicate import DuplicateDetector


def test_exact_duplicate_rejection():
    det = DuplicateDetector()
    historical = ["$SPY is holding better than expected while tech gets slapped."]
    candidate = "$SPY is holding better than expected while tech gets slapped."
    assert det.exact_hash(candidate) == det.exact_hash(historical[0])
    assert det.is_duplicate(candidate, historical)


def test_semantic_duplicate_rejection():
    det = DuplicateDetector()
    historical = ["$SPY is holding better than expected while tech gets slapped."]
    candidate = "$SPY holding surprisingly well while tech gets hit."
    assert det.semantic_duplicate_score(candidate, historical) >= 50
