# SUMO-K8 Architecture

## Modular Structure

The application has been refactored into a clean modular architecture:

```
sumo-k8/
├── app.py                 # Main FastAPI application (367 lines)
└── src/                   # Source modules
    ├── __init__.py
    ├── config.py          # Configuration management
    ├── database.py        # Database connection pooling
    ├── k8s_client.py      # Kubernetes client initialization
    ├── auth.py            # Authentication & tenant management
    ├── jobs.py            # Job submission & management
    ├── logs.py            # Log streaming & retrieval
    ├── scaling.py         # K8s resource management & scaling
    ├── reconciler.py      # Background job status sync
    └── models.py          # Pydantic models
```

## Module Responsibilities

### 1. `config.py` - Configuration
- Environment variable management
- Default values
- Production settings (file size limits, timeouts, etc.)

### 2. `database.py` - Database Layer
- Connection pooling (ThreadedConnectionPool)
- Context manager for transactions
- Automatic rollback on errors
- Pool lifecycle management

### 3. `k8s_client.py` - Kubernetes Client
- K8s client initialization
- In-cluster vs kubeconfig detection
- Global client instances (k8s_core, k8s_batch)

### 4. `auth.py` - Authentication & Tenant Management
- API key generation (`generate_api_key()`)
- Tenant authentication (`auth_tenant()`)
- Tenant creation (`create_tenant()`)
- API key regeneration (`regenerate_api_key()`)
- Tenant CRUD operations

### 5. `jobs.py` - Job Management
- Job submission (`submit_job()`)
- Resource validation (`validate_resource_request()`)
- ZIP file validation (`validate_and_extract_zip()`)
- Concurrent job checking (`check_concurrent_jobs()`)
- K8s Job creation (`create_k8s_job()`)
- Job status retrieval (`get_job_status()`)
- Log retrieval (`get_job_logs()`)

### 6. `logs.py` - Log Streaming
- Real-time log streaming (`stream_job_logs()`)
- Server-Sent Events (SSE) implementation
- Pod lifecycle detection

### 7. `scaling.py` - Resource Management & Scaling
- Namespace/quota management (`ensure_tenant_namespace()`)
- ConfigMap cleanup (`cleanup_configmaps()`)
- Cluster node information (`get_cluster_nodes()`)
- Cluster activity metrics (`get_cluster_activity()`)

### 8. `reconciler.py` - Background Processes
- Job status synchronization (`sync_job_status()`)
- Orphaned ConfigMap cleanup (`cleanup_old_configmaps()`)
- Runs in background threads

### 9. `models.py` - Data Models
- Pydantic models for request/response validation
- Type safety and validation

## API Endpoints

### Authentication & Account Management
- `POST /auth/register` - Create new tenant account
- `POST /auth/regenerate-key` - Regenerate API key
- `GET /auth/tenants` - List all tenants (admin)
- `GET /auth/tenants/{tenant_id}` - Get tenant info
- `PATCH /auth/tenants/{tenant_id}` - Update tenant limits

### Job Management (Tenant)
- `POST /jobs` - Submit simulation job
- `GET /jobs/{job_id}` - Get job status
- `GET /jobs/{job_id}/logs` - Get job logs (snapshot)
- `GET /jobs/{job_id}/logs/stream` - Stream logs (SSE)
- `GET /tenants/me/dashboard` - Tenant dashboard

### Admin Endpoints
- `GET /admin/cluster` - Cluster node status
- `GET /admin/jobs` - All jobs (with filtering)
- `GET /admin/activity` - Cluster activity metrics

### Health
- `GET /health` - Health check
- `GET /ready` - Readiness probe

## API Key Generation

API keys are generated using `secrets` module:
- Format: `sk-{32 random alphanumeric characters}`
- Configurable via `API_KEY_PREFIX` and `API_KEY_LENGTH`
- Cryptographically secure random generation

Example: `sk-AbC123XyZ789Def456Ghi012Jkl345Mno678`

## Account Creation Flow

1. **Register Tenant**: `POST /auth/register`
   ```json
   {
     "tenant_id": "city-a",
     "max_cpu": 10,
     "max_memory_gi": 20,
     "max_concurrent_jobs": 2
   }
   ```
   - Generates API key automatically
   - Creates namespace
   - Sets up ResourceQuota and LimitRange

2. **Use API Key**: Include in `Authorization: Bearer <api_key>` header

3. **Regenerate Key**: `POST /auth/regenerate-key` (admin operation)

## Benefits of Modular Architecture

1. **Separation of Concerns**: Each module has a single responsibility
2. **Testability**: Modules can be tested independently
3. **Maintainability**: Easier to find and fix bugs
4. **Reusability**: Functions can be reused across endpoints
5. **Scalability**: Easy to add new features without touching existing code

## Migration from Monolithic

The original `app.py` (1082 lines) has been split into:
- `app.py`: 367 lines (endpoints only)
- `src/`: ~800 lines across 9 modules

**Total**: Similar line count, but much better organized.

