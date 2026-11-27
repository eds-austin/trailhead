# Implementation decisions

Choices made where PLAN.md / DESIGN_SYSTEM.md were silent or ambiguous.

## Phase 1 — Scaffold & design system
- **Python 3.13 via uv** (`.python-version`). The system Python is 3.14; 3.13 has the broadest wheel coverage for
  PySide6 / pyqtgraph / pandas. `requires-python` stays `>=3.12`.
- **Icons:** Lucide (`lucide-static@0.460.0`, ISC/MIT), bundled under `ui/theme/icons/` and tinted at runtime by
  replacing `currentColor` and setting stroke width 1.75.
- **Fonts:** static TTFs (Regular/Medium/SemiBold/Bold) of Inter 4.1 and JetBrains Mono 2.304, with their OFL files.
- **No global font-size in QSS:** a QSS `font-size` on `QWidget` overrides `setFont()`, so the base size comes from
  `QApplication.setFont(theme.font("body"))` and QSS only sets sizes for specific roles.
- **QSS `url()` icons** (combo arrows, checkbox tick) are written as tinted SVGs to the platformdirs cache folder.
- **KPI strip wrapping:** equal-width columns with balanced rows (10 cards → 5+5 instead of 7+3).
- **Ruff:** line length 120; `qss.py` is exempt from E501.

## Phase 2 — Core & data
- **`core/snapshot.py`** (not in the PLAN tree) holds the market-aware evaluation: lot/position views, open risk,
  actions and portfolio totals. `positions.py` stays market-free (trades → lots → positions only).
- **No quote yet:** valuation falls back to the lot's entry price ("mark"), so portfolio value equals cost basis and
  unrealized P/L / R show "—" until a price exists.
- **Position stop distance in R** uses the R of the lot that owns the highest stop; the target distance uses the R
  of the lot that owns the lowest next target.
- **Position "R now"** = Σ unrealized P/L / Σ(remaining × R) of the open lots.
- **STOP_HIT on a position row** aggregates the remaining shares of every lot whose stop is hit.
- **Action priority:** STOP_HIT > SELL_TRANCHE > RAISE_STOP.
- **Dividends belong to a position** when their ex-date (or event date if there is no ex-date) falls between the
  position's open and close dates.
- **Stats rounding:** values are kept exact (Decimal) and only rounded for display; expectancy uses only lots with
  a defined R.
- **Decimal storage:** Decimals are bound as TEXT; SQLite's NUMERIC affinity stores them as REAL/INTEGER and they
  are read back through `str()` so cents-precision values round-trip exactly.
- **Migration 002** adds `quotes.pinned` (pinned manual overrides) and `dividend_reviews` (dismissed dividend
  proposals). `alerts_log`'s UNIQUE constraint does not deduplicate NULL `lot_id`s, so `AlertRepo.insert_if_new`
  checks with `IS` comparisons first.
- **SELL validation** lives in `TradeRepo` (no oversell, no sell before its lot's BUY, symbol must match the lot).
  `add_many` inserts a split sell atomically.

## Phase 3 — Trade entry & table
- **Child rows** (trades and stop changes) span the full row and are painted as a fixed-width segment line
  (date · ▲BUY/▼SELL · #id · shares @ price · stop / lot · R/risk or P/L/R · strategy · notes), matching the §7.1
  mock, instead of reusing the parent columns.
- **"Open" filter** = open positions plus positions closed in the current month (the §7.3 default).
- **Row click** toggles expansion after the double-click interval so a double-click can open the detail pane (parent)
  or edit the trade (child) without toggling twice.
- **Numeric inputs** use a validated `QLineEdit` (`NumberEdit`) rather than `QDoubleSpinBox`, so fields can be blank
  and show placeholders; it still accepts typed input only, uses the mono font and selects all on focus.
- **Warnings don't block:** when sizing warnings exist, the primary button reads "Save anyway". STOP_INVALID blocks.
- **Split sells** show the FIFO split plan in the dialog with a "Split across lots" checkbox (checked by default).
  Editing an existing SELL never splits.
- **Cash dialog** stays open after adding an entry and shows the ledger with delete buttons.
- **Stop dialog / price override** were added now (small) since the context menu needs them; `Set stop…` records
  `LADDER_CONFIRMED` when the value equals the ladder stop, else `MANUAL`.
- **UI state** kept in settings: `ui.column_state.<pid>` (header state incl. sort), `ui.tab.<pid>` (period, stats unit,
  filter, splitter), `ui.last_page`, `ui.window_geometry`, `ui.table_density`.
- **Shortcuts** use window context (not application) so they don't fire while a modal dialog is open.

## Phase 4 — Market data
- **Providers return `list[Bar]`** instead of a pandas DataFrame from `history()`, so the service and repos stay
  pandas-free; yfinance's frames are converted inside `YFinanceProvider`. Provider prices are rounded to 4 decimals.
- **Sectors are mapped to S&P (GICS) names** (Yahoo "Technology" → "Information Technology", etc.).
- **Dividend yield** = `dividendRate / currentPrice` when available (Yahoo's `dividendYield` field changed units
  between versions), else `trailingAnnualDividendYield`.
- **History start** = min(earliest entry date of the position, 1 year back), so the high-water mark covers the whole
  holding period; afterwards it is incremental, and today's partial bar is re-fetched on each refresh.
- **Profile once per day** is keyed on `instruments.updated_at`; **dividends once per day** on the
  `market.div_checked` setting.
- **Refresh status** is stored in the `market.status` setting (last attempt, last success, errors). The chip is
  green when the last success is ≤ 1 trading day old, amber when stale or when the latest attempt failed.
- **Timeouts:** yfinance calls pass `timeout=10`; each provider call is retried once (`with_retry`).
- **Worker threads open their own SQLite connection** (WAL allows concurrent readers/writers); the UI connection
  re-reads after `market_updated`.
- **"Manual prices only"** provider option for fully offline use.
- **Settings dialog** was implemented in this phase (all keys from PLAN §5 plus `alerts.open_risk_warn_pct`).

## Phase 5 — Strategy UX & alerts
- **Inline action buttons:** hovering a row replaces the Action badge with **Confirm stop** (amber, when a
  RAISE_STOP is due) and **Record sell** (when SELL_TRANCHE or STOP_HIT is due). Clicks are handled in the
  delegate's `editorEvent` and do not toggle row expansion.
- **Confirm stop** writes one `LADDER_CONFIRMED` stop change per lot with a RAISE_STOP action, dated today.
- **Record sell** pre-fills lot, suggested shares (tranche, or all remaining shares of hit lots) and last price.
- **TARGET_REACHED** fires when a lot's highest reached milestone (≥ M1) still has a pending sell or stop raise; its
  level is that milestone's trigger price, so it fires once per milestone.
- **NEAR_STOP** is not raised when STOP_HIT already applies to the lot.
- **EARNINGS_SOON** has no lot; its level is the earnings date encoded as `yyyymmdd`, so it fires once per date.
- **Alert evaluation** runs (debounced 250 ms) after market refreshes, data changes and settings changes, for every
  non-archived stock-trading portfolio. New alerts: toast (first 3), desktop notification (first 5, if enabled),
  bell count, and the row already shows the STOP HIT / action accent.
- **Notifications** run on a daemon thread via `DesktopNotifierSync`; if that fails, a `QSystemTrayIcon` balloon is
  shown from the GUI thread.
- **Alerts drawer** lists unacknowledged alerts (toggle to include acknowledged), grouped by severity; clicking an
  alert selects that position.
- `alerts/engine.py` imports `ui/format.py` for message text; that module is pure Python (no Qt).

## Phase 6 — Dashboard & charts
- **`core/equity.py`** rebuilds the daily equity curve: cash(d) + Σ open shares(d) × close(d), carrying the last
  close forward, valuing at entry before any close exists, and using the live quote on the last day. Weekends are
  skipped. Deposits/withdrawals form the step line.
- **Candle x-axis is a bar index** (no weekend gaps) with date tick labels. Stop and target lines count toward the
  y auto-range so they are always visible; the R-ladder lines do not.
- **The R ladder on the chart** shows −1R, −0.5R, 0, 1R, 2R, 3R, 6R and continues to the next milestone (9R, 12R …)
  for the lot that owns the position's stop.
- **Detail pane** opens on double-click (or Ctrl+J) in the bottom splitter and remembers its size. Missing history
  is fetched in the background (`MarketService.fetch_history`) and the chart redraws when it arrives.
- **Ladder cards** list milestones M1 → next target (✓ reached, ◆ next, ○ pending), sold vs expected shares, the
  ladder stop, and Confirm / Record sell buttons.
- **Sector donut** is painted with QPainter (PyQtGraph has no pie item); it uses only the categorical palette.
- **Dashboard** has a portfolio selector (stock-trading portfolios) and its own period filter; with more than one
  portfolio a cross-portfolio total is shown via each type's `summary_metrics()`.
- **pyqtgraph 0.14** crashes on `BarGraphItem` with empty `pens`/`brushes` lists; empty data is set without them.

## Phase 7 — Dividends, backup & import
- **Dividend eligibility:** shares held at the end of the day before the ex-date (`core/dividends.py`); future
  ex-dates are not proposed. A proposal is resolved when a DIVIDEND cash event links to it (`symbol` +
  `dividend_ex_date`) or it is dismissed (`dividend_reviews`, migration 002).
- **Accepted dividends** are dated on the pay date when known and not in the future, else the ex-date. The amount
  is editable before accepting. The Data menu shows the pending count; a toast announces new proposals after a
  refresh.
- **Automatic backup:** on close and at most once per day while running (hourly check, `backup.last_auto`).
  An unavailable folder (e.g. NAS not mounted) gives a warning toast and never blocks.
- **Restore:** validate (integrity, expected tables, schema version) → safety backup into
  `<backup dir>/pre-restore/` → copy the backup into a staged file with the SQLite backup API → close → remove
  `-wal`/`-shm` → swap → reopen (migrations run) → rebuild every page in place (no restart).
- **JSON import** runs in one explicit transaction and inserts BUYs before SELLs so lot ids can be remapped;
  any error rolls the whole import back. Name clashes become "Name (imported)", then "(imported 2)", …
  Instruments are only added when unknown locally.
- **Legacy CSV import** targets the currently open portfolio; dates accept ISO and US formats; rows with spreadsheet
  errors, missing shares/price or an invalid stop are skipped and listed in the result dialog.
- **Tests never touch the network or the real Documents folder:** the UI fixture forces the manual provider and a
  temp backup folder.

## Phase 8 — Packaging
- **App id** `io.github.trailhead.Trailhead` is a placeholder reverse-DNS id; change it (desktop file,
  metainfo, icon names, manifest, `setDesktopFileName`) once the project has a real home page / repository.
  The metainfo also lacks a homepage URL and developer info (AppStream reports these as warning/info).
- **Flatpak:** `org.kde.Platform//6.11` + `io.qt.PySide.BaseApp//6.11` (PySide6 6.11 from the base; Python 3.13
  from freedesktop 25.08). Python dependencies are pinned wheels with sha256 in `python3-deps.json`, generated
  from `uv export` by `gen_python_deps.py` (wheels for x86_64 and aarch64; hatchling included so the app installs
  offline). Permissions: network (prices), `--filesystem=home` (backup folder / NAS under home, import/export),
  notifications, Wayland with X11 fallback.
- **Not verified here:** `flatpak-builder` and the KDE 6.11 SDK are not installed on the dev machine, so the
  Flatpak manifest was written and its desktop/metainfo files validated, but not built.
- **PyInstaller:** one-folder spec; excludes unused Qt modules and pyqtgraph examples. Smoke-built and launched on
  Linux (≈ 336 MB, dominated by curl_cffi, libpython, ICU and OpenBLAS). The Windows build must be run on Windows.
- **Icon:** candles between an amber stop line and a cyan target line, from the design tokens; SVG plus PNG sizes and
  a Windows `.ico`.

## Changes after release
- **No available-cash check on trades (2026-11-20, user request).** Entering a BUY never requires or warns about
  available cash; `EXCEEDS_CASH` was removed from `core/sizing.py` and PLAN §6.4. (Superseded below: cash no
  longer goes negative.)
- **Level pills show price and % distance only (2026-11-20, user request);** the R distance moved to the tooltip
  context and the Stop / Next target columns were narrowed to 142 px.
- **Trade rows are column-aligned (2026-11-20, user request),** replacing the spanned free-form line from phase 3.
  Each value sits under the heading it belongs to and other cells stay blank: BUY → date, ▲ BUY #id, strategy/TF,
  remaining/original shares, entry (Avg entry), current stop, lot R, lot unrealized P/L, lot open risk, days;
  SELL → date, ▼ SELL #id, lot #, shares, exit price (Last), realized R (R now), realized P/L; stop changes →
  date, ■ STOP, lot #, new stop, reason (Action). Notes appear in the Action column; R/share and initial risk are in
  the BUY row tooltip. Saved column layouts get the new default widths once (`ui.columns_rev.<pid>`).
- **Per-stock trade numbers (2026-11-20, user request).** BUYs and SELLs are numbered separately for each symbol
  (BUY #1, #2 …; SELL #1, #2 …) in trade-date order, ties by entry order, across all positions of that symbol in the
  portfolio. "lot #n" is the BUY number. Numbers are derived (`trade_numbers()` in `core/positions.py`), never
  stored, so a back-dated trade renumbers later ones. Database ids remain internal.
- **No cash in a Stock Trading portfolio (2026-11-20, user request; replaces the short-lived "cash never negative"
  ledger).** A stock-trading portfolio tracks positions that may be spread across accounts, not an account balance.
  Portfolio value = market value of open positions; % of portfolio is each position's share of that. There are no
  deposits/withdrawals: the Cash button and Ctrl+D are hidden via `PortfolioType.has_cash_ledger = False`
  (the cash dialog is kept for the future Cash portfolio type). Deposits already stored are ignored, not deleted.
  Dividends are still recorded per stock (Dividends KPI card, position Total P/L) so they can later be routed to a
  Cash portfolio. The dashboard's equity curve became a **cumulative P/L curve** (realized + unrealized at daily
  closes + dividends, rebased to the period start).
- **Dividends confirmed from the Action cell (2026-11-20, user request).** Pending dividends are assigned to the
  position that held the shares the day before the ex-date and shown as a "Dividend $x" badge (info color) when no
  strategy action is due; hovering shows **Confirm div** (one click, proposed amount, dated on the pay date when
  known). Editing or dismissing stays in Data ▸ Pending dividends.
- **Stop-outs ask for the actual sell price (2026-11-20, user request).** When a stop is hit, the Action cell / lot
  card button is **Record stop-out**: a small dialog pre-filled with the stop price and today's date sells all
  remaining shares of the hit lots at the entered price (note "Stopped out"). Action column widened to 260 px.
- **Exit plans per BUY (2026-11-20, user request).** "1-3-6" (existing ladder, unchanged, default for new and existing
  lots) and "1-2-3" (¼ at 1R/2R/3R; stop → BE at 1R, 1R at 2R, 2R at 3R, then 1R below each new whole R). Stored in
  `trades.exit_plan` (migration 003; NULL = 1-3-6), exported/imported in JSON, editable on the BUY. Shown in the
  Strategy / TF column, BUY rows and lot cards. Next target = next whole R regardless of whether action is due.
- **Threading fix (2026-11-20):** `MarketService` resolves the provider (which reads settings) on the UI thread before
  submitting a job; worker threads never touch the UI thread's SQLite connection. This removed an intermittent
  segfault. Late history callbacks are ignored once the detail pane is hidden.
- **Manual sector (2026-11-21, user request).** Right-click › Edit sector… or double-click the Sector cell. The
  value is stored with `instruments.sector_manual = 1` (migration 004) and refreshes keep it; "Clear (use provider)"
  unlocks it. The refresh now also covers any traded symbol that still has no sector (even long-closed positions),
  and the trade dialog's symbol lookup saves the profile it fetched.
- **Only BUY / SELL rows under a position (2026-11-21, user request).** Stop changes are no longer child rows; each
  BUY row's tooltip lists its stop history and the context menu offers "Undo stop change". BUY rows show the shares
  bought (not remaining).
- **Stop changes back as rows, with a toggle (2026-11-26, user request; reverses the entry above).** Each stop change
  is a child row after the same day's trades: date, "▲ STOP #lot" in the stop colour, the new stop under Stop, and
  "from <previous> · ladder|manual" in italics under Strategy; its tooltip says what the new stop locks in (break-even,
  +xR) on the shares still held. View › Show stop changes (on by default) hides them again; remembered per portfolio
  in `ui.tab.<pid>.stop_rows`. Del on a stop row undoes that stop change (after confirming); the context menu's
  Record sell / Set stop target its lot. The BUY row's tooltip keeps the full stop history.
- **Stop-outs are flagged (2026-11-21, user request).** `trades.exit_reason = 'STOP'` (migration 005, backfilled from
  the earlier "Stopped out" note). Set by Record stop-out, or by the SELL dialog's "Stop-out" checkbox, which ticks
  itself when the price is at or below the lot's current stop (until the user changes it). Shown as a red STOP-OUT
  badge in the SELL row's Status cell.
- **Dashboard redesign (2026-11-23, user request).** Chosen from three mockups: a summary band (total value of all
  portfolios, today's change, period P/L, period income) with a short cumulative P/L chart (all portfolios solid,
  each portfolio dashed when there are several); a Portfolios table (Value, Share, Today, P/L, Unrealized, Income,
  Open risk, Trend; Total row when there are several; double-click/Enter opens the portfolio); and a right rail with
  **Today** (5 biggest % moves across all holdings; needs attention: stop hits, sells and stop raises due, open risk
  over the limit, missing prices, earnings in the next 60 days (widened from 30 on 2026-11-23), or "on plan") above **Allocation** (by portfolio or
  sector). Period is a MTD / QTD / YTD / 12M / All switch. A portfolio value view of the chart was considered and
  left out for now (cleaner with P/L only). Each type supplies a `DashboardSummary` via
  `PortfolioType.dashboard_summary()`; cross-portfolio sums live in `portfolio_types/totals.py`. The KPI strip,
  position bars and sector donut are no longer on the Dashboard; the R histogram moved to the Stock Trading page as an
  "R distribution" panel beside the KPI cards, following their Period and Lot / Position switches.
- **Performance view (2026-11-23, user request).** The Stock Trading table panel has a **Positions | Performance**
  switch. Performance compares closed trades grouped by **Strategy, Time frame or Exit plan** (one grouping at a
  time; tags come from each BUY, so lots are the natural unit; in Position mode a position whose lots differ is
  "Mixed"). Columns: Closed, Open, Win % (W–L), Avg win / Avg loss (R), Profit factor, Expectancy ± 2 standard
  errors with a range plot on a shared axis, StDev, R / StDev, P/L (closed trades only), Avg days; "(none)" row for
  untagged trades; total row last. Groups with fewer than 10 closed trades get a LOW SAMPLE badge. Default sort is
  most closed trades first; headers sort. Selecting a row points the R distribution panel at that group. It follows
  the Period and Lot / Position switches; the view and grouping are remembered per portfolio; Export CSV exports the
  performance table while it is shown. Grouping logic: `core/performance.py`.
- **Performance view: Strategy × Time frame, R strip, SQN (2026-11-26, user request).** A fourth grouping,
  **Strategy × Time frame**, lists each strategy followed by its time frames (indented, same columns; header sorts
  each level separately, total stays last). A nested row's name for selection, the histogram title and CSV is
  "Strategy · Time frame". The Range column became **R range**: top lane one dot per closed trade at its R (green
  win, red loss), bottom lane the expectancy as a diamond with its ± 2 SE range as a capped error bar (a filled bar
  with a dot read as a slider); a legend for both sits in the toolbar while the table is shown; the shared axis covers every trade
  (clamped to −4R…+10R). New **SQN** column (Van Tharp): √min(N, 100) × expectancy ÷ StDev, with Tharp's rating
  word underneath; muted under 30 trades, where Tharp says it isn't reliable. SQN is the expectancy divided by its
  standard error, so SQN > 2 ⇔ the ≈95% bar clears 0. SQN replaces the table's R / StDev column (R / StDev × √N); the
  R / StDev KPI card stays.
- **Long-term Holdings portfolio type (2026-11-24, user request).** For buy-and-hold stocks, ETFs, income
  investments (REITs, bond funds …) and precious metals. No R multiples, ladder or risk settings (the portfolio
  dialog hides Max amount / Risk %; `PortfolioType.uses_risk_settings = False`). Migration 006 adds
  `trades.asset_type` (Equity, ETF, Income, Precious metals, Other; chosen on each BUY, prefilled from the holding),
  `trades.target_price`, `trades.drip`, and `instruments.ex_div_date / div_pay_date` (from the provider's calendar).
  - **Stop and target are optional and belong to the holding.** They are stored on its open BUY lots
    (`initial_stop`, `target_price`) and set together (Set stop & target…, or the BUY dialog), so a stop may sit
    above cost (trailing a winner). `TradeRepo` only requires a stop below the price for `stock_trading`
    portfolios. Stop hit / target reached show as a status badge, a row bar, the Action cell (Record sell), the
    Dashboard and desktop alerts.
  - **Metrics** (`core/holdings.py`): total return = unrealized + realized + dividends received; total return % =
    total return ÷ money invested (every BUY except DRIP buys). Estimated annual income = shares × annual dividend
    per share (provider's forward rate, else the last 12 months of ex-dates). Yield on cost = income ÷ cost basis
    of the shares held (reinvested shares included, the brokerage convention); current yield = income ÷ value.
    Next dividend = the announced ex-date, or the pay date once ex has passed, else projected from the median gap
    of the last payments (shown with "~"). Dividend history is now fetched 400 days back for every symbol.
  - **Dividends:** pending dividends work as in Stock Trading, but **Confirm div** (and Accept… in Data ▸ Pending
    dividends) opens a dialog: amount, received-on date, and **Reinvest (DRIP)** with the shares received and the
    price (pre-filled with the close on the pay date and amount ÷ price). It records the DIVIDEND cash event plus a
    `drip` BUY dated on the pay date that inherits the holding's type, stop and target. Reinvest is pre-ticked
    for holdings that reinvested before. Dividends are child rows (◆ DIV) under the holding, before the DRIP buy
    they paid for, and can be deleted from the context menu (the DRIP row is separate).
  - **Page:** KPI cards (value, total return, unrealized, est. annual income, yield on cost, dividends received
    and realized P/L for the period, next dividend) with an Allocation by type panel; a holdings table (type,
    shares, avg cost, last, market value, unrealized, dividends, total return $ / %, yield, YoC, income / yr,
    next div, stop, target, action; sector, cost basis, realized and days are optional columns). Sells are
    split across lots FIFO automatically. No detail pane (candle chart) for now.
  - **Dashboard:** open risk is "—" for this type; the sector allocation falls back to the investment type for
    ETFs and metals; attention lists stop / target hits, dividends to confirm (one line when there are 3 or
    more) and announced ex-dividend dates in the next 14 days.
- **Symbols the data provider spells differently (2026-11-24, user request).** Yahoo writes share classes with a
  dash (BRK-B), and its search does not find BRK-B for "BRK.B", "BRK/B" or "BRKB". `symbol_variants()` turns
  those spellings into the dash form; each variant is checked with the provider's profile and, if known, listed
  first as a "Likely match", followed by Yahoo search results (equities, ETFs, funds; no options, futures or crypto).
  The picker (`SymbolPickerDialog`) also searches by company name. It appears (1) in the trade dialogs: when a NEW
  symbol is unknown a "Find symbol…" link shows, and saving a BUY pops the picker once ("Keep BRK.B" saves it as
  typed); (2) for existing positions: right-click ▸ Change symbol…, and on a Long-term holding with no price the
  Action cell shows "No price" with a Find symbol button (not in manual mode). Choosing renames the symbol in
  trades, dividends, dividend reviews and alerts in every portfolio (`TradeRepo.rename_symbol`; a manual sector
  moves with it) and starts a price refresh.
- **Stock splits (2026-11-24, user request).** Stored trades keep the values as typed. Splits are recorded per
  portfolio (migration 007, table `splits`) as APPLIED (earlier trades are shown and computed in post-split
  shares: shares × factor, price / stop / target ÷ factor, cost unchanged; `core/splits.py`) or IGNORED (the user
  entered them split-adjusted). Adjustment happens when a portfolio is loaded (both types, pending dividends,
  P/L curve), so it can be undone; the data provider's prices and per-share dividends are split-adjusted, which
  the adjusted trades now match. `TradeRepo` validates sells in post-split shares. Yahoo's split history
  (`market_splits`) is fetched daily per symbol and a split on which shares were held is proposed, never applied
  silently: Data ▸ Stock splits (count in the menu, toast when new), a "Split 50-for-1?" action on Long-term
  rows, right-click ▸ Stock splits…. Ratios that aren't whole (FDX 1.241, GE 1.253) are Yahoo's way of recording
  spin-offs: they are flagged "probably a spin-off" with Already in my numbers as the default. Editing a trade
  opens it as typed. Long-term holdings show applied splits as ⇄ SPLIT child rows; trade tooltips say "Entered as
  …". The Stock Trading trade dialog does not convert a back-dated sell typed in pre-split shares (the repo still
  refuses an oversell); the Long-term dialog does.
- **Spin-offs (2026-11-24, user request; e.g. FDX → FDXF on 2026-06-01).** Long-term Holdings: right-click ▸ Record
  spin-off…, the "Spin-off Jun 1?" action, or Record spin-off… in Data ▸ Stock splits. The user enters the new
  symbol (same portfolio), the shares received and the % of cost moved, pre-filled from Yahoo's price factor
  (1 − 1/1.241 = 19.42%; the user chose this default over entering Form 8937 figures). Stored: a `spinoffs` row
  (migration 008) and the new company's shares as a real BUY lot (`trades.spinoff_of`) dated on the ex-date with
  the moved cost, so it can be sold. Derived at load: parent lots held the day before get a per-share basis cut
  (`Lot.basis_cuts`; `Lot.entry_at(d)` so sells before the ex-date keep the full cost); invested moves with the
  cost, so total value / return don't jump and invested is unchanged. Yahoo records spin-offs as odd-ratio
  'splits' and scales the parent's earlier closes and per-share dividends by that factor: recording the spin-off
  marks the 'split' done and undoes the scaling for that portfolio (pending FDX dividends from before June are
  proposed at the real $1.45, not 1.1684; the Dashboard curve uses unscaled closes and moves cost on the ex-date).
  Shown as a ⑂ SPIN-OFF row under the parent and ▲ SPIN-OFF on the new lot; undo from the row's context menu
  (refused once the new shares have sells). Cash in lieu of fractional shares is not modelled. Exported/imported
  in JSON. Not offered in Stock Trading portfolios (their R math would need it per lot).
- **Cash portfolio type (2026-11-26, user request).** Each row is a cash account or pool of cash: **Savings** (APY
  entered by the user) or **Money market fund** (symbol; 1 share = $1; its yield is Yahoo's `yield`, refreshed
  with prices, unless "Enter the yield myself" is ticked). No checking type (user's choice). Migration 009 adds
  `cash_accounts`, `cash_rates` (dated rate history, source MANUAL or the provider), `cash_txns` and `cash_links`;
  logic in `core/cash.py`, the type in `portfolio_types/cash/`. Yahoo has no dividend history for money market
  funds (checked: SPAXX, VMFXX, SWVXX, FDRXX), so funds accrue like savings at their yield.
  - **Opening:** an account is opened with today's balance (an OPENING entry; user's choice over entering history).
  - **Moving money:** deposit, withdrawal, transfer between accounts (stored as a linked pair; deleting one side
    deletes both; deleting an account turns the other side into a plain deposit / withdrawal). Header button
    **Move Money** (Ctrl+D) and **New Account** (Ctrl+N) replace Cash / New Trade on this type's page
    (`PortfolioType.cash_label`, `entry_label`).
  - **Interest is proposed, never posted** (user's choice). Interest is paid monthly on the account's payout day
    (default: month end). Expected interest = daily compounding at `(1+APY)^(1/365)−1` on each day's end-of-day
    balance, using the rate in effect that day; the first period starts on the opening day. Each payout date with
    no INTEREST entry shows as "Interest Oct 31 ~$81.40" with a Confirm interest button; the dialog takes the
    statement amount and date, or **Skip this month** (records $0 so it isn't proposed again).
  - **True up:** enter the bank's balance on a date; the app shows its own balance then and the difference, booked
    as **Interest** (default when positive) or **Adjustment**, and stores the stated balance on that entry
    (`true_up_balance`; a $0 adjustment when it matched). A true-up settles every payout period ending on or
    before its date (the difference includes interest the bank had paid). Accounts not trued up (or opened) in 30
    days show "True up due" and a Dashboard line; a later edit before the true-up date shows the drift in the
    Last true-up tooltip.
  - **Page:** KPI cards (total cash, weighted APY with the estimated yearly interest, interest earned and yield
    earned in the period, net flows, interest to confirm) and an accounts table (type, institution · symbol,
    linked portfolios, APY, change over the period, balance, interest, yield earned = interest ÷ average daily
    balance annualized, last true-up, action) whose child rows are the ledger, newest first, with the running
    balance. Right-click: Move money, Confirm interest, True up, Change rate (dated), Edit / Delete account.
    Closed accounts are kept (hidden unless All; no interest proposed).
  - **Linking (Stock Trading and Long-term).** From the Cash page: **Link portfolio** (toolbar), right-click ▸
    Link portfolio…, or double-click the Linked to cell (added 2026-11-26 because the portfolio-settings route
    wasn't discoverable); it lists current links with Unlink. The portfolio dialog's **Settles in** also picks an
    account (any Cash portfolio) and a **From** date. From that date its sells (+proceeds), buys (−cost) and confirmed cash
    dividends (+) are **derived from the trades at load time, not copied** into the ledger, so editing a trade
    fixes the balance; they appear in the ledger tagged with the portfolio's name and are edited there. A
    dividend fully reinvested (DRIP buy of the same cost on its date) and spin-off shares move no cash; a partial
    reinvestment shows both lines and the remainder settles. One account can serve several
    portfolios. The cash balance is **not** part of Deployed / Max; the linked portfolio's page shows a "Cash ·
    Schwab sweep $10,000" chip (click opens the Cash page). A buy larger than the account's balance shows a
    warning in the trade dialog but is never blocked (keeps "no cash check on trades").
  - **Dashboard:** value = total balance; P/L and income = interest received in the period (stock dividends stay
    in their own portfolio, so nothing is counted twice); attention lists interest to confirm, true-ups due and
    balances below zero. JSON export/import includes accounts, rates and the ledger (links are not exported; a
    transfer to another portfolio's account becomes a deposit / withdrawal).
- **Dashboard layout (2026-11-26, user request).** The right rail (fixed 340 px; it clipped at the window edge and
  left empty space under a short portfolio table) is gone. The Portfolios table spans the full width, and under
  it three equal panels share a row: **Today's biggest moves**, **Needs attention** (coming up, all portfolios)
  and **Allocation** (by portfolio or sector).
- **Bullion in ounces (2026-11-26, user request: "just an oz unit tied to the metal's price").** In the
  Long-term trade dialog a **Held as: Ticker symbol | Bullion (oz)** row, shown only when the investment type is
  Precious metals (and on a sell when bullion is held; user request the same day); Bullion picks Gold, Silver, Platinum or
  Palladium and takes **Ounces** (troy oz of pure metal, worked out by the user) and **Price / oz** (premium
  included). Stored as an ordinary lot whose symbol is the metal's Yahoo front-month futures symbol (GC=F, SI=F,
  PL=F, PA=F; close to spot, priced by the normal refresh, no dividends or splits), type Precious metals; no
  migration (`core/metals.py`). Shown as "Gold" with "5 oz" in the table, Dashboard and toasts; search matches the
  metal name; Find symbol, spin-off, dividends, splits, Change symbol and Edit sector are not offered for metals.
  No coin / bar presets or premium tracking.
- **Discord alerts (2026-11-26, user request).** Alerts can also go to a Discord channel through a webhook, so
  they reach the phone. The user picks which **categories** are sent; target alerts split into "sale suggested"
  (a tranche is due, or a Long-term holding hit its target) and "raise stop only", so the default can be as
  narrow as Stop hit + Target: sale suggested. Sent once per alert, from the same dedup as the bell, and only
  while the app runs (a headless `--check-alerts` for a timer is still a possible follow-up). No new dependency
  (stdlib `urllib`); `post()` refuses anything that isn't a Discord webhook URL; messages never @mention. The URL
  is stored in the local settings table like other settings and shown masked.
- **Hover price peek (2026-11-27, user request: a chart of the price against the stop and next target without
  cluttering the table).** Resting the mouse on a holding's **Symbol** cell for 450 ms (Stock Trading and
  Long-term) shows a read-only card beside the row: symbol, name, last and day %, about 3 months of daily candles
  (`CandleChart(compact=True)`: no volume, crosshair, readout or R ladder) with the STOP and TGT pills and the
  entry (Avg / Avg cost) line, and a footer in words: "−4.2% to stop", "+8.9% to 2R target", "Stop hit · …",
  "Target reached", "No stop set". Only the Symbol cell triggers it, so moving across the table doesn't flash
  cards; once one is showing, moving to another symbol switches at once. It hides on leaving the cell, clicking,
  scrolling, typing, a refresh or the window losing focus, and replaces the symbol cell's tooltip (the name is in
  its header). Bars come from local `price_history`; a symbol missing them gets one background fetch per session.
  Closed positions show the chart up to the close date with the entry line only. The Stock Trading card points to
  the detail pane ("double-click for the full chart"); Long-term still has no detail pane.
  Same day (user request): the peek's candles run to the right edge, with the stop / target / entry prices as tags
  on the price axis instead of labels on the lines; both candle charts draw a subtle magenta **18-day SMA**
  (new `sma` chart token, Trailhead only, so not mirrored to the house style); the peek loads a month more history
  so the average spans its whole width.
  Entry line (same day, user question "why do only some charts show the entry?"): it used to be drawn but never
  stretched the price range, so an entry outside the shown prices was silently off-chart. Now an entry within 25%
  of the shown price span (candles plus stop and target) stretches the range and is drawn; one further away (an
  old Long-term average cost, a swing trade that has run) is a tag pinned to the top or bottom-left edge,
  "▼ Avg cost 60.00 · −20.2%" (distance from the last close), so the candles are not squashed. Both charts.
  The candle item now reports `dataBounds`, so the y range fits only the bars in view (the peek's warm-up bars
  had been stretching it; the full chart now refits when panned).
- **Position size in multiples of 4 and a fresh price in the trade dialog (2026-11-27, user request).** Profits
  are taken in quarters, so the suggested size is now the most shares whose risk stays within the per-trade
  budget, **rounded down to a multiple of 4** (342 → 340); never rounded up over the budget. If the budget covers
  fewer than 4 shares the plain count is suggested and the link says "(too few to split in quarters)". Still
  offered through the **Use suggested** link (user choice), not filled in automatically. Entering a symbol in a
  new trade now also fetches its **current price** from the provider (`MarketService.fetch_quote`, stored like a
  refresh, so a held symbol's quote is updated too; a pinned manual price is kept) and fills Price / Sell price
  unless the user typed one. Once per symbol per dialog; offline or in manual mode the stored price stays.
  Follow-up (same day, user request): once the size is set (Use suggested, or Enter in Shares on a buy), focus
  moves to **Price** with the fetched quote selected, since the actual fill price is typed after the order
  executes. Enter in Shares therefore no longer saves a buy. The link reserves room for its focus ring (it was
  clipped to "Jse suggested" when focused).
