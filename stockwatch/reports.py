"""Factual, localized plain-text and Gmail-friendly HTML reports."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html import escape
from math import isfinite
from decimal import Decimal

from stockwatch.alerts import Alert, report_session_key, target_distances
from stockwatch.calendar import NY
from stockwatch.i18n import data_status, language, t
from stockwatch.providers.base import Quote
from stockwatch.performance import milestone_status, period_returns
from stockwatch.report_data import append_data, report_data, serialize_data

from stockwatch.presentation import GREEN, RED, NEUTRAL, SOFT, email_section, email_shell, reflection, record_note, value_color


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
    data_json: str | None = None
    data_filename: str | None = None


def intraday_watchlist_highlights(config, quotes, held, session):
    """Up to three unheld watchers reaching 5%; decimal comparisons keep ties stable."""
    candidates = []
    for symbol in config["watchlist"]:
        quote = quotes.get(symbol)
        if symbol in held or not quote or quote.error or quote.session != session:
            continue
        if any(value is None or value <= 0 or not isfinite(value) for value in (quote.price, quote.previous_close)):
            continue
        change = (Decimal(str(quote.price)) / Decimal(str(quote.previous_close)) - 1) * 100
        if abs(change) >= 5:
            candidates.append((change, quote))
    return [quote for _, quote in sorted(candidates, key=lambda item: (-abs(item[0]), item[1].symbol))[:3]]


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

    section = email_section

    def value_rows(rows) -> str:
        return "".join(
            f"<tr><td style='padding:6px 0;font-size:14px;color:{NEUTRAL}'>{escape(name)}</td>"
            f"<td style='padding:6px 0;text-align:right;font-weight:600;color:{color}'>{escape(value)}</td></tr>"
            for name, value, color in rows)

    def list_rows(items: list[str]) -> str:
        rows = []
        for index, item in enumerate(items):
            border = "" if index == len(items) - 1 else "border-bottom:1px solid #e8ece6;"
            rows.append(f"<div style='padding:7px 2px;font-size:13px;{border}'>{escape(item)}</div>")
        return "".join(rows)

    def styled_table(headers: tuple[str, ...], rows_html: str) -> str:
        head = "".join(f"<th style='padding:8px;background:{SOFT};color:{NEUTRAL};font-size:12px;"
                       f"font-weight:500;text-align:left'>{escape(tr(header))}</th>" for header in headers)
        return f"<table class='mail-table' style='width:100%;border-collapse:collapse;font-size:14px;text-align:left'><tr>{head}</tr>{rows_html}</table>"

    def shade_style(index: int) -> str:
        return f"padding:7px;border-bottom:1px solid #e8ece6;background:{'#ffffff' if index % 2 == 0 else '#f7f8f5'}"

    heading = tr("StockWatch Intraday" if intraday else "StockWatch Daily")
    title = f"{heading} — {session.isoformat()}"
    timing = tr("Snapshot at {time} New York · not final closing prices", time=generated_at.astimezone(NY).strftime("%H:%M %Z")) if intraday else tr("Completed NYSE session · USD")
    short_date = f"{session.month}月{session.day}日" if lang == "zh-CN" else f"{session.strftime('%b')} {session.day}"
    # Milestones stay on completed sessions; intraday never claims a final high.
    milestone = milestone_status(performance, as_of=session) if not intraday and performance and portfolio.get("complete") else None
    subject = f"{heading} | {p(portfolio['daily_pct'])} | {short_date}"
    summary = [(tr("Total Cost"), m(portfolio["total_cost"])), (tr("Market Value"), m(portfolio["market_value"])),
               (tr("Daily P/L"), f"{m(portfolio['daily_pl'], signed=True)} ({p(portfolio['daily_pct'])})"),
               (tr("Unrealized P/L"), m(portfolio["unrealized_pl"], signed=True)),
               (tr("Holding unrealized return"), p(portfolio["return_pct"]))]
    if intraday:
        summary = summary[1:3]
    milestone_line = tr("Portfolio NAV reached a new high at the {date} close.", date=session.isoformat()) if milestone and milestone["new_high"] else None
    quote_line = reflection(session, lang)
    lines = [title, timing, "", tr("SIMULATED DEMO DATA — not live market prices") if demo else tr("Regular-session prices · USD"), "", tr("Portfolio")]
    data_warning = tr("Market data is missing; this report cannot provide a complete portfolio valuation.") if portfolio["market_value"] is None else ""
    if data_warning:
        lines[2:2] = [data_warning]
    lines.extend(f"{name}: {value}" for name, value in summary)
    performance_lines = []
    performance_rows = []
    if performance and len(performance["points"]) > 1:
        returns = period_returns(performance)
        performance_lines = [tr("History through {date}; excludes dividends and cash.", date=performance["points"][-1]["date"]),
                             tr("Since inception: {portfolio} · {benchmark}: {return_}",
                                portfolio=p(returns["portfolio"]), benchmark=performance["benchmark"], return_=p(returns["benchmark"]))]
        excess = f"{returns['excess']:+.2f} pp" if returns["excess"] is not None else tr("Data unavailable")
        performance_rows = [(tr("Since inception"), p(returns["portfolio"]), value_color(returns["portfolio"])),
                            (f"{performance['benchmark']} · {tr('Benchmark')}", p(returns["benchmark"]), NEUTRAL),
                            (tr("Excess return (pp)"), excess, value_color(returns["excess"]))]
        if not intraday:
            performance_lines.append(tr("Realized P/L: {value}", value=m(portfolio.get("realized_pl"), signed=True)))
            performance_rows.append((tr("Realized P/L"), m(portfolio.get("realized_pl"), signed=True), value_color(portfolio.get("realized_pl"))))
        lines.extend(["", tr("Performance")] + performance_lines)
        if milestone_line:
            lines.append(milestone_line)
    lines.extend(["", tr("Holdings")])
    holding_html = []
    for index, row in enumerate(portfolio["holdings"]):
        lines.extend(["", row["symbol"], m(row["price"]), tr("Today {value}", value=p(row["daily_pct"]))])
        if not intraday:
            lines.extend([tr("Shares {shares} · Avg Cost {cost}", shares=row["shares"], cost=m(row["average_cost"])),
                          tr("Total {value}", value=p(row["return_pct"])), tr("Weight {value}", value=p(row["weight_pct"], signed=False))])
        elif quotes[row["symbol"]].price_at:
            lines.append(tr("Price time: {time} New York", time=quotes[row["symbol"]].price_at.astimezone(NY).strftime("%H:%M %Z")))
        thesis = config["watchlist"].get(row["symbol"], {}).get("thesis", "").strip()
        if thesis:
            lines.append(tr("Thesis") + ": " + thesis)
        shade = "#ffffff" if index % 2 == 0 else "#f7f8f5"
        base = f"padding:10px 8px;border-bottom:1px solid #e8ece6;background:{shade}"
        total_color = value_color(row["unrealized_pl"])
        cells = [
            f"<td style='{base}'><strong>{escape(row['symbol'])}</strong><br><span style='color:{NEUTRAL}'>{escape(tr('{shares} shares', shares=row['shares']))}</span></td>",
            f"<td style='{base}'>{escape(m(row['price']))}<br><small style='color:{value_color(row['daily_pct'])}'>{escape(tr('{value} today', value=p(row['daily_pct'])))}</small></td>"]
        if not intraday:
            cells.extend([
            f"<td style='{base};color:{total_color}'>{escape(m(row['unrealized_pl'], signed=True))}<br><small>{escape(tr('{value} total', value=p(row['return_pct'])))}</small></td>",
            f"<td style='{base}'>{escape(p(row['weight_pct'], signed=False))}</td>"])
        if intraday:
            stamp = quotes[row["symbol"]].price_at
            cells.append(f"<td style='{base}'>{escape(stamp.astimezone(NY).strftime('%H:%M %Z') if stamp else tr('Data unavailable'))}</td>")
        holding_html.append("<tr>" + "".join(cells) + "</tr>")
        if thesis:
            holding_html.append(f"<tr><td colspan='{3 if intraday else 4}' style='padding:0 8px 10px;color:{NEUTRAL};font-size:13px;white-space:pre-wrap;word-break:break-word'>{escape(tr('Thesis'))}: {escape(thesis)}</td></tr>")
    if not portfolio["holdings"]:
        lines.append(tr("No current holdings."))
        holding_html.append(f"<tr><td colspan='{3 if intraday else 4}' style='padding:12px'>{escape(tr('No current holdings.'))}</td></tr>")
    watch_html = ""
    highlights = intraday_watchlist_highlights(config, quotes, {row["symbol"] for row in portfolio["holdings"]}, session) if intraday else []
    if highlights:
        heading_watch = tr("Watchlist moves ≥5% (up to 3)")
        ranking = tr("Absolute daily change reaches 5%; ranked by magnitude. Normal-session snapshots, not final closes.")
        lines.extend(["", heading_watch, ranking])
        cells = []
        for index, quote in enumerate(highlights):
            stamp = quote.price_at.astimezone(NY).strftime("%H:%M %Z") if quote.price_at else tr("Data unavailable")
            values = [quote.symbol, m(quote.price), p(quote.daily_move_pct), stamp]
            lines.append(tr("{symbol}: {price} · Today {daily} · Price time {time} New York",
                            symbol=quote.symbol, price=values[1], daily=values[2], time=stamp))
            style = shade_style(index)
            cells.append("<tr>" + f"<td style='{style}'><strong>{escape(values[0])}</strong></td>"
                         + f"<td style='{style}'>{escape(values[1])}</td>"
                         + f"<td style='{style};color:{value_color(quote.daily_move_pct)}'>{escape(values[2])}</td>"
                         + f"<td style='{style}'>{escape(values[3])}</td></tr>")
        headers = ("Symbol", "Current Price", "Daily %", "Price time (New York)")
        watch_html = section(heading_watch, f"<p style='font-size:12px;color:{NEUTRAL};margin:0 0 8px'>{escape(ranking)}</p>"
                             + styled_table(headers, "".join(cells)))
    if not intraday and config["watchlist"]:
        rows = watchlist_rows if watchlist_rows is not None else [
            {"Symbol": symbol, "Current Price": quotes.get(symbol, Quote(symbol)).price,
             "Daily %": quotes.get(symbol, Quote(symbol)).daily_move_pct, "5D %": None, "1M %": None}
            for symbol in config["watchlist"]]
        lines.extend(["", tr("Watchlist summary (closing prices)")])
        watch_cells = []
        for index, row in enumerate(rows):
            values = [row["Symbol"], m(row["Current Price"]), p(row["Daily %"]), p(row["5D %"]), p(row["1M %"])]
            lines.append(tr("{symbol}: {price} · Today {daily} · 5D {five} · 1M {month}",
                            symbol=values[0], price=values[1], daily=values[2], five=values[3], month=values[4]))
            style = shade_style(index)
            watch_cells.append("<tr>" + f"<td style='{style}'><strong>{escape(str(values[0]))}</strong></td>"
                               + f"<td style='{style}'>{escape(values[1])}</td>"
                               + f"<td style='{style};color:{value_color(row['Daily %'])}'>{escape(values[2])}</td>"
                               + f"<td style='{style};color:{value_color(row['5D %'])}'>{escape(values[3])}</td>"
                               + f"<td style='{style};color:{value_color(row['1M %'])}'>{escape(values[4])}</td></tr>")
        headers = ("Symbol", "Current Price", "Daily %", "5D %", "1M %")
        watch_html = section(tr("Watchlist summary (closing prices)"), styled_table(headers, "".join(watch_cells)))
    alert_messages = [alert.message for alert in alerts]
    distances = target_distances(config, quotes) if not intraday else []
    unusual = []
    if intraday:
        held_symbols = {row["symbol"] for row in portfolio["holdings"]}
        for symbol, quote in sorted(quotes.items()):
            # Watch-only movements belong in the capped summary; configured alerts remain independent.
            if symbol in config["watchlist"] and symbol not in held_symbols:
                continue
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
    if intraday and (unusual or not highlights):
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

    hero = f"""<table role="presentation" style="width:100%"><tr>
<td style="vertical-align:bottom"><p style="margin:0;font-size:12px;color:{NEUTRAL}">{escape(tr("Market Value"))}</p>
<p class="mail-hero" style="margin:2px 0 0;font-size:34px;font-weight:600">{escape(m(portfolio["market_value"]))}</p></td>
<td style="vertical-align:bottom;text-align:right"><p style="margin:0;font-size:12px;color:{NEUTRAL}">{escape(tr("Daily P/L"))}</p>
<p class="mail-hero" style="margin:2px 0 0;font-size:28px;font-weight:600;color:{value_color(portfolio["daily_pl"])}">{escape(m(portfolio["daily_pl"], signed=True))}</p>
<p style="margin:0;font-size:12px;color:{value_color(portfolio["daily_pct"])}">{escape(p(portfolio["daily_pct"]))}</p></td></tr></table>"""
    milestone_html = (f"<p style='background:{SOFT};color:{GREEN};padding:10px 12px;border-radius:6px;margin:12px 0'>↗ {escape(milestone_line)}</p>") if milestone_line else ""
    if not intraday:
        labeled = [(tr("Total Cost"), m(portfolio["total_cost"]), NEUTRAL),
                   (tr("Unrealized P/L"), m(portfolio["unrealized_pl"], signed=True), value_color(portfolio["unrealized_pl"])),
                   (tr("Holding unrealized return"), p(portfolio["return_pct"]), value_color(portfolio["return_pct"]))]
        summary_block = ("<table role='presentation' style='width:100%;margin:10px 0 0;border-top:1px solid #e8ece6'>"
                         + value_rows(labeled) + "</table>")
    else:
        summary_block = ""
    demo_banner = (f"<div style='background:#fef7e0;border:1px solid #fdd663;border-radius:10px;padding:8px 12px;"
                   f"margin:0 0 10px;color:#7c5800;font-size:12px'>⚠️ {escape(tr('SIMULATED DEMO DATA — not live market prices'))}</div>") if demo else ""
    warning_banner = (f"<div style='background:#fce8e6;border:1px solid #f6aea9;border-radius:10px;padding:8px 12px;"
                      f"margin:0 0 10px;color:{RED};font-size:12px'>{escape(data_warning)}</div>") if data_warning else ""
    holdings_headers = ("Symbol", "Price / Day", "Price time (New York)") if intraday else ("Symbol", "Price / Day", "P/L / Return", "Weight")
    holdings_block = section(tr("Holdings"), styled_table(holdings_headers, "".join(holding_html)))
    performance_block = ""
    if performance_rows:
        history_note = tr("History through {date}; excludes dividends and cash.", date=performance["points"][-1]["date"])
        performance_block = section(tr("Performance"),
                                    f"<table role='presentation' style='width:100%'>{value_rows(performance_rows)}</table>"
                                    f"<p style='margin:8px 0 0;font-size:11px;color:{NEUTRAL}'>{escape(history_note)}</p>")
    alerts_body = (f"<div style='background:#fef7e0;border-radius:8px;padding:2px 12px'>{list_rows(alert_messages)}</div>"
                   if alert_messages else f"<p style='margin:2px 0;font-size:14px;color:{NEUTRAL}'>{escape(tr('No new alerts.'))}</p>")
    alerts_block = section(tr("Alerts"), alerts_body) if alert_messages else f"<p style='color:{NEUTRAL};font-size:14px;margin:20px 0 0'>{escape(tr('No new alerts.'))}</p>"
    notable_block = (section(tr("Notable moves"), list_rows(unusual or [tr("No moves reached the threshold.")]))
                     if intraday and (unusual or not highlights) else "")
    near_block = (section(tr("Near candidate price (within 5%)" if intraday else "Target price distances (informational)"),
                          list_rows(distances or [tr("No candidates within 5% of target.")])) if distances or intraday else "")
    errors_block = (section(tr("Data availability"), f"<div style='background:#fce8e6;border-radius:8px;padding:2px 12px'>{list_rows(errors)}</div>")
                    if errors else "")
    footer = (f"<p style='font-size:11px;color:#80868b;line-height:1.6;margin:18px 0 0;padding-top:12px;border-top:1px solid #e8eaed'>"
              f"{escape(lines[-3])}<br>{escape(lines[-2])}<br>{escape(lines[-1])}</p>")
    note = record_note(performance, lang)
    record_html = f"<p style='font-size:13px;color:{NEUTRAL}'>{escape(note)}</p>" if note else ""
    attachment_note = tr("JSON data is attached for your own analysis.")
    html = email_shell(heading, f"{session.isoformat()} · {timing}",
                       demo_banner + warning_banner + hero + milestone_html + summary_block
                       + record_html + holdings_block + performance_block + watch_html + alerts_block
                       + notable_block + near_block + errors_block + footer
                       + f"<p style='font-size:13px;color:{NEUTRAL}'>{escape(attachment_note)}</p>", lang, closing=quote_line)
    lines.extend(["", quote_line, attachment_note])
    data = report_data(session, portfolio, quotes, config, alerts, mode=mode, generated_at=generated_at,
                       demo=demo, performance=performance, watchlist_rows=watchlist_rows)
    if intraday:
        data["visible_watchlist_symbols"] = [quote.symbol for quote in highlights]
        data["watchlist_highlight_selection"] = "up to 3 unheld watched symbols reaching 5% absolute daily change, ranked by magnitude, ties by symbol"
    plain, html = append_data("\n".join(lines) + "\n", html, data)
    return Report(subject, plain, html, serialize_data(data), f"stockwatch-{session}-{mode.lower()}.json")


def render_failure(session: date, reason: str, symbols: list[str], attempts: int, lang: str, *, mode="CLOSE") -> Report:
    heading = t("StockWatch report error", lang)
    lines = [heading, str(session), t(reason, lang),
             t("Checks attempted: {attempts}", lang, attempts=attempts),
             t("No portfolio report was marked sent; price alerts remain pending.", lang)]
    if symbols:
        lines.append(t("Unavailable symbols: {symbols}", lang, symbols=", ".join(symbols)))
    subject = f"{heading} | {mode} | {session}"
    body = "".join(f"<p style='margin:10px 0'>{escape(line)}</p>" for line in lines[2:])
    html = email_shell(heading, f"{mode} · {session}",
                       f"<div style='border-left:3px solid {RED};padding-left:16px'>{body}</div>", lang)
    data = {
        "schema": "stockwatch.report", "schema_version": 1, "report_type": "error",
        "session": session.isoformat(), "mode": mode, "reason": reason,
        "unavailable_symbols": symbols, "checks_attempted": attempts,
        "normal_report_marked_sent": False, "price_alerts_consumed": False,
    }
    plain, html = append_data("\n".join(lines), html, data)
    return Report(subject, plain, html, serialize_data(data), f"stockwatch-{session}-{mode.lower()}-error.json")
