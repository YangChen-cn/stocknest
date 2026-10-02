"""Dependency-free UTC schedule selection before installing the daily runtime."""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


# Separate cron expressions identify the intended slot even when GitHub is late.
UTC_SCHEDULES = {
    "30 14 * * 1-5": ("INTRADAY", -4),
    "30 15 * * 1-5": ("INTRADAY", -5),
    "0 23 * * 1-5": ("CLOSE", -4),
    "0 0 * * 2-6": ("CLOSE", -5),
}


def scheduled_mode(cron: str, now: datetime) -> str | None:
    """Keep the season's slot; daily still validates NYSE sessions and dedupes.

    Do not require an exact execution hour: delayed valid jobs may catch up.
    DST changes on Sundays, outside these weekday New York report slots.
    """
    if now.tzinfo is None:
        raise ValueError("Schedule time must be timezone-aware")
    mode, offset = UTC_SCHEDULES[cron]
    return mode if now.astimezone(ZoneInfo("America/New_York")).utcoffset() == timedelta(hours=offset) else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cron", default="")
    parser.add_argument("--mode", choices=("CLOSE", "INTRADAY"), default="CLOSE")
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    mode = scheduled_mode(args.cron, now) if args.cron else args.mode
    print(f"UTC={now.isoformat()} NY={now.astimezone(ZoneInfo('America/New_York')).isoformat()} "
          f"cron={args.cron or 'manual'} mode={mode or 'SKIP (inactive DST slot)'}")
    with args.github_output.open("a", encoding="utf-8") as output:
        output.write(f"should_run={str(mode is not None).lower()}\nmode={mode or ''}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
