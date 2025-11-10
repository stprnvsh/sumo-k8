# SUMO-K8 Feature List

## Core Features

### 1. Multi-Tenant Job Submission
- REST API for job submission
- Per-tenant isolation via Kubernetes namespaces
- API key authentication
- Job request includes: scenario_id, cpu_request, memory_gi
- Returns job_id for tracking

### 2. Resource Quota Management
- Per-tenant CPU limits
- Per-tenant memory limits
- Per-tenant concurrent job limits
- Dynamic namespace creation
- Automatic ResourceQuota creation/updates
- Enforcement at Kubernetes level

### 3. Job Lifecycle Tracking
- Status states: PENDING → RUNNING → SUCCEEDED/FAILED
- Database persistence of all jobs
- Background reconciler syncs K8s status to DB (30s interval)
- Job metadata tracking (submission time, scenario data)

### 4. Automatic Scaling
- Karpenter integration for node auto-scaling
- Scale-up: Pods pending → New nodes provisioned (2-5 min)
- Scale-down: Jobs complete → Pods deleted → Nodes terminated (3 min)
- TTL-based cleanup (jobs: 120s, nodes: 60s after empty)
- Cluster-wide resource limits

### 5. Live Monitoring

#### Tenant Dashboard
- Current resource usage vs limits
- Running pod count
- Recent job history (20 jobs)
- Job statistics (pending/running/succeeded/failed)
- Quota status

#### Admin Cluster Overview
- All nodes with status
- CPU/memory capacity and allocation
- Pod counts per node
- Node creation timestamps

#### Job Status API
- Real-time job status from Kubernetes
- Live log retrieval from pods
- Job history queries
- Filter by status

#### Cluster Activity Metrics
- Total nodes
- Pods by status (Running/Pending/Succeeded)
- Active Kubernetes jobs
- Database job statistics
- Timestamp for metrics

### 6. Log Retrieval
- Live streaming of pod logs via API
- Last 500 lines per request
- Pod name identification
- Tenant-scoped (only own logs)

### 7. Authentication & Authorization
- API key per tenant
- Bearer token authentication
- Tenant isolation enforced
- Admin endpoints separated

## Technical Features

### Database
- PostgreSQL with 2 tables (tenants, jobs)
- UUID job identifiers
- JSONB for scenario data
- Timestamps for all events

### Kubernetes Integration
- Dynamic namespace creation
- ResourceQuota management
- Job creation with resource requests
- Pod log access
- Node monitoring
- Label-based pod discovery

### API Design
- RESTful endpoints
- JSON request/response
- Standard HTTP status codes
- Error handling
- Query parameter filtering

### Background Services
- Job status reconciler (30s loop)
- K8s to DB sync
- Automatic status updates
- Error logging

### Container Orchestration
- Kubernetes Job objects
- Resource requests/limits
- Container environment variables
- Restart policy: Never
- Backoff limit: 0

## Operational Features

### Automatic Cleanup
- Jobs deleted 120s after completion
- Pods removed with jobs
- Nodes terminated after 60s empty
- No manual intervention required

### Concurrent Job Limits
- Enforced at API level
- Configurable per tenant
- Returns 429 when exceeded
- Prevents resource exhaustion

### Dynamic Resource Creation
- Namespaces created on first job
- Quotas created automatically
- No manual kubectl commands
- DB-driven configuration

### Status Synchronization
- Background reconciler
- K8s authoritative for runtime state
- DB stores history and metadata
- Fast API responses (query DB, not K8s)

## Endpoints Summary

### Tenant Endpoints (8 total operations)
1. Submit job
2. Get job status
3. Get job logs
4. View tenant dashboard

### Admin Endpoints (3 views)
1. Cluster node status
2. All jobs (with filtering)
3. Real-time activity metrics

## Non-Features (Intentionally Excluded from v1)

### Not Included
- ❌ Multi-region support
- ❌ Job prioritization
- ❌ Plan/tier management (all tenants same initial quota)
- ❌ Usage billing/metering
- ❌ Webhook notifications
- ❌ JWT authentication
- ❌ Rate limiting
- ❌ Prometheus metrics export
- ❌ High availability (single controller)
- ❌ Job cancellation
- ❌ Job restart
- ❌ Persistent storage for results
- ❌ Custom Docker images per tenant
- ❌ GPU support

### Reasons for Exclusion
- Keep v1 minimal and functional
- Add complexity only when needed
- Faster initial implementation
- Easier to test and debug
- Can add in v2 based on real usage

## Performance Targets

| Metric | Target |
|--------|--------|
| API latency | <100ms |
| Node scale-up | 2-5 min |
| Node scale-down | ~3 min after job completion |
| Job status sync | 30s max delay |
| Concurrent jobs per tenant | Configurable (default: 2) |
| Tenants supported | Hundreds |

## Dependencies

### Runtime
- Python 3.11+
- PostgreSQL 15+
- Kubernetes 1.28+
- Karpenter

### Python Packages
- fastapi (API framework)
- uvicorn (ASGI server)
- psycopg2-binary (PostgreSQL client)
- kubernetes (K8s Python client)

## Security Features

### Authentication
- API key per tenant
- Bearer token in headers
- Key lookup in database

### Authorization
- Tenant can only access own jobs
- Tenant can only view own dashboard
- Admin endpoints separate
- Namespace isolation in K8s

### Kubernetes RBAC
- ServiceAccount for controller
- Minimal permissions (namespaces, quotas, jobs, pods)
- Read access to nodes
- No cluster-admin required

## Feature Implementation Status

| Feature | Designed | Implemented | Tested |
|---------|----------|-------------|--------|
| Job submission API | ✅ | ❌ | ❌ |
| API key auth | ✅ | ❌ | ❌ |
| Concurrent limits | ✅ | ❌ | ❌ |
| Namespace creation | ✅ | ❌ | ❌ |
| Quota management | ✅ | ❌ | ❌ |
| Job status tracking | ✅ | ❌ | ❌ |
| Log retrieval | ✅ | ❌ | ❌ |
| Tenant dashboard | ✅ | ❌ | ❌ |
| Admin monitoring | ✅ | ❌ | ❌ |
| Auto-scaling setup | ✅ | ❌ | ❌ |
| Background reconciler | ✅ | ❌ | ❌ |

---

**Total Features**: 11 core + 7 technical + 4 operational = 22 features  
**Lines of Code**: ~300 (estimated)  
**Complexity**: Low (intentionally minimal)  
**Status**: Ready for implementation

