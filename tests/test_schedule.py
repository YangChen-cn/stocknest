from datetime import datetime, timedelta
import sys

import pytest

from stockwatch.schedule import UTC_SCHEDULES, main, scheduled_mode


@pytest.mark.parametrize("date,offset", [
    ("2026-03-06", -5), ("2026-03-09", -4),
    ("2026-10-30", -4), ("2026-11-02", -5),
])
def test_season_selects_only_two_report_modes(date, offset):
    now = datetime.fromisoformat(f"{date}T17:00:00+00:00")
    selected = []
    for cron, (mode, expected_offset) in UTC_SCHEDULES.items():
        result = scheduled_mode(cron, now)
        assert result == (mode if expected_offset == offset else None)
        if result:
            selected.append(result)
    assert sorted(selected) == ["CLOSE", "INTRADAY"]


@pytest.mark.parametrize("cron,timestamp,mode", [
    ("23 14 * * 1-5", "2026-10-01T19:42:00+00:00", "INTRADAY"),
    ("53 22 * * 1-5", "2026-10-02T01:53:00+00:00", "CLOSE"),
    ("53 23 * * 1-5", "2026-11-07T00:00:00+00:00", "CLOSE"),
])
def test_delays_and_winter_close(cron, timestamp, mode):
    assert scheduled_mode(cron, datetime.fromisoformat(timestamp)) == mode


def test_cron_hours_map_to_new_york_report_times():
    from zoneinfo import ZoneInfo
    for cron, (mode, offset) in UTC_SCHEDULES.items():
        minute, hour, *_ = cron.split()
        day = "2026-10-01" if offset == -4 else "2026-11-03"
        utc = datetime.fromisoformat(f"{day}T00:00:00+00:00") + timedelta(hours=int(hour), minutes=int(minute))
        ny = utc.astimezone(ZoneInfo("America/New_York"))
        assert (ny.hour, ny.minute) == ((10, 23) if mode == "INTRADAY" else (18, 53))
        assert ny.weekday() < 5


@pytest.mark.parametrize("cron,manual,expected", [
    ("", "INTRADAY", "INTRADAY"), ("", "CLOSE", "CLOSE"),
    ("23 14 * * 1-5", "CLOSE", "INTRADAY"),
    ("23 15 * * 1-5", "INTRADAY", ""),
])
def test_preflight_output(tmp_path, monkeypatch, cron, manual, expected):
    import stockwatch.schedule as schedule
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat("2026-10-01T17:00:00+00:00").astimezone(tz)
    monkeypatch.setattr(schedule, "datetime", FixedClock)
    output = tmp_path / "output"
    monkeypatch.setattr(sys, "argv", ["schedule", "--cron", cron, "--mode", manual, "--github-output", str(output)])
    assert main() == 0
    assert output.read_text() == f"should_run={str(bool(expected)).lower()}\nmode={expected}\n"


def test_invalid_schedule_fails_closed():
    with pytest.raises(KeyError):
        scheduled_mode("0 19 * * 1-5", datetime.fromisoformat("2026-10-01T23:00:00+00:00"))
    with pytest.raises(ValueError):
        scheduled_mode("53 22 * * 1-5", datetime(2026, 10, 1))
