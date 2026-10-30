from datetime import date, datetime, timezone
import json
from unittest.mock import Mock

import pandas as pd
import pytest

from stockwatch.market_snapshot import save_snapshot
from stockwatch.providers.bars import BAR_COLUMNS, LEGACY_COLUMNS, validate_bars
from stockwatch.providers.base import DataUnavailable, Quote
from stockwatch.providers.snapshot import SnapshotProvider, load_snapshot

DAY = date(2026, 10, 6)
STAMP = datetime(2026, 10, 6, 22, tzinfo=timezone.utc)


@pytest.fixture
def saved(tmp_path):
    path = tmp_path / 'market.json'
    frame = pd.DataFrame([dict(date='2026-10-05', open=10, high=12, low=9, close=11, volume=0, split_ratio=0),
                          dict(date='2026-10-06', open=11, high=13, low=10, close=12, volume=1234, split_ratio=2)])
    frame.attrs.update(source='Yahoo / yfinance', fetched_at=STAMP.isoformat(), adjustment='split_adjusted_no_dividends')
    provider = Mock()
    provider.get_history_range.return_value = frame
    save_snapshot(path, provider, {'XYZ': Quote('XYZ', 12, 11, session=DAY, fetched_at=STAMP)}, DAY, 'CLOSE', now=STAMP)
    return path, provider


def test_v2_ohlcv_and_provenance_roundtrip(saved):
    path, _ = saved
    assert load_snapshot(path)['version'] == 2
    frame = SnapshotProvider(path).get_history_range('XYZ', date(2026, 10, 5), DAY)
    assert list(frame.columns) == BAR_COLUMNS
    assert frame.iloc[-1].to_dict() == dict(date=DAY, open=11, high=13, low=10, close=12, volume=1234, split_ratio=2)
    assert frame.attrs['source'] == 'Yahoo / yfinance'
    assert frame.attrs['fetched_at'] == STAMP.isoformat() and frame.attrs['status'] == 'complete'


def test_v1_read_only_migration_keeps_quotes_and_null_missing_fields(saved):
    path, _ = saved
    raw = json.loads(path.read_text())
    raw['version'] = 1
    item = raw['symbols']['XYZ']
    item.pop('history')
    item['bars'] = [[row[BAR_COLUMNS.index(key)] for key in LEGACY_COLUMNS] for row in item['bars']]
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    provider = SnapshotProvider(path)
    assert provider.get_quote('XYZ').price == 12
    frame = provider.get_history_range('XYZ', date(2026, 10, 5), DAY)
    assert frame.open.isna().all() and frame.volume.isna().all()
    assert frame.close.tolist() == [11, 12] and frame.attrs['status'] == 'legacy'
    assert before == path.read_bytes()


@pytest.mark.parametrize('field,value', [('open', 14), ('high', 8), ('low', 12), ('close', 0),
    ('volume', -1), ('volume', 1.5), ('volume', True), ('split_ratio', -1), ('open', float('inf'))])
def test_bad_bars_rejected(field, value):
    row = dict(date='2026-10-06', open=10, high=12, low=9, close=11, volume=100, split_ratio=0)
    row[field] = value
    with pytest.raises(DataUnavailable):
        validate_bars([row])


@pytest.mark.parametrize('days', [['2026-10-06', '2026-10-05'], ['2026-10-06', '2026-10-06'], ['bad']])
def test_invalid_date_order_and_duplicates_rejected(days):
    with pytest.raises(DataUnavailable):
        validate_bars([{'date': d, 'close': 10} for d in days])


def test_bad_ticker_history_isolated_from_quote_and_other_symbols(saved):
    path, provider = saved
    good = provider.get_history_range.return_value
    provider.get_history_range.side_effect = [good, pd.DataFrame([{'date': DAY, 'close': -1}])]
    quotes = {s: Quote(s, 12, 11, session=DAY) for s in ('AAA', 'BAD')}
    assert save_snapshot(path, provider, quotes, DAY, 'CLOSE', now=STAMP)
    raw = load_snapshot(path)
    assert raw['symbols']['AAA']['history']['status'] == 'complete'
    assert raw['symbols']['BAD']['history']['status'] == 'unavailable'
    assert raw['symbols']['BAD']['bars'] == []
    assert SnapshotProvider(path).get_quote('BAD').price == 12


def test_future_bars_unknown_version_and_nonfinite_json_rejected(saved):
    path, _ = saved
    original = json.loads(path.read_text())
    for change in ('future', 'version', 'nan'):
        raw = json.loads(json.dumps(original))
        if change == 'future':
            raw['symbols']['XYZ']['bars'][-1][0] = '2026-10-07'
        elif change == 'version':
            raw['version'] = 99
        else:
            raw['symbols']['XYZ']['bars'][0][1] = float('nan')
        path.write_text(json.dumps(raw))
        with pytest.raises(DataUnavailable):
            SnapshotProvider(path)
