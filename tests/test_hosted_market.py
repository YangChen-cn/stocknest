from dataclasses import replace
from datetime import date, datetime, timezone
import json
from unittest.mock import Mock

import pandas as pd
import pytest

from stockwatch.market_snapshot import save_snapshot
from stockwatch.providers.base import DataUnavailable, Quote
from stockwatch.providers.hosted import HostedProvider
from stockwatch.providers.openbb_provider import OpenBBProvider, failure_code
from stockwatch.providers.snapshot import SnapshotProvider, load_snapshot
from stockwatch.storage import validate_transactions

DAY = date(2026, 10, 2)
STAMP = datetime(2026, 10, 2, 22, tzinfo=timezone.utc)


@pytest.fixture
def market(tmp_path):
    frame = pd.DataFrame([{'date': d.date(), 'close': 10., 'high': 11., 'low': 9., 'split_ratio': 0.}
                          for d in pd.bdate_range('2025-09-17', DAY)])
    provider = Mock()
    provider.get_history_range.return_value = frame
    q = Quote('XYZ', price=11, previous_close=10, session=DAY, fetched_at=STAMP)
    path = tmp_path / 'market.json'
    save_snapshot(path, provider, {'XYZ': q}, DAY, 'CLOSE', now=STAMP)
    return path, q, provider


def test_snapshot_quote_history_roundtrip_and_strict_dates(market):
    path, q, _ = market
    p = SnapshotProvider(path)
    result = p.get_quote('XYZ', DAY)
    assert result.price == 11 and result.fetched_at == STAMP and 'saved snapshot' in result.source
    assert len(p.get_history('XYZ', '5D', DAY)) == 6
    assert p.get_quote('XYZ', date(2026, 10, 5)).price is None
    assert p.get_quote('MISSING').price is None
    assert p.get_intraday_quote('XYZ', DAY).error
    with pytest.raises(DataUnavailable):
        p.get_history('XYZ', '1M', date(2026, 10, 5))


def test_online_first_then_snapshot_without_repeated_rate_limit(market):
    path, q, _ = market
    online = Mock()
    online.get_quote.return_value = q
    p = HostedProvider(online, path)
    assert p.get_quote('XYZ', DAY) == q
    online.get_quote.return_value = Quote('XYZ', session=DAY, error='Data unavailable: Provider rate limited')
    assert 'saved snapshot' in p.get_quote('XYZ', DAY).source
    assert p.blocked
    assert p.get_quote('XYZ', DAY).price == 11
    assert online.get_quote.call_count == 2
    assert p.get_quote('XYZ', date(2026, 10, 5)).price is None


def test_no_forming_snapshot_substituted_for_close(market):
    path, q, provider = market
    provider.get_history_range.return_value = provider.get_history_range.return_value.query('date < @DAY')
    save_snapshot(path, provider, {'XYZ': q}, DAY, 'INTRADAY', now=STAMP)
    online = Mock()
    online.get_quote.return_value = Quote('XYZ', session=DAY, error='unavailable')
    p = HostedProvider(online, path)
    assert p.get_quote('XYZ', DAY).price is None


def test_snapshot_total_failure_and_write_failure_preserve_old(market, monkeypatch):
    from stockwatch import market_snapshot
    path, q, provider = market
    before = path.read_bytes()
    assert not save_snapshot(path, provider, {'XYZ': Quote('XYZ', session=DAY, error='failed')}, DAY, 'CLOSE')
    assert path.read_bytes() == before
    monkeypatch.setattr(market_snapshot, 'atomic_write', Mock(side_effect=OSError('disk')))
    with pytest.raises(OSError):
        save_snapshot(path, provider, {'XYZ': replace(q, price=12)}, DAY, 'CLOSE')
    assert path.read_bytes() == before


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1, 0])
def test_bad_snapshot_price_rejected(market, value):
    path, _, _ = market
    raw = json.loads(path.read_text())
    raw['symbols']['XYZ']['quote']['price'] = value
    path.write_text(json.dumps(raw))
    with pytest.raises(DataUnavailable):
        load_snapshot(path)


def test_snapshot_whitelist_no_account_data(market):
    path, _, _ = market
    raw = json.loads(path.read_text())
    assert set(raw) == {'version', 'session', 'history_session', 'mode', 'generated_at', 'symbols'}
    assert set(raw['symbols']['XYZ']) == {'quote', 'bars'}
    assert 'note' not in path.read_text() and 'GMAIL' not in path.read_text()


def test_hosted_whole_fallback_uses_saved_ledger_date(market, monkeypatch):
    from stockwatch import ui
    path, _, _ = market
    online = Mock()
    online.get_intraday_quote.return_value = Quote('XYZ', error='Data unavailable: Provider rate limited')
    p = HostedProvider(online, path)
    ledger = validate_transactions([
        {'date': '2026-10-02', 'symbol': 'XYZ', 'side': 'BUY', 'shares': '1', 'price': '10', 'note': ''},
        {'date': '2026-10-05', 'symbol': 'XYZ', 'side': 'BUY', 'shares': '2', 'price': '10', 'note': ''}])
    monkeypatch.setattr(ui, 'cloud_readonly', lambda: True)
    monkeypatch.setattr(ui, 'active_session', lambda: date(2026, 10, 5))
    book, quotes = ui.ui_snapshot({'watchlist': {}}, ledger, p, date(2026, 10, 5), False)
    assert book['_snapshot_whole'] and book['_market_snapshot']['session'] == DAY.isoformat()
    assert book['market_value'] == 11 and book['holdings'][0]['shares'] == 1
    assert quotes['XYZ'].session == DAY


def test_safe_worker_error_codes_and_lightweight_payload(monkeypatch, caplog):
    from stockwatch.providers import openbb_provider as module
    import subprocess
    def worker(*a, **kw):
        assert json.loads(kw['input'])['lightweight'] is True
        return subprocess.CompletedProcess(a, 0, json.dumps({'error': 'rate_limited'}), '')
    monkeypatch.setattr(module.subprocess, 'run', worker)
    q = OpenBBProvider(attempts=1, lightweight=True).get_quote('XYZ', DAY)
    assert 'rate limited' in q.error and 'failure=Provider rate limited' in caplog.text
    assert failure_code(ValueError('429 credentials=do-not-echo')) == 'rate_limited'
    assert 'credentials' not in caplog.text


def test_lightweight_dates_and_unadjusted_dividends(monkeypatch):
    from stockwatch.providers.openbb_provider import lightweight_request
    import yfinance as yf
    instrument = Mock()
    instrument.history.return_value = pd.DataFrame({'Close': [10.], 'Stock Splits': [0.]},
        index=pd.DatetimeIndex(['2026-10-02'], tz='America/New_York', name='Date'))
    monkeypatch.setattr(yf, 'Ticker', lambda symbol: instrument)
    rows = lightweight_request({'symbol': 'XYZ', 'start': '2026-10-01', 'end': '2026-10-03'})['data']
    assert rows[0]['date'] == '2026-10-02' and rows[0]['close'] == 10
    assert instrument.history.call_args.kwargs['auto_adjust'] is False
    assert instrument.history.call_args.kwargs['prepost'] is False


def test_daily_snapshot_independent_of_email_failure(tmp_path, monkeypatch):
    from stockwatch.daily import run
    from stockwatch.notifications.email import EmailDeliveryError
    from stockwatch.providers.demo import DemoProvider
    from stockwatch.storage import ROOT
    monkeypatch.setenv('GMAIL_ADDRESS', 'test@example.com')
    monkeypatch.setenv('GMAIL_APP_PASSWORD', 'test-only')
    monkeypatch.setenv('REPORT_EMAIL', 'test@example.com')
    p = DemoProvider()
    output = tmp_path / 'market.json'
    sender = Mock(side_effect=EmailDeliveryError('SMTP failed'))
    code = run(config_path=ROOT / 'examples/config.yaml', transactions_path=ROOT / 'examples/transactions.csv',
               state_path=tmp_path / 'state.json', output_dir=tmp_path / 'reports', provider=p, session=p.session,
               close_retry_seconds=0, market_snapshot_path=output, sender=sender, importer=lambda *a, **kw: {'disabled': True})
    assert code == 1 and load_snapshot(output)['session'] == p.session.isoformat()
    assert not json.loads((tmp_path / 'state.json').read_text()).get('_meta', {}).get('last_report_session')
    previous = output.read_bytes()
    run(config_path=ROOT / 'examples/config.yaml', transactions_path=ROOT / 'examples/transactions.csv',
        state_path=tmp_path / 'state.json', output_dir=tmp_path / 'reports', provider=p, session=p.session,
        dry_run=True, market_snapshot_path=output, sender=sender, importer=lambda *a, **kw: {'disabled': True})
    assert output.read_bytes() == previous and sender.call_count == 1
