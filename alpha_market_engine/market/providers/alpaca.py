from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from ..streaming.state import DeliveryMode, classify_market_data_source
from ..data_provider import (
    MarketDataAuthenticationError,
    MarketDataError,
    MarketDataProvider,
    MarketDataRateLimitError,
    MarketDataValidationError,
)
from ..session import MarketSession, MarketSessionCalculator


from ..models import IntradayBar, MarketBreadth, MarketQuote, ProviderHealth, SectorPerformance


SECTOR_ETFS: dict[str, str] = {
    "Technology": "XLK",
    "Financials": "XLF",
    "Energy": "XLE",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Industrials": "XLI",
    "Utilities": "XLU",
    "Health Care": "XLV",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}


@dataclass(slots=True)
class ParsedQuotePayload:
    price: Decimal
    previous_close: Decimal | None
    change: Decimal | None
    change_percent: Decimal | None
    day_high: Decimal | None
    day_low: Decimal | None
    open_price: Decimal | None
    volume: int | None
    quote_timestamp: datetime
    is_delayed: bool
    raw_data: dict[str, Any]


def _normalize_timeframe(timeframe: str) -> str:
    text = timeframe.strip()
    lower = text.lower()
    if lower.endswith("m") and lower[:-1].isdigit():
        return f"{int(lower[:-1])}Min"
    return text


class AlpacaMarketDataProvider(MarketDataProvider):
    provider_name = "alpaca"
    supports_batch_quotes = True

    def __init__(self, api_key: str, api_secret: str, *, base_url: str = "https://data.alpaca.markets", data_feed: str = "iex", timeout_seconds: float = 15.0) -> None:
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.base_url = base_url.rstrip("/")
        self.data_feed = data_feed.strip() if data_feed else None
        self.timeout_seconds = timeout_seconds
        self._session_calculator = MarketSessionCalculator()
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        }

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = await self._client.get(f"{self.base_url}{path}", headers=self._headers(), params=params)
        except httpx.TimeoutException as exc:
            raise MarketDataError(f"timeout fetching market data: {path}") from exc
        except httpx.HTTPError as exc:
            raise MarketDataError(f"transport error fetching market data: {path}") from exc

        if response.status_code in {401, 403}:
            raise MarketDataAuthenticationError(f"authentication failed: {response.text[:200]}")
        if response.status_code == 429:
            raise MarketDataRateLimitError(f"rate limit exceeded: {response.text[:200]}")
        if response.status_code >= 500:
            raise MarketDataError(f"upstream error {response.status_code}: {response.text[:200]}")
        if response.status_code >= 400:
            raise MarketDataValidationError(f"bad request {response.status_code}: {response.text[:200]}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise MarketDataValidationError("invalid JSON from Alpaca") from exc
        if not isinstance(payload, dict):
            raise MarketDataValidationError("unexpected Alpaca response shape")
        return payload

    @staticmethod
    def _quote_timestamp_from_payload(payload: dict[str, Any]) -> datetime:
        raw_timestamp = payload.get("t") or payload.get("timestamp") or payload.get("quote_timestamp")
        if raw_timestamp is None:
            return datetime.now(UTC)
        if isinstance(raw_timestamp, datetime):
            return raw_timestamp if raw_timestamp.tzinfo else raw_timestamp.replace(tzinfo=UTC)
        text = str(raw_timestamp)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed

    @classmethod
    def _parse_quote(cls, symbol: str, payload: dict[str, Any], *, is_delayed: bool) -> MarketQuote:
        bid = payload.get("bp")
        ask = payload.get("ap")
        price = payload.get("p") or payload.get("price")
        previous_close = payload.get("pc") or payload.get("previous_close")
        if price is None and bid is not None and ask is not None:
            price = (Decimal(str(bid)) + Decimal(str(ask))) / Decimal("2")
        if price is None:
            raise MarketDataValidationError(f"quote missing price for {symbol}")

        change = payload.get("change")
        change_percent = payload.get("change_percent") or payload.get("percent_change") or payload.get("pct_change")
        if isinstance(change_percent, (list, tuple, dict)):
            change_percent = None
        if change is None and previous_close is not None:
            change = Decimal(str(price)) - Decimal(str(previous_close))
        if change_percent is None and previous_close is not None and Decimal(str(previous_close)) != 0:
            change_percent = (Decimal(str(price)) - Decimal(str(previous_close))) / Decimal(str(previous_close)) * Decimal("100")
        classification = classify_market_data_source(
            payload.get("feed") or payload.get("data_feed") or None,
            delivery_mode=DeliveryMode.REST,
            event_timestamp=cls._quote_timestamp_from_payload(payload),
            source="REST_QUOTE",
        )
        raw_data = {
            "raw_source": "alpaca",
            "raw_keys": sorted(payload.keys()),
            "feed_scope": classification.feed_scope.value,
            "delivery_mode": classification.delivery_mode.value,
            "delay_status": classification.delay_status.value,
            "freshness_status": classification.freshness_status.value,
            "data_feed_kind": classification.data_feed.value,
        }
        return MarketQuote(
            symbol=symbol,
            price=price,
            previous_close=previous_close,
            change=change,
            change_percent=change_percent,
            open_price=payload.get("o") or payload.get("open"),
            day_high=payload.get("h") or payload.get("high"),
            day_low=payload.get("l") or payload.get("low"),
            volume=payload.get("v") or payload.get("volume"),
            quote_timestamp=classification.event_timestamp or cls._quote_timestamp_from_payload(payload),
            provider=cls.provider_name,
            data_feed=payload.get("feed") or payload.get("data_feed") or None,
            is_delayed=classification.delay_status.value == "DELAYED",
            vwap=payload.get("vw") or payload.get("vwap"),
            trade_count=payload.get("n") or payload.get("trade_count"),
            validation_metadata=raw_data,
        )

    @staticmethod
    def _extract_quote_payloads(response: dict[str, Any]) -> dict[str, dict[str, Any]]:
        if isinstance(response.get("quotes"), dict):
            return response["quotes"]
        if isinstance(response.get("quote"), dict):
            quote = response["quote"]
            symbol = quote.get("S") or quote.get("symbol")
            return {symbol: quote} if symbol else {}
        if isinstance(response.get("bars"), dict):
            return response["bars"]
        return {}

    async def get_quote(self, symbol: str) -> MarketQuote:
        response = await self._request("/v2/stocks/quotes/latest", params={"symbols": symbol, "feed": self.data_feed})
        payloads = self._extract_quote_payloads(response)
        payload = payloads.get(symbol)
        if not payload:
            raise MarketDataValidationError(f"missing quote for symbol: {symbol}")
        return self._parse_quote(symbol, payload, is_delayed=(self.data_feed or "").lower() != "sip")

    async def get_quotes(self, symbols: list[str]) -> dict[str, MarketQuote]:
        if not symbols:
            return {}
        response = await self._request("/v2/stocks/quotes/latest", params={"symbols": ",".join(symbols), "feed": self.data_feed})
        payloads = self._extract_quote_payloads(response)
        quotes: dict[str, MarketQuote] = {}
        for symbol in symbols:
            payload = payloads.get(symbol)
            if not payload:
                continue
            try:
                quotes[symbol] = self._parse_quote(symbol, payload, is_delayed=(self.data_feed or "").lower() != "sip")
            except MarketDataValidationError:
                continue
        if not quotes:
            raise MarketDataValidationError(f"no quotes returned for symbols: {', '.join(symbols)}")
        return quotes

    async def get_intraday_bars(self, symbol: str, timeframe: str, limit: int | None = None) -> list[IntradayBar]:
        now = datetime.now(UTC)
        session = self._session_calculator.current_session(now)
        if session == MarketSession.REGULAR:
            session_label = self._session_calculator.calendar.date_to_session(now.date(), direction="none")
        else:
            session_label = self._session_calculator.most_recent_completed_session(now)
        session_open = self._session_calculator.calendar.session_open(session_label).to_pydatetime().astimezone(self._session_calculator.timezone)
        session_close = self._session_calculator.calendar.session_close(session_label).to_pydatetime().astimezone(self._session_calculator.timezone)
        normalized_timeframe = _normalize_timeframe(timeframe)
        params: dict[str, Any] = {
            "symbols": symbol,
            "timeframe": normalized_timeframe,
            "feed": self.data_feed,
            "start": session_open.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "end": session_close.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }
        if limit is not None:
            params["limit"] = limit
        response = await self._request("/v2/stocks/bars", params=params)
        bars_payload = response.get("bars", {})
        raw_bars = bars_payload.get(symbol) if isinstance(bars_payload, dict) else None
        if not raw_bars:
            return []
        parsed: list[IntradayBar] = []
        for item in raw_bars:
            try:
                timestamp = item.get("t") or item.get("timestamp")
                if isinstance(timestamp, str) and timestamp.endswith("Z"):
                    timestamp = timestamp[:-1] + "+00:00"
                dt = datetime.fromisoformat(str(timestamp)) if not isinstance(timestamp, datetime) else timestamp
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                parsed.append(
                    IntradayBar(
                        symbol=symbol,
                        timestamp=dt,
                        open=item.get("o") or item.get("open"),
                        high=item.get("h") or item.get("high"),
                        low=item.get("l") or item.get("low"),
                        close=item.get("c") or item.get("close"),
                        volume=int(item.get("v") or item.get("volume") or 0),
                        trade_count=item.get("n") or item.get("trade_count"),
                        vwap=item.get("vw") or item.get("vwap"),
                        provider=self.provider_name,
                        data_feed=self.data_feed,
                        validation_metadata={
                            "raw_source": "alpaca",
                            "raw_keys": sorted(item.keys()),
                            "feed_scope": "SINGLE_VENUE" if (self.data_feed or "").lower() == "iex" else "CONSOLIDATED" if (self.data_feed or "").lower() in {"sip", "delayed_sip"} else "UNKNOWN",
                            "delivery_mode": "REST",
                            "delay_status": "REALTIME" if (self.data_feed or "").lower() == "iex" else "UNKNOWN",
                        },
                    )
                )
            except Exception as exc:
                raise MarketDataValidationError(f"invalid bar payload for {symbol}: {exc}") from exc
        return parsed

    async def get_market_indices(self) -> dict[str, MarketQuote]:
        return await self.get_quotes(["SPY", "QQQ", "DIA", "IWM"])

    async def get_sector_performance(self) -> list[SectorPerformance] | None:
        quotes = await self.get_quotes(list(SECTOR_ETFS.values()))
        now = datetime.now(UTC)
        performance: list[SectorPerformance] = []
        for sector, symbol in SECTOR_ETFS.items():
            quote = quotes.get(symbol)
            if quote is None or quote.change_percent is None:
                continue
            performance.append(
                SectorPerformance(
                    sector=sector,
                    change_percent=quote.change_percent,
                    provider=self.provider_name,
                    quote_timestamp=quote.quote_timestamp,
                    data_feed=self.data_feed,
                )
            )
        if not performance:
            return None
        return performance

    async def get_market_breadth(self) -> MarketBreadth | None:
        return None

    async def health_check(self) -> ProviderHealth:
        try:
            quote = await self.get_quote("SPY")
            return ProviderHealth(
                provider=self.provider_name,
                healthy=True,
                checked_at=datetime.now(UTC),
                message="alpaca quotes reachable",
                data_feed=self.data_feed,
                metadata={"symbol": quote.symbol, "delayed": quote.is_delayed},
            )
        except Exception as exc:
            return ProviderHealth(
                provider=self.provider_name,
                healthy=False,
                checked_at=datetime.now(UTC),
                message=str(exc),
                data_feed=self.data_feed,
                metadata={"error_type": type(exc).__name__},
            )
