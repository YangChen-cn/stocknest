# StockNest

**StockNest** is the public edition of StockWatch, a small personal US stock/ETF tracker: OpenBB + free yfinance data, a local Streamlit dashboard, and optional Gmail reports through GitHub Actions. No brokerage connection, automatic trading, database server or paid provider.

![Synthetic demo dashboard](docs/images/dashboard-demo.png)

The dashboard is branded StockNest. Internal Python commands, report subjects and workflow names retain `stockwatch` / StockWatch for compatibility with private copies; the core implementation is shared.

## Start locally

Python 3.11 is the tested baseline.

```sh
git clone https://github.com/YangChen-cn/stocknest.git
cd stocknest
```

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
streamlit run app.py --server.address 127.0.0.1
```

Open http://127.0.0.1:8501 and enable **Demo mode** to explore without network requests or email. CORE, TECH, VALUE and CAND are fictional symbols; all demo trades/prices are synthetic. Demo files are read-only.

The app creates an empty `config.yaml` from `config.example.yaml` and an empty `data/transactions.csv` on first use. Both are ignored by Git. To prepare them manually:

```sh
cp config.example.yaml config.yaml
mkdir -p data
cp examples/transactions.example.csv data/transactions.csv
```

Use Transactions to search by company name/ticker and enter **your own** trade date, shares, execution price and optional fee. Dates use NYSE trading sessions. Do not copy synthetic demo symbols into your real watchlist. Settings supports English/Chinese and a configurable benchmark, default SPY.

## What it does

- Dashboard/Holdings: costs, price, daily movement, unrealized P/L, weights and price charts.
- Watchlist: pure observation needs no price target or alert. Targets/alerts are optional; all watched stocks appear in a compact closing summary. Intraday emails include only notable moves, near targets and triggered alerts.
- Performance: historical market value, cash-flow-adjusted index, benchmark, average-cost realized P/L and fees.
- Settings/Control Center: local email switch, existing `gh` login for workflow status/manual dispatch/enable/disable, optional macOS login startup.

![Synthetic performance comparison](docs/images/performance-demo.png)

Buys count as contributions; net sales count as withdrawals. No idle cash account is tracked. Daily return is `(end value + net sales) / (previous value + buy cost) − 1`, compounded into an index starting at 100. Buy costs include fees; sale proceeds exclude fees. Buys are assumed at day start and sales at day end, so trade-day returns are daily approximations. Realized P/L uses average cost, not tax lots.

5D spans five NYSE sessions, using six closing prices. Month/year windows roll back calendar months and choose the trading session on or before the boundary. Histories use completed closes and exact anchors. Missing data leaves gaps; relevant splits block historical calculation rather than inventing share adjustments. Benchmark uses **price** return. Dividends, taxes, automatic corporate actions and XIRR are excluded.

## Reports and Gmail

```sh
# Completely offline: no email or personal state changes
python -m stockwatch.daily --demo --dry-run
# Live closing preview after configuring your own local files
python -m stockwatch.daily --dry-run
# Inspect/rebuild historical performance without changing tracked history
python -m stockwatch.daily --rebuild-performance --dry-run
```

Normal reports read `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `REPORT_EMAIL` from environment variables. Gmail requires an [App Password](https://support.google.com/accounts/answer/185833); never write it into YAML, CSV or a commit. Missing settings skip sending. Disabling `notifications.email_enabled` still generates reports/history and leaves alerts pending. SMTP failures preserve reports and do not consume notifications.

Only CLOSE writes `data/performance.json`; INTRADAY reads the previous close. Alert state is separate per mode. Repeated successful reports for the same session/mode skip email unless forced. Dry-run/demo never change personal state/history. Outputs, logs and user data are ignored.

## GitHub Actions: use a private personal copy

The **public source repository only runs CI**. `daily.yml` explicitly skips public repositories because logs/artifacts could reveal personal portfolio information. Do not enable portfolio automation in a public fork or commit real data there.

For personal automation, create a **private** repository from this clean source (GitHub template if available, or import/push the source to a new private repository). Change `origin` to that private repository. Confirm its visibility in GitHub Settings. Once verified private, intentionally track your personal inputs in that private copy:

```sh
# Only in your verified PRIVATE repository
# gh auth login uses existing GitHub CLI credentials; the app stores no token.
gh auth login
gh api repos/YOUR_ACCOUNT/YOUR_PRIVATE_REPO --jq '.private'
# Continue only when the result is true.
git add -f config.yaml data/transactions.csv
git commit -m "chore: configure personal portfolio"
git push origin main
```

The UI sync button also checks GitHub repository privacy before committing. It commits only config/transactions, pulls remote history/state, and preserves conflicts. A source checkout without a GitHub origin shows unavailable cloud controls and remains usable locally.

Add three repository Secrets: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `REPORT_EMAIL`. Actions → StockWatch Daily → Run workflow supports CLOSE/INTRADAY, dry-run, force resend and performance rebuild. Scheduled runs use America/New_York at 10:30 and 18:30 weekdays; NYSE calendar gates holidays, weekends, early closes and incomplete sessions. GitHub scheduling can be delayed.

CI runs pytest and pip checks separately. Daily jobs use `requirements-runtime.lock` without Streamlit, Plotly or pytest. In a private copy only, the bot commits exactly state/performance with `[skip ci]`, using `contents: write`, concurrency and no force push. Three-day private artifacts contain logs and recovery files. If sending succeeds but pushing fails, restore the artifact state/history before rerunning to avoid duplicate mail. This lightweight SMTP/Git setup cannot guarantee exactly-once delivery after a crash.

## macOS startup

No service is enabled automatically. Stop a manually running Dashboard before enabling login startup (port 8501 must be free):

```sh
python -m stockwatch.control install
python -m stockwatch.control status
python -m stockwatch.control start
python -m stockwatch.control stop
python -m stockwatch.control uninstall
```

Settings offers the same controls. This per-user launchd service starts the local-only Dashboard **after login**, not email jobs. Stopping it disconnects the page; use the terminal to start again. The generated plist is in `~/Library/LaunchAgents/`; logs stay in ignored `logs/`. Reinstall after moving the checkout/Python environment. Other operating systems retain cloud controls without launchd.

## Privacy and development

`.gitignore` excludes local config, all `data/`, Secrets, environments, reports, logs and caches. Do not use `git add -f` for personal files in a public repository. Never publish private screenshots, workflow artifacts or an existing private repository's history. This release starts from a new Git root commit, not a clone of personal history. See [public-release checks](PUBLIC_RELEASE_CHECK.md).

```sh
python -m pytest
python -m pip check
```

Tests use synthetic data, fake providers/SMTP and temporary Git repositories; no live Gmail/Yahoo is required. Core code lives in `stockwatch/` and is identical in public/private editions. Keep this source as the upstream; private copies retain their own tracked data. Merge future source changes into the private copy rather than maintaining a second implementation.

[中文使用说明](使用说明.md)

## License

[GNU AGPL-3.0-only](LICENSE). Dependencies retain their own licenses; consult the pinned package metadata.
