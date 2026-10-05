"""Read-only botanical interpretation of the existing ledger and market snapshot.

No provider calls, notification state, cash accounting, or persistent garden state.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from stockwatch.performance import fingerprint, milestone_status

STAGES = ("Sprout", "Seedling", "Unfurling", "Lush", "Mature")
SPECIES = ("Herb", "Fern", "Rosemary", "Olive", "Hydrangea", "Bamboo",
           "Peony", "Camellia", "Chrysanthemum", "Iris", "Daffodil", "Lotus")
THRESHOLDS = (7, 30, 90, 180)


@dataclass(frozen=True)
class Plant:
    symbol: str
    species: int
    since: date
    days: int
    stage: int


@dataclass(frozen=True)
class GardenEvent:
    kind: str
    day: date
    symbol: str = ""
    stage: int = 0
    days: int = 0


@dataclass(frozen=True)
class Garden:
    as_of: date
    mode: str
    season: str
    weather: str
    plants: tuple[Plant, ...]
    events: tuple[GardenEvent, ...]
    high_dates: tuple[date, ...] = ()


def growth_stage(days: int) -> int:
    return sum(days >= threshold for threshold in THRESHOLDS)


def weather_for(value) -> str:
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            return "Unknown"
        if abs(number) < Decimal("0.005"):
            return "Soft clouds"
        return "Warm light" if number > 0 else "Gentle rain"
    except (InvalidOperation, ValueError, TypeError):
        return "Unknown"


def build_garden(transactions, portfolio, as_of: date, *, mode="CLOSE", history=None,
                 benchmark="SPYM", period_start: date | None = None,
                 period_return=None) -> Garden:
    start = period_start or as_of
    ledger, cycles, events = {}, [], []
    for tx in sorted(transactions, key=lambda tx: tx.date):
        if tx.date > as_of:
            continue
        shares, since = ledger.get(tx.symbol, (Decimal(0), tx.date))
        if tx.side == "BUY":
            kind = "new" if shares == 0 else "water"
            if shares == 0:
                since = tx.date
                cycles.append([tx.symbol, since, None])
            shares += tx.shares
        else:
            shares -= tx.shares
            if shares < 0:
                raise ValueError("Garden requires a validated ledger")
            kind = "archive" if shares == 0 else "prune"
            if shares == 0:
                next(cycle for cycle in reversed(cycles)
                     if cycle[0] == tx.symbol and cycle[2] is None)[2] = tx.date
        ledger[tx.symbol] = shares, since
        if start <= tx.date:
            events.append(GardenEvent(kind, tx.date, tx.symbol,
                                      days=(tx.date - since).days + 1))
    # Include growth milestones for both current and closed cycles in this period.
    if mode in ("WEEKLY", "MONTHLY"):
        for symbol, since, ended in cycles:
            for stage, threshold in enumerate(THRESHOLDS, 1):
                day = since + timedelta(days=threshold - 1)
                if start <= day <= (ended or as_of):
                    events.append(GardenEvent("growth", day, symbol, stage, threshold))
    held = {row["symbol"] for row in portfolio["holdings"]}
    plants = []
    for symbol, (shares, since) in ledger.items():
        if shares > 0 and symbol in held:
            days = (as_of - since).days + 1
            species = int.from_bytes(sha256(symbol.encode()).digest()[:4], "big") % len(SPECIES)
            plants.append(Plant(symbol, species, since, days, growth_stage(days)))
    plants.sort(key=lambda plant: (plant.since, plant.symbol))

    highs = []
    matching = history and history.get("fingerprint") == fingerprint(transactions, benchmark)
    if mode != "INTRADAY" and portfolio.get("complete") and matching:
        # Validate the whole series and target close before examining earlier prefixes.
        status = milestone_status(history, as_of=as_of)
        if status is not None:
            if mode in ("WEEKLY", "MONTHLY"):
                peak = Decimal(str(history["points"][0]["nav"]))
                # The complete series is already validated above. A single pass
                # finds period highs without rechecking every historical prefix.
                for point in history["points"][1:]:
                    day = date.fromisoformat(point["date"])
                    nav = Decimal(str(point["nav"]))
                    if start <= day <= as_of and nav > peak:
                        highs.append(day)
                    peak = max(peak, nav)
            elif status["new_high"]:
                highs.append(as_of)
    events.extend(GardenEvent("high", day) for day in highs)
    # Stable sort keeps same-day transaction order intact.
    events.sort(key=lambda event: event.day)
    season = ("Winter", "Spring", "Summer", "Autumn")[(as_of.month % 12) // 3]
    weather = weather_for(period_return if mode in ("WEEKLY", "MONTHLY") else portfolio.get("daily_pct"))
    return Garden(as_of, mode, season, weather, tuple(plants), tuple(events), tuple(highs))
