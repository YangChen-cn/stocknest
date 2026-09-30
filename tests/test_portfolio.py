from datetime import date
from decimal import Decimal

import pytest

from stockwatch.portfolio import calculate, positions
from stockwatch.providers.base import Quote
from stockwatch.storage import ValidationError, validate_transactions


def tx(side, shares, price, day="2026-09-28", symbol="XYZ"):
    return {"date": day, "symbol": symbol, "side": side, "shares": shares, "price": price, "note": ""}


def test_weighted_cost_partial_sale_full_sale_and_rebuy():
    rows = [tx("BUY", 2, 10), tx("BUY", 2, 20), tx("SELL", 1, 100)]
    held = positions(validate_transactions(rows), date(2026, 9, 30))["XYZ"]
    assert held.shares == 3 and held.cost == 45 and held.average_cost == 15
    rows.append(tx("SELL", 3, 1))
    assert positions(validate_transactions(rows), date(2026, 9, 30)) == {}
    rows.append(tx("BUY", ".5", 8))
    held = positions(validate_transactions(rows), date(2026, 9, 30))["XYZ"]
    assert held.shares == Decimal(".5") and held.cost == 4 and held.average_cost == 8


def test_future_trades_excluded_and_dates_sorted_stably():
    rows = [tx("BUY", 2, 20, "2026-10-05"), tx("BUY", 1, 10), tx("SELL", ".5", 40)]
    held = positions(validate_transactions(rows), date(2026, 9, 30))["XYZ"]
    assert held.shares == Decimal(".5") and held.cost == 5


def test_same_day_order_and_future_oversell_rejected():
    with pytest.raises(ValidationError):
        validate_transactions([tx("SELL", 1, 10), tx("BUY", 1, 10)])
    with pytest.raises(ValidationError):
        validate_transactions([tx("BUY", 1, 10), tx("SELL", 2, 10, "2026-10-05")])


@pytest.mark.parametrize("field,value", [("shares", "NaN"), ("shares", "Infinity"), ("shares", 0), ("shares", -1),
                                         ("price", 0), ("price", "-Infinity"), ("price", "abc"), ("date", "2026-02-30"),
                                         ("side", "SHORT"), ("symbol", "bad symbol"), ("date", "20260928"), ("date", "2026-W40-1")])
def test_invalid_transactions(field, value):
    row = tx("BUY", 1, 10)
    row[field] = value
    with pytest.raises(ValidationError):
        validate_transactions([row])


def test_all_metrics_and_weights():
    held = positions(validate_transactions([tx("BUY", 2, 80), tx("BUY", 1, 100, symbol="ABC")]), date(2026, 9, 30))
    data = calculate(held, {"XYZ": Quote("XYZ", 100, 90, 125, 50), "ABC": Quote("ABC", 120, 110, 150, 60)})
    assert data["total_cost"] == 260 and data["market_value"] == 320
    assert data["unrealized_pl"] == 60 and data["daily_pl"] == 30
    assert data["daily_pct"] == Decimal(30) / 290 * 100
    assert data["return_pct"] == Decimal(60) / 260 * 100
    row = data["holdings"][0]
    assert row["weight_pct"] == Decimal("62.5")
    assert row["return_pct"] == 25
    assert row["high_distance_pct"] == pytest.approx(-20)
    assert row["low_distance_pct"] == 100


def test_missing_price_does_not_count_as_zero():
    held = positions(validate_transactions([tx("BUY", 1, 80), tx("BUY", 1, 100, symbol="ABC")]), date(2026, 9, 30))
    data = calculate(held, {"XYZ": Quote("XYZ", 100, 90)})
    assert data["total_cost"] == 180
    assert data["market_value"] is None and data["unrealized_pl"] is None and data["daily_pl"] is None
    assert all(row["weight_pct"] is None for row in data["holdings"])
    assert data["holdings"][0]["market_value"] == 100


def test_missing_previous_close_only_disables_daily_metrics():
    held = positions(validate_transactions([tx("BUY", 1, 80)]), date(2026, 9, 30))
    data = calculate(held, {"XYZ": Quote("XYZ", 100)})
    assert data["market_value"] == 100 and data["daily_pl"] is None


def test_empty_portfolio_is_safe():
    data = calculate({}, {})
    assert data["market_value"] == 0 and data["daily_pl"] == 0 and data["return_pct"] is None
