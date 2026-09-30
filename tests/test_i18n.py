from datetime import date

import pytest

from stockwatch.alerts import evaluate
from stockwatch.daily import run
from stockwatch.i18n import data_status, error_message, language, t
from stockwatch.notifications.email import EmailDeliveryError
from stockwatch.portfolio import calculate, positions
from stockwatch.providers.base import Quote
from stockwatch.reports import render_report
from stockwatch.storage import (ValidationError, load_config, load_state, load_transactions,
                                save_config, validate_config, validate_transactions)

DAY = date(2026, 10, 6)


def test_legacy_config_and_language_validation():
    assert language(validate_config({"portfolio": {"base_currency": "USD"}})) == "en"
    for lang in ("zh-CN", "en"):
        assert language(validate_config({"portfolio": {"language": lang}})) == lang
    for lang in ("fr", [], None):
        with pytest.raises(ValidationError):
            validate_config({"portfolio": {"language": lang}})


def test_chinese_report_alerts_facts_and_html_escape():
    config = {"portfolio": {"base_currency": "USD", "language": "zh-CN"}, "watchlist": {
        "XYZ": {"alerts": {"below": 95, "daily_move_pct": 5}}, "BAD": {}}}
    held = positions(validate_transactions([{"date": "2026-10-05", "symbol": "XYZ", "side": "BUY", "shares": 2, "price": 80}]), DAY)
    quotes = {"XYZ": Quote("XYZ", 90, 100, session=DAY), "BAD": Quote("BAD", error="Data unavailable: previous close")}
    portfolio = calculate(held, quotes)
    pending, state = evaluate(config, quotes, {}, DAY)
    assert len(pending) == 2 and not state["XYZ"]["below_95"]["triggered"]
    report = render_report(DAY, portfolio, quotes, config, pending)
    assert report.subject == "StockWatch 每日持仓报告 | -10.00% | 10月6日"
    for text in ("持仓成本", "持仓市值", "今日盈亏", "浮动盈亏", "持仓未实现收益率", "目标价 $95.00", "涨跌幅为 -10.00%", "缺少前收盘价"):
        assert text in report.html and text in report.text
    assert '<html lang="zh-CN">' in report.html
    assert "No new alerts" not in report.html and "Regular session" not in report.html
    assert "$180.00" in report.html and "-$20.00" in report.html
    # User text remains data, including literal braces; never interpreted as a template.
    from stockwatch.alerts import Alert
    unsafe = render_report(DAY, portfolio, quotes, config, [Alert("XYZ", "below_95", "<script>{test}</script>")])
    assert "<script>" not in unsafe.html and "&lt;script&gt;{test}&lt;/script&gt;" in unsafe.html


def test_chinese_errors_and_no_raw_secrets():
    with pytest.raises(ValidationError) as error:
        validate_transactions([{"date": "2026-10-05", "symbol": "XYZ", "side": "BUY", "shares": "abc", "price": 80}])
    assert "第2行的股数必须是有效的正数" in error_message(error.value, "zh-CN")
    with pytest.raises(ValidationError) as error:
        validate_transactions([{"date": "2026-10-05", "symbol": "XYZ", "side": "SELL", "shares": 1, "price": 80}])
    assert "不能卖出 1 股，目前仅持有 0 股" in error_message(error.value, "zh-CN")
    assert "Gmail 发送失败" in error_message(EmailDeliveryError("Gmail delivery failed ({kind}); check Secrets and account settings.", kind="SMTPAuthenticationError"), "zh-CN")
    assert "暂时不可用" in data_status("unknown exception with sensitive text", "zh-CN")
    assert t("literal {braces}", "en") == "literal {braces}"


def test_chinese_daily_logs_and_pending_state(portfolio_files, monkeypatch, caplog):
    config = load_config(portfolio_files["config_path"])
    config["portfolio"]["language"] = "zh-CN"
    save_config(portfolio_files["config_path"], config)
    original = portfolio_files["transactions_path"].read_bytes()
    for name in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "REPORT_EMAIL"):
        monkeypatch.delenv(name, raising=False)
    with caplog.at_level("INFO"):
        assert run(**portfolio_files) == 0
    assert "已生成 2026-10-06 日报" in caplog.text and "已跳过发送" in caplog.text
    assert not load_state(portfolio_files["state_path"])["XYZ"]["below_95"]["triggered"]
    assert portfolio_files["transactions_path"].read_bytes() == original
    assert load_transactions(portfolio_files["transactions_path"])[0].symbol == "XYZ"
