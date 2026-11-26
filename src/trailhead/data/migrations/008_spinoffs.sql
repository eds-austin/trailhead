-- BUY lot created by a spin-off: the parent symbol it came from (e.g. FDXF from FDX).
ALTER TABLE trades ADD COLUMN spinoff_of TEXT;
-- Spin-offs recorded per portfolio. cost_pct: fraction of the parent's cost basis moved to the new company.
-- price_factor: the data provider's adjustment of the parent's earlier prices and dividends (FDX 1.241), undone
-- for this portfolio's figures.
CREATE TABLE spinoffs (
  portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
  parent TEXT NOT NULL,
  child TEXT NOT NULL,
  ex_date TEXT NOT NULL,
  cost_pct NUMERIC NOT NULL CHECK (cost_pct > 0 AND cost_pct < 1),
  price_factor NUMERIC,
  child_trade_id INTEGER REFERENCES trades(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (portfolio_id, parent, ex_date)
);
