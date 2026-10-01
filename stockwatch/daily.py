"""Independent daily CLI: python -m stockwatch.daily."""
from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from stockwatch.alerts import evaluate, mark_delivered, report_session_key
from stockwatch.calendar import NY, active_session, latest_session
from stockwatch.i18n import error_message, language, t
from stockwatch.notifications.email import EmailDeliveryError, EmailSettings, send_report
from stockwatch.providers.base import MarketDataProvider
from stockwatch.providers.demo import DemoProvider
from stockwatch.providers.openbb_provider import OpenBBProvider
from stockwatch.performance import DEFAULT_BENCHMARK, update_history, load_history, fingerprint
from stockwatch.reports import render_report
from stockwatch.services import snapshot, watchlist_summary
from stockwatch.storage import ROOT, ValidationError, atomic_write, load_config, load_state, load_transactions, save_state

logger = logging.getLogger(__name__)


def run(*, config_path: Path, transactions_path: Path, state_path: Path, output_dir: Path,
        provider: MarketDataProvider, session: date, dry_run: bool = False, force_send: bool = False,
        demo: bool = False, sender: Callable = send_report, mode: str = "CLOSE",
        now: datetime | None = None, performance_path: Path | None = None,
        rebuild_performance: bool = False) -> int:
    session_key = report_session_key(mode)
    now = now or datetime.now(timezone.utc)
    config = load_config(config_path)
    lang = language(config)
    if mode == "INTRADAY" and not demo and active_session(now) != session:
        logger.info(t("No active NYSE session; intraday run skipped", lang))
        return 0
    transactions = load_transactions(transactions_path)
    state = load_state(state_path)
    portfolio, quotes = snapshot(config, transactions, provider, session, closing=mode == "CLOSE", intraday=mode == "INTRADAY", now=now)
    performance_path = performance_path or state_path.parent / "performance.json"
    benchmark = config["portfolio"].get("benchmark", DEFAULT_BENCHMARK)
    history = None
    if mode == "CLOSE":
        try:
            history = update_history(performance_path, transactions, provider, session, benchmark,
                                     rebuild=rebuild_performance, persist=not (dry_run or demo))
        except (ValidationError, OSError) as exc:
            logger.warning("Performance history unavailable: %s; report continues", error_message(exc, lang) if isinstance(exc, ValidationError) else type(exc).__name__)
    else:
        try:
            history = load_history(performance_path)
            if history and (history["fingerprint"] != fingerprint(transactions, benchmark) or
                            any(p["date"] >= session.isoformat() for p in history["points"])):
                history = None
        except (ValidationError, OSError):
            logger.warning("Performance history unavailable; intraday report continues")
    pending, recovered_state = evaluate(config, quotes, state, session, mode=mode)
    watch_rows = watchlist_summary(config, portfolio, quotes, provider, session) if mode == "CLOSE" else None
    report = render_report(session, portfolio, quotes, config, pending, demo=demo, mode=mode, generated_at=now, performance=history, watchlist_rows=watch_rows)
    filename = f"{session}" if mode == "CLOSE" else f"{session}-intraday"
    atomic_write(output_dir / f"{filename}.txt", report.text)
    atomic_write(output_dir / f"{filename}.html", report.html)
    logger.info(t("Report mode: {mode}", lang, mode=mode))
    logger.info(t("Report generated for {session}: {holdings} holdings, {alerts} new alerts, {unavailable} tickers unavailable",
                  lang, session=session, holdings=len(portfolio["holdings"]), alerts=len(pending), unavailable=sum(q.price is None for q in quotes.values())))
    if portfolio["market_value"] is None:
        logger.warning(t("Market data is missing; this report cannot provide a complete portfolio valuation.", lang))
    if dry_run or demo:
        logger.info(t("Preview only: no email or state changes", lang))
        return 0
    sent = state.get("_meta", {}).get(session_key)
    if sent == session.isoformat() and not force_send:
        # Suppress repeat emails but persist valid recoveries without consuming new alerts.
        save_state(state_path, recovered_state)
        logger.info(t("Report already sent for {session}; skipping email", lang, session=session))
        return 0
    if not config.get("notifications", {}).get("email_enabled", True):
        save_state(state_path, recovered_state)
        logger.info(t("Email notifications disabled; history saved, alerts remain pending", lang))
        return 0
    settings = EmailSettings.from_environment()
    if settings is None:
        save_state(state_path, recovered_state)
        logger.info(t("Email configuration missing or incomplete; delivery skipped; alerts remain pending", lang))
        return 0
    try:
        sender(report, settings)
    except EmailDeliveryError as exc:
        save_state(state_path, recovered_state)
        logger.error("%s", error_message(exc, lang))
        return 1
    save_state(state_path, mark_delivered(recovered_state, pending, session, mode=mode))
    logger.info(t("Gmail accepted report for {session}; notification state saved", lang, session=session))
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="StockWatch 每日持仓日报 / Daily portfolio report")
    result.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    result.add_argument("--transactions", type=Path, default=ROOT / "data/transactions.csv")
    result.add_argument("--state", type=Path, default=ROOT / "data/state.json")
    result.add_argument("--output-dir", type=Path, default=ROOT / "outputs")
    result.add_argument("--log-dir", type=Path, default=ROOT / "logs")
    result.add_argument("--performance", type=Path, default=ROOT / "data/performance.json")
    result.add_argument("--rebuild-performance", action="store_true", help="重新回补已完成 session 的持仓历史")
    result.add_argument("--mode", choices=["CLOSE", "INTRADAY"], default="CLOSE", help="CLOSE 收盘日报；INTRADAY 仅在正常交易时段生成盘中快照")
    result.add_argument("--dry-run", action="store_true", help="仅生成预览，不发送邮件，也不修改提醒状态")
    result.add_argument("--force-send", action="store_true", help="强制重发日报；提醒仍按原规则去重")
    result.add_argument("--scheduled", action="store_true", help="按模式检查纽约当天的交易时段，休市或不合时段则跳过")
    result.add_argument("--demo", action="store_true", help="离线演示，使用模拟数据，不发送邮件或修改状态")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(args.log_dir / "daily.log", encoding="utf-8")])
    logging.Formatter.converter = __import__("time").gmtime
    lang = "zh-CN"
    config_path = ROOT / "examples/config.yaml" if args.demo else args.config
    try:
        lang = language(load_config(config_path))
        provider = DemoProvider() if args.demo else OpenBBProvider()
        now = datetime.now(timezone.utc)
        if args.demo:
            now = datetime.combine(provider.session, datetime.min.time(), NY).replace(hour=10 if args.mode == "INTRADAY" else 18, minute=30)
        session = provider.session if args.demo else (active_session(now) if args.mode == "INTRADAY" else latest_session(now, scheduled=args.scheduled))
        if session is None:
            logger.info(t("No active NYSE session; intraday run skipped" if args.mode == "INTRADAY" else "No completed New York session today; scheduled run skipped", lang))
            return 0
        logger.info(t("Starting {session} report with {source}", lang, session=session, source=t("offline demo", lang) if args.demo else "OpenBB / yfinance"))
        return run(config_path=config_path,
                   transactions_path=ROOT / "examples/transactions.csv" if args.demo else args.transactions,
                   state_path=args.state, output_dir=args.output_dir, provider=provider, session=session,
                   dry_run=args.dry_run, force_send=args.force_send, demo=args.demo, mode=args.mode, now=now, performance_path=args.performance,
                   rebuild_performance=args.rebuild_performance)
    except (ValidationError, OSError, ValueError) as exc:
        # Config validation messages are safe; raw OS errors are not necessary in logs.
        message = error_message(exc, lang) if isinstance(exc, ValidationError) else type(exc).__name__
        logger.error(t("Daily job failed: {error}", lang, error=message))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
