"""Factual, localized plain-text and Gmail-friendly HTML reports."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html import escape
from math import isfinite
from decimal import Decimal

from stockwatch.alerts import Alert, report_session_key, target_distances
from stockwatch.calendar import NY, previous_session
from stockwatch.i18n import data_status, language, t
from stockwatch.providers.base import Quote
from stockwatch.performance import milestone_status, period_returns
from stockwatch.report_data import append_data, report_data, serialize_data
from stockwatch.garden import Garden
from stockwatch.garden_render import garden_view

from stockwatch.presentation import GREEN, INK, RED, NEUTRAL, SOFT, InlineImage, atomic, email_hero, email_holding, email_metrics, email_section, email_shell, reflection, reflection_html, record_note, value_color


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
    inline_images: tuple[InlineImage, ...] = ()


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
                  generated_at: datetime | None = None, performance: dict | None = None, watchlist_rows: list[dict] | None = None,
                  garden: Garden | None = None, quantitative_analysis: dict | None = None) -> Report:
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

    from stockwatch.interpretation import compact_badges
    from stockwatch.presentation import email_quant_badges
    cutoff = str(previous_session(session) if intraday else session) if quantitative_analysis else None
    interpretations = {}
    if cutoff and quantitative_analysis.get('expected_daily_session') == cutoff:
        interpretations = (quantitative_analysis.get('interpretation') or {}).get('instruments') or {}

    def stock_badges(symbol):
        return [badge for badge in compact_badges(interpretations.get(symbol), lang) if badge['session'] == cutoff]

    def badge_html(symbol):
        return email_quant_badges(stock_badges(symbol), lang)

    def badge_text(symbol):
        badges = stock_badges(symbol)
        return (' [' + str(badges[0]['session']) + ' · ' + ' / '.join(b['label'] for b in badges) + ']') if badges else ''

    section = email_section

    def list_rows(items: list[str]) -> str:
        rows = []
        for index, item in enumerate(items):
            border = "" if index == len(items) - 1 else "border-bottom:1px solid #e8ece6;"
            rows.append(f"<div style='padding:9px 2px;font-size:13px;line-height:1.75;text-wrap:pretty;overflow-wrap:break-word;{border}'>{escape(item)}</div>")
        return "".join(rows)

    def watch_table(headers: tuple[str, ...], rows: list[tuple[str, list[tuple[str, str]]]]) -> str:
        head = "".join(f"<th scope='col' style='padding:7px 5px;background:{SOFT};color:{NEUTRAL};font-size:12px;"
                       f"font-weight:500;text-align:{'left' if index == 0 else 'right'}'>{escape(tr(header))}</th>"
                       for index, header in enumerate(headers))
        rows_html = []
        has_badges = False
        unavailable = tr('Data unavailable')
        missing = f"<span title='{escape(unavailable)}' aria-label='{escape(unavailable)}'>—</span>"
        border = 'border-bottom:1px solid #e8ece6;'
        for index, (symbol, values) in enumerate(rows):
            badges = email_quant_badges(stock_badges(symbol), lang, show_date=False)
            has_badges = has_badges or bool(badges)
            background = '#ffffff' if index % 2 == 0 else '#f7f8f5'
            padding = 'padding:8px 5px 2px;' if badges else f'padding:8px 5px;{border}'
            ticker_label = atomic(symbol) if len(symbol) <= 6 else escape(symbol)
            cells = [f"<th scope='row' style='{padding}text-align:left;overflow-wrap:anywhere'>{ticker_label}</th>"]
            for value, color in values:
                label = missing if value == unavailable else atomic(value)
                cells.append(f"<td style='{padding}text-align:right;color:{color}'>{label}</td>")
            detail = (f"<tr class='mail-watch-quant'><td colspan='{len(headers)}' style='padding:0 5px 8px;{border}"
                      f"line-height:1.4;text-align:left'>{badges}</td></tr>") if badges else ''
            rows_html.append(f"<tbody style='background:{background}'><tr>{''.join(cells)}</tr>{detail}</tbody>")
        date_note = (f"<p style='font-size:11px;color:{NEUTRAL};margin:5px 0 0'>{atomic(tr('Indicators at {date} close', date=cutoff))}</p>"
                     if has_badges else '')
        return ("<table class='mail-watchlist' style='width:100%;border-collapse:collapse;font-size:13px'>"
                f"<thead><tr>{head}</tr></thead>{''.join(rows_html)}</table>{date_note}")

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
    garden_content = garden_view(garden, lang)
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
        lines.extend(["", row["symbol"] + badge_text(row["symbol"]), m(row["price"]), tr("Today {value}", value=p(row["daily_pct"]))])
        if not intraday:
            lines.extend([tr("Shares {shares} · Avg Cost {cost}", shares=row["shares"], cost=m(row["average_cost"])),
                          tr("Total {value}", value=p(row["return_pct"])), tr("Weight {value}", value=p(row["weight_pct"], signed=False))])
        elif quotes[row["symbol"]].price_at:
            lines.append(tr("Price time: {time} New York", time=quotes[row["symbol"]].price_at.astimezone(NY).strftime("%H:%M %Z")))
        thesis = config["watchlist"].get(row["symbol"], {}).get("thesis", "").strip()
        if thesis:
            lines.append(tr("Thesis") + ": " + thesis)
        details = [(tr("Daily %"), p(row['daily_pct']), value_color(row['daily_pct']))]
        if not intraday:
            details.extend([(tr("Unrealized P/L"), m(row['unrealized_pl'], signed=True), value_color(row['unrealized_pl'])),
                            (tr("Unrealized return"), p(row['return_pct']), value_color(row['return_pct'])),
                            (tr("Weight"), p(row['weight_pct'], signed=False), NEUTRAL)])
        if intraday:
            stamp = quotes[row["symbol"]].price_at
            details.append((tr("Price time (New York)"), stamp.astimezone(NY).strftime('%H:%M %Z') if stamp else tr('Data unavailable'), NEUTRAL))
        holding_html.append(email_holding(row['symbol'], m(row['price']), row['shares'], details, lang, thesis=thesis, badges=badge_html(row["symbol"])))
    if not portfolio["holdings"]:
        lines.append(tr("No current holdings."))
        holding_html.append(f"<p>{escape(tr('No current holdings.'))}</p>")
    watch_html = ""
    held_symbols = {row["symbol"] for row in portfolio["holdings"]}
    highlights = intraday_watchlist_highlights(config, quotes, held_symbols, session) if intraday else []
    if highlights:
        heading_watch = tr("Watchlist moves ≥5% (up to 3)")
        ranking = tr("Absolute daily change reaches 5%; ranked by magnitude. Normal-session snapshots, not final closes.")
        lines.extend(["", heading_watch, ranking])
        cells = []
        for quote in highlights:
            stamp = quote.price_at.astimezone(NY).strftime("%H:%M %Z") if quote.price_at else tr("Data unavailable")
            price, daily = m(quote.price), p(quote.daily_move_pct)
            lines.append(tr("{symbol}: {price} · Today {daily} · Price time {time} New York",
                            symbol=quote.symbol + badge_text(quote.symbol), price=price, daily=daily, time=stamp))
            cells.append((quote.symbol, [(price, INK), (daily, value_color(quote.daily_move_pct)), (stamp, NEUTRAL)]))
        headers = ("Code", "Last", "Day %", "Time (ET)")
        watch_html = section(heading_watch, f"<p style='font-size:12px;color:{NEUTRAL};margin:0 0 8px'>{escape(ranking)}</p>"
                             + watch_table(headers, cells))
    if not intraday and any(symbol not in held_symbols for symbol in config["watchlist"]):
        rows = watchlist_rows if watchlist_rows is not None else [
            {"Symbol": symbol, "Current Price": quotes.get(symbol, Quote(symbol)).price,
             "Daily %": quotes.get(symbol, Quote(symbol)).daily_move_pct, "5D %": None, "1M %": None}
            for symbol in config["watchlist"]]
        rows = [row for row in rows if row["Symbol"] not in held_symbols]
        lines.extend(["", tr("Watchlist summary (closing prices)")])
        watch_cells = []
        for row in rows:
            symbol, price = row["Symbol"], m(row["Current Price"])
            daily, five, month = p(row["Daily %"]), p(row["5D %"]), p(row["1M %"])
            lines.append(tr("{symbol}: {price} · Today {daily} · 5D {five} · 1M {month}",
                            symbol=symbol + badge_text(symbol), price=price, daily=daily, five=five, month=month))
            watch_cells.append((symbol, [(price, INK), (daily, value_color(row['Daily %'])),
                                         (five, value_color(row['5D %'])), (month, value_color(row['1M %']))]))
        headers = ("Code", "Last", "Day %", "5-day", "1-month")
        watch_html = section(tr("Watchlist summary (closing prices)"), watch_table(headers, watch_cells))
    alert_messages = [alert.message for alert in alerts]
    distances = target_distances(config, quotes) if not intraday else []
    unusual = []
    if intraday:
        for symbol, quote in sorted(quotes.items()):
            # Watch-only movements belong in the capped summary; configured alerts remain independent.
            if symbol in config["watchlist"] and symbol not in held_symbols:
                continue
            threshold = config["watchlist"].get(symbol, {}).get("alerts", {}).get("daily_move_pct", 5)
            if not quote.error and quote.daily_move_pct is not None and abs(quote.daily_move_pct) >= threshold:
                unusual.append(tr("{symbol}: {change} today (movement threshold {threshold:g}%)", symbol=symbol, change=p(quote.daily_move_pct), threshold=threshold))
        for symbol, entry in config["watchlist"].items():
            if symbol in held_symbols:
                continue
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

    footer_lines = lines[-3:]
    hero = email_hero(tr("Market Value"), m(portfolio["market_value"]), tr("Daily P/L"), m(portfolio["daily_pl"], signed=True),
                      right_color=value_color(portfolio["daily_pl"]), detail=p(portfolio["daily_pct"]))
    milestone_html = (f"<div aria-label='{escape(milestone_line)}' style='background:{SOFT};color:{GREEN};padding:12px 14px;border-radius:6px;margin:12px 0'>"
                      f"<div style='font-size:14px'>↗ {escape(tr('Closing NAV high'))}</div>"
                      f"<div style='font-size:11px;margin-top:4px'>{atomic(session.isoformat())} · {escape(tr('Completed NYSE session · USD'))}</div></div>") if milestone_line else ""
    if not intraday:
        labeled = [(tr("Total Cost"), m(portfolio["total_cost"]), NEUTRAL),
                   (tr("Unrealized P/L"), m(portfolio["unrealized_pl"], signed=True), value_color(portfolio["unrealized_pl"])),
                   (tr("Unrealized return"), p(portfolio["return_pct"]), value_color(portfolio["return_pct"]))]
        summary_block = ("<div style='margin:10px 0 0;border-top:1px solid #e8ece6'>"
                         + email_metrics(labeled) + "</div>")
    else:
        summary_block = ""
    demo_banner = (f"<div style='background:#fef7e0;border:1px solid #fdd663;border-radius:10px;padding:8px 12px;"
                   f"margin:0 0 10px;color:#7c5800;font-size:12px'>⚠️ {escape(tr('SIMULATED DEMO DATA — not live market prices'))}</div>") if demo else ""
    warning_banner = (f"<div style='background:#fce8e6;border:1px solid #f6aea9;border-radius:10px;padding:8px 12px;"
                      f"margin:0 0 10px;color:{RED};font-size:12px'>{escape(data_warning)}</div>") if data_warning else ""
    holdings_block = section(tr("Holdings"), "".join(holding_html))
    performance_block = ""
    if performance_rows:
        history_note = tr("History through {date}; excludes dividends and cash.", date=performance["points"][-1]["date"])
        performance_block = section(tr("Performance"),
                                    email_metrics(performance_rows)
                                    + f"<p style='margin:10px 0 0;font-size:11px;line-height:1.75;color:{NEUTRAL};text-wrap:balance'>{escape(history_note)}</p>")
    alerts_body = (f"<div style='background:#fef7e0;border-radius:8px;padding:2px 12px'>{list_rows(alert_messages)}</div>"
                   if alert_messages else f"<p style='margin:2px 0;font-size:14px;color:{NEUTRAL}'>{escape(tr('No new alerts.'))}</p>")
    alerts_block = section(tr("Alerts"), alerts_body) if alert_messages else f"<p style='color:{NEUTRAL};font-size:14px;margin:20px 0 0'>{escape(tr('No new alerts.'))}</p>"
    notable_block = (section(tr("Notable moves"), list_rows(unusual or [tr("No moves reached the threshold.")]))
                     if intraday and (unusual or not highlights) else "")
    near_block = (section(tr("Near candidate price (within 5%)" if intraday else "Target price distances (informational)"),
                          list_rows(distances or [tr("No candidates within 5% of target.")])) if distances or intraday else "")
    errors_block = (section(tr("Data availability"), f"<div style='background:#fce8e6;border-radius:8px;padding:2px 12px'>{list_rows(errors)}</div>")
                    if errors else "")
    footer = (f"<div style='font-size:11px;color:{NEUTRAL};line-height:1.75;margin:20px 0 0;padding-top:12px;border-top:1px solid {SOFT}'>"
              + "".join(f"<p style='margin:6px 0;text-wrap:pretty'>{escape(line)}</p>" for line in footer_lines) + "</div>")
    note = record_note(performance, lang)
    record_html = f"<p style='font-size:13px;color:{NEUTRAL}'>{escape(note)}</p>" if note else ""
    attachment_note = tr("JSON data is attached for your own analysis.")
    html = email_shell(heading, f"{session.isoformat()} · {timing}",
                       demo_banner + warning_banner + hero + milestone_html + summary_block
                       + record_html + holdings_block + performance_block + watch_html + alerts_block
                       + notable_block + near_block + errors_block + footer
                       + f"<p style='font-size:13px;color:{NEUTRAL}'>{escape(attachment_note)}</p>" + garden_content.html, lang,
                       closing=quote_line, closing_html=reflection_html(session, lang))
    if garden_content.text:
        lines.extend(["", garden_content.text])
    lines.extend(["", quote_line, attachment_note])
    data = report_data(session, portfolio, quotes, config, alerts, mode=mode, generated_at=generated_at,
                       demo=demo, performance=performance, watchlist_rows=watchlist_rows, quantitative_analysis=quantitative_analysis)
    if intraday:
        data["visible_watchlist_symbols"] = [quote.symbol for quote in highlights]
        data["watchlist_highlight_selection"] = "up to 3 unheld watched symbols reaching 5% absolute daily change, ranked by magnitude, ties by symbol"
    plain, html = append_data("\n".join(lines) + "\n", html, data)
    return Report(subject, plain, html, serialize_data(data), f"stockwatch-{session}-{mode.lower()}.json", garden_content.images)


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
