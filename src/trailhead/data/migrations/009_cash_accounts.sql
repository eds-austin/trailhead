-- Cash portfolio type: each account is a savings account (APY entered by the user) or a money market fund
-- (symbol; its yield comes from the data provider unless manual_rate is set). payout_day: day of the month interest
-- is paid, 0 = last day.
CREATE TABLE cash_accounts (
  id INTEGER PRIMARY KEY,
  portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('SAVINGS','MONEY_MARKET')),
  institution TEXT,
  symbol TEXT,
  manual_rate INTEGER NOT NULL DEFAULT 0,
  payout_day INTEGER NOT NULL DEFAULT 0 CHECK (payout_day BETWEEN 0 AND 28),
  opened_on TEXT NOT NULL,
  closed INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 0,
  notes TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_cash_accounts_portfolio ON cash_accounts(portfolio_id);

-- Rate history (APY as a fraction, 0.0425 = 4.25 %), effective from a date. source: MANUAL or the provider name.
CREATE TABLE cash_rates (
  account_id INTEGER NOT NULL REFERENCES cash_accounts(id) ON DELETE CASCADE,
  effective_date TEXT NOT NULL,
  apy NUMERIC NOT NULL CHECK (apy >= 0),
  source TEXT NOT NULL DEFAULT 'MANUAL',
  PRIMARY KEY (account_id, effective_date)
);

-- Money moved by hand and interest. amount is signed (withdrawals and outgoing transfers negative).
-- TRANSFER rows come in pairs pointing at each other (transfer_id). INTEREST rows carry the payout period they
-- pay for (period_end). A true-up stores the real balance it was told (true_up_balance) on the INTEREST or
-- ADJUSTMENT row that books the difference (amount 0 when there was none).
CREATE TABLE cash_txns (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES cash_accounts(id) ON DELETE CASCADE,
  txn_date TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('OPENING','DEPOSIT','WITHDRAWAL','TRANSFER','INTEREST','ADJUSTMENT')),
  amount NUMERIC NOT NULL,
  transfer_id INTEGER,
  period_end TEXT,
  true_up_balance NUMERIC,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_cash_txns_account ON cash_txns(account_id, txn_date);

-- A Stock Trading or Long-term portfolio whose sells, buys and cash dividends settle in a cash account, from a
-- date. One cash account may serve several portfolios.
CREATE TABLE cash_links (
  portfolio_id INTEGER PRIMARY KEY REFERENCES portfolios(id) ON DELETE CASCADE,
  account_id INTEGER NOT NULL REFERENCES cash_accounts(id) ON DELETE CASCADE,
  since TEXT NOT NULL
);
