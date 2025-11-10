-- SUMO-K8 Database Schema

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE tenants (
  tenant_id TEXT PRIMARY KEY,
  namespace TEXT UNIQUE NOT NULL,
  api_key TEXT UNIQUE NOT NULL,
  max_cpu INT DEFAULT 10 CHECK (max_cpu > 0 AND max_cpu <= 100),
  max_memory_gi INT DEFAULT 20 CHECK (max_memory_gi > 0 AND max_memory_gi <= 500),
  max_concurrent_jobs INT DEFAULT 2 CHECK (max_concurrent_jobs > 0 AND max_concurrent_jobs <= 50),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_tenants_api_key ON tenants(api_key);

CREATE TABLE jobs (
  job_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  k8s_job_name TEXT NOT NULL,
  k8s_namespace TEXT NOT NULL,
  status TEXT DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')),
  submitted_at TIMESTAMPTZ DEFAULT NOW(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  scenario_data JSONB,
  cpu_request INT CHECK (cpu_request > 0 AND cpu_request <= 32),
  memory_gi INT CHECK (memory_gi > 0 AND memory_gi <= 128),
  result_location TEXT,
  result_files JSONB
);

CREATE INDEX idx_jobs_tenant_status ON jobs(tenant_id, status);
CREATE INDEX idx_jobs_submitted_at ON jobs(submitted_at DESC);
CREATE INDEX idx_jobs_k8s_job_name ON jobs(k8s_job_name, k8s_namespace);

-- Sample tenants for testing
INSERT INTO tenants (tenant_id, namespace, api_key, max_cpu, max_memory_gi, max_concurrent_jobs) VALUES
  ('city-a', 'city-a', 'key-city-a-12345', 10, 20, 2),
  ('city-b', 'city-b', 'key-city-b-67890', 5, 10, 1)
ON CONFLICT (tenant_id) DO NOTHING;

