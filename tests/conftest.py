from datetime import date

import pytest

from stockwatch.providers.base import Quote
from stockwatch.storage import save_config, save_transactions

DAY = date(2026, 10, 6)


class FakeProvider:
    def __init__(self, quotes=None):
        self.quotes = quotes or {"XYZ": Quote("XYZ", 90, 100, 120, 60, DAY)}

    def get_quote(self, symbol, session=None):
        quote = self.quotes.get(symbol)
        if isinstance(quote, Exception):
            raise quote
        return quote or Quote(symbol, error="Data unavailable")


@pytest.fixture
def portfolio_files(tmp_path):
    config = tmp_path / "config.yaml"
    transactions = tmp_path / "transactions.csv"
    state = tmp_path / "state.json"
    save_config(config, {"portfolio": {"base_currency": "USD"}, "watchlist": {
        "XYZ": {"thesis": "Facts only", "alerts": {"below": 95, "daily_move_pct": 5}}}})
    save_transactions(transactions, [{"date": "2026-10-05", "symbol": "XYZ", "side": "BUY", "shares": "2", "price": "80", "note": ""}])
    state.write_text("{}\n")
    return dict(config_path=config, transactions_path=transactions, state_path=state,
                output_dir=tmp_path / "outputs", provider=FakeProvider(), session=DAY)


@pytest.fixture(autouse=True)
def isolate_optional_local_gmail(tmp_path, monkeypatch):
    """A developer's saved local Gmail must never influence offline tests."""
    from stockwatch.notifications import local
    monkeypatch.setattr(local, "ROOT", tmp_path / "local-profile")
