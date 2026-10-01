"""Strict HSBC HK completed-order parser and CSV-first idempotent import."""
from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

from stockwatch.calendar import NY
from stockwatch.history import sessions
from stockwatch.i18n import UserFacingError
from stockwatch.storage import (Transaction, ValidationError, atomic_write, load_transactions,
                                number, save_transactions, ticker, validate_transactions)

SENDER = "hsbc@notification.hsbc.com.hk"
logger = logging.getLogger(__name__)
MARKER = re.compile(r"\[HSBC:([A-Z0-9-]{3,40})\]")


class HSBCSyncError(UserFacingError, RuntimeError):
    pass


class ReceiptSkipped(ValueError):
    """Reason codes only; never expose raw bank messages."""


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if not self.hidden and tag in {"p", "br", "li", "tr", "div"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in {"p", "li", "tr", "div"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(body: str) -> str:
    parser = _Text()
    parser.feed(body)
    return "\n".join(re.sub(r"\s+", " ", line).strip() for line in "".join(parser.parts).splitlines() if line.strip())


@dataclass(frozen=True)
class BankMessage:
    sender: str
    subject: str
    sent_at: str
    authentication: str
    body: str
    message_id: str = ""


@dataclass(frozen=True)
class Execution:
    trade_id: str
    transaction: Transaction
    date_source: str


def _field(text: str, label: str) -> str:
    values = re.findall(r"^\s*[•●\-]?\s*" + label + r"\s*[:：]\s*(.+?)\s*$", text, re.M)
    if len(values) != 1:
        raise ReceiptSkipped("missing_or_ambiguous_field")
    return values[0]


def _decimal(value: str, *, zero=False) -> Decimal:
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", value):
        raise ReceiptSkipped("invalid_number")
    try:
        return number(value.replace(",", ""), "HSBC value", allow_zero=zero)
    except ValidationError:
        raise ReceiptSkipped("invalid_number") from None


def parse_execution(message: BankMessage, *, allow_email_date: bool = False,
                    date_override: date | None = None) -> Execution:
    if parseaddr(message.sender)[1].lower() != SENDER:
        raise ReceiptSkipped("untrusted_sender")
    auth = " ".join(message.authentication.lower().split())
    if (not auth.startswith("mx.google.com;") or
            not re.search(r"dkim=pass\b[^;]*header\.i=@hsbc\.com\.hk(?![A-Za-z0-9.-])", auth) or
            not re.search(r"dmarc=pass\b[^;]*header\.from=hsbc\.com\.hk(?![A-Za-z0-9.-])", auth)):
        raise ReceiptSkipped("authentication_not_confirmed")
    if len(message.body) > 1_000_000:
        raise ReceiptSkipped("message_too_large")
    subject = re.fullmatch(r"全部執行\s*[:：]\s*(買入|賣出)\s+([A-Z][A-Z0-9.\-]{0,19})\s*[:：].*?"
                           r"\(交易編號\s*[:：]\s*([A-Z0-9-]{3,40})\)\s*", message.subject)
    if not subject:
        raise ReceiptSkipped("unsupported_subject_or_status")
    text = plain_text(message.body)
    trade_id = _field(text, "交易編號")
    if trade_id != subject[3] or _field(text, "交易狀況") != "全部執行":
        raise ReceiptSkipped("inconsistent_order_or_status")
    side_text = _field(text, "指示類別")
    if side_text != subject[1]:
        raise ReceiptSkipped("inconsistent_side")
    stock = _field(text, r"股票名稱\s*/\s*股票編號")
    stock_match = re.fullmatch(r".+?\s*\(([A-Z][A-Z0-9.\-]{0,19})\)", stock)
    if not stock_match or stock_match[1] != subject[2]:
        raise ReceiptSkipped("inconsistent_symbol")
    shares = _decimal(_field(text, r"已成交數量\(股/單位\)"))
    total = _decimal(_field(text, r"共成交數量\(股/單位\)"))
    remaining = _decimal(_field(text, r"餘下數量\(股/單位\)"), zero=True)
    if remaining or total != shares:
        # A final partial fill's price cannot price the cumulative quantity.
        raise ReceiptSkipped("partial_or_multiple_fills")
    price_match = re.fullmatch(r"USD\s*(.+)", _field(text, "成交價"))
    if not price_match:
        raise ReceiptSkipped("unsupported_currency")
    price = _decimal(price_match[1])
    explicit = re.findall(r"^\s*[•●\-]?\s*(?:交易日期|成交日期)\s*[:：]\s*(.+?)\s*$", text, re.M)
    if len(explicit) > 1:
        raise ReceiptSkipped("ambiguous_date")
    source = "explicit"
    try:
        if explicit:
            day = date.fromisoformat(explicit[0])
        elif date_override is not None:
            day, source = date_override, "user_confirmed"
        elif allow_email_date:
            stamp = parsedate_to_datetime(message.sent_at)
            if stamp.tzinfo is None:
                raise ValueError
            day, source = stamp.astimezone(NY).date(), "email_new_york"
        else:
            raise ReceiptSkipped("missing_trade_date")
    except ReceiptSkipped:
        raise
    except (ValueError, TypeError, OverflowError):
        raise ReceiptSkipped("invalid_date") from None
    if day not in sessions(day, day):
        raise ReceiptSkipped("non_trading_date")
    tx = Transaction(day, ticker(stock_match[1]), "BUY" if side_text == "買入" else "SELL",
                     shares, price, f"[HSBC:{trade_id}] date_source={source}; fee_not_provided", Decimal(0))
    return Execution(trade_id, tx, source)


@contextmanager
def ledger_lock(path: Path):
    """Cross-process lock on macOS/Linux; no lock-file deletion race."""
    try:
        import fcntl
    except ImportError:
        raise HSBCSyncError("HSBC import currently requires macOS or Linux file locking.") from None
    lock = path.with_suffix(path.suffix + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HSBCSyncError("Transaction import is already running; retry later.") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def load_import_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "imported": {}}
    try:
        state = json.loads(path.read_text())
        if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("imported"), dict):
            raise ValueError
        for key, item in state["imported"].items():
            if not re.fullmatch(r"[A-Z0-9-]{3,40}", key) or not isinstance(item, dict):
                raise ValueError
            row = item["transaction"]
            if row["side"] not in {"BUY", "SELL"} or MARKER.findall(row["note"]) != [key]:
                raise ValueError
            validate_transactions([{**row, "side": "BUY"}])
            if item.get("date_source") not in {"explicit", "email_new_york", "user_confirmed"}:
                raise ValueError
        return state
    except (ValueError, TypeError, KeyError, ValidationError):
        raise HSBCSyncError("Invalid HSBC import state; restore it before syncing.") from None


def import_messages(messages: list[BankMessage], ledger: Path, state_path: Path, *,
                    allow_email_date=False, dry_run=False, date_overrides: dict[str, date] | None = None) -> dict:
    parsed, skipped = [], 0
    known = load_import_state(state_path)
    overrides = {key: date.fromisoformat(item["transaction"]["date"]) for key, item in known["imported"].items()}
    overrides.update(date_overrides or {})
    for message in messages:
        try:
            # Overrides are explicit user decisions, keyed by order reference.
            match = re.search(r"交易編號\s*[:：]\s*([A-Z0-9-]{3,40})", message.subject)
            override = overrides.get(match[1]) if match else None
            parsed.append(parse_execution(message, allow_email_date=allow_email_date, date_override=override))
        except ReceiptSkipped as exc:
            skipped += 1
            logger.info("HSBC message skipped: %s", exc)
    groups = {}
    for execution in parsed:
        groups.setdefault(execution.trade_id, []).append(execution)
    conflicts = {key for key, values in groups.items() if len({trade_signature(item.transaction.row()) for item in values}) > 1}
    if conflicts:
        skipped += sum(len(groups[key]) for key in conflicts)
        logger.warning("HSBC import skipped: conflicting_order_messages")
    parsed = [execution for execution in parsed if execution.trade_id not in conflicts]
    parsed.sort(key=lambda execution: execution.transaction.date)
    with ledger_lock(ledger):
        rows = [tx.row() for tx in load_transactions(ledger)]
        state = load_import_state(state_path)
        seen = {reference for row in rows for reference in MARKER.findall(row["note"])}
        imported, duplicate = 0, 0
        for execution in parsed:
            reference = execution.trade_id
            candidate = execution.transaction.row()
            if reference in seen:
                existing = next(row for row in rows if reference in MARKER.findall(row["note"]))
                if trade_signature(existing) != trade_signature(candidate):
                    skipped += 1
                    logger.warning("HSBC import skipped: imported_order_changed")
                else:
                    duplicate += 1
                continue
            if reference in state["imported"]:
                # A manually deleted/edited imported row must never silently reappear.
                skipped += 1
                logger.warning("HSBC import skipped: ledger_marker_missing")
                continue
            if any(not MARKER.search(row["note"]) and trade_signature(row) == trade_signature(candidate) for row in rows):
                skipped += 1
                logger.warning("HSBC import skipped: possible_manual_duplicate; bind the reference explicitly")
                continue
            try:
                validate_transactions(rows + [candidate])
            except ValidationError:
                skipped += 1
                logger.warning("HSBC import skipped: ledger_validation_failed")
                continue
            rows.append(candidate)
            seen.add(reference)
            state["imported"][reference] = {"transaction": candidate, "date_source": execution.date_source}
            imported += 1
        if not dry_run:
            # CSV carries the ID before the audit file: if the second write fails,
            # a subsequent run still cannot duplicate the already imported trade.
            if imported:
                save_transactions(ledger, rows)
            state["last_sync_at"] = datetime.now(timezone.utc).isoformat()
            state["summary"] = {"imported": imported, "duplicates": duplicate, "skipped": skipped}
            atomic_write(state_path, json.dumps(state, indent=2, sort_keys=True, allow_nan=False) + "\n")
        return {"imported": imported, "duplicates": duplicate, "skipped": skipped}


def trade_signature(row: dict) -> tuple:
    return (row["date"], row["symbol"], row["side"], Decimal(row["shares"]), Decimal(row["price"]))


def bind_existing(execution: Execution, ledger: Path, state_path: Path, row_index: int) -> None:
    """Explicit user-confirmed reconciliation; never guess matching manual rows."""
    with ledger_lock(ledger):
        rows = [tx.row() for tx in load_transactions(ledger)]
        state = load_import_state(state_path)
        if not 0 <= row_index < len(rows):
            raise HSBCSyncError("Invalid transaction selection.")
        if MARKER.search(rows[row_index]["note"]):
            raise HSBCSyncError("Selected transaction already has an HSBC reference.")
        row = rows[row_index]
        candidate = execution.transaction.row()
        if trade_signature({**row, "date": candidate["date"]}) != trade_signature(candidate):
            raise HSBCSyncError("Selected transaction does not match the confirmed execution.")
        if any(execution.trade_id in MARKER.findall(item["note"]) for item in rows) or execution.trade_id in state["imported"]:
            raise HSBCSyncError("HSBC reference already imported.")
        row["date"] = candidate["date"]
        row["note"] = (row["note"] + f" [HSBC:{execution.trade_id}]").strip()
        validate_transactions(rows)
        save_transactions(ledger, rows)
        state["imported"][execution.trade_id] = {"transaction": row, "date_source": "user_confirmed"}
        atomic_write(state_path, json.dumps(state, indent=2, sort_keys=True) + "\n")
