"""Causal daily technical indicators. No UI, network, or file access.

Windows count observations, not calendar days. ``analyze`` removes NYSE holidays
and weekends, inserts missing sessions as NaN, and never fills prices or volume.
Each smoother restarts its warm-up after a missing input. EMA is SMA-seeded;
RSI and ATR use Wilder smoothing (alpha=1/n). Bollinger uses population stddev.
"""
from numbers import Integral

import pandas as pd

from stockwatch.history import sessions
from stockwatch.providers.bars import validate_bars


def _period(period):
    if isinstance(period, bool) or not isinstance(period, Integral) or period < 1:
        raise ValueError('Period must be a positive integer')
    return int(period)


def _values(values):
    series = pd.Series(values, copy=True, dtype=float)
    # Public series helpers also treat infinities as missing, never propagate them.
    return series.where(series.abs() < float('inf'))


def sma(values, period=20):
    return _values(values).rolling(_period(period), min_periods=period).mean()


def _smooth(values, period, alpha):
    values = _values(values)
    result = pd.Series(float('nan'), index=values.index)
    seed = []
    previous = None
    for i, value in enumerate(values):
        if pd.isna(value):
            seed, previous = [], None
            continue
        if previous is None:
            seed.append(value)
            if len(seed) < period:
                continue
            previous = sum(seed) / period
        else:
            previous += alpha * (value - previous)
        result.iloc[i] = previous
    return result


def ema(values, period=20):
    period = _period(period)
    return _smooth(values, period, 2 / (period + 1))


def rsi(values, period=14):
    period = _period(period)
    change = _values(values).diff()
    gains = _smooth(change.clip(lower=0), period, 1 / period)
    losses = _smooth((-change).clip(lower=0), period, 1 / period)
    result = 100 - 100 / (1 + gains / losses)
    result = result.mask((losses == 0) & (gains > 0), 100)
    result = result.mask((losses == 0) & (gains == 0), 50)
    return result


def macd(values, fast=12, slow=26, signal=9):
    fast, slow, signal = _period(fast), _period(slow), _period(signal)
    if fast >= slow:
        raise ValueError('MACD fast period must be shorter than slow period')
    line = ema(values, fast) - ema(values, slow)
    average = ema(line, signal)
    return pd.DataFrame({'macd': line, 'macd_signal': average, 'macd_histogram': line - average})


def bollinger_bands(values, period=20, deviations=2):
    period = _period(period)
    if isinstance(deviations, bool) or not isinstance(deviations, (int, float)) or not 0 < deviations < float('inf'):
        raise ValueError('Band width must be positive and finite')
    values = _values(values)
    middle = sma(values, period)
    spread = values.rolling(period, min_periods=period).std(ddof=0) * deviations
    return pd.DataFrame({'bb_middle': middle, 'bb_upper': middle + spread, 'bb_lower': middle - spread})


def atr(bars, period=14):
    """Wilder ATR: first TR of each contiguous block is high-low, no prior close.

    A missing H/L/C resets the block. N valid true ranges seed the first ATR.
    """
    period = _period(period)
    frame = pd.DataFrame(bars)
    high, low, close = (_values(frame[name]) for name in ('high', 'low', 'close'))
    valid = high.notna() & low.notna() & close.notna()
    prior = close.where(valid).shift()
    ranges = pd.concat([high - low, (high - prior).abs(), (low - prior).abs()], axis=1)
    return _smooth(ranges.max(axis=1).where(valid), period, 1 / period)


def prepare_bars(bars, as_of=None):
    metadata = dict(getattr(bars, 'attrs', {}))
    frame = validate_bars(bars)
    if as_of is not None:
        frame = frame[frame.date <= as_of]
    if frame.empty:
        frame.attrs.update(metadata)
        return frame
    expected = sessions(frame.date.min(), frame.date.max())
    frame = frame.set_index('date').reindex(expected).rename_axis('date').reset_index()
    frame.attrs.update(metadata)
    return frame


def analyze(bars, as_of=None):
    """Compute on the complete available prefix, then let the UI trim its viewport.

    Do not trim to the visible range before computing: that changes EMA seeds.
    The optional as_of excludes future bars before any calculation.
    """
    frame = prepare_bars(bars, as_of)
    if frame.empty:
        return frame
    for period in (20, 50, 200):
        frame[f'sma_{period}'] = sma(frame.close, period)
    for period in (20, 50):
        frame[f'ema_{period}'] = ema(frame.close, period)
    frame['rsi_14'] = rsi(frame.close)
    for name, values in macd(frame.close).items():
        frame[name] = values
    for name, values in bollinger_bands(frame.close).items():
        frame[name] = values
    frame['atr_14'] = atr(frame)
    return frame
