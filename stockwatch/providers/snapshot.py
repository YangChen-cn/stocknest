"""Read-only market snapshot provider. Never contacts Yahoo or writes user files."""
import json
import math
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from stockwatch.history import period_start
from stockwatch.providers.base import DataUnavailable, PERIODS, Quote
from stockwatch.providers.bars import BAR_COLUMNS, LEGACY_COLUMNS, validate_bars
from stockwatch.storage import ticker

QUOTE_NUMBERS = ('price', 'previous_close', 'year_high', 'year_low')


def load_snapshot(path: Path) -> dict:
    try:
        def reject_constant(value):
            raise ValueError('Non-finite JSON number')
        data = json.loads(path.read_text(encoding='utf-8'), parse_constant=reject_constant)
        if type(data['version']) is not int or data['version'] not in (1, 2) or data['mode'] not in ('CLOSE', 'INTRADAY'):
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
            columns = LEGACY_COLUMNS if data['version'] == 1 else BAR_COLUMNS
            if not isinstance(item['bars'], list) or any(not isinstance(bar, list) or len(bar) != len(columns) for bar in item['bars']):
                raise ValueError
            frame = validate_bars(pd.DataFrame(item['bars'], columns=columns))
            if not frame.empty and frame.date.max() > cutoff:
                raise ValueError
            if data['version'] == 1 and frame.close.isna().any():
                raise ValueError
            if data['version'] == 2:
                meta = item['history']
                if set(meta) != {'source', 'fetched_at', 'adjustment', 'status'}:
                    raise ValueError
                if not isinstance(meta['source'], str) or meta['status'] not in ('complete', 'partial', 'unavailable'):
                    raise ValueError
                if meta['adjustment'] not in ('split_adjusted_no_dividends', 'unknown'):
                    raise ValueError
                if meta['fetched_at'] is not None and datetime.fromisoformat(meta['fetched_at']).tzinfo is None:
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
        columns = LEGACY_COLUMNS if self.data['version'] == 1 else BAR_COLUMNS
        frame = validate_bars(pd.DataFrame(item['bars'], columns=columns))
        frame = frame[(frame.date >= start) & (frame.date <= end)].copy()
        meta = item.get('history', {'source': item['quote']['source'], 'fetched_at': self.data['generated_at'],
                                  'adjustment': 'unknown', 'status': 'legacy'})
        frame.attrs.update(meta, snapshot_version=self.data['version'], saved=True,
                           generated_at=self.data['generated_at'], history_session=self.data['history_session'])
        return frame

    def get_history(self, symbol, period, as_of=None):
        if period not in PERIODS:
            raise ValueError('Unsupported history period')
        day = as_of or self.history_session
        start = period_start(day, period)
        frame = self.get_history_range(symbol, start, day)
        if start not in set(frame.date) or day not in set(frame.date):
            raise DataUnavailable('Historical endpoints missing or stale')
        return frame
