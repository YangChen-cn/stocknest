"""Read-only daily candle lab; market loading is injected by the Dashboard."""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from stockwatch.history import period_start, sessions
from stockwatch.i18n import t
from stockwatch.presentation import GREEN, RED, NEUTRAL, PALETTE
from stockwatch.providers.base import DataUnavailable, PERIODS
from stockwatch.storage import ValidationError, ticker
from stockwatch.technical import analyze
from stockwatch.ui_style import plot

OVERLAYS = {'SMA 20': 'sma_20', 'SMA 50': 'sma_50', 'SMA 200': 'sma_200', 'EMA 20': 'ema_20', 'EMA 50': 'ema_50'}
PANELS = {'RSI 14': 'rsi_14', 'MACD (12,26,9)': 'macd', 'ATR 14': 'atr_14'}
NOTES = {
    'SMA': 'SMA averages N completed closes equally. The first value needs N consecutive observations.',
    'EMA': 'EMA weights recent closes more: alpha = 2/(N+1). Its first value is the SMA of N closes; a gap restarts the seed.',
    'RSI': 'RSI (14) uses Wilder averages of gains and losses: alpha = 1/14, seeded by 14 price changes (15 closes). Rising-only = 100, falling-only = 0, flat = 50. The 30/70 lines are references, not trade signals.',
    'MACD': 'MACD = EMA(12) - EMA(26); signal = EMA(9) of MACD; histogram = MACD - signal. SMA-seeded EMAs need 26 closes for the line and 34 for the signal.',
    'Bollinger Bands': 'Bollinger Bands = SMA(20) plus/minus 2 population standard deviations (ddof=0). They describe historical dispersion, not forecast confidence intervals.',
    'ATR': 'ATR (14) smooths true range with Wilder alpha = 1/14. True range is max(high-low, abs(high-previous close), abs(low-previous close)). A new block starts with high-low and needs 14 complete bars. Units: USD, not percent.',
}


def technical_figure(frame, symbol, overlays, panels, *, volume=True, bands=False, lang='en'):
    """Only render supplied, already-calculated data. NaN remains a visible gap."""
    text = lambda message: t(message, lang)
    titles = [symbol] + ([text('Volume (shares)')] if volume else []) + list(panels)
    heights = [.56] + ([.14] if volume else []) + [.20] * len(panels)
    fig = make_subplots(rows=len(titles), cols=1, shared_xaxes=True, vertical_spacing=.035,
                        row_heights=heights, subplot_titles=titles)
    complete = frame[['open', 'high', 'low', 'close']].notna().all(axis=1)
    if complete.any():
        fig.add_trace(go.Candlestick(x=frame.date, open=frame.open.where(complete), high=frame.high.where(complete),
                                    low=frame.low.where(complete), close=frame.close.where(complete), name=text('Daily candles'),
                                    increasing_line_color=GREEN, decreasing_line_color=RED), row=1, col=1)
    else:
        fig.add_trace(go.Scatter(x=frame.date, y=frame.close, name=text('Close only'), connectgaps=False,
                                 line=dict(color=GREEN)), row=1, col=1)
    for i, name in enumerate(overlays):
        fig.add_trace(go.Scatter(x=frame.date, y=frame[OVERLAYS[name]], name=name, connectgaps=False,
                                 line=dict(color=PALETTE[(i + 1) % len(PALETTE)], width=1.5)), row=1, col=1)
    if bands:
        for column, label in [('bb_upper', 'BB upper'), ('bb_lower', 'BB lower'), ('bb_middle', 'BB middle')]:
            fig.add_trace(go.Scatter(x=frame.date, y=frame[column], name=text(label), connectgaps=False,
                                     line=dict(color='#b59c76', width=1, dash='dot')), row=1, col=1)
    row = 2
    if volume:
        colors = [NEUTRAL if pd.isna(o) or pd.isna(c) else GREEN if c >= o else RED for o, c in zip(frame.open, frame.close)]
        fig.add_trace(go.Bar(x=frame.date, y=frame.volume, name=text('Volume (shares)'), marker_color=colors), row=row, col=1)
        row += 1
    for name in panels:
        if name.startswith('MACD'):
            for column, color in [('macd', GREEN), ('macd_signal', '#b59c76')]:
                fig.add_trace(go.Scatter(x=frame.date, y=frame[column], name=column, connectgaps=False,
                                         line=dict(color=color)), row=row, col=1)
            fig.add_trace(go.Bar(x=frame.date, y=frame.macd_histogram, name=text('MACD histogram'),
                                marker_color=[GREEN if v >= 0 else RED for v in frame.macd_histogram]), row=row, col=1)
        else:
            fig.add_trace(go.Scatter(x=frame.date, y=frame[PANELS[name]], name=name, connectgaps=False,
                                     line=dict(color=GREEN)), row=row, col=1)
            if name.startswith('RSI'):
                fig.update_yaxes(range=[0, 100], row=row, col=1)
                for level in (30, 70):
                    fig.add_hline(y=level, line_dash='dot', line_color=NEUTRAL, row=row, col=1)
        row += 1
    fig.update_yaxes(title_text='USD', row=1, col=1)
    fig.update_layout(height=430 + 140 * (len(titles) - 1), xaxis_rangeslider_visible=False,
                      legend=dict(orientation='h', y=-.12), hovermode='x unified', margin=dict(t=45, b=95))
    # Hide exchange closures only; missing trading sessions remain visible gaps.
    if not frame.empty:
        calendar = set(pd.date_range(frame.date.min(), frame.date.max()).date)
        closed = sorted(calendar - set(sessions(frame.date.min(), frame.date.max())))
        fig.update_xaxes(rangebreaks=[dict(values=[str(day) for day in closed])])
    return fig


def quant_lab_page(config, transactions, demo, as_of, loader, *, refresh=False):
    lang = st.session_state.get('_stockwatch_language', 'en')
    text = lambda message, **values: t(message, lang, **values)
    st.caption(text('Completed daily bars · research workspace · no orders, alerts or report changes'))
    symbols = sorted({tx.symbol for tx in transactions} | set(config['watchlist']) |
                     {config['portfolio'].get('benchmark', 'SPYM')})
    with st.form('quant_request'):
        left, right = st.columns(2)
        symbol = left.selectbox(text('Stock / ETF'), symbols, index=None, placeholder=text('Select a stock'), key='quant_symbol')
        custom = right.text_input(text('Or enter a ticker'), max_chars=20, key='quant_custom')
        saved_only = st.selectbox(text('Daily bar source'), [True, False],
                                  format_func=lambda saved: text('Saved snapshot (offline)' if saved else 'Online / local market cache'), key='quant_source')
        load = st.form_submit_button(text('Load daily bars'))
    key = f'_quant_loaded_{demo}'
    if load or refresh and key in st.session_state:
        try:
            # Header refresh reloads the dataset currently shown, not unsubmitted form input.
            previous = st.session_state.get(key)
            chosen = ticker(custom.strip() or symbol or '') if load else previous['symbol']
            offline = saved_only if load else previous['offline']
            with st.spinner(text('Loading daily bars…')):
                frame = loader(chosen, as_of, demo, offline)
            st.session_state[key] = dict(symbol=chosen, offline=offline, frame=frame)
        except (DataUnavailable, ValidationError, OSError, ValueError):
            st.warning(text('Daily bars unavailable or invalid. Check the ticker or source; the previous chart, if any, is retained.'))
    loaded = st.session_state.get(key)
    if loaded is None:
        st.info(text('Choose a ticker and load daily bars. Saved snapshots work offline; online loading is explicit. Indicator switches never fetch prices.'))
    else:
        frame = loaded['frame']
        meta = frame.attrs
        st.subheader(text('Daily analysis: {symbol}', symbol=loaded['symbol']))
        st.caption(text('Market source: {source} · fetched: {time} · snapshot saved: {saved}',
                        source=meta.get('source') or text('Unknown'), time=meta.get('fetched_at') or '—',
                        saved=meta.get('generated_at') or '—'))
        try:
            result = analyze(frame, as_of)
        except DataUnavailable:
            result = pd.DataFrame()
        if result.empty or not result.close.notna().any():
            st.warning(text('No usable completed daily bars for this ticker. No zero prices or synthetic candles are substituted.'))
        else:
            valid_days = result.loc[result.close.notna(), 'date']
            st.caption(text('Daily data: {start} — {end} · {count} closes · expected through {cutoff}',
                            start=str(valid_days.min()), end=str(valid_days.max()), count=len(valid_days), cutoff=str(as_of)))
            if valid_days.max() < as_of:
                st.warning(text('History is stale; the latest available bar is {date}. It is not today’s close.', date=str(valid_days.max())))
            missing = int(result.close.isna().sum())
            if missing:
                st.warning(text('{count} trading sessions have missing closes. Gaps are not filled; indicators restart their warm-up.', count=missing))
            if meta.get('adjustment') == 'split_adjusted_no_dividends':
                st.caption(text('Provider prices are split-adjusted, without dividend adjustment. Split events remain visible in the data table.'))
            else:
                st.warning(text('Price adjustment is unknown or legacy. Corporate actions may affect indicator comparability; no adjustment is guessed.'))
            controls = st.columns(2)
            period = controls[0].selectbox(text('Analysis range'), PERIODS, index=2, key='quant_period')
            overlays = controls[1].multiselect(text('Moving averages'), list(OVERLAYS), default=['SMA 20', 'SMA 50'], key='quant_averages')
            panels = controls[0].multiselect(text('Indicator panels'), list(PANELS), default=['RSI 14'], key='quant_panels')
            volume = controls[1].checkbox(text('Show volume'), value=True, key='quant_volume')
            bands = controls[1].checkbox(text('Bollinger Bands (20, 2σ)'), key='quant_bands')
            start = period_start(as_of, period)
            visible = result[result.date >= start].copy()
            if visible.empty:
                st.warning(text('No bars in the selected range. Choose a longer range or load newer data.'))
            else:
                incomplete = visible[['open', 'high', 'low', 'close']].isna().any(axis=1)
                if incomplete.any():
                    st.info(text('Some candles lack OHLC fields (including v1 snapshots). Incomplete candles are omitted; close-only history remains available in the table.'))
                if volume and visible.volume.isna().any():
                    st.info(text('Volume is missing for some sessions; the volume panel leaves those bars empty.'))
                selected = [OVERLAYS[name] for name in overlays] + [PANELS[name] for name in panels]
                if bands:
                    selected.append('bb_upper')
                unavailable = [name for name in selected if visible[name].isna().all()]
                if unavailable:
                    st.info(text('Not enough consecutive history for: {indicators}. Warm-up values remain blank.', indicators=', '.join(unavailable)))
                plot(technical_figure(visible, loaded['symbol'], overlays, panels, volume=volume, bands=bands,
                                      lang=st.session_state.get('_stockwatch_language', 'en')), key='quant_chart')
                st.caption(text('Indicators use the full loaded prefix before the visible range. Only completed NYSE sessions count; missing sessions reset smoothers. Limited snapshot history can leave a long warm-up.'))
                with st.expander(text('Daily bars and indicator values')):
                    st.dataframe(visible.rename(columns={name: text(name.title()) for name in ('date', 'open', 'high', 'low', 'close', 'volume', 'split_ratio')}),
                                 hide_index=True, width='stretch')
    with st.expander(text('How these indicators are calculated'), expanded=loaded is None):
        for name, explanation in NOTES.items():
            st.markdown(f'**{name}** · {text(explanation)}')
        st.caption(text('These are descriptive calculations for learning. This page does not generate trading decisions.'))
