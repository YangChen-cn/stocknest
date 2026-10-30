# Quantitative Daily Insights

CLOSE and INTRADAY daily reports now carry an optional `quantitative_analysis` object inside the existing `stockwatch.report` schema version 1. Existing fields, numeric encoding, MIME JSON attachment and text markers are unchanged. Older render/export callers may omit it; periodic and failure reports keep their existing payloads.

## Daily facts

The object has its own `schema_version: 1`, `expected_daily_session`, `report_mode`, conventions, instruments, portfolio concentration, all deterministic events and up to three email observations. Instruments cover current holdings, configured watchlist entries and benchmark, with roles deduplicated per symbol.

Each instrument records its actual latest valid completed daily session, expected cutoff, previous session, source, acquisition time, adjustment convention, quality (`complete`, `partial`, `stale`, `unavailable`), reasons, total valid closes, consecutive valid closes and missing-session count. Each metric records a finite decimal-string `value` or null, `unit`, consecutive `valid_samples`, `required_samples`, and an explicit unavailable reason. Metric dates and provenance are inherited from the instrument. Changes contain the previous value and delta in the same unit, only for the adjacent NYSE session; unavailable comparisons remain null.

- SMA20/SMA50 and percent deviations `(close / SMA - 1) * 100`.
- Wilder RSI14 (15 consecutive closes to initialize).
- MACD minus signal (12/26/9, 34 closes to initialize).
- Wilder ATR14 in USD and ATR / close as a fraction (0.05 means 5%).
- 20-day historical volatility: **sample standard deviation (ddof=1) of 20 log returns**, annualized by sqrt(252), expressed in percentage points. Requires 21 consecutive closes. This is historical price volatility, not a return forecast.

Indicators reuse `stockwatch/technical.py`. Daily history requests use the same 380-calendar-day range and provider cache as quotes. Each symbol is requested once for the quantitative collection, with per-symbol failure isolation and the existing bounded provider requests. An intraday benchmark may require a daily-history request if not already cached. No new provider, dependency, file, news API or scheduler is added. Calculations have no network or file side effects.

Missing sessions restart warm-up; no interpolation or zero fill. Null H/L prevents ATR while valid closes can still support close-based indicators. A stale latest valid close retains its actual date and cannot generate daily-change events. Unknown price adjustment is flagged, and suppresses technical events. INTRADAY always uses daily bars through the previous completed session; its current portfolio weights are labeled separately as intraday valuation. It never treats the forming day's price as a daily close.

## Deterministic observations

Events are descriptive facts with dates, values and thresholds, not recommendations:

1. Absolute daily price change >= max(5 percentage points, 2 times the **previous** session's 20-return daily sample volatility). No volatility baseline means no such event.
2. Close/SMA50 deviation or MACD minus signal crosses zero between adjacent sessions.
3. RSI crosses upward through 70 or downward through 30 (reference levels only).
4. Largest holding weight >= 40%, a concentration observation rather than a new price-change event.

Sort by the above priority, then prefer held instruments, then ticker and event kind. At most three appear in the email; all events and all instruments remain in JSON. No events means no qualifying changes **in available data**, not an assertion of low risk. These observations never evaluate or consume price-alert state.

Concentration includes per-holding market-value weights, largest/top-three weights and HHI (sum of squared fractional weights). It is unavailable if valuation is incomplete or empty. Daily ATR coverage and the weight of covered holdings with ATR/close >= 5% are reported with the coverage boundary; uncovered holdings are never treated as low risk. This is not a sector, correlation, portfolio volatility or VaR estimate.

## Reading with ChatGPT

Prefer the JSON attachment. Check actual session, expected session, quality, metric reasons and coverage before using a number. Keep technical observations distinct from investment recommendations. External company news should be independently retrieved and linked, with publication/event dates, and joined by ticker; this application does not fetch or verify news. In intraday reports do not describe yesterday's RSI/MACD as a live intraday measurement. Missing data must remain unknown.

Financial returns, the transaction ledger, performance history, report schedules, cloud editing permissions, email deduplication and delivery state are unchanged. Normal scheduled reports include the enrichment; validation does not send extra real email.
