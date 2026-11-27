# Trailhead — Plan & Build Prompt

> A desktop app for tracking an R-multiple stock-trading portfolio, built to grow into a multi-portfolio tracker.
> Status: **phases 1–8 implemented**, plus the Long-term Holdings type, 2026-11-24 (see README.md and DECISIONS.md). The design system is in [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md).
> Source requirements: `UpNote_2026-11-19_16-04-02/Stock Trading Portfolio Tracker.md`. Reference spreadsheet: `Sample Spreadsheet.xlsx`.

---

## 1. Goals

1. Replace the trading spreadsheet with an attractive, fast desktop app.
2. The main view is a **spreadsheet-style positions table** with one row per stock position. A caret expands each row to show its individual trades.
3. Above the table, a **KPI strip** shows portfolio summary and performance indicators.
4. Trades are entered through a **New Trade** form.
5. Prices, dividends, S&P sector and next earnings date are **fetched from the internet** on startup and on demand. Manual entry is the fallback.
6. The app has **built-in strategy awareness:** for every position it shows the current stop and the next target prominently, suggests actions at milestones, and raises alerts.
7. **Backups are easy** and data can be restored or imported.
8. The architecture is ready for **more portfolio types** (cash, ETF/mutual-fund buy-and-hold, bonds, options, precious metals) and a **unified dashboard**.
9. It runs on Fedora KDE first and is portable to **Windows**, with Flatpak and .exe packaging.

### Non-goals (v1)
Short selling, commissions, real-time streaming quotes, broker integration, tax-lot reporting, multi-currency, phone or email alerts, monthly risk budgets.

---

## 2. Interview decisions

| Topic | Decision |
|---|---|
| Direction | **Long only.** The data model keeps a `side` field so shorts can be added later. |
| Add-on buys | **Each buy is its own lot**, with its own entry, initial stop, R and 25% sell ladder. The position row aggregates its lots. |
| Strategy milestones | **The app suggests, the user confirms.** The app computes the ladder stop and next target and shows an *Action due* badge. The user records sells and confirms each stop raise with one click. |
| Commissions | **Not tracked.** |
| Risk model | **Per-trade risk only:** risk per trade = max portfolio amount × risk % (default 1%). There is no monthly budget or monthly stop. |
| Storage | **Local SQLite** with automatic timestamped backups to a user-chosen folder (e.g. a NAS mount), plus manual export and import. |
| Alerts | **In-app highlights and desktop notifications.** |
| Market data | **Free `yfinance`** behind a pluggable provider interface, plus manual overrides. |
| Theme | **Dark trading-terminal look** (see DESIGN_SYSTEM.md). |
| Charts | Candlesticks with stop and target lines, equity curve, R-multiple histogram, and allocation by sector and by position. |
| Stats | **All the spreadsheet stats** plus **open-risk metrics**. |
| Cash | **No cash balance in a Stock Trading portfolio** (revised 2026-11-20). It tracks positions that may live in several accounts; portfolio value = market value of open positions. Dividends are recorded per stock (they count in Total P/L). A Stock Trading or Long-term portfolio can **settle in a cash account** of a Cash portfolio (2026-11-26): its sells, buys and cash dividends move that account's balance, which never counts toward Deployed / Max. |

---

## 3. Platform & tech stack

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.12+ | |
| GUI | **PySide6** (Qt 6, LGPL) | Native on KDE and Windows. Qt Widgets, not QML. |
| Charts | **PyQtGraph** | Fast and themeable. `CandlestickItem` is custom (about 40 lines). |
| Data | **pandas**, **NumPy** | Price history and stats. |
| Storage | stdlib `sqlite3` | One file, WAL mode, the backup API for hot copies. |
| Market data | `yfinance` | Behind the `MarketDataProvider` protocol. |
| Paths | `platformdirs` | No hard-coded paths. Linux uses `~/.local/share/Trailhead`; Windows uses `%LOCALAPPDATA%`. |
| Notifications | `desktop-notifier` | Uses D-Bus on Linux and WinRT on Windows. `QSystemTrayIcon.showMessage` is the fallback. |
| Tooling | `uv`, `pyproject.toml`, src layout, `ruff`, `pytest`, `pytest-qt` | |
| Packaging | Flatpak (`org.kde.Platform` + `io.qt.PySide.BaseApp`), PyInstaller (Windows) | Phase 8. |

**Paid data options, for later:** Tiingo (~$10/mo, clean EOD and IEX quotes), Financial Modeling Prep (~$20/mo, includes earnings calendar and sector), Polygon.io/Massive (from ~$29/mo), and Alpha Vantage (limited free tier, paid from ~$50/mo). Each would be a new `MarketDataProvider` with an API key set in Settings. yfinance is unofficial and occasionally breaks, which is why the provider layer exists and why manual entry is always available.

---

## 4. Architecture

```
trailhead/
  pyproject.toml
  README.md
  docs/PLAN.md, docs/DESIGN_SYSTEM.md
  src/trailhead/
    __main__.py / app.py      # QApplication bootstrap, load fonts + QSS, open DB, show MainWindow
    core/                     # PURE PYTHON — must never import Qt
      models.py               # dataclasses (frozen where sensible)
      r_engine.py             # R math + milestone ladder
      positions.py            # trades → lots → positions; FIFO sell matching
      stats.py                # performance + open-risk statistics
      sizing.py               # position sizing + max-amount checks
      money.py                # Decimal helpers, rounding, formatting-agnostic math
    portfolio_types/
      base.py                 # PortfolioType ABC
      registry.py             # type registry
      stock_trading/
        type.py               # StockTradingType(PortfolioType)
        tab.py                # PortfolioTab widget (KPI strip + table + detail pane)
        table_model.py        # PositionsTreeModel(QAbstractItemModel)
        delegates.py          # pill/badge/number painting
        kpis.py               # metric → KpiCard mapping
    data/
      db.py                   # connect(), PRAGMAs, migrate()
      migrations/001_init.sql
      repo.py                 # PortfolioRepo, TradeRepo, CashRepo, StopRepo, MarketRepo, AlertRepo, SettingsRepo
      backup.py               # backup_now(), rotate(), restore(), export_json(), import_json()
      importers/spreadsheet_csv.py   # optional: import legacy sheet rows
    market/
      provider.py             # MarketDataProvider Protocol + DTOs
      yfinance_provider.py
      manual_provider.py
      service.py              # MarketService(QObject): refresh() on QThreadPool, signals
    alerts/
      engine.py               # pure: evaluate(state) -> list[Alert]
      notifier.py             # desktop-notifier / tray fallback
    ui/
      theme/tokens.py, theme/qss.py, theme/fonts/   # Inter + JetBrains Mono (OFL)
      main_window.py
      widgets/ kpi_card.py, pill.py, badge.py, segmented.py, toast.py, empty_state.py, sidebar.py
      charts/ candle_chart.py, equity_chart.py, r_histogram.py, allocation_chart.py
      dialogs/ trade_dialog.py, cash_dialog.py, portfolio_dialog.py, price_override_dialog.py,
               dividend_review_dialog.py, settings_dialog.py, alerts_panel.py
  tests/
    core/ test_r_engine.py, test_positions.py, test_stats.py, test_sizing.py
    data/ test_migrations.py, test_repo.py, test_backup.py
    market/ test_service_fake_provider.py
    ui/ test_smoke.py
  packaging/ flatpak/…yml, pyinstaller/trailhead.spec, icons/, *.desktop
```

### Layering rules
- `core/` is pure Python with no Qt or I/O. Everything here is unit-tested.
- `data/` and `market/` do I/O and never touch widgets.
- `ui/` and `portfolio_types/*/tab.py` call services and repositories and never run SQL directly.
- Network and slow database work run on `QThreadPool` and send results back to the UI through signals. The UI thread never blocks.

### Extensibility: portfolio types

```python
class PortfolioType(ABC):
    key: str                      # "stock_trading"
    label: str                    # "Stock Trading"
    icon: str
    @abstractmethod
    def create_tab(self, ctx: AppContext, portfolio: Portfolio) -> QWidget: ...
    @abstractmethod
    def summary_metrics(self, ctx: AppContext, portfolio: Portfolio) -> PortfolioSummary: ...
    def new_entry_action(self) -> QAction | None: ...   # e.g. "New Trade"
    def settings_fields(self) -> list[FieldSpec]: ...   # extra per-portfolio settings
```

- `PortfolioSummary` holds `value`, `cash`, `day_change`, `unrealized_pl`, `realized_pl`, `open_risk` and `extras: dict`.
- The **Unified Dashboard** iterates over every non-archived portfolio and calls `dashboard_summary()` (default: built from `summary_metrics()`) to show cross-portfolio totals, a combined P/L curve and allocation by portfolio or sector.
- Built types: `stock_trading`, `long_term`, `cash` (DECISIONS.md). Future types (`etf_hold`, `bonds`, `options`, `metals`) add their own tables in a new migration plus a `PortfolioType` subclass. The shared tables are `portfolios`, `cash_events`, `instruments`, `quotes`, `price_history` and `dividends`.

---

## 5. Data model (SQLite)

The database file is `platformdirs.user_data_dir("Trailhead")/portfolio.db`. It runs with `PRAGMA journal_mode=WAL; foreign_keys=ON`. Money is stored as TEXT decimals or as REAL rounded at boundaries; in Python it is always `Decimal`.

```sql
CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE portfolios (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT NOT NULL DEFAULT 'stock_trading',
  max_amount NUMERIC NOT NULL,
  risk_pct NUMERIC NOT NULL DEFAULT 0.01,
  settings_json TEXT NOT NULL DEFAULT '{}',
  archived INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE trades (
  id INTEGER PRIMARY KEY,
  portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
  trade_date TEXT NOT NULL,           -- ISO date
  shares NUMERIC NOT NULL CHECK (shares > 0),
  price NUMERIC NOT NULL CHECK (price > 0),
  initial_stop NUMERIC,               -- BUY only, required for BUY, < price
  strategy TEXT,                      -- BUY only: Pullback|Breakout|Reversion|NULL
  timeframe TEXT,                     -- BUY only: Monthly/Weekly|Weekly/Daily|Daily/Hourly|Intraday|NULL
  lot_id INTEGER REFERENCES trades(id) ON DELETE RESTRICT,  -- SELL only: which BUY lot
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX ix_trades_pf_symbol ON trades(portfolio_id, symbol, trade_date);

CREATE TABLE stop_changes (
  id INTEGER PRIMARY KEY,
  lot_id INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
  effective_date TEXT NOT NULL,
  stop NUMERIC NOT NULL,
  reason TEXT NOT NULL CHECK (reason IN ('MANUAL','LADDER_CONFIRMED')),
  created_at TEXT NOT NULL
);

CREATE TABLE cash_events (
  id INTEGER PRIMARY KEY,
  portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
  event_date TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('DEPOSIT','WITHDRAWAL','DIVIDEND','ADJUSTMENT')),
  amount NUMERIC NOT NULL,            -- signed: withdrawals negative
  symbol TEXT,
  dividend_ex_date TEXT,              -- links DIVIDEND to dividends row
  note TEXT
);

CREATE TABLE instruments (
  symbol TEXT PRIMARY KEY,
  name TEXT, sector TEXT, industry TEXT,
  next_earnings_date TEXT, div_yield NUMERIC, annual_dividend NUMERIC,
  updated_at TEXT
);

CREATE TABLE quotes (
  symbol TEXT PRIMARY KEY,
  last NUMERIC NOT NULL, prev_close NUMERIC,
  as_of TEXT NOT NULL, source TEXT NOT NULL, is_manual INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE price_history (
  symbol TEXT NOT NULL, date TEXT NOT NULL,
  open NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC, volume INTEGER,
  PRIMARY KEY (symbol, date)
);

CREATE TABLE dividends (
  symbol TEXT NOT NULL, ex_date TEXT NOT NULL, pay_date TEXT,
  amount_per_share NUMERIC NOT NULL, source TEXT NOT NULL,
  PRIMARY KEY (symbol, ex_date)
);

CREATE TABLE alerts_log (
  id INTEGER PRIMARY KEY,
  portfolio_id INTEGER NOT NULL, symbol TEXT NOT NULL, lot_id INTEGER,
  kind TEXT NOT NULL,      -- STOP_HIT|TARGET_REACHED|NEAR_STOP|EARNINGS_SOON
  level NUMERIC, message TEXT NOT NULL,
  triggered_at TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0,
  UNIQUE (lot_id, kind, level)
);

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

**Derived, never stored:** lots, positions, remaining shares, P/L, R, ladder state, stats, open risk and portfolio value. Editing or deleting any trade recomputes everything. With at most a few hundred trades this takes milliseconds.

**Settings keys:** `backup.dir`, `backup.keep` (30), `refresh.on_startup` (true), `refresh.auto_minutes` (0 = off), `alerts.near_stop_pct` (0.02), `alerts.earnings_days` (7), `alerts.desktop` (true), `ui.table_density`, `ui.column_state.<portfolio_id>`, `ui.window_geometry`.

---

## 6. Domain logic

### 6.1 Lots and positions (`core/positions.py`)
- A **lot** is one BUY trade. `remaining = shares − Σ SELL.shares where lot_id = lot.id`.
- A **SELL** must reference a lot. The trade dialog defaults to **FIFO** (the oldest open lot of that symbol in that portfolio) and lets the user pick another lot. If a sell is larger than the selected lot, the dialog offers to **split it across lots in FIFO order**, which creates several SELL rows.
- A **position** groups the lots of one symbol from the first BUY (while flat) until remaining shares reach 0. The position is then **Closed**. A later BUY of the same symbol starts a new position.
- Position aggregates:
  - open shares, original shares;
  - weighted average entry of open lots;
  - market value;
  - unrealized P/L = Σ remaining × (last − entry);
  - realized P/L = Σ sells × (sell price − lot entry);
  - dividends received (cash events with that symbol in the position's date range);
  - total P/L.

### 6.2 R engine (`core/r_engine.py`)

For each lot, with `E` the entry price, `S0` the initial stop, `N` the original shares and `R = E − S0` (validated > 0):

- `level(k) = E + k·R`
- Tranche size `q = floor(N / 4)`. Tranches 1–3 are `q` shares each, and the runner (tranche 4) is `N − 3q`.

| Milestone | Trigger (high-water ≥) | Suggested sell | Ladder stop after |
|---|---|---|---|
| M0 Start | — | — | `E − 1R` (initial stop) |
| M1 | `level(1)` | tranche 1 (25%) | `E − 0.5R` |
| M2 | `level(2)` | — | `E` (break-even) |
| M3 | `level(3)` | tranche 2 (25%) | `E` |
| M4 | `level(6)` | tranche 3 (25%) | `level(3)` |
| T9 | `level(9)` | — | `level(6)` |
| T12 | `level(12)` | — | `level(9)` |
| T(3k) | `level(3k)`, k ≥ 3 | — | `level(3k − 3)` |

Definitions:
**Exit plans (added 2026-11-20).** Each BUY picks an exit plan (stored in `trades.exit_plan`, default 1-3-6):
- **1-3-6** — the table above (sell ¼ at 1R, 3R, 6R; trail the runner 3R behind in 3R steps).
- **1-2-3** — sell ¼ at 1R, 2R, 3R; stop → E at 1R, level(1) at 2R, level(2) at 3R; then at each new whole R `k ≥ 4`
  the stop rises to level(k − 1).

Prompts (sell / raise stop) happen only at the plan's milestones; the **next target** shown is always the next whole
R above the HWM (e.g. 1-3-6 at 4.2R → target 5R, next action at 6R).

- **High-water mark (HWM)** = max(daily highs in `price_history` from the entry date onward, last quote). A milestone counts as reached once the HWM crosses it, even if the price later falls back.
- **Highest milestone reached** = the largest milestone whose trigger ≤ HWM.
- **Next target** = the next whole R above the HWM (at least 1R). This is the cyan pill. The next *action* level is
  the trigger of the next milestone above the highest one reached.
- **Ladder stop** = the stop for the highest milestone reached.
- **Current stop** = the latest `stop_changes.stop` for the lot, or `S0` if there is none. This is the amber pill, and it only changes when the user confirms or edits it.
- **Shares expected sold** at the highest milestone reached: 0 (M0), q (M1, M2), 2q (M3), 3q (M4 and up). **Shares actually sold** = Σ SELLs on the lot.
- **Action due** (one or more apply):
  - `SELL_TRANCHE`: shares actually sold < shares expected sold. Suggest selling the difference at about the current price.
  - `RAISE_STOP`: ladder stop > current stop. Suggest "Raise stop → $X", with a **Confirm** button that writes `stop_changes(reason='LADDER_CONFIRMED')`.
  - `STOP_HIT`: last ≤ current stop. Suggest "Stop hit — sell remaining N sh". This has the highest priority and shows a red row accent.
- **Current R (unrealized)** = (last − E) / R.
- **Realized R of a lot** = Σ(sell shares × (sell price − E)) / (N × R). This only becomes final once the lot is closed.
- **Lot open risk** = remaining × max(0, last − current stop).

  This is the money at risk from today's price if the stop is hit. A second measure, *risk to entry*, = remaining × (E − current stop) and is shown as negative when the stop is above entry ("locked-in profit").

The **position row** shows:
- the **highest current stop** among open lots (the nearest exit);
- the **lowest next target** among open lots;
- the highest-priority action due.

The **detail pane** shows the ladder for each lot.

### 6.3 Statistics (`core/stats.py`)

The **unit of a trade is a closed lot**. This matches the spreadsheet, where each row is one buy taken to completion. A toggle can group the stats by position instead.

| Stat | Formula |
|---|---|
| Wins / Losses | Closed lots with realized P/L > 0 / < 0. Zero P/L counts as neither. |
| Win % | wins / (wins + losses) |
| Profit factor | Σ winning P/L / \|Σ losing P/L\|. Shown as **"—"** when there are no losses. |
| Expectancy (R) | mean of the closed lots' realized R |
| StDev (R) | sample standard deviation of realized R (n−1); "—" when n < 2 |
| R / StDev | expectancy / StDev |
| Realized P/L | Σ realized P/L (all sells in the period) |
| Unrealized P/L | Σ over open lots |
| Open risk $ / % | Σ lot open risk; ÷ portfolio value |
| Deployed | Σ remaining × entry (cost basis of open lots), compared with `max_amount` |
| Dividends | Σ DIVIDEND cash events in the period |
| Portfolio value | Σ remaining × last (open positions only; no cash) |
| Day change | Σ remaining × (last − prev_close) |

**Period filter:** MTD, QTD, YTD, Last 12 months, All, or Custom. Closed-lot stats use the lot's close date; realized P/L uses each sell's date.

### 6.4 Sizing and warnings (`core/sizing.py`)
- `risk_per_trade = max_amount × risk_pct`
- `suggested_shares = floor(risk_per_trade / (price − stop))`, rounded down to a multiple of 4 so the four tranches are whole shares (below 4 shares, the plain count). Entering a symbol fetches its current price for the Price field.
- Warnings shown in the trade dialog. They don't block saving; the user can choose "Save anyway".
  - `EXCEEDS_MAX`: deployed + new cost > max_amount (the explicit requirement).
  - `EXCEEDS_RISK`: shares × R > risk_per_trade × 1.05.
  - `STOP_INVALID` (blocks saving): stop ≥ price.
- The dashboard's *Deployed vs Max* bar turns warning-colored above 90% and negative-colored above 100%.

### 6.5 Golden test (from the spreadsheet)
Portfolio: max_amount 100,000 and risk 1%, so risk per trade is $1,000. Cash deposit of 100,000 on 2016-07-29.

| | Buy | Shares | Entry | Stop | Sell date | Sell price |
|---|---|---|---|---|---|---|
| Lot 1 | 2016-07-29 W | 50 | 43.18 | 40.26 | 2016-08-02 | 46.24 |
| Lot 2 | 2016-07-29 W | 50 | 43.18 | 40.26 | 2016-08-04 | 49.01 |

Expected values:
- R = 2.92
- level(1) = 46.10, level(2) = 49.02, level(3) = 51.94, level(6) = 60.70
- M1 stop = 41.72
- Lot 1 R = 1.05 (P/L $153.00); Lot 2 R = 2.00 (P/L $291.50)

  The spreadsheet shows $152.50 and $291.00 because it includes $0.25 commissions on each side. The app ignores commissions.
- Wins 2, Losses 0, Win % 100%, Profit factor "—"
- Expectancy **1.52**, StDev **0.67**, R/StDev **2.27**
- Position W is Closed; position realized R = 444.50 / 292 = 1.52

---

## 7. User interface

The visual spec is in [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md).

### 7.1 Main window
```
┌────────┬──────────────────────────────────────────────────────────────────────┐
│ ◧ Dash │  Swing Trading ▾        ● Prices 10:42 ⟳   🔔3   [ Cash ]  [＋ New Trade ]│
│ ▣ Swing│──────────────────────────────────────────────────────────────────────│
│  (+)   │ [Value][Cash][Day Δ][Unreal P/L][Real P/L][Open Risk][Deployed/Max]   │
│        │ [Win %][Profit F.][Expectancy R][R/StDev]           Period: YTD ▾     │
│        │──────────────────────────────────────────────────────────────────────│
│        │ Filter: (Open)(Closed)(All)   🔍 symbol            Columns ▾          │
│        │ ▸ NVDA  Tech  OPEN  Breakout  Daily/Hourly  150/200 …  [stop 118.40] [tgt 131.20] ⚠ Raise stop│
│        │ ▾ W     Cons.D CLOSED …                                               │
│        │     07-29 BUY 50 @ 43.18  stop 40.26  R 2.92                         │
│        │     08-02 SELL 50 @ 46.24 +153.00  +1.05R                            │
│        │──────────────────────────────────────────────────────────────────────│
│ ⚙ Set. │ Detail: candle chart with entry/stop/target/R-ladder lines │ lot ladders │
└────────┴──────────────────────────────────────────────────────────────────────┘
```
- **Sidebar:** Dashboard, the list of portfolios (a type icon and name, with a right-click menu to rename, edit settings, export or archive), **＋ New portfolio**, and Settings at the bottom.
- **Header:** the portfolio switcher, a price-status chip (green if fresh, amber if stale for more than 1 trading day or after a failed refresh, with a tooltip showing the source and time), the Refresh button (F5), the alerts bell with a count, **Cash**, and **New Trade** (Ctrl+N).
- **Menu bar:** File (New portfolio, Backup now, Restore…, Export portfolio JSON, Import portfolio JSON, Export table CSV, Import legacy spreadsheet CSV, Quit) · Edit (Edit trade, Delete trade, Settings) · View (Density, Columns, Show closed) · Data (Refresh prices, Pending dividends, Manual price, Manual dividend) · Help.

### 7.2 KPI strip
The KPI cards wrap onto a second row on narrow windows. Each card shows a label (muted), a value (mono, 20px), and an optional delta or sub-line:

| Card | Value | Sub-line |
|---|---|---|
| Portfolio value | $ | Δ day $ / % |
| Dividends | $ in period | # payments, all-time total |
| Unrealized P/L | $ (signed, colored) | % of cost |
| Realized P/L | $ (signed, colored), period | # sells |
| Open risk | $ | % of value. Warning color when > 6% (a setting). |
| Deployed / Max | $ / $ | progress bar |
| Win % | % | W–L |
| Profit factor | x.xx or — | |
| Expectancy | x.xx R | n trades |
| R / StDev | x.xx | StDev x.xx |

An **R distribution** panel (340px wide, the height of two card rows) sits to the right of the strip: the R
multiples of the closed trades behind Win % / Expectancy in 0.5R bins, with the expectancy line. It follows the
same Period and Lot / Position switches.

### 7.3 Positions table
`QTreeView` with a custom `PositionsTreeModel`. The column order is configurable and remembered.

**Position (parent) row:**

| Column | Notes |
|---|---|
| ▸ | Expand caret. Clicking anywhere on the row toggles it; double-click opens the detail pane. |
| Symbol | Bold, with the company name in the tooltip |
| Sector | S&P sector, muted |
| Status | Badge: OPEN, CLOSED, or STOP HIT |
| Strategy / Timeframe | From the first lot. "Mixed" if the lots differ. |
| Shares | open / original |
| Avg entry | |
| Last | With a "M" badge for a manual price |
| Day % | Colored |
| Mkt value | |
| % of port. | |
| **Stop** | Amber pill with the distance (`−4.1%`, `−1.3R`) |
| **Next target** | Cyan pill with the distance |
| R now | Unrealized R, colored |
| Unreal. P/L | |
| Real. P/L | |
| Total P/L | Includes dividends |
| Open risk | |
| Earnings | Date, with an "Earnings soon" badge if within 7 days |
| Div yield | |
| Days held | |
| Action | Badge (Sell 12 sh @1R / Raise stop → $41.72 / Stop hit). Hovering shows inline buttons **Confirm stop** and **Record sell**. |

**Trade (child) rows:** listed by date, with stop-change events shown inline (muted italic):
- BUY: date · BUY · shares · price · initial stop · R/share · $ risk · strategy · timeframe · notes icon
- SELL: date · SELL · shares · price · lot # · realized P/L · realized R
- STOP: date · "Stop → $X" · reason

Interactions:
- Double-click a trade to edit it.
- Delete asks for confirmation. Deleting a BUY that has SELLs is blocked until the SELLs are removed.
- Context menu: Edit, Delete, Record sell (pre-filled), Set stop…, Override price…, Copy symbol.
- Sorting applies to parent rows only.
- Open/Closed/All filter (default Open: open positions only) and a symbol search box.

### 7.3a Performance view
A **Positions | Performance** switch on the table panel. Performance compares closed trades by Strategy, Time
frame or Exit plan, or Strategy × Time frame (time frames nested under each strategy): Closed, Open, Win %, Avg
win / loss (R), Profit factor, Expectancy ± 2 SE, an R range strip (every trade's R plus the expectancy bar), StDev,
SQN (Van Tharp, with rating), P/L, Avg days, plus a total row. LOW SAMPLE marks groups under 10 closed trades. Selecting a
group shows its R distribution in the panel beside the KPI cards. Same Period and Lot / Position switches.

### 7.4 Detail pane (bottom splitter, collapsible)
- **Candle chart** (PyQtGraph): about 6 months of daily candles and volume.
  - Horizontal lines:
    - entry (white dashed);
    - current stop (amber, solid, labeled);
    - next target (cyan, solid, labeled);
    - R ladder −1R, −0.5R, 0, 1R, 2R, 3R, 6R, 9R… (muted dotted, labeled on the right).
  - Markers: ▲ buys, ▼ sells, an earnings-date vertical marker, and dividend ex-date ticks.
  - Mouse-wheel zoom and a crosshair with an OHLC readout.
- **Lot ladder cards** beside the chart: one card per open lot showing the milestone checklist (✓ reached / ○ pending), the tranche status, the current stop vs the ladder stop, and the Confirm button.

### 7.5 Dashboard tab
Redesigned 2026-11-23 (see DECISIONS.md). Covers every non-archived portfolio; the period is a MTD / QTD / YTD / 12M /
All switch.
- **Summary band:** total value of all portfolios with today's change, P/L and income for the period, and a short
  cumulative P/L chart (combined line; a dashed line per portfolio when there are several).
- **Portfolios table:** Value, Share, Today, P/L, Unrealized, Income, Open risk (warning when over the limit), Trend
  sparkline; a Total row when there are several portfolios. Double-click or Enter opens the portfolio.
- **Right rail:** *Today* (biggest moves across all holdings; needs attention and coming up) above *Allocation*
  (by portfolio or by sector).
- Each portfolio type supplies its row through `PortfolioType.dashboard_summary()`.

### 7.6 Dialogs

**New/Edit Trade** (modal, about 520px wide):
```
  [  BUY  |  SELL  ]                         ← segmented control (keyboard B / S)
  Symbol   [ NVDA      ]  NVIDIA Corp · Technology · last 127.40
  Date     [ 2026-11-19 📅]
  ── BUY ──────────────────────────────────────
  Price    [ 127.40 ]      Initial stop* [ 121.00 ]
  Shares   [ 156    ]      ← "Use suggested: 156"
  Strategy [ — ▾ ] (Pullback/Breakout/Reversion)   Timeframe [ — ▾ ]
  ┌ Risk panel ─────────────────────────────────┐
  │ R/share 6.40 · Risk $998 (1.00%) · Cost $19,874 │
  │ 1R 133.80 · 2R 140.20 · 3R 146.60 · 6R 165.80   │
  │ ⚠ Deployed would be $104,200 > max $100,000     │
  └──────────────────────────────────────────────┘
  ── SELL ─────────────────────────────────────
  Lot      [ #12 2026-10-02 · 100 rem · R 2.10 ▾ ]  (FIFO default)
  Shares   [ 25 ]  ← "Suggested tranche: 25"   Price [ 131.20 ]
  Realized: +$480.00 · +0.91R
  Notes    [                                   ]
                                   [Cancel]  [Save trade ⏎]
```
- Symbol entry uppercases automatically, completes from known symbols, and validates against the provider asynchronously. An unknown symbol gives a warning, not an error, so offline use still works.
- Price pre-fills from the last quote. Date defaults to today.
- Strategy and timeframe can be left blank and edited later.
- Validation errors appear inline under each field.

Other dialogs:
- **Cash portfolio dialogs:** new / edit account, Move money (deposit, withdrawal, transfer), True up, Confirm interest, Change rate (DECISIONS.md, Cash portfolio type). The old per-portfolio cash ledger dialog was removed.
- **Portfolio dialog:** name, type (only Stock Trading in v1), max amount, risk % (default 1%).
- **Price override:** symbol, price, as-of. It is flagged with the "M" badge until the next successful fetch clears it, or it can be pinned.
- **Dividend review:** a table of proposed dividends (symbol, ex-date, pay date, $/sh, shares held, amount) with Accept, Edit and Dismiss. Manual add is also available.
- **Settings:** backup folder and retention, refresh options, alert thresholds, desktop notifications on or off, table density, data provider (yfinance only in v1, with the field reserved for an API key).

### 7.7 Keyboard
Ctrl+N new trade · Ctrl+D cash · F5 refresh · Ctrl+F search · Enter/Space expand row · Ctrl+E edit · Del delete · Ctrl+B backup now · Ctrl+1…9 switch portfolio.

---

## 8. Market data

```python
class MarketDataProvider(Protocol):
    name: str
    def quotes(self, symbols: list[str]) -> dict[str, QuoteDTO]: ...
    def history(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...   # OHLCV daily
    def dividends(self, symbol: str, start: date) -> list[DividendDTO]: ...
    def profile(self, symbol: str) -> ProfileDTO: ...   # name, sector, industry, next_earnings, div_yield
```
- `YFinanceProvider` uses:
  - `yf.download(tickers, period="5d", group_by="ticker")` for batched last and previous close;
  - `Ticker.history` for OHLCV;
  - `Ticker.dividends` for dividend history;
  - `Ticker.info` (sector, industry, dividendYield) and `Ticker.calendar` (earnings date) for the profile.

  Every call is wrapped so that exceptions become `ProviderError`.
- `MarketService(QObject)`:
  - `refresh(portfolio_ids)` gathers the symbols of open positions plus positions closed within 30 days.
  - It runs on the QThreadPool. Quotes are fetched every time; history is incremental from the last stored date (or 1 year back); profile and dividends at most once per day per symbol.
  - It writes through the repositories, then emits `refreshed(summary)` or `failed(errors)`.
  - It has a 10-second per-request timeout and one retry.
- **Manual overrides:** `quotes.is_manual=1` rows are not overwritten unless the override is not pinned and a fresh price arrives.
- **Triggers:** on startup (if the setting is on), F5 or the Refresh button, and optionally every N minutes on weekdays from 09:30 to 16:00 America/New_York.
- **Dividends:** after a refresh, for each dividend row with an ex-date on which the portfolio held shares (shares held at the end of the day before the ex-date) and no linked cash event yet, the app creates a *pending* proposal and shows a badge on Data ▸ Pending dividends. Accepting creates a `DIVIDEND` cash event.

---

## 9. Alerts

`alerts/engine.py` is pure: `evaluate(lots_state, quotes, instruments, settings) -> list[Alert]`.

| Kind | Condition | Severity |
|---|---|---|
| STOP_HIT | last ≤ current stop | critical |
| TARGET_REACHED | HWM ≥ next milestone trigger that has not yet been acted on | info |
| NEAR_STOP | last ≤ current stop × (1 + near_stop_pct) | warning |
| EARNINGS_SOON | next earnings within N days | info |

- Alerts are deduplicated by `alerts_log UNIQUE(lot_id, kind, level)`, so each fires once per level.
- On a new alert:
  - the row is highlighted;
  - the bell count goes up;
  - a toast appears;
  - a desktop notification is sent if enabled (title "NVDA — Stop hit", body "Last 118.10 ≤ stop 118.40. Sell remaining 75 sh.").
- The alerts panel lists alerts with Acknowledge / Acknowledge all.
- **Discord (optional):** a channel webhook in Settings. Each alert has a category (Stop hit, Near stop,
  Target: sale suggested, Target: raise stop only, Earnings soon); new alerts in the ticked categories are posted as
  embeds (≤ 10 per message), default Stop hit + Target: sale suggested. A "Send test" button checks the URL.
  `alerts/discord.py`, stdlib `urllib`, background thread; a failure shows a warning toast.
- Because the engine is UI-free, a headless `trailhead --check-alerts` command (cron or systemd timer, e.g. on the NAS) can be added later.

---

## 10. Backup, restore, import & export

- **Automatic:** on app close and at most once per day while running, `sqlite3.Connection.backup()` writes `portfolio-YYYYMMDD-HHMMSS.db` into `settings.backup.dir`. The default is `platformdirs.user_documents_dir()/Trailhead Backups`; the user can point it at a NAS mount. The newest `backup.keep` copies (30) are kept. If the folder is unavailable (e.g. the NAS is not mounted), the app shows a non-blocking warning toast.
- **Backup now** (Ctrl+B): the same backup, with a "Save as…" option.
- **Restore:**
  1. Choose a `.db` file.
  2. Validate it (`PRAGMA integrity_check`, the schema_version is ≤ the app's version, and the expected tables exist).
  3. Back up the current database first automatically.
  4. Swap the files.
  5. Run migrations and reload.
- **Export portfolio → JSON:** a versioned document holding the portfolio, trades, stop changes, cash events and the referenced instruments. **Import JSON** always creates a **new** portfolio, renamed "(imported)" if the name already exists, with all IDs remapped.
- **Export table → CSV** (and XLSX if `openpyxl` is installed): the current table view.
- **Import legacy spreadsheet CSV** (optional, phase 7): maps columns B (date), C (symbol), E (shares), F (price), I (stop), K (sell date) and L (exit price) to a BUY plus a SELL, skipping blank and `#DIV/0!` rows.

---

## 11. Build phases & acceptance criteria

Each phase ends with the app runnable, `uv run pytest` green, and `ruff check` clean.

| # | Phase | Acceptance |
|---|---|---|
| 1 | **Scaffold & design system** | `uv run trailhead` opens a themed window with the sidebar, header, empty KPI strip, empty table with an empty state ("Add your first trade"), and the bundled fonts loaded. `tokens.py` and `qss.py` are implemented, and a hidden debug window (`--gallery`) shows every component. |
| 2 | **Core & data** | Migrations create the schema. Repositories have CRUD with tests. `positions`, `r_engine`, `stats` and `sizing` pass all tests, including the §6.5 golden test and ladder transitions through T12. |
| 3 | **Trade entry & table** | Create a portfolio; add, edit and delete BUYs and SELLs; FIFO lot matching and splitting work; parent and child rows expand and collapse; a position shows Closed when flat; cash deposits work; the max-amount warning appears; column state is remembered. |
| 4 | **Market data** | Prices refresh on startup and on F5 without freezing the UI; the stale chip shows; manual override with the "M" badge works; sector and earnings are filled in; failures show a toast and keep the cached values. Tests use a fake provider. |
| 5 | **Strategy UX & alerts** | Stop and target pills, the Action badge, Confirm stop (writes `stop_changes`), Record sell pre-fill, the alert engine with dedup, the bell panel and desktop notifications. |
| 6 | **Dashboard & charts** | All KPI cards are live with the period filter; the candle chart shows level lines and markers; the equity curve, R histogram and allocation charts are themed from the tokens. |
| 7 | **Dividends, backup & import** | Pending-dividend review and manual dividends; automatic and manual backups with rotation; restore with a safety backup; JSON export and import round-trips (tested); CSV export; legacy spreadsheet CSV import. |
| 8 | **Packaging** | Flatpak manifest builds and runs on Fedora (`flatpak-builder`), with `--filesystem=home` or portal access for the backup dir and a network permission. The PyInstaller spec builds a Windows one-folder exe. The app icon, `.desktop` file and AppStream metainfo are in place. |

---

## 12. Testing & verification

- **Unit (core):**
  - the golden test;
  - each milestone transition, using an HWM sequence of 45 → 46.10 → 49.02 → 51.94 → 60.70 → 69.46 → 78.22 on the golden lot (levels 1/2/3/6/9/12R);
  - tranche rounding for N = 50, 101 and 3;
  - FIFO with several lots;
  - a sell larger than a lot is split;
  - closing and reopening a symbol creates a new position;
  - stats with no trades, no losses and a single trade;
  - sizing and warnings.
- **Data:** migration from empty; repository round-trips; backup, restore and integrity checks; JSON export and import ID remapping.
- **Market:** a fake provider checks the service's writes, incremental history, the manual-override precedence and error handling (offline).
- **UI (pytest-qt):**
  1. Start with a temporary database.
  2. Create a portfolio.
  3. Add a deposit and a BUY.
  4. Expand the row and check there is 1 child.
  5. Record a SELL.
  6. The KPI realized P/L updates.
  7. Set a manual price below the stop, run the alerts, and a STOP_HIT badge appears.
- **Manual:** enter the W trades from the spreadsheet and compare the stats; refresh real prices for a few tickers; check the chart lines; back up to the NAS folder and restore.

---

## 13. Future expansion (designed for, not built)
- Additional `PortfolioType`s: ETF/Mutual-fund buy-and-hold (allocation targets, rebalancing, total return), Bonds (coupon, maturity, YTM, ladder), Options (legs, expiry, Greeks). (Bullion in ounces is part of Long-term Holdings since 2026-11-26.)
- A full Unified Dashboard: net worth, allocation by portfolio and asset class, and a combined equity curve.
- Short selling (`side` SHORT/COVER, reversed ladder).
- A headless alert checker on the NAS with ntfy or email notifications.
- Paid data providers, a light theme, and per-strategy and per-timeframe analytics.

---

## 14. Build prompt

> Copy everything below into a coding agent (e.g. Claude Code) opened in `~/Projects/trailhead/`.

```text
You are building "Trailhead", a cross-platform desktop app (Fedora KDE first, Windows later) for tracking an
R-multiple stock-trading portfolio. The complete specification is in docs/PLAN.md and the visual spec is in
docs/DESIGN_SYSTEM.md. Read both files fully before writing any code; they are the source of truth. If something is
ambiguous, choose the option most consistent with those documents and note it in docs/DECISIONS.md.

STACK: Python 3.12+, PySide6 (Qt Widgets), PyQtGraph, pandas, NumPy, sqlite3, yfinance, platformdirs,
desktop-notifier, pytest, pytest-qt, ruff. Use uv with pyproject.toml and a src/ layout (package `trailhead`,
console script `trailhead`). Bundle Inter and JetBrains Mono (OFL) under ui/theme/fonts.

HARD RULES
1. core/ is pure Python: no Qt imports, no I/O. Domain math uses Decimal. Everything in core/ is unit-tested.
2. Never block the UI thread: network and slow DB work run on QThreadPool and return results through signals.
3. No hard-coded paths (use platformdirs) and no hard-coded colors or sizes in widgets (use ui/theme/tokens.py and
   the generated QSS).
4. Derived values (lots, positions, P/L, R, ladder state, stats) are computed from stored rows and never persisted.
5. Long-only, no commissions, per-trade risk only (max_amount × risk_pct, default 1%). Each BUY is its own lot with
   its own R ladder; SELLs reference a lot (FIFO default, user-selectable, split across lots if needed).
6. Strategy milestones are SUGGESTED, never auto-applied: show the ladder stop and next target, raise "Action due",
   and the user confirms stop raises (writes stop_changes) and records sells.
7. Portfolio types go through the PortfolioType ABC + registry so future types (cash, ETF, bonds, options, metals)
   and the Unified Dashboard plug in without changing the core.
8. Every phase ends runnable, with `uv run pytest` passing and `ruff check` clean. Commit at the end of each phase with
   a descriptive message.

WORK PLAN — implement the phases in docs/PLAN.md §11 in order, meeting each phase's acceptance criteria:
  1 Scaffold & design system → 2 Core & data → 3 Trade entry & table → 4 Market data →
  5 Strategy UX & alerts → 6 Dashboard & charts → 7 Dividends, backup & import → 8 Packaging.

KEY SPECS (details in PLAN.md):
- Schema: §5 (migrations/001_init.sql verbatim as the starting point).
- R engine: §6.2 milestone table (M1 +1R sell 25%, stop −0.5R; M2 +2R stop to entry; M3 +3R sell 25%;
  M4 +6R sell 25%, stop +3R; then trail: at +3kR stop to +3(k−1)R). High-water mark = max(daily highs since entry,
  last). Tranche q = floor(N/4), runner = N − 3q.
- Stats: §6.3, with the closed lot as the trade unit. Profit factor shows "—" when there are no losses. Never show
  #DIV/0!.
- Golden test §6.5 MUST pass: R 2.92, 1R 46.10, M1 stop 41.72, lot R 1.05 and 2.00, expectancy 1.52,
  StDev 0.67, R/StDev 2.27.
- UI: §7 (main window, KPI strip, expandable positions QTreeView with a custom QAbstractItemModel, amber stop pill,
  cyan target pill, detail pane with a PyQtGraph candle chart and R-ladder lines, dashboard charts, New Trade
  dialog with live risk panel + suggested shares + max-amount warning).
- Market data: §8 (MarketDataProvider protocol; yfinance default; manual overrides; refresh on startup and F5;
  dividends proposed for user review).
- Alerts: §9 (pure engine, dedup in alerts_log, in-app + desktop notifications).
- Backups: §10 (sqlite backup API to a configurable folder, rotation of 30, restore with safety backup, JSON
  portfolio export/import as a new portfolio).

QUALITY BAR: this must look and feel like a polished, modern finance app, not a default Qt app. Follow
DESIGN_SYSTEM.md precisely: dark surfaces, tabular monospaced numbers right-aligned, generous but compact spacing,
clear hierarchy, color used only for meaning (P/L sign, stop, target, warnings). Provide empty, loading, stale and
error states. Keyboard shortcuts per §7.7.

Start with Phase 1. After each phase, summarize what was built, how to run it, and the test results, then continue
with the next phase.
```
