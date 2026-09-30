"""Pure alert evaluation; notification state is committed only after delivery."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from stockwatch.i18n import language, t

from stockwatch.providers.base import Quote


@dataclass(frozen=True)
class Alert:
    symbol: str
    rule: str
    message: str


def rule_key(kind: str, value: float) -> str:
    return f"{kind}_{format(Decimal(str(value)).normalize(), 'f')}"


def report_session_key(mode: str) -> str:
    if mode not in {"CLOSE", "INTRADAY"}:
        raise ValueError("Report mode must be CLOSE or INTRADAY")
    return "last_report_session" if mode == "CLOSE" else "last_intraday_session"


def evaluate(config: dict, quotes: dict[str, Quote], state: dict, session: date,
             *, mode: str = "CLOSE") -> tuple[list[Alert], dict]:
    report_session_key(mode)
    armed_state = deepcopy(state)
    lang = language(config)
    pending = []
    for symbol, entry in config["watchlist"].items():
        quote = quotes.get(symbol)
        if quote is None or quote.price is None or quote.error or quote.session != session:
            continue
        rules = dict(entry.get("alerts", {}))
        if "buy_below" in entry:
            rules["buy_below"] = entry["buy_below"]
        for kind, threshold in rules.items():
            key = ("intraday_" if mode == "INTRADAY" else "") + rule_key(kind, threshold)
            item = armed_state.setdefault(symbol, {}).setdefault(key, {"triggered": False, "last_notified": None})
            if kind in {"below", "buy_below"}:
                if quote.price > threshold:
                    item["triggered"] = False
                elif not item["triggered"] and item.get("last_notified") != session.isoformat():
                    message = "{symbol} is at ${price:.2f}, at or below candidate price ${threshold:.2f}." if kind == "buy_below" else "{symbol} is at ${price:.2f}, at or below target price ${threshold:.2f}."
                    pending.append(Alert(symbol, key, t(message, lang, symbol=symbol, price=quote.price, threshold=threshold)))
            elif kind == "daily_move_pct" and quote.daily_move_pct is not None:
                if abs(quote.daily_move_pct) >= threshold and item.get("last_notified") != session.isoformat():
                    pending.append(Alert(symbol, key, t("{symbol} moved {change:+.2f}% today (configured threshold {threshold:g}%).", lang, symbol=symbol, change=quote.daily_move_pct, threshold=threshold)))
    return pending, armed_state


def mark_delivered(state: dict, alerts: list[Alert], session: date, *, mode: str = "CLOSE") -> dict:
    state = deepcopy(state)
    for alert in alerts:
        state.setdefault(alert.symbol, {})[alert.rule] = {"triggered": True, "last_notified": session.isoformat()}
    state.setdefault("_meta", {})[report_session_key(mode)] = session.isoformat()
    return state


def target_distances(config: dict, quotes: dict[str, Quote]) -> list[str]:
    result = []
    lang = language(config)
    for symbol, item in config["watchlist"].items():
        threshold = item.get("buy_below", item.get("alerts", {}).get("below"))
        quote = quotes.get(symbol)
        if threshold is not None and quote is not None and quote.price is not None:
            distance = (quote.price / threshold - 1) * 100
            relation = "above" if distance > 0 else "below" if distance < 0 else "at"
            result.append(t("{symbol} is {distance:.2f}% {relation} target price ${threshold:.2f}.", lang, symbol=symbol, distance=abs(distance), relation=t(relation, lang), threshold=threshold))
    return result
