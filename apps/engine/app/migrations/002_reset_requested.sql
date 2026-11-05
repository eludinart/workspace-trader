ALTER TABLE runtime_meta ADD COLUMN reset_requested BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE runtime_meta ADD COLUMN reset_balance DOUBLE PRECISION;
