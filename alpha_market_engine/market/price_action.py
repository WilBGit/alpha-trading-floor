from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Iterable

from .brain_types import EventLifecycle, OpeningRangeLevels, PriceActionAnalysis, PriceActionEvent, PriceActionEventType
from .models import IntradayBar


@dataclass(slots=True)
class PriceActionRules:
    confirm_bars: int = 3
    reclaim_below_bars: int = 5
    reclaim_noise_tolerance: Decimal = Decimal("0.05")
    breakout_fail_bars: int = 4
    session_test_tolerance: Decimal = Decimal("0.10")
    range_compression_ratio: Decimal = Decimal("0.75")
    range_expansion_ratio: Decimal = Decimal("1.25")


class PriceActionAnalyzer:
    def __init__(self, *, confirm_bars: int = 3, reclaim_noise_tolerance: Decimal = Decimal("0.05"), rules: PriceActionRules | None = None) -> None:
        self.rules = rules or PriceActionRules(confirm_bars=confirm_bars, reclaim_noise_tolerance=reclaim_noise_tolerance)

    def _event_id(self, symbol: str, event_type: PriceActionEventType, session_date: datetime, suffix: str) -> str:
        return f"{symbol}:{session_date.date().isoformat()}:{event_type.value}:{suffix}"

    def _event_identity(
        self,
        previous_events: list[PriceActionEvent] | None,
        symbol: str,
        event_type: PriceActionEventType,
        session_date: datetime,
        suffix: str,
    ) -> str:
        matching = []
        for event in previous_events or []:
            if getattr(event, "symbol", None) != symbol:
                continue
            current_type = getattr(event, "event_type", None)
            current_value = current_type.value if hasattr(current_type, "value") else str(current_type)
            if current_value != event_type.value:
                continue
            matching.append(event)
        active = next(
            (
                event
                for event in reversed(matching)
                if (
                    (event.lifecycle.value if hasattr(event.lifecycle, "value") else str(event.lifecycle))
                    not in {EventLifecycle.FAILED.value, EventLifecycle.RESOLVED.value, EventLifecycle.EXPIRED.value}
                    and getattr(event, "event_id", None)
                )
            ),
            None,
        )
        if active is not None and active.event_id is not None:
            return active.event_id
        occurrence = len(matching) + 1
        return self._event_id(symbol, event_type, session_date, f"{suffix}:occ{occurrence:03d}")

    def _consecutive(self, values: list[bool], *, expected: bool) -> int:
        count = 0
        for value in reversed(values):
            if value is expected:
                count += 1
            else:
                break
        return count

    def _recent_range(self, bars: list[IntradayBar], count: int) -> Decimal:
        recent = bars[-count:]
        if not recent:
            return Decimal("0")
        return sum((Decimal(str(bar.high)) - Decimal(str(bar.low))) for bar in recent) / Decimal(len(recent))

    def _dedupe(self, event: PriceActionEvent, previous_events: list[PriceActionEvent] | None) -> bool:
        if not previous_events:
            return False
        previous_ids = {existing.event_id for existing in previous_events if existing.event_id}
        return event.event_id in previous_ids

    def analyze(
        self,
        *,
        symbol: str,
        bars: list[IntradayBar],
        current_price: Decimal,
        previous_close: Decimal | None,
        session_vwap: Decimal | None,
        ranges: OpeningRangeLevels,
        day_high: Decimal | None = None,
        day_low: Decimal | None = None,
        previous_events: list[PriceActionEvent] | None = None,
    ) -> list[PriceActionEvent]:
        ordered = sorted(bars, key=lambda item: item.timestamp)
        if not ordered:
            return []
        detected_at = ordered[-1].timestamp.astimezone(UTC)
        closes = [Decimal(str(bar.close)) for bar in ordered]
        highs = [Decimal(str(bar.high)) for bar in ordered]
        lows = [Decimal(str(bar.low)) for bar in ordered]
        event_bools_above_vwap = [session_vwap is not None and close > session_vwap for close in closes]
        event_bools_below_vwap = [session_vwap is not None and close < session_vwap for close in closes]
        events: list[PriceActionEvent] = []

        def maybe_add(event: PriceActionEvent) -> None:
            if not self._dedupe(event, previous_events):
                events.append(event)

        # Simple position checks
        if session_vwap is not None:
            above = current_price > session_vwap * (Decimal("1") + self.rules.reclaim_noise_tolerance / Decimal("100"))
            below = current_price < session_vwap * (Decimal("1") - self.rules.reclaim_noise_tolerance / Decimal("100"))
            if above:
                maybe_add(
                    PriceActionEvent(
                        event_type=PriceActionEventType.ABOVE_VWAP,
                        symbol=symbol,
                        detected_at=detected_at,
                        confidence=85,
                        importance=60,
                        evidence=[f"{symbol} is trading above session VWAP"],
                        supporting_values={"session_vwap": str(session_vwap), "latest_price": str(current_price)},
                        lifecycle=EventLifecycle.CONFIRMED,
                        event_id=self._event_identity(previous_events, symbol, PriceActionEventType.ABOVE_VWAP, detected_at, "above"),
                    )
                )
            elif below:
                maybe_add(
                    PriceActionEvent(
                        event_type=PriceActionEventType.BELOW_VWAP,
                        symbol=symbol,
                        detected_at=detected_at,
                        confidence=85,
                        importance=60,
                        evidence=[f"{symbol} is trading below session VWAP"],
                        supporting_values={"session_vwap": str(session_vwap), "latest_price": str(current_price)},
                        lifecycle=EventLifecycle.CONFIRMED,
                        event_id=self._event_identity(previous_events, symbol, PriceActionEventType.BELOW_VWAP, detected_at, "below"),
                    )
                )

            above_flags = [close > session_vwap * (Decimal("1") + self.rules.reclaim_noise_tolerance / Decimal("100")) for close in closes]
            below_flags = [close < session_vwap * (Decimal("1") - self.rules.reclaim_noise_tolerance / Decimal("100")) for close in closes]
            above_streak = self._consecutive(above_flags, expected=True)
            below_streak = self._consecutive(below_flags, expected=True)
            prior_segment = below_flags[:-above_streak] if above_streak > 0 else below_flags
            prior_below_streak = self._consecutive(prior_segment, expected=True)
            has_above_window = any(
                all(above_flags[index : index + self.rules.confirm_bars])
                for index in range(0, max(0, len(above_flags) - self.rules.confirm_bars + 1))
            )
            touch_recently = any(abs(close - session_vwap) / session_vwap * Decimal("100") <= self.rules.reclaim_noise_tolerance * Decimal("2") for close in closes[-3:])

            if above_streak >= self.rules.confirm_bars and prior_below_streak >= self.rules.reclaim_below_bars:
                event = PriceActionEvent(
                    event_type=PriceActionEventType.VWAP_RECLAIM,
                    symbol=symbol,
                    detected_at=detected_at,
                    confidence=94,
                    importance=78,
                    evidence=[
                        f"{symbol} traded below session VWAP before reclaiming it",
                        f"{above_streak} consecutive 1-minute closes finished above session VWAP",
                        f"Latest price remains {((current_price - session_vwap) / session_vwap * Decimal('100')):.2f}% above VWAP",
                    ],
                    supporting_values={"session_vwap": str(session_vwap), "latest_price": str(current_price), "above_streak": above_streak, "below_streak_before": prior_below_streak},
                    lifecycle=EventLifecycle.CONFIRMED,
                    event_id=self._event_identity(previous_events, symbol, PriceActionEventType.VWAP_RECLAIM, detected_at, "confirmed"),
                )
                maybe_add(event)
            elif current_price < session_vwap and (touch_recently or above_streak > 0 or has_above_window):
                if has_above_window:
                    event = PriceActionEvent(
                        event_type=PriceActionEventType.VWAP_LOSS,
                        symbol=symbol,
                        detected_at=detected_at,
                        confidence=88,
                        importance=72,
                        evidence=[
                            f"{symbol} was established above session VWAP",
                            "Price crossed back below VWAP",
                            f"Latest price is {((session_vwap - current_price) / session_vwap * Decimal('100')):.2f}% below VWAP",
                        ],
                        supporting_values={"session_vwap": str(session_vwap), "latest_price": str(current_price), "above_streak_before": self.rules.confirm_bars},
                        lifecycle=EventLifecycle.CONFIRMED,
                        event_id=self._event_identity(previous_events, symbol, PriceActionEventType.VWAP_LOSS, detected_at, "confirmed"),
                    )
                    maybe_add(event)
                else:
                    maybe_add(
                        PriceActionEvent(
                            event_type=PriceActionEventType.VWAP_REJECTION,
                            symbol=symbol,
                            detected_at=detected_at,
                            confidence=86,
                            importance=68,
                            evidence=[
                                f"{symbol} probed VWAP but failed to hold above it",
                                "Subsequent closes returned below VWAP",
                            ],
                            supporting_values={"session_vwap": str(session_vwap), "latest_price": str(current_price)},
                            lifecycle=EventLifecycle.FAILED,
                            event_id=self._event_identity(previous_events, symbol, PriceActionEventType.VWAP_REJECTION, detected_at, "failed"),
                        )
                    )

        if previous_close is not None:
            if current_price > previous_close:
                maybe_add(
                    PriceActionEvent(
                        event_type=PriceActionEventType.ABOVE_PREVIOUS_CLOSE,
                        symbol=symbol,
                        detected_at=detected_at,
                        confidence=80,
                        importance=55,
                        evidence=[f"{symbol} is above the previous close"],
                        supporting_values={"previous_close": str(previous_close), "latest_price": str(current_price)},
                        lifecycle=EventLifecycle.CONFIRMED,
                        event_id=self._event_identity(previous_events, symbol, PriceActionEventType.ABOVE_PREVIOUS_CLOSE, detected_at, "above_prev_close"),
                    )
                )
            elif current_price < previous_close:
                maybe_add(
                    PriceActionEvent(
                        event_type=PriceActionEventType.BELOW_PREVIOUS_CLOSE,
                        symbol=symbol,
                        detected_at=detected_at,
                        confidence=80,
                        importance=55,
                        evidence=[f"{symbol} is below the previous close"],
                        supporting_values={"previous_close": str(previous_close), "latest_price": str(current_price)},
                        lifecycle=EventLifecycle.CONFIRMED,
                        event_id=self._event_identity(previous_events, symbol, PriceActionEventType.BELOW_PREVIOUS_CLOSE, detected_at, "below_prev_close"),
                    )
                )

        if day_high is not None and current_price > day_high:
            maybe_add(
                PriceActionEvent(
                    event_type=PriceActionEventType.BREAK_PREVIOUS_DAY_HIGH,
                    symbol=symbol,
                    detected_at=detected_at,
                    confidence=92,
                    importance=75,
                    evidence=[f"{symbol} broke the previous day high"],
                    supporting_values={"previous_day_high": str(day_high), "latest_price": str(current_price)},
                    lifecycle=EventLifecycle.CONFIRMED,
                    event_id=self._event_identity(previous_events, symbol, PriceActionEventType.BREAK_PREVIOUS_DAY_HIGH, detected_at, "day_high"),
                )
            )
        if day_low is not None and current_price < day_low:
            maybe_add(
                PriceActionEvent(
                    event_type=PriceActionEventType.BREAK_PREVIOUS_DAY_LOW,
                    symbol=symbol,
                    detected_at=detected_at,
                    confidence=92,
                    importance=75,
                    evidence=[f"{symbol} broke the previous day low"],
                    supporting_values={"previous_day_low": str(day_low), "latest_price": str(current_price)},
                    lifecycle=EventLifecycle.CONFIRMED,
                    event_id=self._event_identity(previous_events, symbol, PriceActionEventType.BREAK_PREVIOUS_DAY_LOW, detected_at, "day_low"),
                )
            )

        def breakout_for(level_high: Decimal | None, level_low: Decimal | None, label: str) -> None:
            if level_high is None or level_low is None:
                return
            breakout = current_price > level_high and all(close > level_high for close in closes[-self.rules.confirm_bars:])
            breakdown = current_price < level_low and all(close < level_low for close in closes[-self.rules.confirm_bars:])
            failed_breakout = any(close > level_high for close in closes[-self.rules.breakout_fail_bars:]) and closes[-1] < level_high and any(close < level_high for close in closes[-self.rules.breakout_fail_bars:])
            failed_breakdown = any(close < level_low for close in closes[-self.rules.breakout_fail_bars:]) and closes[-1] > level_low and any(close > level_low for close in closes[-self.rules.breakout_fail_bars:])
            if breakout:
                maybe_add(
                    PriceActionEvent(
                        event_type=getattr(PriceActionEventType, f"{label}_BREAKOUT"),
                        symbol=symbol,
                        detected_at=detected_at,
                        confidence=91,
                        importance=77,
                        evidence=[f"{symbol} closed above {label} high {level_high}", f"{self.rules.confirm_bars} confirming closes above the range"],
                        supporting_values={"range_high": str(level_high), "range_low": str(level_low), "latest_price": str(current_price), "opening_range": dict(ranges.metadata.get(label, {}))},
                        lifecycle=EventLifecycle.CONFIRMED,
                        event_id=self._event_identity(previous_events, symbol, getattr(PriceActionEventType, f"{label}_BREAKOUT"), detected_at, label),
                    )
                )
            elif breakdown:
                maybe_add(
                    PriceActionEvent(
                        event_type=getattr(PriceActionEventType, f"{label}_BREAKDOWN"),
                        symbol=symbol,
                        detected_at=detected_at,
                        confidence=91,
                        importance=77,
                        evidence=[f"{symbol} closed below {label} low {level_low}", f"{self.rules.confirm_bars} confirming closes below the range"],
                        supporting_values={"range_high": str(level_high), "range_low": str(level_low), "latest_price": str(current_price), "opening_range": dict(ranges.metadata.get(label, {}))},
                        lifecycle=EventLifecycle.CONFIRMED,
                        event_id=self._event_identity(previous_events, symbol, getattr(PriceActionEventType, f"{label}_BREAKDOWN"), detected_at, label),
                    )
                )
            elif failed_breakout:
                maybe_add(
                    PriceActionEvent(
                        event_type=PriceActionEventType.FAILED_OR_BREAKOUT,
                        symbol=symbol,
                        detected_at=detected_at,
                        confidence=83,
                        importance=70,
                        evidence=[f"{symbol} failed to hold above {label} high {level_high}"],
                        supporting_values={"range_high": str(level_high), "latest_price": str(current_price)},
                        lifecycle=EventLifecycle.FAILED,
                        event_id=self._event_identity(previous_events, symbol, PriceActionEventType.FAILED_OR_BREAKOUT, detected_at, label),
                    )
                )
            elif failed_breakdown:
                maybe_add(
                    PriceActionEvent(
                        event_type=PriceActionEventType.FAILED_OR_BREAKDOWN,
                        symbol=symbol,
                        detected_at=detected_at,
                        confidence=83,
                        importance=70,
                        evidence=[f"{symbol} failed to hold below {label} low {level_low}"],
                        supporting_values={"range_low": str(level_low), "latest_price": str(current_price)},
                        lifecycle=EventLifecycle.FAILED,
                        event_id=self._event_identity(previous_events, symbol, PriceActionEventType.FAILED_OR_BREAKDOWN, detected_at, label),
                    )
                )

        breakout_for(ranges.or5_high, ranges.or5_low, "OR5")
        breakout_for(ranges.or15_high, ranges.or15_low, "OR15")
        breakout_for(ranges.or30_high, ranges.or30_low, "OR30")

        session_high = max(highs)
        session_low = min(lows)
        if abs(current_price - session_high) <= session_high * (self.rules.session_test_tolerance / Decimal("100")):
            maybe_add(
                PriceActionEvent(
                    event_type=PriceActionEventType.SESSION_HIGH_TEST,
                    symbol=symbol,
                    detected_at=detected_at,
                    confidence=75,
                    importance=60,
                    evidence=[f"{symbol} is testing the session high {session_high}"],
                    supporting_values={"session_high": str(session_high), "latest_price": str(current_price)},
                    lifecycle=EventLifecycle.DETECTED,
                    event_id=self._event_identity(previous_events, symbol, PriceActionEventType.SESSION_HIGH_TEST, detected_at, "session_high"),
                )
            )
        if abs(current_price - session_low) <= session_low * (self.rules.session_test_tolerance / Decimal("100")):
            maybe_add(
                PriceActionEvent(
                    event_type=PriceActionEventType.SESSION_LOW_TEST,
                    symbol=symbol,
                    detected_at=detected_at,
                    confidence=75,
                    importance=60,
                    evidence=[f"{symbol} is testing the session low {session_low}"],
                    supporting_values={"session_low": str(session_low), "latest_price": str(current_price)},
                    lifecycle=EventLifecycle.DETECTED,
                    event_id=self._event_identity(previous_events, symbol, PriceActionEventType.SESSION_LOW_TEST, detected_at, "session_low"),
                )
            )
        if current_price >= session_high:
            maybe_add(
                PriceActionEvent(
                    event_type=PriceActionEventType.NEW_SESSION_HIGH,
                    symbol=symbol,
                    detected_at=detected_at,
                    confidence=88,
                    importance=72,
                    evidence=[f"{symbol} has set a new session high"],
                    supporting_values={"session_high": str(session_high), "latest_price": str(current_price)},
                    lifecycle=EventLifecycle.CONFIRMED,
                    event_id=self._event_identity(previous_events, symbol, PriceActionEventType.NEW_SESSION_HIGH, detected_at, "new_high"),
                )
            )
        if current_price <= session_low:
            maybe_add(
                PriceActionEvent(
                    event_type=PriceActionEventType.NEW_SESSION_LOW,
                    symbol=symbol,
                    detected_at=detected_at,
                    confidence=88,
                    importance=72,
                    evidence=[f"{symbol} has set a new session low"],
                    supporting_values={"session_low": str(session_low), "latest_price": str(current_price)},
                    lifecycle=EventLifecycle.CONFIRMED,
                    event_id=self._event_identity(previous_events, symbol, PriceActionEventType.NEW_SESSION_LOW, detected_at, "new_low"),
                )
            )

        recent_range = self._recent_range(ordered, 5)
        early_range = self._recent_range(ordered[: max(5, len(ordered) // 2)], min(5, len(ordered[: max(5, len(ordered) // 2)])))
        if early_range and recent_range / early_range <= self.rules.range_compression_ratio:
            maybe_add(
                PriceActionEvent(
                    event_type=PriceActionEventType.RANGE_COMPRESSION,
                    symbol=symbol,
                    detected_at=detected_at,
                    confidence=76,
                    importance=58,
                    evidence=[f"Recent bar range compressed to {recent_range} from {early_range}"],
                    supporting_values={"recent_range": str(recent_range), "early_range": str(early_range)},
                    lifecycle=EventLifecycle.CONFIRMED,
                    event_id=self._event_identity(previous_events, symbol, PriceActionEventType.RANGE_COMPRESSION, detected_at, "compression"),
                )
            )
        if early_range and recent_range / early_range >= self.rules.range_expansion_ratio:
            maybe_add(
                PriceActionEvent(
                    event_type=PriceActionEventType.RANGE_EXPANSION,
                    symbol=symbol,
                    detected_at=detected_at,
                    confidence=76,
                    importance=58,
                    evidence=[f"Recent bar range expanded to {recent_range} from {early_range}"],
                    supporting_values={"recent_range": str(recent_range), "early_range": str(early_range)},
                    lifecycle=EventLifecycle.CONFIRMED,
                    event_id=self._event_identity(previous_events, symbol, PriceActionEventType.RANGE_EXPANSION, detected_at, "expansion"),
                )
            )

        up_moves = sum(1 for prev, cur in zip(closes, closes[1:]) if cur > prev)
        down_moves = sum(1 for prev, cur in zip(closes, closes[1:]) if cur < prev)
        if up_moves >= len(closes) - 2 and highs[-1] >= max(highs[:-1]):
            maybe_add(
                PriceActionEvent(
                    event_type=PriceActionEventType.HIGHER_HIGHS_HIGHER_LOWS,
                    symbol=symbol,
                    detected_at=detected_at,
                    confidence=72,
                    importance=55,
                    evidence=["Recent bars show a sequence of higher highs and higher lows"],
                    supporting_values={"up_moves": up_moves, "down_moves": down_moves},
                    lifecycle=EventLifecycle.CONFIRMED,
                    event_id=self._event_identity(previous_events, symbol, PriceActionEventType.HIGHER_HIGHS_HIGHER_LOWS, detected_at, "hhhl"),
                )
            )
        if down_moves >= len(closes) - 2 and lows[-1] <= min(lows[:-1]):
            maybe_add(
                PriceActionEvent(
                    event_type=PriceActionEventType.LOWER_HIGHS_LOWER_LOWS,
                    symbol=symbol,
                    detected_at=detected_at,
                    confidence=72,
                    importance=55,
                    evidence=["Recent bars show a sequence of lower highs and lower lows"],
                    supporting_values={"up_moves": up_moves, "down_moves": down_moves},
                    lifecycle=EventLifecycle.CONFIRMED,
                    event_id=self._event_identity(previous_events, symbol, PriceActionEventType.LOWER_HIGHS_LOWER_LOWS, detected_at, "lhll"),
                )
            )
        if 0 < up_moves < len(closes) - 2 and 0 < down_moves < len(closes) - 2:
            maybe_add(
                PriceActionEvent(
                    event_type=PriceActionEventType.MIXED_STRUCTURE,
                    symbol=symbol,
                    detected_at=detected_at,
                    confidence=68,
                    importance=50,
                    evidence=["Directional closes are mixed"],
                    supporting_values={"up_moves": up_moves, "down_moves": down_moves},
                    lifecycle=EventLifecycle.DETECTED,
                    event_id=self._event_identity(previous_events, symbol, PriceActionEventType.MIXED_STRUCTURE, detected_at, "mixed"),
                )
            )

        return events
