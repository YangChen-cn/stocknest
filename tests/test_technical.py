from datetime import date
import math

import pandas as pd
from pandas.testing import assert_frame_equal
import pytest

from stockwatch.history import sessions
from stockwatch.technical import analyze, atr, bollinger_bands, ema, macd, prepare_bars, rsi, sma


def candles(count=90):
    days = sessions(date(2026, 1, 2), date(2026, 10, 6))[:count]
    return pd.DataFrame([dict(date=d, open=10 + i, high=12 + i, low=9 + i,
                              close=11 + i, volume=100 + i, split_ratio=0) for i, d in enumerate(days)])


def test_sma_and_sma_seeded_ema_exact_values_and_warmup():
    prices = [1, 2, 3, 4, 8]
    assert sma(prices, 3).iloc[:2].isna().all()
    assert sma(prices, 3).iloc[2:].tolist() == pytest.approx([2, 3, 5])
    assert ema(prices, 3).iloc[2:].tolist() == pytest.approx([2, 3, 5.5])
    assert ema([10], 3).isna().all()


def test_wilder_rsi_seed_and_recursion_and_flat_prices():
    values = rsi([10, 11, 10, 12, 11], 3)
    assert values.iloc[:3].isna().all()
    assert values.iloc[3:].tolist() == pytest.approx([75, 100 * 6 / 11])
    assert rsi(range(20)).iloc[-1] == 100
    assert rsi(range(20, 0, -1)).iloc[-1] == 0
    assert rsi([10] * 20).iloc[-1] == 50
    # Wilder's standard 14-change worked example.
    prices = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
              45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    assert rsi(prices).iloc[-1] == pytest.approx(70.464135, abs=1e-6)


def test_macd_seed_signal_and_histogram():
    result = macd(range(1, 61))
    assert result.macd.first_valid_index() == 25
    assert result.macd_signal.first_valid_index() == 33
    assert result.iloc[-1].tolist() == pytest.approx([7, 7, 0])


def test_bollinger_population_standard_deviation():
    result = bollinger_bands([1, 2, 3], 3)
    assert result.iloc[:2].isna().all().all()
    assert result.iloc[-1].tolist() == pytest.approx([2, 2 + 2 * math.sqrt(2 / 3), 2 - 2 * math.sqrt(2 / 3)])


def test_wilder_atr_true_range_includes_overnight_gap():
    frame = pd.DataFrame({'high': [11, 12, 13, 16], 'low': [9, 10, 11, 14], 'close': [10, 11, 12, 15]})
    result = atr(frame, 3)
    assert result.iloc[:2].isna().all()
    assert result.iloc[2:].tolist() == pytest.approx([2, 8 / 3])


def test_missing_session_resets_indicators_but_weekends_and_holidays_do_not():
    frame = candles(75)
    missing_day = frame.iloc[30].date
    frame = frame.drop(index=30)
    result = analyze(frame)
    gap = result.index[result.date == missing_day][0]
    assert result.loc[gap, ['close', 'volume', 'rsi_14', 'atr_14']].isna().all()
    assert result.loc[gap:gap + 19, 'sma_20'].isna().all()
    assert pd.notna(result.loc[gap + 20, 'sma_20'])
    assert result.loc[gap:gap + 14, 'rsi_14'].isna().all()
    assert pd.notna(result.loc[gap + 15, 'rsi_14'])
    assert len(result) == 75 and date(2026, 1, 19) not in set(result.date)
    # An extra weekend bar is discarded, never counted in a rolling window.
    weekend = frame.iloc[[0]].assign(date=date(2026, 1, 3))
    with_weekend = pd.concat([frame, weekend]).sort_values('date')
    assert_frame_equal(result, analyze(with_weekend))


def test_every_indicator_is_prefix_invariant_and_as_of_ignores_future_values():
    frame = candles()
    original = frame.copy(deep=True)
    all_data = analyze(frame)
    for length in (1, 14, 20, 34, 55):
        prefix = analyze(frame.iloc[:length])
        assert_frame_equal(prefix, all_data.iloc[:length].reset_index(drop=True))
        assert_frame_equal(prefix, analyze(frame, frame.iloc[length - 1].date))
    assert_frame_equal(frame, original)


def test_legacy_null_ohlcv_and_insufficient_history_are_explicit_missing():
    frame = candles(10)[['date', 'close', 'split_ratio']]
    result = analyze(frame)
    assert result[['open', 'volume', 'sma_20', 'ema_20', 'rsi_14', 'atr_14', 'macd']].isna().all().all()
    assert result.close.notna().all()
    assert analyze(pd.DataFrame()).empty
    assert prepare_bars(frame, date(2025, 1, 1)).empty


@pytest.mark.parametrize('period', [0, -1, True, 1.5])
def test_bad_periods_rejected(period):
    for function in (sma, ema, rsi):
        with pytest.raises(ValueError):
            function([1, 2, 3], period)
