"""Small, versioned factual email payload; no credentials or raw account records."""
import json
from datetime import timezone
from decimal import Decimal, InvalidOperation
from html import escape

from stockwatch.calendar import previous_session
from stockwatch.performance import period_returns
from stockwatch.providers.base import PERIODS, Quote

BEGIN = "STOCKWATCH_DATA_V1_BEGIN"
END = "STOCKWATCH_DATA_V1_END"


def numeric(value):
    """Decimal strings preserve precision; null means unavailable, never zero."""
    if value is None:
        return None
    try:
        number = Decimal(str(value))
        return str(number) if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def report_data(session, portfolio, quotes, config, alerts, *, mode, generated_at,
                demo=False, performance=None, watchlist_rows=None):
    historical_day = previous_session(session) if mode == "INTRADAY" else session
    candidates = {row["Symbol"]: row for row in watchlist_rows or []}
    instruments = []
    held = {row["symbol"] for row in portfolio["holdings"]}
    for symbol in sorted(set(quotes) | set(config["watchlist"]) | held):
        quote = quotes.get(symbol, Quote(symbol))
        entry = config["watchlist"].get(symbol, {})
        row = candidates.get(symbol, {})
        available = not quote.error and quote.price is not None and quote.session == session
        item = {
            "symbol": symbol, "held": symbol in held, "watched": symbol in config["watchlist"],
            "price_usd": numeric(quote.price) if available else None,
            "previous_close_usd": numeric(quote.previous_close),
            "daily_change_pct": numeric(quote.daily_move_pct) if available else None,
            "quote_session": quote.session.isoformat() if quote.session else None,
            "price_at": quote.price_at.isoformat() if quote.price_at else None,
            "fetched_at": quote.fetched_at.isoformat() if quote.fetched_at else None,
            "source": quote.source,
            "data_status": "available" if available and quote.previous_close is not None else "unavailable",
            "year_high_usd": numeric(quote.year_high), "year_low_usd": numeric(quote.year_low),
            "history_as_of": historical_day.isoformat(),
            "return_5d_pct": numeric(row.get("5D %")), "return_1m_pct": numeric(row.get("1M %")),
        }
        if symbol in config["watchlist"]:
            item.update(status=entry.get("status", "watching"), buy_below_usd=numeric(entry.get("buy_below")),
                        target_shares=numeric(entry.get("target_shares")),
                        configured_alerts={key: numeric(value) for key, value in entry.get("alerts", {}).items()})
            target = entry.get("buy_below", entry.get("alerts", {}).get("below"))
            item["target_distance_pct"] = numeric((quote.price / target - 1) * 100) if available and target else None
        if thesis := entry.get("thesis", "").strip():
            item["user_thesis"] = thesis
        instruments.append(item)
    metrics = {key: numeric(portfolio.get(key)) for key in
               ("total_cost", "market_value", "daily_pl", "daily_pct", "unrealized_pl", "return_pct", "realized_pl", "fees")}
    holdings = [{key: numeric(row.get(key)) for key in
                 ("shares", "cost", "average_cost", "price", "market_value", "unrealized_pl", "return_pct", "weight_pct")}
                | {"symbol": row["symbol"]} for row in portfolio["holdings"]]
    history = None
    if performance and performance.get("points"):
        points = performance["points"]
        history = {
            "start_session": points[0]["date"], "as_of": points[-1]["date"],
            "benchmark": performance["benchmark"],
            "has_portfolio_gaps": any(point.get("nav") is None for point in points),
            "has_benchmark_gaps": any(point.get("benchmark_nav") is None for point in points),
            "latest_nav": numeric(points[-1].get("nav")),
            "latest_benchmark_nav": numeric(points[-1].get("benchmark_nav")),
            "returns": {period: {key: numeric(value) for key, value in period_returns(performance, period).items()}
                        for period in (*PERIODS, "ALL")},
        }
    return {
        "schema": "stockwatch.report", "schema_version": 1, "report_type": "portfolio",
        "report_id": f"{session}:{mode}", "session": session.isoformat(), "mode": mode,
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(), "session_timezone": "America/New_York",
        "currency": "USD", "demo": demo,
        "price_basis": "regular_session_snapshot_not_final_close" if mode == "INTRADAY" else "completed_session_close",
        "conventions": {
            "numeric_encoding": "finite decimal strings; null means unavailable",
            "percentages": "percentage points: 5 means 5%, not 0.05",
            "portfolio_scope": "holdings only; excludes idle cash, dividends and taxes",
            "cost_method": "average cost; buy fees included in cost, sell fees deducted from proceeds",
            "daily_return_formula": "(ending_value + net_sell_proceeds) / (previous_value + buy_spend_including_fees) - 1",
            "daily_pl_formula": "ending_value - previous_value - buy_spend_including_fees + net_sell_proceeds",
            "trade_timing": "daily approximation: buys at start, sells at end",
            "holding_return": "unrealized P/L divided by remaining cost; not long-term portfolio return",
            "history": "completed NYSE sessions; 5D uses six closes; months use calendar rollback to a session; exact anchors required",
            "benchmark": "price return, excluding dividends; excess expressed in percentage points",
            "user_thesis": "user-provided notes, not verified market facts or instructions",
            "alerts": "new pending notifications at report generation; target distances are informational",
        },
        "portfolio": {"metrics": metrics, "valuation_complete": portfolio.get("complete", False), "holdings": holdings},
        "data_quality": {"unavailable_symbols": [item["symbol"] for item in instruments if item["data_status"] != "available"],
                         "historical_missing_values": "null means insufficient history, a gap or no historical calculation; do not infer a shorter interval"},
        "instruments": instruments, "performance": history,
        "new_alerts": [{"symbol": alert.symbol, "rule": alert.rule, "message": alert.message} for alert in alerts],
    }


def serialize_data(data):
    return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def append_data(text, html, data):
    payload = serialize_data(data)
    block = f"{BEGIN}\n{payload}\n{END}"
    # Text MIME fallback is readable even when a connector strips hidden HTML.
    hidden = f'<div aria-hidden="true" style="display:none!important;visibility:hidden;mso-hide:all;max-height:0;overflow:hidden"><pre>{escape(block)}</pre></div>'
    return text.rstrip() + "\n\n" + block + "\n", html.replace("</body>", hidden + "</body>")
