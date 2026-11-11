from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo
from typing import cast

import exchange_calendars as xcals
import pandas as pd

from .models import MarketSession


NY_TZ = ZoneInfo("America/New_York")
XNYS = xcals.get_calendar("XNYS")


@dataclass(slots=True)
class MarketSessionBounds:
    session: MarketSession
    session_date: datetime
    market_open: datetime | None
    market_close: datetime | None
    is_early_close: bool


class MarketSessionCalculator:
    def __init__(self, calendar=XNYS, timezone: ZoneInfo = NY_TZ) -> None:
        self.calendar = calendar
        self.timezone = timezone

    @staticmethod
    def _to_ny_datetime(value: datetime | None = None) -> datetime:
        dt = value or datetime.now(UTC)
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return dt.astimezone(NY_TZ)

    @staticmethod
    def _session_label(value: datetime) -> pd.Timestamp:
        return cast(pd.Timestamp, pd.Timestamp(value.date()))

    def bounds(self, value: datetime | None = None) -> MarketSessionBounds:
        ny = self._to_ny_datetime(value)
        session_label = self._session_label(ny)
        market_open = market_close = None
        is_early_close = False

        if self.calendar.is_session(session_label):
            market_open = self.calendar.session_open(session_label).to_pydatetime().astimezone(NY_TZ)
            market_close = self.calendar.session_close(session_label).to_pydatetime().astimezone(NY_TZ)
            is_early_close = market_close.timetz().replace(tzinfo=None) < time(16, 0)
            session = MarketSession.REGULAR
        else:
            session = MarketSession.CLOSED

        return MarketSessionBounds(
            session=session,
            session_date=ny,
            market_open=market_open,
            market_close=market_close,
            is_early_close=is_early_close,
        )

    def current_session(self, value: datetime | None = None) -> MarketSession:
        ny = self._to_ny_datetime(value)
        session_label = self._session_label(ny)

        if not self.calendar.is_session(session_label):
            return MarketSession.CLOSED

        market_open = self.calendar.session_open(session_label).to_pydatetime().astimezone(NY_TZ)
        market_close = self.calendar.session_close(session_label).to_pydatetime().astimezone(NY_TZ)
        early_close = market_close.timetz().replace(tzinfo=None) < time(16, 0)

        if ny < market_open:
            return MarketSession.PREMARKET
        if market_open <= ny <= market_close:
            return MarketSession.REGULAR
        if early_close:
            return MarketSession.CLOSED
        if ny <= ny.replace(hour=20, minute=0, second=0, microsecond=0):
            return MarketSession.AFTER_HOURS
        return MarketSession.CLOSED

    def most_recent_completed_session(self, value: datetime | None = None) -> pd.Timestamp:
        ny = self._to_ny_datetime(value)
        session_label = self._session_label(ny)
        session = self.current_session(ny)

        if session == MarketSession.PREMARKET:
            return self.calendar.previous_session(session_label)
        if self.calendar.is_session(session_label):
            return self.calendar.date_to_session(session_label, direction="none")
        return self.calendar.date_to_session(session_label, direction="previous")

    def most_recent_completed_session_close(self, value: datetime | None = None) -> datetime:
        session_label = self.most_recent_completed_session(value)
        return self.calendar.session_close(session_label).to_pydatetime().astimezone(NY_TZ)

    def freshness_tolerance_seconds(self, session: MarketSession) -> int:
        if session == MarketSession.REGULAR:
            return 60
        if session in {MarketSession.PREMARKET, MarketSession.AFTER_HOURS}:
            return 300
        return 24 * 60 * 60

    def is_early_close_day(self, value: datetime | None = None) -> bool:
        ny = self._to_ny_datetime(value)
        session_label = self._session_label(ny)
        if not self.calendar.is_session(session_label):
            return False
        close = self.calendar.session_close(session_label).to_pydatetime().astimezone(NY_TZ)
        return close.timetz().replace(tzinfo=None) < time(16, 0)

    def session_close_time(self, value: datetime | None = None) -> datetime | None:
        ny = self._to_ny_datetime(value)
        session_label = self._session_label(ny)
        if not self.calendar.is_session(session_label):
            return None
        return self.calendar.session_close(session_label).to_pydatetime().astimezone(NY_TZ)

    def session_open_time(self, value: datetime | None = None) -> datetime | None:
        ny = self._to_ny_datetime(value)
        session_label = self._session_label(ny)
        if not self.calendar.is_session(session_label):
            return None
        return self.calendar.session_open(session_label).to_pydatetime().astimezone(NY_TZ)
