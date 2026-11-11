from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace as N

import pytest

from alpha_market_engine.content.drafting import (
    ContentDraftingService,
    ContentDraftPackage,
    DraftValidator,
    FakeDraftingProvider,
    NumericFact,
    build_draft_package,
    content_hash,
)
from alpha_market_engine.content.opportunities import ContentOpportunity, ConfirmationState, OpportunityType


def opportunity(kind=OpportunityType.QQQ_LEADERSHIP, facts=None):
    now = datetime(2026, 7, 14, 16, 0, tzinfo=UTC)
    facts = facts or {"index_a": "QQQ", "index_b": "SPY", "index_a_change_percent": "1.1113608902", "index_b_change_percent": "0.3617336519", "relative_difference": "0.7496272383", "display_difference": "0.75"}
    return ContentOpportunity(
        opportunity_id="opp-1", opportunity_key="key-1", analysis_id="analysis-1", opportunity_type=kind,
        symbols=("QQQ", "SPY") if kind == OpportunityType.QQQ_LEADERSHIP else ("SPY",), primary_symbol="QQQ" if kind == OpportunityType.QQQ_LEADERSHIP else "SPY",
        session_date="2026-07-14", detected_at=now, source_regime="ROTATION", source_regime_confidence=64,
        facts=facts, importance_score=76, confidence_score=72, freshness_score=100, novelty_score=90,
        content_score=Decimal("81.70"), confirmation_state=ConfirmationState.CONFIRMED,
        content_pattern_key="QQQ_SPY:RELATIVE_PERFORMANCE:QQQ", interpretation_boundary="Data supports deterministic relative performance across the captured indices. It does not independently prove institutional activity, sector-level capital rotation, or causation.",
    )


def validator_for(kind=OpportunityType.QQQ_LEADERSHIP, facts=None, recent=None):
    return DraftValidator(recent_drafts=recent).validate


@pytest.fixture
def qqq_package():
    return build_draft_package(opportunity())


@pytest.mark.parametrize("text,expected", [
    ("$QQQ putting 0.75 percentage points of distance on $SPY. Relative strength is showing up.", True),
    ("$QQQ putting 0.83 percentage points of distance on $SPY.", False),
    ("$QQQ is up 0.75% more than $SPY.", False),
    ("$QQQ is outperforming $SPY. NVDA is leading too.", False),
    ("Institutions are clearly rotating into tech.", False),
    ("Money is rotating into the tech sector.", False),
    ("$QQQ has the relative-strength edge over $SPY here. Follow-through still needs to prove itself.", True),
    ("$QQQ is guaranteed to keep running.", False),
    ("Buy $QQQ now.", False),
])
def test_qqq_claim_validation_scenarios(qqq_package, text, expected):
    result = DraftValidator().validate(text, qqq_package)
    assert result.is_valid is expected


def test_scenario_10_wrong_vwap_claim_fails():
    package = build_draft_package(opportunity(OpportunityType.VWAP_LOSS, {"vwap_position": "below"}))
    result = DraftValidator().validate("$SPY is holding above VWAP.", package)
    assert not result.is_valid


def test_scenario_11_valid_vwap_draft():
    package = build_draft_package(opportunity(OpportunityType.VWAP_RECLAIM, {"vwap_position": "reclaimed"}))
    result = DraftValidator().validate("$SPY reclaimed VWAP. Follow-through still needs to prove itself.", package)
    assert result.is_valid


def test_scenario_12_wrong_opening_range_fails():
    package = build_draft_package(opportunity(OpportunityType.OR15_BREAKOUT_CONFIRMED, {"range": "OR15"}))
    result = DraftValidator().validate("$SPY cleared the OR30 high.", package)
    assert not result.is_valid


def test_scenario_13_valid_opening_range():
    package = build_draft_package(opportunity(OpportunityType.OR15_BREAKOUT_CONFIRMED, {"range": "OR15"}))
    result = DraftValidator().validate("$SPY cleared the OR15 high. Watch whether price accepts above the range.", package)
    assert result.is_valid


def test_scenario_14_and_15_certainty_boundary():
    package = build_draft_package(opportunity())
    assert not DraftValidator().validate("$QQQ next leg is higher.", package).is_valid
    assert DraftValidator().validate("Watch whether $QQQ can hold the relative-strength edge over $SPY.", package).is_valid


def test_scenario_16_generic_ai_language_is_rewrite_quality_failure():
    package = build_draft_package(opportunity())
    result = DraftValidator().validate("According to the data, market participants should consider the relative strength in $QQQ.", package)
    assert not result.is_valid
    assert "GENERIC_AI_LANGUAGE" in result.warnings


def test_scenario_17_length_failure():
    package = build_draft_package(opportunity())
    result = DraftValidator().validate("$QQQ leads $SPY. " + "watch " * 100, package)
    assert "PLATFORM_LENGTH_EXCEEDED" in result.failures


def test_scenario_18_and_19_duplicate_similarity():
    package = build_draft_package(opportunity())
    old = N(draft_id="old", content_text="$QQQ putting 0.75 percentage points of distance on $SPY. Relative strength is showing up.")
    exact = DraftValidator(recent_drafts=[old]).validate(old.content_text, package)
    near = DraftValidator(recent_drafts=[old]).validate("$QQQ is putting 0.75 percentage points of distance on $SPY. Relative strength is showing up here.", package)
    assert exact.exact_duplicate
    assert not exact.is_valid
    assert near.near_duplicate


def test_scenario_20_rewrite_success():
    provider = FakeDraftingProvider(["According to the data, $QQQ is up 0.83 percentage points.", "$QQQ putting 0.75 percentage points of distance on $SPY. Relative strength is showing up."])
    result = ContentDraftingService(provider).draft(opportunity())
    assert result.final_decision == "APPROVE"
    assert len(result.attempts) == 2


def test_scenario_21_rewrite_limit_rejects():
    provider = FakeDraftingProvider(["Buy NVDA now.", "Buy NVDA now.", "Buy NVDA now."])
    result = ContentDraftingService(provider).draft(opportunity())
    assert result.final_decision == "REJECT"
    assert len(result.attempts) == 3


def test_scenario_22_unapproved_number_with_restricted_package():
    package = build_draft_package(opportunity(facts={"relative_difference": "0.7496272383", "display_difference": "0.75"}))
    result = DraftValidator().validate("$QQQ is up 1.11% while $SPY is up 0.36%.", package)
    assert not result.is_valid


def test_scenario_23_rotation_boundary():
    package = build_draft_package(opportunity(OpportunityType.MARKET_ROTATION, {"regime": "ROTATION"}))
    assert DraftValidator().validate("The major indices are trading with divergent relative performance. Rotation regime is still active.", package).is_valid
    assert not DraftValidator().validate("Money is rotating from industrials into tech.", package).is_valid


def test_scenario_24_degraded_fact_boundary():
    package = build_draft_package(opportunity(facts={"quality": "DEGRADED"}))
    result = DraftValidator().validate("$QQQ is clean and confirmed by every feed.", package)
    assert not result.is_valid or result.quality_score < 70


def test_scenario_25_symbol_format_and_hash():
    package = build_draft_package(opportunity())
    a = DraftValidator().validate("$QQQ is outperforming $SPY by 0.75 percentage points.", package)
    b = DraftValidator().validate("QQQ is outperforming SPY by 0.75 percentage points.", package)
    assert a.is_valid and b.is_valid
    assert content_hash(" $QQQ  is outperforming $SPY by 0.75 percentage points. ") == content_hash("$QQQ is outperforming $SPY by 0.75 percentage points.")
