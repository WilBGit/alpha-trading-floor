from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as N

from alpha_market_engine.content.opportunities import (
    ContentDecision,
    ContentOpportunityEngine,
    OpportunityType,
)
from alpha_market_engine.market.brain_types import DivergenceSignal, EventLifecycle, PriceActionEventType


def brain(*, events=None, signals=None, confidence=80, regime="RANGE", relative=None, session="2026-07-14"):
    now = datetime(2026, 7, 14, 16, 0, tzinfo=UTC)
    events = events or []
    price_action = {}
    for event in events:
        price_action.setdefault(event.symbol, N(events=[])).events.append(event)
    return N(
        analysis_id=f"analysis-{now.timestamp()}-{len(events)}",
        analysis_started_at=now,
        analysis_session_date=session,
        analysis_confidence=N(score=confidence),
        price_action=price_action,
        divergences=N(signals=signals or [], confidence=confidence, relative_performance_facts=relative or {}, index_spread_fact=None),
        regime=N(regime=N(value=regime), confidence=confidence, supporting_data={}, scores={regime: confidence}),
        observations=[], structures={}, momentum={},
    )


def event(event_type, symbol="SPY", lifecycle=EventLifecycle.CONFIRMED, confidence=90, event_id="e1", minutes_ago=0):
    return N(
        event_type=event_type,
        symbol=symbol,
        detected_at=datetime(2026, 7, 14, 16, 0, tzinfo=UTC) - timedelta(minutes=minutes_ago),
        confidence=confidence,
        importance=80,
        evidence=["deterministic evidence"],
        supporting_values={"range_high": "750.98"},
        lifecycle=lifecycle,
        event_id=event_id,
    )


def test_repeated_or5_breakout_is_one_lifecycle_and_suppressed():
    result = brain(events=[event(PriceActionEventType.OR5_BREAKOUT)])
    engine = ContentOpportunityEngine()
    first = engine.evaluate(result)
    second = engine.evaluate(result)
    first_or = next(o for o in first.detected_opportunities if o.opportunity_type == OpportunityType.OR5_BREAKOUT_CONFIRMED)
    second_or = next(o for o in second.detected_opportunities if o.opportunity_type == OpportunityType.OR5_BREAKOUT_CONFIRMED)
    assert first_or.decision == ContentDecision.POST_CANDIDATE
    assert second_or.decision == ContentDecision.SUPPRESS_DUPLICATE
    assert first_or.opportunity_id == second_or.opportunity_id
    assert first_or.opportunity_key == second_or.opportunity_key


def test_qqq_leadership_uses_structured_divergence_and_boundary():
    result = brain(
        signals=[DivergenceSignal.QQQ_OUTPERFORMING_SPY],
        relative={"QQQ": {"index_a": "QQQ", "index_b": "SPY", "relative_difference": "0.75", "display_difference": "0.75"}},
        regime="ROTATION",
        confidence=80,
    )
    opportunity = next(o for o in ContentOpportunityEngine().evaluate(result).detected_opportunities if o.opportunity_type == OpportunityType.QQQ_LEADERSHIP)
    assert opportunity.facts["relative_difference"] == "0.75"
    assert "institutional" in opportunity.interpretation_boundary
    assert "sector-level" in opportunity.interpretation_boundary


def test_small_leadership_change_keeps_key_and_material_change_threshold():
    engine = ContentOpportunityEngine()
    first = brain(signals=[DivergenceSignal.QQQ_OUTPERFORMING_SPY], relative={"QQQ": {"relative_difference": "0.75"}}, regime="ROTATION")
    second = brain(signals=[DivergenceSignal.QQQ_OUTPERFORMING_SPY], relative={"QQQ": {"relative_difference": "0.78"}}, regime="ROTATION")
    a = next(o for o in engine.evaluate(first).detected_opportunities if o.opportunity_type == OpportunityType.QQQ_LEADERSHIP)
    b = next(o for o in engine.evaluate(second).detected_opportunities if o.opportunity_type == OpportunityType.QQQ_LEADERSHIP)
    assert a.opportunity_key == b.opportunity_key
    assert b.material_change is False
    assert b.decision == ContentDecision.SUPPRESS_DUPLICATE


def test_material_leadership_change_updates_same_identity():
    engine = ContentOpportunityEngine()
    first = brain(signals=[DivergenceSignal.QQQ_OUTPERFORMING_SPY], relative={"QQQ": {"relative_difference": "0.75"}}, regime="ROTATION")
    second = brain(signals=[DivergenceSignal.QQQ_OUTPERFORMING_SPY], relative={"QQQ": {"relative_difference": "1.35"}}, regime="ROTATION")
    a = next(o for o in engine.evaluate(first).detected_opportunities if o.opportunity_type == OpportunityType.QQQ_LEADERSHIP)
    b = next(o for o in engine.evaluate(second).detected_opportunities if o.opportunity_type == OpportunityType.QQQ_LEADERSHIP)
    assert a.opportunity_key == b.opportunity_key
    assert b.material_change is True
    assert "MATERIAL_CHANGE_DETECTED" in {reason.value for reason in b.decision_reasons}


def test_failed_breakout_is_distinct_and_low_confidence_cannot_post():
    failed = brain(events=[event(PriceActionEventType.FAILED_OR_BREAKOUT, lifecycle=EventLifecycle.FAILED, event_id="failed")])
    opportunity = next(o for o in ContentOpportunityEngine().evaluate(failed).detected_opportunities if o.opportunity_type == OpportunityType.FAILED_OR_BREAKOUT)
    assert opportunity.confirmation_state.value == "RESOLVED"
    low = brain(events=[event(PriceActionEventType.OR30_BREAKOUT, confidence=35)], confidence=35)
    low_opportunity = next(o for o in ContentOpportunityEngine().evaluate(low).detected_opportunities if o.opportunity_type == OpportunityType.OR30_BREAKOUT_CONFIRMED)
    assert low_opportunity.decision != ContentDecision.POST_CANDIDATE
    assert "LOW_CONFIDENCE" in {reason.value for reason in low_opportunity.decision_reasons}


def test_developing_event_waits_and_rotation_is_bounded():
    developing = brain(events=[event(PriceActionEventType.OR5_BREAKOUT, lifecycle=EventLifecycle.DETECTED)])
    output = ContentOpportunityEngine().evaluate(developing)
    breakout = next(o for o in output.detected_opportunities if o.opportunity_type == OpportunityType.OR5_BREAKOUT_CONFIRMED)
    assert breakout.decision == ContentDecision.WAIT_FOR_CONFIRMATION
    assert output.selected_candidates
    assert len(output.selected_candidates) <= 1
