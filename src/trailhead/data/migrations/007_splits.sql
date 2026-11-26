-- Stock splits reported by the data provider (ratio = new shares per old share: 50 for 50-for-1, 0.1 for 1-for-10).
CREATE TABLE market_splits (
  symbol TEXT NOT NULL, ex_date TEXT NOT NULL, ratio NUMERIC NOT NULL, source TEXT NOT NULL,
  PRIMARY KEY (symbol, ex_date)
);
-- Splits recorded per portfolio. APPLIED: earlier trades are shown in post-split shares (stored trades keep the
-- values as typed). IGNORED: the user's numbers already include it.
CREATE TABLE splits (
  portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
  symbol TEXT NOT NULL,
  ex_date TEXT NOT NULL,
  ratio NUMERIC NOT NULL CHECK (ratio > 0),
  status TEXT NOT NULL CHECK (status IN ('APPLIED','IGNORED')),
  created_at TEXT NOT NULL,
  PRIMARY KEY (portfolio_id, symbol, ex_date)
);
