# Quantitative explanations and inline email labels

The deterministic `stockwatch/interpretation.py` reads the existing quantitative facts without fetching data or recalculating technical indicators. Daily JSON keeps its version and original fields; `quantitative_analysis.interpretation` is optional and carries its own version, language, daily cutoff, per-instrument stages and portfolio exposure.

Every stage contains a stable ID, localized label, original values, reason, caveat, next observation, threshold definitions/nature, source, actual/expected date and quality. Unknown, stale, adjustment-unknown, insufficient-sample and incomparable inputs are not neutral or low risk. A missing comparison permits a confirmed current value/sign, but no strengthening/weakening claim or arrow. Comparisons require the adjacent NYSE session; gaps are never skipped.

| Stage | Exact convention |
| --- | --- |
| Trend | strong: close > SMA20 > SMA50; weak: close < SMA20 < SMA50; otherwise mixed, including equality. |
| RSI | [0,30) oversold reference; [30,45) weak; [45,55] neutral; (55,70] strong; (70,100] overbought reference. Thus exactly 30/70 are reference boundaries, not inside the extreme zones. 30/70 are traditional references; 45/55 are product partitions. |
| MACD difference | Positive + positive delta: positive strengthening; positive + negative delta: positive weakening; negative + negative delta: negative strengthening; negative + positive delta: negative weakening. Zero and unchanged values are explicit. Sign-cross metadata does not imply a reliable reversal. |
| ATR/price | A fraction, e.g. 0.04 = 4%. Display adjacent-session direction when comparable; otherwise show the current scale only. No universal high/middle/low thresholds or invented percentiles. |
| Historical volatility | Past 20 log returns, sample standard deviation annualized sqrt(252), not a forecast loss. Display adjacent-session change; no percentile without a distribution. |
| Exposure | Market-value largest/top-three weights and HHI; 1/HHI is equivalent equal-weight count, not actual count or a correlation/sector diversification guarantee. |

Quant Lab defaults to **Beginner / 简明**: current holdings first, dated/source/quality metadata, portfolio concentration, per-holding trend, momentum, volatility and exposure, plus the user's existing thesis. The first view loads only saved data (or offline Demo); online refresh requires an explicit checkbox and refresh click. At most three alphabetically selected unheld observers are loaded; only those with rule-qualified changes are highlighted. This bounded preview does not claim to rank the entire watchlist. **Advanced / 进阶** retains the original individual-symbol chart, ranges, indicators, raw data and formulas. Changing views or opening explanations does not fetch online data or write user records.

Daily emails show compact Trend, RSI, ATR and MACD badges beside holdings and across a full-width row below each watchlist quote. The watchlist prints its indicator close date once below the table. Badges wrap on narrow screens and include context in their titles. Plain text keeps dated labels on each ticker line. Unavailable stages leave the price row intact. The JSON includes the original indicators, deterministic events and concentration facts, including the legacy `email_observations` field.

These are dated facts and technical observations to support personal review with weights, recorded thesis, portfolio performance and independently verified news. No price target, profit forecast, combined buy/sell score, order, rebalancing, reminder, new schedule or GPT call is introduced. Cost basis is not treated as fair value. No real email is sent for validation.


## Plain language without extra paragraphs

Beginner definitions now explain averages, RSI pressure versus valuation, MACD direction, daily swing size and HHI in both languages. Default cards stay compact: state labels, key numbers and weight only. Definitions, detailed reasons, caveats and next observations live in the default-collapsed **Explanation and basis** panel. Emails use plain short labels such as Above averages, Gains forceful and Daily swing 3.2%; no explanation paragraphs or duplicate stock list are added. Text MIME keeps the same concise meaning. Technical formulas, stage IDs, boundaries and quality gates are unchanged.
