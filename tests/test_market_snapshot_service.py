from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal

import pytest

from alpha_market_engine.database.models import MarketSnapshot, SystemEvent
from alpha_market_engine.database.repository import Repository
from alpha_market_engine.market.data_provider import MarketBreadth, StaticMarketDataProvider
from alpha_market_engine.market.models import IntradayBar, MarketQuote, MarketSession, SectorPerformance
from alpha_market_engine.market.snapshot import DataQualityStatus, MarketSnapshotService


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def make_quote(symbol: str, price: str, previous_close: str, *, quote_timestamp: datetime = NOW, vwap: str | None = None) -> MarketQuote:
    return MarketQuote(
        symbol=symbol,
        price=Decimal(price),
        previous_close=Decimal(previous_close),
        quote_timestamp=quote_timestamp,
        provider="static",
        data_feed="synthetic",
        vwap=Decimal(vwap) if vwap is not None else None,
    )


def make_bar(symbol: str, timestamp: datetime, open_: str, high: str, low: str, close: str, volume: int, *, vwap: str | None = None) -> IntradayBar:
    return IntradayBar(
        symbol=symbol,
        timestamp=timestamp,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
        vwap=Decimal(vwap) if vwap is not None else None,
        provider="static",
        data_feed="synthetic",
    )


async def build_service(provider, repo=None):
    return MarketSnapshotService(provider, repository=repo, clock=lambda: NOW)


@pytest.mark.asyncio
async def test_vwap_calculation_marks_above_vwap():
    provider = StaticMarketDataProvider(
        quotes={
            "SPY": make_quote("SPY", "102", "100"),
            "QQQ": make_quote("QQQ", "202", "200"),
            "DIA": make_quote("DIA", "302", "300"),
            "IWM": make_quote("IWM", "402", "400"),
            "VIX": make_quote("VIX", "18", "17"),
        },
        breadth=MarketBreadth(advancers=2000, decliners=1500, up_volume=1000000, down_volume=800000, breadth_score=Decimal("0.10"), quote_timestamp=NOW),
        sector_performance=[SectorPerformance(sector="Technology", change_percent=Decimal("1.2"), provider="static", quote_timestamp=NOW)],
        bars={
            "SPY": [make_bar("SPY", NOW - timedelta(minutes=3), "100", "103", "99", "102", 100), make_bar("SPY", NOW - timedelta(minutes=2), "102", "103", "101", "102", 100)],
            "QQQ": [make_bar("QQQ", NOW - timedelta(minutes=3), "200", "201", "199", "200", 100)],
            "DIA": [make_bar("DIA", NOW - timedelta(minutes=3), "300", "301", "299", "300", 100)],
            "IWM": [make_bar("IWM", NOW - timedelta(minutes=3), "400", "401", "399", "400", 100)],
        },
    )
    snapshot = await (await build_service(provider)).create_snapshot()

    assert snapshot.spy is not None
    assert snapshot.spy.above_vwap is True
    assert snapshot.spy.session_vwap is not None
    assert snapshot.data_quality_status in {DataQualityStatus.GOOD, DataQualityStatus.DEGRADED}


@pytest.mark.asyncio
async def test_zero_volume_vwap_returns_none():
    provider = StaticMarketDataProvider(
        quotes={
            "SPY": make_quote("SPY", "102", "100"),
            "QQQ": make_quote("QQQ", "202", "200"),
            "DIA": make_quote("DIA", "302", "300"),
            "IWM": make_quote("IWM", "402", "400"),
            "VIX": make_quote("VIX", "18", "17"),
        },
        breadth=MarketBreadth(advancers=2000, decliners=1500, up_volume=1000000, down_volume=800000, breadth_score=Decimal("0.10"), quote_timestamp=NOW),
        sector_performance=[SectorPerformance(sector="Technology", change_percent=Decimal("1.2"), provider="static", quote_timestamp=NOW)],
        bars={
            "SPY": [make_bar("SPY", NOW - timedelta(minutes=3), "100", "103", "99", "102", 0)],
            "QQQ": [make_bar("QQQ", NOW - timedelta(minutes=3), "200", "201", "199", "200", 100)],
            "DIA": [make_bar("DIA", NOW - timedelta(minutes=3), "300", "301", "299", "300", 100)],
            "IWM": [make_bar("IWM", NOW - timedelta(minutes=3), "400", "401", "399", "400", 100)],
        },
    )
    snapshot = await (await build_service(provider)).create_snapshot()

    assert snapshot.spy is not None
    assert snapshot.spy.session_vwap is None
    assert snapshot.spy.above_vwap is None


@pytest.mark.asyncio
async def test_missing_breadth_produces_degraded_quality():
    provider = StaticMarketDataProvider(
        quotes={
            "SPY": make_quote("SPY", "102", "100"),
            "QQQ": make_quote("QQQ", "202", "200"),
            "DIA": make_quote("DIA", "302", "300"),
            "IWM": make_quote("IWM", "402", "400"),
            "VIX": make_quote("VIX", "18", "17"),
        },
        breadth=None,
        sector_performance=[SectorPerformance(sector="Technology", change_percent=Decimal("1.2"), provider="static", quote_timestamp=NOW)],
        bars={
            "SPY": [make_bar("SPY", NOW - timedelta(minutes=3), "100", "103", "99", "102", 100)],
            "QQQ": [make_bar("QQQ", NOW - timedelta(minutes=3), "200", "201", "199", "200", 100)],
            "DIA": [make_bar("DIA", NOW - timedelta(minutes=3), "300", "301", "299", "300", 100)],
            "IWM": [make_bar("IWM", NOW - timedelta(minutes=3), "400", "401", "399", "400", 100)],
        },
    )
    snapshot = await (await build_service(provider)).create_snapshot()

    assert snapshot.data_quality_status == DataQualityStatus.DEGRADED
    assert "market_breadth" in snapshot.missing_fields


@pytest.mark.asyncio
async def test_missing_vix_produces_degraded_quality():
    provider = StaticMarketDataProvider(
        quotes={
            "SPY": make_quote("SPY", "102", "100"),
            "QQQ": make_quote("QQQ", "202", "200"),
            "DIA": make_quote("DIA", "302", "300"),
            "IWM": make_quote("IWM", "402", "400"),
        },
        breadth=MarketBreadth(advancers=2000, decliners=1500, up_volume=1000000, down_volume=800000, breadth_score=Decimal("0.10"), quote_timestamp=NOW),
        sector_performance=[SectorPerformance(sector="Technology", change_percent=Decimal("1.2"), provider="static", quote_timestamp=NOW)],
        bars={
            "SPY": [make_bar("SPY", NOW - timedelta(minutes=3), "100", "103", "99", "102", 100)],
            "QQQ": [make_bar("QQQ", NOW - timedelta(minutes=3), "200", "201", "199", "200", 100)],
            "DIA": [make_bar("DIA", NOW - timedelta(minutes=3), "300", "301", "299", "300", 100)],
            "IWM": [make_bar("IWM", NOW - timedelta(minutes=3), "400", "401", "399", "400", 100)],
        },
    )
    snapshot = await (await build_service(provider)).create_snapshot()

    assert snapshot.data_quality_status == DataQualityStatus.DEGRADED
    assert "vix" in snapshot.missing_fields


@pytest.mark.asyncio
async def test_premarket_snapshot_vwap_excludes_current_premarket_bars():
    friday_open = datetime(2026, 1, 2, 15, 58, tzinfo=ZoneInfo("America/New_York"))
    friday_close = datetime(2026, 1, 2, 15, 59, tzinfo=ZoneInfo("America/New_York"))
    monday_premarket = datetime(2026, 1, 5, 8, 0, tzinfo=ZoneInfo("America/New_York"))
    provider = StaticMarketDataProvider(
        quotes={
            "SPY": make_quote("SPY", "102", "100", quote_timestamp=datetime(2026, 1, 2, 16, 0, tzinfo=ZoneInfo("America/New_York"))),
            "QQQ": make_quote("QQQ", "202", "200", quote_timestamp=datetime(2026, 1, 2, 16, 0, tzinfo=ZoneInfo("America/New_York"))),
            "DIA": make_quote("DIA", "302", "300", quote_timestamp=datetime(2026, 1, 2, 16, 0, tzinfo=ZoneInfo("America/New_York"))),
            "IWM": make_quote("IWM", "402", "400", quote_timestamp=datetime(2026, 1, 2, 16, 0, tzinfo=ZoneInfo("America/New_York"))),
            "VIX": make_quote("VIX", "18", "17", quote_timestamp=datetime(2026, 1, 2, 16, 0, tzinfo=ZoneInfo("America/New_York"))),
        },
        breadth=MarketBreadth(advancers=2000, decliners=1500, up_volume=1000000, down_volume=800000, breadth_score=Decimal("0.10"), quote_timestamp=datetime(2026, 1, 2, 16, 0, tzinfo=ZoneInfo("America/New_York"))),
        sector_performance=[SectorPerformance(sector="Technology", change_percent=Decimal("1.2"), provider="static", quote_timestamp=datetime(2026, 1, 2, 16, 0, tzinfo=ZoneInfo("America/New_York")))],
        bars={
            "SPY": [
                make_bar("SPY", friday_open, "100", "101", "99", "100.5", 100),
                make_bar("SPY", friday_close, "101", "103", "100", "102", 200),
                make_bar("SPY", monday_premarket, "110", "111", "109", "110", 300),
            ],
            "QQQ": [make_bar("QQQ", friday_close, "200", "201", "199", "200", 100)],
            "DIA": [make_bar("DIA", friday_close, "300", "301", "299", "300", 100)],
            "IWM": [make_bar("IWM", friday_close, "400", "401", "399", "400", 100)],
        },
    )
    premarket_now = datetime(2026, 1, 5, 13, 15, tzinfo=UTC)
    snapshot = await MarketSnapshotService(provider, clock=lambda: premarket_now).create_snapshot()

    assert snapshot.market_session.value == "PREMARKET"
    assert snapshot.spy is not None
    assert snapshot.spy.session_vwap is not None
    assert snapshot.raw_data["bars_count"]["SPY"] == 2
    assert snapshot.raw_data["latest_bar_timestamps"]["SPY"] == friday_close.isoformat()
    assert snapshot.spy.session_vwap == Decimal("101.1666666666666666666666667")


@pytest.mark.asyncio
async def test_stale_core_index_causes_stale_snapshot():
    stale_time = NOW - timedelta(minutes=10)
    provider = StaticMarketDataProvider(
        quotes={
            "SPY": make_quote("SPY", "102", "100", quote_timestamp=stale_time),
            "QQQ": make_quote("QQQ", "202", "200", quote_timestamp=stale_time),
            "DIA": make_quote("DIA", "302", "300", quote_timestamp=stale_time),
            "IWM": make_quote("IWM", "402", "400", quote_timestamp=stale_time),
            "VIX": make_quote("VIX", "18", "17", quote_timestamp=stale_time),
        },
        breadth=MarketBreadth(advancers=2000, decliners=1500, up_volume=1000000, down_volume=800000, breadth_score=Decimal("0.10"), quote_timestamp=stale_time),
        sector_performance=[SectorPerformance(sector="Technology", change_percent=Decimal("1.2"), provider="static", quote_timestamp=stale_time)],
        bars={
            "SPY": [make_bar("SPY", stale_time, "100", "103", "99", "102", 100)],
            "QQQ": [make_bar("QQQ", stale_time, "200", "201", "199", "200", 100)],
            "DIA": [make_bar("DIA", stale_time, "300", "301", "299", "300", 100)],
            "IWM": [make_bar("IWM", stale_time, "400", "401", "399", "400", 100)],
        },
    )
    snapshot = await (await build_service(provider)).create_snapshot()

    assert snapshot.data_quality_status == DataQualityStatus.STALE


class MissingSymbolProvider(StaticMarketDataProvider):
    async def get_market_indices(self):
        data = await super().get_market_indices()
        data.pop("QQQ", None)
        return data


class RecordingRepo:
    def __init__(self):
        self.events: list[dict] = []
        self.snapshots: list[object] = []

    async def add_system_event(self, **kwargs):
        self.events.append(kwargs)
        return kwargs

    async def save_market_snapshot(self, snapshot):
        self.snapshots.append(snapshot)
        return snapshot


@pytest.mark.asyncio
async def test_invalid_snapshot_is_rejected_and_records_error_event():
    provider = MissingSymbolProvider(
        quotes={
            "SPY": make_quote("SPY", "102", "100"),
            "QQQ": make_quote("QQQ", "202", "200"),
            "DIA": make_quote("DIA", "302", "300"),
            "IWM": make_quote("IWM", "402", "400"),
        },
        breadth=MarketBreadth(advancers=2000, decliners=1500, up_volume=1000000, down_volume=800000, breadth_score=Decimal("0.10"), quote_timestamp=NOW),
        sector_performance=[SectorPerformance(sector="Technology", change_percent=Decimal("1.2"), provider="static", quote_timestamp=NOW)],
        bars={
            "SPY": [make_bar("SPY", NOW, "100", "103", "99", "102", 100)],
            "QQQ": [make_bar("QQQ", NOW, "200", "201", "199", "200", 100)],
            "DIA": [make_bar("DIA", NOW, "300", "301", "299", "300", 100)],
            "IWM": [make_bar("IWM", NOW, "400", "401", "399", "400", 100)],
        },
    )
    repo = RecordingRepo()
    snapshot = await (await build_service(provider, repo=repo)).create_snapshot()

    assert snapshot.data_quality_status == DataQualityStatus.INVALID
    assert repo.events and repo.events[0]["severity"] == "ERROR"
    assert not repo.snapshots


@pytest.mark.asyncio
async def test_good_snapshot_is_stored(db_session):
    session = db_session
    repo = Repository(session)
    provider = StaticMarketDataProvider(
        quotes={
            "SPY": make_quote("SPY", "102", "100"),
            "QQQ": make_quote("QQQ", "202", "200"),
            "DIA": make_quote("DIA", "302", "300"),
            "IWM": make_quote("IWM", "402", "400"),
            "VIX": make_quote("VIX", "18", "17"),
        },
        breadth=MarketBreadth(advancers=2000, decliners=1500, up_volume=1000000, down_volume=800000, breadth_score=Decimal("0.10"), quote_timestamp=NOW),
        sector_performance=[SectorPerformance(sector="Technology", change_percent=Decimal("1.2"), provider="static", quote_timestamp=NOW)],
        bars={
            "SPY": [make_bar("SPY", NOW - timedelta(minutes=3), "100", "103", "99", "102", 100)],
            "QQQ": [make_bar("QQQ", NOW - timedelta(minutes=3), "200", "201", "199", "200", 100)],
            "DIA": [make_bar("DIA", NOW - timedelta(minutes=3), "300", "301", "299", "300", 100)],
            "IWM": [make_bar("IWM", NOW - timedelta(minutes=3), "400", "401", "399", "400", 100)],
        },
    )
    snapshot = await (await build_service(provider, repo=repo)).create_snapshot()
    assert snapshot.data_quality_status == DataQualityStatus.GOOD
    assert session.query(MarketSnapshot).count() == 1
    assert session.query(SystemEvent).count() == 0


@pytest.mark.asyncio
async def test_degraded_snapshot_is_stored(db_session):
    session = db_session
    repo = Repository(session)
    provider = StaticMarketDataProvider(
        quotes={
            "SPY": make_quote("SPY", "102", "100"),
            "QQQ": make_quote("QQQ", "202", "200"),
            "DIA": make_quote("DIA", "302", "300"),
            "IWM": make_quote("IWM", "402", "400"),
            "VIX": make_quote("VIX", "18", "17"),
        },
        breadth=None,
        sector_performance=[SectorPerformance(sector="Technology", change_percent=Decimal("1.2"), provider="static", quote_timestamp=NOW)],
        bars={
            "SPY": [make_bar("SPY", NOW - timedelta(minutes=3), "100", "103", "99", "102", 100)],
            "QQQ": [make_bar("QQQ", NOW - timedelta(minutes=3), "200", "201", "199", "200", 100)],
            "DIA": [make_bar("DIA", NOW - timedelta(minutes=3), "300", "301", "299", "300", 100)],
            "IWM": [make_bar("IWM", NOW - timedelta(minutes=3), "400", "401", "399", "400", 100)],
        },
    )
    snapshot = await (await build_service(provider, repo=repo)).create_snapshot()
    assert snapshot.data_quality_status == DataQualityStatus.DEGRADED
    assert session.query(MarketSnapshot).count() == 1
