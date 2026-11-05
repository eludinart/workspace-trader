ALTER TABLE runtime_meta
  ADD COLUMN IF NOT EXISTS close_requests JSONB NOT NULL DEFAULT '[]'::jsonb;
