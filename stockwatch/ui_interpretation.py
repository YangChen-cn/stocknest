"""Beginner portfolio explanations using saved bars unless explicitly refreshed."""
import streamlit as st

from stockwatch.i18n import t
from stockwatch.portfolio import positions, calculate
from stockwatch.providers.base import Quote, DataUnavailable
from stockwatch.quantitative import build_analysis
from stockwatch.interpretation import interpret_analysis


def display_values(values, text):
    labels = {'close_usd': 'Close', 'sma20': 'SMA20', 'sma50': 'SMA50', 'distance_sma20_pct': 'Distance SMA20',
              'distance_sma50_pct': 'Distance SMA50', 'rsi14': 'RSI14', 'difference': 'MACD difference',
              'delta': 'Adjacent-session delta', 'atr14_usd': 'ATR (USD)', 'atr_price_ratio': 'ATR / price',
              'historical_volatility20_pct': 'Annualized historical volatility', 'largest_weight_fraction': 'Largest weight',
              'top_three_weight_fraction': 'Top three weights', 'hhi': 'HHI', 'effective_equal_weight_count': 'Equivalent equal-weight count'}
    from decimal import Decimal
    cells = []
    for key, raw in values.items():
        if raw is None:
            value = '—'
        else:
            value = f"{Decimal(raw) * (100 if key.endswith('_fraction') or key == 'atr_price_ratio' or key == 'delta' and 'atr_price_ratio' in values else 1):.3g}"
            if key.endswith('_fraction') or key == 'atr_price_ratio' or key.endswith('_pct'):
                value += '%'
            elif key == 'delta':
                value += ' pp' if 'atr_price_ratio' in values or 'historical_volatility20_pct' in values else ''
        cells.append(text(labels.get(key, key)) + ': ' + value)
    return ' · '.join(cells)


def beginner_view(config, transactions, demo, as_of, loader, refresh=False):
    lang = st.session_state.get('_stockwatch_language', 'en')
    text = lambda message, **values: t(message, lang, **values)
    held = positions(transactions, as_of)
    key = f'_quant_beginner_{demo}'
    state = st.session_state.setdefault(key, {})
    signature = (str(as_of), tuple(sorted(held)), tuple(sorted(config['watchlist'])))
    if state.get('signature') != signature:
        state.clear()
        state.update(signature=signature, histories={})
    online = st.checkbox(text('Use online data for the next portfolio refresh'), key='quant_beginner_online', value=False)
    requested = st.button(text('Refresh portfolio analysis'), key='quant_beginner_refresh') or refresh
    targets = sorted(held) + [s for s in sorted(config['watchlist']) if s not in held][:3]
    for symbol in targets:
        if symbol not in state['histories'] or requested:
            try:
                state['histories'][symbol] = loader(symbol, as_of, demo, not (requested and online))
            except (DataUnavailable, OSError, ValueError):
                state['histories'][symbol] = None
    empty = {'holdings': [{'symbol': s} for s in held], 'complete': False, 'market_value': None}
    # Build once, then derive the exposure from the same dated closing facts.
    analysis = build_analysis(state['histories'], empty, config, as_of)
    quotes = {i['symbol']: Quote(i['symbol'], price=float(i['close_usd']) if i.get('session') == str(as_of) and i.get('close_usd') else None,
                               session=as_of) for i in analysis['instruments']}
    book = calculate(held, quotes)
    if book.get('complete') and book.get('market_value') and book['market_value'] > 0:
        weights = sorted([r['market_value'] / book['market_value'] for r in book['holdings']], reverse=True)
        analysis['portfolio_concentration'] = dict(status='available', valuation_session=str(as_of), price_basis='closing_valuation',
            largest_weight_fraction=str(weights[0]), top_three_weight_fraction=str(sum(weights[:3])), hhi=str(sum(w*w for w in weights)))
    interpretations = interpret_analysis(analysis, lang)
    st.caption(text('Expected daily cutoff: {date}. Each holding shows its actual date and quality.', date=str(as_of)))
    st.subheader(text('Portfolio exposure'))
    exposure = interpretations['exposure']
    st.write(exposure['label'])
    st.write(display_values(exposure['values'], text))
    st.write(exposure['reason'])
    st.caption(exposure['caveat'])
    st.caption(exposure['next_observation'])
    if not held:
        st.info(text('No current holdings. Select Advanced for individual stock analysis.'))
    for symbol in sorted(held):
        item = next(i for i in analysis['instruments'] if i['symbol'] == symbol)
        with st.container(border=True):
            st.subheader(symbol)
            st.caption(text('Data date / source / quality: {date} · {source} · {quality}', date=item['session'] or '—', source=item['source'], quality=text(item['quality'])))
            thesis = config['watchlist'].get(symbol, {}).get('thesis')
            if thesis:
                st.write(text('Thesis') + ': ' + thesis)
            stages = interpretations['instruments'][symbol]
            for title, names in [('Trend', ('trend',)), ('Momentum', ('rsi', 'macd')), ('Volatility', ('atr', 'volatility'))]:
                with st.expander(text(title), expanded=title == 'Trend'):
                    for name in names:
                        stage = stages[name]
                        st.markdown('**' + stage['label'] + '**')
                        st.write(display_values(stage['values'], text))
                        st.write(stage['reason'])
                        st.caption(stage['caveat'])
                        st.caption(text('Next observation') + ': ' + stage['next_observation'])
            st.caption(exposure['caveat'])
            weight = next((r['weight_pct'] for r in book['holdings'] if r['symbol'] == symbol), None)
            st.caption(text('Exposure') + ': ' + (f'{weight:.1f}%' if weight is not None else '—'))
    changed = {e['symbol'] for e in analysis['events'] if e.get('symbol') not in held}
    for symbol in sorted(changed)[:3]:
        with st.expander(text('Changed watchlist stock: {symbol}', symbol=symbol)):
            for stage in interpretations['instruments'][symbol].values():
                st.write(stage['label'])
                st.caption(stage['reason'])
    st.caption(text('Facts and technical observations only. Review weights, your thesis and verified news together; no combined buy/sell score is generated.'))
