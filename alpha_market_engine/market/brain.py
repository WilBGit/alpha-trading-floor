from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .brain_types import AnalysisConfidence, MarketAnalysisInput, MarketAnalysisSymbolInput, MarketBrainResult, OpeningRangeLevels, PriceActionAnalysis
from .confidence import MarketConfidenceCalculator
from .cross_validation import MarketDataCrossValidator
from .data_provider import FallbackQuoteProvider, MarketDataProvider
from .divergence import IndexDivergenceAnalyzer
from .momentum import MomentumAnalyzer
from .observations import MarketObservationEngine
from .price_action import PriceActionAnalyzer
from .regime import MarketRegimeAnalyzer
from .snapshot import MarketSnapshotEngine
from .source_policy import PHASE4_SOURCE_POLICY
from .streaming.state import assess_session_bar_coverage, calculate_session_vwap
from .structure import MarketStructureAnalyzer


@dataclass(slots=True)
class MarketBrainConfig:
    symbols: tuple[str, ...] = ("SPY", "QQQ", "DIA", "IWM")
    bars_limit: int = 390
    timeframe: str = "1m"
    opening_range_minutes: tuple[int, ...] = (5, 15, 30)
    enable_secondary_bar_validation: bool = PHASE4_SOURCE_POLICY.enable_secondary_bar_validation


class MarketBrainService:
    def __init__(
        self,
        primary_provider: MarketDataProvider,
        secondary_provider: MarketDataProvider | None = None,
        *,
        config: MarketBrainConfig | None = None,
        secondary: MarketDataProvider | None = None,
        symbols: list[str] | tuple[str, ...] | None = None,
        repository: Any | None = None,
        bar_provider: MarketDataProvider | None = None,
    ) -> None:
        self.secondary_provider = secondary_provider or secondary
        self.primary_provider = FallbackQuoteProvider(primary_provider, self.secondary_provider) if self.secondary_provider is not None and primary_provider.provider_name == "robinhood" else primary_provider
        self.bar_provider = bar_provider or (self.secondary_provider if self.secondary_provider is not None and self.secondary_provider.provider_name == PHASE4_SOURCE_POLICY.bar_preferred else self.primary_provider)
        self.repository = repository
        self.config = config or MarketBrainConfig(symbols=tuple(symbols) if symbols is not None else MarketBrainConfig().symbols)
        self.snapshot_engine = MarketSnapshotEngine(self.primary_provider, bar_provider=self.bar_provider)
        self.cross_validator = MarketDataCrossValidator(self.primary_provider, self.secondary_provider)
        self.confidence_calculator = MarketConfidenceCalculator()
        self.price_action_analyzer = PriceActionAnalyzer()
        self.structure_analyzer = MarketStructureAnalyzer()
        self.momentum_analyzer = MomentumAnalyzer()
        self.divergence_analyzer = IndexDivergenceAnalyzer()
        self.regime_analyzer = MarketRegimeAnalyzer()
        self.observation_engine = MarketObservationEngine()
        self._latest_price_action_events: dict[str, list[Any]] = {}

    def _opening_ranges_from_bars(self, bars: list[Any]) -> OpeningRangeLevels:
        ordered = sorted(bars, key=lambda item: item.timestamp)
        if not ordered:
            return OpeningRangeLevels()

        metadata: dict[str, Any] = {}
        def levels(minutes: int, label: str) -> tuple[Decimal | None, Decimal | None]:
            sample = ordered[: max(1, minutes)]
            highs = [Decimal(str(bar.high)) for bar in sample]
            lows = [Decimal(str(bar.low)) for bar in sample]
            if sample:
                start = sample[0].timestamp.astimezone(ZoneInfo("America/New_York"))
                end = (sample[-1].timestamp + timedelta(minutes=1)).astimezone(ZoneInfo("America/New_York"))
                metadata[label] = {
                    "session_date": start.date().isoformat(),
                    "interval_start_et": start.isoformat(),
                    "interval_end_et": end.isoformat(),
                    "end_boundary": "EXCLUSIVE",
                    "expected_bar_count": minutes,
                    "actual_bar_count": len(sample),
                    "coverage_status": "COMPLETE" if len(sample) == minutes else "INCOMPLETE",
                    "bar_source": "PRIMARY_CAPTURED_BARS",
                }
            return (max(highs) if highs else None, min(lows) if lows else None)

        or5_high, or5_low = levels(5, "OR5")
        or15_high, or15_low = levels(15, "OR15")
        or30_high, or30_low = levels(30, "OR30")
        return OpeningRangeLevels(or5_high=or5_high, or5_low=or5_low, or15_high=or15_high, or15_low=or15_low, or30_high=or30_high, or30_low=or30_low, metadata=metadata)

    async def _load_symbol_inputs(self, symbol: str) -> dict[str, Any]:
        quote_task = self.primary_provider.get_quote(symbol)
        bars_task = self.primary_provider.get_intraday_bars(symbol, self.config.timeframe, self.config.bars_limit)
        secondary_quote_task = None
        secondary_bars_task = None
        if self.secondary_provider is not None:
            secondary_quote_task = self.secondary_provider.get_quote(symbol)
            secondary_bars_task = self.secondary_provider.get_intraday_bars(symbol, self.config.timeframe, self.config.bars_limit)

        primary_quote_result, primary_bars_result = await asyncio.gather(quote_task, bars_task, return_exceptions=True)
        secondary_quote = None
        secondary_bars = None
        if secondary_quote_task is not None and secondary_bars_task is not None:
            secondary_quote_result, secondary_bars_result = await asyncio.gather(secondary_quote_task, secondary_bars_task, return_exceptions=True)
            secondary_quote = None if isinstance(secondary_quote_result, Exception) else secondary_quote_result
            secondary_bars = None if isinstance(secondary_bars_result, Exception) else secondary_bars_result

        quote = None if isinstance(primary_quote_result, Exception) else primary_quote_result
        bars = [] if isinstance(primary_bars_result, Exception) else primary_bars_result
        if quote is None and secondary_quote is not None:
            quote = secondary_quote
        if not bars and secondary_bars:
            bars = secondary_bars
        if quote is None or bars is None:
            raise RuntimeError(f"unable to load market inputs for {symbol}")
        return {"quote": quote, "bars": bars, "secondary_quote": secondary_quote, "secondary_bars": secondary_bars}

    async def _load_secondary_inputs(self, symbol: str) -> dict[str, Any]:
        if self.secondary_provider is None:
            return {"quote": None, "bars": None}
        quote_task = self.secondary_provider.get_quote(symbol)
        validate_secondary_bars = self.config.enable_secondary_bar_validation or self.primary_provider.provider_name != "robinhood"
        bars_task = self.secondary_provider.get_intraday_bars(symbol, self.config.timeframe, self.config.bars_limit) if validate_secondary_bars else None
        if bars_task is None:
            try:
                quote_result = await quote_task
            except Exception:
                quote_result = None
            return {"quote": quote_result, "bars": None}
        quote_result, bars_result = await asyncio.gather(quote_task, bars_task, return_exceptions=True)
        return {
            "quote": None if isinstance(quote_result, Exception) else quote_result,
            "bars": None if isinstance(bars_result, Exception) else bars_result,
        }

    async def analyze_market(self) -> MarketBrainResult:
        analysis_started_at = datetime.now(UTC)
        snapshot = await self.snapshot_engine.service.create_snapshot()
        primary_quotes = self.snapshot_engine.service.last_source_quotes
        primary_bars = self.snapshot_engine.service.last_source_raw_bars
        secondary_inputs_list = await asyncio.gather(*(self._load_secondary_inputs(symbol) for symbol in self.config.symbols))
        secondary_inputs = {symbol: data for symbol, data in zip(self.config.symbols, secondary_inputs_list)}
        source_captured_at = datetime.now(UTC)
        symbol_inputs = {
            symbol: {
                "quote": primary_quotes.get(symbol),
                "bars": primary_bars.get(symbol, []),
                "secondary_quote": secondary_inputs[symbol]["quote"],
                "secondary_bars": secondary_inputs[symbol]["bars"],
            }
            for symbol in self.config.symbols
        }

        cross_validation = {
            symbol: self.cross_validator.validate_captured(
                symbol,
                primary_quote=data["quote"],
                primary_bars=data["bars"],
                secondary_quote=data["secondary_quote"],
                secondary_bars=data["secondary_bars"],
            )
            for symbol, data in symbol_inputs.items()
        }
        for symbol_results in cross_validation.values():
            for validation in symbol_results.values():
                if validation.field != "current_price":
                    validation.primary_provider = self.bar_provider.provider_name
                    validation.secondary_provider = PHASE4_SOURCE_POLICY.bar_secondary_validation if self.config.enable_secondary_bar_validation else None


        core_field_status = {symbol: data["quote"] is not None and len(data["bars"]) > 0 for symbol, data in symbol_inputs.items()}
        cross_validation_statuses = [result.status for symbol_results in cross_validation.values() for result in symbol_results.values()]
        session_bar_coverage: dict[str, Decimal] = {}
        vwap_quality: dict[str, str] = {}
        session_calc = self.snapshot_engine.service.session_calculator
        snapshot_ny = snapshot.snapshot_time.astimezone(session_calc.timezone)
        session_label = session_calc.calendar.date_to_session(snapshot_ny.date(), direction="none") if snapshot.market_session.value == "REGULAR" else session_calc.most_recent_completed_session(snapshot.snapshot_time)
        session_open = session_calc.calendar.session_open(session_label).to_pydatetime().astimezone(session_calc.timezone)
        session_close = session_calc.calendar.session_close(session_label).to_pydatetime().astimezone(session_calc.timezone)
        coverage_close = session_close if snapshot.market_session.value != "REGULAR" else min(session_close, snapshot_ny.replace(second=0, microsecond=0))
        for symbol, data in symbol_inputs.items():
            coverage = assess_session_bar_coverage(data["bars"], session_open=session_open, session_close=coverage_close)
            vwap_result = calculate_session_vwap(data["bars"], session_open=session_open, session_close=coverage_close)
            session_bar_coverage[symbol] = coverage.coverage_percentage
            vwap_quality[symbol] = vwap_result.quality.value
        analysis_confidence = self.confidence_calculator.calculate(
            core_field_status=core_field_status,
            cross_validation_statuses=cross_validation_statuses,
            session_bar_coverage=session_bar_coverage,
            vwap_quality=vwap_quality,
            data_sources=[self.primary_provider.provider_name] + ([self.secondary_provider.provider_name] if self.secondary_provider is not None else []),
        )

        price_action: dict[str, Any] = {}
        structures: dict[str, Any] = {}
        momentum: dict[str, Any] = {}
        index_data: dict[str, dict[str, Any]] = {}
        latest_events: list[Any] = []

        for symbol, data in symbol_inputs.items():
            quote = data["quote"]
            bars = data["bars"]
            if quote is None:
                structures[symbol] = self.structure_analyzer.analyze(symbol, bars)
                momentum[symbol] = self.momentum_analyzer.analyze(symbol, bars, session_vwap=None)
                index_data[symbol] = {"change_percent": None, "above_vwap": None, "below_vwap": None, "structure": structures[symbol].structure, "momentum": momentum[symbol].regime, "current_price": None}
                continue
            opening_ranges = self._opening_ranges_from_bars(bars)
            session_vwap = None
            if bars:
                session_vwap = Decimal(str(sum(Decimal(str(bar.vwap or bar.close)) * Decimal(bar.volume) for bar in bars if bar.volume > 0) / sum(Decimal(bar.volume) for bar in bars if bar.volume > 0))) if any(bar.volume > 0 for bar in bars) else None
            events = self.price_action_analyzer.analyze(
                symbol=symbol,
                bars=bars,
                current_price=Decimal(str(quote.price)),
                previous_close=Decimal(str(quote.previous_close)) if quote.previous_close is not None else None,
                session_vwap=session_vwap,
                ranges=opening_ranges,
                day_high=Decimal(str(quote.day_high)) if quote.day_high is not None else None,
                day_low=Decimal(str(quote.day_low)) if quote.day_low is not None else None,
                previous_events=self._latest_price_action_events.get(symbol),
            )
            self._latest_price_action_events[symbol] = events
            latest_events.extend(events)
            price_action[symbol] = PriceActionAnalysis(
                symbol=symbol,
                detected_at=events[-1].detected_at if events else snapshot.snapshot_time,
                current_price=Decimal(str(quote.price)),
                previous_close=Decimal(str(quote.previous_close)) if quote.previous_close is not None else None,
                session_vwap=session_vwap,
                opening_ranges=opening_ranges,
                events=events,
            )
            structures[symbol] = self.structure_analyzer.analyze(symbol, bars)
            momentum[symbol] = self.momentum_analyzer.analyze(symbol, bars, session_vwap=session_vwap)
            index_data[symbol] = {
                "change_percent": quote.change_percent,
                "above_vwap": bool(session_vwap is not None and Decimal(str(quote.price)) > session_vwap),
                "below_vwap": bool(session_vwap is not None and Decimal(str(quote.price)) < session_vwap),
                "structure": structures[symbol].structure,
                "momentum": momentum[symbol].regime,
                "current_price": Decimal(str(quote.price)),
            }

        analysis_session_date = snapshot.snapshot_time.astimezone(UTC).date().isoformat()
        normalized_symbols: list[MarketAnalysisSymbolInput] = []
        for symbol in self.config.symbols:
            data = symbol_inputs[symbol]
            quote = data["quote"]
            metadata = getattr(quote, "validation_metadata", {}) or {}
            analysis = price_action.get(symbol)
            normalized_symbols.append(
                MarketAnalysisSymbolInput(
                    symbol=symbol,
                    current_price=Decimal(str(quote.price)) if quote is not None else None,
                    current_price_source=str(metadata.get("current_price_source", metadata.get("price_source", "UNKNOWN"))) if quote is not None else "UNKNOWN",
                    current_price_timestamp=getattr(quote, "quote_timestamp", None),
                    previous_close=Decimal(str(quote.previous_close)) if quote is not None and quote.previous_close is not None else None,
                    previous_close_source=str(metadata.get("previous_close_source", "UNKNOWN")),
                    change_percent=quote.change_percent if quote is not None else None,
                    change_percent_source=str(metadata.get("change_percent_source", "CALCULATED_FROM_PRICE_AND_PREVIOUS_CLOSE" if quote is not None and quote.change_percent is not None else "UNKNOWN")),
                    session_vwap=analysis.session_vwap if analysis is not None else None,
                    vwap_method="PROVIDER_BAR_WEIGHTED" if data["bars"] and all(getattr(bar, "vwap", None) is not None for bar in data["bars"]) else "TYPICAL_PRICE_APPROXIMATION" if data["bars"] else "UNAVAILABLE",
                    vwap_quality=vwap_quality[symbol],
                    above_vwap=index_data.get(symbol, {}).get("above_vwap"),
                    opening_ranges=analysis.opening_ranges if analysis is not None else OpeningRangeLevels(),
                    bar_status="AVAILABLE" if data["bars"] else "UNAVAILABLE",
                    bar_source=str(metadata.get("bar_source", f"{self.bar_provider.provider_name.upper()}_BAR" if data["bars"] else "UNKNOWN")),
                    analysis_session_date=analysis_session_date,
                    requested_primary_provider=str(metadata.get("requested_primary_provider", self.primary_provider.provider_name)),
                    active_quote_provider=str(metadata.get("active_quote_provider", self.primary_provider.provider_name)) if quote is not None else "UNAVAILABLE",
                    fallback_used=bool(metadata.get("fallback_used", False)),
                    primary_failure_reason=metadata.get("primary_failure_reason") if quote is not None else getattr(self.primary_provider, "last_quote_failures", {}).get(symbol),
                    requested_bar_provider=self.bar_provider.provider_name,
                    active_bar_provider=self.bar_provider.provider_name if data["bars"] else "UNAVAILABLE",
                    bar_fallback_used=False,
                    bar_primary_failure_reason=None if data["bars"] else "no usable bars from primary bar provider",
                    previous_close_provider=str(metadata.get("active_quote_provider", self.primary_provider.provider_name)) if quote is not None and quote.previous_close is not None else "UNAVAILABLE",
                )
            )
        for item in normalized_symbols:
            if item.primary_failure_reason:
                analysis_confidence.warnings.append(
                    f"{item.symbol}: requested primary provider {item.requested_primary_provider} failed; active quote provider is {item.active_quote_provider} ({item.primary_failure_reason})"
                )
        analysis_input = MarketAnalysisInput(
            analysis_id=str(uuid.uuid4()),
            analysis_started_at=analysis_started_at,
            source_captured_at=source_captured_at,
            analysis_session_date=analysis_session_date,
            primary_provider=self.primary_provider.provider_name,
            secondary_provider=self.secondary_provider.provider_name if self.secondary_provider else None,
            symbols=tuple(normalized_symbols),
            provider_metadata={"primary_feed": self.primary_provider.data_feed, "secondary_feed": self.secondary_provider.data_feed if self.secondary_provider else None, "data_type_source_policy": {"quote_preferred": PHASE4_SOURCE_POLICY.quote_preferred, "quote_fallback": PHASE4_SOURCE_POLICY.quote_fallback, "previous_close_preferred": PHASE4_SOURCE_POLICY.previous_close_preferred, "previous_close_fallback": PHASE4_SOURCE_POLICY.previous_close_fallback, "bar_preferred": PHASE4_SOURCE_POLICY.bar_preferred, "bar_secondary_validation": PHASE4_SOURCE_POLICY.bar_secondary_validation, "enable_secondary_bar_validation": self.config.enable_secondary_bar_validation}},
            cross_validation_metadata={"result_count": sum(len(items) for items in cross_validation.values())},
            data_capabilities={"primary_quotes": True, "primary_bars": any(bool(data["bars"]) for data in symbol_inputs.values()), "secondary_validation": self.secondary_provider is not None},
        )

        divergences = self.divergence_analyzer.analyze(index_data)
        regime = self.regime_analyzer.analyze(
            index_data=index_data,
            structures=structures,
            momentum=momentum,
            divergences=divergences,
            cross_validation_confidence=analysis_confidence.score,
        )

        observations = self.observation_engine.build(
            [
                *({"signal": signal, "symbols": ["QQQ", "SPY"], "confidence": divergences.confidence, "importance": 82, "facts": divergences.evidence, "detected_at": snapshot.snapshot_time, "supporting_data": divergences.supporting_data} for signal in divergences.signals),
                *latest_events,
            ]
        )

        result = MarketBrainResult(
            analysis_id=analysis_input.analysis_id,
            analysis_started_at=analysis_input.analysis_started_at,
            source_captured_at=analysis_input.source_captured_at,
            analysis_session_date=analysis_input.analysis_session_date,
            analysis_input=analysis_input,
            snapshot=snapshot,
            cross_validation={f"{symbol}.{field}": result for symbol, results in cross_validation.items() for field, result in results.items()},
            analysis_confidence=analysis_confidence,
            price_action=price_action,
            structures=structures,
            momentum=momentum,
            divergences=divergences,
            regime=regime,
            observations=observations,
        )
        if self.repository is not None:
            for analysis in result.price_action.values():
                for event in analysis.events:
                    if event.event_id is None:
                        continue
                    await self.repository.save_price_action_event(
                        event_id=event.event_id,
                        symbol=event.symbol,
                        event_type=event.event_type.value,
                        lifecycle=event.lifecycle.value,
                        detected_at=event.detected_at,
                        confidence=event.confidence,
                        importance=event.importance,
                        evidence=event.evidence,
                        supporting_values=event.supporting_values,
                        session_date=event.session_date or event.detected_at,
                    )
            for observation in result.observations:
                if observation.observation_key is None:
                    continue
                await self.repository.save_market_observation(
                    observation_key=observation.observation_key,
                    observation_type=observation.observation_type.value,
                    symbols=observation.symbols,
                    detected_at=observation.detected_at,
                    importance_score=observation.importance_score,
                    confidence_score=observation.confidence_score,
                    facts=observation.facts,
                    interpretation_boundary=observation.interpretation_boundary,
                    source_events=observation.source_events,
                    session_date=observation.session_date or observation.detected_at,
                )
        return result
