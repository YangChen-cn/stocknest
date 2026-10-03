"""Small report schedule model shared by UI, CLI and external scheduler."""
from copy import deepcopy
from datetime import date, timedelta
import re
from zoneinfo import ZoneInfo

MODES = ("INTRADAY", "CLOSE", "WEEKLY", "MONTHLY")
PERIODIC = ("WEEKLY", "MONTHLY")
DEFAULTS = {
    "INTRADAY": {"enabled": True, "time": "10:23", "days": [0, 1, 2, 3, 4]},
    "CLOSE": {"enabled": True, "time": "18:53", "days": [0, 1, 2, 3, 4]},
    "WEEKLY": {"enabled": False, "time": "10:52"},
    "MONTHLY": {"enabled": False, "time": "10:52"},
}


def report_timezone(mode):
    return ZoneInfo("Asia/Hong_Kong" if mode in PERIODIC else "America/New_York")


def report_settings(raw=None):
    raw = {} if raw is None else raw
    if not isinstance(raw, dict) or set(raw) - set(MODES):
        raise ValueError("Invalid report schedule.")
    result = deepcopy(DEFAULTS)
    for mode, values in raw.items():
        if not isinstance(values, dict) or set(values) - {"enabled", "time", "days"}:
            raise ValueError("Invalid report schedule.")
        # Old periodic weekday schedules migrate to the new weekend defaults.
        if mode in PERIODIC and "days" in values:
            values = {"enabled": values.get("enabled", False)}
        item = {**result[mode], **values}
        if not isinstance(item["enabled"], bool) or not isinstance(item["time"], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", item["time"]):
            raise ValueError("Invalid report schedule.")
        if mode not in PERIODIC:
            days = item["days"]
            if not isinstance(days, list) or not days or any(type(d) is not int or not 0 <= d <= 4 for d in days) or len(set(days)) != len(days):
                raise ValueError("Invalid report schedule.")
            if mode == "INTRADAY" and not "09:30" <= item["time"] < "16:00" or mode == "CLOSE" and item["time"] < "16:05":
                raise ValueError("Report time must be within regular trading hours for intraday, or after 16:05 for closing summaries.")
            item["days"] = sorted(days)
        result[mode] = item
    return result


def first_weekend(day):
    first = day.replace(day=1)
    return first if first.weekday() == 6 else first + timedelta(days=(5 - first.weekday()) % 7)


def scheduled_due(config, mode, now):
    """Weekend summaries use Hong Kong dates; daily slots use New York dates."""
    item = report_settings(config.get("reports"))[mode]
    local = now.astimezone(report_timezone(mode))
    if not item["enabled"] or local.strftime("%H:%M") < item["time"]:
        return False
    if mode in PERIODIC:
        return local.weekday() == 5 if mode == "WEEKLY" else local.date() == first_weekend(local.date())
    return local.weekday() in item["days"]


def summary_period(mode, session: date):
    """Reference date is delivery date, not necessarily the quote's session."""
    from stockwatch.calendar import previous_session
    if mode == "WEEKLY":
        start = session - timedelta(days=session.weekday())
        # A weekend/holiday has no close; use the last session within this week.
        end = previous_session(session + timedelta(days=1))
        key = f"{session.isocalendar().year}-W{session.isocalendar().week:02d}"
    elif mode == "MONTHLY":
        last_day = session.replace(day=1) - timedelta(days=1)
        start = last_day.replace(day=1)
        end = previous_session(last_day + timedelta(days=1))
        key = start.strftime("%Y-%m")
    else:
        raise ValueError("Invalid summary mode")
    return start, end, key
