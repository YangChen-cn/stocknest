"""Clearly labelled, deterministic offline fixtures for demos and UI tests."""
import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from stockwatch.providers.base import DataUnavailable, Instrument, PERIODS, Quote
from stockwatch.storage import ROOT
from stockwatch.calendar import NY, previous_session


class DemoProvider:
    def __init__(self, path: Path = ROOT / "examples/quotes.json"):
        self.fixture = json.loads(path.read_text())
        self.session = date.fromisoformat(self.fixture["session"])

    def search(self, query: str) -> list[Instrument]:
        query = query.strip().casefold()
        if not query:
            return []
        results = []
        for symbol in self.fixture["quotes"]:
            entry = self.fixture.get("instruments", {}).get(symbol, {})
            name = entry.get("name", symbol)
            if query in symbol.casefold() or query in name.casefold():
                results.append(Instrument(symbol, name, "Demo", entry.get("kind", "EQUITY")))
        return results

    def get_quote(self, symbol: str, session: date | None = None) -> Quote:
        data = self.fixture["quotes"].get(symbol)
        if data is None or (session is not None and session != self.session):
            return Quote(symbol, error="Data unavailable: no demo fixture")
        return Quote(symbol, **data, session=self.session, source="Simulated demo data")

    def get_intraday_quote(self, symbol: str, session: date, now: datetime | None = None) -> Quote:
        stamp = datetime.combine(self.session, datetime.min.time(), NY).replace(hour=10, minute=30)
        return replace(self.get_quote(symbol, session), price_at=stamp, fetched_at=stamp)

    def get_history(self, symbol: str, period: str, as_of: date | None = None) -> pd.DataFrame:
        if period not in PERIODS:
            raise ValueError("Unsupported period")
        from stockwatch.history import period_start
        day = as_of or self.session
        return self.get_history_range(symbol, period_start(day, period), day)

    def get_history_range(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        from stockwatch.history import sessions
        quote = self.get_quote(symbol)
        if quote.price is None:
            raise DataUnavailable("No demo fixture")
        prior = previous_session(self.session)
        prices = [quote.price if day == self.session else quote.previous_close * (1 + (day - prior).days / 5000) for day in sessions(start, end)]
        return pd.DataFrame({"date": sessions(start, end), "close": prices, "split_ratio": 0.0})
