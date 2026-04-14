# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SUMO-K8 Controller — a multi-tenant FastAPI service that manages Kubernetes Jobs for running SUMO traffic simulations. It handles tenant isolation (via Kubernetes namespaces and resource quotas), job lifecycle (submit → queue → run → collect results), log streaming, and result storage (PVC or S3/GCS/Azure).

## Development Commands

### Local Setup
```bash
./setup_local.sh            # Creates local PostgreSQL DB
pip install -r requirements.txt
export DATABASE_URL="postgresql://$(whoami)@localhost/sumo_k8"
python app.py               # API at http://localhost:8000
```

### Quick Test
```bash
bash quick_test.sh          # Smoke test against running local API
bash test_job.sh            # Submit a test job
bash test_deployment.sh     # End-to-end deployment test
```

### Tests
```bash
python -m pytest tests/ -v
```

### Kubernetes Deploy (existing cluster)
```bash
docker build -t sumo-k8-controller:latest .
kubectl create namespace sumo-k8
kubectl apply -f k8s/
# See README for full steps (DB init, secrets, port-forward)
```

### Helm Deploy
```bash
helm install sumo-k8 helm/sumo-k8/
```

## Architecture

### Source Layout (`src/`)

| Module | Responsibility |
|--------|---------------|
| `config.py` | All configuration via `os.getenv` — see table below |
| `database.py` | asyncpg connection pool (`init_db_pool`, `close_db_pool`, `get_db`) |
| `auth.py` | API key creation, tenant CRUD, header-based auth (`get_tenant_from_header`) |
| `jobs.py` | Job submission, status query (`submit_job`, `get_job_status`, `get_job_logs`) |
| `k8s_client.py` | Kubernetes API wrapper — creates Jobs, ConfigMaps, namespaces, quotas |
| `reconciler.py` | Background thread: syncs K8s Job status → DB, cleans up old ConfigMaps |
| `scaling.py` | Node listing, cluster activity, namespace setup |
| `storage.py` | Result collection — auto-detects PVC / S3 / GCS / Azure based on config |
| `logs.py` | Log streaming (`stream_job_logs`) via K8s pod log API |
| `models.py` | Pydantic request/response models |

### Request Flow
```
POST /jobs
  → auth.get_tenant_from_header (API key → tenant row)
  → jobs.submit_job
      → store zip in object storage (QUEUE_S3_PREFIX) or local queue dir
      → insert job row in DB (state=queued)
  → reconciler background loop picks up queued jobs
      → k8s_client creates ConfigMap (sumocfg zip) + Job in tenant namespace
      → polls Job until complete
      → storage collects results → uploads to object storage or PVC
      → updates DB state to completed/failed
```

### Authentication
- **Tenant endpoints** (`/jobs`, `/jobs/{id}`, `/jobs/{id}/logs`): `Authorization: Bearer <api-key>`
- **Admin endpoints** (`/admin/*`): `X-Admin-Key: <admin-key>`
- Admin key is set via `ADMIN_KEY` env var — empty string disables admin endpoints.

### Key Configuration (`src/config.py`)

| Env Var | Default | Notes |
|---------|---------|-------|
| `DATABASE_URL` | — | Required. PostgreSQL DSN |
| `ADMIN_KEY` | `""` | Admin API key; empty = disabled |
| `SUMO_IMAGE` | `ghcr.io/eclipse-sumo/sumo:latest` | SUMO container image for jobs |
| `RESULT_STORAGE_TYPE` | `auto` | `auto`, `pvc`, `s3`, `gcs`, `azure` |
| `S3_BUCKET` / `GCS_BUCKET` / `AZURE_STORAGE_ACCOUNT` | — | Cloud storage target |
| `S3_REGION` | `us-east-1` | |
| `S3_IAM_ROLE_ARN` | `""` | For IRSA (EKS) |
| `MAX_FILE_SIZE_MB` | `100` | Upload cap per job |
| `MAX_JOB_DURATION_HOURS` | `24` | TTL before forced termination |
| `MAX_CONCURRENT_JOBS_PER_TENANT` | `10` | Active job cap |
| `MAX_QUEUED_JOBS_PER_TENANT` | `500` | Queue depth cap |
| `SIMULATION_NODE_SELECTOR_KEY` | `node-type` | K8s node label key |
| `SIMULATION_NODE_SELECTOR_VALUES` | `simulation` | Comma-separated allowed values |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins |
| `LOG_LEVEL` | `INFO` | |

All config is read once at import time in `config.py` — never read `os.getenv` directly in other modules.

### Kubernetes Manifests (`k8s/`)

| File | Purpose |
|------|---------|
| `namespace.yaml` | `sumo-k8` namespace |
| `deployment.yaml` | Controller deployment |
| `service.yaml` | ClusterIP service |
| `ingress.yaml` | Ingress (edit host) |
| `postgres.yaml` | In-cluster PostgreSQL (dev only) |
| `rbac.yaml` + `serviceaccount.yaml` | RBAC for Job/ConfigMap/Namespace management |
| `secret.yaml.example` | Copy to `secret.yaml`, fill DB URL + admin key |
| `karpenter-nodepool-simulation.yaml` | Karpenter NodePool for simulation nodes |

### Queue Dashboard
A secondary web UI for monitoring job queues:
- `queue-dashboard-api/` — backend (FastAPI or Express)
- `queue-dashboard-web/` — frontend
- `queue-dashboard/` — shared config / Docker Compose for the dashboard

### Storage Auto-Detection
When `RESULT_STORAGE_TYPE=auto`:
1. If `S3_BUCKET` is set → S3
2. Else if `GCS_BUCKET` is set → GCS
3. Else if `AZURE_STORAGE_ACCOUNT` is set → Azure
4. Else → PVC

## Code Conventions

- All config via `src/config.py` — never call `os.getenv` elsewhere
- Async handlers where possible (FastAPI async def)
- DB access only via `get_db()` dependency injector
- Reconciler runs as a daemon thread — do not block it with slow operations
- SUMO job ZIP must contain at least one `.sumocfg` file
