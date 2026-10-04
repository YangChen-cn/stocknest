from datetime import date
from decimal import Decimal
import copy

import pandas as pd
import pytest

from stockwatch.history import period_start, price_return, sessions
from stockwatch.performance import (build_history, daily_result, fingerprint, load_history, milestone_status,
                                    period_returns, save_history, update_history)
from stockwatch.portfolio import accounting
from stockwatch.providers.base import Quote
from stockwatch.services import snapshot
from stockwatch.storage import ValidationError, load_transactions, save_transactions, validate_transactions

FIRST, SECOND, THIRD, FOURTH = date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)


def tx(day=FIRST, side="BUY", shares=1, price=100, fee=0, symbol="XYZ"):
    return {"date": day.isoformat(), "symbol": symbol, "side": side, "shares": shares, "price": price, "fee": fee}


class Prices:
    def __init__(self, values=None, missing=(), split=None, fail=()):
        self.values = values or {}
        self.missing = set(missing)
        self.split = split or {}
        self.fail = set(fail)
        self.calls = []

    def get_history_range(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        if symbol in self.fail:
            raise RuntimeError("Do not expose credentials")
        return pd.DataFrame([{"date": day, "close": self.values.get((symbol, day), 100),
                              "split_ratio": self.split.get((symbol, day), 0)}
                             for day in sessions(start, end) if (symbol, day) not in self.missing])


def test_exact_windows_and_insufficient_history():
    assert period_start(THIRD, "5D") == date(2026, 9, 23)
    assert period_start(date(2026, 3, 31), "1M") == date(2026, 2, 27)
    assert period_start(date(2026, 8, 3), "1M") == date(2026, 7, 2)  # July 3 holiday
    for period in ("5D", "1M", "3M", "6M", "1Y"):
        start = period_start(THIRD, period)
        frame = pd.DataFrame({"date": [start, THIRD], "close": [80, 100]})
        assert price_return(frame, THIRD, period) == 25
        assert price_return(frame.tail(1), THIRD, period) is None
    assert len(sessions(period_start(THIRD, "5D"), THIRD)) == 6


def test_flows_do_not_create_returns_and_empty_days_preserve_nav():
    rows = [tx(), tx(SECOND, "BUY", 2), tx(THIRD, "SELL", 3), tx(FOURTH)]
    history = build_history(validate_transactions(rows), Prices(), FOURTH)
    assert [p["nav"] for p in history["points"]] == [100] * 5
    assert [p["daily_pl"] for p in history["points"]] == [0] * 5
    assert history["points"][-2]["market_value"] == 0
    assert period_returns(history)["portfolio"] == 0
    assert period_returns(history, "5D")["portfolio"] is None
    assert daily_result(Decimal(0), Decimal(0), Decimal(0), Decimal(0)) == (0, 0)


def test_first_day_execution_gain_fees_and_realized_profit():
    ledger = validate_transactions([tx(price=100, fee=1), tx(SECOND, "SELL", ".5", 120, 1)])
    book = accounting(ledger, SECOND)
    assert book["positions"]["XYZ"].cost == Decimal("50.5")
    assert book["realized_pl"] == Decimal("8.5") and book["fees"] == 2
    history = build_history(ledger, Prices({("XYZ", SECOND): 120}), SECOND)
    assert history["points"][1]["nav"] == pytest.approx(100 / 101 * 100)
    last = history["points"][-1]
    assert last["daily_pl"] == 19 and last["daily_return_pct"] == 19
    assert last["market_value"] - last["cost"] + last["realized_pl"] == 18
    assert last["fees"] == 2
    first_gain = build_history(validate_transactions([tx(price=90)]), Prices(), FIRST)
    assert first_gain["points"][-1]["nav"] == pytest.approx(100 / 90 * 100)


def test_sold_symbols_future_trades_and_same_day_order():
    ledger = validate_transactions([tx(), tx(FIRST, "SELL"), tx(SECOND, symbol="ABC"), tx(FOURTH, symbol="FUTURE")])
    provider = Prices()
    result = build_history(ledger, provider, THIRD)
    assert {call[0] for call in provider.calls} == {"XYZ", "ABC", "SPY"}
    assert result["points"][1]["market_value"] == 0
    assert result["points"][-1]["market_value"] == 100
    with pytest.raises(ValidationError):
        validate_transactions([tx(side="SELL"), tx()])


def test_benchmark_failure_does_not_block_portfolio_and_missing_price_creates_gap():
    ledger = validate_transactions([tx()])
    bad = build_history(ledger, Prices(missing=[("XYZ", SECOND)], fail=["SPY"]), THIRD)
    assert bad["points"][1]["nav"] == 100
    assert bad["points"][2]["market_value"] is None
    assert bad["points"][3]["market_value"] == 100 and bad["points"][3]["nav"] is None
    assert period_returns(bad) == {"portfolio": None, "benchmark": None, "excess": None}
    repaired = build_history(ledger, Prices(), THIRD, existing=bad)
    assert repaired == build_history(ledger, Prices(), THIRD)


def test_incremental_idempotence_rebuild_and_note_fingerprint(tmp_path):
    ledger = validate_transactions([tx()])
    path = tmp_path / "history.json"
    first = update_history(path, ledger, Prices(), SECOND)
    provider = Prices()
    updated = update_history(path, ledger, provider, THIRD)
    assert next(call for call in provider.calls if call[0] == "XYZ")[1] == FIRST
    assert updated == build_history(ledger, Prices(), THIRD)
    assert len(updated["points"]) == 4
    update_history(path, ledger, Prices(), THIRD)
    assert load_history(path) == updated
    note = validate_transactions([{**tx(), "note": "new note"}])
    assert fingerprint(note, "SPY") == fingerprint(ledger, "SPY")
    assert fingerprint(ledger, "OTHER") != fingerprint(ledger, "SPY")
    changed = validate_transactions([tx(price=90)])
    before = path.read_bytes()
    update_history(path, changed, Prices(fail=["XYZ"]), THIRD)
    assert path.read_bytes() == before  # Failed rebuild preserves the original.
    update_history(path, changed, Prices(), THIRD, persist=False)
    assert path.read_bytes() == before
    assert first["points"][-1]["date"] == SECOND.isoformat()


def test_split_protection_even_when_encountered_incrementally():
    ledger = validate_transactions([tx()])
    old = build_history(ledger, Prices(), SECOND)
    new = build_history(ledger, Prices(split={("XYZ", THIRD): 2}), THIRD, existing=old)
    assert all(p["nav"] is None for p in new["points"][1:])
    assert "split" in new["points"][-1]["errors"][0]


def milestone_history(*navs):
    points = [{"date": day.isoformat(), "nav": value} for day, value in zip((FIRST, SECOND, THIRD, FOURTH), navs)]
    return {"version": 1, "fingerprint": "0" * 64, "benchmark": "SPY", "points": points}


def test_milestone_status_new_high_drawdown_and_gaps():
    assert milestone_status(None) is None and milestone_status({"points": []}) is None
    assert milestone_status(milestone_history(100)) is None
    status = milestone_status(milestone_history(100, None, 105))
    assert status == {"new_high": True, "current_nav": 105.0, "peak_nav": 100.0, "peak_date": FIRST.isoformat(),
                      "drawdown_pct": None, "as_of": THIRD.isoformat(), "basis": "session"}
    status = milestone_status(milestone_history(100, 130, 120))
    assert status["new_high"] is False and status["peak_date"] == SECOND.isoformat() and status["peak_nav"] == 130
    assert status["drawdown_pct"] == pytest.approx((120 / 130 - 1) * 100)
    status = milestone_status(milestone_history(100, 100))
    assert status["new_high"] is False and status["drawdown_pct"] == 0


def test_milestone_status_intraday_estimate_requires_fresh_history():
    history = milestone_history(100, 110)
    status = milestone_status(history, estimated_return_pct=Decimal("2"), as_of=THIRD)
    # previous_session(THIRD) == SECOND, so the live return may be chained on.
    assert status["basis"] == "intraday" and status["new_high"] and status["as_of"] == THIRD.isoformat()
    assert status["current_nav"] == pytest.approx(112.2) and status["peak_nav"] == 110
    status = milestone_status(history, estimated_return_pct=None, as_of=THIRD)
    assert status["basis"] == "session" and status["new_high"] and status["as_of"] == SECOND.isoformat()
    stale = milestone_history(100, 90)
    status = milestone_status(stale, estimated_return_pct=0, as_of=THIRD)
    assert status["basis"] == "intraday" and status["drawdown_pct"] == pytest.approx(-10)
    status = milestone_status(stale, estimated_return_pct=5, as_of=FOURTH)
    # A history older than the previous session never bridges the missing days.
    assert status["basis"] == "session" and status["drawdown_pct"] == pytest.approx(-10)


def test_bad_dates_storage_and_atomic_failure(tmp_path, monkeypatch):
    with pytest.raises(ValidationError, match="NYSE"):
        build_history(validate_transactions([tx(date(2026, 9, 27))]), Prices(), THIRD)
    path = tmp_path / "performance.json"
    result = build_history(validate_transactions([tx()]), Prices(), THIRD)
    save_history(path, result)
    before = path.read_bytes()
    invalid = copy.deepcopy(result)
    invalid["points"][-1]["nav"] = float("nan")
    with pytest.raises(ValidationError):
        save_history(path, invalid)
    def fail(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr("stockwatch.storage.os.replace", fail)
    with pytest.raises(OSError):
        save_history(path, build_history(validate_transactions([tx(price=90)]), Prices(), THIRD))
    assert path.read_bytes() == before


def test_csv_legacy_and_optional_fee(tmp_path):
    path = tmp_path / "tx.csv"
    path.write_text("date,symbol,side,shares,price,note\n2026-09-28,XYZ,BUY,1,100,original\n")
    old = load_transactions(path)
    assert old[0].fee == 0
    save_transactions(path, [old[0].row()])
    assert path.read_text().splitlines()[0].endswith(",fee")
    assert load_transactions(path) == old
    for fee in (-1, "NaN", "Infinity"):
        with pytest.raises(ValidationError):
            validate_transactions([tx(fee=fee)])
    with pytest.raises(ValidationError):
        validate_transactions([tx(), tx(SECOND, "SELL", price=100, fee=101)])


def test_snapshot_trade_day_buy_and_full_sell():
    class Provider:
        def get_quote(self, symbol, session=None):
            return Quote(symbol, 120, 100, session=SECOND)
    config = {"portfolio": {}, "watchlist": {}}
    bought, _ = snapshot(config, validate_transactions([tx(SECOND, price=110)]), Provider(), SECOND, closing=True)
    assert bought["daily_pl"] == 10 and bought["daily_pct"] == Decimal(10) / 110 * 100
    sold, quotes = snapshot(config, validate_transactions([tx(), tx(SECOND, "SELL", price=110)]), Provider(), SECOND, closing=True)
    assert sold["daily_pl"] == 10 and sold["daily_pct"] == 10
    assert sold["realized_pl"] == 10 and "XYZ" in quotes


def test_daily_history_independent_of_smtp_and_preview(tmp_path, monkeypatch):
    import json
    from datetime import datetime, timezone
    from stockwatch.daily import run
    from stockwatch.notifications.email import EmailDeliveryError
    from stockwatch.storage import save_config
    config, ledger, state = (tmp_path / name for name in ("config.yaml", "transactions.csv", "state.json"))
    save_config(config, {"portfolio": {"language": "zh-CN"}, "watchlist": {"XYZ": {"alerts": {"below": 110}}}})
    save_transactions(ledger, [tx()])
    state.write_text("{}")
    class Provider(Prices):
        def get_quote(self, symbol, session=None):
            return Quote(symbol, 100, 100, session=session)
        def get_intraday_quote(self, symbol, session, now):
            return self.get_quote(symbol, session)
    history_path = tmp_path / "performance.json"
    options = dict(config_path=config, transactions_path=ledger, state_path=state,
                   output_dir=tmp_path / "outputs", provider=Provider(), session=FIRST,
                   performance_path=history_path)
    for name in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "REPORT_EMAIL"):
        monkeypatch.delenv(name, raising=False)
    assert run(**options) == 0 and load_history(history_path)["points"][-1]["nav"] == 100
    assert json.loads(state.read_text())["XYZ"]["below_110"]["last_notified"] is None
    before = history_path.read_bytes()
    for name in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "REPORT_EMAIL"):
        monkeypatch.setenv(name, "test-secret")
    def fail(*args):
        raise EmailDeliveryError("SMTP failed")
    assert run(**{**options, "session": SECOND}, sender=fail) == 1
    assert load_history(history_path)["points"][-1]["date"] == SECOND.isoformat()
    assert json.loads(state.read_text())["XYZ"]["below_110"]["last_notified"] is None
    before = history_path.read_bytes()
    assert run(**{**options, "session": THIRD}, dry_run=True) == 0
    assert history_path.read_bytes() == before
    options.update(mode="INTRADAY", session=THIRD, now=datetime(2026, 9, 30, 14, 30, tzinfo=timezone.utc))
    assert run(**options, dry_run=True) == 0 and history_path.read_bytes() == before
    report = (tmp_path / "outputs/2026-09-30-intraday.txt").read_text()
    assert "历史截至 2026-09-29" in report
