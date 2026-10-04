"""Validated CSV/YAML storage and atomic file replacement."""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from stockwatch.i18n import LANGUAGES, UserFacingError

LEGACY_COLUMNS = ["date", "symbol", "side", "shares", "price", "note"]
COLUMNS = LEGACY_COLUMNS + ["fee"]
ROOT = Path(__file__).resolve().parents[1]
WATCH_STATUSES = ("watching", "interested", "waiting")


class ValidationError(UserFacingError, ValueError):
    """User data is invalid; callers should display the message."""


def number(value: Any, label: str, *, allow_zero: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError("{label} must be a finite positive number.", label=label) from None
    if not result.is_finite() or result < 0 or (result == 0 and not allow_zero):
        message = "{label} must be a finite non-negative number." if allow_zero else "{label} must be a finite positive number."
        raise ValidationError(message, label=label)
    return result


def ticker(value: Any) -> str:
    result = str(value).strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,19}", result) or result == "_META":
        raise ValidationError("Invalid US symbol: {symbol!r}", symbol=result)
    return result


def config_number(value: Any, label: str, *, allow_zero: bool = False) -> float:
    decimal = number(value, label, allow_zero=allow_zero)
    result = float(decimal)
    if not math.isfinite(result) or (result == 0 and decimal != 0):
        raise ValidationError("{label} is outside the supported numeric range.", label=label)
    return result


@dataclass(frozen=True)
class Transaction:
    date: date
    symbol: str
    side: str
    shares: Decimal
    price: Decimal
    note: str = ""
    fee: Decimal = Decimal(0)

    def row(self) -> dict[str, str]:
        return dict(zip(COLUMNS, [self.date.isoformat(), self.symbol, self.side,
                                 str(self.shares), str(self.price), self.note, str(self.fee)]))


def validate_transactions(rows: list[dict]) -> list[Transaction]:
    result = []
    for i, row in enumerate(rows, 2):
        try:
            value = str(row.get("date", ""))
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError
            day = date.fromisoformat(value)
        except ValueError:
            raise ValidationError("Row {row}: date must be YYYY-MM-DD.", row=i) from None
        side = str(row.get("side", "")).strip().upper()
        if side not in {"BUY", "SELL"}:
            raise ValidationError("Row {row}: side must be BUY or SELL.", row=i)
        result.append(Transaction(day, ticker(row.get("symbol", "")), side,
                                  number(row.get("shares"), f"Row {i} shares"),
                                  number(row.get("price"), f"Row {i} price"),
                                  str(row.get("note") or ""),
                                  number(row.get("fee") or "0", f"Row {i} fee", allow_zero=True)))
        if side == "SELL" and result[-1].fee > result[-1].shares * result[-1].price:
            raise ValidationError("Sell fee cannot exceed sale proceeds.")
    # Validate the entire ledger, including future transactions, without reordering storage.
    balances: dict[str, Decimal] = {}
    for tx in sorted(result, key=lambda tx: tx.date):
        held = balances.get(tx.symbol, Decimal(0))
        if tx.side == "SELL" and tx.shares > held:
            raise ValidationError("{date} {symbol}: cannot sell {shares}; only {held} shares held.", date=tx.date, symbol=tx.symbol, shares=tx.shares, held=held)
        balances[tx.symbol] = held + tx.shares * (1 if tx.side == "BUY" else -1)
    return result


def load_transactions(path: Path) -> list[Transaction]:
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames not in (LEGACY_COLUMNS, COLUMNS):
            raise ValidationError("CSV columns must be: {columns}", columns=','.join(COLUMNS))
        rows = list(reader)
        if any(None in row or any(row[key] is None for key in reader.fieldnames) for row in rows):
            raise ValidationError("CSV has rows with missing or extra columns.")
        return validate_transactions(rows)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=path.parent, suffix=".tmp", delete=False) as file:
            temp_path = Path(file.name)
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def save_transactions(path: Path, rows: list[dict]) -> None:
    transactions = validate_transactions(rows)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(tx.row() for tx in transactions)
    atomic_write(path, output.getvalue())


def validate_config(raw: Any) -> dict:
    if not isinstance(raw, dict) or set(raw) - {"portfolio", "watchlist", "notifications", "imports", "reports", "scheduler"}:
        raise ValidationError("Config supports portfolio, watchlist, notifications, imports, reports and scheduler only.")
    portfolio = raw.get("portfolio", {})
    if not isinstance(portfolio, dict) or set(portfolio) - {"base_currency", "language", "benchmark"} or portfolio.get("base_currency", "USD") != "USD":
        raise ValidationError("Only portfolio.base_currency: USD is supported.")
    lang = portfolio.get("language", "en")
    if not isinstance(lang, str) or lang not in LANGUAGES:
        raise ValidationError("Language must be zh-CN or en.")
    scheduler = raw.get("scheduler", {})
    if not isinstance(scheduler, dict) or set(scheduler) - {"trigger"}:
        raise ValidationError("scheduler supports trigger only.")
    trigger = scheduler.get("trigger", "native")
    if trigger not in ("native", "cron-job.org"):
        raise ValidationError("scheduler.trigger must be native or cron-job.org.")
    watchlist = raw.get("watchlist", {})
    if not isinstance(watchlist, dict):
        raise ValidationError("watchlist must map symbols to settings.")
    clean = {"portfolio": {"base_currency": "USD", "language": lang}, "watchlist": {}}
    if trigger != "native":
        clean["scheduler"] = {"trigger": trigger}
    if "benchmark" in portfolio:
        clean["portfolio"]["benchmark"] = ticker(portfolio["benchmark"])
    if "reports" in raw:
        from stockwatch.report_settings import report_settings
        try:
            clean["reports"] = report_settings(raw["reports"])
        except ValueError as exc:
            raise ValidationError(str(exc)) from None
    if "notifications" in raw:
        notifications = raw["notifications"]
        if not isinstance(notifications, dict) or set(notifications) - {"email_enabled"} or not isinstance(notifications.get("email_enabled", True), bool):
            raise ValidationError("notifications.email_enabled must be true or false.")
        clean["notifications"] = {"email_enabled": notifications.get("email_enabled", True)}
    if "imports" in raw:
        imports = raw["imports"]
        if not isinstance(imports, dict) or set(imports) - {"hsbc"}:
            raise ValidationError("imports supports hsbc only.")
        hsbc = imports.get("hsbc", {})
        if not isinstance(hsbc, dict) or set(hsbc) - {"enabled", "allow_email_date", "lookback_days"}:
            raise ValidationError("Invalid HSBC import settings.")
        if any(not isinstance(hsbc.get(key, key == "allow_email_date"), bool) for key in ("enabled", "allow_email_date")):
            raise ValidationError("HSBC import switches must be true or false.")
        days = hsbc.get("lookback_days", 3)
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
            raise ValidationError("HSBC lookback must be between 1 and 365 days.")
        clean["imports"] = {"hsbc": {"enabled": hsbc.get("enabled", False),
                                     "allow_email_date": hsbc.get("allow_email_date", True), "lookback_days": days}}
    for symbol, entry in watchlist.items():
        symbol = ticker(symbol)
        if symbol in clean["watchlist"]:
            raise ValidationError("Duplicate symbol: {symbol}", symbol=symbol)
        if not isinstance(entry, dict) or set(entry) - {"thesis", "target_shares", "alerts", "status", "buy_below"}:
            raise ValidationError("{symbol}: unsupported watchlist fields.", symbol=symbol)
        if not isinstance(entry.get("thesis", ""), str):
            raise ValidationError("{symbol}: thesis must be text.", symbol=symbol)
        item = {"thesis": entry.get("thesis", "")}
        if "status" in entry:
            if not isinstance(entry["status"], str) or entry["status"] not in WATCH_STATUSES:
                raise ValidationError("{symbol}: status must be watching, interested or waiting.", symbol=symbol)
            item["status"] = entry["status"]
        if "buy_below" in entry:
            item["buy_below"] = config_number(entry["buy_below"], "buy_below")
        if "target_shares" in entry:
            item["target_shares"] = config_number(entry["target_shares"], "target_shares", allow_zero=True)
        alerts = entry.get("alerts", {})
        if not isinstance(alerts, dict) or set(alerts) - {"below", "daily_move_pct"}:
            raise ValidationError("{symbol}: alerts support below and daily_move_pct only.", symbol=symbol)
        if alerts:
            item["alerts"] = {key: config_number(value, key) for key, value in alerts.items()}
        clean["watchlist"][symbol] = item
    return clean


def load_config(path: Path) -> dict:
    try:
        return validate_config(yaml.safe_load(path.read_text(encoding="utf-8")))
    except yaml.YAMLError:
        raise ValidationError("Invalid YAML configuration.") from None


def save_config(path: Path, config: dict) -> None:
    atomic_write(path, yaml.safe_dump(validate_config(config), sort_keys=False, allow_unicode=True))


def validate_state(state: Any) -> dict:
    if not isinstance(state, dict):
        raise ValidationError("State must be a JSON object.")
    for symbol, rules in state.items():
        if not isinstance(rules, dict):
            raise ValidationError("Invalid state rule map.")
        if symbol == "_meta":
            for key in ("last_report_session", "last_intraday_session", "last_close_error_session", "last_hsbc_error_close_session", "last_hsbc_error_intraday_session"):
                sent = rules.get(key)
                if sent is not None:
                    try:
                        date.fromisoformat(sent)
                    except (ValueError, TypeError):
                        raise ValidationError("Invalid report session in state.") from None
            for key, pattern in (("last_weekly_period", r"\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])"), ("last_monthly_period", r"\d{4}-(?:0[1-9]|1[0-2])")):
                if key in rules and (not isinstance(rules[key], str) or not re.fullmatch(pattern, rules[key])):
                    raise ValidationError("Invalid report session in state.")
            continue
        ticker(symbol)
        for rule, item in rules.items():
            if not isinstance(item, dict) or not isinstance(item.get("triggered"), bool):
                raise ValidationError("Invalid state for {symbol}/{rule}.", symbol=symbol, rule=rule)
            notified = item.get("last_notified")
            if notified is not None:
                try:
                    date.fromisoformat(notified)
                except (ValueError, TypeError):
                    raise ValidationError("Invalid notification date for {symbol}.", symbol=symbol) from None
    return state


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return validate_state(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        raise ValidationError("Invalid JSON state; restore a valid file before running.") from None


def save_state(path: Path, state: dict) -> None:
    content = json.dumps(validate_state(state), indent=2, sort_keys=True) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        atomic_write(path, content)


def ensure_user_files(root: Path) -> None:
    """First-run empty defaults; never replace existing personal files."""
    config = root / "config.yaml"
    if not config.exists():
        template = root / "config.example.yaml"
        save_config(config, load_config(template))
    ledger = root / "data/transactions.csv"
    if not ledger.exists():
        save_transactions(ledger, [])
