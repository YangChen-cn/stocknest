from copy import deepcopy
from datetime import date, datetime
from unittest.mock import Mock
import json
import subprocess

import pandas as pd
import pytest

from stockwatch.alerts import evaluate, mark_delivered
from stockwatch.calendar import NY, active_session
from stockwatch.daily import main, run
from stockwatch.notifications.email import EmailDeliveryError
from stockwatch.providers.base import Quote
from stockwatch.providers.demo import DemoProvider
from stockwatch.providers.openbb_provider import OpenBBProvider, normalize_history, quote_from_intraday
from stockwatch.reports import render_report
from stockwatch.services import candidate_rows
from stockwatch.storage import load_config, load_state, validate_config, validate_state, ValidationError

DAY = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, 10, 30, tzinfo=NY)


@pytest.mark.parametrize("stamp,expected", [
    ("2026-07-01T14:30:00+00:00", date(2026, 7, 1)),
    ("2026-01-06T15:30:00+00:00", date(2026, 1, 6)),
    ("2026-07-03T14:30:00+00:00", None),
    ("2026-07-04T14:30:00+00:00", None),
    ("2026-07-01T13:29:00+00:00", None),
    ("2026-07-01T13:30:00+00:00", date(2026, 7, 1)),
    ("2026-07-01T20:00:00+00:00", None),
    ("2026-11-27T17:30:00+00:00", date(2026, 11, 27)),
    ("2026-11-27T18:00:00+00:00", None),
])
def test_active_session_dst_holidays_boundaries_early_close(stamp, expected):
    assert active_session(datetime.fromisoformat(stamp)) == expected


def prior_history():
    return normalize_history([{"date": "2026-10-02", "close": 95, "high": 120, "low": 70},
                              {"date": "2026-10-05", "close": 100, "high": 105, "low": 99}])


@pytest.mark.parametrize("stamp", ["2026-10-06 10:29:00", "2026-10-06T14:29:00+00:00"])
def test_intraday_uses_minute_bar_and_actual_timestamp(stamp):
    quote = quote_from_intraday("DYNAMIC", [{"date": stamp, "close": 90}], prior_history(), DAY, NOW)
    assert quote.price == 90 and quote.previous_close == 100 and quote.daily_move_pct == pytest.approx(-10)
    assert quote.price_at == NOW.replace(minute=29)
    assert quote.session == DAY and quote.year_high == 120 and not quote.error


@pytest.mark.parametrize("bars", [[], [{"date": "2026-10-05 15:59:00", "close": 90}],
                                   [{"date": "2026-10-06 09:59:00", "close": 90}],
                                   [{"date": "2026-10-06 10:31:00", "close": 90}],
                                   [{"date": "bad", "close": 90}],
                                   [{"date": "2026-10-06 10:29:00", "close": float("nan")}],
                                   [{"date": "2026-10-06 09:20:00", "close": 90}]])
def test_missing_stale_premarket_future_intraday_is_unavailable(bars):
    quote = quote_from_intraday("DYNAMIC", bars, prior_history(), DAY, NOW)
    assert quote.price is None and quote.error


def test_intraday_worker_protocol_and_timeout(monkeypatch):
    raw = {"bars": [{"date": "2026-10-06 10:29:00", "close": 90}],
           "history": [{"date": "2026-10-05", "close": 100}]}
    worker = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(raw), ""))
    monkeypatch.setattr("stockwatch.providers.openbb_provider.subprocess.run", worker)
    assert OpenBBProvider().get_intraday_quote("DYNAMIC", DAY, NOW).price == 90
    assert json.loads(worker.call_args.kwargs["input"]) == {"operation": "intraday", "symbol": "DYNAMIC", "session": DAY.isoformat()}
    worker.side_effect = subprocess.TimeoutExpired("worker", 0.01)
    monkeypatch.setattr("stockwatch.providers.openbb_provider.time.sleep", lambda _: None)
    assert OpenBBProvider(timeout=0.01).get_intraday_quote("DYNAMIC", DAY, NOW).error
    worker.reset_mock()
    assert OpenBBProvider().get_intraday_quote("DYNAMIC", DAY, NOW.replace(hour=16)).price is None
    worker.assert_not_called()


def test_optional_fields_roundtrip_and_legacy_defaults():
    old = validate_config({"watchlist": {"XYZ": {"thesis": "old", "alerts": {"below": 95}}}})
    assert "buy_below" not in old["watchlist"]["XYZ"]
    new = validate_config({"watchlist": {"XYZ": {"status": "waiting", "buy_below": "95"}}})
    assert new["watchlist"]["XYZ"]["buy_below"] == 95
    assert new["watchlist"]["XYZ"]["status"] == "waiting"


@pytest.mark.parametrize("entry", [{"status": "bought"}, {"status": []}, {"buy_below": 0}, {"buy_below": "NaN"}])
def test_invalid_candidate_fields(entry):
    with pytest.raises(ValidationError):
        validate_config({"watchlist": {"XYZ": entry}})


def test_intraday_alerts_isolate_close_state_and_crossing_dedup():
    config = {"watchlist": {"XYZ": {"buy_below": 95, "alerts": {"below": 95, "daily_move_pct": 5}}}}
    original = {"XYZ": {"below_95": {"triggered": True, "last_notified": "2026-10-05"}},
                "_meta": {"last_report_session": "2026-10-05"}}
    saved = deepcopy(original)
    quotes = {"XYZ": Quote("XYZ", 95, 100, session=DAY)}
    pending, state = evaluate(config, quotes, original, DAY, mode="INTRADAY")
    assert original == saved and len(pending) == 3
    state = mark_delivered(state, pending, DAY, mode="INTRADAY")
    assert state["XYZ"]["below_95"] == original["XYZ"]["below_95"]
    assert state["_meta"] == {"last_report_session": "2026-10-05", "last_intraday_session": "2026-10-06"}
    assert not evaluate(config, quotes, state, DAY, mode="INTRADAY")[0]
    _, recovered = evaluate(config, {"XYZ": Quote("XYZ", 96, 100, session=DAY)}, state, DAY, mode="INTRADAY")
    assert not recovered["XYZ"]["intraday_buy_below_95"]["triggered"]
    assert not evaluate(config, quotes, recovered, DAY, mode="INTRADAY")[0]  # Same-day recross suppressed.
    next_day = date(2026, 10, 7)
    pending, _ = evaluate(config, {"XYZ": Quote("XYZ", 95, 100, session=next_day)}, recovered, next_day, mode="INTRADAY")
    assert len(pending) == 3
    close_pending, _ = evaluate(config, quotes, state, DAY)
    assert {alert.rule for alert in close_pending} == {"buy_below_95", "daily_move_pct_5"}


def test_error_quote_does_not_rearm_or_trigger_candidate_alert():
    config = {"watchlist": {"XYZ": {"buy_below": 95}}}
    state = {"XYZ": {"buy_below_95": {"triggered": True, "last_notified": DAY.isoformat()}}}
    assert evaluate(config, {"XYZ": Quote("XYZ", 100, None, session=DAY, error="Data unavailable")}, state, DAY) == ([], state)


def test_candidate_facts_missing_history_and_owned_classification():
    config = {"watchlist": {"XYZ": {"status": "waiting", "buy_below": 95}, "BAD": {}}}
    history = pd.DataFrame({"date": pd.date_range("2026-10-01", periods=6), "close": [80, 90, 92, 94, 96, 100]})
    rows = candidate_rows(config, {"XYZ"}, {"XYZ": Quote("XYZ", 100, 99, 120, 70)}, {"XYZ": history})
    row, missing = rows
    assert row["Held"] and row["Status"] == "waiting"
    assert row["5D %"] is None and row["1M %"] is None  # Exact anchors are absent.
    assert row["Distance to Buy Below"] == pytest.approx((100 / 95 - 1) * 100)
    assert missing["Current Price"] is None and missing["5D %"] is None and not missing["Held"]


def test_short_report_semantics_timestamps_near_targets_and_html_escaping():
    provider = DemoProvider()
    from stockwatch.storage import ROOT, load_transactions
    from stockwatch.services import snapshot
    config = load_config(ROOT / "examples/config.yaml")
    config["portfolio"]["language"] = "zh-CN"
    config["watchlist"]["CAND"]["status"] = "<script>"
    now = datetime(2026, 10, 6, 10, 30, tzinfo=NY)
    portfolio, quotes = snapshot(config, load_transactions(ROOT / "examples/transactions.csv"), provider, DAY, intraday=True, now=now)
    short = render_report(DAY, portfolio, quotes, config, [], mode="INTRADAY", generated_at=now)
    close = render_report(DAY, portfolio, quotes, config, [])
    assert "盘中简报" in short.subject and "并非最终收盘价" in short.text
    assert "10:30 EDT" in short.text and ("明显异动" in short.text or "观察股异动" in short.text) and "CAND" in short.text
    assert "平均成本" not in short.text and "NYSE 已完成" not in short.html
    assert "&lt;script&gt;" in short.html and "<script>" not in short.html
    assert "NYSE 已完成" in close.text and "每日持仓报告" in close.subject


@pytest.fixture
def intraday_files(portfolio_files):
    from conftest import FakeProvider
    class Provider(FakeProvider):
        def get_intraday_quote(self, symbol, session, now=None):
            return self.get_quote(symbol, session)
    return {**portfolio_files, "provider": Provider(), "mode": "INTRADAY", "now": NOW}


def test_two_report_modes_send_once_each_without_overwriting_reports(intraday_files, monkeypatch):
    for key, value in {"GMAIL_ADDRESS": "from@gmail.com", "GMAIL_APP_PASSWORD": "secret", "REPORT_EMAIL": "to@gmail.com"}.items():
        monkeypatch.setenv(key, value)
    sent = []
    def sender(report, settings):
        sent.append(report)
    assert run(**intraday_files, sender=sender) == 0 and len(sent) == 1
    assert run(**intraday_files, sender=sender) == 0 and len(sent) == 1
    assert run(**intraday_files, sender=sender, force_send=True) == 0 and len(sent) == 2
    assert "No new alerts" in sent[-1].text
    close_files = {**intraday_files, "mode": "CLOSE"}
    assert run(**close_files, sender=sender) == 0 and len(sent) == 3
    state = load_state(intraday_files["state_path"])
    assert state["_meta"] == {"last_report_session": DAY.isoformat(), "last_intraday_session": DAY.isoformat()}
    assert state["XYZ"]["below_95"]["triggered"] and state["XYZ"]["intraday_below_95"]["triggered"]
    assert (intraday_files["output_dir"] / f"{DAY}.html").exists()
    assert (intraday_files["output_dir"] / f"{DAY}-intraday.html").exists()


def test_intraday_failure_preview_missing_email_and_closed_session(intraday_files, monkeypatch):
    for key in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "REPORT_EMAIL"):
        monkeypatch.delenv(key, raising=False)
    state_path = intraday_files["state_path"]
    original = state_path.read_bytes()
    assert run(**intraday_files, dry_run=True) == 0 and state_path.read_bytes() == original
    assert run(**intraday_files) == 0
    assert not load_state(state_path)["XYZ"]["intraday_below_95"]["triggered"]
    for key, value in {"GMAIL_ADDRESS": "from@gmail.com", "GMAIL_APP_PASSWORD": "secret", "REPORT_EMAIL": "to@gmail.com"}.items():
        monkeypatch.setenv(key, value)
    def fail(*args):
        raise EmailDeliveryError("SMTP failed")
    assert run(**intraday_files, sender=fail) == 1
    assert "_meta" not in load_state(state_path)
    before = state_path.read_bytes()
    assert run(**{**intraday_files, "now": NOW.replace(hour=16)}, sender=fail) == 0
    assert state_path.read_bytes() == before


def test_mode_metadata_validation_and_offline_intraday_cli(tmp_path):
    with pytest.raises(ValidationError):
        validate_state({"_meta": {"last_intraday_session": "bad"}})
    state = tmp_path / "state.json"
    assert main(["--demo", "--mode", "INTRADAY", "--state", str(state), "--output-dir", str(tmp_path), "--log-dir", str(tmp_path / "logs")]) == 0
    assert not state.exists() and "StockWatch Intraday" in (tmp_path / "2026-10-06-intraday.html").read_text()


@pytest.mark.parametrize("stamp", ["2026-07-03T14:30:00+00:00", "2026-11-27T18:01:00+00:00"])
def test_intraday_manual_cli_skips_holiday_or_after_early_close(tmp_path, monkeypatch, stamp):
    from stockwatch import daily
    from stockwatch.storage import save_config
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(stamp).astimezone(tz)
    monkeypatch.setattr(daily, "datetime", Clock)
    config = tmp_path / "config.yaml"
    save_config(config, {"watchlist": {}})
    assert main(["--config", str(config), "--mode", "INTRADAY", "--force-send", "--output-dir", str(tmp_path / "out"), "--log-dir", str(tmp_path / "logs")]) == 0
    assert not (tmp_path / "out").exists()


def test_intraday_ticker_failure_does_not_break_other_prices(portfolio_files):
    from stockwatch.services import snapshot
    from stockwatch.storage import load_transactions
    class Provider:
        def get_intraday_quote(self, symbol, session, now=None):
            if symbol == "BAD":
                raise RuntimeError("credentials=must-not-log")
            return Quote(symbol, 90, 100, session=session, price_at=NOW)
    config = load_config(portfolio_files["config_path"])
    config["watchlist"]["BAD"] = {"buy_below": 100}
    portfolio, quotes = snapshot(config, load_transactions(portfolio_files["transactions_path"]), Provider(), DAY, intraday=True, now=NOW)
    assert portfolio["market_value"] == 180 and quotes["BAD"].price is None
    pending, state = evaluate(config, quotes, {}, DAY, mode="INTRADAY")
    assert "BAD" not in state and all(alert.symbol != "BAD" for alert in pending)


def test_pure_watchlist_in_close_summary_but_not_intraday():
    from stockwatch.portfolio import calculate
    config = {"portfolio": {"language": "zh-CN"}, "watchlist": {"PURE": {"thesis": "", "status": "watching"}}}
    quotes = {"PURE": Quote("PURE", 100, 99)}
    facts = [{"Symbol": "PURE", "Current Price": 100, "Daily %": 1, "5D %": 2, "1M %": 3}]
    close = render_report(DAY, calculate({}, {}), quotes, config, [], watchlist_rows=facts)
    assert "关注列表简报（收盘价）" in close.text and "PURE：$100.00 · 当日 +1.00% · 5D +2.00% · 1M +3.00%" in close.text
    assert "PURE" in close.html and "+3.00%" in close.html
    intraday = render_report(DAY, calculate({}, {}), quotes, config, [], mode="INTRADAY", watchlist_rows=facts)
    # The visible intraday report stays short; the hidden AI payload may include the pool.
    visible_html = intraday.html.split('<div aria-hidden="true"', 1)[0]
    assert "关注列表简报" not in intraday.text and "PURE" not in visible_html
    assert "STOCKWATCH_DATA_V1_BEGIN" in intraday.html


def test_intraday_watchlist_top_three_by_absolute_move_without_targets():
    from stockwatch.portfolio import calculate, Position
    from stockwatch.reports import intraday_watchlist_highlights
    from stockwatch.report_data import BEGIN, END
    from decimal import Decimal
    config = {"portfolio": {"language": "zh-CN"}, "watchlist": {symbol: {} for symbol in ("UP", "DOWN", "TIE", "SMALL", "HELD", "BAD", "STALE")}}
    quotes = {"UP": Quote("UP", 107, 100, session=DAY, price_at=NOW),
              "DOWN": Quote("DOWN", 90, 100, session=DAY, price_at=NOW),
              "TIE": Quote("TIE", 110, 100, session=DAY, price_at=NOW),
              "SMALL": Quote("SMALL", 106, 100, session=DAY, price_at=NOW),
              "HELD": Quote("HELD", 130, 100, session=DAY, price_at=NOW),
              "BAD": Quote("BAD", error="Data unavailable"),
              "STALE": Quote("STALE", 140, 100, session=date(2026, 10, 5))}
    book = calculate({"HELD": Position("HELD", Decimal(1), Decimal(100))}, quotes)
    chosen = intraday_watchlist_highlights(config, quotes, {"HELD"}, DAY)
    assert [quote.symbol for quote in chosen] == ["DOWN", "TIE", "UP"]
    report = render_report(DAY, book, quotes, config, [], mode="INTRADAY", generated_at=NOW)
    visible = report.html.split('<div aria-hidden="true"', 1)[0]
    section = visible.split("观察股异动 ≥5%（最多3只）", 1)[1].split("</table>", 1)[0]
    assert all(symbol in section for symbol in ("UP", "DOWN", "TIE"))
    assert all(symbol not in section for symbol in ("SMALL", "HELD", "BAD", "STALE"))
    assert "SMALL" not in visible  # Fourth mover is not duplicated in the generic movement section.
    data = json.loads(report.text.split(BEGIN + "\n", 1)[1].split("\n" + END, 1)[0])
    assert data["visible_watchlist_symbols"] == [quote.symbol for quote in chosen]
    assert json.loads(report.data_json) == data
    close = render_report(DAY, book, quotes, config, [], generated_at=NOW)
    assert "观察股异动 ≥5%（最多3只）" not in close.html


def performance_history(*navs):
    points = [{"date": day, "market_value": nav, "cost": 100.0, "inflow": 0.0, "outflow": 0.0,
               "daily_pl": 0.0, "daily_return_pct": 0.0, "nav": nav, "benchmark_nav": nav,
               "realized_pl": 0.0, "fees": 0.0, "errors": []}
              for day, nav in zip(("2026-10-02", "2026-10-05", "2026-10-06"), navs)]
    return {"version": 1, "fingerprint": "0" * 64, "benchmark": "SPY", "benchmark_base": 100.0, "points": points}


def test_close_report_milestone_all_time_high_and_drawdown():
    from stockwatch.portfolio import calculate
    config = {"portfolio": {"language": "zh-CN"}, "watchlist": {}}
    book = calculate({}, {})
    rising = render_report(DAY, book, {}, config, [], performance=performance_history(100, 110, 121))
    assert "🎉" in rising.subject
    assert "组合净值今日创出历史新高。🎉" in rising.text
    assert "组合净值今日创出历史新高。🎉" in rising.html and "#e6f4ea" in rising.html
    falling = render_report(DAY, book, {}, config, [], performance=performance_history(100, 130, 120))
    assert "🎉" not in falling.subject and "#e6f4ea" not in falling.html
    assert "组合净值距 2026-10-05 的历史高点为 -7.69%。" in falling.text
    assert "2026-10-05" in falling.html
    intraday = render_report(DAY, book, {}, config, [], mode="INTRADAY", generated_at=NOW,
                             performance=performance_history(100, 110, 121))
    assert "历史新高" not in intraday.text and "🎉" not in intraday.subject


def test_intraday_watchlist_empty_one_and_stable_ties():
    from stockwatch.reports import intraday_watchlist_highlights
    config = {"watchlist": {"A": {}, "B": {}}}
    assert not intraday_watchlist_highlights(config, {}, set(), DAY)
    quotes = {"B": Quote("B", 90, 100, session=DAY), "A": Quote("A", 90, 100, session=DAY)}
    assert [q.symbol for q in intraday_watchlist_highlights(config, quotes, set(), DAY)] == ["A", "B"]
    assert [q.symbol for q in intraday_watchlist_highlights(config, quotes, {"B"}, DAY)] == ["A"]


def test_intraday_watchlist_five_percent_boundary_and_quiet_pool_hidden():
    from stockwatch.reports import intraday_watchlist_highlights
    from stockwatch.portfolio import calculate
    config = {"portfolio": {}, "watchlist": {"AT": {}, "QUIET": {}, "LOSS": {}}}
    quotes = {"AT": Quote("AT", 105, 100, session=DAY),
              "QUIET": Quote("QUIET", 104.99, 100, session=DAY),
              "LOSS": Quote("LOSS", 95, 100, session=DAY)}
    assert [q.symbol for q in intraday_watchlist_highlights(config, quotes, set(), DAY)] == ["AT", "LOSS"]
    report = render_report(DAY, calculate({}, {}), quotes, config, [], mode="INTRADAY", generated_at=NOW)
    visible = report.html.split('<div aria-hidden="true"', 1)[0]
    assert "QUIET" not in visible
    assert "QUIET" in report.data_json
    quiet_config = {"portfolio": {}, "watchlist": {"QUIET": {}}}
    quiet = render_report(DAY, calculate({}, {}), {"QUIET": quotes["QUIET"]}, quiet_config, [], mode="INTRADAY", generated_at=NOW)
    assert "Watchlist moves" not in quiet.html and "QUIET" in quiet.data_json
