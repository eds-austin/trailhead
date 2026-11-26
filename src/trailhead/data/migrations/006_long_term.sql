-- Long-term Holdings portfolio type.
-- BUY only: investment type (Equity, ETF, Income, Precious metals, Other) and an optional profit target.
ALTER TABLE trades ADD COLUMN asset_type TEXT;
ALTER TABLE trades ADD COLUMN target_price NUMERIC;
-- BUY bought with a reinvested dividend (DRIP): 1, else 0.
ALTER TABLE trades ADD COLUMN drip INTEGER NOT NULL DEFAULT 0;
-- Upcoming dividend dates from the data provider.
ALTER TABLE instruments ADD COLUMN ex_div_date TEXT;
ALTER TABLE instruments ADD COLUMN div_pay_date TEXT;
