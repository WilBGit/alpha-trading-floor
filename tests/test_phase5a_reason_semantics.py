from types import SimpleNamespace as N
from datetime import UTC, datetime
from alpha_market_engine.content.opportunities import ContentOpportunityEngine, ContentDecision, DecisionReason, OpportunityType
from alpha_market_engine.market.brain_types import EventLifecycle, PriceActionEventType


def test_lower_ranked_eligible_opportunity_gets_cycle_selection_reason():
    now = datetime(2026, 7, 14, 16, 0, tzinfo=UTC)
    def event(kind, eid):
        return N(event_type=kind, symbol='SPY', detected_at=now, confidence=90, importance=90, evidence=['e'], supporting_values={}, lifecycle=EventLifecycle.CONFIRMED, event_id=eid)
    result = N(analysis_id='rank-analysis', analysis_started_at=now, analysis_session_date='2026-07-14', analysis_confidence=N(score=90), price_action={'SPY': N(events=[event(PriceActionEventType.OR30_BREAKOUT, 'rank-30'), event(PriceActionEventType.OR15_BREAKOUT, 'rank-15')])}, divergences=N(signals=[], confidence=0, relative_performance_facts={}, index_spread_fact=None), regime=N(regime=N(value='RANGE'), confidence=50, supporting_data={}, scores={}), observations=[], structures={}, momentum={})
    output = ContentOpportunityEngine().evaluate(result)
    lower = next(o for o in output.ranked_opportunities if o.opportunity_type == OpportunityType.OR15_BREAKOUT_CONFIRMED)
    assert lower.decision == ContentDecision.SKIP
    assert DecisionReason.CYCLE_SELECTION_LIMIT_REACHED in lower.decision_reasons
