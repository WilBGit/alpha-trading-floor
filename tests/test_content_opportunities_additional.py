from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace as N

from alpha_market_engine.content.opportunities import ContentDecision, ContentOpportunityEngine, OpportunityType
from alpha_market_engine.market.brain_types import DivergenceSignal, EventLifecycle, PriceActionEventType


def make_result(*, event_type=None, lifecycle=EventLifecycle.CONFIRMED, event_id='x', confidence=80, regime='RANGE', signal=None, diff='0.75'):
    now = datetime(2026, 7, 14, 16, 0, tzinfo=UTC)
    events = []
    if event_type:
        events = [N(event_type=event_type, symbol='SPY', detected_at=now, confidence=confidence, importance=80, evidence=['e'], supporting_values={}, lifecycle=lifecycle, event_id=event_id)]
    return N(analysis_id=event_id, analysis_started_at=now, analysis_session_date='2026-07-14', analysis_confidence=N(score=confidence), price_action={'SPY': N(events=events)}, divergences=N(signals=[signal] if signal else [], confidence=confidence, relative_performance_facts={'QQQ': {'relative_difference': diff}} if signal else {}, index_spread_fact=None), regime=N(regime=N(value=regime), confidence=confidence, supporting_data={}, scores={regime: confidence}), observations=[], structures={}, momentum={})


def test_scenario_2_confirmed_vwap_reclaim_is_candidate():
    out = ContentOpportunityEngine().evaluate(make_result(event_type=PriceActionEventType.VWAP_RECLAIM))
    assert next(o for o in out.detected_opportunities if o.opportunity_type == OpportunityType.VWAP_RECLAIM).decision == ContentDecision.POST_CANDIDATE


def test_scenario_3_and_4_or_breakout_importance_order():
    engine = ContentOpportunityEngine()
    or5 = next(o for o in engine.evaluate(make_result(event_type=PriceActionEventType.OR5_BREAKOUT)).detected_opportunities if o.opportunity_type == OpportunityType.OR5_BREAKOUT_CONFIRMED)
    or30 = next(o for o in engine.evaluate(make_result(event_type=PriceActionEventType.OR30_BREAKOUT, event_id='or30')).detected_opportunities if o.opportunity_type == OpportunityType.OR30_BREAKOUT_CONFIRMED)
    assert or30.importance_score > or5.importance_score


def test_scenario_9_and_10_resolve_then_recur_with_new_key():
    engine = ContentOpportunityEngine()
    active = next(o for o in engine.evaluate(make_result(signal=DivergenceSignal.QQQ_OUTPERFORMING_SPY, diff='0.75', event_id='active', regime='ROTATION')).detected_opportunities if o.opportunity_type == OpportunityType.QQQ_LEADERSHIP)
    resolved = next(o for o in engine.evaluate(make_result(signal=DivergenceSignal.QQQ_OUTPERFORMING_SPY, diff='0.05', event_id='resolved', regime='ROTATION')).detected_opportunities if o.opportunity_type == OpportunityType.QQQ_LEADERSHIP)
    recur = next(o for o in engine.evaluate(make_result(signal=DivergenceSignal.QQQ_OUTPERFORMING_SPY, diff='0.80', event_id='recur', regime='ROTATION')).detected_opportunities if o.opportunity_type == OpportunityType.QQQ_LEADERSHIP)
    assert active.opportunity_key == resolved.opportunity_key
    assert recur.opportunity_key != active.opportunity_key


def test_scenario_11_rotation_boundary_and_12_regime_change_inputs():
    out = ContentOpportunityEngine().evaluate(make_result(regime='ROTATION', event_id='rotation'))
    rotation = next(o for o in out.detected_opportunities if o.opportunity_type == OpportunityType.MARKET_ROTATION)
    assert rotation.confirmation_state.value == 'CONFIRMED'
    assert 'sector-level' in rotation.interpretation_boundary


def test_scenario_15_confirmation_promotion_and_16_duplicate_suppression():
    engine = ContentOpportunityEngine()
    developing = next(o for o in engine.evaluate(make_result(event_type=PriceActionEventType.OR5_BREAKOUT, lifecycle=EventLifecycle.DETECTED, event_id='same')).detected_opportunities if o.opportunity_type == OpportunityType.OR5_BREAKOUT_CONFIRMED)
    confirmed = next(o for o in engine.evaluate(make_result(event_type=PriceActionEventType.OR5_BREAKOUT, lifecycle=EventLifecycle.CONFIRMED, event_id='same')).detected_opportunities if o.opportunity_type == OpportunityType.OR5_BREAKOUT_CONFIRMED)
    assert developing.decision == ContentDecision.WAIT_FOR_CONFIRMATION
    assert confirmed.decision == ContentDecision.POST_CANDIDATE


def test_scenario_17_material_change_has_explicit_reason():
    engine = ContentOpportunityEngine()
    engine.evaluate(make_result(signal=DivergenceSignal.QQQ_OUTPERFORMING_SPY, diff='0.75', event_id='same', regime='ROTATION'))
    changed = next(o for o in engine.evaluate(make_result(signal=DivergenceSignal.QQQ_OUTPERFORMING_SPY, diff='1.35', event_id='same', regime='ROTATION')).detected_opportunities if o.opportunity_type == OpportunityType.QQQ_LEADERSHIP)
    assert changed.material_change is True
    assert 'MATERIAL_CHANGE_DETECTED' in {r.value for r in changed.decision_reasons}


def test_scenario_19_zero_event_range_has_no_post_candidate_from_regime():
    out = ContentOpportunityEngine().evaluate(make_result(regime='RANGE', confidence=50, event_id='quiet'))
    assert not out.selected_candidates


def test_scenario_20_repeated_equivalent_analysis_has_stable_identity():
    result = make_result(event_type=PriceActionEventType.OR15_BREAKOUT, event_id='repeat')
    engine = ContentOpportunityEngine()
    a = next(o for o in engine.evaluate(result).detected_opportunities if o.opportunity_type == OpportunityType.OR15_BREAKOUT_CONFIRMED)
    b = next(o for o in engine.evaluate(result).detected_opportunities if o.opportunity_type == OpportunityType.OR15_BREAKOUT_CONFIRMED)
    assert (a.opportunity_id, a.opportunity_key) == (b.opportunity_id, b.opportunity_key)
