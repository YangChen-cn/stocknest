from datetime import date
from decimal import Decimal

from stockwatch.portfolio import calculate, positions
from stockwatch.storage import Transaction

import pytest

from stockwatch.alerts import evaluate, mark_delivered
from stockwatch.daily import main, run
from stockwatch.notifications.email import EmailDeliveryError, EmailSettings, configuration_status, make_message, send_report
from stockwatch.providers.base import Quote
from stockwatch.reports import render_report
from stockwatch.services import snapshot
from stockwatch.storage import load_config, load_state, load_transactions
from conftest import DAY, FakeProvider


def config():
    return {"portfolio": {"base_currency": "USD"}, "watchlist": {"XYZ": {"alerts": {"below": 50.5, "daily_move_pct": 5}}}}


def test_threshold_first_touch_no_repeat_rearm_and_touch_again():
    data = config()
    quotes = {"XYZ": Quote("XYZ", 50.5, 50.5, session=DAY)}
    alerts, state = evaluate(data, quotes, {}, DAY)
    assert len(alerts) == 1 and not state["XYZ"]["below_50.5"]["triggered"]
    state = mark_delivered(state, alerts, DAY)
    assert evaluate(data, quotes, state, DAY)[0] == []
    next_day = date(2026, 10, 7)
    alerts, state = evaluate(data, {"XYZ": Quote("XYZ", 51, 51, session=next_day)}, state, next_day)
    assert not alerts and not state["XYZ"]["below_50.5"]["triggered"]
    alerts, _ = evaluate(data, {"XYZ": Quote("XYZ", 50, 50, session=next_day)}, state, next_day)
    assert len(alerts) == 1


def test_daily_move_abs_threshold_and_session_dedup():
    quotes = {"XYZ": Quote("XYZ", 90, 100, session=DAY)}
    alerts, state = evaluate(config(), quotes, {}, DAY)
    assert len(alerts) == 1 and "moved -10.00%" in alerts[0].message
    delivered = mark_delivered(state, alerts, DAY)
    assert not evaluate(config(), quotes, delivered, DAY)[0]
    following = date(2026, 10, 7)
    assert evaluate(config(), {"XYZ": Quote("XYZ", 110, 100, session=following)}, delivered, following)[0]


def test_unavailable_or_stale_prices_do_not_reset():
    state = {"XYZ": {"below_50.5": {"triggered": True, "last_notified": DAY.isoformat()}}}
    for quote in (Quote("XYZ", error="Data unavailable"), Quote("XYZ", 100, 100, session=date(2026, 10, 5))):
        alerts, updated = evaluate(config(), {"XYZ": quote}, state, DAY)
        assert not alerts and updated == state


@pytest.fixture
def mail_env(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "sender@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "test-secret-must-not-leak")
    monkeypatch.setenv("REPORT_EMAIL", "reader@gmail.com")


def test_missing_email_runs_and_keeps_alerts_pending(portfolio_files, monkeypatch):
    for name in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "REPORT_EMAIL"):
        monkeypatch.delenv(name, raising=False)
    assert run(**portfolio_files) == 0
    state = load_state(portfolio_files["state_path"])
    assert not state["XYZ"]["below_95"]["triggered"] and "_meta" not in state
    assert (portfolio_files["output_dir"] / f"{DAY}.html").exists()


def test_success_repeat_and_force_send(portfolio_files, mail_env):
    sent = []
    def sender(report, settings):
        sent.append(report)
    assert run(**portfolio_files, sender=sender) == 0
    assert len(sent) == 1
    state = load_state(portfolio_files["state_path"])
    assert state["XYZ"]["below_95"]["triggered"] and state["_meta"]["last_report_session"] == DAY.isoformat()
    assert run(**portfolio_files, sender=sender) == 0 and len(sent) == 1
    assert run(**portfolio_files, sender=sender, force_send=True) == 0 and len(sent) == 2
    assert "No new alerts." in sent[1].text


def test_delivery_failure_preserves_pending_and_report(portfolio_files, mail_env, caplog):
    def fail(report, settings):
        raise EmailDeliveryError("Delivery failed (safe message)")
    assert run(**portfolio_files, sender=fail) == 1
    assert not load_state(portfolio_files["state_path"])["XYZ"]["below_95"]["triggered"]
    assert "test-secret-must-not-leak" not in caplog.text


def test_preview_never_sends_or_mutates_state(portfolio_files, mail_env):
    original = portfolio_files["state_path"].read_bytes()
    def forbidden(*args):
        raise AssertionError("Preview must not send")
    for args in ({"dry_run": True}, {"demo": True}):
        assert run(**portfolio_files, sender=forbidden, **args) == 0
        assert portfolio_files["state_path"].read_bytes() == original


def test_failing_ticker_isolated_and_no_secrets_logged(portfolio_files, caplog):
    data = load_config(portfolio_files["config_path"])
    data["watchlist"]["BAD"] = {}
    portfolio, quotes = snapshot(data, load_transactions(portfolio_files["transactions_path"]),
                                 FakeProvider({"XYZ": Quote("XYZ", 90, 100, session=DAY), "BAD": RuntimeError("API_KEY=secret")}), DAY, closing=True)
    assert portfolio["market_value"] == 180 and quotes["BAD"].price is None
    assert "API_KEY=secret" not in caplog.text


def test_html_escaping_unavailable_and_multipart(portfolio_files, mail_env):
    data = load_config(portfolio_files["config_path"])
    portfolio, quotes = snapshot(data, load_transactions(portfolio_files["transactions_path"]), FakeProvider(), DAY, closing=True)
    from stockwatch.alerts import Alert
    report = render_report(DAY, portfolio, quotes, data, [Alert("XYZ", "below_95", "<script>alert('x')</script>")])
    assert "<script>" not in report.html and "&lt;script&gt;" in report.html
    message = make_message(report, EmailSettings.from_environment())
    assert message.get_content_type() == "multipart/alternative"
    assert [part.get_content_type() for part in message.iter_parts()] == ["text/plain", "text/html"]
    assert "test-secret-must-not-leak" not in message.as_string()


def test_smtp_failure_sanitized(monkeypatch, mail_env, portfolio_files):
    import smtplib
    def unsafe(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"test-secret-must-not-leak")
    monkeypatch.setattr("stockwatch.notifications.email.smtplib.SMTP_SSL", unsafe)
    portfolio, quotes = snapshot(load_config(portfolio_files["config_path"]), load_transactions(portfolio_files["transactions_path"]), FakeProvider(), DAY, closing=True)
    report = render_report(DAY, portfolio, quotes, config(), [])
    with pytest.raises(EmailDeliveryError) as error:
        send_report(report, EmailSettings.from_environment())
    assert "test-secret-must-not-leak" not in str(error.value)
    assert all(isinstance(value, bool) for value in configuration_status().values())


def test_invalid_email_header_injection():
    from stockwatch.reports import Report
    with pytest.raises(EmailDeliveryError):
        make_message(Report("subject", "text", "html"), EmailSettings("sender@gmail.com\nBcc: bad", "secret", "to@gmail.com"))


def test_demo_cli_offline_without_state_write(tmp_path):
    state = tmp_path / "state.json"
    assert main(["--demo", "--state", str(state), "--output-dir", str(tmp_path / "out"), "--log-dir", str(tmp_path / "log")]) == 0
    assert not state.exists()
    assert "SIMULATED DEMO DATA" in (tmp_path / "out/2026-10-06.html").read_text()


def test_incomplete_valuation_warning_precedes_summary():
    unavailable = {"XYZ": Quote("XYZ", session=DAY, error="Data unavailable")}
    portfolio = calculate(positions([Transaction(DAY, "XYZ", "BUY", Decimal(1), Decimal(90))], DAY), unavailable)
    data = config()
    data["portfolio"]["language"] = "zh-CN"
    report = render_report(DAY, portfolio, unavailable, data, [])
    warning = "行情数据缺失，本次报告无法计算完整的持仓估值。"
    assert warning in report.html and warning in report.text
    assert report.html.index(warning) < report.html.index("持仓成本")
