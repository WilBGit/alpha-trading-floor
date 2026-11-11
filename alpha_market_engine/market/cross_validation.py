from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .brain_types import (
    CrossValidationResult,
    CrossValidationStatus,
    CrossValidationTolerances,
    decimal_or_none,
    pct_difference,
)
from .data_provider import MarketDataProvider
from .models import IntradayBar, MarketQuote
from .session import MarketSessionCalculator
from .streaming.state import assess_session_bar_coverage, calculate_session_vwap


@dataclass(slots=True)
class _ComparableSample:
    value: Decimal | None
    reason: str


class MarketDataCrossValidator:
    def __init__(
        self,
        primary_provider: MarketDataProvider,
        secondary_provider: MarketDataProvider | None,
        *,
        tolerances: CrossValidationTolerances | None = None,
        session_calculator: MarketSessionCalculator | None = None,
    ) -> None:
        self.primary_provider = primary_provider
        self.secondary_provider = secondary_provider
        self.tolerances = tolerances or CrossValidationTolerances()
        self.session_calculator = session_calculator or MarketSessionCalculator()

    async def _secondary_quote(self, symbol: str) -> MarketQuote | None:
        if self.secondary_provider is None:
            return None
        try:
            return await self.secondary_provider.get_quote(symbol)
        except Exception:
            return None

    async def _secondary_bars(self, symbol: str, timeframe: str, limit: int | None) -> list[IntradayBar] | None:
        if self.secondary_provider is None:
            return None
        try:
            return await self.secondary_provider.get_intraday_bars(symbol, timeframe, limit)
        except Exception:
            return None

    def _compare(
        self,
        *,
        symbol: str,
        field: str,
        primary_provider: str,
        secondary_provider: str | None,
        primary_value: Decimal | None,
        secondary_value: Decimal | None,
        tolerance_percent: Decimal,
    ) -> CrossValidationResult:
        if self.secondary_provider is None:
            return CrossValidationResult(
                symbol=symbol,
                field=field,
                primary_provider=primary_provider,
                secondary_provider=None,
                primary_value=primary_value,
                secondary_value=None,
                absolute_difference=None,
                percentage_difference=None,
                status=CrossValidationStatus.SECONDARY_UNAVAILABLE,
                reason="secondary provider unavailable",
            )
        if primary_value is None or secondary_value is None:
            return CrossValidationResult(
                symbol=symbol,
                field=field,
                primary_provider=primary_provider,
                secondary_provider=secondary_provider,
                primary_value=primary_value,
                secondary_value=secondary_value,
                absolute_difference=None,
                percentage_difference=None,
                status=CrossValidationStatus.NOT_COMPARABLE,
                reason="missing comparable values",
            )
        absolute_difference = abs(primary_value - secondary_value)
        percentage_difference = pct_difference(primary_value, secondary_value)
        if percentage_difference is None:
            status = CrossValidationStatus.NOT_COMPARABLE
            reason = "unable to compute percentage difference"
        elif absolute_difference == 0:
            status = CrossValidationStatus.MATCH
            reason = "values match exactly"
        elif percentage_difference <= tolerance_percent:
            status = CrossValidationStatus.WITHIN_TOLERANCE
            reason = f"difference {percentage_difference} within tolerance {tolerance_percent}"
        else:
            status = CrossValidationStatus.DIVERGENT
            reason = f"difference {percentage_difference} exceeds tolerance {tolerance_percent}"
        return CrossValidationResult(
            symbol=symbol,
            field=field,
            primary_provider=primary_provider,
            secondary_provider=secondary_provider,
            primary_value=primary_value,
            secondary_value=secondary_value,
            absolute_difference=absolute_difference,
            percentage_difference=percentage_difference,
            status=status,
            reason=reason,
        )

    async def compare_quotes(self, symbol: str, secondary_symbol: str | None = None) -> CrossValidationResult:
        primary_quote = await self.primary_provider.get_quote(symbol)
        secondary_quote = await self._secondary_quote(secondary_symbol or symbol)
        return self._compare(
            symbol=symbol,
            field="current_price",
            primary_provider=self.primary_provider.provider_name,
            secondary_provider=self.secondary_provider.provider_name if self.secondary_provider is not None else None,
            primary_value=decimal_or_none(primary_quote.price),
            secondary_value=decimal_or_none(secondary_quote.price) if secondary_quote is not None else None,
            tolerance_percent=self.tolerances.price_percent,
        )

    async def compare_latest_bar_close(self, symbol: str, timeframe: str = "1m", limit: int | None = 390, secondary_symbol: str | None = None) -> CrossValidationResult:
        primary_bars = await self.primary_provider.get_intraday_bars(symbol, timeframe, limit)
        secondary_bars = await self._secondary_bars(secondary_symbol or symbol, timeframe, limit)
        primary_close = decimal_or_none(primary_bars[-1].close) if primary_bars else None
        secondary_close = decimal_or_none(secondary_bars[-1].close) if secondary_bars else None
        return self._compare(
            symbol=symbol,
            field="latest_completed_1m_bar_close",
            primary_provider=self.primary_provider.provider_name,
            secondary_provider=self.secondary_provider.provider_name if self.secondary_provider is not None else None,
            primary_value=primary_close,
            secondary_value=secondary_close,
            tolerance_percent=self.tolerances.bar_close_percent,
        )

    async def compare_session_high(self, symbol: str, timeframe: str = "1m", limit: int | None = 390, secondary_symbol: str | None = None) -> CrossValidationResult:
        primary_bars = await self.primary_provider.get_intraday_bars(symbol, timeframe, limit)
        secondary_bars = await self._secondary_bars(secondary_symbol or symbol, timeframe, limit)
        primary_high_values: list[Decimal] = []
        for bar in primary_bars:
            value = decimal_or_none(bar.high)
            if value is not None:
                primary_high_values.append(value)
        secondary_high_values: list[Decimal] = []
        if secondary_bars:
            for bar in secondary_bars:
                value = decimal_or_none(bar.high)
                if value is not None:
                    secondary_high_values.append(value)
        primary_high = max(primary_high_values) if primary_high_values else None
        secondary_high = max(secondary_high_values) if secondary_high_values else None
        return self._compare(
            symbol=symbol,
            field="session_high",
            primary_provider=self.primary_provider.provider_name,
            secondary_provider=self.secondary_provider.provider_name if self.secondary_provider is not None else None,
            primary_value=primary_high,
            secondary_value=secondary_high,
            tolerance_percent=self.tolerances.session_high_low_percent,
        )

    async def compare_session_low(self, symbol: str, timeframe: str = "1m", limit: int | None = 390, secondary_symbol: str | None = None) -> CrossValidationResult:
        primary_bars = await self.primary_provider.get_intraday_bars(symbol, timeframe, limit)
        secondary_bars = await self._secondary_bars(secondary_symbol or symbol, timeframe, limit)
        primary_low_values: list[Decimal] = []
        for bar in primary_bars:
            value = decimal_or_none(bar.low)
            if value is not None:
                primary_low_values.append(value)
        secondary_low_values: list[Decimal] = []
        if secondary_bars:
            for bar in secondary_bars:
                value = decimal_or_none(bar.low)
                if value is not None:
                    secondary_low_values.append(value)
        primary_low = min(primary_low_values) if primary_low_values else None
        secondary_low = min(secondary_low_values) if secondary_low_values else None
        return self._compare(
            symbol=symbol,
            field="session_low",
            primary_provider=self.primary_provider.provider_name,
            secondary_provider=self.secondary_provider.provider_name if self.secondary_provider is not None else None,
            primary_value=primary_low,
            secondary_value=secondary_low,
            tolerance_percent=self.tolerances.session_high_low_percent,
        )

    async def compare_session_vwap(self, symbol: str, timeframe: str = "1m", limit: int | None = 390, secondary_symbol: str | None = None) -> CrossValidationResult:
        primary_bars = await self.primary_provider.get_intraday_bars(symbol, timeframe, limit)
        secondary_bars = await self._secondary_bars(secondary_symbol or symbol, timeframe, limit)
        session = self.session_calculator.most_recent_completed_session(datetime.now(UTC))
        session_open = self.session_calculator.calendar.session_open(session).to_pydatetime().astimezone(UTC)
        session_close = self.session_calculator.calendar.session_close(session).to_pydatetime().astimezone(UTC)
        primary_vwap = calculate_session_vwap(primary_bars, session_open=session_open, session_close=session_close).vwap
        secondary_vwap = (
            calculate_session_vwap(secondary_bars, session_open=session_open, session_close=session_close).vwap
            if secondary_bars is not None
            else None
        )
        return self._compare(
            symbol=symbol,
            field="session_vwap",
            primary_provider=self.primary_provider.provider_name,
            secondary_provider=self.secondary_provider.provider_name if self.secondary_provider is not None else None,
            primary_value=decimal_or_none(primary_vwap),
            secondary_value=decimal_or_none(secondary_vwap),
            tolerance_percent=self.tolerances.session_vwap_percent,
        )

    def validate_captured(
        self,
        symbol: str,
        *,
        primary_quote: MarketQuote | None,
        primary_bars: list[IntradayBar],
        secondary_quote: MarketQuote | None,
        secondary_bars: list[IntradayBar] | None,
    ) -> dict[str, CrossValidationResult]:
        """Compare captured inputs without refreshing either provider."""
        primary_highs = [decimal_or_none(bar.high) for bar in primary_bars]
        primary_lows = [decimal_or_none(bar.low) for bar in primary_bars]
        secondary_highs = [decimal_or_none(bar.high) for bar in (secondary_bars or [])]
        secondary_lows = [decimal_or_none(bar.low) for bar in (secondary_bars or [])]
        return {
            "current_price": self._compare(
                symbol=symbol, field="current_price",
                primary_provider=self.primary_provider.provider_name,
                secondary_provider=self.secondary_provider.provider_name if self.secondary_provider else None,
                primary_value=decimal_or_none(primary_quote.price) if primary_quote is not None else None,
                secondary_value=decimal_or_none(secondary_quote.price) if secondary_quote is not None else None,
                tolerance_percent=self.tolerances.price_percent,
            ),
            "latest_completed_1m_bar_close": self._compare(
                symbol=symbol, field="latest_completed_1m_bar_close",
                primary_provider=self.primary_provider.provider_name,
                secondary_provider=self.secondary_provider.provider_name if self.secondary_provider else None,
                primary_value=decimal_or_none(primary_bars[-1].close) if primary_bars else None,
                secondary_value=decimal_or_none(secondary_bars[-1].close) if secondary_bars else None,
                tolerance_percent=self.tolerances.bar_close_percent,
            ),
            "session_high": self._compare(
                symbol=symbol, field="session_high",
                primary_provider=self.primary_provider.provider_name,
                secondary_provider=self.secondary_provider.provider_name if self.secondary_provider else None,
                primary_value=max((value for value in primary_highs if value is not None), default=None),
                secondary_value=max((value for value in secondary_highs if value is not None), default=None),
                tolerance_percent=self.tolerances.session_high_low_percent,
            ),
            "session_low": self._compare(
                symbol=symbol, field="session_low",
                primary_provider=self.primary_provider.provider_name,
                secondary_provider=self.secondary_provider.provider_name if self.secondary_provider else None,
                primary_value=min((value for value in primary_lows if value is not None), default=None),
                secondary_value=min((value for value in secondary_lows if value is not None), default=None),
                tolerance_percent=self.tolerances.session_high_low_percent,
            ),
            "session_vwap": self._compare(
                symbol=symbol, field="session_vwap",
                primary_provider=self.primary_provider.provider_name,
                secondary_provider=self.secondary_provider.provider_name if self.secondary_provider else None,
                primary_value=decimal_or_none(calculate_session_vwap(primary_bars).vwap),
                secondary_value=decimal_or_none(calculate_session_vwap(secondary_bars).vwap) if secondary_bars is not None else None,
                tolerance_percent=self.tolerances.session_vwap_percent,
            ),
        }

    async def validate_symbol(self, symbol: str, secondary_symbol: str | None = None, timeframe: str = "1m", limit: int | None = 390) -> dict[str, CrossValidationResult]:
        secondary_symbol = secondary_symbol or symbol
        primary_quote_task = self.primary_provider.get_quote(symbol)
        primary_bars_task = self.primary_provider.get_intraday_bars(symbol, timeframe, limit)
        secondary_quote_task = self._secondary_quote(secondary_symbol)
        secondary_bars_task = self._secondary_bars(secondary_symbol, timeframe, limit)
        results = await asyncio.gather(
            primary_quote_task,
            primary_bars_task,
            secondary_quote_task,
            secondary_bars_task,
            return_exceptions=True,
        )
        primary_quote_result, primary_bars_result, secondary_quote_result, secondary_bars_result = results
        primary_quote = None if isinstance(primary_quote_result, Exception) else primary_quote_result
        primary_bars = [] if isinstance(primary_bars_result, Exception) else primary_bars_result
        secondary_quote = None if isinstance(secondary_quote_result, Exception) else secondary_quote_result
        secondary_bars = None if isinstance(secondary_bars_result, Exception) else secondary_bars_result

        primary_close = decimal_or_none(primary_bars[-1].close) if primary_bars else None
        secondary_close = decimal_or_none(secondary_bars[-1].close) if secondary_bars else None
        primary_high_values: list[Decimal] = []
        for bar in primary_bars:
            value = decimal_or_none(bar.high)
            if value is not None:
                primary_high_values.append(value)
        secondary_high_values: list[Decimal] = []
        if secondary_bars:
            for bar in secondary_bars:
                value = decimal_or_none(bar.high)
                if value is not None:
                    secondary_high_values.append(value)
        primary_low_values: list[Decimal] = []
        for bar in primary_bars:
            value = decimal_or_none(bar.low)
            if value is not None:
                primary_low_values.append(value)
        secondary_low_values: list[Decimal] = []
        if secondary_bars:
            for bar in secondary_bars:
                value = decimal_or_none(bar.low)
                if value is not None:
                    secondary_low_values.append(value)
        session = self.session_calculator.most_recent_completed_session(datetime.now(UTC))
        session_open = self.session_calculator.calendar.session_open(session).to_pydatetime().astimezone(UTC)
        session_close = self.session_calculator.calendar.session_close(session).to_pydatetime().astimezone(UTC)
        primary_vwap = calculate_session_vwap(primary_bars, session_open=session_open, session_close=session_close).vwap
        secondary_vwap = (
            calculate_session_vwap(secondary_bars, session_open=session_open, session_close=session_close).vwap
            if secondary_bars is not None
            else None
        )
        return {
            "current_price": self._compare(
                symbol=symbol,
                field="current_price",
                primary_provider=self.primary_provider.provider_name,
                secondary_provider=self.secondary_provider.provider_name if self.secondary_provider is not None else None,
                primary_value=decimal_or_none(primary_quote.price) if isinstance(primary_quote, MarketQuote) else None,
                secondary_value=decimal_or_none(secondary_quote.price) if isinstance(secondary_quote, MarketQuote) else None,
                tolerance_percent=self.tolerances.price_percent,
            ),
            "latest_completed_1m_bar_close": self._compare(
                symbol=symbol,
                field="latest_completed_1m_bar_close",
                primary_provider=self.primary_provider.provider_name,
                secondary_provider=self.secondary_provider.provider_name if self.secondary_provider is not None else None,
                primary_value=primary_close,
                secondary_value=secondary_close,
                tolerance_percent=self.tolerances.bar_close_percent,
            ),
            "session_high": self._compare(
                symbol=symbol,
                field="session_high",
                primary_provider=self.primary_provider.provider_name,
                secondary_provider=self.secondary_provider.provider_name if self.secondary_provider is not None else None,
                primary_value=max(primary_high_values) if primary_high_values else None,
                secondary_value=max(secondary_high_values) if secondary_high_values else None,
                tolerance_percent=self.tolerances.session_high_low_percent,
            ),
            "session_low": self._compare(
                symbol=symbol,
                field="session_low",
                primary_provider=self.primary_provider.provider_name,
                secondary_provider=self.secondary_provider.provider_name if self.secondary_provider is not None else None,
                primary_value=min(primary_low_values) if primary_low_values else None,
                secondary_value=min(secondary_low_values) if secondary_low_values else None,
                tolerance_percent=self.tolerances.session_high_low_percent,
            ),
            "session_vwap": self._compare(
                symbol=symbol,
                field="session_vwap",
                primary_provider=self.primary_provider.provider_name,
                secondary_provider=self.secondary_provider.provider_name if self.secondary_provider is not None else None,
                primary_value=decimal_or_none(primary_vwap),
                secondary_value=decimal_or_none(secondary_vwap),
                tolerance_percent=self.tolerances.session_vwap_percent,
            ),
        }

    async def validate_many(self, symbols: list[str], secondary_symbols: dict[str, str] | None = None, timeframe: str = "1m", limit: int | None = 390) -> dict[str, dict[str, CrossValidationResult]]:
        secondary_symbols = secondary_symbols or {}
        pairs = [self.validate_symbol(symbol, secondary_symbols.get(symbol), timeframe=timeframe, limit=limit) for symbol in symbols]
        results = await asyncio.gather(*pairs)
        return {symbol: result for symbol, result in zip(symbols, results)}
