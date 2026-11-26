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
INSERT INTO schema_version (version) VALUES (1);
