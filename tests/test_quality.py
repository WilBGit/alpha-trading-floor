from __future__ import annotations

from alpha_market_engine.content.quality import compute_quality_score


def test_low_quality_rejection():
    score = compute_quality_score("🚨🚨🚨 BUY NOW!!!", fact_confidence=40, duplicate_score=90)
    assert score < 80
