"""Provider-independent data contracts. Percentages use percentage points."""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

import pandas as pd

PERIODS = ("5D", "1M", "3M", "6M", "1Y")


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    exchange: str
    kind: str


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float | None = None
    previous_close: float | None = None
    year_high: float | None = None
    year_low: float | None = None
    session: date | None = None
    fetched_at: datetime | None = None
    source: str = "OpenBB / yfinance"
    error: str | None = None
    price_at: datetime | None = None

    @property
    def daily_move_pct(self) -> float | None:
        if self.price is None or self.previous_close is None or self.previous_close <= 0:
            return None
        return (self.price / self.previous_close - 1) * 100


class MarketDataProvider(Protocol):
    def search(self, query: str) -> list[Instrument]: ...

    def get_quote(self, symbol: str, session: date | None = None) -> Quote: ...

    def get_intraday_quote(self, symbol: str, session: date, now: datetime | None = None) -> Quote: ...

    def get_history_range(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...

    def get_history(self, symbol: str, period: str, as_of: date | None = None) -> pd.DataFrame: ...


class DataUnavailable(RuntimeError):
    """A ticker failed or supplied unusable/stale data."""
