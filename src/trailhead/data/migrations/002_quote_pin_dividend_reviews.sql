-- Manual price overrides can be pinned so a fetched quote never replaces them (PLAN §7.6 Price override).
ALTER TABLE quotes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0;

-- Dividend proposals the user dismissed (PLAN §8: pending dividends are proposed for review).
CREATE TABLE dividend_reviews (
  portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
  symbol TEXT NOT NULL,
  ex_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('DISMISSED')),
  reviewed_at TEXT NOT NULL,
  PRIMARY KEY (portfolio_id, symbol, ex_date)
);
