from __future__ import annotations

from datetime import date, datetime, timedelta


def _holiday_with_observed(day: date) -> set[date]:
    dates = {day}
    if day.weekday() == 5:
        dates.add(day - timedelta(days=1))
    elif day.weekday() == 6:
        dates.add(day + timedelta(days=1))
    return dates


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    current = date(year, month, 1)
    count = 0
    while True:
        if current.weekday() == weekday:
            count += 1
            if count == n:
                return current
        current += timedelta(days=1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    current = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    while current.weekday() != weekday:
        current -= timedelta(days=1)
    return current


def _good_friday(year: int) -> date:
    # Anonymous Gregorian algorithm for Easter Sunday.
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    easter = date(year, month, day)
    return easter - timedelta(days=2)


def nyse_holidays(year: int) -> set[date]:
    holidays = set().union(
        _holiday_with_observed(date(year, 1, 1)),
        {_nth_weekday(year, 1, 0, 3)},
        {_nth_weekday(year, 2, 0, 3)},
        {_good_friday(year)},
        {_last_weekday(year, 5, 0)},
        _holiday_with_observed(date(year, 6, 19)),
        _holiday_with_observed(date(year, 7, 4)),
        {_nth_weekday(year, 9, 0, 1)},
        {_nth_weekday(year, 11, 3, 4)},
        _holiday_with_observed(date(year, 12, 25)),
    )
    return holidays


def is_nyse_holiday(day: date | datetime) -> bool:
    if isinstance(day, datetime):
        day = day.date()
    return day in nyse_holidays(day.year)


def is_trading_day(dt: datetime) -> bool:
    return dt.weekday() < 5 and not is_nyse_holiday(dt.date())
