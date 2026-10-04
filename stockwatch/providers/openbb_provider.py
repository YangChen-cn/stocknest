"""OpenBB/yfinance adapter with per-request subprocess timeouts.

OpenBB imports remain confined to this module. Killing a timed-out worker gives
a real timeout instead of leaving a blocked network thread alive.
"""
from __future__ import annotations

import json
import logging
import math
import subprocess
import sys
import time
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from stockwatch.history import period_start, sessions
from stockwatch.calendar import NY, latest_session, active_session, current_price_session, previous_session
from stockwatch.providers.base import DataUnavailable, Instrument, PERIODS, Quote
from stockwatch.storage import ValidationError, ticker

logger = logging.getLogger(__name__)

# Company-name aliases only; instrument symbols always come from provider results.
NAME_ALIASES = {"苹果": "Apple", "特斯拉": "Tesla", "英伟达": "NVIDIA", "微软": "Microsoft",
                "亚马逊": "Amazon", "谷歌": "Alphabet", "哔哩哔哩": "Bilibili", "优步": "Uber"}
US_EXCHANGES = {"NMS", "NGM", "NCM", "NAS", "NYQ", "ASE", "PCX", "BTS", "BATS", "NYSE", "NASDAQ", "AMEX"}


def normalize_search(raw: list[dict]) -> list[Instrument]:
    results = {}
    for row in raw:
        if not isinstance(row, dict) or not isinstance(row.get("symbol"), str):
            continue
        if str(row.get("quoteType")) not in {"EQUITY", "ETF"} or str(row.get("exchange")) not in US_EXCHANGES:
            continue
        try:
            symbol = ticker(row.get("symbol", ""))
        except ValidationError:
            continue
        results.setdefault(symbol, Instrument(symbol, str(row.get("longname") or row.get("shortname") or symbol),
                                               str(row.get("exchDisp") or row["exchange"]), row["quoteType"]))
    return list(results.values())


def finite_price(value) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError):
        return None


def normalize_history(raw: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(raw)
    if frame.empty or "date" not in frame or "close" not in frame:
        raise DataUnavailable("No historical daily data")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    for name in ("open", "high", "low", "close", "volume", "split_ratio"):
        frame[name] = pd.to_numeric(frame.get(name, pd.Series(index=frame.index, dtype=float)), errors="coerce")
    frame = frame.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date", keep="last")
    frame = frame[frame.close.map(lambda value: finite_price(value) is not None)]
    if frame.empty:
        raise DataUnavailable("No usable historical daily data")
    return frame[["date", "open", "high", "low", "close", "volume", "split_ratio"]].reset_index(drop=True)


def quote_from_history(symbol: str, frame: pd.DataFrame, session: date, *, source: str = "OpenBB / yfinance") -> Quote:
    frame = frame[frame.date <= session]
    if frame.empty or frame.iloc[-1].date != session:
        return Quote(symbol, session=session, source=source, error="Data unavailable: closing data is missing or stale")
    price = finite_price(frame.iloc[-1].close)
    prior = frame[frame.date == previous_session(session)]
    prev = finite_price(prior.iloc[-1].close) if not prior.empty else None
    annual = frame[frame.date >= session - timedelta(weeks=52)]
    return Quote(symbol, price, prev, finite_price(annual.high.max()), finite_price(annual.low.min()),
                 session, datetime.now(timezone.utc), source,
                 "Data unavailable: previous close" if prev is None else None)


def quote_from_intraday(symbol: str, bars: list[dict], history: pd.DataFrame,
                        session: date, now: datetime) -> Quote:
    """OpenBB/yfinance's minute timestamps are naive New York wall-clock time."""
    if active_session(now) != session:
        return Quote(symbol, session=session, error="Data unavailable: no active NYSE session")
    usable = []
    for row in bars:
        if not isinstance(row, dict):
            continue
        stamp = pd.to_datetime(row.get("date"), errors="coerce")
        price = finite_price(row.get("close"))
        if pd.isna(stamp) or price is None:
            continue
        stamp = stamp.to_pydatetime()
        stamp = stamp.replace(tzinfo=NY) if stamp.tzinfo is None else stamp.astimezone(NY)
        if stamp.date() == session and stamp.hour * 60 + stamp.minute >= 570 and stamp <= now:
            usable.append((stamp, price))
    if not usable:
        return Quote(symbol, session=session, error="Data unavailable: intraday prices missing or stale")
    stamp, price = max(usable, key=lambda item: item[0])
    if now - stamp > timedelta(minutes=20):
        return Quote(symbol, session=session, error="Data unavailable: intraday prices missing or stale")
    prior = quote_from_history(symbol, history, previous_session(session))
    return Quote(symbol, price, prior.price, prior.year_high, prior.year_low, session, now,
                 error="Data unavailable: previous close" if prior.price is None else None, price_at=stamp)


class OpenBBProvider:
    def __init__(self, timeout: float = 45, attempts: int = 2, *, lightweight: bool = False):
        self.timeout = timeout
        self.lightweight = lightweight
        self.source = "Yahoo / yfinance" if lightweight else "OpenBB / yfinance"
        self.attempts = attempts
        self._history_cache = {}

    def clear_cache(self):
        self._history_cache.clear()

    def get_intraday_quote(self, symbol: str, session: date, now: datetime | None = None) -> Quote:
        now = now or datetime.now(timezone.utc)
        if active_session(now) != session:
            return Quote(symbol, session=session, error="Data unavailable: no active NYSE session")
        payload = json.dumps({"operation": "intraday", "symbol": symbol, "session": session.isoformat(), "lightweight": self.lightweight})
        for attempt in range(self.attempts):
            try:
                result = subprocess.run([sys.executable, "-m", "stockwatch.providers.worker"], input=payload,
                                        text=True, capture_output=True, timeout=self.timeout)
                if result.returncode != 0:
                    raise DataUnavailable(provider_failure(None, result.returncode))
                response = json.loads(result.stdout.strip().splitlines()[-1])
                if "error" in response:
                    raise DataUnavailable(provider_failure(response.get("error")))
                history = normalize_history(response["history"])
                self._history_cache[(symbol, session - timedelta(days=380), session)] = history
                return replace(quote_from_intraday(symbol, response["bars"], history, session, now), source=self.source)
            except (subprocess.TimeoutExpired, ValueError, IndexError, KeyError, TypeError, OSError, DataUnavailable) as exc:
                failure = "Provider request timed out" if isinstance(exc, subprocess.TimeoutExpired) else str(exc) if isinstance(exc, DataUnavailable) else "Provider returned no usable data"
                logger.warning("%s: intraday provider failure=%s attempt=%s/%s", symbol, failure, attempt + 1, self.attempts)
                if attempt + 1 < self.attempts:
                    time.sleep(1)
        return Quote(symbol, session=session, error=f"Data unavailable: {failure}")

    def search(self, query: str) -> list[Instrument]:
        query = query.strip()
        if not query:
            return []
        if len(query) > 100:
            raise DataUnavailable("Search query too long")
        # Installed OpenBB/yfinance has no EquitySearch endpoint. Use its existing
        # yfinance dependency solely for symbol discovery, in the isolated worker.
        try:
            completed = subprocess.run([sys.executable, "-m", "stockwatch.providers.worker"],
                                       input=json.dumps({"operation": "search", "query": NAME_ALIASES.get(query, query)}),
                                       text=True, capture_output=True, timeout=min(self.timeout, 20))
            response = json.loads(completed.stdout.strip().splitlines()[-1])
            if completed.returncode != 0 or "error" in response or not isinstance(response.get("data"), list):
                raise DataUnavailable("Search unavailable")
            return normalize_search(response["data"])
        except (subprocess.TimeoutExpired, ValueError, IndexError, OSError) as exc:
            raise DataUnavailable("Search unavailable") from exc

    def _request(self, symbol: str, start: date, end: date, *, required_session: date | None = None) -> pd.DataFrame:
        for (cached_symbol, cached_start, cached_end), frame in self._history_cache.items():
            if symbol == cached_symbol and cached_start <= start and end <= cached_end:
                selected = frame[(frame.date >= start) & (frame.date < end)].copy()
                if required_session is None or required_session in set(selected.date):
                    return selected
        payload = json.dumps({"symbol": symbol, "start": start.isoformat(), "end": end.isoformat(), "lightweight": self.lightweight})
        error = "Provider failed"
        partial = None
        for attempt in range(self.attempts):
            try:
                completed = subprocess.run([sys.executable, "-m", "stockwatch.providers.worker"],
                                           input=payload, text=True, capture_output=True, timeout=self.timeout)
                if completed.returncode != 0:
                    raise DataUnavailable(provider_failure(None, completed.returncode))
                # Only sanitized worker protocol data is read, never raw stderr.
                response = json.loads(completed.stdout.strip().splitlines()[-1])
                if "error" in response:
                    raise DataUnavailable(provider_failure(response["error"]))
                frame = normalize_history(response["data"])
                if required_session is not None and required_session not in set(frame.date):
                    partial = frame
                    logger.warning("%s: missing target session; expected=%s latest=%s attempt=%s/%s",
                                   symbol, required_session, frame.date.max(), attempt + 1, self.attempts)
                    if attempt + 1 < self.attempts:
                        time.sleep(3)
                    continue
                self._history_cache[(symbol, start, end)] = frame
                return frame
            except (subprocess.TimeoutExpired, ValueError, IndexError, KeyError, TypeError, OSError, DataUnavailable) as exc:
                error = "Provider request timed out" if isinstance(exc, subprocess.TimeoutExpired) else str(exc) if isinstance(exc, DataUnavailable) else "Provider returned no usable data"
                logger.warning("%s: provider failure=%s attempt=%s/%s", symbol, error, attempt + 1, self.attempts)
                if attempt + 1 < self.attempts:
                    time.sleep(1)
        if partial is not None:
            # Preserve usable older bars for gap reporting, never treat them as the
            # target close or cache a stale response across this job.
            return partial
        raise DataUnavailable(error)

    def get_quote(self, symbol: str, session: date | None = None) -> Quote:
        day = session or current_price_session()
        try:
            # End dates are exclusive in yfinance; include the entire target session.
            frame = self._request(symbol, day - timedelta(days=380), day + timedelta(days=1), required_session=day)
            return quote_from_history(symbol, frame, day, source=self.source)
        except DataUnavailable as exc:
            return Quote(symbol, session=day, source=self.source, error=f"Data unavailable: {exc}")

    def get_history(self, symbol: str, period: str, as_of: date | None = None) -> pd.DataFrame:
        if period not in PERIODS:
            raise ValueError(f"Unsupported period: {period}")
        day = as_of or latest_session()
        start = period_start(day, period)
        frame = self.get_history_range(symbol, start, day)
        if frame.empty or start not in set(frame.date) or day not in set(frame.date):
            raise DataUnavailable("Historical endpoints missing or stale")
        return frame

    def get_history_range(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        if end < start:
            raise ValueError("End precedes start")
        trading_days = sessions(start, end)
        required = trading_days[-1] if trading_days else None
        frame = self._request(symbol, start, end + timedelta(days=1), required_session=required)
        return frame[(frame.date >= start) & (frame.date <= end)].reset_index(drop=True)


def provider_failure(code, returncode=0):
    if returncode < 0:
        return "Provider worker killed (resource or process limit)"
    return {"rate_limited": "Provider rate limited", "timeout": "Provider request timed out",
            "network": "Provider network error", "dependency": "Provider dependency unavailable",
            "no_data": "Provider returned no usable data"}.get(code, "Provider returned no usable data")


def failure_code(exc):
    """Classify without emitting upstream text, URLs or credentials."""
    text = str(exc).lower()
    names = type(exc).__name__.lower()
    if "ratelimit" in names or "too many requests" in text or "rate limit" in text or "429" in text:
        return "rate_limited"
    if "timeout" in names or "timed out" in text:
        return "timeout"
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return "dependency"
    if "connection" in names or "resolve host" in text or "network" in text:
        return "network"
    return "no_data"


def lightweight_request(payload):
    """Yahoo data without loading the full OpenBB runtime in a second UI process.

    auto_adjust=False matches OpenBB splits_only: Yahoo prices include split
    adjustments, while dividends are not reinvested or back-adjusted.
    """
    import yfinance as yf
    instrument = yf.Ticker(payload['symbol'])

    def rows(start, end, interval):
        frame = instrument.history(start=start, end=end, interval=interval, auto_adjust=False,
                                   back_adjust=False, actions=True, prepost=False, timeout=12, raise_errors=True)
        if frame.empty:
            raise DataUnavailable('No historical daily data')
        frame = frame.reset_index().rename(columns={'Date': 'date', 'Datetime': 'date', 'Open': 'open',
            'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume', 'Stock Splits': 'split_ratio'})
        # Daily dates are exchange dates, not UTC-midnight conversions.
        frame['date'] = frame['date'].map(lambda value: value.isoformat() if interval != '1d' else value.date().isoformat())
        return json.loads(frame.to_json(orient='records'))

    if payload.get('operation') == 'intraday':
        day = date.fromisoformat(payload['session'])
        return {'bars': rows(day, day + timedelta(days=1), '1m'),
                'history': rows(day - timedelta(days=380), day, '1d')}
    return {'data': rows(payload['start'], payload['end'], '1d')}


def execute_request(payload: dict) -> dict:
    """Executed only in an isolated worker; no secrets are passed to this worker."""
    if payload.get("operation") == "search":
        import yfinance as yf

        result = yf.Search(payload["query"], max_results=15, news_count=0, lists_count=0,
                           recommended=0, timeout=12)
        return {"data": result.quotes}
    if payload.get("lightweight"):
        return lightweight_request(payload)
    from openbb import obb

    if payload.get("operation") == "intraday":
        day = date.fromisoformat(payload["session"])
        def prices(start, end, interval):
            result = obb.equity.price.historical(symbol=payload["symbol"], start_date=start, end_date=end,
                                               interval=interval, provider="yfinance", adjustment="splits_only", extended_hours=False)
            # Keep New York wall-clock strings, not pandas' assumption of UTC for naive dates.
            frame = result.to_df().reset_index()
            frame["date"] = frame["date"].map(str)
            return json.loads(frame.to_json(orient="records"))
        return {"bars": prices(day, day + timedelta(days=1), "1m"),
                "history": prices(day - timedelta(days=380), day, "1d")}
    result = obb.equity.price.historical(symbol=payload["symbol"], start_date=payload["start"],
                                       end_date=payload["end"], interval="1d", provider="yfinance",
                                       adjustment="splits_only", extended_hours=False)
    frame = result.to_df().reset_index()
    return {"data": json.loads(frame.to_json(orient="records", date_format="iso"))}
