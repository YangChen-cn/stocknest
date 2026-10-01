"""Fully synthetic bank receipts; never copy a user's emails here."""
from datetime import date
from dataclasses import replace
from email.message import EmailMessage
from unittest.mock import Mock

import pytest

from stockwatch.imports.hsbc import (BankMessage, ReceiptSkipped, HSBCSyncError, parse_execution,
                                    import_messages, bind_existing)
from stockwatch.imports.gmail import message_from_bytes, read_messages
from stockwatch.storage import save_transactions, load_transactions, validate_config, ValidationError

AUTH = "mx.google.com; dkim=pass header.i=@hsbc.com.hk; dmarc=pass header.from=hsbc.com.hk"


def receipt(reference="T0001", side="買入", day="2026-10-06"):
    body = f'''<style>secret-looking example text</style><p>• 交易編號: {reference}</p>
<p>• 交易狀況: 全部執行</p><p>• 指示類別: {side}</p><p>• 股票名稱/ 股票編號: Fictional (XYZ)</p>
<p>• 已成交數量(股/單位): 2</p><p>• 成交價: USD20.50</p><p>• 共成交數量(股/單位): 2</p>
<p>• 餘下數量(股/單位): 0</p><p>• 交易日期: {day}</p>'''
    return BankMessage("HSBC <hsbc@notification.hsbc.com.hk>", f"全部執行: {side} XYZ: Fictional 的股/單位(交易編號: {reference})",
                       "Tue, 6 Oct 2026 14:00:00 +0000", AUTH, body, "synthetic-id")


@pytest.mark.parametrize("side,result", [("買入", "BUY"), ("賣出", "SELL")])
def test_clear_completed_orders(side, result):
    execution = parse_execution(receipt(side=side))
    assert execution.trade_id == "T0001" and execution.transaction.side == result
    assert str(execution.transaction.shares) == "2" and str(execution.transaction.price) == "20.50"
    assert execution.transaction.date == date(2026, 10, 6) and execution.transaction.fee == 0


@pytest.mark.parametrize("transform", [
    lambda m: replace(m, sender="attacker@example.com"),
    lambda m: replace(m, authentication="mx.google.com; dkim=fail; dmarc=fail"),
    lambda m: replace(m, authentication=AUTH.replace("hsbc.com.hk", "hsbc.com.hk.evil.example")),
    lambda m: replace(m, subject=m.subject.replace("全部執行", "部分執行")),
    lambda m: replace(m, body=m.body.replace("交易狀況: 全部執行", "交易狀況: 已取消")),
    lambda m: replace(m, body=m.body.replace("餘下數量(股/單位): 0", "餘下數量(股/單位): 1")),
    lambda m: replace(m, body=m.body.replace("共成交數量(股/單位): 2", "共成交數量(股/單位): 3")),
    lambda m: replace(m, body=m.body.replace("USD20.50", "HKD20.50")),
    lambda m: replace(m, body=m.body.replace("USD20.50", "USDNaN")),
    lambda m: replace(m, body=m.body.replace("(XYZ)", "(OTHER)")),
    lambda m: replace(m, body=m.body + "<p>• 成交價: USD20</p>"),
    lambda m: replace(m, body=m.body.replace("2026-10-06", "2026-10-04")),
])
def test_unknown_inconsistent_or_incomplete_orders_skip(transform):
    with pytest.raises(ReceiptSkipped):
        parse_execution(transform(receipt()))


def test_missing_date_is_conservative_and_opt_in_is_new_york():
    message = replace(receipt(), body=receipt().body.replace("<p>• 交易日期: 2026-10-06</p>", ""),
                      sent_at="Wed, 7 Oct 2026 00:30:00 +0000")
    with pytest.raises(ReceiptSkipped, match="missing_trade_date"):
        parse_execution(message)
    execution = parse_execution(message, allow_email_date=True)
    assert execution.transaction.date == date(2026, 10, 6) and execution.date_source == "email_new_york"
    assert parse_execution(message, date_override=date(2026, 10, 5)).date_source == "user_confirmed"


@pytest.fixture
def files(tmp_path):
    ledger, audit = tmp_path / "transactions.csv", tmp_path / "hsbc_imports.json"
    save_transactions(ledger, [])
    return ledger, audit


def test_idempotent_repeat_dry_run_and_chronological_sell(files):
    ledger, audit = files
    preview = import_messages([receipt()], ledger, audit, dry_run=True)
    assert preview["imported"] == 1 and not load_transactions(ledger) and not audit.exists()
    result = import_messages([receipt(side="賣出", reference="T0002"), receipt()], ledger, audit)
    # Same-day order is intentionally input order; sell-before-buy must be skipped.
    assert result["imported"] == 1 and result["skipped"] == 1
    assert import_messages([receipt()], ledger, audit)["duplicates"] == 1
    assert len(load_transactions(ledger)) == 1


def test_csv_first_survives_audit_write_failure(files, monkeypatch):
    ledger, audit = files
    monkeypatch.setattr("stockwatch.imports.hsbc.atomic_write", Mock(side_effect=OSError("synthetic failure")))
    with pytest.raises(OSError):
        import_messages([receipt()], ledger, audit)
    assert len(load_transactions(ledger)) == 1
    assert import_messages([receipt()], ledger, audit, dry_run=True)["duplicates"] == 1


def test_manual_collision_and_explicit_binding(files):
    ledger, audit = files
    row = {"date": "2026-10-05", "symbol": "XYZ", "side": "BUY", "shares": "2", "price": "20.50", "note": "My manual note"}
    save_transactions(ledger, [row])
    execution = parse_execution(receipt())
    bind_existing(execution, ledger, audit, 0)
    tx = load_transactions(ledger)[0]
    assert tx.date == date(2026, 10, 6) and "My manual note" in tx.note
    assert import_messages([receipt()], ledger, audit)["duplicates"] == 1
    assert len(load_transactions(ledger)) == 1


def test_ambiguous_manual_duplicate_never_auto_binds(files):
    ledger, audit = files
    save_transactions(ledger, [{"date": "2026-10-06", "symbol": "XYZ", "side": "BUY", "shares": "2", "price": "20.50", "note": "manual"}])
    assert import_messages([receipt()], ledger, audit)["skipped"] == 1
    assert load_transactions(ledger)[0].note == "manual"


def test_deleted_import_is_not_restored_and_conflicting_batch_skips(files):
    ledger, audit = files
    import_messages([receipt()], ledger, audit)
    save_transactions(ledger, [])
    assert import_messages([receipt()], ledger, audit)["skipped"] == 1
    assert not load_transactions(ledger)
    ledger2 = ledger.parent / "other.csv"
    save_transactions(ledger2, [])
    result = import_messages([receipt(), replace(receipt(), body=receipt().body.replace("USD20.50", "USD30"))], ledger2, ledger.parent / "other.json")
    assert result["skipped"] == 2 and not load_transactions(ledger2)


def test_corrupt_audit_prevents_writes(files):
    ledger, audit = files
    audit.write_text('{"version":1,"imported":{"T0001":{}}}')
    with pytest.raises(HSBCSyncError):
        import_messages([receipt()], ledger, audit)
    assert not load_transactions(ledger)


def test_mime_charset_and_imap_is_read_only(monkeypatch):
    msg = EmailMessage()
    example = receipt()
    for key, value in {"From":example.sender, "Subject":example.subject, "Date":example.sent_at, "Authentication-Results":AUTH}.items():
        msg[key] = value
    msg.set_content(example.body, subtype="html")
    assert parse_execution(message_from_bytes(msg.as_bytes())).trade_id == "T0001"
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.list.return_value = ("OK", [b'(\\All) "/" "[Gmail]/All Mail"'])
    client.select.return_value = ("OK", [b"1"])
    client.uid.side_effect = [("OK", [b"1"]), ("OK", [(b"1", msg.as_bytes())])]
    monkeypatch.setattr("stockwatch.imports.gmail.imaplib.IMAP4_SSL", Mock(return_value=client))
    monkeypatch.setenv("GMAIL_ADDRESS", "synthetic@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "synthetic-secret")
    assert len(read_messages()) == 1
    client.select.assert_called_once_with(b'"[Gmail]/All Mail"', readonly=True)
    assert client.uid.call_args.args == ("fetch", b"1", "(BODY.PEEK[])")


def test_import_settings_validate():
    options = validate_config({"imports":{"hsbc":{"enabled": True}}, "portfolio": {}, "watchlist": {}})["imports"]["hsbc"]
    assert options == {"enabled":True,"allow_email_date":True,"lookback_days":30}
    with pytest.raises(ValidationError):
        validate_config({"imports":{"hsbc":{"lookback_days": True}}})


def test_imap_failure_is_sanitized(monkeypatch, caplog):
    import imaplib
    import stockwatch.imports.gmail as gmail
    client=Mock()
    client.__enter__=Mock(return_value=client)
    client.__exit__=Mock(return_value=False)
    client.login.side_effect=imaplib.IMAP4.error("password synthetic-secret must never appear")
    monkeypatch.setattr(gmail.imaplib, "IMAP4_SSL", Mock(return_value=client))
    monkeypatch.setenv("GMAIL_ADDRESS", "synthetic@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "synthetic-secret")
    with pytest.raises(HSBCSyncError) as error:
        read_messages()
    assert "synthetic-secret" not in str(error.value) and "synthetic-secret" not in caplog.text


def test_reconciled_missing_date_is_duplicate_after_restart(files):
    ledger,audit=files
    message=replace(receipt(),body=receipt().body.replace("<p>• 交易日期: 2026-10-06</p>",""))
    execution=parse_execution(message,date_override=date(2026,10,6))
    save_transactions(ledger,[{"date":"2026-10-05","symbol":"XYZ","side":"BUY","shares":"2","price":"20.50","note":"manual"}])
    bind_existing(execution,ledger,audit,0)
    assert import_messages([message],ledger,audit)["duplicates"]==1


def test_same_day_sorted_buy_then_sell_and_fractional_values(files):
    ledger,audit=files
    buy=receipt(reference="T0001")
    sell=receipt(reference="T0002",side="賣出")
    result=import_messages([buy,sell],ledger,audit)
    assert result["imported"]==2
    txs=load_transactions(ledger)
    assert [tx.side for tx in txs]==["BUY","SELL"]
    fractional=replace(receipt(reference="T0003"), body=receipt(reference="T0003").body.replace("單位): 2", "單位): 0.5"))
    assert str(parse_execution(fractional).transaction.shares)=="0.5"
