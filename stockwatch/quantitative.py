"""Read-only quantitative facts from completed daily bars, never trading signals."""
import math

import pandas as pd

from stockwatch.calendar import previous_session
from stockwatch.technical import analyze
from stockwatch.report_data import numeric

METRICS = {
    'sma20': ('sma_20', 20, 'USD'), 'sma50': ('sma_50', 50, 'USD'),
    'distance_sma20_pct': ('distance_sma20_pct', 20, 'percentage_points'),
    'distance_sma50_pct': ('distance_sma50_pct', 50, 'percentage_points'),
    'rsi14': ('rsi_14', 15, 'index_0_100'), 'macd_signal_difference': ('macd_histogram', 34, 'USD'),
    'atr14': ('atr_14', 14, 'USD'), 'atr_price_ratio': ('atr_price_ratio', 14, 'fraction'),
    'historical_volatility20_pct': ('historical_volatility20_pct', 21, 'annualized_percentage_points'),
}


def _number(value):
    return None if value is None or not math.isfinite(float(value)) else float(value)


def instrument_facts(symbol, frame, cutoff, roles):
    meta = dict(getattr(frame, 'attrs', {}))
    item = dict(symbol=symbol, roles=roles, expected_session=str(cutoff), session=None,
                source=meta.get('source') or 'unknown', fetched_at=meta.get('fetched_at'),
                adjustment=meta.get('adjustment', 'unknown'), price_basis='completed_daily_close',
                quality='unavailable', reasons=[], valid_close_samples=0, consecutive_close_samples=0,
                metrics={}, changes_from_previous_session={}, events=[])
    try:
        if frame is not None and not frame.empty:
            # Exclude forming/future bars before validating OHLC, not just before
            # smoothing: malformed future OHLC must not invalidate past facts.
            days = pd.to_datetime(frame['date'], errors='raise').dt.date
            frame = frame.loc[days <= cutoff].copy()
        values = analyze(frame, cutoff)
    except (ValueError, TypeError, RuntimeError, KeyError, AttributeError, OverflowError):
        values = pd.DataFrame()
        item['reasons'].append('invalid_history')
    valid = values[values.close.notna()] if not values.empty else values
    if valid.empty:
        for key, (_, required, unit) in METRICS.items():
            item['metrics'][key] = dict(session=None, source=item['source'], quality='unavailable', value=None, unit=unit, valid_samples=0, required_samples=required, reason='history_unavailable')
        item['reasons'].append('history_unavailable')
        return item
    # An older valid close is explicitly stale, never relabeled as the target day.
    day = valid.iloc[-1].date
    values = values[values.date <= day].copy()
    values['distance_sma20_pct'] = (values.close / values.sma_20 - 1) * 100
    values['distance_sma50_pct'] = (values.close / values.sma_50 - 1) * 100
    values['atr_price_ratio'] = values.atr_14 / values.close
    log_returns = values.close.map(lambda x: math.log(x) if pd.notna(x) else float('nan')).diff()
    values['historical_volatility20_pct'] = log_returns.rolling(20, min_periods=20).std(ddof=1) * math.sqrt(252) * 100
    current = values.iloc[-1]
    prior_rows = values[values.date == previous_session(day)]
    prior = prior_rows.iloc[-1] if not prior_rows.empty else None
    close_run = atr_run = 0
    for row in reversed(list(values.itertuples())):
        if pd.isna(row.close):
            break
        close_run += 1
    for row in reversed(list(values.itertuples())):
        if any(pd.isna(x) for x in (row.close, row.high, row.low)):
            break
        atr_run += 1
    item.update(session=str(day), previous_session=str(previous_session(day)), close_usd=numeric(current.close),
                valid_close_samples=int(values.close.notna().sum()), consecutive_close_samples=close_run,
                missing_close_sessions=int(values.close.isna().sum()))
    if day != cutoff:
        item['reasons'].append('stale_history')
    if values.close.isna().any():
        item['reasons'].append('historical_gaps')
    if item['adjustment'] != 'split_adjusted_no_dividends':
        item['reasons'].append('adjustment_unknown')
    for key, (column, required, unit) in METRICS.items():
        count = atr_run if key.startswith('atr') else close_run
        value = _number(current[column])
        reason = None if value is not None else ('missing_ohlc' if key.startswith('atr') and atr_run < close_run else 'insufficient_consecutive_history')
        item['metrics'][key] = dict(session=str(day), source=item['source'], quality='unavailable' if value is None else 'stale' if day != cutoff else 'partial' if item['adjustment'] == 'unknown' else 'complete', value=numeric(value), unit=unit, valid_samples=count, required_samples=required, reason=reason)
        old = _number(prior[column]) if prior is not None else None
        item['changes_from_previous_session'][key] = dict(previous_value=numeric(old), delta=numeric(value - old) if value is not None and old is not None else None,
                                                         reason=None if value is not None and old is not None else 'comparison_unavailable')
    if any(m['reason'] for m in item['metrics'].values()):
        item['reasons'].append('indicator_history_incomplete')
    item['quality'] = 'stale' if day != cutoff else 'partial' if item['reasons'] else 'complete'
    # No event from stale history, unknown adjustment or non-adjacent sessions.
    if day == cutoff and prior is not None and item['adjustment'] == 'split_adjusted_no_dividends':
        for column, event in [('distance_sma50_pct', 'sma50_cross'), ('macd_histogram', 'macd_signal_cross')]:
            old, new = _number(prior[column]), _number(current[column])
            if old is not None and new is not None and (old <= 0 < new or old >= 0 > new):
                item['events'].append(dict(kind=event, direction='above' if new > 0 else 'below', session=str(day), previous_value=numeric(old), value=numeric(new), priority=2))
        old, new = _number(prior.rsi_14), _number(current.rsi_14)
        if old is not None and new is not None and ((old < 70 <= new) or (old > 30 >= new)):
            item['events'].append(dict(kind='rsi_reference_cross', direction='above_70' if new >= 70 else 'below_30', session=str(day), previous_value=numeric(old), value=numeric(new), priority=3))
        if pd.notna(prior.close):
            change = (current.close / prior.close - 1) * 100
            sigma = _number(prior.historical_volatility20_pct)
            threshold = max(5, 2 * sigma / math.sqrt(252)) if sigma is not None else None
            if threshold is not None and abs(change) >= threshold:
                item['events'].append(dict(kind='unusual_daily_move', session=str(day), value=numeric(change), threshold_pct=numeric(threshold), baseline='previous_session_20_return_sample_volatility', priority=1))
    return item


def build_analysis(histories, portfolio, config, session, *, mode='CLOSE'):
    cutoff = previous_session(session) if mode == 'INTRADAY' else session
    held = {row['symbol'] for row in portfolio['holdings']}
    watched = set(config['watchlist'])
    benchmark = config['portfolio'].get('benchmark', 'SPYM')
    items = [instrument_facts(s, histories.get(s), cutoff,
             [name for name, symbols in [('holding', held), ('watchlist', watched), ('benchmark', {benchmark})] if s in symbols])
             for s in sorted(held | watched | {benchmark})]
    events = [dict(event, symbol=item['symbol'], roles=item['roles']) for item in items for event in item['events']]
    weights = []
    complete = portfolio.get('complete', False) and portfolio.get('market_value') is not None and portfolio['market_value'] > 0
    if complete:
        weights = sorted([(r['symbol'], float(r['market_value'] / portfolio['market_value'])) for r in portfolio['holdings']], key=lambda x: (-x[1], x[0]))
    concentration = dict(valuation_session=str(session), price_basis='intraday_valuation' if mode == 'INTRADAY' else 'closing_valuation',
        status='available' if complete else 'unavailable', reason=None if complete else 'incomplete_or_empty_portfolio_valuation',
        largest_weight_fraction=numeric(weights[0][1]) if weights else None,
        top_three_weight_fraction=numeric(sum(w for _, w in weights[:3])) if weights else None,
        hhi=numeric(sum(w * w for _, w in weights)) if weights else None,
        weights=[dict(symbol=s, weight_fraction=numeric(w)) for s, w in weights],
        interpretation='market-value concentration only; no sector, correlation or portfolio VaR estimate')
    by_symbol = {item['symbol']: item for item in items}
    covered = [(s, w) for s, w in weights if by_symbol[s]['session'] == str(cutoff)
               and by_symbol[s]['adjustment'] == 'split_adjusted_no_dividends'
               and by_symbol[s]['metrics']['atr_price_ratio']['value'] is not None]
    concentration.update(daily_risk_session=str(cutoff),
        atr_coverage_weight_fraction=numeric(sum(w for _, w in covered)) if weights else None,
        high_atr_weight_fraction=numeric(sum(w for s, w in covered if float(by_symbol[s]['metrics']['atr_price_ratio']['value']) >= .05)) if covered else None,
        high_atr_threshold_fraction='0.05', coverage_note='high ATR exposure uses only current completed daily ATR; uncovered holdings are not assumed low risk')
    if weights and weights[0][1] >= .4:
        events.append(dict(kind='holding_concentration', symbol=weights[0][0], session=str(session), value=numeric(weights[0][1]), threshold_fraction='0.4', priority=4))
    events.sort(key=lambda e: (e['priority'], 'holding' not in e.get('roles', []), e['symbol'], e['kind']))
    return dict(schema_version=1, expected_daily_session=str(cutoff), report_mode=mode,
        price_basis='previous_completed_session' if mode == 'INTRADAY' else 'completed_session',
        conventions=dict(volatility='sample standard deviation of 20 log returns, annualized sqrt(252); needs 21 consecutive closes',
                         smoothing='stockwatch.technical: SMA-seeded EMA; Wilder RSI and ATR; reset at missing sessions',
                         changes='adjacent NYSE sessions only; deltas use each metric unit',
                         missing='null is unavailable, never zero; do not infer recommendations',
                         news='not fetched or verified by StockWatch; join external news by symbol and dated source'),
        instruments=items, portfolio_concentration=concentration, events=events, email_observations=events[:3])


def observation_lines(analysis, lang):
    from stockwatch.i18n import t
    lines = [t('Daily indicators through {date}; completed closes only, not intraday signals.', lang, date=analysis['expected_daily_session'])]
    for event in analysis['email_observations']:
        template = {
            'unusual_daily_move': '{symbol} · {session}: close changed {value}% (rule threshold {threshold_pct}%).',
            'sma50_cross': '{symbol} · {session}: close/SMA50 deviation crossed zero ({previous_value}% → {value}%).',
            'macd_signal_cross': '{symbol} · {session}: MACD minus signal crossed zero ({previous_value} → {value} USD).',
            'rsi_reference_cross': '{symbol} · {session}: RSI crossed a 30/70 reference ({previous_value} → {value}).',
            'holding_concentration': '{symbol} · {session}: portfolio weight {weight}% reaches the 40% concentration reference.',
        }[event['kind']]
        values = dict(event)
        for key in ('value', 'previous_value', 'threshold_pct'):
            if key in values:
                values[key] = f"{float(values[key]):.2f}"
        if event['kind'] == 'holding_concentration':
            values['weight'] = f"{float(event['value']) * 100:.1f}"
        lines.append(t(template, lang, **values))
    if len(lines) == 1:
        lines.append(t('No qualifying quantitative changes in the available data; missing history is not evidence of stable conditions.', lang))
    return lines
