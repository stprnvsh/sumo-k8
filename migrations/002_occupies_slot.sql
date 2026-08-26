-- Track whether a job row holds a real concurrency slot (defense against ghost PENDING/RUNNING).
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS occupies_slot BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE jobs SET occupies_slot = TRUE WHERE status IN ('PENDING', 'RUNNING');
UPDATE jobs SET occupies_slot = FALSE WHERE status NOT IN ('PENDING', 'RUNNING');
