"""Dependency-free UTC schedule selection before installing the daily runtime."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo


# Separate cron expressions identify the intended slot even when GitHub is late.
UTC_SCHEDULES = {
    "23 14 * * 1-5": ("INTRADAY", -4),
    "23 15 * * 1-5": ("INTRADAY", -5),
    "53 22 * * 1-5": ("CLOSE", -4),
    "53 23 * * 1-5": ("CLOSE", -5),
}

EXTERNAL_TRIGGER = "cron-job.org"


def configured_trigger(path: Path) -> str:
    """Read scheduler.trigger from the config without a YAML dependency (native by default)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "native"
    in_section = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line[0].isspace():
            if in_section:
                break  # A new top-level key ends the scheduler section.
            in_section = stripped.split(":", 1)[0] == "scheduler" and stripped.endswith(":")
        elif in_section:
            match = re.match(r"trigger:\s*(native|cron-job\.org)\b", stripped)
            if match:
                return match.group(1)
    return "native"


def scheduled_mode(cron: str, now: datetime) -> str | None:
    """Keep the season's slot; daily still validates NYSE sessions and dedupes.

    Do not require an exact execution hour: delayed valid jobs may catch up.
    DST changes on Sundays, outside these weekday New York report slots.
    """
    if now.tzinfo is None:
        raise ValueError("Schedule time must be timezone-aware")
    mode, offset = UTC_SCHEDULES[cron]
    return mode if now.astimezone(ZoneInfo("America/New_York")).utcoffset() == timedelta(hours=offset) else None


def configured_slot(config, mode, now, state):
    """Cheap preflight: do not install OpenBB for disabled/already-sent slots.

    NYSE holidays are still checked by daily with the full calendar.
    """
    from stockwatch.report_settings import report_timezone, scheduled_due
    local = now.astimezone(report_timezone(mode))
    if not scheduled_due(config, mode, now):
        return False
    if mode == "MONTHLY":
        key = "last_monthly_period"
        period = (local.date().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    elif mode == "WEEKLY":
        key = "last_weekly_period"
        year, week, _ = local.date().isocalendar()
        period = f"{year}-W{week:02d}"
    else:
        key = "last_report_session" if mode == "CLOSE" else "last_intraday_session"
        period = local.date().isoformat()
    return state.get("_meta", {}).get(key) != period


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cron", default="")
    parser.add_argument("--mode", choices=("CLOSE", "INTRADAY", "WEEKLY", "MONTHLY"), default="CLOSE")
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--state", type=Path, default=Path("data/state.json"))
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    mode = scheduled_mode(args.cron, now) if args.cron else args.mode
    if mode and args.cron and configured_trigger(args.config) == EXTERNAL_TRIGGER:
        # The dashboard scheduler switch owns the native slots; external dispatches
        # and manual runs are never gated by this.
        print("Native slot skipped: config scheduler.trigger is cron-job.org.")
        mode = None
    if mode and args.scheduled:
        import yaml
        config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
        state = json.loads(args.state.read_text(encoding="utf-8")) if args.state.exists() else {}
        if not configured_slot(config, mode, now, state):
            mode = None
    print(f"UTC={now.isoformat()} NY={now.astimezone(ZoneInfo('America/New_York')).isoformat()} "
          f"cron={args.cron or 'manual'} mode={mode or 'SKIP (inactive slot)'}")
    with args.github_output.open("a", encoding="utf-8") as output:
        output.write(f"should_run={str(mode is not None).lower()}\nmode={mode or ''}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
