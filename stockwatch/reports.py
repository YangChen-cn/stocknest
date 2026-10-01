"""Factual, localized plain-text and Gmail-friendly HTML reports."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html import escape

from stockwatch.alerts import Alert, report_session_key, target_distances
from stockwatch.calendar import NY
from stockwatch.i18n import data_status, language, t
from stockwatch.providers.base import Quote
from stockwatch.performance import period_returns


def money(value, *, signed: bool = False, lang: str = "en") -> str:
    if value is None:
        return t("Data unavailable", lang)
    if signed:
        return f"{'+' if value >= 0 else '-'}${abs(value):,.2f}"
    return f"${value:,.2f}"


def percent(value, *, signed: bool = True, lang: str = "en") -> str:
    if value is None:
        return t("Data unavailable", lang)
    return f"{value:+.2f}%" if signed else f"{value:.2f}%"


@dataclass(frozen=True)
class Report:
    subject: str
    text: str
    html: str


def render_report(session: date, portfolio: dict, quotes: dict[str, Quote], config: dict,
                  alerts: list[Alert], *, demo: bool = False, mode: str = "CLOSE",
                  generated_at: datetime | None = None, performance: dict | None = None, watchlist_rows: list[dict] | None = None) -> Report:
    report_session_key(mode)
    intraday = mode == "INTRADAY"
    generated_at = generated_at or datetime.now(timezone.utc)
    lang = language(config)

    def tr(key: str, **values):
        return t(key, lang, **values)

    def m(value, **options):
        return money(value, lang=lang, **options)

    def p(value, **options):
        return percent(value, lang=lang, **options)

    heading = tr("StockWatch Intraday" if intraday else "StockWatch Daily")
    title = f"{heading} — {session.isoformat()}"
    timing = tr("Snapshot at {time} New York · not final closing prices", time=generated_at.astimezone(NY).strftime("%H:%M %Z")) if intraday else tr("Completed NYSE session · USD")
    short_date = f"{session.month}月{session.day}日" if lang == "zh-CN" else f"{session.strftime('%b')} {session.day}"
    subject = f"{heading} | {p(portfolio['daily_pct'])} | {short_date}"
    summary = [(tr("Total Cost"), m(portfolio["total_cost"])), (tr("Market Value"), m(portfolio["market_value"])),
               (tr("Daily P/L"), f"{m(portfolio['daily_pl'], signed=True)} ({p(portfolio['daily_pct'])})"),
               (tr("Unrealized P/L"), m(portfolio["unrealized_pl"], signed=True)),
               (tr("Holding unrealized return"), p(portfolio["return_pct"]))]
    if intraday:
        summary = summary[1:3]
    lines = [title, timing, "", tr("SIMULATED DEMO DATA — not live market prices") if demo else tr("Regular-session prices · USD"), "", tr("Portfolio")]
    data_warning = tr("Market data is missing; this report cannot provide a complete portfolio valuation.") if portfolio["market_value"] is None else ""
    if data_warning:
        lines[2:2] = [data_warning]
    lines.extend(f"{name}: {value}" for name, value in summary)
    performance_lines = []
    if performance and len(performance["points"]) > 1:
        returns = period_returns(performance)
        performance_lines = [tr("History through {date}; excludes dividends and cash.", date=performance["points"][-1]["date"]),
                             tr("Since inception: {portfolio} · {benchmark}: {return_}",
                                portfolio=p(returns["portfolio"]), benchmark=performance["benchmark"], return_=p(returns["benchmark"]))]
        if not intraday:
            performance_lines.append(tr("Realized P/L: {value}", value=m(portfolio.get("realized_pl"), signed=True)))
        lines.extend(["", tr("Performance")] + performance_lines)
    lines.extend(["", tr("Holdings")])
    holding_html = []
    for row in portfolio["holdings"]:
        lines.extend(["", row["symbol"], m(row["price"]), tr("Today {value}", value=p(row["daily_pct"]))])
        if not intraday:
            lines.extend([tr("Shares {shares} · Avg Cost {cost}", shares=row["shares"], cost=m(row["average_cost"])),
                          tr("Total {value}", value=p(row["return_pct"])), tr("Weight {value}", value=p(row["weight_pct"], signed=False))])
        elif quotes[row["symbol"]].price_at:
            lines.append(tr("Price time: {time} New York", time=quotes[row["symbol"]].price_at.astimezone(NY).strftime("%H:%M %Z")))
        color = "#137333" if row["unrealized_pl"] is not None and row["unrealized_pl"] >= 0 else "#b3261e"
        cells = [
            f"<td style='padding:12px 6px;border-bottom:1px solid #eee'><strong>{escape(row['symbol'])}</strong><br><span style='color:#666'>{escape(tr('{shares} shares', shares=row['shares']))}</span></td>",
            f"<td style='padding:12px 6px;border-bottom:1px solid #eee'>{escape(m(row['price']))}<br><small>{escape(tr('{value} today', value=p(row['daily_pct'])))}</small></td>"]
        if not intraday:
            cells.extend([
            f"<td style='padding:12px 6px;border-bottom:1px solid #eee;color:{color}'>{escape(m(row['unrealized_pl'], signed=True))}<br><small>{escape(tr('{value} total', value=p(row['return_pct'])))}</small></td>",
            f"<td style='padding:12px 6px;border-bottom:1px solid #eee'>{escape(p(row['weight_pct'], signed=False))}</td>"])
        if intraday:
            stamp = quotes[row["symbol"]].price_at
            cells.append(f"<td style='padding:12px 6px;border-bottom:1px solid #eee'>{escape(stamp.astimezone(NY).strftime('%H:%M %Z') if stamp else tr('Data unavailable'))}</td>")
        holding_html.append("<tr>" + "".join(cells) + "</tr>")
    if not portfolio["holdings"]:
        lines.append(tr("No current holdings."))
        holding_html.append(f"<tr><td colspan='{3 if intraday else 4}' style='padding:12px'>{escape(tr('No current holdings.'))}</td></tr>")
    watch_html = ""
    if not intraday and config["watchlist"]:
        rows = watchlist_rows if watchlist_rows is not None else [
            {"Symbol": symbol, "Current Price": quotes.get(symbol, Quote(symbol)).price,
             "Daily %": quotes.get(symbol, Quote(symbol)).daily_move_pct, "5D %": None, "1M %": None}
            for symbol in config["watchlist"]]
        lines.extend(["", tr("Watchlist summary (closing prices)")])
        watch_cells = []
        for row in rows:
            values = [row["Symbol"], m(row["Current Price"]), p(row["Daily %"]), p(row["5D %"]), p(row["1M %"])]
            lines.append(tr("{symbol}: {price} · Today {daily} · 5D {five} · 1M {month}",
                            symbol=values[0], price=values[1], daily=values[2], five=values[3], month=values[4]))
            watch_cells.append("<tr>" + "".join(f"<td style='padding:8px 3px;border-bottom:1px solid #eee'>{escape(value)}</td>" for value in values) + "</tr>")
        headers = ("Symbol", "Current Price", "Daily %", "5D %", "1M %")
        watch_html = "<h2 style='font-size:18px'>" + escape(tr("Watchlist summary (closing prices)")) + "</h2><table style='width:100%;font-size:12px;border-collapse:collapse;text-align:left'><tr>" + "".join("<th>" + escape(tr(header)) + "</th>" for header in headers) + "</tr>" + "".join(watch_cells) + "</table>"
    alert_messages = [alert.message for alert in alerts]
    distances = target_distances(config, quotes) if not intraday else []
    unusual = []
    if intraday:
        for symbol, quote in sorted(quotes.items()):
            threshold = config["watchlist"].get(symbol, {}).get("alerts", {}).get("daily_move_pct", 5)
            if not quote.error and quote.daily_move_pct is not None and abs(quote.daily_move_pct) >= threshold:
                unusual.append(tr("{symbol}: {change} today (movement threshold {threshold:g}%)", symbol=symbol, change=p(quote.daily_move_pct), threshold=threshold))
        for symbol, entry in config["watchlist"].items():
            target = entry.get("buy_below", entry.get("alerts", {}).get("below"))
            quote = quotes.get(symbol)
            if target and quote and quote.price is not None and not quote.error:
                distance = (quote.price / target - 1) * 100
                if abs(distance) <= 5:
                    distances.append(tr("{symbol}: {price} · {distance} from target {target} · {status}", symbol=symbol, price=m(quote.price), distance=p(distance), target=m(target), status=tr(entry.get("status", "watching"))))
    errors = [f"{symbol}: {data_status(quote.error, lang)}" for symbol, quote in quotes.items() if quote.error or quote.price is None]
    lines.extend(["", tr("Alerts")] + (alert_messages or [tr("No new alerts.")]))
    if intraday:
        lines.extend(["", tr("Notable moves")] + (unusual or [tr("No moves reached the threshold.")]))
    if distances:
        lines.extend(["", tr("Near candidate price (within 5%)" if intraday else "Target price distances (informational)")] + distances)
    elif intraday:
        lines.extend(["", tr("Near candidate price (within 5%)"), tr("No candidates within 5% of target.")])
    if errors:
        lines.extend(["", tr("Data availability")] + errors)
    lines.extend(["", tr("Minute snapshots may be delayed. They are not final closing prices.") if intraday else tr("Daily P/L adjusts for recorded buys, sells and fees; trade-day returns use daily timing assumptions."),
                  tr("Daily P/L adjusts for recorded buys, sells and fees; trade-day returns use daily timing assumptions.") if intraday else tr("Holdings-only performance excludes dividends and cash. Realized P/L uses average cost, not tax lots."),
                  tr("Sources: {sources}", sources=", ".join(sorted({tr(quote.source) for quote in quotes.values()})))])

    def paragraphs(items: list[str]) -> str:
        return "".join(f"<p style='margin:8px 0'>{escape(item)}</p>" for item in items)

    summary_html = "".join(f"<tr><td style='padding:6px 0;color:#555'>{escape(name)}</td><td style='padding:6px 0;text-align:right;font-weight:600'>{escape(value)}</td></tr>" for name, value in summary)
    html = f"""<!doctype html><html lang="{lang}"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:12px;background:#f5f7fa;font-family:Arial,'PingFang SC','Microsoft YaHei',sans-serif;color:#202124">
<div style="max-width:640px;margin:0 auto;background:white;padding:20px;border-radius:12px">
<h1 style="font-size:22px;margin:0 0 6px">{escape(heading)}</h1><p style="color:#666;margin:0 0 16px">{session.isoformat()} · {escape(tr('USD'))} · {escape(tr('Regular session'))}</p>
<p style="color:#666;font-size:13px">{escape(timing)}</p>
{paragraphs([data_warning]) if data_warning else ''}
{paragraphs([tr('SIMULATED DEMO DATA — not live market prices')]) if demo else ''}
<table role="presentation" style="width:100%;font-size:15px">{summary_html}</table>
{'<h2 style="font-size:18px">' + escape(tr('Performance')) + '</h2>' + paragraphs(performance_lines) if performance_lines else ''}
<h2 style="font-size:18px">{escape(tr('Holdings'))}</h2><table style="width:100%;border-collapse:collapse;font-size:13px;text-align:left">
<tr><th>{escape(tr('Symbol'))}</th><th>{escape(tr('Price / Day'))}</th>{'<th>' + escape(tr('Price time (New York)')) + '</th>' if intraday else '<th>' + escape(tr('P/L / Return')) + '</th><th>' + escape(tr('Weight')) + '</th>'}</tr>{''.join(holding_html)}</table>
{watch_html}
<h2 style="font-size:18px">{escape(tr('Alerts'))}</h2>{paragraphs(alert_messages or [tr('No new alerts.')])}
{'<h2 style="font-size:18px">' + escape(tr('Notable moves')) + '</h2>' + paragraphs(unusual or [tr('No moves reached the threshold.')]) if intraday else ''}
{'<h3 style="font-size:15px">' + escape(tr('Near candidate price (within 5%)' if intraday else 'Target price distances (informational)')) + '</h3>' + paragraphs(distances or [tr('No candidates within 5% of target.')]) if distances or intraday else ''}
{'<h2 style="font-size:18px">' + escape(tr('Data availability')) + '</h2>' + paragraphs(errors) if errors else ''}
<p style="font-size:12px;color:#666;margin-top:24px">{escape(lines[-3])}<br>{escape(lines[-2])}<br>{escape(lines[-1])}</p>
</div></body></html>"""
    return Report(subject, "\n".join(lines) + "\n", html)
