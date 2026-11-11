from __future__ import annotations

from alpha_market_engine.content.safety import safety_check


def test_financial_advice_safety_rejection():
    result = safety_check("Buy NVDA now. Easy money.")
    assert result.status == "FAIL"


def test_safe_commentary_passes():
    result = safety_check("$SPY holding better than expected while tech gets slapped.")
    assert result.status == "PASS"
