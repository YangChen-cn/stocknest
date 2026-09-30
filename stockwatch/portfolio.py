"""Average-cost holdings and mark-to-market calculation, with average-cost realized P/L."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from stockwatch.providers.base import Quote
from stockwatch.storage import Transaction

ZERO = Decimal(0)


@dataclass(frozen=True)
class Position:
    symbol: str
    shares: Decimal
    cost: Decimal

    @property
    def average_cost(self) -> Decimal:
        return self.cost / self.shares if self.shares else ZERO


def positions(transactions: list[Transaction], as_of: date) -> dict[str, Position]:
    return accounting(transactions, as_of)["positions"]


def calculate(held: dict[str, Position], quotes: dict[str, Quote]) -> dict:
    rows = []
    for symbol, position in held.items():
        quote = quotes.get(symbol, Quote(symbol, error="Data unavailable"))
        price = Decimal(str(quote.price)) if quote.price is not None else None
        prev = Decimal(str(quote.previous_close)) if quote.previous_close is not None else None
        value = price * position.shares if price is not None else None
        profit = value - position.cost if value is not None else None
        previous_value = prev * position.shares if prev is not None else None
        daily = value - previous_value if value is not None and previous_value is not None else None
        rows.append({"symbol": symbol, "shares": position.shares, "cost": position.cost,
                     "average_cost": position.average_cost, "price": price,
                     "daily_pct": quote.daily_move_pct, "market_value": value,
                     "unrealized_pl": profit, "return_pct": profit / position.cost * 100 if profit is not None else None,
                     "daily_pl": daily, "previous_value": previous_value, "weight_pct": None,
                     "high_distance_pct": (quote.price / quote.year_high - 1) * 100 if quote.price is not None and quote.year_high else None,
                     "low_distance_pct": (quote.price / quote.year_low - 1) * 100 if quote.price is not None and quote.year_low else None,
                     "error": quote.error, "session": quote.session})
    cost = sum((row["cost"] for row in rows), ZERO)
    complete = all(row["market_value"] is not None for row in rows)
    daily_complete = all(row["daily_pl"] is not None for row in rows)
    value = sum((row["market_value"] for row in rows), ZERO) if complete else None
    daily = sum((row["daily_pl"] for row in rows), ZERO) if daily_complete else None
    previous = sum((row["previous_value"] for row in rows), ZERO) if daily_complete else None
    profit = value - cost if value is not None else None
    for row in rows:
        if value is not None and value > 0 and row["market_value"] is not None:
            row["weight_pct"] = row["market_value"] / value * 100
    return {"holdings": rows, "total_cost": cost, "market_value": value,
            "daily_pl": daily, "daily_pct": daily / previous * 100 if daily is not None and previous else None,
            "unrealized_pl": profit, "return_pct": profit / cost * 100 if profit is not None and cost else None,
            "complete": complete}


def accounting(transactions: list[Transaction], as_of: date) -> dict:
    """Fees are included once: buy fees in cost, sell fees in net proceeds."""
    ledger = {}
    realized = fees = ZERO
    for tx in sorted(transactions, key=lambda tx: tx.date):
        if tx.date > as_of:
            continue
        held = ledger.get(tx.symbol, Position(tx.symbol, ZERO, ZERO))
        fees += tx.fee
        if tx.side == "BUY":
            ledger[tx.symbol] = Position(tx.symbol, held.shares + tx.shares, held.cost + tx.shares * tx.price + tx.fee)
        else:
            if tx.shares > held.shares:
                raise ValueError(f"Oversell: {tx.symbol}")
            realized += tx.shares * (tx.price - held.average_cost) - tx.fee
            remaining = held.shares - tx.shares
            ledger[tx.symbol] = Position(tx.symbol, remaining, held.average_cost * remaining if remaining else ZERO)
    return {"positions": {symbol: held for symbol, held in ledger.items() if held.shares > 0},
            "realized_pl": realized, "fees": fees}
