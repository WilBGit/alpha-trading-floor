from datetime import UTC, datetime
from types import SimpleNamespace as N

from alpha_market_engine.content.opportunities import ContentDecision, ContentOpportunityEngine, OpportunityType
from alpha_market_engine.market.brain_types import EventLifecycle, PriceActionEventType


def test_scenario_1_repeated_above_vwap_is_suppressed():
    now = datetime(2026, 7, 14, 16, 0, tzinfo=UTC)
    event = N(event_type=PriceActionEventType.ABOVE_VWAP, symbol='SPY', detected_at=now, confidence=80, importance=50, evidence=['above'], supporting_values={'current_price': '1', 'session_vwap': '0.9'}, lifecycle=EventLifecycle.CONFIRMED, event_id='above-vwap')
    result = N(analysis_id='above-vwap-analysis', analysis_started_at=now, analysis_session_date='2026-07-14', analysis_confidence=N(score=80), price_action={'SPY': N(events=[event])}, divergences=N(signals=[], confidence=0, relative_performance_facts={}, index_spread_fact=None), regime=N(regime=N(value='RANGE'), confidence=64, supporting_data={}, scores={}), observations=[], structures={}, momentum={})
    engine = ContentOpportunityEngine()
    first = next(o for o in engine.evaluate(result).detected_opportunities if o.opportunity_type == OpportunityType.ABOVE_VWAP_CONFIRMATION)
    second = next(o for o in engine.evaluate(result).detected_opportunities if o.opportunity_type == OpportunityType.ABOVE_VWAP_CONFIRMATION)
    assert first.opportunity_id == second.opportunity_id
    assert second.decision == ContentDecision.SUPPRESS_DUPLICATE
