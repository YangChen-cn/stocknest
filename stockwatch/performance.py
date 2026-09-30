"""Daily holdings-only performance. No cash account, dividends or tax lots."""
from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import date
from decimal import Decimal
from pathlib import Path

from stockwatch.calendar import previous_session
from stockwatch.history import period_start, sessions
from stockwatch.portfolio import ZERO, accounting
from stockwatch.storage import ValidationError, atomic_write, ticker

DEFAULT_BENCHMARK = "SPY"
VERSION = 1
logger = logging.getLogger(__name__)
NUMBERS = ("market_value", "cost", "inflow", "outflow", "daily_pl", "daily_return_pct", "nav", "benchmark_nav", "realized_pl", "fees")


def fingerprint(transactions, benchmark: str) -> str:
    rows = [[tx.date.isoformat(), tx.symbol, tx.side, str(tx.shares.normalize()),
             str(tx.price.normalize()), str(tx.fee.normalize())]
            for tx in sorted(transactions, key=lambda tx: tx.date)]
    payload = json.dumps([VERSION, benchmark, rows], separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def validate_history(raw: dict) -> dict:
    if not isinstance(raw, dict) or raw.get("version") != VERSION:
        raise ValidationError("Invalid performance history version.")
    ticker(raw.get("benchmark", ""))
    if not isinstance(raw.get("fingerprint"), str) or len(raw["fingerprint"]) != 64 or not isinstance(raw.get("points"), list):
        raise ValidationError("Invalid performance history.")
    reference = raw.get("benchmark_base")
    if reference is not None and (isinstance(reference, bool) or not isinstance(reference, (int, float)) or not math.isfinite(reference) or reference <= 0):
        raise ValidationError("Invalid benchmark reference price.")
    last = None
    for point in raw["points"]:
        if not isinstance(point, dict):
            raise ValidationError("Invalid performance point.")
        try:
            day = date.fromisoformat(point["date"])
        except (ValueError, KeyError, TypeError):
            raise ValidationError("Invalid performance date.") from None
        if last is not None and day <= last:
            raise ValidationError("Performance dates must be unique and increasing.")
        last = day
        if not isinstance(point.get("errors"), list) or any(not isinstance(error, str) for error in point["errors"]):
            raise ValidationError("Invalid performance data status.")
        for key in NUMBERS:
            if key not in point:
                raise ValidationError("Incomplete performance point.")
            value = point[key]
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValidationError("Invalid performance number.")
        if any(point[key] is not None and point[key] < 0 for key in ("market_value", "cost", "inflow", "outflow", "fees", "nav", "benchmark_nav")):
            raise ValidationError("Negative performance value.")
    return raw


def load_history(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return validate_history(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        raise ValidationError("Invalid performance JSON; rebuild or restore history.") from None


def save_history(path: Path, history: dict) -> None:
    text = json.dumps(validate_history(history), indent=2, sort_keys=True, allow_nan=False) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        atomic_write(path, text)


def daily_result(previous, current, inflow, outflow) -> tuple[Decimal | None, Decimal | None]:
    if previous is None or current is None:
        return None, None
    profit = current - previous - inflow + outflow
    denominator = previous + inflow
    # An inactive, empty portfolio has a neutral day, not a division by zero.
    result = profit / denominator * 100 if denominator else (ZERO if not current and not outflow else None)
    return profit, result


def _prices(provider, symbol, start, end):
    try:
        frame = provider.get_history_range(symbol, start, end)
        prices, splits = {}, {}
        for row in frame.to_dict("records"):
            day = row["date"]
            day = date.fromisoformat(str(day)[:10]) if not isinstance(day, date) else day
            price = Decimal(str(row["close"]))
            if start <= day <= end and price.is_finite() and price > 0:
                prices[day] = price
            ratio = row.get("split_ratio", 0)
            if ratio is not None and math.isfinite(float(ratio)) and float(ratio) not in (0, 1):
                splits[day] = float(ratio)
        return prices, splits
    except Exception as exc:
        logger.warning("%s: historical data unavailable (%s)", symbol, type(exc).__name__)
        return {}, {}


def build_history(transactions, provider, end: date, benchmark: str = DEFAULT_BENCHMARK, *, existing: dict | None = None,
                  rebuild: bool = False) -> dict:
    """Incremental tail refresh, full rebuild on changed inputs, retry gaps."""
    benchmark = ticker(benchmark)
    digest = fingerprint(transactions, benchmark)
    result = {"version": VERSION, "fingerprint": digest, "benchmark": benchmark, "points": []}
    active = [tx for tx in transactions if tx.date <= end]
    if not active:
        return result
    first = min(tx.date for tx in active)
    days = sessions(first, end)
    invalid = sorted({tx.date for tx in active} - set(days))
    if invalid:
        raise ValidationError("Transactions must use NYSE session dates: {dates}", dates=", ".join(map(str, invalid)))
    baseline = previous_session(first)
    reuse = existing and not rebuild and existing["fingerprint"] == digest
    old = [p for p in existing["points"] if date.fromisoformat(p["date"]) <= end] if reuse else []
    # Recompute the latest day, or the earliest gap, so delayed bars can repair it.
    tail = date.fromisoformat(old[-1]["date"]) if old else first
    gaps = [date.fromisoformat(p["date"]) for p in old[1:] if p["errors"]]
    if gaps:
        tail = min(tail, min(gaps))
    tail = max(first, tail)
    prefix = [p for p in old if date.fromisoformat(p["date"]) < tail]
    start = previous_session(tail)
    symbols = sorted({tx.symbol for tx in active})
    prices, actions = {}, {}
    for symbol in set(symbols) | {benchmark}:
        # Persist the one reference price, not the entire benchmark price history.
        request_start = baseline if symbol == benchmark and (not reuse or existing.get("benchmark_base") is None) else start
        prices[symbol], actions[symbol] = _prices(provider, symbol, request_start, end)
    if reuse and old and any(split_day >= date.fromisoformat(old[-1]["date"]) for split_day in actions[benchmark]):
        return build_history(transactions, provider, end, benchmark, rebuild=True)
    # Split-adjusted bars change the past. Any relevant split invalidates reuse;
    # block the whole affected security segment rather than invent share events.
    split_symbols = set()
    for symbol in symbols:
        for split_day in actions[symbol]:
            earliest_buy = min(tx.date for tx in active if tx.symbol == symbol and tx.side == "BUY")
            if earliest_buy < split_day <= end:
                split_symbols.add(symbol)
    if split_symbols and prefix:
        return build_history(transactions, provider, end, benchmark, rebuild=True)
    if not prefix:
        base = dict.fromkeys(NUMBERS, 0.0)
        base.update(date=baseline.isoformat(), nav=100.0, benchmark_nav=100.0, errors=[])
        prefix = [base]
    result["points"] = prefix
    nav = Decimal(str(prefix[-1]["nav"])) if prefix[-1]["nav"] is not None else None
    previous_value = Decimal(str(prefix[-1]["market_value"])) if prefix[-1]["market_value"] is not None else None
    reference = existing.get("benchmark_base") if reuse else None
    benchmark_base = Decimal(str(reference)) if reference is not None else prices[benchmark].get(baseline)
    result["benchmark_base"] = float(benchmark_base) if benchmark_base is not None else None
    for day in (day for day in days if day >= tail):
        book = accounting(active, day)
        errors = []
        value = ZERO
        for symbol, held in book["positions"].items():
            price = prices[symbol].get(day)
            if symbol in split_symbols:
                errors.append(f"{symbol}: stock split requires ledger reconciliation")
                value = None
            elif price is None:
                errors.append(f"{symbol}: closing price unavailable")
                value = None
            elif value is not None:
                value += held.shares * price
        # Once a split has affected historical holdings, never bridge its NAV gap.
        if split_symbols:
            errors.extend(f"{symbol}: historical split protection" for symbol in sorted(split_symbols) if not any(e.startswith(symbol + ":") for e in errors))
            value = None
        today = [tx for tx in active if tx.date == day]
        inflow = sum((tx.shares * tx.price + tx.fee for tx in today if tx.side == "BUY"), ZERO)
        outflow = sum((tx.shares * tx.price - tx.fee for tx in today if tx.side == "SELL"), ZERO)
        profit, change = daily_result(previous_value, value, inflow, outflow)
        nav = nav * (1 + change / 100) if nav is not None and change is not None else None
        bp = prices[benchmark].get(day)
        benchmark_nav = bp / benchmark_base * 100 if bp is not None and benchmark_base else None
        if benchmark_nav is None:
            errors.append(f"{benchmark}: benchmark price unavailable")
        values = dict(market_value=value, cost=sum((p.cost for p in book["positions"].values()), ZERO),
                      inflow=inflow, outflow=outflow, daily_pl=profit, daily_return_pct=change, nav=nav,
                      benchmark_nav=benchmark_nav, realized_pl=book["realized_pl"], fees=book["fees"])
        result["points"].append({"date": day.isoformat(), **{k: float(v) if v is not None else None for k, v in values.items()}, "errors": errors})
        previous_value = value
    return validate_history(result)


def update_history(path, transactions, provider, end, benchmark=DEFAULT_BENCHMARK, *, rebuild=False, persist=True):
    try:
        old = load_history(path)
    except ValidationError:
        if not rebuild:
            raise
        old = None
    new = build_history(transactions, provider, end, benchmark, existing=old, rebuild=rebuild)
    changed = old is not None and (old["fingerprint"] != new["fingerprint"] or rebuild)
    failed_rebuild = changed and any(p["nav"] is None for p in new["points"])
    if persist and not failed_rebuild:
        save_history(path, new)
    elif failed_rebuild:
        logger.warning("Performance rebuild incomplete; original history preserved (stale)")
    return new


def period_returns(history, period="ALL") -> dict:
    empty = {"portfolio": None, "benchmark": None, "excess": None}
    if not history or len(history["points"]) < 2:
        return empty
    points = history["points"]
    end = date.fromisoformat(points[-1]["date"])
    start = points[0]["date"] if period == "ALL" else period_start(end, period).isoformat()
    lookup = {p["date"]: p for p in points}
    anchor = lookup.get(start)
    if anchor is None:
        return empty
    window = [p for p in points if start <= p["date"] <= end.isoformat()]
    result = {}
    for name, key in (("portfolio", "nav"), ("benchmark", "benchmark_nav")):
        first, last = anchor[key], points[-1][key]
        result[name] = (last / first - 1) * 100 if first and last is not None and all(p[key] is not None for p in window) else None
    result["excess"] = result["portfolio"] - result["benchmark"] if all(result[k] is not None for k in ("portfolio", "benchmark")) else None
    return result
