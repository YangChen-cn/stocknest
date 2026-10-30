"""Daily-bar validation shared by snapshots and read-only technical analysis."""
from datetime import date, datetime
from numbers import Real
import math

import pandas as pd

from stockwatch.providers.base import DataUnavailable

BAR_COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume', 'split_ratio']
LEGACY_COLUMNS = ['date', 'close', 'high', 'low', 'split_ratio']


def validate_bars(raw) -> pd.DataFrame:
    """Keep nulls as gaps; reject invalid values, duplicates and unsorted dates.

    Missing fields are allowed for v1/partial data. No price or volume is invented.
    A zero split ratio means no event; a positive ratio records the provider event.
    Session filtering belongs to analysis, not this legacy-compatible decoder.
    """
    frame = pd.DataFrame(raw).copy()
    if frame.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    if 'date' not in frame:
        raise DataUnavailable('Invalid daily bars')
    try:
        days = []
        for value in frame.date:
            if isinstance(value, datetime):
                raise ValueError
            day = value if isinstance(value, date) else date.fromisoformat(value)
            days.append(day)
        if days != sorted(set(days)):
            raise ValueError
        frame['date'] = days
        for name in BAR_COLUMNS[1:]:
            if name not in frame:
                frame[name] = float('nan')
            values = []
            for value in frame[name]:
                if value is None or value is pd.NA or isinstance(value, Real) and math.isnan(value):
                    values.append(float('nan'))
                    continue
                if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
                    raise ValueError
                number = float(value)
                if name in ('volume', 'split_ratio'):
                    if number < 0 or name == 'volume' and not number.is_integer():
                        raise ValueError
                elif number <= 0:
                    raise ValueError
                values.append(number)
            frame[name] = values
        for row in frame.itertuples():
            if pd.notna(row.high) and any(pd.notna(v) and v > row.high for v in (row.open, row.close, row.low)):
                raise ValueError
            if pd.notna(row.low) and any(pd.notna(v) and v < row.low for v in (row.open, row.close, row.high)):
                raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise DataUnavailable('Invalid daily bars') from None
    return frame[BAR_COLUMNS].reset_index(drop=True)
