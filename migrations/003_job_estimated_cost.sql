-- Estimated compute cost (USD) from JOB_COST_* env and wall-clock (finished_at - started_at)
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS estimated_cost_usd NUMERIC(14, 6);
