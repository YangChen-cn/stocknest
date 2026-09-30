from datetime import date, datetime, timezone
from unittest.mock import Mock
import json
import subprocess

import pandas as pd
import pytest

from stockwatch.calendar import current_price_session, latest_session, previous_session
from stockwatch.providers.base import DataUnavailable
from stockwatch.providers.openbb_provider import OpenBBProvider, normalize_history, quote_from_history


def clock(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def test_holiday_weekend_and_early_close():
    assert latest_session(clock("2026-07-03T22:30:00"), scheduled=True) is None
    assert latest_session(clock("2026-07-04T22:30:00"), scheduled=True) is None
    assert latest_session(clock("2026-07-04T22:30:00")) == date(2026, 7, 2)
    assert latest_session(clock("2026-11-27T18:01:00"), scheduled=True) == date(2026, 11, 27)
    assert latest_session(clock("2026-11-27T17:59:00"), scheduled=True) is None


def test_dst_session_closes_and_live_session_selection():
    assert latest_session(clock("2026-07-01T19:59:00"), scheduled=True) is None
    assert latest_session(clock("2026-07-01T20:01:00"), scheduled=True) == date(2026, 7, 1)
    assert latest_session(clock("2026-01-06T20:59:00"), scheduled=True) is None
    assert latest_session(clock("2026-01-06T21:01:00"), scheduled=True) == date(2026, 1, 6)
    assert current_price_session(clock("2026-07-01T13:29:00")) == date(2026, 6, 30)
    assert current_price_session(clock("2026-07-01T13:31:00")) == date(2026, 7, 1)
    assert previous_session(date(2026, 7, 6)) == date(2026, 7, 2)
    with pytest.raises(ValueError):
        latest_session(datetime(2026, 1, 1))


def test_normalization_close_and_stale_data():
    frame = normalize_history([{"date": "2026-10-06", "close": 90, "high": 91, "low": 89},
                               {"date": "2026-10-05", "close": 100, "high": 102, "low": 99},
                               {"date": "bad", "close": "NaN"}, {"date": "2026-10-04", "close": float("inf")}])
    assert len(frame) == 2
    quote = quote_from_history("ETF", frame, date(2026, 10, 6))
    assert quote.price == 90 and quote.previous_close == 100 and quote.daily_move_pct == pytest.approx(-10)
    assert quote.year_high == 102 and quote.year_low == 89
    assert quote_from_history("ETF", frame, date(2026, 10, 7)).price is None
    quote = quote_from_history("ETF", frame.tail(1), date(2026, 10, 6))
    assert quote.price == 90 and quote.previous_close is None and quote.error


@pytest.mark.parametrize("raw", [[], [{"date": "2026-10-06"}], [{"date": "2026-10-06", "close": -1}]])
def test_empty_or_invalid_history(raw):
    with pytest.raises(DataUnavailable):
        normalize_history(raw)


def test_timeout_bounded_and_failure_returned(monkeypatch):
    request = Mock(side_effect=subprocess.TimeoutExpired("worker", 0.01))
    monkeypatch.setattr("stockwatch.providers.openbb_provider.subprocess.run", request)
    monkeypatch.setattr("stockwatch.providers.openbb_provider.time.sleep", lambda _: None)
    quote = OpenBBProvider(timeout=0.01).get_quote("XYZ", date(2026, 10, 6))
    assert quote.price is None and "timed out" in quote.error and request.call_count == 2


@pytest.mark.parametrize("period", ["5D", "1M", "3M", "6M", "1Y"])
def test_history_periods_and_exclusive_end(monkeypatch, period):
    captured = []
    def worker(*args, **kwargs):
        payload = json.loads(kwargs["input"])
        captured.append(payload)
        dates = pd.bdate_range(payload["start"], "2026-10-06")
        return subprocess.CompletedProcess(args, 0, json.dumps({"data": [{"date": d.date().isoformat(), "close": 100} for d in dates]}), "")
    monkeypatch.setattr("stockwatch.providers.openbb_provider.subprocess.run", worker)
    history = OpenBBProvider().get_history("DYNAMIC", period, date(2026, 10, 6))
    assert captured[0]["symbol"] == "DYNAMIC" and captured[0]["end"] == "2026-10-07"
    assert not history.empty and (period != "5D" or len(history) == 6)


def test_invalid_period():
    with pytest.raises(ValueError):
        OpenBBProvider().get_history("XYZ", "2Y")
