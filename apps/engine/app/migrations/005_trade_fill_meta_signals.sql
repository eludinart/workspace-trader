-- Fill meta + cost basis + signal observations (additive, no data loss).

ALTER TABLE positions
  ADD COLUMN IF NOT EXISTS cost_basis DOUBLE PRECISION;

UPDATE positions
SET cost_basis = qty * avg_entry
WHERE cost_basis IS NULL;

ALTER TABLE trades
  ADD COLUMN IF NOT EXISTS decision_ts TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS decision_price DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS latency_ms INTEGER,
  ADD COLUMN IF NOT EXISTS slip_bps DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS impact_bps DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS invested_quote DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS avg_entry_ref DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS cost_basis_closed DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS reference_ts TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS meta_complete BOOLEAN NOT NULL DEFAULT false;

-- Existing rows stay incomplete for display enrichment (no retroactive PnL invent).
UPDATE trades SET meta_complete = false WHERE meta_complete IS DISTINCT FROM true;

CREATE TABLE IF NOT EXISTS signal_observations (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  symbol TEXT NOT NULL,
  as_of TIMESTAMPTZ NOT NULL,
  ingested_at TIMESTAMPTZ NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  confidence_tier TEXT NOT NULL DEFAULT 'hypothesis',
  source TEXT NOT NULL DEFAULT 'stub',
  CONSTRAINT signal_obs_tier_chk CHECK (confidence_tier IN ('fact', 'indicator', 'hypothesis')),
  CONSTRAINT signal_obs_time_chk CHECK (as_of <= ingested_at)
);
CREATE INDEX IF NOT EXISTS signal_observations_symbol_asof_idx
  ON signal_observations (symbol, as_of DESC);
