from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MarketDataType(str, Enum):
    QUOTE = "QUOTE"
    PREVIOUS_CLOSE = "PREVIOUS_CLOSE"
    INTRADAY_BAR = "INTRADAY_BAR"


@dataclass(frozen=True, slots=True)
class DataTypeSourcePolicy:
    quote_preferred: str = "robinhood"
    quote_fallback: str = "alpaca"
    previous_close_preferred: str = "robinhood"
    previous_close_fallback: str = "alpaca"
    bar_preferred: str = "alpaca"
    bar_secondary_validation: str = "robinhood"
    enable_secondary_bar_validation: bool = False


PHASE4_SOURCE_POLICY = DataTypeSourcePolicy()
