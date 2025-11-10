"""SUMO-K8 Controller - Main FastAPI application"""
import os
import signal
import sys
import threading
import logging
from datetime import datetime
from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# Import modules
from src import config
from src.database import init_db_pool, close_db_pool, get_db
from src.k8s_client import k8s_available
from src.auth import (
    get_tenant_from_header, create_tenant, regenerate_api_key,
    get_tenant, list_tenants, update_tenant_limits
)
from src.jobs import submit_job, get_job_status, get_job_logs
from src.logs import stream_job_logs
from src.scaling import get_cluster_nodes, get_cluster_activity, ensure_tenant_namespace
from src.reconciler import sync_job_status, cleanup_old_configmaps
from src.models import TenantCreate, TenantResponse, APIKeyRegenerate
from src.storage import detect_storage_type

# Configure logging
logging.basicConfig(
    level=config.LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="SUMO-K8 Controller",
    description="Multi-tenant Kubernetes job controller for SUMO simulations",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# Health & Readiness Endpoints
# ============================================================================

@app.get("/health")
def health_check():
    """Health check endpoint"""
    health = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "k8s_available": k8s_available,
        "db_available": False
    }
    
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            health["db_available"] = True
    except Exception as e:
        health["status"] = "unhealthy"
        health["db_error"] = str(e)
        return JSONResponse(status_code=503, content=health)
    
    return health

@app.get("/ready")
def readiness_check():
    """Readiness check endpoint"""
    if not k8s_available:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "Kubernetes not available"}
        )
    
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": f"Database error: {str(e)}"}
        )
    
    return {"status": "ready"}

# ============================================================================
# Authentication & Tenant Management Endpoints
# ============================================================================

@app.post("/auth/register", response_model=TenantResponse)
def register_tenant(tenant_data: TenantCreate):
    """Create a new tenant account"""
    tenant = create_tenant(
        tenant_id=tenant_data.tenant_id,
        max_cpu=tenant_data.max_cpu,
        max_memory_gi=tenant_data.max_memory_gi,
        max_concurrent_jobs=tenant_data.max_concurrent_jobs
    )
    
    # Ensure namespace is created
    ensure_tenant_namespace(tenant)
    
    return tenant

@app.post("/auth/regenerate-key", response_model=TenantResponse)
def regenerate_key(request: APIKeyRegenerate):
    """Regenerate API key for a tenant (admin operation)"""
    return regenerate_api_key(request.tenant_id)

@app.get("/auth/tenants")
def list_all_tenants():
    """List all tenants (admin endpoint)"""
    return {"tenants": list_tenants()}

@app.get("/auth/tenants/{tenant_id}", response_model=TenantResponse)
def get_tenant_info(tenant_id: str):
    """Get tenant information"""
    return get_tenant(tenant_id)

@app.patch("/auth/tenants/{tenant_id}", response_model=TenantResponse)
def update_tenant(tenant_id: str, max_cpu: int = None, max_memory_gi: int = None, max_concurrent_jobs: int = None):
    """Update tenant resource limits"""
    tenant = update_tenant_limits(tenant_id, max_cpu, max_memory_gi, max_concurrent_jobs)
    # Update K8s resources
    ensure_tenant_namespace(tenant)
    return tenant

# ============================================================================
# Job Endpoints (Tenant)
# ============================================================================

@app.post("/jobs")
async def submit_job_endpoint(
    scenario_id: str = Form(..., min_length=1, max_length=100),
    cpu_request: int = Form(2, ge=1, le=32),
    memory_gi: int = Form(4, ge=1, le=128),
    sumo_files: UploadFile = File(...),
    authorization: str = Header(None, alias="Authorization")
):
    """Submit a SUMO simulation job"""
    tenant = get_tenant_from_header(authorization)
    return await submit_job(tenant, scenario_id, cpu_request, memory_gi, sumo_files)

@app.get("/jobs/{job_id}")
def get_job_status_endpoint(job_id: str, authorization: str = Header(None, alias="Authorization")):
    """Get job status"""
    tenant = get_tenant_from_header(authorization)
    return get_job_status(job_id, tenant['tenant_id'])

@app.get("/jobs/{job_id}/logs")
def get_job_logs_endpoint(job_id: str, authorization: str = Header(None, alias="Authorization")):
    """Get job logs (snapshot)"""
    tenant = get_tenant_from_header(authorization)
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM jobs WHERE job_id = %s AND tenant_id = %s",
            (job_id, tenant['tenant_id'])
        )
        job = cur.fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        job = dict(job)
    
    return get_job_logs(job_id, tenant['tenant_id'], job['k8s_namespace'], job['k8s_job_name'])

@app.get("/jobs/{job_id}/logs/stream")
def stream_job_logs_endpoint(job_id: str, authorization: str = Header(None, alias="Authorization")):
    """Stream job logs in real-time using Server-Sent Events (SSE)"""
    tenant = get_tenant_from_header(authorization)
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM jobs WHERE job_id = %s AND tenant_id = %s",
            (job_id, tenant['tenant_id'])
        )
        job = cur.fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        job = dict(job)
    
    return stream_job_logs(job['k8s_namespace'], job['k8s_job_name'])

@app.get("/jobs/{job_id}/results")
def get_job_results(job_id: str, authorization: str = Header(None, alias="Authorization")):
    """Get job result files list"""
    tenant = get_tenant_from_header(authorization)
    
    with get_db() as conn:
        cur = conn.cursor()
        # Handle UUID type properly - try both UUID and text comparison
        try:
            cur.execute(
                """SELECT job_id::text, status, result_location, result_files, k8s_namespace 
                   FROM jobs WHERE job_id = %s::uuid AND tenant_id = %s""",
                (job_id, tenant['tenant_id'])
            )
        except:
            # Fallback to text comparison if UUID cast fails
            cur.execute(
                """SELECT job_id::text, status, result_location, result_files, k8s_namespace 
                   FROM jobs WHERE job_id::text = %s AND tenant_id = %s""",
                (job_id, tenant['tenant_id'])
            )
        job = cur.fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
    
    if job['status'] != 'SUCCEEDED':
        return {"job_id": job_id, "status": job['status'], "results": None, "message": "Job has not completed successfully"}
    
    storage_type = detect_storage_type()
    result_info = {
        "job_id": job_id,
        "storage_type": storage_type,
        "result_location": job['result_location'],
        "result_files": job['result_files']
    }
    
    if storage_type == "pvc":
        result_info["message"] = f"Results stored in PVC at {job['result_location']}"
        result_info["access_note"] = "Use kubectl exec to access files from a pod mounting the PVC"
    elif storage_type in ("s3", "gcs", "azure"):
        if job['result_files']:
            result_info["files"] = job['result_files'].get("files", [])
        else:
            result_info["message"] = "Results are being uploaded to object storage"
    else:
        result_info["message"] = "Result storage not configured"
    
    return result_info

@app.get("/tenants/me/dashboard")
def my_dashboard(authorization: str = Header(None, alias="Authorization")):
    """Get tenant dashboard with quota usage"""
    tenant = get_tenant_from_header(authorization)
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM jobs WHERE tenant_id = %s ORDER BY submitted_at DESC LIMIT 20",
            (tenant['tenant_id'],)
        )
        jobs = [dict(j) for j in cur.fetchall()]
    
    quota_status = {}
    running_pods = 0
    if k8s_available:
        from src.k8s_client import k8s_core
        try:
            quota = k8s_core.read_namespaced_resource_quota(
                f"{tenant['namespace']}-quota",
                tenant['namespace']
            )
            if quota.status and quota.status.used:
                quota_status = {
                    "requests.cpu": quota.status.used.get("requests.cpu", "0"),
                    "requests.memory": quota.status.used.get("requests.memory", "0"),
                    "limits.cpu": quota.status.used.get("limits.cpu", "0"),
                    "limits.memory": quota.status.used.get("limits.memory", "0")
                }
        except:
            pass
        
        try:
            pods = k8s_core.list_namespaced_pod(tenant['namespace'])
            running_pods = len([p for p in pods.items if p.status.phase == 'Running'])
        except:
            pass
    
    stats = {
        "pending": len([j for j in jobs if j['status'] == 'PENDING']),
        "running": len([j for j in jobs if j['status'] == 'RUNNING']),
        "succeeded": len([j for j in jobs if j['status'] == 'SUCCEEDED']),
        "failed": len([j for j in jobs if j['status'] == 'FAILED'])
    }
    
    return {
        "tenant_id": tenant['tenant_id'],
        "plan_limits": {
            "max_cpu": tenant['max_cpu'],
            "max_memory_gi": tenant['max_memory_gi'],
            "max_concurrent_jobs": tenant['max_concurrent_jobs']
        },
        "current_usage": quota_status,
        "running_pods": running_pods,
        "recent_jobs": [
            {
                "job_id": str(j['job_id']),
                "status": j['status'],
                "submitted_at": j['submitted_at'].isoformat() if j['submitted_at'] else None
            }
            for j in jobs
        ],
        "stats": stats
    }

# ============================================================================
# Admin Endpoints
# ============================================================================

@app.get("/admin/cluster")
def cluster_status():
    """Get cluster status (admin endpoint)"""
    if not k8s_available:
        return {"error": "Kubernetes not available", "nodes": [], "total_nodes": 0}
    
    nodes = get_cluster_nodes()
    return {"nodes": nodes, "total_nodes": len(nodes)}

@app.get("/admin/jobs")
def all_jobs(status: str = None):
    """List all jobs (admin endpoint)"""
    with get_db() as conn:
        cur = conn.cursor()
        if status:
            cur.execute(
                """SELECT j.*, t.namespace FROM jobs j 
                   JOIN tenants t ON j.tenant_id = t.tenant_id 
                   WHERE j.status = %s ORDER BY j.submitted_at DESC""",
                (status,)
            )
        else:
            cur.execute(
                """SELECT j.*, t.namespace FROM jobs j 
                   JOIN tenants t ON j.tenant_id = t.tenant_id 
                   ORDER BY j.submitted_at DESC LIMIT 100"""
            )
        
        jobs = [dict(j) for j in cur.fetchall()]
        
        if k8s_available:
            from src.k8s_client import k8s_batch
            for job in jobs:
                try:
                    k8s_job = k8s_batch.read_namespaced_job(job['k8s_job_name'], job['namespace'])
                    job['k8s_status'] = {
                        "active": k8s_job.status.active or 0,
                        "succeeded": k8s_job.status.succeeded or 0,
                        "failed": k8s_job.status.failed or 0
                    }
                except:
                    job['k8s_status'] = None
        else:
            for job in jobs:
                job['k8s_status'] = None
        
        return {"jobs": jobs, "total": len(jobs)}

@app.get("/admin/activity")
def cluster_activity():
    """Get cluster activity stats (admin endpoint)"""
    activity = get_cluster_activity()
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT status, COUNT(*) as count
            FROM jobs
            GROUP BY status
        """)
        db_stats = {row['status']: row['count'] for row in cur.fetchall()}
    
    return {
        "timestamp": datetime.now().isoformat(),
        "nodes": activity["nodes"],
        "pods": activity["pods"],
        "k8s_jobs": activity["k8s_jobs"],
        "db_jobs": db_stats
    }

# ============================================================================
# Startup & Shutdown
# ============================================================================

def graceful_shutdown(signum, frame):
    """Handle graceful shutdown"""
    logger.info("Received shutdown signal, cleaning up...")
    close_db_pool()
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_shutdown)
signal.signal(signal.SIGTERM, graceful_shutdown)

@app.on_event("startup")
def startup_event():
    """Initialize on startup"""
    init_db_pool()
    
    reconciler_thread = threading.Thread(target=sync_job_status, daemon=True)
    reconciler_thread.start()
    logger.info("Background job reconciler started")
    
    cleanup_thread = threading.Thread(target=cleanup_old_configmaps, daemon=True)
    cleanup_thread.start()
    logger.info("Background ConfigMap cleanup started")

@app.on_event("shutdown")
def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down...")
    close_db_pool()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        log_level=config.LOG_LEVEL.lower()
    )
