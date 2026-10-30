from datetime import date, timedelta, datetime, timezone
from decimal import Decimal
import json
import math

import pandas as pd
import pytest

from stockwatch.history import sessions
from stockwatch.quantitative import build_analysis, instrument_facts, observation_lines
from stockwatch.services import quantitative_histories
from stockwatch.report_data import report_data, serialize_data
from stockwatch.providers.base import DataUnavailable

DAY = date(2026, 10, 6)
CONFIG = {'portfolio': {'benchmark': 'BENCH'}, 'watchlist': {'WATCH': {}}}
BOOK = {'holdings': [{'symbol': 'HELD', 'market_value': Decimal(70)}, {'symbol': 'OTHER', 'market_value': Decimal(30)}],
        'complete': True, 'market_value': Decimal(100)}


def bars():
    days = sessions(DAY - timedelta(days=160), DAY)
    prices = [100 * math.exp(i * .001 + (.003 if i % 2 else 0)) for i in range(len(days))]
    frame = pd.DataFrame({'date': days, 'open': prices, 'close': prices,
                          'high': [x + 2 for x in prices], 'low': [x - 2 for x in prices], 'volume': 1000, 'split_ratio': 0})
    frame.attrs.update(source='Test daily source', fetched_at='2026-10-06T22:00:00+00:00', adjustment='split_adjusted_no_dividends')
    return frame


def test_values_volatility_units_and_adjacent_changes():
    frame = bars()
    result = instrument_facts('HELD', frame, DAY, ['holding'])
    assert result['quality'] == 'complete' and result['session'] == str(DAY)
    assert result['source'] == 'Test daily source'
    assert float(result['metrics']['sma20']['value']) == pytest.approx(frame.close.tail(20).mean())
    expected = frame.close.map(math.log).diff().tail(20).std(ddof=1) * math.sqrt(252) * 100
    assert float(result['metrics']['historical_volatility20_pct']['value']) == pytest.approx(expected)
    assert float(result['metrics']['atr_price_ratio']['value']) == pytest.approx(4 / frame.close.iloc[-1])
    change = result['changes_from_previous_session']['sma20']
    assert float(change['delta']) == pytest.approx(frame.close.tail(20).mean() - frame.close.iloc[-21:-1].mean())


def test_missing_short_stale_invalid_history_and_unknown_adjustment():
    frame = bars()
    short = instrument_facts('A', frame.tail(10), DAY, [])
    assert short['metrics']['sma20']['value'] is None
    assert short['metrics']['sma20']['valid_samples'] == 10
    assert short['metrics']['sma20']['reason'] == 'insufficient_consecutive_history'
    stale = instrument_facts('A', frame.iloc[:-1], DAY, [])
    assert stale['quality'] == 'stale' and not stale['events']
    assert instrument_facts('A', None, DAY, [])['metrics']['rsi14']['reason'] == 'history_unavailable'
    invalid = frame.copy(); invalid.loc[0, 'low'] = -1
    assert 'invalid_history' in instrument_facts('A', invalid, DAY, [])['reasons']
    frame.attrs.clear()
    frame.loc[len(frame) - 1, ['close', 'high', 'open']] *= 1.2
    assert not instrument_facts('A', frame, DAY, [])['events']


def test_gap_and_intraday_no_future_leakage():
    frame = bars()
    all_data = build_analysis({s: frame for s in ['HELD', 'OTHER', 'WATCH', 'BENCH']}, BOOK, CONFIG, DAY, mode='INTRADAY')
    assert all(i['session'] < str(DAY) for i in all_data['instruments'])
    frame2 = frame.copy(); frame2.loc[frame2.index[-1], ['open', 'close', 'high', 'low']] *= 2
    assert all_data == build_analysis({s: frame2 for s in ['HELD', 'OTHER', 'WATCH', 'BENCH']}, BOOK, CONFIG, DAY, mode='INTRADAY')
    frame2.loc[frame2.index[-1], 'high'] = -1
    assert all_data == build_analysis({s: frame2 for s in ['HELD', 'OTHER', 'WATCH', 'BENCH']}, BOOK, CONFIG, DAY, mode='INTRADAY')
    gap = frame.drop(frame.index[-2])
    item = instrument_facts('A', gap, DAY, [])
    assert item['metrics']['sma20']['value'] is None and not item['events']
    assert item['changes_from_previous_session']['rsi14']['delta'] is None


def test_deterministic_events_concentration_and_json():
    frame = bars()
    last = frame.index[-1]
    frame.loc[last, ['close', 'open', 'high']] *= 1.2
    histories = {s: frame for s in ['HELD', 'OTHER', 'WATCH', 'BENCH']}
    result = build_analysis(histories, BOOK, CONFIG, DAY)
    assert any(e['kind'] == 'unusual_daily_move' for e in result['events'])
    assert len(result['email_observations']) == 3
    assert result == build_analysis(dict(reversed(list(histories.items()))), BOOK, CONFIG, DAY)
    assert result['portfolio_concentration']['largest_weight_fraction'] == '0.7'
    assert result['portfolio_concentration']['hhi'] == str(.7 * .7 + .3 * .3)
    assert len(observation_lines(result, 'zh-CN')) == 4
    json.loads(serialize_data(result))
    broken = build_analysis({}, dict(BOOK, complete=False), CONFIG, DAY)
    assert broken['portfolio_concentration']['hhi'] is None
    assert broken['portfolio_concentration']['high_atr_weight_fraction'] is None


def test_provider_failure_isolated_and_same_range_cache_can_be_reused():
    calls = []
    class Provider:
        def get_history_range(self, symbol, start, end):
            calls.append((symbol, start, end))
            if symbol == 'WATCH':
                raise DataUnavailable('no data')
            return bars()
    loaded = quantitative_histories(CONFIG, BOOK, Provider(), DAY, 'INTRADAY')
    assert loaded['WATCH'] is None and len(calls) == 4
    assert all(end < DAY and start == DAY - timedelta(days=380) for _, start, end in calls)


def test_report_field_is_optional_and_existing_version_unchanged():
    kwargs = dict(mode='CLOSE', generated_at=datetime.now(timezone.utc))
    base = report_data(DAY, BOOK, {}, CONFIG, [], **kwargs)
    assert 'quantitative_analysis' not in base
    extra = build_analysis({}, BOOK, CONFIG, DAY)
    enriched = report_data(DAY, BOOK, {}, CONFIG, [], quantitative_analysis=extra, **kwargs)
    assert enriched.pop('quantitative_analysis') == extra
    assert base == enriched and base['schema_version'] == 1


def test_bilingual_email_and_json_share_identical_analysis():
    from stockwatch.providers.demo import DemoProvider
    from stockwatch.storage import ROOT, load_config, load_transactions
    from stockwatch.services import snapshot
    from stockwatch.reports import render_report
    provider = DemoProvider()
    config = load_config(ROOT / 'examples/config.yaml')
    book, quotes = snapshot(config, load_transactions(ROOT / 'examples/transactions.csv'), provider, provider.session, closing=True)
    analysis = build_analysis(quantitative_histories(config, book, provider, provider.session, 'CLOSE'), book, config, provider.session)
    for lang, heading in [('en', 'Quantitative market observations'), ('zh-CN', '市场量化观察')]:
        config['portfolio']['language'] = lang
        report = render_report(provider.session, book, quotes, config, [], quantitative_analysis=analysis)
        assert heading in report.text and heading in report.html
        assert json.loads(report.data_json)['quantitative_analysis'] == analysis
        assert report.data_json in report.text


def test_optional_quantitative_failure_does_not_block_daily(portfolio_files, monkeypatch):
    from stockwatch import quantitative
    from stockwatch.daily import run
    def fail(*args, **kwargs):
        raise ValueError('invalid optional analysis')
    monkeypatch.setattr(quantitative, 'build_analysis', fail)
    assert run(**portfolio_files, dry_run=True) == 0
    html = (portfolio_files['output_dir'] / f'{DAY}.html').read_text()
    assert 'Quantitative market observations' not in html
