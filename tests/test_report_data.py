import json
from datetime import date, datetime, timezone
from decimal import Decimal
from html import unescape

import pytest

from stockwatch.alerts import Alert
from stockwatch.notifications.email import EmailSettings, make_message
from stockwatch.portfolio import calculate, Position
from stockwatch.providers.base import Quote
from stockwatch.report_data import BEGIN, END, numeric
from stockwatch.reports import render_failure, render_report

DAY = date(2026, 9, 30)
NOW = datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc)


def payload(text):
    return json.loads(text.split(BEGIN + "\n", 1)[1].split("\n" + END, 1)[0])


@pytest.mark.parametrize("mode", ["CLOSE", "INTRADAY"])
def test_machine_data_in_both_mime_parts_preserves_visible_body(mode):
    quotes = {"XYZ": Quote("XYZ", 21, 20, session=DAY, price_at=NOW),
              "CAND": Quote("CAND", error="credential=do-not-export")}
    book = calculate({"XYZ": Position("XYZ", Decimal("0.25"), Decimal("5"))}, quotes)
    config = {"portfolio": {"base_currency": "USD", "language": "zh-CN"},
              "notifications": {"private_extra": "secret-not-exported"},
              "watchlist": {"XYZ": {"thesis": "user note </pre><script>untrusted</script>", "buy_below": 22}, "CAND": {}}}
    rows = [{"Symbol": "XYZ", "Current Price": 21, "Daily %": 5, "5D %": 2.5, "1M %": None}]
    report = render_report(DAY, book, quotes, config, [Alert("XYZ", "buy_below_22", "At configured price")],
                           mode=mode, generated_at=NOW, watchlist_rows=rows)
    message = make_message(report, EmailSettings("sender@example.invalid", "not-exported", "reader@example.invalid"))
    assert message.get_content_type() == "multipart/mixed"
    data = payload(message.get_body(preferencelist=("plain",)).get_content())
    html = message.get_body(preferencelist=("html",)).get_content()
    assert data == payload(unescape(html))
    attachments = list(message.iter_attachments())
    assert len(attachments) == 1
    attachment = attachments[0]
    assert attachment.get_content_type() == "application/json"
    assert attachment.get_content_disposition() == "attachment"
    assert attachment.get_filename() == f"stockwatch-{DAY}-{mode.lower()}.json"
    assert json.loads(attachment.get_payload(decode=True).decode("utf-8")) == data
    # Parsing the serialized wire message preserves attachment bytes and Chinese notes.
    from email import policy
    from email.parser import BytesParser
    wire = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
    assert json.loads(next(wire.iter_attachments()).get_payload(decode=True).decode("utf-8")) == data
    assert 'display:none!important' in html and '<script>' not in html
    assert data["mode"] == mode and data["session_timezone"] == "America/New_York"
    assert data["portfolio"]["holdings"][0]["shares"] == "0.25"
    assert data["portfolio"]["metrics"]["market_value"] == "5.25"
    assert data["instruments"][0]["data_status"] == "unavailable"
    assert data["instruments"][0]["price_usd"] is None
    assert data["instruments"][1]["return_5d_pct"] == "2.5"
    assert data["instruments"][1]["return_1m_pct"] is None
    assert data["new_alerts"][0]["rule"] == "buy_below_22"
    assert "credential=" not in report.text and "secret-not-exported" not in report.text
    assert "sender@example.invalid" not in report.text
    if mode == "INTRADAY":
        assert data["price_basis"].endswith("not_final_close")
        assert data["instruments"][1]["history_as_of"] == "2026-09-29"
    visible = html.split('<div aria-hidden="true"', 1)[0]
    assert BEGIN not in visible and "持仓" in visible


def test_performance_returns_include_cutoff_and_no_full_ledger():
    history = {"benchmark": "BENCH", "points": [
        {"date": "2026-09-29", "nav": 100, "benchmark_nav": 100},
        {"date": "2026-09-30", "nav": 102, "benchmark_nav": 101},
    ]}
    config = {"portfolio": {}, "watchlist": {}}
    report = render_report(DAY, calculate({}, {}), {}, config, [], performance=history)
    data = payload(report.text)
    assert data["performance"]["as_of"] == DAY.isoformat()
    assert float(data["performance"]["returns"]["ALL"]["portfolio"]) == pytest.approx(2)
    assert data["performance"]["returns"]["1M"]["portfolio"] is None
    assert "points" not in data["performance"] and "transactions" not in data


def test_missing_data_and_failure_are_not_zero_or_success():
    quotes = {"XYZ": Quote("XYZ", error="not available")}
    config = {"portfolio": {}, "watchlist": {"XYZ": {}}}
    data = payload(render_report(DAY, calculate({"XYZ": Position("XYZ", Decimal(1), Decimal(5))}, quotes), quotes, config, []).text)
    assert data["portfolio"]["metrics"]["market_value"] is None
    assert not data["portfolio"]["valuation_complete"]
    failure = payload(render_failure(DAY, "Close data remains unavailable; the daily report was not sent.", ["XYZ"], 3, "en").text)
    assert failure["report_type"] == "error" and not failure["price_alerts_consumed"]
    assert "portfolio" not in failure
    assert numeric(float("nan")) is None and numeric(float("inf")) is None


def test_failure_json_attachment_and_legacy_report_compatibility():
    from stockwatch.reports import Report
    settings = EmailSettings("sender@example.invalid", "not-exported", "reader@example.invalid")
    failure = render_failure(DAY, "Unavailable", ["XYZ"], 3, "en")
    message = make_message(failure, settings)
    attachment = next(message.iter_attachments())
    assert attachment.get_filename() == f"stockwatch-{DAY}-close-error.json"
    assert json.loads(attachment.get_payload(decode=True))["report_type"] == "error"
    legacy = make_message(Report("Subject", "Plain", "<p>HTML</p>"), settings)
    assert legacy.get_content_type() == "multipart/alternative"
    assert not list(legacy.iter_attachments())
