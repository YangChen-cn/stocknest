"""One NYSE/calendar definition for price and portfolio return windows."""
from datetime import date, timedelta
import math
from functools import lru_cache

import pandas as pd
import pandas_market_calendars as mcal

from stockwatch.providers.base import PERIODS


@lru_cache(maxsize=256)
def sessions(start: date, end: date) -> tuple[date, ...]:
    if end < start:
        return ()
    return tuple(mcal.get_calendar("NYSE").valid_days(start, end).date)


@lru_cache(maxsize=256)
def period_start(day: date, period: str) -> date:
    if period not in PERIODS:
        raise ValueError(f"Unsupported period: {period}")
    if period == "5D":
        return sessions(day - timedelta(days=30), day)[-6]
    months = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12}[period]
    boundary = (pd.Timestamp(day) - pd.DateOffset(months=months)).date()
    return sessions(boundary - timedelta(days=20), boundary)[-1]


def price_return(frame: pd.DataFrame | None, day: date, period: str) -> float | None:
    if frame is None or frame.empty:
        return None
    start = period_start(day, period)
    prices = dict(zip(pd.to_datetime(frame.date).dt.date, frame.close))
    first, last = prices.get(start), prices.get(day)
    if first is None or last is None or not pd.notna(first) or not pd.notna(last) or first <= 0 or last <= 0 or not math.isfinite(float(first)) or not math.isfinite(float(last)):
        return None
    return (float(last) / float(first) - 1) * 100
