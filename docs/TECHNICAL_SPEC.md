# SUMO-K8 Quota Controller - Technical Specification

## System Overview

A minimal control plane for multi-tenant SUMO simulation orchestration on Kubernetes with automatic resource scaling.

**Core Function**: Accept authenticated simulation job submissions, enforce per-tenant quotas, create Kubernetes Jobs, and enable automatic node scaling.

---

## Features

### 1. Multi-Tenant Job Submission
- REST API endpoint for job submission
- Per-tenant API key authentication
- Dynamic namespace and resource quota creation
- Kubernetes Job creation with resource requests
- Concurrent job limit enforcement

### 2. Job Lifecycle Management
- Job status tracking (PENDING → RUNNING → SUCCEEDED/FAILED)
- Background reconciler syncing K8s status to database
- Automatic job cleanup after completion
- Real-time log retrieval

### 3. Resource Management
- Per-tenant CPU and memory quotas
- Automatic ResourceQuota synchronization
- Node auto-scaling via Karpenter integration
- Automatic scale-down after job completion

### 4. Monitoring & Observability
- Tenant dashboard (usage, limits, job history)
- Admin cluster overview (nodes, resources, activity)
- Live job logs via API
- Real-time cluster activity metrics

---

## Technical Stack

| Component | Technology |
|-----------|-----------|
| API Framework | FastAPI (Python 3.11+) |
| Database | PostgreSQL 15+ |
| Container Orchestration | Kubernetes 1.28+ |
| Auto-scaling | Karpenter |
| Container Runtime | Docker/containerd |
| API Authentication | Bearer token (API keys) |

---

## Database Schema

### `tenants`
```sql
CREATE TABLE tenants (
  tenant_id TEXT PRIMARY KEY,
  namespace TEXT UNIQUE NOT NULL,
  api_key TEXT UNIQUE NOT NULL,
  max_cpu INT DEFAULT 10,
  max_memory_gi INT DEFAULT 20,
  max_concurrent_jobs INT DEFAULT 2,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### `jobs`
```sql
CREATE TABLE jobs (
  job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id TEXT REFERENCES tenants(tenant_id),
  k8s_job_name TEXT NOT NULL,
  k8s_namespace TEXT NOT NULL,
  status TEXT DEFAULT 'PENDING',
  submitted_at TIMESTAMPTZ DEFAULT NOW(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  scenario_data JSONB,
  cpu_request INT,
  memory_gi INT
);
```

---

## API Endpoints

### Tenant Endpoints (Requires: `Authorization: Bearer <API_KEY>`)

#### `POST /jobs`
Submit a simulation job.

**Request:**
```json
{
  "scenario_id": "zurich_peak",
  "cpu_request": 2,
  "memory_gi": 4
}
```

**Response:**
```json
{
  "job_id": "abc-123-def",
  "status": "PENDING"
}
```

**Logic:**
1. Authenticate API key → tenant
2. Check concurrent job limit
3. Create namespace + quota if not exists
4. Insert DB record
5. Create K8s Job
6. Return job_id

---

#### `GET /jobs/{job_id}`
Get job status.

**Response:**
```json
{
  "job_id": "abc-123-def",
  "status": "RUNNING",
  "submitted_at": "2025-11-09T10:30:00Z"
}
```

---

#### `GET /jobs/{job_id}/logs`
Get live logs from job's pod.

**Response:**
```json
{
  "job_id": "abc-123-def",
  "pod_name": "sim-abc123-xyz",
  "logs": "Starting SUMO simulation...\n..."
}
```

**Logic:**
1. Find pod by label `job-name=<k8s_job_name>`
2. Call K8s API `read_namespaced_pod_log()`
3. Return last 500 lines

---

#### `GET /tenants/me/dashboard`
Tenant's resource usage and job history.

**Response:**
```json
{
  "tenant_id": "city-a",
  "plan_limits": {
    "max_cpu": 10,
    "max_memory_gi": 20,
    "max_concurrent_jobs": 2
  },
  "current_usage": {
    "requests.cpu": "4",
    "requests.memory": "8Gi"
  },
  "running_pods": 2,
  "recent_jobs": [...],
  "stats": {
    "pending": 0,
    "running": 2,
    "succeeded": 15,
    "failed": 1
  }
}
```

---

### Admin Endpoints

#### `GET /admin/cluster`
All nodes and their status.

**Response:**
```json
{
  "nodes": [
    {
      "name": "node-abc123",
      "status": ["Ready"],
      "capacity": {"cpu": "16", "memory": "64Gi"},
      "allocatable": {"cpu": "15.5", "memory": "60Gi"},
      "pods_running": 3,
      "created": "2025-11-09T10:00:00Z"
    }
  ],
  "total_nodes": 1
}
```

---

#### `GET /admin/jobs?status=RUNNING`
All jobs across tenants (optional status filter).

**Response:**
```json
{
  "jobs": [
    {
      "job_id": "abc-123",
      "tenant_id": "city-a",
      "namespace": "city-a",
      "status": "RUNNING",
      "submitted_at": "2025-11-09T10:30:00Z",
      "k8s_status": {
        "active": 1,
        "succeeded": 0,
        "failed": 0
      }
    }
  ],
  "total": 1
}
```

---

#### `GET /admin/activity`
Real-time cluster activity summary.

**Response:**
```json
{
  "timestamp": "2025-11-09T11:00:00Z",
  "nodes": 2,
  "pods": {
    "Running": 5,
    "Pending": 1,
    "Succeeded": 12
  },
  "k8s_jobs": {
    "total": 18,
    "active": 5,
    "succeeded": 12
  },
  "db_jobs": {
    "PENDING": 1,
    "RUNNING": 5,
    "SUCCEEDED": 12
  }
}
```

---

## Kubernetes Resources

### Namespace
Created dynamically per tenant via API:
```python
client.V1Namespace(
    metadata=client.V1ObjectMeta(name=tenant['namespace'])
)
```

---

### ResourceQuota
Created/updated per tenant:
```python
client.V1ResourceQuota(
    metadata=client.V1ObjectMeta(name=f"{namespace}-quota"),
    spec=client.V1ResourceQuotaSpec(
        hard={
            "requests.cpu": str(tenant['max_cpu']),
            "requests.memory": f"{tenant['max_memory_gi']}Gi",
            "pods": "10"
        }
    )
)
```

---

### Job
Created per simulation:
```python
client.V1Job(
    metadata=client.V1ObjectMeta(
        name=job_name,
        namespace=tenant['namespace']
    ),
    spec=client.V1JobSpec(
        ttl_seconds_after_finished=120,  # Auto-cleanup
        template=client.V1PodTemplateSpec(
            spec=client.V1PodSpec(
                containers=[
                    client.V1Container(
                        name="sumo",
                        image="your-sumo-image:latest",
                        resources=client.V1ResourceRequirements(
                            requests={
                                "cpu": str(cpu_request),
                                "memory": f"{memory_gi}Gi"
                            }
                        ),
                        env=[
                            client.V1EnvVar(
                                name="SCENARIO_ID",
                                value=scenario_id
                            )
                        ]
                    )
                ],
                restart_policy="Never"
            )
        ),
        backoff_limit=0
    )
)
```

---

### Karpenter Provisioner
```python
{
    "apiVersion": "karpenter.sh/v1",
    "kind": "Provisioner",
    "metadata": {"name": "default"},
    "spec": {
        "ttlSecondsAfterEmpty": 60,
        "ttlSecondsUntilExpired": 604800,
        "limits": {
            "resources": {
                "cpu": "100",
                "memory": "200Gi"
            }
        },
        "requirements": [
            {
                "key": "kubernetes.io/arch",
                "operator": "In",
                "values": ["amd64"]
            }
        ]
    }
}
```

---

## Background Processes

### Job Status Reconciler
Runs every 30 seconds:

```python
def sync_job_status():
    while True:
        jobs = db.fetch("SELECT * FROM jobs WHERE status IN ('PENDING', 'RUNNING')")
        for job in jobs:
            k8s_job = k8s_batch.read_namespaced_job(job.k8s_job_name, job.namespace)
            new_status = determine_status(k8s_job.status)
            if new_status != job.status:
                db.update_job_status(job.job_id, new_status)
        time.sleep(30)
```

**Purpose**: Keep database in sync with Kubernetes Job states.

---

## Auto-Scaling Flow

```
1. Job submitted → DB record created
2. K8s Job created with resource requests
3. Pod pending (no capacity) → Karpenter detects
4. Karpenter provisions new node
5. Pod scheduled → Running
6. Simulation completes → Pod succeeds
7. After 120s → K8s deletes Job/Pod (ttlSecondsAfterFinished)
8. Node empty → After 60s → Karpenter terminates node
```

**Total time from completion to scale-down: ~3 minutes**

---

## Security & Authentication

### API Key Management
- Generated as UUID or random string
- Stored in `tenants.api_key`
- Sent as `Authorization: Bearer <API_KEY>`
- Mapped to tenant in all endpoints

### Kubernetes RBAC
Controller ServiceAccount needs:
```yaml
rules:
- apiGroups: [""]
  resources: ["namespaces", "resourcequotas", "pods", "pods/log"]
  verbs: ["get", "list", "create", "update", "patch"]
- apiGroups: ["batch"]
  resources: ["jobs"]
  verbs: ["get", "list", "create", "delete"]
```

---

## Configuration

### Environment Variables
```bash
DATABASE_URL=postgresql://user:pass@host:5432/sumo_k8
KUBE_CONFIG_PATH=/etc/kubeconfig  # or use in-cluster config
LOG_LEVEL=INFO
```

### Dependencies
```
fastapi==0.104.1
uvicorn==0.24.0
psycopg2-binary==2.9.9
kubernetes==28.1.0
```

---

## Deployment

### Controller Deployment
```python
# Create via Python (no YAML)
client.AppsV1Api().create_namespaced_deployment(
    namespace="default",
    body={
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "quota-controller"},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": "controller"}},
            "template": {
                "metadata": {"labels": {"app": "controller"}},
                "spec": {
                    "serviceAccountName": "quota-controller-sa",
                    "containers": [{
                        "name": "controller",
                        "image": "quota-controller:latest",
                        "ports": [{"containerPort": 8000}],
                        "env": [
                            {"name": "DATABASE_URL", "value": "..."}
                        ]
                    }]
                }
            }
        }
    }
)
```

---

## Project Structure

```
sumo-k8/
├── app.py                  # Main FastAPI application (all endpoints)
├── requirements.txt        # Python dependencies
├── Dockerfile             # Container image
├── README.md              # Setup and usage guide
└── TECHNICAL_SPEC.md      # This document
```

**Total: ~300 lines of Python code in single file**

---

## Setup Steps

1. **Database**:
   ```bash
   psql -h localhost -U postgres -c "CREATE DATABASE sumo_k8"
   psql -h localhost -U postgres sumo_k8 < schema.sql
   ```

2. **Controller**:
   ```bash
   docker build -t quota-controller:latest .
   kubectl apply -f serviceaccount.yaml
   kubectl apply -f deployment.yaml
   ```

3. **Create Tenant**:
   ```sql
   INSERT INTO tenants VALUES 
     ('city-a', 'city-a', 'key-abc123', 10, 20, 2);
   ```

4. **Submit Job**:
   ```bash
   curl -X POST http://controller/jobs \
     -H "Authorization: Bearer key-abc123" \
     -d '{"scenario_id": "zurich_peak", "cpu_request": 2, "memory_gi": 4}'
   ```

---

## Performance Characteristics

- **API latency**: <100ms (job submission)
- **Scale-up time**: 2-5 minutes (node provisioning)
- **Scale-down time**: 3 minutes (after job completion)
- **DB queries per request**: 1-3
- **Reconciler interval**: 30 seconds
- **Concurrent jobs per tenant**: Configurable (default: 2)

---

## Future Enhancements (v2)

- [ ] Plans table for tiered pricing
- [ ] Usage tracking and billing
- [ ] Webhook notifications on job completion
- [ ] Job priority queues
- [ ] Multi-region support
- [ ] JWT authentication
- [ ] Prometheus metrics export
- [ ] Rate limiting per tenant

---

**Document Version**: 1.0  
**Last Updated**: 2025-11-09  
**Status**: Ready for Implementation

