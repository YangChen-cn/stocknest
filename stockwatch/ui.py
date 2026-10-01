"""Localized Streamlit dashboard. No OpenBB imports or notification state writes."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import streamlit as st

from stockwatch.imports.gmail import sync_hsbc
from stockwatch.imports.hsbc import HSBCSyncError
from stockwatch.calendar import current_price_session, latest_session
from stockwatch.history import period_start
from stockwatch.performance import DEFAULT_BENCHMARK, build_history, load_history, fingerprint, period_returns
from stockwatch.control import (ControlError, cloud_email_status, workflow_status, set_workflow_enabled, trigger_workflow,
                                service_status, install_service, stop_service, uninstall_service)
from stockwatch.git_sync import SyncError, sync
from stockwatch.i18n import LANGUAGES, data_status, error_message, language, t
from stockwatch.notifications.email import configuration_status
from stockwatch.notifications.local import credentials, load_local, save_local, remove_local
from stockwatch.providers.base import DataUnavailable, PERIODS
from stockwatch.providers.demo import DemoProvider
from stockwatch.providers.openbb_provider import OpenBBProvider
from stockwatch.reports import money as format_money, percent as format_percent
from stockwatch.services import candidate_rows, search_instruments, snapshot
from stockwatch.storage import (COLUMNS, ROOT, WATCH_STATUSES, ValidationError, load_config, load_transactions,
                                save_config, save_transactions, validate_transactions, ensure_user_files)

PAGES = ("Dashboard", "Performance", "Holdings", "Transactions", "Watchlist & Alerts", "Settings")

def text(message: str, **values) -> str:
    return t(message, st.session_state.get("_stockwatch_language", "en"), **values)


def money(value, **options) -> str:
    return format_money(value, lang=st.session_state.get("_stockwatch_language", "en"), **options)


def percent(value, **options) -> str:
    return format_percent(value, lang=st.session_state.get("_stockwatch_language", "en"), **options)


def localized_error(error: Exception) -> str:
    return error_message(error, st.session_state.get("_stockwatch_language", "en"))


@st.cache_data(ttl=300, show_spinner=False)
def cached_snapshot(config: dict, rows: list[dict], as_of, demo: bool):
    provider = DemoProvider() if demo else OpenBBProvider()
    return snapshot(config, validate_transactions(rows), provider, as_of, closing=demo)


@st.cache_data(ttl=300, show_spinner=False)
def cached_history(symbol: str, period: str, as_of, demo: bool):
    provider = DemoProvider() if demo else OpenBBProvider()
    return provider.get_history(symbol, period, as_of)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_search(query: str, demo: bool):
    provider = DemoProvider() if demo else OpenBBProvider()
    return search_instruments(provider, query)


@st.cache_data(ttl=300, show_spinner=False)
def cached_candidate_history(symbols: tuple[str, ...], as_of, demo: bool):
    provider = DemoProvider() if demo else OpenBBProvider()
    histories = {}
    for symbol in symbols:
        try:
            histories[symbol] = provider.get_history(symbol, "1M", as_of)
        except Exception:
            # An unavailable candidate must not break the rest of the watchlist.
            histories[symbol] = None
    return histories


@st.cache_data(ttl=300, show_spinner=False)
def cached_instrument_quote(symbol, demo):
    provider = DemoProvider() if demo else OpenBBProvider()
    day = DemoProvider().session if demo else current_price_session()
    _, quotes = snapshot({"portfolio": {"base_currency": "USD"}, "watchlist": {symbol: {}}}, [], provider, day, closing=demo)
    return quotes[symbol]


@st.cache_data(ttl=300, show_spinner=False)
def cached_watchlist_snapshot(config, rows, as_of, demo):
    # Quotes already fetch a year's daily bars. Reuse this provider's range cache.
    provider = DemoProvider() if demo else OpenBBProvider()
    portfolio, quotes = snapshot(config, validate_transactions(rows), provider, as_of, closing=demo)
    end = DemoProvider().session if demo else latest_session()
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
        rows.append({"Symbol": row["symbol"], "Shares": float(row["shares"]), "Avg Cost": money(row["average_cost"]),
                     "Current Price": money(row["price"]), "Daily %": percent(row["daily_pct"]),
                     "Market Value": money(row["market_value"]), "Unrealized P/L": money(row["unrealized_pl"], signed=True),
                     "Return %": percent(row["return_pct"]), "Portfolio Weight": percent(row["weight_pct"], signed=False),
                     "Distance from 52W High": percent(row["high_distance_pct"]),
                     "Distance from 52W Low": percent(row["low_distance_pct"])})
    return pd.DataFrame(rows).rename(columns=text)


def summary(portfolio: dict):
    columns = st.columns(5)
    values = [("Total Cost", money(portfolio["total_cost"])), ("Market Value", money(portfolio["market_value"])),
              ("Daily P/L", money(portfolio["daily_pl"], signed=True)),
              ("Unrealized P/L", money(portfolio["unrealized_pl"], signed=True)), ("Holding unrealized return", percent(portfolio["return_pct"]))]
    for column, (label, value) in zip(columns, values):
        column.metric(text(label), value)
    st.caption(text("Daily P/L adjusts for recorded buys, sells and fees; trade-day returns use daily timing assumptions."))


def chart_prices(symbols: list[str], as_of, demo: bool):
    if not symbols:
        return
    left, right = st.columns([3, 2])
    symbol = left.selectbox(text("Price history"), symbols)
    period_labels = {value: text(value) for value in PERIODS}
    period = right.selectbox(text("Period"), PERIODS, index=1, format_func=period_labels.__getitem__)
    try:
        with st.spinner(text("Loading daily prices…")):
            history = cached_history(symbol, period, as_of, demo)
        figure = px.line(history, x="date", y="close", title=f"{symbol} · {text(period)}", labels={"date": text("Date"), "close": text("Current Price")})
        figure.update_layout(xaxis_title=None, yaxis_title=text("USD"), margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(figure, width="stretch")
    except (DataUnavailable, ValueError):
        st.info(text("Data unavailable: price history could not be loaded."))


def transactions_page(path: Path, transactions, config: dict, demo: bool):
    st.subheader(text("Add a transaction"))
    symbol = symbol_picker("trade", sorted({tx.symbol for tx in transactions} | set(config["watchlist"])), demo)
    if symbol:
        st.caption(text("Recording transaction for {symbol}. Enter the actual fill price; the quote above is for reference only.", symbol=symbol))
        if symbol not in config["watchlist"] and st.button(text("Add selected stock to watchlist"), disabled=demo):
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
        added = st.form_submit_button(text("Add transaction"), disabled=demo or not symbol)
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
    st.subheader(text("Edit transaction history"))
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
        submitted = st.form_submit_button(text("Save transactions"), disabled=demo)
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
                selected.append({key: text(value) if key == "Status" else money(value) if key in {"Current Price", "Buy Below"}
                                 else percent(value) if key.endswith("%") or key.startswith("Distance") else value
                                 for key, value in row.items() if key != "Held"})
            if selected:
                st.markdown(f"**{text(label)}**")
                st.dataframe(pd.DataFrame(selected).rename(columns=text), hide_index=True, width="stretch")
        st.caption(text("Historical returns use completed sessions and exact anchors; 5D spans five trading intervals. Positive target distance means above buy_below."))
    else:
        st.info(text("Your candidate pool is empty. Add a stock below; no purchase is made."))
    st.subheader(text("Add or update a watched stock"))
    symbol = symbol_picker("watch", sorted(quotes), demo)
    entry = config["watchlist"].get(symbol, {})
    st.caption(text("Watching needs no target price or alert. Optional fields can stay empty."))
    with st.form("watch_stock_form"):
        thesis = st.text_input(text("Thesis"), value=entry.get("thesis", ""), key=f"thesis_{symbol}")
        status_labels = {value: text(value) for value in WATCH_STATUSES}
        status = st.selectbox(text("Status"), WATCH_STATUSES, index=WATCH_STATUSES.index(entry.get("status", "watching")), format_func=status_labels.__getitem__, key=f"status_{symbol}")
        with st.expander(text("Optional targets & alerts"), expanded=bool(entry.get("alerts") or entry.get("buy_below") or entry.get("target_shares"))):
            st.caption(text("Set a number to 0 to leave that optional target or alert unset."))
            buy_below = st.number_input(text("Buy Below"), min_value=0.0, value=float(entry.get("buy_below", 0)), step=0.01, key=f"buy_below_{symbol}")
            target = st.number_input(text("Target Shares"), min_value=0.0, value=float(entry.get("target_shares", 0)), step=1.0, key=f"target_{symbol}")
            left, right = st.columns(2)
            below = left.number_input(text("Below"), min_value=0.0, value=float(entry.get("alerts", {}).get("below", 0)), step=0.01, key=f"below_{symbol}")
            move = right.number_input(text("Daily Move %"), min_value=0.0, value=float(entry.get("alerts", {}).get("daily_move_pct", 0)), step=1.0, key=f"move_{symbol}")
        saved = st.form_submit_button(text("Save this stock"), disabled=demo or not symbol)
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
    st.subheader(text("Edit all watchlist entries"))
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
        submitted = st.form_submit_button(text("Save watchlist"), disabled=demo)
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
    for symbol, quote in quotes.items():
        st.caption(text("{symbol}: {price} · {change} today · {source}", symbol=symbol, price=money(quote.price), change=percent(quote.daily_move_pct), source=text(quote.source)) + (" · " + data_status(quote.error, st.session_state["_stockwatch_language"]) if quote.error else ""))


def email_control(path, config, demo):
    enabled = config.get("notifications", {}).get("email_enabled", True)
    toggle_key = f"email_enabled_toggle_{demo}"
    config_key = f"_email_config_{demo}"
    if st.session_state.get(config_key) != enabled:
        st.session_state[toggle_key] = enabled
        st.session_state[config_key] = enabled
    selected = st.toggle(text("Email notifications"), value=enabled, disabled=demo, key=toggle_key)
    if selected != enabled and not demo:
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
    st.caption(text("Cloud Gmail uses GitHub Actions Secrets. Local Gmail is optional and does not indicate cloud health."))
    if st.button(text("Check cloud Gmail Secrets"), disabled=demo):
        try:
            st.session_state["_cloud_gmail_status"] = cloud_email_status(ROOT)
        except ControlError:
            st.session_state.pop("_cloud_gmail_status", None)
            st.info(text("Cloud Secrets could not be checked. Use GitHub repository settings; local Gmail is independent."))
    cloud = st.session_state.get("_cloud_gmail_status") if not demo else None
    if cloud is not None:
        st.write(text("Cloud Gmail Secrets") + ": " + ", ".join(f"{name}: {text('Configured' if ready else 'Missing')}" for name, ready in cloud.items()))
    with st.expander(text("Advanced: optional local Gmail"), expanded=False):
        st.caption(text("Used only for local email and direct HSBC sync. Saved as a plaintext file readable only by your user, ignored by Git; it never updates cloud Secrets."))
        st.caption(text("Environment variables take precedence. Leave App Password blank to retain the saved local password; it is never displayed."))
        try:
            values = {} if demo else credentials(ROOT)
            local = {} if demo else load_local(ROOT)
            status = {name: bool(value) for name, value in values.items()}
            st.write(text("Local Gmail ready" if status and all(status.values()) else "Local Gmail not configured (optional)"))
            if st.session_state.pop("_clear_local_password", False):
                st.session_state["local_gmail_password"] = ""
            with st.form("local_gmail"):
                address = st.text_input(text("Local Gmail Address"), value=local.get("GMAIL_ADDRESS", values.get("GMAIL_ADDRESS", "")))
                recipient = st.text_input(text("Local Report Email"), value=local.get("REPORT_EMAIL", values.get("REPORT_EMAIL", "")))
                password = st.text_input(text("Local Gmail App Password"), type="password", key="local_gmail_password")
                saved = st.form_submit_button(text("Save local Gmail"), disabled=demo)
            if saved:
                save_local(address, recipient, password, ROOT)
                st.session_state["_clear_local_password"] = True
                st.session_state["_stockwatch_notice"] = "Local Gmail saved. Cloud Secrets were not changed."
                st.rerun()
            if st.button(text("Remove saved local Gmail"), disabled=demo or not local):
                remove_local(ROOT)
                st.session_state["_clear_local_password"] = True
                st.session_state["_stockwatch_notice"] = "Local Gmail removed. Environment variables and cloud Secrets were not changed."
                st.rerun()
        except (ValidationError, OSError) as exc:
            st.error(localized_error(exc) if not isinstance(exc, OSError) else text("Save failed; original file preserved."))

def hsbc_control(path, config, demo, *, settings=False):
    options = config.get("imports", {}).get("hsbc", {})
    if settings:
        with st.form("hsbc_settings"):
            enabled = st.checkbox(text("Automatically import completed HSBC trades"), value=options.get("enabled", False))
            allow_date = st.checkbox(text("Use the email New York date when execution date is missing"), value=options.get("allow_email_date", True))
            st.caption(text("Email date is an estimate, not a confirmed execution date. Delayed emails can have the wrong trade date."))
            days = st.number_input(text("HSBC email lookback (days)"), min_value=1, max_value=365, value=options.get("lookback_days", 3))
            saved = st.form_submit_button(text("Save HSBC settings"), disabled=demo)
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
    if st.button(text("Sync HSBC trades now"), disabled=demo or not options.get("enabled", False), key="hsbc_now"):
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
    st.subheader(text("Control Center"))
    with st.expander(text("HSBC trade import")):
        hsbc_control(path, config, demo, settings=True)
    email_control(path, config, demo)
    if st.button(text("Sync notification setting with GitHub"), disabled=demo):
        try:
            sync(ROOT)
            st.success(text("Configuration synced to GitHub."))
        except (SyncError, ValidationError) as exc:
            st.error(localized_error(exc))
    st.markdown(f"**{text('GitHub Actions')}**")
    if st.button(text("Refresh workflow status"), disabled=demo):
        try:
            st.session_state["_workflow_status"] = workflow_status(ROOT)
        except ControlError as exc:
            st.error(localized_error(exc))
    status = st.session_state.get("_workflow_status") if not demo else None
    if status:
        st.write(f"{status['repository']} · {text(status['state'])}")
        st.link_button(text("Open workflow"), status["url"])
        for run in status["runs"]:
            stamp = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Hong_Kong")).strftime("%m-%d %H:%M HKT")
            label = f"{stamp} · {text(run['status'])} · {text(run['conclusion'] or 'Pending')}"
            st.link_button(label, run["html_url"])
    with st.form("workflow_controls"):
        mode_labels = {"CLOSE": text("Closing report"), "INTRADAY": text("Intraday brief")}
        mode = st.selectbox(text("Report mode"), list(mode_labels), format_func=mode_labels.__getitem__)
        dry_run = st.checkbox(text("Preview only (no email or state changes)"), value=True)
        dispatch = st.form_submit_button(text("Run workflow"), disabled=demo)
    if dispatch:
        try:
            trigger_workflow(ROOT, mode, dry_run=dry_run)
            st.success(text("Workflow queued. Refresh status to see its result."))
        except ControlError as exc:
            st.error(localized_error(exc))
    left, right = st.columns(2)
    enable = left.button(text("Enable daily workflow"), disabled=demo)
    disable = right.button(text("Disable daily workflow"), disabled=demo)
    if enable or disable:
        try:
            set_workflow_enabled(ROOT, enable)
            st.session_state["_workflow_status"] = workflow_status(ROOT)
            st.success(text("Workflow setting updated."))
        except ControlError as exc:
            st.error(localized_error(exc))
    st.caption(text("Uses your existing gh login. Disabling the daily workflow stops both schedules; CI stays enabled."))
    st.markdown(f"**{text('macOS login startup')}**")
    try:
        local = service_status(ROOT)
        st.write(text("Service: {state} · Login startup: {startup} · Port 8501: {port}",
                      state=text("Running" if local["running"] else "Manual Dashboard or other process" if local["port_open"] else "Stopped"),
                      startup=text("Enabled" if local["installed"] else "Disabled"),
                      port=text("Responding" if local["port_open"] else "Closed")))
        if local["supported"]:
            cols = st.columns(3)
            actions = ("Enable login startup", "Stop service", "Disable login startup")
            funcs = (install_service, stop_service, uninstall_service)
            for column, label, function in zip(cols, actions, funcs):
                if column.button(text(label), disabled=demo or label == "Stop service" and not local["loaded"]):
                    function(ROOT)
                    st.success(text("Service setting updated. Refresh the page to see its status."))
        else:
            st.info(text("Login startup is available only on macOS."))
    except ControlError as exc:
        st.error(localized_error(exc))
    st.caption(text("Enabling login startup only registers the next login and leaves this Dashboard running. To switch now, stop the manual terminal process, then run python -m stockwatch.control start in that terminal."))
    st.caption(text("Login startup runs only the local Dashboard on 127.0.0.1:8501, not email jobs. Stopping it disconnects this page; cloud schedules continue."))


def settings_page(path: Path, config: dict, demo: bool):
    control_center(path, config, demo)
    st.selectbox(text("Base currency"), ["USD"], format_func={"USD": text("USD")}.__getitem__)
    selected_language = st.selectbox(text("Language"), list(LANGUAGES), index=list(LANGUAGES).index(st.session_state["_stockwatch_language"]), format_func=LANGUAGES.get)
    st.caption(text("Data provider: OpenBB / yfinance · local files · no brokerage access"))
    benchmark = symbol_picker("benchmark", [config["portfolio"].get("benchmark", DEFAULT_BENCHMARK)], demo)
    st.caption(text("Benchmark: price return, excluding dividends."))
    if st.button(text("Save settings"), disabled=demo):
        try:
            save_config(path, {**config, "portfolio": {**config["portfolio"], "language": selected_language, "benchmark": benchmark or config["portfolio"].get("benchmark", DEFAULT_BENCHMARK)}})
            st.session_state["_stockwatch_notice"] = "Settings saved locally."
            st.rerun()
        except OSError:
            st.error(text("Save failed; original file preserved."))
    gmail_controls(demo)
    st.subheader(text("GitHub sync"))
    st.write(text("Sync commits transactions and config, pulls remote updates including alert state, and pushes to main."))
    if st.button(text("Sync with GitHub"), disabled=demo):
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
    provider = DemoProvider() if demo else OpenBBProvider()
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
    st.plotly_chart(value_chart, width="stretch")
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
    st.plotly_chart(figure, width="stretch")
    issues = sorted({error for point in history["points"] for error in point["errors"]})
    if issues:
        st.warning(text("Data gaps or stock splits prevent a continuous return calculation."))
        with st.expander(text("Data availability")):
            for error in issues:
                symbol = error.split(":", 1)[0]
                st.write(f"{symbol}: " + text("Stock split requires ledger reconciliation." if "split" in error else "Historical prices unavailable."))


def main(app_name: str = "StockWatch"):
    st.set_page_config(page_title=app_name, page_icon="📈", layout="wide")
    try:
        ensure_user_files(ROOT)
        st.session_state["_stockwatch_language"] = language(load_config(ROOT / "config.yaml"))
    except (ValidationError, OSError):
        st.session_state.setdefault("_stockwatch_language", "zh-CN")
    st.sidebar.title(app_name)
    st.sidebar.caption(text("Your portfolio, clearly."))
    demo = st.sidebar.toggle(text("Demo mode"), value=False)
    page_labels = {value: text(value) for value in PAGES}
    page = st.sidebar.radio(text("Navigation"), PAGES, format_func=page_labels.__getitem__, key="navigation")
    if st.sidebar.button(text("Refresh market data")):
        if page == "Watchlist & Alerts":
            cached_watchlist_snapshot.clear()
        elif page == "Performance":
            cached_performance_preview.clear()
        elif page in {"Transactions", "Settings"}:
            cached_instrument_quote.clear()
        else:
            cached_snapshot.clear()
            cached_history.clear()
        # Search names and other pages' data do not need refreshing here.
    config_path = ROOT / ("examples/config.yaml" if demo else "config.yaml")
    transactions_path = ROOT / ("examples/transactions.csv" if demo else "data/transactions.csv")
    st.title(text(page))
    if notice := st.session_state.pop("_stockwatch_notice", None):
        st.success(text(notice))
    if demo:
        st.warning(text("SIMULATED DEMO DATA · As of Oct 6, 2026 · No emails are sent. Demo files are read-only."))
    try:
        config = load_config(config_path)
        transactions = load_transactions(transactions_path)
    except (ValidationError, OSError) as exc:
        st.error(localized_error(exc) if isinstance(exc, ValidationError) else text("Could not read portfolio files. Check the project directory."))
        return
    if page == "Transactions":
        transactions_page(transactions_path, transactions, config, demo)
        return
    if page == "Settings":
        settings_page(config_path, config, demo)
        return
    if page == "Performance":
        performance_page(config, transactions, demo)
        return
    as_of = DemoProvider().session if demo else current_price_session()
    if page == "Watchlist & Alerts":
        with st.spinner(text("Loading portfolio…")):
            portfolio, quotes, histories = cached_watchlist_snapshot(config, [tx.row() for tx in transactions], as_of, demo)
        chart_day = DemoProvider().session if demo else latest_session()
        watchlist_page(config_path, config, quotes, demo, portfolio, chart_day, histories)
        return
    with st.spinner(text("Loading portfolio…")):
        # Dashboard/Holdings do not display pure watchlist quotes.
        portfolio, quotes = cached_snapshot({**config, "watchlist": {}}, [tx.row() for tx in transactions], as_of, demo)
    if page == "Dashboard":
        email_control(config_path, config, demo)
        gmail_controls(demo)
        with st.expander(text("HSBC trade import")):
            hsbc_control(config_path, config, demo)
    summary(portfolio)
    unavailable = [symbol for symbol, quote in quotes.items() if quote.error or quote.price is None]
    if unavailable:
        st.warning(text("Data unavailable: {symbols}. Missing holdings are not treated as zero.", symbols=", ".join(unavailable)))
    table = holdings_table(portfolio)
    if table.empty:
        st.info(text("No current holdings. Add transactions or enable Demo mode to explore StockWatch."))
    else:
        st.dataframe(table, hide_index=True, width="stretch")
    if page == "Holdings":
        for row in portfolio["holdings"]:
            entry = config["watchlist"].get(row["symbol"], {})
            symbol = row["symbol"]
            with st.expander(text("Holding notes: {symbol}", symbol=symbol), expanded=not bool(entry.get("thesis"))):
                with st.form(f"holding_notes_{symbol}"):
                    thesis = st.text_area(text("Thesis"), value=entry.get("thesis", ""), key=f"holding_thesis_{symbol}")
                    st.caption(text("Notes are optional. Saving notes does not create a transaction or require a target price."))
                    saved = st.form_submit_button(text("Save holding notes"), disabled=demo)
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
    else:
        complete_rows = [row for row in portfolio["holdings"] if row["market_value"] is not None]
        if complete_rows:
            if not portfolio["complete"]:
                st.caption(text("Charts show priced holdings only; allocation is not the complete portfolio."))
            chart_data = pd.DataFrame([{"Symbol": row["symbol"], "Market Value": float(row["market_value"]),
                                        "Return %": float(row["return_pct"])} for row in complete_rows])
            left, right = st.columns(2)
            left.plotly_chart(px.pie(chart_data, names="Symbol", values="Market Value", hole=0.65, title=text("Portfolio allocation"), labels={"Symbol": text("Symbol"), "Market Value": text("Market Value")}), width="stretch")
            right.plotly_chart(px.bar(chart_data, x="Symbol", y="Return %", title=text("Holding returns"), labels={"Symbol": text("Symbol"), "Return %": text("Return %")}), width="stretch")
    chart_day = DemoProvider().session if demo else latest_session()
    held_symbols = [row["symbol"] for row in portfolio["holdings"]]
    st.caption(text("Charts below show current holdings. Watch-only stocks are on Watchlist & Alerts."))
    chart_prices(held_symbols, chart_day, demo)
    st.caption(text("Price history ends at the latest completed NYSE session. Prices are cached for up to five minutes."))
