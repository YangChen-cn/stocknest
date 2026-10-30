from datetime import date
import json

import pandas as pd
from streamlit.testing.v1 import AppTest
import pytest

from stockwatch import ui
from stockwatch.providers.base import DataUnavailable
from stockwatch.providers.demo import DemoProvider
from stockwatch.storage import ROOT, save_config, save_transactions
from stockwatch.technical import analyze
from stockwatch.ui_quant import technical_figure


@pytest.fixture
def lab(tmp_path, monkeypatch):
    save_config(tmp_path / 'config.yaml', {'portfolio': {'language': 'zh-CN'}, 'watchlist': {'XYZ': {}}})
    save_transactions(tmp_path / 'data/transactions.csv', [])
    (tmp_path / 'examples').symlink_to(ROOT / 'examples', target_is_directory=True)
    monkeypatch.setattr(ui, 'ROOT', tmp_path)
    monkeypatch.delenv('STOCKWATCH_CLOUD_TOKEN', raising=False)
    monkeypatch.delenv('STOCKWATCH_CLOUD_REPOSITORY', raising=False)
    def no_portfolio(*args, **kwargs):
        raise AssertionError('Quant Lab must not fetch portfolio quotes')
    monkeypatch.setattr(ui, 'cached_snapshot', no_portfolio)
    app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=10)
    app.session_state['navigation'] = 'Quant Lab'
    return app, tmp_path


@pytest.mark.parametrize('readonly', [False, True])
def test_quant_lab_load_controls_no_refetch_no_user_writes(lab, monkeypatch, readonly):
    app, root = lab
    monkeypatch.setenv('STOCKWATCH_READONLY', '1' if readonly else '0')
    calls = []
    def load(symbol, day, demo, offline):
        calls.append((symbol, day, demo, offline))
        return DemoProvider().get_history_range('CORE', date(2025, 10, 1), date(2026, 10, 6))
    monkeypatch.setattr(ui, 'cached_quant_history', load)
    before = {path: path.read_bytes() for path in (root / 'config.yaml', root / 'data/transactions.csv')}
    app.run()
    assert not app.exception and app.title[0].value == '量化实验室'
    assert not calls and not app.get('plotly_chart')
    app.sidebar.toggle[0].set_value(True).run()
    next(s for s in app.selectbox if s.key == 'quant_symbol').set_value('CORE')
    next(b for b in app.button if b.label == '加载日线').click().run()
    assert not app.exception and len(calls) == 1 and app.get('plotly_chart')
    next(m for m in app.multiselect if m.key == 'quant_panels').set_value(['RSI 14', 'MACD (12,26,9)', 'ATR 14']).run()
    next(c for c in app.checkbox if c.key == 'quant_bands').check().run()
    next(s for s in app.selectbox if s.key == 'quant_period').set_value('1M').run()
    assert not app.exception and len(calls) == 1
    assert before == {path: path.read_bytes() for path in before}
    assert not (root / 'data/state.json').exists() and not (root / 'data/performance.json').exists()
    assert not (root / 'data/market_snapshot.json').exists()


def test_legacy_offline_snapshot_chart_and_failed_reload_keep_chart(lab, monkeypatch):
    app, root = lab
    raw = {'version': 1, 'mode': 'CLOSE', 'session': '2026-10-06', 'history_session': '2026-10-06',
           'generated_at': '2026-10-06T22:00:00+00:00', 'symbols': {'XYZ': {
               'quote': {'price': 10, 'previous_close': 9, 'year_high': None, 'year_low': None,
                         'session': '2026-10-06', 'source': 'OpenBB / yfinance', 'error': None, 'fetched_at': None, 'price_at': None},
               'bars': [['2026-10-05', 9, 10, 8, 0], ['2026-10-06', 10, 11, 9, 0]]}}}
    path = root / 'data/market_snapshot.json'
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(ui, 'latest_session', lambda: date(2026, 10, 6))
    monkeypatch.setattr(ui, 'ui_provider', lambda *_: pytest.fail('offline load contacted provider'))
    ui.cached_quant_history.clear()
    app.run()
    next(s for s in app.selectbox if s.key == 'quant_symbol').set_value('XYZ')
    next(b for b in app.button if b.label == '加载日线').click().run()
    assert not app.exception and app.get('plotly_chart')
    spec = json.loads(app.get('plotly_chart')[0].proto.spec)
    assert spec['data'][0]['type'] == 'scatter'  # no made-up candle opens
    assert any('缺少 OHLC' in i.value for i in app.info)
    before = path.read_bytes()
    def fail(*args):
        raise DataUnavailable('unavailable')
    monkeypatch.setattr(ui, 'cached_quant_history', fail)
    next(b for b in app.button if b.label == '加载日线').click().run()
    assert not app.exception and app.get('plotly_chart')
    assert any('校验失败' in w.value for w in app.warning)
    assert path.read_bytes() == before


def test_candles_and_optional_panels_preserve_missing_volume():
    frame = DemoProvider().get_history_range('CORE', date(2026, 6, 1), date(2026, 10, 6))
    frame.loc[5, 'volume'] = float('nan')
    result = analyze(frame)
    fig = technical_figure(result, 'CORE', ['SMA 20'], ['RSI 14', 'MACD (12,26,9)', 'ATR 14'], bands=True)
    assert fig.data[0].type == 'candlestick'
    volume = next(trace for trace in fig.data if trace.name == 'Volume (shares)')
    assert pd.isna(volume.y[5])
    assert any(trace.name == 'macd_signal' for trace in fig.data)
