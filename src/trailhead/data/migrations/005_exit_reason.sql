-- Why a SELL happened: 'STOP' for a stop-out, NULL for a normal sale (profit-take, discretionary).
ALTER TABLE trades ADD COLUMN exit_reason TEXT;
-- Sells recorded with "Record stop-out" before this column existed carried the note "Stopped out".
UPDATE trades SET exit_reason = 'STOP', notes = NULL WHERE side = 'SELL' AND notes = 'Stopped out';
