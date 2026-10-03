"""Independent daily CLI: python -m stockwatch.daily."""
from __future__ import annotations

import argparse
import logging
import time
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from stockwatch.alerts import evaluate, mark_delivered, report_session_key
from stockwatch.calendar import NY, active_session, latest_session
from stockwatch.i18n import error_message, language, t
from stockwatch.imports.gmail import sync_hsbc
from stockwatch.imports.hsbc import HSBCSyncError
from stockwatch.notifications.email import EmailDeliveryError, EmailSettings, send_report
from stockwatch.providers.base import MarketDataProvider
from stockwatch.providers.demo import DemoProvider
from stockwatch.providers.openbb_provider import OpenBBProvider
from stockwatch.performance import DEFAULT_BENCHMARK, update_history, load_history, fingerprint
from stockwatch.reports import render_report, render_failure
from stockwatch.services import snapshot, watchlist_summary
from stockwatch.storage import ROOT, ValidationError, atomic_write, load_config, load_state, load_transactions, save_state

logger = logging.getLogger(__name__)


def run(*, config_path: Path, transactions_path: Path, state_path: Path, output_dir: Path,
        provider: MarketDataProvider, session: date, dry_run: bool = False, force_send: bool = False,
        demo: bool = False, sender: Callable = send_report, mode: str = "CLOSE",
        now: datetime | None = None, performance_path: Path | None = None,
        rebuild_performance: bool = False, close_attempts: int = 3, close_retry_seconds: float = 120,
        sleeper: Callable = time.sleep, importer: Callable = sync_hsbc,
        import_state_path: Path | None = None, delivery_day: date | None = None) -> int:
    session_key = report_session_key(mode) if mode in ("CLOSE", "INTRADAY") else f"last_{mode.lower()}_session"
    now = now or datetime.now(timezone.utc)
    config = load_config(config_path)
    lang = language(config)
    if mode == "INTRADAY" and not demo and active_session(now) != session:
        logger.info(t("No active NYSE session; intraday run skipped", lang))
        return 0
    if not 1 <= close_attempts <= 3 or not 0 <= close_retry_seconds <= 180:
        raise ValueError("Invalid close retry limits")
    state = load_state(state_path)
    settings = EmailSettings.from_environment()
    mail_enabled = config.get("notifications", {}).get("email_enabled", True)
    sent_before = state.get("_meta", {}).get(session_key) == session.isoformat()
    import_state_path = import_state_path or state_path.parent / "hsbc_imports.json"
    if not demo:
        try:
            import_config = deepcopy(config)
            options = import_config.setdefault("imports", {}).setdefault("hsbc", {})
            options["lookback_days"] = min(3, options.get("lookback_days", 3))
            result = importer(import_config, transactions_path, import_state_path, dry_run=dry_run)
            if not result.get("disabled"):
                logger.info(t("HSBC sync: {imported} imported, {duplicates} duplicates, {skipped} skipped", lang, **result))
        except (HSBCSyncError, ValidationError, OSError) as exc:
            logger.error("HSBC sync failed (%s); portfolio report withheld", type(exc).__name__)
            report = render_failure(session, "HSBC sync failed; the portfolio ledger may be incomplete.", [], 1, lang, mode=mode if mode in ("CLOSE", "INTRADAY") else "CLOSE")
            return deliver_failure(report, state, state_path, output_dir, session,
                                   f"last_hsbc_error_{mode.lower()}_session", settings if mail_enabled else None,
                                   sender, dry_run=dry_run)
    if mode in ("WEEKLY", "MONTHLY"):
        from stockwatch.periodic import run_summary
        return run_summary(mode=mode, config=config, session=session, now=now, provider=provider,
                           transactions_path=transactions_path, state_path=state_path, output_dir=output_dir,
                           performance_path=performance_path or state_path.parent / "performance.json",
                           dry_run=dry_run, demo=demo, force_send=force_send, sender=sender, delivery_day=delivery_day)
    transactions = load_transactions(transactions_path)
    attempts = close_attempts if mode == "CLOSE" and not (dry_run or demo or sent_before and not force_send) and mail_enabled and settings else 1
    if state.get("_meta", {}).get("last_close_error_session") == session.isoformat() and not force_send:
        attempts = 1
    missing = []
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            clear = getattr(provider, "clear_cache", None)
            if clear:
                clear()
        portfolio, quotes = snapshot(config, transactions, provider, session, closing=mode == "CLOSE", intraday=mode == "INTRADAY", now=now)
        missing = [symbol for symbol, quote in quotes.items() if quote.price is None or
                   quote.previous_close is None or quote.previous_close <= 0 or quote.error or quote.session != session]
        if mode != "CLOSE" or not missing:
            break
        logger.warning(t("Close data incomplete: check {attempt}/{attempts}; unavailable: {symbols}", lang,
                         attempt=attempt, attempts=attempts, symbols=", ".join(missing)))
        if attempt < attempts:
            logger.info(t("Retrying close data in {seconds:g} seconds; no email or alert state consumed", lang, seconds=close_retry_seconds))
            sleeper(close_retry_seconds)
    if mode == "CLOSE" and missing and not (dry_run or demo) and mail_enabled and settings and not (sent_before and not force_send):
        report = render_failure(session, "Close data remains unavailable; the daily report was not sent.", missing, attempts, lang)
        return deliver_failure(report, state, state_path, output_dir, session, "last_close_error_session", settings, sender)

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


def deliver_failure(report, state, state_path, output_dir, session, key, settings, sender, *, dry_run=False):
    """An error notification is never a delivered portfolio report or price alert."""
    atomic_write(output_dir / f"{session}-{key}-error.txt", report.text)
    atomic_write(output_dir / f"{session}-{key}-error.html", report.html)
    if dry_run:
        return 2
    if settings is None or state.get("_meta", {}).get(key) == session.isoformat():
        logger.warning("Error report withheld: email unavailable/disabled or already notified for this session")
        return 2
    try:
        sender(report, settings)
    except EmailDeliveryError:
        logger.error("Error report delivery failed; notification state unchanged")
        return 1
    updated = deepcopy(state)
    updated.setdefault("_meta", {})[key] = session.isoformat()
    save_state(state_path, updated)
    logger.warning("Error notification accepted; portfolio report and price alerts remain pending")
    return 2


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="StockWatch 每日持仓日报 / Daily portfolio report")
    result.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    result.add_argument("--transactions", type=Path, default=ROOT / "data/transactions.csv")
    result.add_argument("--state", type=Path, default=ROOT / "data/state.json")
    result.add_argument("--output-dir", type=Path, default=ROOT / "outputs")
    result.add_argument("--log-dir", type=Path, default=ROOT / "logs")
    result.add_argument("--performance", type=Path, default=ROOT / "data/performance.json")
    result.add_argument("--rebuild-performance", action="store_true", help="重新回补已完成 session 的持仓历史")
    result.add_argument("--close-attempts", type=int, choices=range(1, 4), default=3)
    result.add_argument("--close-retry-seconds", type=int, metavar="SECONDS", choices=range(60, 181), default=120,
                        help="收盘缺价重试间隔，默认120秒，可选60至180秒")
    result.add_argument("--import-state", type=Path, default=None)
    result.add_argument("--lookback-days", type=int, metavar="DAYS", choices=range(1, 366), default=None, help="仅 --sync-only 的历史补录范围，不修改日常配置")
    result.add_argument("--sync-only", action="store_true", help="仅同步汇丰成交，不生成或发送日报")
    result.add_argument("--skip-hsbc", action="store_true", help="本次不读取 Gmail 或导入交易")
    result.add_argument("--mode", choices=["CLOSE", "INTRADAY", "WEEKLY", "MONTHLY"], default="CLOSE", help="CLOSE 收盘日报；INTRADAY 仅在正常交易时段生成盘中快照")
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
        if args.sync_only:
            if args.demo or args.skip_hsbc:
                logger.info("HSBC sync skipped in demo/skip mode")
                return 0
            config = load_config(config_path)
            try:
                result = sync_hsbc(config, args.transactions, args.import_state or args.state.parent / "hsbc_imports.json", dry_run=args.dry_run, lookback_days=args.lookback_days)
            except (HSBCSyncError, OSError) as exc:
                logger.error("HSBC sync failed (%s)", type(exc).__name__)
                return 1
            logger.info(t("HSBC sync: {imported} imported, {duplicates} duplicates, {skipped} skipped", lang, **result))
            return 0
        config = load_config(config_path)
        if args.scheduled and not args.demo:
            from stockwatch.report_settings import scheduled_due
            if not scheduled_due(config, args.mode, datetime.now(timezone.utc)):
                logger.info("Report disabled or outside configured schedule; skipped")
                return 0
        provider = DemoProvider() if args.demo else OpenBBProvider()
        now = datetime.now(timezone.utc)
        if args.demo:
            now = datetime.combine(provider.session, datetime.min.time(), NY).replace(hour=10 if args.mode == "INTRADAY" else 18, minute=30)
        session = provider.session if args.demo else (active_session(now) if args.mode == "INTRADAY" else latest_session(now, scheduled=args.scheduled and args.mode not in ("WEEKLY", "MONTHLY")))
        if session is None:
            logger.info(t("No active NYSE session; intraday run skipped" if args.mode == "INTRADAY" else "No completed New York session today; scheduled run skipped", lang))
            return 0
        logger.info(t("Starting {session} report with {source}", lang, session=session, source=t("offline demo", lang) if args.demo else "OpenBB / yfinance"))
        from stockwatch.report_settings import report_timezone
        return run(config_path=config_path,
                   transactions_path=ROOT / "examples/transactions.csv" if args.demo else args.transactions,
                   state_path=args.state, output_dir=args.output_dir, provider=provider, session=session,
                   dry_run=args.dry_run, force_send=args.force_send, demo=args.demo, mode=args.mode, now=now, performance_path=args.performance,
                   delivery_day=(provider.session if args.demo else now.astimezone(report_timezone(args.mode)).date()) if args.mode in ("WEEKLY", "MONTHLY") else None,
                   rebuild_performance=args.rebuild_performance, close_attempts=args.close_attempts,
                   close_retry_seconds=args.close_retry_seconds, import_state_path=args.import_state,
                   **({"importer": lambda *a, **kw: {"disabled": True}} if args.skip_hsbc else {}))
    except (ValidationError, OSError, ValueError) as exc:
        # Config validation messages are safe; raw OS errors are not necessary in logs.
        message = error_message(exc, lang) if isinstance(exc, ValidationError) else type(exc).__name__
        logger.error(t("Daily job failed: {error}", lang, error=message))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
