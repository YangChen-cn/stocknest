"""Read-only Gmail IMAP using environment variables or an optional private local profile."""
import imaplib
import logging
import re
import ssl
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser

from stockwatch.notifications.local import credentials
from stockwatch.imports.hsbc import BankMessage, HSBCSyncError, SENDER, import_messages

logger = logging.getLogger(__name__)


def message_from_bytes(raw: bytes) -> BankMessage:
    if len(raw) > 2_000_000:
        raise HSBCSyncError("HSBC email exceeds the import size limit.")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    plain, html = [], []
    for part in message.walk():
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() in {"text/plain", "text/html"}:
            target = plain if part.get_content_type() == "text/plain" else html
            target.append(part.get_content())
    return BankMessage(str(message.get("From", "")), str(message.get("Subject", "")), str(message.get("Date", "")),
                       str(message.get("Authentication-Results", "")), "\n".join(plain or html),
                       str(message.get("Message-ID", "")))


def read_messages(*, lookback_days=3, now=None, limit=200) -> list[BankMessage]:
    values = credentials()
    address = values["GMAIL_ADDRESS"]
    password = "".join(values["GMAIL_APP_PASSWORD"].split())
    if not address or not password:
        raise HSBCSyncError("Gmail credentials missing; configure the existing Gmail environment variables.")
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
    try:
        with imaplib.IMAP4_SSL("imap.gmail.com", 993, ssl_context=ssl.create_default_context(), timeout=30) as client:
            client.login(address, password)
            status, mailboxes = client.list()
            if status != "OK":
                raise ValueError
            mailbox = None
            for line in mailboxes:
                match = re.match(rb'^\((.*?)\) "[^"]*" (.+)$', line or b"")
                if match and b"\\All" in match[1].split():
                    mailbox = match[2]
                    break
            if mailbox is None or client.select(mailbox, readonly=True)[0] != "OK":
                raise ValueError
            status, results = client.uid("search", None, "FROM", f'"{SENDER}"', "SINCE", since)
            ids = results[0].split() if status == "OK" and results else None
            if ids is None or len(ids) > limit:
                raise ValueError
            messages = []
            for uid in ids:
                status, parts = client.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK":
                    raise ValueError
                raw = next((part[1] for part in parts if isinstance(part, tuple)), None)
                if not isinstance(raw, bytes):
                    raise ValueError
                try:
                    messages.append(message_from_bytes(raw))
                except (HSBCSyncError, ValueError, UnicodeError, LookupError, TypeError):
                    logger.info("HSBC message skipped: unreadable_mime")
            return messages
    except (imaplib.IMAP4.error, OSError, ValueError, TypeError):
        raise HSBCSyncError("Gmail read-only sync failed; check App Password and IMAP access.") from None


def sync_hsbc(config, ledger, state_path, *, dry_run=False, reader=None, lookback_days=None):
    settings = config.get("imports", {}).get("hsbc", {})
    if not settings.get("enabled", False):
        return {"imported": 0, "duplicates": 0, "skipped": 0, "disabled": True}
    messages = (reader or read_messages)(lookback_days=lookback_days if lookback_days is not None else settings.get("lookback_days", 3))
    return import_messages(messages, ledger, state_path, allow_email_date=settings.get("allow_email_date", True), dry_run=dry_run)
