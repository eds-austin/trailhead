-- A sector entered by the user is kept even when the data provider later returns a different one.
ALTER TABLE instruments ADD COLUMN sector_manual INTEGER NOT NULL DEFAULT 0;
