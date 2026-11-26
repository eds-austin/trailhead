# Trailhead

*Trade the plan.* (Formerly Portfolio Tracker.)

A desktop app for tracking portfolios (PySide6 / Qt Widgets). Fedora KDE first, portable to Windows.
Two portfolio types: **Stock Trading** (R multiples) and **Long-term Holdings** (buy-and-hold stocks, ETFs,
income investments and precious metals).

- Spreadsheet-style positions table: one row per position, expandable to its trades and stop changes
- KPI strip: value, cash, P/L, open risk, deployed vs max, win %, profit factor, expectancy, R / StDev
- Per-lot R ladder (25 % tranches at 1R / 3R / 6R, trailing stop every 3R) with suggested actions you confirm
- Prices, sectors, earnings dates and dividends from Yahoo Finance (`yfinance`), manual overrides, offline mode
- Candle chart with entry / stop / target / R-ladder lines, equity curve, R histogram, allocation charts
- Alerts (stop hit, near stop, target reached, earnings soon) in-app and as desktop notifications
- Long-term Holdings: investment type per buy, total return, dividends, yield on cost, estimated income,
  next dividend date, optional stop / target, dividends confirmed with an optional reinvestment (DRIP)
- Automatic SQLite backups (e.g. to a NAS folder), restore, JSON export/import, legacy spreadsheet CSV import

Specs: [docs/PLAN.md](docs/PLAN.md) · design system: [docs/DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md) ·
implementation notes: [docs/DECISIONS.md](docs/DECISIONS.md).

## Run (development)

```bash
uv sync                      # Python 3.13 is managed by uv (.python-version)
uv run trailhead             # data: ~/.local/share/Trailhead/portfolio.db
uv run trailhead --db /tmp/test.db   # use another database
uv run trailhead --gallery           # component gallery (debug)
```

The app was renamed from Portfolio Tracker. On first start it moves `~/.local/share/PortfolioTracker`,
the theme cache and `~/Documents/PortfolioTracker Backups` to their Trailhead names (only when the new
folder doesn't exist yet). JSON exports made under the old name still import.

First steps: **＋ New portfolio** → **Cash** (deposit) → **＋ New Trade**. Press **F5** to refresh prices.

## Test & lint

```bash
uv run pytest
uv run ruff check .
```

Tests run offline (fake / manual market provider) and never touch your real data or backup folder.

## Package

**Flatpak (Fedora / any Linux)**

```bash
flatpak install --user flathub org.kde.Sdk//6.11 io.qt.PySide.BaseApp//6.11 org.flatpak.Builder
flatpak run org.flatpak.Builder --user --install --force-clean build-dir \
    packaging/flatpak/io.github.trailhead.Trailhead.yml
flatpak run io.github.trailhead.Trailhead
```

After changing dependencies, regenerate the pinned wheel list:
`uv run python packaging/flatpak/gen_python_deps.py`.

**Windows (.exe, one folder)** — on Windows:

```powershell
uv sync
uv run --with pyinstaller pyinstaller packaging/pyinstaller/trailhead.spec --noconfirm
# → dist\Trailhead\Trailhead.exe
```

## Keyboard

Ctrl+N new trade · Ctrl+D cash · F5 refresh · Ctrl+F search · Enter/Space expand row · Ctrl+E edit ·
Del delete · Ctrl+B backup now · Ctrl+J detail pane · Ctrl+1…9 switch portfolio · Ctrl+0 dashboard · Ctrl+\\ collapse sidebar ·
B / S switch BUY / SELL in the trade dialog.

## Licenses

Fonts: Inter and JetBrains Mono (SIL OFL 1.1). Icons: Lucide (ISC). Market data via the unofficial `yfinance`
package; Yahoo Finance data is for personal use.

## License

Trailhead is free software: you can redistribute it and/or modify it under the terms of the
GNU General Public License, version 3 or (at your option) any later version. See [LICENSE](LICENSE).
The bundled Inter and JetBrains Mono fonts are under the SIL Open Font License 1.1, and the bundled
Lucide icons under the ISC license.
