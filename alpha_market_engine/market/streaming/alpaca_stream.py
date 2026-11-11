from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import websockets

from ..models import IntradayBar
from .base import MarketDataStream
from .state import (
    DelayStatus,
    DeliveryMode,
    FeedScope,
    LiveMarketStateStore,
    StreamHealth,
    classify_market_data_source,
)


DEFAULT_STALE_AFTER_SECONDS = 30
DEFAULT_DISCONNECTED_AFTER_SECONDS = 120


def bounded_exponential_backoff(attempt: int, *, max_seconds: int = 30, base_seconds: int = 1, jitter_ratio: float = 0.2, rng: random.Random | None = None) -> int:
    delay = min(max_seconds, base_seconds * (2 ** max(0, attempt)))
    if jitter_ratio <= 0:
        return delay
    rng = rng or random.Random()
    jitter = delay * jitter_ratio
    adjusted = delay + rng.uniform(-jitter, jitter)
    return max(1, min(max_seconds, int(round(adjusted))))


class AlpacaMarketDataStream(MarketDataStream):
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        feed: str = "iex",
        symbols: Iterable[str] | None = None,
        state_store: LiveMarketStateStore | None = None,
        base_url: str = "wss://stream.data.alpaca.markets",
        stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
        disconnected_after_seconds: int = DEFAULT_DISCONNECTED_AFTER_SECONDS,
        reconnect_max_seconds: int = 30,
    ) -> None:
        super().__init__(state_store=state_store)
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.feed = feed.strip() if feed else "iex"
        self.symbols = tuple(symbols or ())
        self.base_url = base_url.rstrip("/")
        self.stale_after_seconds = stale_after_seconds
        self.disconnected_after_seconds = disconnected_after_seconds
        self.reconnect_max_seconds = reconnect_max_seconds
        self._stop_event = asyncio.Event()
        self._socket = None
        self._authenticated = False
        self._connected = False
        self._subscribed_symbols: set[str] = set()
        self._last_error: str | None = None

    def _url(self) -> str:
        return f"{self.base_url}/v2/{self.feed}"

    def _auth_message(self) -> str:
        return json.dumps({"action": "auth", "key": self.api_key, "secret": self.api_secret})

    def _subscription_message(self, symbols: Iterable[str]) -> str:
        symbols = list(dict.fromkeys(symbols))
        return json.dumps({"action": "subscribe", "trades": symbols, "quotes": symbols, "bars": symbols})

    async def connect(self) -> None:
        self._socket = await websockets.connect(self._url(), ping_interval=20, ping_timeout=20, max_queue=100)
        self._connected = True
        self.state_store.set_connection(connected=True, authenticated=False, subscribed_symbols=set(self._subscribed_symbols), last_error=None)
        await self._socket.send(self._auth_message())
        self.state_store.last_message_time = datetime.now(UTC)
        self.state_store.set_connection(last_message_time=self.state_store.last_message_time)
        auth_response = await self._socket.recv()
        if isinstance(auth_response, str):
            payload = json.loads(auth_response)
        else:
            payload = json.loads(auth_response.decode())
        messages = payload if isinstance(payload, list) else [payload]
        success = any((msg.get("T") == "success" or msg.get("msg") == "authenticated" or msg.get("action") == "auth") for msg in messages if isinstance(msg, dict))
        if not success:
            raise RuntimeError(f"alpaca websocket authentication failed: {payload!r}")
        self._authenticated = True
        self.state_store.set_connection(authenticated=True, last_message_time=datetime.now(UTC))
        if self.symbols:
            await self.subscribe(self.symbols)

    async def subscribe(self, symbols: Iterable[str]) -> None:
        symbols = tuple(dict.fromkeys(symbols))
        if not symbols:
            return
        if self._socket is None:
            raise RuntimeError("stream not connected")
        await self._socket.send(self._subscription_message(symbols))
        self._subscribed_symbols.update(symbols)
        self.state_store.set_connection(subscribed_symbols=set(self._subscribed_symbols), last_message_time=datetime.now(UTC))

    async def unsubscribe(self, symbols: Iterable[str]) -> None:
        symbols = tuple(dict.fromkeys(symbols))
        if not symbols or self._socket is None:
            return
        await self._socket.send(json.dumps({"action": "unsubscribe", "trades": list(symbols), "quotes": list(symbols), "bars": list(symbols)}))
        self._subscribed_symbols.difference_update(symbols)
        self.state_store.set_connection(subscribed_symbols=set(self._subscribed_symbols), last_message_time=datetime.now(UTC))

    def _parse_timestamp(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    def _handle_message(self, payload: Any) -> None:
        messages = payload if isinstance(payload, list) else [payload]
        for message in messages:
            if not isinstance(message, dict):
                continue
            msg_type = message.get("T")
            if msg_type in {"success", "subscription"}:
                continue
            symbol = message.get("S") or message.get("symbol")
            if not symbol:
                continue
            timestamp = self._parse_timestamp(message.get("t") or message.get("timestamp") or datetime.now(UTC))
            if msg_type == "t":
                self.state_store.update_trade(
                    symbol,
                    price=Decimal(str(message.get("p") or message.get("price"))),
                    timestamp=timestamp,
                    provider="alpaca",
                    feed=self.feed,
                    feed_scope=FeedScope.SINGLE_VENUE if self.feed.lower() == "iex" else FeedScope.CONSOLIDATED if self.feed.lower() in {"sip", "delayed_sip"} else FeedScope.UNKNOWN,
                    delay_status=DelayStatus.REALTIME if self.feed.lower() == "iex" else DelayStatus.UNKNOWN,
                    delivery_mode=DeliveryMode.WEBSOCKET,
                )
            elif msg_type == "q":
                self.state_store.update_quote(
                    symbol,
                    bid=Decimal(str(message.get("bp") or message.get("bid"))) if message.get("bp") or message.get("bid") is not None else None,
                    ask=Decimal(str(message.get("ap") or message.get("ask"))) if message.get("ap") or message.get("ask") is not None else None,
                    timestamp=timestamp,
                    provider="alpaca",
                    feed=self.feed,
                    feed_scope=FeedScope.SINGLE_VENUE if self.feed.lower() == "iex" else FeedScope.CONSOLIDATED if self.feed.lower() in {"sip", "delayed_sip"} else FeedScope.UNKNOWN,
                    delay_status=DelayStatus.REALTIME if self.feed.lower() == "iex" else DelayStatus.UNKNOWN,
                    delivery_mode=DeliveryMode.WEBSOCKET,
                )
            elif msg_type == "b":
                bar = IntradayBar(
                    symbol=symbol,
                    timestamp=timestamp,
                    open=Decimal(str(message.get("o") or message.get("open"))),
                    high=Decimal(str(message.get("h") or message.get("high"))),
                    low=Decimal(str(message.get("l") or message.get("low"))),
                    close=Decimal(str(message.get("c") or message.get("close"))),
                    volume=int(message.get("v") or message.get("volume") or 0),
                    provider="alpaca",
                    data_feed=self.feed,
                    vwap=Decimal(str(message.get("vw") or message.get("vwap"))) if message.get("vw") is not None or message.get("vwap") is not None else None,
                    trade_count=int(message.get("n") or message.get("trade_count") or 0) if message.get("n") is not None or message.get("trade_count") is not None else None,
                )
                self.state_store.update_bar(
                    symbol,
                    bar=bar,
                    provider="alpaca",
                    feed=self.feed,
                    feed_scope=FeedScope.SINGLE_VENUE if self.feed.lower() == "iex" else FeedScope.CONSOLIDATED if self.feed.lower() in {"sip", "delayed_sip"} else FeedScope.UNKNOWN,
                    delay_status=DelayStatus.REALTIME if self.feed.lower() == "iex" else DelayStatus.UNKNOWN,
                    delivery_mode=DeliveryMode.WEBSOCKET,
                )
            self.state_store.set_connection(last_message_time=timestamp)

    async def run(self) -> None:
        attempt = 0
        while not self._stop_event.is_set():
            try:
                await self.connect()
                self.state_store.set_connection(connected=True, authenticated=True, last_error=None)
                attempt = 0
                while not self._stop_event.is_set() and self._socket is not None:
                    raw = await self._socket.recv()
                    payload = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
                    self._handle_message(payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                self.state_store.last_error = self._last_error
                self.state_store.set_connection(connected=False, authenticated=False, reconnect_count=self.state_store.reconnect_count + 1, last_error=self._last_error)
                if self._stop_event.is_set():
                    break
                await asyncio.sleep(bounded_exponential_backoff(attempt, max_seconds=self.reconnect_max_seconds))
                attempt += 1
            finally:
                if self._socket is not None:
                    try:
                        await self._socket.close()
                    except Exception:
                        pass
                    self._socket = None
                self._connected = False
                self._authenticated = False
                self.state_store.set_connection(connected=False, authenticated=False)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._socket is not None:
            try:
                await self._socket.close()
            except Exception:
                pass
            self._socket = None
        self._connected = False
        self._authenticated = False
        self.state_store.set_connection(connected=False, authenticated=False)

    def health_check(self) -> StreamHealth:
        return self.state_store.get_health(
            connected=self._connected,
            authenticated=self._authenticated,
            subscribed_symbols=set(self._subscribed_symbols),
            stale_after_seconds=self.stale_after_seconds,
            disconnected_after_seconds=self.disconnected_after_seconds,
        )
