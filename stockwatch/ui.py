"""Localized Streamlit dashboard. No OpenBB imports or notification state writes."""
import os
from datetime import date, datetime, timezone
from html import escape
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.io.formats.style import Styler
import plotly.express as px
import streamlit as st

from stockwatch.ui_reports import report_controls
from stockwatch.ui_control import control_center as render_control_center, gmail_controls as render_gmail_controls
from stockwatch.imports.gmail import sync_hsbc
from stockwatch.imports.hsbc import HSBCSyncError
from stockwatch.calendar import current_price_session, latest_session, active_session
from stockwatch.history import period_start
from stockwatch.performance import DEFAULT_BENCHMARK, build_history, load_history, fingerprint, milestone_status, period_returns
from stockwatch.control import (ControlError, trigger_workflow)
from stockwatch.git_sync import SyncError, sync
from stockwatch.i18n import LANGUAGES, data_status, error_message, language, t
from stockwatch.notifications.email import configuration_status
from stockwatch.providers.base import DataUnavailable, PERIODS
from stockwatch.providers.cached import ClosedSessionProvider, clear_market_cache
from stockwatch.providers.hosted import HostedProvider
from stockwatch.providers.demo import DemoProvider
from stockwatch.providers.openbb_provider import OpenBBProvider
from stockwatch.reports import money as format_money, percent as format_percent
from stockwatch.report_data import report_data, serialize_data
from stockwatch.services import candidate_rows, search_instruments, snapshot
from stockwatch.storage import (COLUMNS, ROOT, WATCH_STATUSES, ValidationError, load_config, load_transactions,
                                save_config, save_transactions, validate_transactions, ensure_user_files)

from stockwatch.presentation import GREEN, PALETTE, reflection, record_note, value_color
from stockwatch.ui_style import apply_style, plot

PAGES = ("Dashboard", "Performance", "Holdings", "Transactions", "Watchlist & Alerts", "Settings")

def text(message: str, **values) -> str:
    return t(message, st.session_state.get("_stockwatch_language", "en"), **values)


def money(value, **options) -> str:
    return format_money(value, lang=st.session_state.get("_stockwatch_language", "en"), **options)


def percent(value, **options) -> str:
    return format_percent(value, lang=st.session_state.get("_stockwatch_language", "en"), **options)


def localized_error(error: Exception) -> str:
    return error_message(error, st.session_state.get("_stockwatch_language", "en"))


def cloud_readonly() -> bool:
    """Hosted deployments (Streamlit Community Cloud) render the synced repository read-only."""
    return os.environ.get("STOCKWATCH_READONLY") == "1"


def ui_provider(demo):
    if demo:
        return DemoProvider()
    if cloud_readonly():
        return HostedProvider(OpenBBProvider(timeout=30, attempts=1, lightweight=True), ROOT / "data/market_snapshot.json")
    provider = OpenBBProvider()
    if active_session() is None:
        return ClosedSessionProvider(provider, ROOT / ".cache/market", latest_session())
    return provider


def market_cache(function):
    # Session and market state are part of the memory key, including at open/close.
    @st.cache_data(ttl=300, show_spinner=False)
    def cached(args, kwargs, market_session, function_name):
        return function(*args, **kwargs)

    @wraps(function)
    def wrapped(*args, **kwargs):
        active = active_session()
        key = ("open", active) if active else ("closed", latest_session())
        if cloud_readonly():
            try:
                stat = (ROOT / "data/market_snapshot.json").stat()
                key += ("hosted", stat.st_mtime_ns, stat.st_size)
            except OSError:
                key += ("hosted", None)
        return cached(args, kwargs, key, function.__name__)

    wrapped.clear = cached.clear
    return wrapped


def ui_snapshot(config, transactions, provider, as_of, demo):
    hosted = cloud_readonly() and not demo
    portfolio, quotes = snapshot(config, transactions, provider, as_of,
                                closing=demo or hosted and active_session() is None,
                                intraday=hosted and active_session() is not None)
    saved = getattr(provider, 'saved', None)
    if hosted and saved is not None:
        # A whole-portfolio fallback uses its own date and ledger cutoff. Never
        # mix an old close into today's P/L or treat absent prices as zero.
        fallback = portfolio['market_value'] is None or quotes and all(q.price is None for q in quotes.values())
        if fallback and saved.session <= as_of:
            old_portfolio, old_quotes = snapshot(config, transactions, saved, saved.session, closing=True)
            if old_portfolio['market_value'] is not None and any(q.price is not None for q in old_quotes.values()):
                portfolio, quotes = old_portfolio, old_quotes
                portfolio['_snapshot_whole'] = True
        if any('saved snapshot' in q.source for q in quotes.values()):
            portfolio['_market_snapshot'] = {key: saved.data[key] for key in ('session', 'mode', 'history_session', 'generated_at')}
    return portfolio, quotes


def snapshot_caption(portfolio, quotes, as_of, demo):
    saved = portfolio.get('_market_snapshot')
    if saved:
        st.warning(text('Saved market snapshot from {time} ({mode}, {date}); these prices are not live. Transactions after that date are excluded when the whole portfolio falls back.',
                        time=saved['generated_at'], mode=saved['mode'], date=saved['session']))
    else:
        st.caption(f"{text('intraday estimate') if not demo and active_session() else text('completed session')} · {as_of} · {text('USD')}")
    if cloud_readonly() and not demo:
        st.caption(text('Online quotes are attempted first (Yahoo via yfinance); saved Actions data is a dated fallback. Refresh retries online data.'))
        sources = sorted({q.source for q in quotes.values() if q.price is not None})
        if sources:
            st.caption(" · ".join(sources))


@market_cache
def cached_snapshot(config: dict, rows: list[dict], as_of, demo: bool):
    provider = ui_provider(demo)
    return ui_snapshot(config, validate_transactions(rows), provider, as_of, demo)


@market_cache
def cached_history(symbol: str, period: str, as_of, demo: bool):
    provider = ui_provider(demo)
    return provider.get_history(symbol, period, as_of)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_search(query: str, demo: bool):
    provider = DemoProvider() if demo else OpenBBProvider()
    return search_instruments(provider, query)


@market_cache
def cached_candidate_history(symbols: tuple[str, ...], as_of, demo: bool):
    provider = ui_provider(demo)
    histories = {}
    for symbol in symbols:
        try:
            histories[symbol] = provider.get_history(symbol, "1M", as_of)
        except Exception:
            # An unavailable candidate must not break the rest of the watchlist.
            histories[symbol] = None
    return histories


@market_cache
def cached_instrument_quote(symbol, demo):
    provider = ui_provider(demo)
    day = DemoProvider().session if demo else current_price_session()
    _, quotes = snapshot({"portfolio": {"base_currency": "USD"}, "watchlist": {symbol: {}}}, [], provider, day, closing=demo)
    return quotes[symbol]


@market_cache
def cached_watchlist_snapshot(config, rows, as_of, demo):
    # Quotes already fetch a year's daily bars. Reuse this provider's range cache.
    provider = ui_provider(demo)
    portfolio, quotes = ui_snapshot(config, validate_transactions(rows), provider, as_of, demo)
    saved = portfolio.get("_market_snapshot") if portfolio.get("_snapshot_whole") else None
    end = date.fromisoformat(saved["history_session"]) if saved else DemoProvider().session if demo else latest_session()
    histories = {}
    for symbol in config["watchlist"]:
        quote = quotes.get(symbol)
        if not quote or quote.error or quote.price is None:
            continue
        try:
            histories[symbol] = provider.get_history(symbol, "1M", end)
        except Exception:
            histories[symbol] = None
    return portfolio, quotes, histories


def symbol_picker(key: str, symbols: list[str], demo: bool) -> str | None:
    query = st.text_input(text("Stock name or ticker"), placeholder=text("e.g. Apple, Tesla, 苹果 or an ETF ticker"), max_chars=100, key=f"{key}_query")
    searched = st.button(text("Search stocks"), key=f"{key}_search_button")
    st.caption(text("Enter a name and press Enter or leave the input to search. Search results replace the previous selection."))
    changed = query.strip() != st.session_state.get(f"{key}_last_query", "")
    if searched or changed:
        st.session_state[f"{key}_last_query"] = query.strip()
        st.session_state[f"{key}_manual"] = False
        try:
            with st.spinner(text("Searching stocks…")):
                results = cached_search(query, demo) if query.strip() else []
            st.session_state[f"{key}_results"] = results
            st.session_state[f"{key}_selection"] = results[0].symbol if results else None
            if not results:
                st.info(text("No US stocks or ETFs found. Try an English name or enter a ticker manually."))
        except DataUnavailable:
            st.session_state[f"{key}_results"] = []
            st.session_state[f"{key}_selection"] = None
            st.warning(text("Stock search is temporarily unavailable. Choose an existing ticker or enter one manually."))
    results = st.session_state.get(f"{key}_results", [])
    searching = bool(query.strip())
    labels = {item.symbol: f"{item.symbol} · {item.name} · {item.exchange} · {text(item.kind)}" for item in results}
    options = list(dict.fromkeys(item.symbol for item in results)) if searching else sorted(set(symbols))
    for symbol in symbols:
        labels.setdefault(symbol, f"{symbol} · {text('In your portfolio or watchlist')}")
    if searching and results:
        st.caption(text("Search results: {count}", count=len(results)))
    if st.checkbox(text("Enter ticker manually"), key=f"{key}_manual"):
        symbol = st.text_input(text("Symbol"), key=f"{key}_ticker", max_chars=20).strip().upper() or None
    else:
        if st.session_state.get(f"{key}_selection") not in options:
            st.session_state[f"{key}_selection"] = None
        symbol = st.selectbox(text("Choose stock / ETF"), options, index=0 if key == "benchmark" and options else None,
                              format_func=labels.__getitem__, placeholder=text("Search above to see options"), key=f"{key}_selection")
    if symbol and key != "benchmark":
        with st.spinner(text("Loading selected stock…")):
            quote = cached_instrument_quote(symbol, demo)
        st.markdown(f"**{symbol}**")
        cols = st.columns(3)
        cols[0].metric(text("Current Price"), money(quote.price))
        cols[1].metric(text("Daily %"), percent(quote.daily_move_pct))
        cols[2].metric(text("52-week range"), f"{money(quote.year_low)} – {money(quote.year_high)}")
        st.caption(text("Quote date: {date}. Latest available regular-session data; not a guaranteed real-time price.", date=str(quote.session or "—")))
        if quote.error:
            st.info(text("Quote unavailable; you can still add this stock using your actual execution details."))
    return symbol


def holdings_table(portfolio: dict) -> pd.DataFrame:
    rows = []
    for row in portfolio["holdings"]:
        rows.append({"Symbol": row["symbol"], "Shares": format(row["shares"].normalize(), "f"), "Avg Cost": money(row["average_cost"]),
                     "Current Price": money(row["price"]), "Daily %": percent(row["daily_pct"]),
                     "Market Value": money(row["market_value"]), "Unrealized P/L": money(row["unrealized_pl"], signed=True),
                     "Return %": percent(row["return_pct"]), "Portfolio Weight": percent(row["weight_pct"], signed=False),
                     "Distance from 52W High": percent(row["high_distance_pct"]),
                     "Distance from 52W Low": percent(row["low_distance_pct"])})
    return pd.DataFrame(rows).rename(columns=text)


def summary(portfolio: dict):
    values = [("Market Value", money(portfolio["market_value"]), None, None),
              ("Daily P/L", money(portfolio["daily_pl"], signed=True),
               percent(portfolio["daily_pct"]) if portfolio["daily_pct"] is not None else None, portfolio["daily_pl"]),
              ("Unrealized P/L", money(portfolio["unrealized_pl"], signed=True), None, portfolio["unrealized_pl"]),
              ("Holding unrealized return", percent(portfolio["return_pct"]), None, portfolio["return_pct"]),
              ("Total Cost", money(portfolio["total_cost"]), None, None)]
    with st.container(key="summary_metrics"):
        for i, (column, (label, value, delta, numeric)) in enumerate(zip(st.columns(5), values)):
            with column, st.container(key=f"summary_{i}"):
                st.metric(text(label), value, delta, delta_color="off" if not numeric else "normal")
                if numeric is not None:
                    st.html(f"<style>.st-key-summary_{i} [data-testid='stMetricValue'],.st-key-summary_{i} [data-testid='stMetricDelta']{{color:{value_color(numeric)};}}</style>")


def milestone_caption(portfolio: dict, transactions, config: dict, demo: bool, as_of):
    """Display-only context from existing records; no extra live history request."""
    first = min((tx.date for tx in transactions if tx.side == "BUY" and tx.date <= as_of), default=None)
    if first:
        st.caption(text("Your record began on {date}.", date=first.isoformat()))
    benchmark = config["portfolio"].get("benchmark", DEFAULT_BENCHMARK)
    try:
        if demo:
            history = cached_performance_preview([tx.row() for tx in transactions], benchmark, DemoProvider().session, True, None)
        else:
            history = load_history(ROOT / "data/performance.json")
    except (ValidationError, OSError):
        return
    if not history or history["fingerprint"] != fingerprint(transactions, benchmark):
        return
    estimated = portfolio["daily_pct"] if not demo and active_session() == as_of else None
    status = milestone_status(history, estimated_return_pct=estimated, as_of=as_of)
    if not portfolio.get("complete") or status is None or not status["new_high"]:
        return
    message = "Portfolio NAV is above its previous high (intraday estimate, {date})." if status["basis"] == "intraday" else "Portfolio NAV reached a new high at the {date} close."
    st.markdown(f'<div class="sw-milestone">↗ {escape(text(message, date=status["as_of"]))}</div>', unsafe_allow_html=True)


def tint_gains_losses(frame: pd.DataFrame) -> Styler:
    """Green/red text for signed money and percent strings; unavailable cells stay neutral."""
    def tint(value):
        if isinstance(value, str) and value.startswith(("-", "+")):
            return f"color: {value_color(value.replace('$', '').replace('%', '').replace(',', ''))}"
        return None
    columns = [text(name) for name in ("Daily %", "Return %", "Unrealized P/L", "5D %", "1M %") if text(name) in frame.columns]
    return frame.style.map(tint, subset=columns)


def chart_prices(symbols: list[str], as_of, demo: bool, key="holdings", optional=False):
    if not symbols:
        return
    left, right = st.columns([3, 2])
    symbol = left.selectbox(text("Watchlist stock" if optional else "Price history"), symbols, index=None if optional else 0,
                            placeholder=text("Select a watched stock to load its chart"), key=f"{key}_history_symbol")
    period_labels = {value: text(value) for value in PERIODS}
    period = right.selectbox(text("Period"), PERIODS, index=1, format_func=period_labels.__getitem__, key=f"{key}_history_period")
    if symbol is None:
        st.caption(text("Select a watched stock to load its chart"))
        return
    try:
        with st.spinner(text("Loading daily prices…")):
            history = cached_history(symbol, period, as_of, demo)
        figure = px.line(history, x="date", y="close", title=f"{symbol} · {text(period)}", labels={"date": text("Date"), "close": text("Current Price")})
        figure.update_layout(xaxis_title=None, yaxis_title=text("USD"), height=310, margin=dict(l=10, r=10, t=45, b=10))
        figure.update_traces(line_color=GREEN, connectgaps=False)
        plot(figure, key=f"{key}_price_chart")
    except (DataUnavailable, ValueError):
        st.info(text("Data unavailable: price history could not be loaded."))


def transactions_page(path: Path, transactions, config: dict, demo: bool):
    with st.expander(text("Add a transaction"), expanded=False):
        symbol = symbol_picker("trade", sorted({tx.symbol for tx in transactions} | set(config["watchlist"])), demo)
        if symbol:
            st.caption(text("Recording transaction for {symbol}. Enter the actual fill price; the quote above is for reference only.", symbol=symbol))
            if symbol not in config["watchlist"] and st.button(text("Add selected stock to watchlist"), disabled=demo or cloud_readonly()):
                try:
                    updated = load_config(ROOT / "config.yaml")
                    updated["watchlist"].setdefault(symbol, {"thesis": "", "status": "watching"})
                    save_config(ROOT / "config.yaml", updated)
                    cached_snapshot.clear()
                    st.session_state["_stockwatch_notice"] = "Watchlist saved locally. Sync with GitHub to update daily alerts."
                    st.rerun()
                except (ValidationError, OSError) as exc:
                    st.error(localized_error(exc) if isinstance(exc, ValidationError) else text("Save failed; original file preserved."))
        with st.form("add_transaction_form"):
            left, right = st.columns(2)
            day = left.date_input(text("Date"), value=datetime.now(ZoneInfo("America/New_York")).date())
            side_labels = {"BUY": text("Buy"), "SELL": text("Sell")}
            side = right.selectbox(text("Transaction direction"), list(side_labels), format_func=side_labels.__getitem__)
            shares = left.number_input(text("Shares"), min_value=0.000001, value=1.0, step=1.0, format="%.6f")
            price = right.number_input(text("Execution price (USD)"), min_value=0.0, value=0.0, step=0.01, format="%.4f", key=f"execution_price_{symbol}")
            fee = st.number_input(text("Fee (USD)"), min_value=0.0, value=0.0, step=0.01, key=f"trade_fee_{symbol}")
            st.caption(text("Trade dates use New York market dates. Fees default to zero."))
            note = st.text_input(text("Note"))
            added = st.form_submit_button(text("Add transaction"), disabled=demo or cloud_readonly() or not symbol)
        if added:
            try:
                # Reload before appending, so existing edits made in another tab survive.
                rows = [tx.row() for tx in load_transactions(path)]
                rows.append({"date": day.isoformat(), "symbol": symbol, "side": side, "shares": str(shares), "price": str(price), "note": note, "fee": str(fee)})
                save_transactions(path, rows)
                cached_snapshot.clear()
                st.session_state["_stockwatch_notice"] = "Transactions saved locally. Use Settings → Sync with GitHub to update daily reports."
                st.rerun()
            except (ValidationError, OSError) as exc:
                st.error(localized_error(exc) if isinstance(exc, ValidationError) else text("Save failed; original file preserved."))
    with st.expander(text("Edit transaction history"), expanded=False):
        st.caption(text("Shares and prices are saved as decimal text. Same-day transactions follow row order; future trades are excluded until their date."))
        frame = pd.DataFrame([tx.row() for tx in transactions], columns=COLUMNS)
        frame["date"] = pd.to_datetime(frame["date"]).dt.date
        columns = {column: st.column_config.TextColumn(text(column.title()), required=column != "note") for column in COLUMNS}
        columns["date"] = st.column_config.DateColumn(text("Date"), format="YYYY-MM-DD", required=True)
        columns["side"] = st.column_config.SelectboxColumn(text("Side"), options=["BUY", "SELL"], required=True)
        with st.form("transactions_form"):
            edited = st.data_editor(frame, num_rows="dynamic", hide_index=True, width="stretch",
                                    column_config=columns,
                                    key=f"transactions_editor_{demo}")
            submitted = st.form_submit_button(text("Save transactions"), disabled=demo or cloud_readonly())
        if demo:
            st.info(text("Demo files are read-only. Turn off Demo mode to edit your real portfolio."))
        if submitted:
            try:
                save_transactions(path, edited.fillna("").to_dict("records"))
                cached_snapshot.clear()
                st.session_state["_stockwatch_notice"] = "Transactions saved locally. Use Settings → Sync with GitHub to update daily reports."
                st.rerun()
            except (ValidationError, OSError) as exc:
                st.error(localized_error(exc) if isinstance(exc, ValidationError) else text("Save failed; original file preserved."))


def watchlist_page(path: Path, config: dict, quotes: dict, demo: bool, portfolio: dict, as_of, histories=None):
    st.subheader(text("Candidate pool"))
    if config["watchlist"]:
        symbols = tuple(symbol for symbol in config["watchlist"] if quotes.get(symbol) and quotes[symbol].price is not None and not quotes[symbol].error)
        if histories is None:
            with st.spinner(text("Loading candidate history…")):
                histories = cached_candidate_history(symbols, as_of, demo)
        rows = candidate_rows(config, {row["symbol"] for row in portfolio["holdings"]}, quotes, histories, as_of)
        for is_held, label in ((False, "Unheld candidates"), (True, "Watched stocks you hold")):
            selected = []
            for row in rows:
                if row["Held"] != is_held:
                    continue
                selected.append({key: "—" if value is None and (key == "Buy Below" or "Buy Below" in key) else text(value) if key == "Status" else money(value) if key in {"Current Price", "Buy Below"}
                                 else percent(value) if key.endswith("%") or key.startswith("Distance") else value
                                 for key, value in row.items() if key != "Held"})
            if selected:
                st.markdown(f"**{text(label)}**")
                st.dataframe(tint_gains_losses(pd.DataFrame(selected).rename(columns=text)), hide_index=True, width="stretch")
        st.caption(text("Historical returns use completed sessions and exact anchors; 5D spans five trading intervals. Positive target distance means above buy_below."))
    else:
        st.info(text("Your candidate pool is empty. Add a stock below; no purchase is made."))
    with st.expander(text("Add or update a watched stock"), expanded=False):
        symbol = symbol_picker("watch", sorted(quotes), demo)
        entry = config["watchlist"].get(symbol, {})
        st.caption(text("Watching needs no target price or alert. Optional fields can stay empty."))
        with st.form("watch_stock_form"):
            thesis = st.text_input(text("Thesis"), value=entry.get("thesis", ""), key=f"thesis_{symbol}")
            status_labels = {value: text(value) for value in WATCH_STATUSES}
            status = st.selectbox(text("Status"), WATCH_STATUSES, index=WATCH_STATUSES.index(entry.get("status", "watching")), format_func=status_labels.__getitem__, key=f"status_{symbol}")
            with st.expander(text("Optional targets & alerts"), expanded=False):
                st.caption(text("Set a number to 0 to leave that optional target or alert unset."))
                buy_below = st.number_input(text("Buy Below"), min_value=0.0, value=float(entry.get("buy_below", 0)), step=0.01, key=f"buy_below_{symbol}")
                target = st.number_input(text("Target Shares"), min_value=0.0, value=float(entry.get("target_shares", 0)), step=1.0, key=f"target_{symbol}")
                left, right = st.columns(2)
                below = left.number_input(text("Below"), min_value=0.0, value=float(entry.get("alerts", {}).get("below", 0)), step=0.01, key=f"below_{symbol}")
                move = right.number_input(text("Daily Move %"), min_value=0.0, value=float(entry.get("alerts", {}).get("daily_move_pct", 0)), step=1.0, key=f"move_{symbol}")
            saved = st.form_submit_button(text("Save this stock"), disabled=demo or cloud_readonly() or not symbol)
        if saved:
            try:
                updated = load_config(path)
                item = {"thesis": thesis, "status": status}
                if buy_below > 0:
                    item["buy_below"] = buy_below
                if target > 0:
                    item["target_shares"] = target
                alerts = {key: value for key, value in (("below", below), ("daily_move_pct", move)) if value > 0}
                if alerts:
                    item["alerts"] = alerts
                updated["watchlist"][symbol] = item
                save_config(path, updated)
                cached_snapshot.clear()
                st.session_state["_stockwatch_notice"] = "Watchlist saved locally. Sync with GitHub to update daily alerts."
                st.rerun()
            except (ValidationError, OSError) as exc:
                st.error(localized_error(exc) if isinstance(exc, ValidationError) else text("Save failed; original file preserved."))
    with st.expander(text("Edit all watchlist entries"), expanded=False):
        records = []
        for symbol, entry in config["watchlist"].items():
            records.append({"Symbol": symbol, "Thesis": entry.get("thesis", ""), "Target Shares": entry.get("target_shares"),
                            "Status": entry.get("status", "watching"), "Buy Below": entry.get("buy_below"),
                            "Below": entry.get("alerts", {}).get("below"), "Daily Move %": entry.get("alerts", {}).get("daily_move_pct")})
        st.caption(text("Below triggers at or below the target; Daily Move % compares the absolute daily change. Email alerts are checked only by the daily job."))
        with st.form("watchlist_form"):
            edited = st.data_editor(pd.DataFrame(records, columns=["Symbol", "Status", "Thesis", "Target Shares", "Buy Below", "Below", "Daily Move %"]),
                                    num_rows="dynamic", hide_index=True, width="stretch",
                                    column_config={"Symbol": st.column_config.TextColumn(text("Symbol"), required=True), "Thesis": st.column_config.TextColumn(text("Thesis")),
                                                   "Status": st.column_config.SelectboxColumn(text("Status"), options=list(WATCH_STATUSES), default="watching"),
                                                   "Buy Below": st.column_config.NumberColumn(text("Buy Below"), min_value=0.01),
                                                   "Target Shares": st.column_config.NumberColumn(text("Target Shares"), min_value=0.0),
                                                   "Below": st.column_config.NumberColumn(text("Below"), min_value=0.01),
                                                   "Daily Move %": st.column_config.NumberColumn(text("Daily Move %"), min_value=0.01)}, key=f"watchlist_editor_{demo}")
            submitted = st.form_submit_button(text("Save watchlist"), disabled=demo or cloud_readonly())
        if submitted:
            try:
                watchlist = {}
                for row in edited.to_dict("records"):
                    symbol = str(row["Symbol"] or "").strip().upper()
                    if symbol in watchlist:
                        raise ValidationError("Duplicate symbol: {symbol}", symbol=symbol)
                    entry = {"thesis": "" if pd.isna(row["Thesis"]) else str(row["Thesis"])}
                    if not pd.isna(row["Status"]):
                        entry["status"] = row["Status"]
                    if not pd.isna(row["Buy Below"]):
                        entry["buy_below"] = row["Buy Below"]
                    if not pd.isna(row["Target Shares"]):
                        entry["target_shares"] = row["Target Shares"]
                    alerts = {}
                    for column, key in (("Below", "below"), ("Daily Move %", "daily_move_pct")):
                        if not pd.isna(row[column]):
                            alerts[key] = row[column]
                    if alerts:
                        entry["alerts"] = alerts
                    watchlist[symbol] = entry
                save_config(path, {**config, "watchlist": watchlist})
                st.session_state["_stockwatch_notice"] = "Watchlist saved locally. Sync with GitHub to update daily alerts."
                cached_snapshot.clear()
                st.rerun()
            except (ValidationError, OSError) as exc:
                st.error(localized_error(exc) if isinstance(exc, ValidationError) else text("Save failed; original file preserved."))
    with st.expander(text("Data availability"), expanded=False):
        for symbol, quote in quotes.items():
            st.caption(text("{symbol}: {price} · {change} today · {source}", symbol=symbol, price=money(quote.price), change=percent(quote.daily_move_pct), source=text(quote.source)) + (" · " + data_status(quote.error, st.session_state["_stockwatch_language"]) if quote.error else ""))


def email_control(path, config, demo):
    enabled = config.get("notifications", {}).get("email_enabled", True)
    toggle_key = f"email_enabled_toggle_{demo}"
    config_key = f"_email_config_{demo}"
    if toggle_key not in st.session_state or st.session_state.get(config_key) != enabled:
        st.session_state[toggle_key] = enabled
        st.session_state[config_key] = enabled
    selected = st.toggle(text("Email notifications"), disabled=demo or cloud_readonly(), key=toggle_key)
    if selected != enabled and not demo and not cloud_readonly():
        try:
            updated = load_config(path)
            updated["notifications"] = {"email_enabled": selected}
            save_config(path, updated)
            st.session_state["_stockwatch_notice"] = "Email setting saved locally. Sync with GitHub to apply it to scheduled reports."
            st.rerun()
        except (ValidationError, OSError) as exc:
            st.error(localized_error(exc))
    st.caption(text("This switch controls sending only; reports and history continue. Sync to apply it in GitHub Actions."))



def gmail_controls(demo):
    render_gmail_controls(demo, ROOT, readonly=cloud_readonly())


def hsbc_control(path, config, demo, *, settings=False):
    options = config.get("imports", {}).get("hsbc", {})
    if settings:
        with st.form("hsbc_settings"):
            enabled = st.checkbox(text("Automatically import completed HSBC trades"), value=options.get("enabled", False))
            allow_date = st.checkbox(text("Use the email New York date when execution date is missing"), value=options.get("allow_email_date", True))
            st.caption(text("Email date is an estimate, not a confirmed execution date. Delayed emails can have the wrong trade date."))
            days = st.number_input(text("HSBC email lookback (days)"), min_value=1, max_value=365, value=options.get("lookback_days", 3))
            saved = st.form_submit_button(text("Save HSBC settings"), disabled=demo or cloud_readonly())
        if saved:
            try:
                updated = load_config(path)
                updated["imports"] = {"hsbc": {"enabled": enabled, "allow_email_date": allow_date, "lookback_days": days}}
                save_config(path, updated)
                st.session_state["_stockwatch_notice"] = "HSBC settings saved. Sync with GitHub to apply them in the cloud."
                st.rerun()
            except (ValidationError, OSError) as exc:
                st.error(localized_error(exc) if not isinstance(exc, OSError) else text("Save failed; original file preserved."))
    manual_days = st.number_input(text("Manual HSBC lookback (days)"), min_value=1, max_value=365, value=options.get("lookback_days", 3), key="hsbc_manual_days")
    st.caption(text("Daily reports check at most three days. This window is for one-time manual history imports."))
    if st.button(text("Sync HSBC trades now"), disabled=demo or cloud_readonly() or not options.get("enabled", False), key="hsbc_now"):
        try:
            present = configuration_status()
            if present["GMAIL_ADDRESS"] and present["GMAIL_APP_PASSWORD"]:
                with st.spinner(text("Reading HSBC confirmations…")):
                    result = sync_hsbc(load_config(path), ROOT / "data/transactions.csv", ROOT / "data/hsbc_imports.json", lookback_days=manual_days)
                cached_snapshot.clear()
                cached_performance_preview.clear()
                st.success(text("HSBC sync: {imported} imported, {duplicates} duplicates, {skipped} skipped", **result))
            else:
                trigger_workflow(ROOT, "CLOSE", dry_run=False, sync_only=True, lookback_days=manual_days)
                st.success(text("Cloud HSBC sync queued; it sends no email. After completion, use GitHub sync to pull the new transactions."))
        except (HSBCSyncError, ControlError, ValidationError, OSError) as exc:
            st.error(localized_error(exc) if not isinstance(exc, OSError) else text("Save failed; original file preserved."))
    st.caption(text("Only completed USD orders are imported. Missing execution dates are skipped unless email-date estimates are enabled. Local settings must be synced before cloud runs."))


def control_center(path, config, demo):
    render_control_center(path, config, demo, ROOT, hsbc_control, email_control, readonly=cloud_readonly())


def settings_page(path: Path, config: dict, demo: bool):
    report_controls(path, config, demo, ROOT, readonly=cloud_readonly())
    control_center(path, config, demo)
    st.selectbox(text("Base currency"), ["USD"], format_func={"USD": text("USD")}.__getitem__)
    selected_language = st.selectbox(text("Language"), list(LANGUAGES), index=list(LANGUAGES).index(st.session_state["_stockwatch_language"]), format_func=LANGUAGES.get)
    st.caption(text("Data provider: OpenBB / yfinance · local files · no brokerage access"))
    with st.expander(text("Advanced: performance benchmark"), expanded=False):
        benchmark = st.text_input(text("Benchmark ticker"), value=config["portfolio"].get("benchmark", DEFAULT_BENCHMARK), max_chars=20).strip().upper()
        st.caption(text("Benchmark: price return, excluding dividends."))
    if st.button(text("Save settings"), disabled=demo or cloud_readonly()):
        try:
            save_config(path, {**config, "portfolio": {**config["portfolio"], "language": selected_language, "benchmark": benchmark or config["portfolio"].get("benchmark", DEFAULT_BENCHMARK)}})
            st.session_state["_stockwatch_notice"] = "Settings saved locally."
            st.rerun()
        except (ValidationError, OSError) as exc:
            st.error(localized_error(exc) if isinstance(exc, ValidationError) else text("Save failed; original file preserved."))
    gmail_controls(demo)
    st.subheader(text("GitHub sync"))
    st.write(text("Sync commits transactions and config, pulls remote updates including alert state, and pushes to main."))
    if st.button(text("Sync with GitHub"), disabled=demo or cloud_readonly()):
        try:
            with st.spinner(text("Syncing with GitHub…")):
                result = sync(ROOT)
            cached_snapshot.clear()
            st.success(text(result))
        except (SyncError, ValidationError) as exc:
            st.error(localized_error(exc))
    st.caption(text("This button uses your local Git login. Conflicting changes are preserved for manual resolution."))


@st.cache_data(ttl=300, show_spinner=False)
def cached_performance_preview(rows, benchmark, end, demo, existing):
    provider = ui_provider(demo)
    return build_history(validate_transactions(rows), provider, end, benchmark, existing=existing)


def performance_page(config, transactions, demo):
    end = DemoProvider().session if demo else latest_session()
    benchmark = config["portfolio"].get("benchmark", DEFAULT_BENCHMARK)
    st.caption(text("Holdings-only, excluding dividends and cash. Daily returns assume buys at day start and sells at day end."))
    history = None
    try:
        if not demo:
            history = load_history(ROOT / "data/performance.json")
    except (ValidationError, OSError) as exc:
        st.warning(localized_error(exc))
    stale = not history or history["fingerprint"] != fingerprint(transactions, benchmark) or not history["points"] or history["points"][-1]["date"] != end.isoformat()
    if stale and not demo:
        st.info(text("History is missing or stale. Recalculate a local preview or wait for the closing workflow."))
    refresh = st.button(text("Recalculate local preview"))
    key = (fingerprint(transactions, benchmark), end.isoformat(), demo)
    if refresh or demo:
        try:
            with st.spinner(text("Calculating historical performance…")):
                history = cached_performance_preview([tx.row() for tx in transactions], benchmark, end, demo, history)
            st.session_state["_performance_preview"] = (key, history)
            stale = False
        except (ValidationError, OSError) as exc:
            st.error(localized_error(exc))
    preview = st.session_state.get("_performance_preview")
    if preview and preview[0] == key:
        history, stale = preview[1], False
    if stale or not history or len(history["points"]) < 2:
        st.info(text("No complete performance history yet. Original history and user files are preserved."))
        return
    st.caption(text("History through {date}; excludes dividends and cash.", date=history["points"][-1]["date"]))
    if preview and preview[0] == key:
        st.caption(text("Local preview only; no Git-tracked history or alert state was modified."))
    status = milestone_status(history, as_of=end)
    if status and not status["new_high"]:
        st.caption(text("Portfolio NAV is {value} from the all-time high of {date}.", value=percent(status["drawdown_pct"], signed=False), date=status["peak_date"]))
    if note := record_note(history, st.session_state["_stockwatch_language"]):
        st.caption(note)
    labels = {value: text("Since inception") if value == "ALL" else text(value) for value in ["ALL", *PERIODS]}
    period = st.selectbox(text("Period"), ["ALL", *PERIODS], format_func=labels.__getitem__, key="performance_period")
    returns = period_returns(history, period)
    cols = st.columns(3)
    for column, label, value in zip(cols, ("Holding performance", "Benchmark", "Excess return (pp)"), (returns["portfolio"], returns["benchmark"], returns["excess"])):
        column.metric(text(label), percent(value) if label != "Excess return (pp)" or value is None else f"{value:+.2f} pp")
    last = history["points"][-1]
    unrealized = last["market_value"] - last["cost"] if last["market_value"] is not None else None
    total = unrealized + last["realized_pl"] if unrealized is not None else None
    for column, label, value in zip(st.columns(4), ("Realized P/L", "Unrealized P/L", "Cumulative fees", "Cumulative P/L"), (last["realized_pl"], unrealized, last["fees"], total)):
        column.metric(text(label), money(value, signed=label != "Cumulative fees"))
    frame = pd.DataFrame(history["points"])
    boundary = frame.iloc[0]["date"] if period == "ALL" else period_start(end, period).isoformat()
    window = frame[frame.date >= boundary].copy()
    value_chart = px.line(window, x="date", y="market_value", title=text("Holding market value"), labels={"date": text("Date"), "market_value": text("USD")})
    value_chart.update_xaxes(tickformat="%Y-%m-%d")
    plot(value_chart)
    # Rebase both series to the selected common anchor; never bridge gaps.
    anchor = frame[frame.date == boundary]
    if not anchor.empty:
        for field in ("nav", "benchmark_nav"):
            first = anchor.iloc[0][field]
            window[field] = window[field] / first * 100 if pd.notna(first) and first > 0 else float("nan")
    window = window.rename(columns={"nav": text("Holding performance"), "benchmark_nav": benchmark})
    figure = px.line(window, x="date", y=[text("Holding performance"), benchmark], title=text("Performance index (base 100)"), labels={"date": text("Date"), "value": text("Index"), "variable": text("Series")})
    figure.update_xaxes(tickformat="%Y-%m-%d")
    figure.update_traces(connectgaps=False)
    plot(figure)
    issues = sorted({error for point in history["points"] for error in point["errors"]})
    if issues:
        st.warning(text("Data gaps or stock splits prevent a continuous return calculation."))
        with st.expander(text("Data availability")):
            for error in issues:
                symbol = error.split(":", 1)[0]
                st.write(f"{symbol}: " + text("Stock split requires ledger reconciliation." if "split" in error else "Historical prices unavailable."))


def ai_export(config, transactions, portfolio, quotes, as_of, demo, watchlist_rows=None, *, compact=False):
    """Download the visible snapshot without sending, fetching or updating state."""
    now = datetime.now(timezone.utc)
    completed = DemoProvider().session if demo else latest_session(now)
    mode = portfolio.get("_market_snapshot", {}).get("mode") if portfolio.get("_snapshot_whole") else "CLOSE" if as_of <= completed else "INTRADAY"
    history = None
    history_status = "not_loaded" if demo else "missing"
    if not demo:
        try:
            history = load_history(ROOT / "data/performance.json")
            if history:
                benchmark = config["portfolio"].get("benchmark", DEFAULT_BENCHMARK)
                if history["fingerprint"] != fingerprint(transactions, benchmark):
                    history, history_status = None, "transactions_or_benchmark_changed"
                elif not history["points"] or history["points"][-1]["date"] > completed.isoformat():
                    history, history_status = None, "invalid_cutoff"
                else:
                    history_status = "current" if history["points"][-1]["date"] == completed.isoformat() else "outdated"
        except (ValidationError, OSError):
            history, history_status = None, "unavailable"
    data = report_data(as_of, portfolio, quotes, config, [], mode=mode, generated_at=now,
                       demo=demo, performance=history, watchlist_rows=watchlist_rows)
    data.update(origin="dashboard_export", price_basis="dashboard_snapshot_may_be_cached",
                alert_evaluation="not_evaluated", performance_status=history_status)
    if saved := portfolio.get("_market_snapshot"):
        data.update(saved_market_snapshot=saved, price_basis="saved_snapshot_or_online_quotes_with_per_instrument_source")
    for item in data["instruments"]:
        if item["symbol"] not in quotes:
            item["data_status"] = "not_loaded"
    data["conventions"]["alerts"] = "Configured thresholds only; this export does not evaluate or consume alerts"
    data["data_quality"]["not_loaded_symbols"] = [item["symbol"] for item in data["instruments"] if item["data_status"] == "not_loaded"]
    data["data_quality"]["unavailable_symbols"] = [item["symbol"] for item in data["instruments"] if item["data_status"] == "unavailable"]
    st.download_button(text("Export current AI data (JSON)"), serialize_data(data).encode("utf-8"),
                       file_name=f"stockwatch-{as_of}-{mode.lower()}-snapshot.json", mime="application/json",
                       on_click="ignore", key="ai_snapshot_download")
    if not compact:
        st.caption(text("Exports loaded prices, holdings, optional notes and local history. Unloaded watchlist values are null; alerts are not evaluated. Refresh prices first if needed."))


def main(app_name: str = "StockWatch"):
    st.set_option("client.toolbarMode", "minimal")
    st.set_page_config(page_title=app_name, page_icon="📈", layout="wide")
    apply_style()
    try:
        ensure_user_files(ROOT)
        st.session_state["_stockwatch_language"] = language(load_config(ROOT / "config.yaml"))
    except (ValidationError, OSError):
        st.session_state.setdefault("_stockwatch_language", "zh-CN")
    st.sidebar.title(app_name)
    st.sidebar.caption(text("See the changes. Keep the record."))
    demo = st.sidebar.toggle(text("Demo mode"), value=False)
    page_labels = {value: text(value) for value in PAGES}
    page = st.sidebar.radio(text("Navigation"), PAGES, format_func=page_labels.__getitem__, key="navigation")
    with st.container(key="page_header"):
        header = st.columns([3, 1.25, 2], vertical_alignment="center")
    header[0].title(text(page))
    export_slot = header[2].container()
    if header[1].button(text("Refresh market data")):
        if not demo:
            try:
                clear_market_cache(ROOT / ".cache/market")
            except OSError:
                st.sidebar.warning(text("Could not clear local market cache."))
        cached_candidate_history.clear()
        cached_instrument_quote.clear()
        cached_watchlist_snapshot.clear()
        cached_snapshot.clear()
        cached_history.clear()
        if page == "Performance":
            cached_performance_preview.clear()
        # Instrument-name searches retain their separate cache.
    config_path = ROOT / ("examples/config.yaml" if demo else "config.yaml")
    transactions_path = ROOT / ("examples/transactions.csv" if demo else "data/transactions.csv")
    if notice := st.session_state.pop("_stockwatch_notice", None):
        st.success(text(notice))
    if demo:
        st.warning(text("SIMULATED DEMO DATA · As of Oct 6, 2026 · No emails are sent. Demo files are read-only."))
    if cloud_readonly():
        st.info(text("Cloud read-only view: this deployment shows the portfolio synced to your private repository. Make changes locally, then sync."))
    try:
        config = load_config(config_path)
        transactions = load_transactions(transactions_path)
    except (ValidationError, OSError) as exc:
        st.error(localized_error(exc) if isinstance(exc, ValidationError) else text("Could not read portfolio files. Check the project directory."))
        return
    if page == "Transactions":
        transactions_page(transactions_path, transactions, config, demo)
        page_footer()
        return
    if page == "Settings":
        settings_page(config_path, config, demo)
        page_footer()
        return
    if page == "Performance":
        performance_page(config, transactions, demo)
        page_footer()
        return
    as_of = DemoProvider().session if demo else current_price_session()
    if page == "Watchlist & Alerts":
        with st.spinner(text("Loading portfolio…")):
            portfolio, quotes, histories = cached_watchlist_snapshot(config, [tx.row() for tx in transactions], as_of, demo)
        saved = portfolio.get("_market_snapshot") if portfolio.get("_snapshot_whole") else None
        if saved:
            as_of = date.fromisoformat(saved["session"])
        chart_day = date.fromisoformat(saved["history_session"]) if saved else DemoProvider().session if demo else latest_session()
        snapshot_caption(portfolio, quotes, as_of, demo)
        with export_slot:
            ai_export(config, transactions, portfolio, quotes, as_of, demo,
                      candidate_rows(config, {row["symbol"] for row in portfolio["holdings"]}, quotes, histories, chart_day), compact=True)
        watchlist_page(config_path, config, quotes, demo, portfolio, chart_day, histories)
        page_footer(as_of)
        return
    with st.spinner(text("Loading portfolio…")):
        # Dashboard/Holdings do not display pure watchlist quotes.
        portfolio, quotes = cached_snapshot({**config, "watchlist": {}}, [tx.row() for tx in transactions], as_of, demo)
    saved = portfolio.get("_market_snapshot") if portfolio.get("_snapshot_whole") else None
    if saved:
        as_of = date.fromisoformat(saved["session"])
    snapshot_caption(portfolio, quotes, as_of, demo)
    summary(portfolio)
    if page == "Dashboard":
        if not saved or as_of == current_price_session():
            milestone_caption(portfolio, transactions, config, demo, as_of)
        with export_slot:
            ai_export(config, transactions, portfolio, quotes, as_of, demo, compact=True)
    unavailable = [symbol for symbol, quote in quotes.items() if quote.error or quote.price is None]
    if unavailable:
        st.warning(text("Data unavailable: {symbols}. Missing holdings are not treated as zero.", symbols=", ".join(unavailable)))
    table = holdings_table(portfolio)
    if table.empty:
        st.info(text("Start with your first transaction, or explore with Demo mode."))
        st.button(text("Add your first transaction"), on_click=lambda: st.session_state.update(navigation="Transactions"))
    else:
        st.subheader(text("Current holdings"))
        core = table.drop(columns=[text("Distance from 52W High"), text("Distance from 52W Low")]) if page == "Dashboard" else table
        st.dataframe(tint_gains_losses(core), hide_index=True, width="stretch", height="content")
        if page == "Dashboard":
            with st.expander(text("More holding details"), expanded=False):
                st.dataframe(tint_gains_losses(table), hide_index=True, width="stretch")
                st.caption(text("Daily P/L adjusts for recorded buys, sells and fees; trade-day returns use daily timing assumptions."))
    if page == "Holdings":
        for row in portfolio["holdings"]:
            entry = config["watchlist"].get(row["symbol"], {})
            symbol = row["symbol"]
            with st.expander(text("Holding notes: {symbol}", symbol=symbol), expanded=False):
                with st.form(f"holding_notes_{symbol}"):
                    thesis = st.text_area(text("Thesis"), value=entry.get("thesis", ""), key=f"holding_thesis_{symbol}")
                    st.caption(text("Notes are optional. Saving notes does not create a transaction or require a target price."))
                    saved = st.form_submit_button(text("Save holding notes"), disabled=demo or cloud_readonly())
                if saved:
                    try:
                        updated = load_config(config_path)
                        updated["watchlist"].setdefault(symbol, {"status": "watching"})["thesis"] = thesis
                        save_config(config_path, updated)
                        cached_snapshot.clear()
                        st.session_state["_stockwatch_notice"] = "Holding notes saved locally."
                        st.rerun()
                    except (ValidationError, OSError) as exc:
                        st.error(localized_error(exc) if isinstance(exc, ValidationError) else text("Save failed; original file preserved."))
                if "target_shares" in entry:
                    st.caption(text("Target: {target:g} shares · Held: {shares}", target=entry["target_shares"], shares=row["shares"]))
    market_context = portfolio.get("_market_snapshot") if portfolio.get("_snapshot_whole") else None
    chart_day = date.fromisoformat(market_context["history_session"]) if market_context else DemoProvider().session if demo else latest_session()
    held_symbols = [row["symbol"] for row in portfolio["holdings"]]
    if page == "Dashboard":
        with st.container(key="overview_charts"):
            price_column, allocation_column, returns_column = st.columns([2.4, 1, 1.15])
            with price_column, st.container(border=True):
                st.subheader(text("Price trends"))
                chart_prices(held_symbols, chart_day, demo)
            complete_rows = [row for row in portfolio["holdings"] if row["market_value"] is not None and row["return_pct"] is not None]
            if complete_rows:
                if not portfolio["complete"]:
                    st.caption(text("Charts show priced holdings only; allocation is not the complete portfolio."))
                chart_data = pd.DataFrame([{"Symbol": row["symbol"], "Market Value": float(row["market_value"]),
                                            "Return %": float(row["return_pct"])} for row in complete_rows])
                with allocation_column, st.container(border=True):
                    st.subheader(text("Portfolio allocation"))
                    pie = px.pie(chart_data, names="Symbol", values="Market Value", hole=.72, color_discrete_sequence=PALETTE)
                    pie.update_layout(height=376, margin=dict(l=5,r=5,t=20,b=5), legend=dict(orientation="h",y=-.1,x=0))
                    if portfolio["complete"]:
                        pie.add_annotation(text=money(portfolio["market_value"]), x=.5, y=.5, showarrow=False, font=dict(size=17))
                    pie.update_traces(textinfo="none", hovertemplate="%{label}<br>%{value:$,.2f}<br>%{percent}<extra></extra>")
                    plot(pie, key="allocation_chart")
                with returns_column, st.container(border=True):
                    st.subheader(text("Holding returns"))
                    bars = px.bar(chart_data, x="Symbol", y="Return %", text_auto=".2f", labels={"Return %": text("Return %")})
                    bars.update_traces(marker_color=[value_color(v) for v in chart_data["Return %"]], textposition="outside", cliponaxis=False)
                    bars.update_layout(height=376, xaxis_title=None, yaxis_title=None, margin=dict(l=10,r=5,t=30,b=30))
                    plot(bars, key="holding_returns_chart")
        with st.expander(text("Watchlist price chart"), expanded=False):
            if config["watchlist"]:
                chart_prices(list(config["watchlist"]), chart_day, demo, "dashboard_watchlist", True)
            else:
                st.caption(text("No watched stocks yet. Add them on Watchlist & Alerts."))
        with st.expander(text("Reports & automation"), expanded=False):
            email_control(config_path, config, demo)
            report_controls(config_path, config, demo, ROOT, readonly=cloud_readonly())
            gmail_controls(demo)
            with st.expander(text("HSBC trade import")):
                hsbc_control(config_path, config, demo)
    else:
        chart_prices(held_symbols, chart_day, demo)
    page_footer(as_of)


def page_footer(day=None):
    day = day or latest_session()
    st.markdown(f'<div class="sw-footer">{escape(reflection(day, st.session_state.get("_stockwatch_language", "en")))}</div>', unsafe_allow_html=True)
