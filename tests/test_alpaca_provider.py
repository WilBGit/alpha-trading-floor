from __future__ import annotations

from datetime import UTC, datetime

import pytest

from alpha_market_engine.market.session import MarketSession
from alpha_market_engine.market.providers.alpaca import AlpacaMarketDataProvider


class _Stamp:
    def __init__(self, value: datetime) -> None:
        self._value = value

    def to_pydatetime(self) -> datetime:
        return self._value


class _FakeCalendar:
    def __init__(self, open_time: datetime, close_time: datetime) -> None:
        self._open_time = open_time
        self._close_time = close_time

    def session_open(self, session_label):  # noqa: D401 - test double
        return _Stamp(self._open_time)

    def session_close(self, session_label):  # noqa: D401 - test double
        return _Stamp(self._close_time)

    def date_to_session(self, value, direction="none"):
        return "2026-07-14"


class _FakeSessionCalculator:
    timezone = UTC

    def __init__(self, open_time: datetime, close_time: datetime) -> None:
        self.calendar = _FakeCalendar(open_time, close_time)

    def current_session(self, value):
        return MarketSession.PREMARKET

    def most_recent_completed_session(self, value):
        return "2026-07-13"


class FakeAlpacaProvider(AlpacaMarketDataProvider):
    def __init__(self, responses: dict[tuple[str, tuple[tuple[str, object], ...]], dict[str, object]]) -> None:
        super().__init__(api_key="key", api_secret="secret", data_feed="iex")
        self._responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def _request(self, path: str, params: dict[str, object] | None = None) -> dict[str, object]:
        captured = params or {}
        self.calls.append((path, captured))
        key = (path, tuple(sorted(captured.items())))
        response = self._responses.get(key)
        if response is None:
            raise AssertionError(f"unexpected request: {path} {captured}")
        return response


@pytest.mark.asyncio
async def test_alpaca_bar_fetch_uses_most_recent_completed_session_during_premarket() -> None:
    session_open = datetime(2026, 7, 13, 13, 30, tzinfo=UTC)
    session_close = datetime(2026, 7, 13, 20, 0, tzinfo=UTC)
    provider = FakeAlpacaProvider(
        {
            (
                "/v2/stocks/bars",
                (
                    ("end", "2026-07-13T20:00:00Z"),
                    ("feed", "iex"),
                    ("limit", 390),
                    ("start", "2026-07-13T13:30:00Z"),
                    ("symbols", "SPY"),
                    ("timeframe", "1Min"),
                ),
            ): {
                "bars": {
                    "SPY": [
                        {
                            "t": "2026-07-13T13:30:00Z",
                            "o": "752.470000",
                            "h": "753.200000",
                            "l": "752.400000",
                            "c": "753.170000",
                            "v": 114204,
                            "vw": "752.900000",
                        }
                    ]
                }
            }
        }
    )
    provider._session_calculator = _FakeSessionCalculator(session_open, session_close)  # type: ignore[assignment]

    bars = await provider.get_intraday_bars("SPY", "1m", limit=390)
    assert len(bars) == 1
    assert bars[0].timestamp == datetime(2026, 7, 13, 13, 30, tzinfo=UTC)
    assert len(provider.calls) == 1
    assert provider.calls[0][1]["start"] == "2026-07-13T13:30:00Z"
    assert provider.calls[0][1]["end"] == "2026-07-13T20:00:00Z"


@pytest.mark.asyncio
async def test_alpaca_bar_fetch_uses_current_regular_session_and_one_minute_mapping() -> None:
    session_open = datetime(2026, 7, 14, 13, 30, tzinfo=UTC)
    session_close = datetime(2026, 7, 14, 20, 0, tzinfo=UTC)

    class _RegularSessionCalculator(_FakeSessionCalculator):
        def current_session(self, value):  # type: ignore[override]
            return MarketSession.REGULAR

    provider = FakeAlpacaProvider(
        {
            (
                "/v2/stocks/bars",
                (
                    ("end", "2026-07-14T20:00:00Z"),
                    ("feed", "iex"),
                    ("limit", 390),
                    ("start", "2026-07-14T13:30:00Z"),
                    ("symbols", "SPY"),
                    ("timeframe", "1Min"),
                ),
            ): {"bars": {"SPY": []}}
        }
    )
    provider._session_calculator = _RegularSessionCalculator(session_open, session_close)  # type: ignore[assignment]

    bars = await provider.get_intraday_bars("SPY", "1m", limit=390)

    assert bars == []
    assert provider.calls[0][0] == "/v2/stocks/bars"
    assert provider.calls[0][1]["timeframe"] == "1Min"
    assert provider.calls[0][1]["start"] == "2026-07-14T13:30:00Z"
    assert provider.calls[0][1]["end"] == "2026-07-14T20:00:00Z"
