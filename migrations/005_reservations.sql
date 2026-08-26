-- Node reservations for pre-warming simulation nodes before job submission
CREATE TABLE reservations (
  reservation_id  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id       TEXT        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  cpu_request     INT         NOT NULL CHECK (cpu_request > 0 AND cpu_request <= 32),
  memory_gi       INT         NOT NULL CHECK (memory_gi > 0 AND memory_gi <= 128),
  placeholder_pod TEXT,
  namespace       TEXT,
  status          TEXT        NOT NULL DEFAULT 'PENDING_NODE'
                  CHECK (status IN ('PENDING_NODE', 'READY', 'CLAIMED', 'EXPIRED', 'FAILED')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at      TIMESTAMPTZ NOT NULL,
  claimed_job_id  UUID        REFERENCES jobs(job_id) ON DELETE SET NULL
);

CREATE INDEX idx_reservations_tenant_status ON reservations(tenant_id, status);
CREATE INDEX idx_reservations_expiry        ON reservations(expires_at)
  WHERE status IN ('PENDING_NODE', 'READY');
