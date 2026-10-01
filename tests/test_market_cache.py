from datetime import date, timedelta

import pandas as pd
import pytest

from stockwatch.calendar import previous_session
from stockwatch.providers.base import DataUnavailable
from stockwatch.providers.cached import ClosedSessionProvider, clear_market_cache


class Provider:
    def __init__(self, day, missing=False):
        self.day = day
        self.missing = missing
        self.calls = 0

    def get_history_range(self, symbol, start, end):
        self.calls += 1
        days = [previous_session(self.day), self.day]
        if self.missing:
            days = days[:1]
        return pd.DataFrame([{"date": day, "close": 20 + index, "high": 22, "low": 18}
                             for index, day in enumerate(days)])


def test_disk_reuse_after_restart_and_expiry(tmp_path):
    day = date(2026, 9, 30)
    upstream = Provider(day)
    first = ClosedSessionProvider(upstream, tmp_path, day)
    assert first.get_quote("XYZ").price == 21
    assert first.get_quote("XYZ").previous_close == 20
    assert upstream.calls == 1
    restarted = ClosedSessionProvider(upstream, tmp_path, day)
    assert restarted.get_quote("XYZ").price == 21
    assert upstream.calls == 1
    upstream.day = day + timedelta(days=1)
    assert ClosedSessionProvider(upstream, tmp_path, upstream.day).get_quote("XYZ").session == upstream.day
    assert upstream.calls == 2
    clear_market_cache(tmp_path)
    ClosedSessionProvider(upstream, tmp_path, upstream.day).get_quote("XYZ")
    assert upstream.calls == 3


def test_missing_close_not_cached(tmp_path):
    provider = Provider(date(2026, 9, 30), missing=True)
    cached = ClosedSessionProvider(provider, tmp_path, provider.day)
    for _ in range(2):
        with pytest.raises(DataUnavailable):
            cached.get_quote("XYZ")
    assert provider.calls == 2
    assert not list(tmp_path.glob("*.json"))


def test_corrupt_cache_and_missing_history_anchor(tmp_path):
    (tmp_path / "XYZ.json").write_text("{bad json")
    provider = Provider(date(2026, 9, 30))
    cached = ClosedSessionProvider(provider, tmp_path, provider.day)
    assert cached.get_quote("XYZ").price == 21
    with pytest.raises(DataUnavailable, match="endpoints"):
        cached.get_history("XYZ", "1M")
    assert provider.calls == 1


def test_cache_write_failure_keeps_fresh_quote(tmp_path, monkeypatch):
    from stockwatch.providers import cached
    def fail(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(cached, "atomic_write", fail)
    provider = Provider(date(2026, 9, 30))
    assert ClosedSessionProvider(provider, tmp_path, provider.day).get_quote("XYZ").price == 21


def test_market_memory_key_changes_at_open_close(monkeypatch):
    from stockwatch import ui
    active = [None]
    monkeypatch.setattr(ui, "active_session", lambda: active[0])
    monkeypatch.setattr(ui, "latest_session", lambda: date(2026, 9, 30))
    calls = []
    @ui.market_cache
    def request(symbol):
        calls.append(symbol)
        return len(calls)
    assert request("XYZ") == 1
    assert request("XYZ") == 1
    active[0] = date(2026, 10, 1)
    assert request("XYZ") == 2
    active[0] = None
    # A new completed close requires a new memory entry too.
    monkeypatch.setattr(ui, "latest_session", lambda: date(2026, 10, 1))
    assert request("XYZ") == 3
    request.clear()


def test_market_cache_keeps_functions_separate():
    from stockwatch.ui import market_cache
    @market_cache
    def first(value):
        return "first"
    @market_cache
    def second(value):
        return "second"
    assert first("same") == "first"
    assert second("same") == "second"
    first.clear()
    second.clear()
