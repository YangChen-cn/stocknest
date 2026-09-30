"""Shared application services used by both UI and daily job."""
import logging
from datetime import date, datetime

from stockwatch.i18n import data_status, language

from stockwatch.portfolio import ZERO, accounting, calculate, positions
from stockwatch.calendar import previous_session
from stockwatch.history import price_return
from stockwatch.performance import daily_result
from decimal import Decimal
from stockwatch.providers.base import Instrument, MarketDataProvider, Quote
from stockwatch.storage import Transaction

logger = logging.getLogger(__name__)


def search_instruments(provider: MarketDataProvider, query: str) -> list[Instrument]:
    return provider.search(query)


def candidate_rows(config: dict, held: set[str], quotes: dict[str, Quote], histories: dict, as_of: date | None = None) -> list[dict]:
    """Candidate analytics are facts only; never evaluate or mutate alert state."""
    rows = []
    for symbol, entry in config["watchlist"].items():
        quote = quotes.get(symbol, Quote(symbol))
        history = histories.get(symbol)
        day = as_of or (max(pd_day for pd_day in history.date) if history is not None and not history.empty else None)
        change_5d = price_return(history, day, "5D") if day else None
        change_1m = price_return(history, day, "1M") if day else None
        price, target = quote.price, entry.get("buy_below")
        rows.append({"Symbol": symbol, "Held": symbol in held, "Status": entry.get("status", "watching"),
                     "Current Price": price, "Daily %": quote.daily_move_pct,
                     "5D %": change_5d, "1M %": change_1m, "Buy Below": target,
                     "Distance to Buy Below": (price / target - 1) * 100 if price is not None and target else None,
                     "Distance from 52W High": (price / quote.year_high - 1) * 100 if price is not None and quote.year_high else None,
                     "Distance from 52W Low": (price / quote.year_low - 1) * 100 if price is not None and quote.year_low else None})
    return rows


def snapshot(config: dict, transactions: list[Transaction], provider: MarketDataProvider,
             as_of: date, *, closing: bool = False, intraday: bool = False,
             now: datetime | None = None) -> tuple[dict, dict[str, Quote]]:
    held = positions(transactions, as_of)
    opening = positions(transactions, previous_session(as_of))
    symbols = sorted(set(held) | set(opening) | set(config["watchlist"]))
    quotes = {}
    for symbol in symbols:
        try:
            quote = provider.get_intraday_quote(symbol, as_of, now) if intraday else provider.get_quote(symbol, as_of if closing else None)
        except Exception as exc:
            # Avoid raw provider exception text; it may contain request credentials.
            quote = Quote(symbol, error=f"Data unavailable ({type(exc).__name__})")
        quotes[symbol] = quote
        if quote.error:
            logger.warning("%s: %s", symbol, data_status(quote.error, language(config)))
    portfolio = calculate(held, quotes)
    previous = ZERO
    for symbol, position in opening.items():
        price = quotes[symbol].previous_close
        if price is None:
            previous = None
            break
        previous += position.shares * Decimal(str(price))
    today = [tx for tx in transactions if tx.date == as_of]
    inflow = sum((tx.shares * tx.price + tx.fee for tx in today if tx.side == "BUY"), ZERO)
    outflow = sum((tx.shares * tx.price - tx.fee for tx in today if tx.side == "SELL"), ZERO)
    portfolio["daily_pl"], portfolio["daily_pct"] = daily_result(previous, portfolio["market_value"], inflow, outflow)
    book = accounting(transactions, as_of)
    portfolio.update(realized_pl=book["realized_pl"], fees=book["fees"])
    return portfolio, quotes


def watchlist_summary(config, portfolio, quotes, provider, session):
    """All watched symbols, including pure observers; no notification state."""
    histories = {}
    for symbol in config["watchlist"]:
        try:
            histories[symbol] = provider.get_history(symbol, "1M", session)
        except Exception as exc:
            histories[symbol] = None
            logger.warning("%s: watchlist history unavailable (%s)", symbol, type(exc).__name__)
    return candidate_rows(config, {row["symbol"] for row in portfolio["holdings"]}, quotes, histories, session)
