"""UI-only completed-session disk cache; daily jobs always fetch independently."""
import json
import logging
from datetime import timedelta
from pathlib import Path

from stockwatch.history import period_start
from stockwatch.providers.base import DataUnavailable, PERIODS
from stockwatch.providers.openbb_provider import normalize_history, quote_from_history
from stockwatch.storage import atomic_write, ticker

logger = logging.getLogger(__name__)


class ClosedSessionProvider:
    def __init__(self, provider, directory: Path, session):
        self.provider = provider
        self.directory = directory
        self.session = session
        self.frames = {}

    def _frame(self, symbol):
        symbol = ticker(symbol)
        if symbol in self.frames:
            return self.frames[symbol]
        path = self.directory / f"{symbol}.json"
        frame = None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload["version"] == 1 and payload["session"] == self.session.isoformat():
                candidate = normalize_history(payload["bars"])
                if not quote_from_history(symbol, candidate, self.session).error:
                    frame = candidate
        except (OSError, ValueError, KeyError, TypeError, DataUnavailable):
            pass
        if frame is None:
            frame = self.provider.get_history_range(symbol, self.session - timedelta(days=380), self.session)
            frame = normalize_history(frame.to_dict("records"))
            quote = quote_from_history(symbol, frame, self.session)
            if quote.error:
                raise DataUnavailable(quote.error)
            # pandas serializes missing optional values as null, never NaN.
            bars = json.loads(frame.assign(date=frame.date.map(str)).to_json(orient="records"))
            content = json.dumps({"version": 1, "session": self.session.isoformat(), "bars": bars}, allow_nan=False)
            try:
                atomic_write(path, content + "\n")
            except OSError:
                logger.warning("Could not save local market cache")
        self.frames[symbol] = frame
        return frame

    def get_quote(self, symbol, session=None):
        if session is not None and session != self.session:
            return self.provider.get_quote(symbol, session)
        return quote_from_history(symbol, self._frame(symbol), self.session)

    def get_history(self, symbol, period, as_of=None):
        day = as_of or self.session
        if period not in PERIODS:
            raise ValueError("Unsupported history period")
        if day != self.session:
            return self.provider.get_history(symbol, period, day)
        start = period_start(day, period)
        frame = self._frame(symbol)
        result = frame[(frame.date >= start) & (frame.date <= day)].copy()
        if start not in set(result.date) or day not in set(result.date):
            raise DataUnavailable("Historical endpoints missing or stale")
        return result


def clear_market_cache(directory: Path):
    """Explicit refresh discards only this UI cache, never portfolio/state files."""
    if directory.exists():
        for path in directory.glob("*.json"):
            path.unlink(missing_ok=True)
