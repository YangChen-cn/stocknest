# Quant Lab: daily bars and technical indicators

A separate read-only workspace for exploring completed US equity / ETF daily bars. It uses the existing warm-paper UI, Plotly theme, market cache and English / 简体中文 catalog. No new dependencies or market providers are introduced.

## Load and explore

1. Open **Quant Lab / 量化实验室**. Select a configured ticker or enter another US ticker directly. This does not add it to the watchlist or create a transaction.
2. Choose **Saved snapshot (offline)** to read `data/market_snapshot.json` without a network request. Choose **Online / local market cache** to use the existing provider and cache, with a dated snapshot fallback. Click **Load daily bars**; the explicit single-ticker request allows up to 60 seconds, with one attempt.
3. Choose 5D / 1M / 3M / 6M / 1Y, moving averages, volume, Bollinger Bands and RSI / MACD / ATR panels. These switches only calculate and redraw the loaded data; they never fetch prices.
4. Inspect **Daily bars and indicator values** for actual inputs, gaps and split ratios. The chart identifies its ticker, source, acquisition time, snapshot save time and available dates. Stale or missing data is labeled. A failed reload keeps the previous chart and its original metadata.

Demo candles and volumes are deterministic, fictional and offline. Neither mode changes trades, notes, performance, alerts, report deduplication or scheduling. Online loading can update the existing local market cache. Quant Lab itself does not change report data. Daily reports can independently include optional [Quantitative Daily Insights](quantitative-insights.md).

## Snapshot compatibility

- **Version 2 writes** use seven values per daily bar, in this order: `date, open, high, low, close, volume, split_ratio`. All numeric fields may be null to express missing data. Prices must be positive and finite when present; volume must be a finite nonnegative integer (zero is valid). A split ratio is finite and nonnegative; zero means no recorded event. Dates must be valid ISO calendar dates, strictly increasing, unique and no later than the completed history cutoff. Available OHLC fields must satisfy `low <= open/close <= high` and `low <= high`.
- Each symbol has independent history metadata: `source`, `fetched_at`, `adjustment` and `status` (`complete`, `partial`, `unavailable`). The snapshot retains its existing `session`, `history_session`, `mode`, `generated_at` and quote metadata. History cutoff is the preceding completed session for intraday snapshots. Invalid history for one ticker is omitted and marked unavailable while valid quotes / other ticker histories remain usable. A total quote failure preserves the previous file.
- **Version 1 reads** continue to accept `date, close, high, low, split_ratio`. The decoder adds null open / volume fields in memory and marks history as legacy; it does not rewrite the file. Existing portfolio quote views remain usable. Quant Lab shows a close-only line when full candles are unavailable, leaves volume blank and explains the limitation. A subsequent normal snapshot write produces v2.
- Unknown versions, corrupt JSON and invalid fields produce a bounded unavailable-data result, not an uncaught Dashboard error. Snapshot validation precedes atomic replacement. The local closed-session cache similarly reads its old v1 format and writes v2 metadata when refreshed. Refresh discards only market caches, not the ledger or performance history.
- Online provider prices keep the existing **split-adjusted, non-dividend-adjusted** convention. Split events remain separate fields. Quant Lab does not guess adjustments for unknown / legacy data. Existing `performance.py` rules and its treatment of corporate actions are unchanged.

## Exact calculation conventions

Windows count completed **NYSE sessions**, not elapsed calendar days. Weekends and exchange holidays are excluded. An absent trading session is inserted as null; there is no forward fill, zero fill, interpolation or cross-gap smoothing. Missing inputs restart the relevant indicator's warm-up. Missing volume alone does not suppress a valid close-based indicator. Only supplied historical bars on or before the analysis cutoff enter calculation.

| Indicator | Convention and first usable value |
| --- | --- |
| SMA(N) | Equal-weight mean; N consecutive closes. UI: 20 / 50 / 200. |
| EMA(N) | First value = SMA of N consecutive closes; then `EMA += 2/(N+1) * (close - EMA)`. UI: 20 / 50. |
| RSI(14) | Wilder mean gains/losses; seed from 14 changes (15 closes), then alpha = 1/14. `100 - 100/(1 + avg_gain/avg_loss)`. No losses = 100, no gains = 0, both zero = 50. |
| MACD(12,26,9) | EMA12 − EMA26; signal = SMA-seeded EMA9 of that line; histogram = line − signal. Line starts at close 26; signal at close 34. |
| Bollinger(20,2) | SMA20 ± 2 population standard deviations (`ddof=0`), 20 consecutive closes. |
| ATR(14) | TR = max(H−L, abs(H−previous C), abs(L−previous C)); first bar of each contiguous valid H/L/C block uses H−L. Seed = mean of 14 TRs, then Wilder alpha = 1/14. Units: price (USD). |

Functions live in `stockwatch/technical.py`; they have no Streamlit, file or network access. Series helpers operate on observations; `prepare_bars` / `analyze` provide the session-aware path. The UI calculates all loaded history **before** trimming the display range, so changing the visible range does not reseed EMA. Snapshot and local-cache history remain limited to about 380 calendar days: longer indicators can have blank initial values, and snapshots do not promise pre-listing or multi-year coverage. Different EMA seeds and data start dates can differ from another charting platform.

Formula references: [Fidelity RSI](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/RSI), [Fidelity Bollinger Bands](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/bollinger-bands), [Fidelity indicator reference](https://www.fidelity.com/products/atp/content/wsFuncRef_US.pdf). The exact choices above, including seeds, gap resets and flat RSI, are this project's explicit reproducibility contract.

## Learning path after this foundation

The [MIT 18.642 Fall 2024 calendar](https://ocw.mit.edu/courses/18-642-topics-in-mathematics-with-applications-in-finance-fall-2024/pages/calendar/) includes regression, PCA, time series, portfolio management, volatility modeling and stochastic processes. A practical continuation is:

1. Study log returns, rolling volatility and autocorrelation using the same validated bars; compare distributions across windows and make assumptions visible.
2. Add benchmark regression / beta and rolling correlation, then covariance / PCA experiments on strictly aligned common sessions. Do not silently fill gaps or interpret a historical fit as a forecast.
3. Build a separate educational backtest only after time-ordered train / validation / test splits, next-session execution, fees / slippage and baseline comparisons are defined. Keep it separate from the actual ledger and daily reports.
4. Add simulated stochastic paths / Monte Carlo and compare realized volatility with model assumptions before exploring derivative pricing.

This is a proposed learning sequence, not a claim that these technical indicators predict returns. No strategy engine or backtest is part of this change.
