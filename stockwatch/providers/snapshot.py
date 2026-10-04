"""Read-only market snapshot provider. Never contacts Yahoo or writes user files."""
import json
import math
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from stockwatch.history import period_start
from stockwatch.providers.base import DataUnavailable, PERIODS, Quote
from stockwatch.storage import ticker

BAR_COLUMNS = ['date', 'close', 'high', 'low', 'split_ratio']
QUOTE_NUMBERS = ('price', 'previous_close', 'year_high', 'year_low')


def load_snapshot(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if data['version'] != 1 or data['mode'] not in ('CLOSE', 'INTRADAY'):
            raise ValueError
        day = date.fromisoformat(data['session'])
        cutoff = date.fromisoformat(data['history_session'])
        stamp = datetime.fromisoformat(data['generated_at'])
        if cutoff > day or stamp.tzinfo is None or not isinstance(data['symbols'], dict):
            raise ValueError
        for symbol, item in data['symbols'].items():
            if ticker(symbol) != symbol:
                raise ValueError
            q = item['quote']
            if set(q) != set(QUOTE_NUMBERS) | {'session', 'source', 'error', 'fetched_at', 'price_at'}:
                raise ValueError
            if q['session'] != day.isoformat() or not isinstance(q.get('source'), str):
                raise ValueError
            for name in QUOTE_NUMBERS:
                value = q[name]
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0):
                    raise ValueError
            if q.get('error') is not None and not isinstance(q['error'], str):
                raise ValueError
            for field in ('fetched_at', 'price_at'):
                if q.get(field) is not None and datetime.fromisoformat(q[field]).tzinfo is None:
                    raise ValueError
            previous = None
            for bar in item['bars']:
                if len(bar) != len(BAR_COLUMNS):
                    raise ValueError
                bar_day = date.fromisoformat(bar[0])
                if bar_day > cutoff or previous is not None and bar_day <= previous:
                    raise ValueError
                previous = bar_day
                for i, value in enumerate(bar[1:], 1):
                    if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or i <= 3 and value == 0):
                        raise ValueError
                if bar[1] is None:
                    raise ValueError
        return data
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        raise DataUnavailable('Cloud market snapshot missing or invalid') from None


class SnapshotProvider:
    def __init__(self, path: Path):
        self.data = load_snapshot(path)
        self.session = date.fromisoformat(self.data['session'])
        self.history_session = date.fromisoformat(self.data['history_session'])
        self.mode = self.data['mode']

    def search(self, query):
        return []  # Editing/search belongs to the local dashboard.

    def get_quote(self, symbol, session=None):
        if session is not None and session != self.session:
            return Quote(symbol, session=session, source='Saved market snapshot', error='Data unavailable: snapshot session mismatch')
        item = self.data['symbols'].get(symbol)
        if item is None:
            return Quote(symbol, session=self.session, source='Saved market snapshot', error='Data unavailable: symbol not in snapshot')
        q = dict(item['quote'])
        q['session'] = date.fromisoformat(q['session'])
        for name in ('fetched_at', 'price_at'):
            q[name] = datetime.fromisoformat(q[name]) if q.get(name) else None
        return replace(Quote(symbol, **q), source=f"{q['source']} · saved snapshot")

    def get_intraday_quote(self, symbol, session, now=None):
        if self.mode != 'INTRADAY':
            return Quote(symbol, session=session, error='Data unavailable: no intraday snapshot')
        return self.get_quote(symbol, session)

    def get_history_range(self, symbol, start, end):
        item = self.data['symbols'].get(symbol)
        if item is None or not item['bars']:
            raise DataUnavailable('Saved historical prices unavailable')
        frame = pd.DataFrame(item['bars'], columns=BAR_COLUMNS)
        frame['date'] = frame['date'].map(date.fromisoformat)
        return frame[(frame.date >= start) & (frame.date <= end)].copy()

    def get_history(self, symbol, period, as_of=None):
        if period not in PERIODS:
            raise ValueError('Unsupported history period')
        day = as_of or self.history_session
        start = period_start(day, period)
        frame = self.get_history_range(symbol, start, day)
        if start not in set(frame.date) or day not in set(frame.date):
            raise DataUnavailable('Historical endpoints missing or stale')
        return frame
