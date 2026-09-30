"""NYSE session dates, including holidays, early closes and DST."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from functools import lru_cache

import pandas_market_calendars as mcal

NY = ZoneInfo("America/New_York")


def active_session(now: datetime | None = None) -> date | None:
    """Only the current regular session; never fall back to yesterday's close."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("A timezone-aware clock is required.")
    day = now.astimezone(NY).date()
    schedule = mcal.get_calendar("NYSE").schedule(day, day)
    if schedule.empty:
        return None
    row = schedule.iloc[0]
    return day if row.market_open <= now < row.market_close else None


def latest_session(now: datetime | None = None, *, scheduled: bool = False) -> date | None:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("A timezone-aware clock is required.")
    today = now.astimezone(NY).date()
    schedule = mcal.get_calendar("NYSE").schedule(today - timedelta(days=20), today)
    completed = schedule[schedule.market_close <= now]
    if completed.empty:
        return None
    day = completed.index[-1].date()
    return day if not scheduled or day == today else None


@lru_cache(maxsize=512)
def previous_session(day: date) -> date:
    schedule = mcal.get_calendar("NYSE").schedule(day - timedelta(days=20), day - timedelta(days=1))
    return schedule.index[-1].date()


def current_price_session(now: datetime | None = None) -> date:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(NY).date()
    schedule = mcal.get_calendar("NYSE").schedule(today, today)
    if not schedule.empty and now >= schedule.iloc[0].market_open:
        return today
    day = latest_session(now)
    if day is None:
        raise ValueError("No recent trading session found.")
    return day
