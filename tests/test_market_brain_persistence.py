from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from alpha_market_engine.database.repository import Repository
from alpha_market_engine.market.brain_types import DivergenceSignal, EventLifecycle, ObservationType, OpeningRangeLevels, PriceActionEventType
from alpha_market_engine.market.models import IntradayBar
from alpha_market_engine.market.observations import MarketObservationEngine
from alpha_market_engine.market.price_action import PriceActionAnalyzer


def _bar(symbol: str, ts: datetime, price: str, *, volume: int = 10_000) -> IntradayBar:
    value = Decimal(price)
    return IntradayBar(
        symbol=symbol,
        timestamp=ts,
        open=value,
        high=value + Decimal("0.05"),
        low=value - Decimal("0.05"),
        close=value,
        volume=volume,
        provider="test",
        data_feed="unit-test",
        vwap=value,
    )


def _run(coro: Any):
    return asyncio.run(coro)


def _require(value: str | None) -> str:
    assert value is not None
    return value


def _enum_text(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None and dt.utcoffset() is not None else dt.replace(tzinfo=UTC)


def _save_price_action(repo: Repository, event: Any):
    evidence = getattr(event, "evidence", None)
    if evidence is None:
        evidence = getattr(event, "evidence_json", [])
    supporting_values = getattr(event, "supporting_values", None)
    if supporting_values is None:
        supporting_values = getattr(event, "supporting_values_json", {})
    return _run(
        repo.save_price_action_event(
            event_id=_require(event.event_id),
            symbol=event.symbol,
            event_type=_enum_text(event.event_type),
            lifecycle=_enum_text(event.lifecycle),
            detected_at=_aware(event.detected_at),
            confidence=event.confidence,
            importance=event.importance,
            evidence=list(evidence),
            supporting_values=dict(supporting_values),
            session_date=_aware(event.session_date or event.detected_at),
        )
    )


def _save_observation(repo: Repository, observation: Any):
    return _run(
        repo.save_market_observation(
            observation_key=_require(observation.observation_key),
            observation_type=observation.observation_type.value,
            symbols=list(observation.symbols),
            detected_at=_aware(observation.detected_at),
            importance_score=observation.importance_score,
            confidence_score=observation.confidence_score,
            facts=list(observation.facts),
            interpretation_boundary=observation.interpretation_boundary,
            source_events=list(observation.source_events),
            session_date=_aware(observation.session_date or observation.detected_at),
        )
    )


def _vwap_reclaim_event(analyzer: PriceActionAnalyzer, *, ts: datetime, previous_events: list[Any] | None = None):
    bars = [
        _bar("SPY", ts - timedelta(minutes=7), "99.20"),
        _bar("SPY", ts - timedelta(minutes=6), "99.10"),
        _bar("SPY", ts - timedelta(minutes=5), "99.30"),
        _bar("SPY", ts - timedelta(minutes=4), "99.25"),
        _bar("SPY", ts - timedelta(minutes=3), "99.35"),
        _bar("SPY", ts - timedelta(minutes=2), "100.20"),
        _bar("SPY", ts - timedelta(minutes=1), "100.30"),
        _bar("SPY", ts, "100.40"),
    ]
    events = analyzer.analyze(
        symbol="SPY",
        bars=bars,
        current_price=Decimal("100.40"),
        previous_close=Decimal("99.00"),
        session_vwap=Decimal("100.00"),
        ranges=OpeningRangeLevels(),
        previous_events=previous_events,
    )
    return next(event for event in events if event.event_type == PriceActionEventType.VWAP_RECLAIM)


def _above_vwap_event(analyzer: PriceActionAnalyzer, *, ts: datetime, previous_events: list[Any] | None = None):
    bars = [
        _bar("SPY", ts - timedelta(minutes=2), "100.10"),
        _bar("SPY", ts - timedelta(minutes=1), "100.15"),
        _bar("SPY", ts, "100.25"),
    ]
    events = analyzer.analyze(
        symbol="SPY",
        bars=bars,
        current_price=Decimal("100.25"),
        previous_close=Decimal("99.80"),
        session_vwap=Decimal("100.00"),
        ranges=OpeningRangeLevels(),
        previous_events=previous_events,
    )
    return next(event for event in events if event.event_type == PriceActionEventType.ABOVE_VWAP)


def _qqq_weak_observation(engine: MarketObservationEngine, *, ts: datetime, gap: str, source_events: list[str]):
    qqq_change = Decimal(gap)
    spy_change = Decimal("0.00")
    return engine.build(
        [
            {
                "signal": DivergenceSignal.QQQ_UNDERPERFORMING_SPY,
                "symbols": ["QQQ", "SPY"],
                "confidence": 90,
                "importance": 80,
                "facts": [f"QQQ underperforms SPY by {abs(qqq_change - spy_change):.2f} percentage points"],
                "detected_at": ts,
                "supporting_data": {
                    "QQQ": {"change_percent": qqq_change, "above_vwap": False, "structure": "DOWNTREND", "momentum": "WEAK"},
                    "SPY": {"change_percent": spy_change, "above_vwap": True, "structure": "UPTREND", "momentum": "STRONG"},
                },
                "source_events": source_events,
            }
        ]
    )[0]


def test_price_action_repository_round_trip_and_dedup(db_session):
    repo = Repository(db_session)
    analyzer = PriceActionAnalyzer(confirm_bars=3)
    now = datetime.now(UTC)

    event = _above_vwap_event(analyzer, ts=now)
    stored = _save_price_action(repo, event)
    repo.commit()
    repo.refresh(stored)

    assert stored.event_id == event.event_id
    assert stored.symbol == "SPY"
    assert stored.event_type == PriceActionEventType.ABOVE_VWAP.value
    assert stored.detected_at.tzinfo is not None
    assert stored.confidence == event.confidence
    assert stored.importance == event.importance
    assert stored.evidence_json == list(event.evidence)
    assert stored.supporting_values_json["session_vwap"] == "100.00"
    assert stored.lifecycle == EventLifecycle.CONFIRMED.value
    assert stored.session_date.date() == now.date()

    fetched = repo.get_price_action_event_by_event_id(_require(event.event_id))
    assert fetched is not None
    assert fetched.event_id == stored.event_id

    duplicate = _save_price_action(repo, event)
    repo.commit()
    assert duplicate.id == stored.id
    assert len(repo.get_recent_price_action_events(limit=10, symbol="SPY", event_type=PriceActionEventType.ABOVE_VWAP.value)) == 1

    latest = repo.get_recent_price_action_events(limit=5, symbol="SPY", event_type=PriceActionEventType.ABOVE_VWAP.value)
    assert latest[0].event_id == event.event_id


def test_price_action_active_lookup_lifecycle_and_transition_guard(db_session):
    repo = Repository(db_session)
    analyzer = PriceActionAnalyzer(confirm_bars=3)
    now = datetime.now(UTC)

    active = _save_price_action(repo, _above_vwap_event(analyzer, ts=now))
    repo.commit()
    repo.refresh(active)

    lookup = repo.get_active_price_action_event("SPY", PriceActionEventType.ABOVE_VWAP.value)
    assert lookup is not None
    assert lookup.event_id == active.event_id

    resolved = repo.resolve_price_action_event(active.event_id)
    assert resolved.lifecycle == EventLifecycle.RESOLVED.value
    assert resolved.resolved_at is not None
    assert repo.get_active_price_action_event("SPY", PriceActionEventType.ABOVE_VWAP.value) is None

    with pytest.raises(ValueError):
        repo.update_price_action_event_lifecycle(active.event_id, EventLifecycle.CONFIRMED)

    second_cycle = _save_price_action(repo, _above_vwap_event(analyzer, previous_events=[resolved], ts=now + timedelta(minutes=5)))
    repo.commit()
    assert second_cycle.event_id != active.event_id
    assert len(repo.get_recent_price_action_events(limit=10, symbol="SPY", event_type=PriceActionEventType.ABOVE_VWAP.value)) == 2


def test_price_action_duplicate_behavior_transaction_safety_and_validation(db_session):
    repo = Repository(db_session)
    analyzer = PriceActionAnalyzer(confirm_bars=3)
    now = datetime.now(UTC)
    event = _save_price_action(repo, _above_vwap_event(analyzer, ts=now))
    repo.commit()

    with pytest.raises(ValueError):
        _run(
            repo.save_price_action_event(
                event_id="invalid",
                symbol="SPY",
                event_type=PriceActionEventType.ABOVE_VWAP.value,
                lifecycle=EventLifecycle.CONFIRMED.value,
                detected_at=now,
                confidence=101,
                importance=50,
                evidence=[],
                supporting_values={},
                session_date=now,
            )
        )
    repo.session.rollback()

    duplicate = _save_price_action(repo, event)
    repo.commit()
    assert duplicate.id == event.id

    with pytest.raises(ValueError):
        repo.update_price_action_event_lifecycle(event.event_id, EventLifecycle.CONFIRMED)

    repo.resolve_price_action_event(event.event_id)
    valid_again = _run(
        repo.save_price_action_event(
            event_id="SPY:2026-07-13:ABOVE_VWAP:manual:occ999",
            symbol="SPY",
            event_type=PriceActionEventType.ABOVE_VWAP.value,
            lifecycle=EventLifecycle.DETECTED.value,
            detected_at=now + timedelta(minutes=2),
            confidence=80,
            importance=50,
            evidence=["manual fallback"],
            supporting_values={"value": Decimal("1.25")},
            session_date=now + timedelta(minutes=2),
        )
    )
    repo.commit()
    assert valid_again.event_id.endswith("occ999")


def test_price_action_end_to_end_dedup_and_new_lifecycle_occurrence(db_session):
    repo = Repository(db_session)
    analyzer = PriceActionAnalyzer(confirm_bars=3)
    now = datetime.now(UTC)

    above_vwap_event = _above_vwap_event(analyzer, ts=now)
    for _ in range(5):
        stored = _save_price_action(repo, above_vwap_event)
        repo.commit()
        assert stored.event_id == above_vwap_event.event_id
    assert len(repo.get_recent_price_action_events(limit=10, symbol="SPY", event_type=PriceActionEventType.ABOVE_VWAP.value)) == 1

    reclaim_event = _vwap_reclaim_event(analyzer, ts=now)
    reclaim_record = _save_price_action(repo, reclaim_event)
    repo.commit()
    assert reclaim_record.event_id == reclaim_event.event_id

    for _ in range(5):
        stored = _save_price_action(repo, reclaim_event)
        repo.commit()
        assert stored.event_id == reclaim_event.event_id
    assert len(repo.get_recent_price_action_events(limit=10, symbol="SPY", event_type=PriceActionEventType.VWAP_RECLAIM.value)) == 1

    resolved = repo.resolve_price_action_event(_require(reclaim_event.event_id))
    assert resolved.lifecycle == EventLifecycle.RESOLVED.value

    new_reclaim = _vwap_reclaim_event(analyzer, previous_events=[resolved], ts=now + timedelta(minutes=15))
    new_stored = _save_price_action(repo, new_reclaim)
    repo.commit()
    assert new_stored.event_id != reclaim_event.event_id
    assert len(repo.get_recent_price_action_events(limit=10, symbol="SPY", event_type=PriceActionEventType.VWAP_RECLAIM.value)) == 2


def test_market_observation_repository_round_trip_and_filters(db_session):
    repo = Repository(db_session)
    engine = MarketObservationEngine()
    now = datetime.now(UTC)

    observation = _qqq_weak_observation(engine, ts=now, gap="-0.25", source_events=["cycle-1"])
    stored = _save_observation(repo, observation)
    repo.commit()
    repo.refresh(stored)

    assert stored.observation_key == observation.observation_key
    assert stored.observation_type == ObservationType.QQQ_WEAKER_THAN_SPY.value
    assert stored.symbols_json == ["QQQ", "SPY"]
    assert stored.detected_at.tzinfo is not None
    assert stored.importance_score == 80
    assert stored.confidence_score == 90
    assert stored.facts_json[0].startswith("QQQ underperforms SPY")
    assert stored.source_events_json == ["cycle-1"]
    assert stored.session_date.date() == now.date()

    fetched = repo.get_market_observation_by_key(_require(observation.observation_key))
    assert fetched is not None
    assert fetched.observation_key == stored.observation_key

    duplicate = _save_observation(repo, observation)
    repo.commit()
    assert duplicate.id == stored.id
    assert len(repo.get_recent_market_observations(limit=10)) == 1

    same_type = _qqq_weak_observation(engine, ts=now + timedelta(minutes=1), gap="-0.25", source_events=["cycle-1"])
    _save_observation(repo, same_type)
    repo.commit()
    assert len(repo.get_market_observations_by_type(ObservationType.QQQ_WEAKER_THAN_SPY.value, limit=10)) == 1
    assert len(repo.get_high_importance_market_observations(minimum_importance=75, limit=10)) == 1


def test_market_observation_material_change_policy_and_recurrence(db_session):
    repo = Repository(db_session)
    engine = MarketObservationEngine()
    now = datetime.now(UTC)

    first = _qqq_weak_observation(engine, ts=now, gap="-0.25", source_events=["cycle-1"])
    second = _qqq_weak_observation(engine, ts=now + timedelta(minutes=1), gap="-0.28", source_events=["cycle-1"])
    third = _qqq_weak_observation(engine, ts=now + timedelta(minutes=2), gap="-0.90", source_events=["cycle-1"])
    recurrence = _qqq_weak_observation(engine, ts=now + timedelta(minutes=20), gap="-0.25", source_events=["cycle-2"])

    for observation in (first, second):
        _save_observation(repo, observation)
        repo.commit()
    assert len(repo.get_recent_market_observations(limit=10)) == 1
    fetched_first = repo.get_market_observation_by_key(_require(first.observation_key))
    assert fetched_first is not None
    assert fetched_first.observation_key == first.observation_key

    _save_observation(repo, third)
    repo.commit()
    assert len(repo.get_recent_market_observations(limit=10)) == 2

    _save_observation(repo, recurrence)
    repo.commit()
    assert len(repo.get_recent_market_observations(limit=10)) == 3

    assert repo.get_market_observations_by_type(ObservationType.QQQ_WEAKER_THAN_SPY.value, limit=10)[0].observation_key == recurrence.observation_key
    assert len(repo.get_high_importance_market_observations(minimum_importance=81, limit=10)) == 0


def test_json_serialization_timezone_and_bounded_scores(db_session):
    repo = Repository(db_session)
    now = datetime.now(UTC)

    event = _run(
        repo.save_price_action_event(
            event_id="SPY:2026-07-13:JSON:occ001",
            symbol="SPY",
            event_type=PriceActionEventType.ABOVE_VWAP.value,
            lifecycle=EventLifecycle.CONFIRMED.value,
            detected_at=now,
            confidence=100,
            importance=0,
            evidence=["decimal", Decimal("1.25"), {"nested": Decimal("2.50")}, None],
            supporting_values={
                "price": Decimal("749.10"),
                "observed_at": now,
                "enum": EventLifecycle.CONFIRMED,
                "nested": {"values": [Decimal("3.25"), None]},
            },
            session_date=now,
        )
    )
    repo.commit()
    repo.refresh(event)
    assert event.detected_at.tzinfo is not None
    assert event.session_date.tzinfo is not None
    assert event.supporting_values_json["price"] == "749.10"
    assert event.supporting_values_json["observed_at"].endswith("+00:00")
    assert event.supporting_values_json["enum"] == EventLifecycle.CONFIRMED.value
    assert event.supporting_values_json["nested"]["values"][0] == "3.25"

    with pytest.raises(ValueError):
        _run(
            repo.save_price_action_event(
                event_id="SPY:2026-07-13:JSON:bad-low",
                symbol="SPY",
                event_type=PriceActionEventType.ABOVE_VWAP.value,
                lifecycle=EventLifecycle.CONFIRMED.value,
                detected_at=now,
                confidence=-1,
                importance=50,
                evidence=[],
                supporting_values={},
                session_date=now,
            )
        )
    repo.session.rollback()

    with pytest.raises(ValueError):
        _run(
            repo.save_market_observation(
                observation_key="obs:bad-high",
                observation_type=ObservationType.QQQ_WEAKER_THAN_SPY.value,
                symbols=["QQQ", "SPY"],
                detected_at=now,
                importance_score=101,
                confidence_score=50,
                facts=["bad"],
                interpretation_boundary="boundary",
                source_events=["evt"],
                session_date=now,
            )
        )
    repo.session.rollback()

    valid = _run(
        repo.save_market_observation(
            observation_key="obs:good",
            observation_type=ObservationType.QQQ_WEAKER_THAN_SPY.value,
            symbols=["QQQ", "SPY"],
            detected_at=now,
            importance_score=100,
            confidence_score=0,
            facts=["good", Decimal("4.50"), {"gap": Decimal("0.25"), "when": now}],
            interpretation_boundary="boundary",
            source_events=["evt"],
            session_date=now,
        )
    )
    repo.commit()
    repo.refresh(valid)
    assert valid.facts_json[1] == "4.50"
    assert valid.facts_json[2]["gap"] == "0.25"
    assert valid.detected_at.tzinfo is not None
