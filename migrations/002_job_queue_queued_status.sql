-- Run once on existing DBs: allow QUEUED status and default new rows to QUEUED
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_status_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_status_check
  CHECK (status IN ('QUEUED', 'PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED'));
