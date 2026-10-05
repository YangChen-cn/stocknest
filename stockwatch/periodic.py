"""Weekly/monthly summaries reuse daily market, performance and email services."""
import logging
from datetime import date
from decimal import Decimal
from html import escape

from stockwatch.calendar import previous_session
from stockwatch.i18n import language, t
from stockwatch.presentation import INK, NEUTRAL, BORDER, SOFT, email_hero, email_holding, email_metrics, email_shell, email_section, reflection, reflection_html, record_note, value_color
from stockwatch.garden import Garden, build_garden
from stockwatch.garden_render import garden_view
from stockwatch.notifications.email import EmailDeliveryError, EmailSettings, send_report
from stockwatch.performance import DEFAULT_BENCHMARK, update_history
from stockwatch.report_data import append_data, report_data, serialize_data
from stockwatch.report_settings import summary_period
from stockwatch.reports import Report, money, percent
from stockwatch.services import snapshot
from stockwatch.storage import atomic_write, load_state, load_transactions, save_state

logger = logging.getLogger(__name__)


def period_metrics(history, start, end):
    points = history.get("points", []) if history else []
    baseline = previous_session(start)
    # For a newly established portfolio, start at its actual baseline, not a fake price.
    if points and baseline < date.fromisoformat(points[0]["date"]) <= end:
        baseline = date.fromisoformat(points[0]["date"])
    window = [p for p in points if baseline.isoformat() <= p["date"] <= end.isoformat()]
    result = {"start": start.isoformat(), "end": end.isoformat(), "baseline": baseline.isoformat(),
              "portfolio_return_pct": None, "benchmark_return_pct": None, "excess_pp": None,
              "profit_usd": None, "inflow_usd": None, "outflow_usd": None, "fees_usd": None,
              "realized_pl_usd": None}
    if not window or window[0]["date"] != baseline.isoformat() or window[-1]["date"] != end.isoformat():
        return result
    first, last = window[0], window[-1]
    for label, key in (("portfolio_return_pct", "nav"), ("benchmark_return_pct", "benchmark_nav")):
        if first[key] and all(p[key] is not None for p in window):
            result[label] = (Decimal(str(last[key])) / Decimal(str(first[key])) - 1) * 100
    if result["portfolio_return_pct"] is not None and result["benchmark_return_pct"] is not None:
        result["excess_pp"] = result["portfolio_return_pct"] - result["benchmark_return_pct"]
    for label, key in (("inflow_usd", "inflow"), ("outflow_usd", "outflow"), ("profit_usd", "daily_pl")):
        if all(p[key] is not None for p in window[1:]):
            result[label] = sum((Decimal(str(p[key])) for p in window[1:]), Decimal(0))
    for label, key in (("fees_usd", "fees"), ("realized_pl_usd", "realized_pl")):
        result[label] = Decimal(str(last[key])) - Decimal(str(first[key]))
    return result


def render_summary(mode, session, portfolio, quotes, config, history, now, demo=False, *, garden: Garden | None = None):
    start, end, key = summary_period(mode, session)
    metrics = period_metrics(history, start, end)
    lang = language(config)
    title = t("StockWatch Weekly" if mode == "WEEKLY" else "StockWatch Monthly", lang)
    labels = [("Market Value", portfolio["market_value"], False), ("Period holdings return", metrics["portfolio_return_pct"], True),
              ("Benchmark price return", metrics["benchmark_return_pct"], True), ("Excess return (pp)", metrics["excess_pp"], True), ("Period P/L", metrics["profit_usd"], False),
              ("Buy contributions", metrics["inflow_usd"], False), ("Net sale withdrawals", metrics["outflow_usd"], False),
              ("Realized P/L", metrics["realized_pl_usd"], False), ("Fees", metrics["fees_usd"], False)]
    lines = [title, f"{start} — {end}", t("Holdings only; excluding dividends. Returns adjust for buy/sell flows.", lang), t("If established during this period, returns begin with the first contribution. Summary runs do not trigger alerts.", lang)]
    rows = []
    holding_rows = []
    for label, value, is_percent in labels:
        if label == "Excess return (pp)" and value is not None:
            formatted = f"{value:+.2f} {t('pp', lang)}"
        else:
            formatted = percent(value, lang=lang) if is_percent else money(value, signed=label in ("Period P/L", "Realized P/L"), lang=lang)
        lines.append(f"{t(label, lang)}: {formatted}")
        color = value_color(value) if is_percent or label in ("Period P/L", "Realized P/L") else NEUTRAL
        rows.append((t(label, lang), formatted, color))
    lines.extend(["", t("Holdings", lang)])
    for holding in portfolio["holdings"]:
        symbol = holding["symbol"]
        line = f"{symbol}: {money(holding['price'], lang=lang)} · {holding['shares']} {t('Shares', lang)} · {t('Market Value', lang)} {money(holding['market_value'], lang=lang)} · {t('Return %', lang)} {percent(holding['return_pct'], lang=lang)}"
        lines.append(line)
        thesis = config["watchlist"].get(symbol, {}).get("thesis", "").strip()
        if thesis:
            lines.append(thesis)
        holding_rows.append(email_holding(symbol, money(holding['price'], lang=lang), holding['shares'],
                                         [(t('Market Value', lang), money(holding['market_value'], lang=lang), INK),
                                          (t('Return %', lang), percent(holding['return_pct'], lang=lang), value_color(holding['return_pct']))], lang, thesis=thesis))
    if demo:
        lines.append(t("Demo mode", lang))
    if not holding_rows:
        holding_rows.append(f"<p>{escape(t('No current holdings.', lang))}</p>")
    notice = f"<p style='background:#fff4d9;padding:8px 12px'>{escape(t('SIMULATED DEMO DATA — not live market prices', lang))}</p>" if demo else ""
    intro = "".join(f"<p style='color:{NEUTRAL};font-size:11px;line-height:1.75;margin:6px 0;text-wrap:pretty'>{escape(line)}</p>" for line in lines[2:4])
    note = record_note(history, lang)
    closing = reflection(end, lang)
    garden_content = garden_view(garden, lang)
    hero = email_hero(t('Market Value', lang), money(portfolio['market_value'], lang=lang),
                      t('Period holdings return', lang), percent(metrics['portfolio_return_pct'], lang=lang),
                      right_color=value_color(metrics['portfolio_return_pct']))
    content = notice + hero + email_metrics(rows[2:])
    content += email_section(t('Holdings', lang), ''.join(holding_rows)) + intro
    if note:
        content += f"<p style='color:{NEUTRAL};font-size:13px'>{escape(note)}</p>"
    content += f"<p style='font-size:13px;color:{NEUTRAL}'>{escape(t('JSON data is attached for your own analysis.', lang))}</p>"
    content += garden_content.html
    html = email_shell(title, f"{start} — {end}", content, lang, closing=closing, closing_html=reflection_html(end, lang))
    if garden_content.text:
        lines.extend(["", garden_content.text])
    lines.extend(["", closing, t('JSON data is attached for your own analysis.', lang)])
    data = report_data(end, portfolio, quotes, config, [], mode=mode, generated_at=now, demo=demo, performance=history)
    data['report_type'] = 'period_summary'
    data['period_summary'] = {k: str(v) if isinstance(v, Decimal) else v for k, v in metrics.items()}
    text, html = append_data('\n'.join(lines), html, data)
    return Report(f"{title} | {percent(metrics['portfolio_return_pct'], lang=lang)} | {key}", text, html,
                  serialize_data(data), f"stockwatch-{mode.lower()}-{key}.json", garden_content.images)


def run_summary(*, mode, config, session, now, provider, transactions_path, state_path, output_dir,
                performance_path, dry_run=False, demo=False, force_send=False, sender=send_report, delivery_day=None):
    reference = delivery_day or session
    _, end, key = summary_period(mode, reference)
    if end > session:
        logger.warning("%s target close is not completed; summary withheld", mode)
        return 1
    state = load_state(state_path)
    state_key = f"last_{mode.lower()}_period"
    if state.get('_meta', {}).get(state_key) == key and not force_send and not (dry_run or demo):
        logger.info("%s already sent for %s", mode, key)
        return 0
    transactions = load_transactions(transactions_path)
    history = update_history(performance_path, transactions, provider, end,
                             config['portfolio'].get('benchmark', DEFAULT_BENCHMARK), persist=False)
    portfolio, quotes = snapshot(config, transactions, provider, end, closing=True, now=now)
    start = summary_period(mode, reference)[0]
    garden = build_garden(transactions, portfolio, end, mode=mode, history=history,
                          benchmark=config['portfolio'].get('benchmark', DEFAULT_BENCHMARK),
                          period_start=start, period_return=period_metrics(history, start, end)['portfolio_return_pct'])
    report = render_summary(mode, reference, portfolio, quotes, config, history, now, demo, garden=garden)
    atomic_write(output_dir / f"{mode.lower()}-{key}.txt", report.text)
    atomic_write(output_dir / f"{mode.lower()}-{key}.html", report.html)
    if dry_run or demo:
        return 0
    # Never label an incomplete period or closing snapshot as a complete summary.
    metrics = period_metrics(history, *summary_period(mode, reference)[:2])
    held = {row['symbol'] for row in portfolio['holdings']}
    invalid_prices = any(quotes[symbol].error or quotes[symbol].price is None or quotes[symbol].session != end for symbol in held)
    if invalid_prices or portfolio['market_value'] is None or any(tx.date <= end for tx in transactions) and metrics['portfolio_return_pct'] is None:
        logger.error("%s incomplete; summary withheld and notification state unchanged", mode)
        return 1
    settings = EmailSettings.from_environment()
    if not config.get('notifications', {}).get('email_enabled', True) or not settings:
        logger.info("%s generated; email disabled or unconfigured", mode)
        return 0
    try:
        sender(report, settings)
    except EmailDeliveryError:
        logger.error("%s email delivery failed; state unchanged", mode)
        return 1
    state.setdefault('_meta', {})[state_key] = key
    save_state(state_path, state)
    return 0
