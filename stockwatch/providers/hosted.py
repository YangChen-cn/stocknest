"""Hosted UI: lightweight online Yahoo first, saved quotes only with matching dates."""
import logging
from time import monotonic

from stockwatch.calendar import current_price_session
from stockwatch.providers.base import DataUnavailable, Quote
from stockwatch.providers.snapshot import SnapshotProvider

logger = logging.getLogger(__name__)


class HostedProvider:
    def __init__(self, online, path, *, timeout_budget=None):
        self.online = online
        self.blocked = False
        self.deadline = monotonic() + timeout_budget if timeout_budget is not None else None
        try:
            self.saved = SnapshotProvider(path)
        except DataUnavailable:
            self.saved = None

    def _online_ready(self):
        if self.deadline is not None:
            remaining = self.deadline - monotonic()
            if remaining <= 0:
                self.blocked = True
            elif hasattr(self.online, 'timeout'):
                self.online.timeout = min(self.online.timeout, remaining)
        return not self.blocked

    def _history_failed(self, exc):
        if any(code in str(exc).lower() for code in ('rate limited', 'timed out', 'worker killed')):
            self.blocked = True

    def _quote(self, method, symbol, day, *args):
        quote = Quote(symbol, session=day, error='Data unavailable: online request blocked')
        if self._online_ready():
            quote = getattr(self.online, method)(symbol, day, *args)
            if quote.price is not None and not quote.error:
                return quote
            if quote.error and any(code in quote.error for code in ('rate limited', 'timed out', 'worker killed')):
                self.blocked = True
        if self.saved is not None:
            saved = self.saved.get_quote(symbol, day)
            # Never substitute a different session or a forming bar for a close.
            if saved.price is not None and not saved.error and (method != 'get_quote' or self.saved.mode == 'CLOSE'):
                logger.info('%s: using dated saved market snapshot', symbol)
                return saved
        return quote

    def get_quote(self, symbol, session=None):
        return self._quote('get_quote', symbol, session or current_price_session())

    def get_intraday_quote(self, symbol, session, now=None):
        return self._quote('get_intraday_quote', symbol, session, now)

    def search(self, query):
        return self.online.search(query)

    def get_history_range(self, symbol, start, end):
        if self._online_ready():
            try:
                return self.online.get_history_range(symbol, start, end)
            except DataUnavailable as exc:
                self._history_failed(exc)
        if self.saved:
            return self.saved.get_history_range(symbol, start, end)
        raise DataUnavailable('Historical data unavailable online and in saved snapshot')

    def get_history(self, symbol, period, as_of=None):
        if self._online_ready():
            try:
                return self.online.get_history(symbol, period, as_of)
            except DataUnavailable as exc:
                self._history_failed(exc)
        if self.saved:
            return self.saved.get_history(symbol, period, as_of)
        raise DataUnavailable('Historical data unavailable online and in saved snapshot')
