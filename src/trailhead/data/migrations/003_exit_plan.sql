-- Per-BUY exit plan: '1-3-6' (default; NULL means 1-3-6) or '1-2-3'.
ALTER TABLE trades ADD COLUMN exit_plan TEXT;
