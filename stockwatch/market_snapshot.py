"""Small market-only snapshot for hosted UI, independent of mail/alert state."""
import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stockwatch.calendar import latest_session, previous_session
from stockwatch.portfolio import positions
from stockwatch.providers.openbb_provider import OpenBBProvider
from stockwatch.providers.snapshot import BAR_COLUMNS, QUOTE_NUMBERS, load_snapshot
from stockwatch.storage import ROOT, atomic_write, load_config, load_transactions

logger = logging.getLogger(__name__)


def save_snapshot(path, provider, quotes, session, mode, *, now=None):
    """Reuse provider history cache, preserving the old file on total fetch failure."""
    if not any(q.price is not None and not q.error and q.session == session for q in quotes.values()):
        logger.warning('Market snapshot not updated: no valid quotes; previous file preserved')
        return False
    now = now or datetime.now(timezone.utc)
    cutoff = previous_session(session) if mode == 'INTRADAY' else session
    data = dict(version=1, mode=mode, session=session.isoformat(), history_session=cutoff.isoformat(),
                generated_at=now.isoformat(), symbols={})
    for symbol, quote in sorted(quotes.items()):
        q = {name: getattr(quote, name) for name in QUOTE_NUMBERS}
        # Never persist arbitrary provider errors or other user/account fields.
        valid = quote.price is not None and quote.session == session and not quote.error
        if not valid:
            q = {name: None for name in QUOTE_NUMBERS}
        q.update(session=session.isoformat(), source=quote.source if quote.source in ('OpenBB / yfinance', 'Yahoo / yfinance') else 'OpenBB / yfinance',
                 error=None if valid else 'Data unavailable',
                 fetched_at=quote.fetched_at.isoformat() if quote.fetched_at else None,
                 price_at=quote.price_at.isoformat() if quote.price_at else None)
        bars = []
        try:
            frame = provider.get_history_range(symbol, session - timedelta(days=380), cutoff).copy()
            for field in BAR_COLUMNS:
                if field not in frame:
                    frame[field] = None
            frame['date'] = frame['date'].map(str)
            bars = json.loads(frame[BAR_COLUMNS].to_json(orient='values'))
        except Exception as exc:
            logger.warning('%s: snapshot history unavailable (%s)', symbol, type(exc).__name__)
        data['symbols'][symbol] = {'quote': q, 'bars': bars}
    content = json.dumps(data, allow_nan=False, separators=(',', ':')) + '\n'
    # Validate before replacing, including dates and finite prices.
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / 'market.json'
        candidate.write_text(content, encoding='utf-8')
        load_snapshot(candidate)
    atomic_write(path, content)
    logger.info('Market snapshot saved: session=%s mode=%s symbols=%s', session, mode, len(quotes))
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description='Refresh hosted market snapshot only; no Gmail, trades or alerts changed')
    parser.add_argument('--config', type=Path, default=ROOT / 'config.yaml')
    parser.add_argument('--transactions', type=Path, default=ROOT / 'data/transactions.csv')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/market_snapshot.json')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    config = load_config(args.config)
    transactions = load_transactions(args.transactions)
    day = latest_session()
    symbols = sorted(set(positions(transactions, day)) | set(positions(transactions, previous_session(day))) |
                     set(config['watchlist']) | {config['portfolio'].get('benchmark', 'SPYM')})
    provider = OpenBBProvider()
    quotes = {symbol: provider.get_quote(symbol, day) for symbol in symbols}
    return 0 if save_snapshot(args.output, provider, quotes, day, 'CLOSE') else 1


if __name__ == '__main__':
    raise SystemExit(main())
