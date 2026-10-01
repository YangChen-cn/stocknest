<div align="center">

# StockNest

<p align="center">
  <b>A lightweight, privacy-first, self-hosted US stock & ETF portfolio tracker and automated email reporter.</b>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11"></a>
  <a href="https://streamlit.io/"><img src="https://img.shields.io/badge/Streamlit-1.38+-FF4B4B?style=flat-square&logo=streamlit&logoColor=white" alt="Streamlit"></a>
  <a href="https://openbb.co/"><img src="https://img.shields.io/badge/Data-OpenBB%20%7C%20yfinance-00E5A3?style=flat-square" alt="OpenBB / yfinance"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0-blue.svg?style=flat-square" alt="License: AGPL-3.0"></a>
  <a href="https://github.com/YangChen-cn/stocknest/actions"><img src="https://img.shields.io/badge/CI-Passing-brightgreen?style=flat-square&logo=githubactions&logoColor=white" alt="CI"></a>
  <img src="https://img.shields.io/badge/Database-Zero%20(Plain%20Files)-orange?style=flat-square" alt="No DB">
</p>

<p align="center">
  <a href="README.md"><b>English</b></a> &nbsp;•&nbsp; <a href="使用说明.md"><b>简体中文说明</b></a>
</p>

---

</div>

**StockNest** is the public open-source edition of StockWatch, a clean personal US stock & ETF tracking workstation: free market data via OpenBB Platform & yfinance, a responsive local Streamlit dashboard, and automated email briefings powered by GitHub Actions.

> [!NOTE]
> **No Brokerage Connection • No Database Server • No Paid Provider • No Auto-Trading**  
> All portfolio records, settings, and performance histories are stored strictly in local CSV, JSON, and YAML files.

<div align="center">
  <img src="docs/images/dashboard-demo.png" alt="StockNest Dashboard Demo" width="100%" />
</div>

*The dashboard is branded StockNest. Internal Python commands, report subjects and workflow names retain `stockwatch` / StockWatch for compatibility with private copies; the core implementation is shared.*

---

## 🚀 Quick Start

### 1. Prerequisites
- **Python 3.11** (tested baseline).

### 2. Installation

```sh
# 1. Clone repository
git clone https://github.com/YangChen-cn/stocknest.git
cd stocknest

# 2. Setup virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# 3. Install locked dependencies
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .

# 4. Start local dashboard
streamlit run app.py --server.address 127.0.0.1
```

Open `http://127.0.0.1:8501` in your browser. Toggle **Demo mode** in the sidebar to explore offline with synthetic tickers (`CORE`, `TECH`, `VALUE`, `CAND`) without network requests or private data.

---

## 📝 Portfolio Configuration

The app automatically initializes an empty `config.yaml` from `config.example.yaml` and an empty `data/transactions.csv` on first use (both are excluded by Git). To initialize manually:

```sh
cp config.example.yaml config.yaml
mkdir -p data
cp examples/transactions.example.csv data/transactions.csv
```

### Transactions CSV (`data/transactions.csv`)

Record trades directly via the **Transactions** tab in the UI or by editing the CSV file:

| date | symbol | type | shares | price | fee | notes |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `2026-03-10` | `NVDA` | `BUY` | `10` | `120.50` | `1.00` | Initial purchase |
| `2026-03-15` | `AAPL` | `BUY` | `20` | `185.00` | `0.00` | Core position |
| `2026-03-20` | `NVDA` | `SELL` | `5` | `135.00` | `1.00` | Partial profit take |

*Dates use NYSE trading sessions. Legacy 6-column CSV files remain fully supported (`fee` defaults to 0).*

### Configuration (`config.yaml`)

```yaml
benchmark: SPY
timezone: America/New_York
language: en  # 'en' or 'zh'

watchlist:
  - symbol: MSFT
    target_price: 450.0
    notes: Long-term cloud growth
  - symbol: QQQ

notifications:
  email_enabled: true
  price_alert_threshold: 0.03
```

---

## 📊 Performance & Methodology

- **Dashboard & Holdings**: Real-time valuation, cost basis, daily movements, unrealized P/L, portfolio allocation weights, interactive Plotly charts, and editable per-holding notes/theses.
- **Watchlist & Alerts**: Pure observation requires no price target or alert. Targets and alerts are optional; all watched stocks appear in a compact closing summary. Intraday emails highlight notable moves (≥5%), near targets, and triggered alerts.
- **Historical Performance**: Historical market value, cash-flow-adjusted index, benchmark comparison (default `SPY`), average-cost realized P/L, and fee accounting.

<div align="center">
  <img src="docs/images/performance-demo.png" alt="StockNest Performance Analysis" width="100%" />
</div>

### Return & Accounting Principles

- **Cash Flow Accounting**: Buys count as capital contributions; net sales count as withdrawals. No idle cash account is tracked.
- **Daily Return Formula**:
  $$\text{Daily Return} = \frac{\text{End Market Value} + \text{Net Sales}}{\text{Previous Market Value} + \text{Buy Cost}} - 1$$
  Compounded daily into a money-weighted return index starting at $100.0$.
- **Approximations**: Buys are assumed at day start and sales at day end (daily approximation). Buy costs include fees; sale proceeds exclude fees.
- **Realized P/L**: Calculated using the **Average Cost** method across multiple buys/sells. Fees are deducted exactly once.
- **Data Integrity & Windows**:
  - **5D** spans five NYSE sessions, using six closing prices.
  - **1M / 1Y** windows roll back calendar months and choose the trading session on or before the boundary.
  - Histories use completed closes and exact anchors. Missing data leaves gaps; relevant splits block historical calculation rather than inventing share adjustments.
  - Benchmark uses **price** return. Dividends, taxes, automatic corporate actions, and XIRR are excluded.

---

## 🎯 Watchlist & UI Showcase

<div align="center">

| Desktop Watchlist & Alerts | Mobile Responsive View |
| :---: | :---: |
| <img src="docs/images/watchlist-demo.png" alt="Desktop Watchlist & Alerts" width="680" /> | <img src="docs/images/performance-mobile-demo.png" alt="Mobile View" width="220" /> |

</div>

---

## 📬 Automated Reports & Gmail

```sh
# Completely offline demo dry-run (no email, no state change)
python -m stockwatch.daily --demo --dry-run

# Live closing preview with your local data (terminal preview, no mail sent)
python -m stockwatch.daily --dry-run

# Rebuild historical performance index in dry-run mode
python -m stockwatch.daily --rebuild-performance --dry-run
```

Normal reports read `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `REPORT_EMAIL` from environment variables. Gmail requires an [App Password](https://support.google.com/accounts/answer/185833); never write it into YAML, CSV or a commit. Missing settings skip sending. Disabling `notifications.email_enabled` still generates reports/history and leaves alerts pending. SMTP failures preserve reports and do not consume notifications.

Only **CLOSE** writes `data/performance.json`; **INTRADAY** reads the previous close. Alert state is separate per mode. Repeated successful reports for the same session/mode skip email unless forced. Dry-run/demo never change personal state/history.

---

## ☁️ GitHub Actions: Private Repository Setup

> [!WARNING]
> **The public source repository does not run portfolio automation.**  
> CI checks shared code in the public repository. `daily.yml` explicitly skips public repositories because logs/artifacts could reveal personal portfolio information. Do not enable portfolio automation in a public fork or commit real data there.

For personal automation, create a **private** repository from this clean source. Change `origin` to that private repository, verify its visibility, and commit your personal files:

```sh
# 1. Check private repository visibility
gh auth login
gh api repos/YOUR_ACCOUNT/YOUR_PRIVATE_REPO --jq '.private'
# Continue only when the result is true.

# 2. Stage and push personal inputs
git add -f config.yaml data/transactions.csv
git commit -m "chore: configure personal portfolio"
git push origin main
```

The UI sync button also checks GitHub repository privacy before committing. It commits only config/transactions/import audit (never local Gmail), pulls remote history/state, and preserves conflicts.

### Workflow Configuration
1. **Repository Secrets**: Add `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, and `REPORT_EMAIL`.
2. **Schedules**: America/New_York at **10:30** (Intraday) and **19:00** (Close) weekdays; NYSE calendar gates holidays, weekends, early closes and incomplete sessions.
3. **State Persistence**: In a private copy only, the bot commits `state.json`, `performance.json`, and (when HSBC imports exist) `transactions.csv` / `hsbc_imports.json` with `[skip ci]`.
4. **Crash Recovery**: 3-day private artifacts contain logs and recovery files. If sending succeeds but pushing fails, restore the affected artifact files before rerunning.

---

## 🔄 Close-Data Retry & HSBC Execution Sync

### Close-Data Retry Engine
CLOSE checks all configured holdings/watchlist quotes for the target NYSE session and a valid previous close before delivery. Missing or stale quotes cause up to **three checks, two minutes apart** (`--close-attempts 1..3`, `--close-retry-seconds 60..180`).

No partial daily report is sent during this wait. Exhaustion sends one error notification per session and exits nonzero; price alerts and the regular report remain pending. A later run can send the recovered report.

### HSBC Trade Execution Sync (Optional)
Enable **HSBC execution sync** in Settings, or configure:

```yaml
imports:
  hsbc:
    enabled: true
    allow_email_date: true
    lookback_days: 3
```

- **Authentication & Parsing**: Uses read-only Gmail IMAP. Accepts official Traditional Chinese **fully executed** USD confirmations with DKIM/DMARC authentication. Partial/cancelled/unknown confirmations are skipped.
- **Trade Date**: Uses email Date converted to America/New_York when absent. Delayed emails can require manual date adjustment; disable `allow_email_date` to skip them instead. Non-session dates are never guessed.
- **Deduplication**: Trade IDs are preserved in CSV notes as `[HSBC:ID]` and in `data/hsbc_imports.json`.

```sh
python -m stockwatch.daily --sync-only --dry-run  # read and preview; no writes/mail
python -m stockwatch.daily --sync-only            # import only; no report/mail
python -m stockwatch.daily --skip-hsbc --dry-run  # skip Gmail access
```

---

## ⚡ UI Performance & Closed-Market Cache

- **Search & Selection**: Stock search runs upon pressing Enter or leaving the input. New results replace the previous selection. Only the selected instrument's latest price, day move, 52-week range and date are fetched.
- **Holding Thesis / Notes**: Editable thesis notes appear in both text and HTML emails; blank notes are omitted and HTML is escaped.
- **Closed-Market Cache**: Outside active NYSE sessions, Dashboard and Watchlist reuse validated daily bars in `.cache/market/`. The cache survives page navigation and restarts, expiring when a newer completed NYSE session exists. Regular-session prices retain a 5-minute memory cache. Click **Refresh market data** to clear the cache.

---

## 🤖 AI / LLM Analysis Integration

Every generated email and dashboard view provides machine-readable structured financial data:

1. **Email Reports**: Formatted with HTML/text plus an attached UTF-8 JSON file (`stockwatch-YYYY-MM-DD-close.json`) and an inline `STOCKWATCH_DATA_V1` payload.
2. **Dashboard Export**: Click **Export current AI data (JSON)** on the Dashboard or Watchlist page to download an instant snapshot.

> **💡 Sample AI Prompt**:  
> *"Read the JSON attachment of my latest StockWatch report (fall back to the STOCKWATCH_DATA_V1 block in the plain-text part if needed). Compare it with the previous report, analyze portfolio performance vs SPY benchmark, explain individual price changes and configured alerts. Distinguish my thesis notes from objective market facts."*

---

## 🍎 macOS Login Background Service (`launchd`)

No service is enabled automatically. Register next-login startup while the current Dashboard continues running:

```sh
python -m stockwatch.control install
python -m stockwatch.control status
python -m stockwatch.control start
python -m stockwatch.control stop
python -m stockwatch.control uninstall
```

This per-user launchd service starts the local-only Dashboard **after login**. The generated plist is in `~/Library/LaunchAgents/`; logs stay in `logs/`.

---

## 🧪 Testing & Privacy Checks

```sh
# Run comprehensive test suite
python -m pytest

# Validate dependencies
python -m pip check

# Public release compliance scan
python tools/check_public_release.py
```

`.gitignore` excludes local config, `data/`, Secrets, environments, reports, logs, and caches. Never use `git add -f` for personal files in a public repository. See [public-release checks](PUBLIC_RELEASE_CHECK.md).

---

## 📄 License

This project is licensed under the **[GNU AGPL-3.0-only](LICENSE)**. Dependencies retain their respective licenses.
