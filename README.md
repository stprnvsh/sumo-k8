# SUMO-K8 Controller

A multi-tenant Kubernetes job controller for running SUMO traffic simulation jobs. Manages tenant isolation, resource quotas, job lifecycle, and result storage through a REST API.

## What This Does

- Accepts SUMO simulation jobs via HTTP API
- Manages Kubernetes namespaces and resource quotas per tenant
- Tracks job status and provides log access
- Stores job results automatically (PVC for local clusters, object storage for cloud)
- Enforces resource limits to prevent cost overruns
- Works with any Kubernetes cluster (requires Karpenter or Cluster Autoscaler for node scaling)
- Can optionally create Kubernetes clusters (kind, minikube, GKE, EKS, AKS) via deployment scripts

## What This Does Not Do

- Does not manage SUMO network files (you provide them)
- Does not provide SUMO GUI or visualization
- Does not include built-in monitoring dashboards

## Prerequisites

- Kubernetes cluster (1.20+) OR ability to create one (kind, minikube, GKE, EKS, AKS)
- PostgreSQL database (12+)
- kubectl configured for your cluster (if using existing cluster)
- Python 3.9+ (for local development)

## Quick Start

### Local Development

```bash
# Setup PostgreSQL database
./setup_local.sh

# Install dependencies
pip install -r requirements.txt

# Set database URL
export DATABASE_URL="postgresql://$(whoami)@localhost/sumo_k8"

# Run application
python app.py
```

The API will be available at `http://localhost:8000`

### Kubernetes Deployment

**Deploy to existing cluster (minikube/kind/GKE/EKS/AKS)**

```bash
# 1. Build Docker image
docker build -t sumo-k8-controller:latest .

# 2. Load image into cluster (for local clusters)
# For minikube:
minikube image load sumo-k8-controller:latest
# For kind:
kind load docker-image sumo-k8-controller:latest

# 3. Create namespace and apply manifests
kubectl create namespace sumo-k8
kubectl apply -f k8s/

# 4. Create database secret
kubectl create secret generic sumo-k8-secrets \
  --from-literal=DATABASE_URL="postgresql://postgres:postgres@postgres-service:5432/sumo_k8" \
  --from-literal=POSTGRES_PASSWORD="postgres" \
  -n sumo-k8

# 5. Initialize database schema
kubectl cp schema.sql sumo-k8/$(kubectl get pods -n sumo-k8 -l app=postgres -o name | cut -d/ -f2):/tmp/schema.sql
kubectl exec -n sumo-k8 $(kubectl get pods -n sumo-k8 -l app=postgres -o name) -- \
  psql -U postgres -d sumo_k8 -f /tmp/schema.sql

# 6. Wait for pods to be ready
kubectl wait --for=condition=ready pod -l app=sumo-k8-controller -n sumo-k8 --timeout=120s

# 7. Port forward to access API
kubectl port-forward -n sumo-k8 svc/sumo-k8-controller 8000:80
```

The API will be available at `http://localhost:8000`

## Usage

### 1. Create Tenant Account

```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "my-tenant",
    "max_cpu": 10,
    "max_memory_gi": 20,
    "max_concurrent_jobs": 2
  }'
```

Returns an API key in the response. Save this key for authenticated requests.

### 2. Submit Simulation Job

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -F "scenario_id=my_simulation" \
  -F "cpu_request=2" \
  -F "memory_gi=4" \
  -F "sumo_files=@path/to/sumo_files.zip"
```

The ZIP file must contain:
- At least one `.sumocfg` file
- Required network files (`.net.xml`, `.rou.xml`, etc.)

### 3. Check Job Status

```bash
curl http://localhost:8000/jobs/{job_id} \
  -H "Authorization: Bearer YOUR_API_KEY"
```

### 4. Get Job Logs

```bash
# Snapshot
curl http://localhost:8000/jobs/{job_id}/logs \
  -H "Authorization: Bearer YOUR_API_KEY"

# Stream (Server-Sent Events)
curl -N http://localhost:8000/jobs/{job_id}/logs/stream \
  -H "Authorization: Bearer YOUR_API_KEY"
```

### 5. Get Job Results

```bash
# List result files
curl http://localhost:8000/jobs/{job_id}/results \
  -H "Authorization: Bearer YOUR_API_KEY"
```

Results are automatically stored:
- **Local clusters (minikube/kind)**: Results stored in PVC at `/results/{job_id}/`
- **Cloud clusters (GKE/EKS/AKS)**: Results uploaded to object storage (S3/GCS/Azure Blob)

## API Reference

### Authentication

All job endpoints require an API key in the Authorization header:
```
Authorization: Bearer sk-...
```

### Endpoints

**Account Management**
- `POST /auth/register` - Create tenant account
- `POST /auth/regenerate-key` - Regenerate API key
- `GET /auth/tenants/{tenant_id}` - Get tenant information
- `PATCH /auth/tenants/{tenant_id}` - Update resource limits

**Job Management** (requires API key)
- `POST /jobs` - Submit simulation job
- `GET /jobs/{job_id}` - Get job status
- `GET /jobs/{job_id}/logs` - Get job logs
- `GET /jobs/{job_id}/logs/stream` - Stream logs (SSE)
- `GET /jobs/{job_id}/results` - Get job result files
- `GET /tenants/me/dashboard` - Tenant dashboard

**Admin** (no authentication required)
- `GET /admin/cluster` - Cluster node status
- `GET /admin/jobs` - List all jobs
- `GET /admin/activity` - Cluster activity metrics

**Health**
- `GET /health` - Health check
- `GET /ready` - Readiness probe

## Configuration

### Required

```
DATABASE_URL=postgresql://user:pass@host:5432/sumo_k8
```

### Optional

```
# Application
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=INFO
CORS_ORIGINS=*

# Job Limits
MAX_FILE_SIZE_MB=100
MAX_JOB_DURATION_HOURS=24
MAX_CONCURRENT_JOBS_PER_TENANT=10

# Result Storage
RESULT_STORAGE_TYPE=auto              # auto, pvc, s3, gcs, azure
RESULT_STORAGE_SIZE_GI=10            # PVC size for local clusters

# Object Storage (cloud clusters only)
S3_BUCKET=my-bucket
S3_REGION=us-east-1
GCS_BUCKET=my-bucket
AZURE_STORAGE_ACCOUNT=myaccount
AZURE_CONTAINER=results
AZURE_STORAGE_CONNECTION_STRING=...

# Database Pooling
DB_POOL_MIN=2
DB_POOL_MAX=10
```

## Result Storage

Job results are automatically stored based on cluster type:

### Local Clusters (minikube/kind)

- Uses PersistentVolumeClaim (PVC) per tenant
- Results stored at `/results/{job_id}/` in the PVC
- PVC created automatically when tenant namespace is created
- Access results via `kubectl exec` into a pod mounting the PVC

### Cloud Clusters (GKE/EKS/AKS)

- Automatically detects cloud provider from node labels
- Uploads results to object storage if credentials are configured:
  - **GKE**: Google Cloud Storage (requires `GCS_BUCKET`)
  - **EKS**: Amazon S3 (requires `S3_BUCKET`, `S3_REGION`)
  - **AKS**: Azure Blob Storage (requires `AZURE_STORAGE_ACCOUNT`, `AZURE_CONTAINER`, `AZURE_STORAGE_CONNECTION_STRING`)
- Falls back to PVC if object storage not configured

### Accessing Results

```bash
# Get result file list and storage information
curl http://localhost:8000/jobs/{job_id}/results \
  -H "Authorization: Bearer YOUR_API_KEY"
```

For PVC storage, access files directly:
```bash
# Create a temporary pod to access PVC
kubectl run -it --rm debug --image=busybox --restart=Never \
  -n {tenant_namespace} -- sh

# Inside the pod
ls -lah /results/{job_id}/
```

For object storage, use the URLs provided in the API response.

## Architecture

- **FastAPI application** (`app.py`) - HTTP API and routing
- **Authentication** (`src/auth.py`) - API key management and tenant operations
- **Job management** (`src/jobs.py`) - Job submission, status tracking, and K8s Job creation
- **Storage** (`src/storage.py`) - Result storage management (PVC and object storage)
  - Auto-detects storage type (PVC for local, object storage for cloud)
  - Creates PVCs per tenant namespace automatically
  - Handles result file copying and metadata storage
- **Kubernetes client** (`src/k8s_client.py`) - K8s API client initialization
- **Resource management** (`src/scaling.py`) - Namespace, quota, and PVC management
  - Creates namespaces, ResourceQuota, LimitRange per tenant
  - Automatically creates PVC for result storage
- **Background reconciler** (`src/reconciler.py`) - Syncs K8s job status to database
  - Updates job status every 30 seconds
  - Backfills missing timestamps and result locations
- **Database** (`src/database.py`) - Connection pooling and queries
- **Configuration** (`src/config.py`) - Environment variable management

## Database Schema

Two main tables:

- `tenants` - Tenant accounts with API keys and resource limits
- `jobs` - Job records with status and Kubernetes metadata

See `schema.sql` for complete schema.

## Repurposing for Other Workloads

To adapt this for non-SUMO workloads:

1. **Change container image** in `src/jobs.py`:
   - Replace `ghcr.io/eclipse-sumo/sumo:latest` with your container image
   - Modify the command/args in the Kubernetes Job spec

2. **Modify file handling** in `src/jobs.py`:
   - Adjust ZIP extraction logic if needed
   - Change file validation (currently checks for `.sumocfg` files)
   - Modify result file patterns if different from `.xml`, `.txt`, `.log`

3. **Update job execution** in `src/jobs.py`:
   - Modify the run script that extracts files and executes the job
   - Change the main container command and working directory

4. **Adjust resource limits** in `src/config.py`:
   - Update default CPU/memory limits
   - Modify file size limits and job duration

5. **Update result storage** in `src/storage.py`:
   - Modify result file patterns if needed
   - Adjust storage paths or object storage prefixes

The core architecture (tenant isolation, quota management, job tracking, result storage) remains the same.

## Limitations

- Jobs are single-run (no retries on failure by default)
- ConfigMaps used for input file storage (1MB limit per ConfigMap, auto-chunked for larger files)
- Job cleanup happens after TTL (120 seconds default, configurable)
- Authentication limited to API keys (no OAuth/OIDC)
- Result file download from PVC requires manual pod access (object storage provides direct URLs)
- Result location backfill happens every 30 seconds (reconciler cycle)
- PVC creation requires ClusterRole permissions for persistentvolumeclaims resource

## Development

```bash
# Install dependencies
pip install -r requirements.txt

# Setup local database
./setup_local.sh

# Run application locally
export DATABASE_URL="postgresql://$(whoami)@localhost/sumo_k8"
python app.py

# For Kubernetes development with minikube
minikube start
docker build -t sumo-k8-controller:latest .
minikube image load sumo-k8-controller:latest
kubectl apply -f k8s/
kubectl port-forward -n sumo-k8 svc/sumo-k8-controller 8000:80
```

### Testing

```bash
# Run test script
./test_job.sh

# Or manually test
# 1. Create tenant
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"test","max_cpu":10,"max_memory_gi":20,"max_concurrent_jobs":2}'

# 2. Submit job
curl -X POST http://localhost:8000/jobs \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -F "scenario_id=test" \
  -F "cpu_request=2" \
  -F "memory_gi=4" \
  -F "sumo_files=@test_networks/zips/bologna-acosta.zip"

# 3. Check results
curl http://localhost:8000/jobs/{job_id}/results \
  -H "Authorization: Bearer YOUR_API_KEY"
```

## Project Structure

```
sumo-k8/
├── app.py                 # Main FastAPI application
├── src/                   # Source modules
│   ├── auth.py           # Authentication and tenant management
│   ├── jobs.py           # Job submission and status tracking
│   ├── storage.py        # Result storage (PVC and object storage)
│   ├── scaling.py        # K8s resource management (namespaces, quotas, PVCs)
│   ├── database.py       # Database connection pooling
│   ├── k8s_client.py    # Kubernetes client initialization
│   ├── reconciler.py     # Background job status sync
│   ├── logs.py           # Log streaming (SSE)
│   ├── config.py         # Configuration management
│   └── models.py         # Pydantic models
├── schema.sql            # Database schema
├── k8s/                  # Kubernetes deployment manifests
│   ├── namespace.yaml    # Namespace definition
│   ├── serviceaccount.yaml # ServiceAccount and RBAC
│   ├── rbac.yaml         # Additional RBAC for PVC creation
│   ├── configmap.yaml    # Application configuration
│   ├── secret.yaml.example # Secret template
│   ├── deployment.yaml   # Application deployment
│   ├── service.yaml      # Service definition
│   ├── ingress.yaml      # Ingress (optional)
│   ├── postgres.yaml     # PostgreSQL deployment
│   └── kustomization.yaml # Kustomize config
├── scripts/              # Utility scripts
│   └── port-forward.sh   # Stable port-forward script
├── test_networks/        # SUMO test scenarios
│   └── zips/             # Test network ZIP files
├── test_job.sh          # Test script for job submission
├── setup_local.sh        # Local database setup
├── setup_k8s.sh         # Kubernetes setup script
├── schema.sql           # Database schema
├── requirements.txt     # Python dependencies
├── Dockerfile          # Container image definition
└── README.md           # This file
```

## Additional Documentation

- `docs/DEPLOYMENT.md` - Detailed deployment guide
- `docs/TECHNICAL_SPEC.md` - Technical specification
- `schema.sql` - Database schema definition

## Troubleshooting

### Jobs stuck in PENDING
- Check cluster resources and node availability
- Verify ResourceQuota limits are not exceeded
- Check pod events: `kubectl describe pod -n {namespace}`

### Results not appearing
- For PVC storage: Verify PVC exists and is bound: `kubectl get pvc -n {namespace}`
- Check reconciler logs: `kubectl logs -n sumo-k8 -l app=sumo-k8-controller | grep result_location`
- Wait 30-60 seconds after job completion for reconciler to update result_location
- For object storage: Check credentials and bucket permissions
- Verify job completed successfully: `kubectl get job -n {namespace}`

### API authentication failures
- Verify API key format: `Authorization: Bearer sk-...`
- Ensure port-forward is running: `kubectl port-forward -n sumo-k8 svc/sumo-k8-controller 8000:80`
- Check tenant exists: `curl http://localhost:8000/auth/tenants/{tenant_id}`
- Regenerate API key if needed: `POST /auth/regenerate-key`

### Storage module not found
- Rebuild Docker image: `docker build --no-cache -t sumo-k8-controller:latest .`
- Clear minikube cache: `minikube ssh -- docker system prune -f`
- Reload image: `docker save sumo-k8-controller:latest | minikube image load -`
- Restart pods: `kubectl delete pods -n sumo-k8 -l app=sumo-k8-controller`

### PVC creation fails
- Verify RBAC permissions: `kubectl get clusterrole sumo-k8-controller -o yaml`
- Check service account: `kubectl get serviceaccount sumo-k8-controller -n sumo-k8`
- Ensure ClusterRole includes persistentvolumeclaims resource
- Check logs: `kubectl logs -n sumo-k8 -l app=sumo-k8-controller | grep PVC`

### Database connection errors
- Verify PostgreSQL pod is running: `kubectl get pods -n sumo-k8 -l app=postgres`
- Check secret exists: `kubectl get secret sumo-k8-secrets -n sumo-k8`
- Verify DATABASE_URL in secret matches PostgreSQL service
- Check database schema is initialized: `kubectl exec -n sumo-k8 $(kubectl get pods -n sumo-k8 -l app=postgres -o name) -- psql -U postgres -d sumo_k8 -c "\dt"`

## License

See LICENSE file.
