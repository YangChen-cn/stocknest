"""Small report schedule model shared by UI, CLI and external scheduler."""
from copy import deepcopy
from datetime import date, timedelta
import re

MODES = ("INTRADAY", "CLOSE", "WEEKLY", "MONTHLY")
DEFAULTS = {
    "INTRADAY": {"enabled": True, "time": "10:23", "days": [0, 1, 2, 3, 4]},
    "CLOSE": {"enabled": True, "time": "18:53", "days": [0, 1, 2, 3, 4]},
    "WEEKLY": {"enabled": False, "time": "18:53", "days": [4]},
    "MONTHLY": {"enabled": False, "time": "18:53", "days": [0, 1, 2, 3, 4]},
}


def report_settings(raw=None):
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - set(MODES):
        raise ValueError("Invalid report schedule.")
    result = deepcopy(DEFAULTS)
    for mode, values in raw.items():
        if not isinstance(values, dict) or set(values) - {"enabled", "time", "days"}:
            raise ValueError("Invalid report schedule.")
        item = {**result[mode], **values}
        days = item["days"]
        if not isinstance(item["enabled"], bool) or not isinstance(item["time"], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", item["time"]):
            raise ValueError("Invalid report schedule.")
        if not isinstance(days, list) or not days or any(type(d) is not int or not 0 <= d <= 4 for d in days) or len(set(days)) != len(days):
            raise ValueError("Invalid report schedule.")
        if mode == "INTRADAY" and not "09:30" <= item["time"] < "16:00" or mode != "INTRADAY" and item["time"] < "16:05":
            raise ValueError("Report time must be within regular trading hours for intraday, or after 16:05 for closing summaries.")
        if mode == "WEEKLY" and len(days) != 1:
            raise ValueError("Weekly report requires one weekday.")
        result[mode] = {**item, "days": sorted(days)}
    return result


def scheduled_due(config, mode, now):
    """Delayed same-day triggers may catch up, but never send early."""
    from stockwatch.calendar import NY
    from stockwatch.history import sessions
    item = report_settings(config.get("reports"))[mode]
    local = now.astimezone(NY)
    day = local.date()
    if not item["enabled"] or day.weekday() not in item["days"] or local.strftime("%H:%M") < item["time"]:
        return False
    if mode == "MONTHLY":
        eligible = [d for d in sessions(day.replace(day=1), day) if d.weekday() in item["days"]]
        return bool(eligible) and day == eligible[0]
    return True


def summary_period(mode, session: date):
    """Weekly: current week to cutoff. Monthly: previous complete month."""
    from stockwatch.calendar import previous_session
    if mode == "WEEKLY":
        start = session - timedelta(days=session.weekday())
        end = session
        key = f"{session.isocalendar().year}-W{session.isocalendar().week:02d}"
    elif mode == "MONTHLY":
        last_day = session.replace(day=1) - timedelta(days=1)
        start = last_day.replace(day=1)
        end = previous_session(last_day + timedelta(days=1))
        key = start.strftime("%Y-%m")
    else:
        raise ValueError("Invalid summary mode")
    return start, end, key
