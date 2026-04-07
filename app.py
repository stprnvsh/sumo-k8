"""SUMO-K8 Controller - Main FastAPI application"""
import os
import signal
import sys
import threading
import logging
from datetime import datetime
from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Form, Depends
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
from src.storage import detect_storage_type, list_s3_files

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

# ============================================================================
# Admin Auth (X-Admin-Key)
# ============================================================================

def require_admin(x_admin_key: str = Header(None, alias="X-Admin-Key")):
    """Require X-Admin-Key for admin endpoints."""
    if not config.ADMIN_KEY:
        raise HTTPException(status_code=503, detail="Admin key not configured")
    if not x_admin_key or x_admin_key.strip() != config.ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    return True

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
def register_tenant(tenant_data: TenantCreate, _admin_ok: bool = Depends(require_admin)):
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
def regenerate_key(request: APIKeyRegenerate, _admin_ok: bool = Depends(require_admin)):
    """Regenerate API key for a tenant (admin operation)"""
    return regenerate_api_key(request.tenant_id)

@app.get("/auth/tenants")
def list_all_tenants(_admin_ok: bool = Depends(require_admin)):
    """List all tenants (admin endpoint)"""
    return {"tenants": list_tenants()}

@app.get("/auth/tenants/{tenant_id}", response_model=TenantResponse)
def get_tenant_info(tenant_id: str, _admin_ok: bool = Depends(require_admin)):
    """Get tenant information"""
    return get_tenant(tenant_id)

@app.patch("/auth/tenants/{tenant_id}", response_model=TenantResponse)
def update_tenant(
    tenant_id: str,
    max_cpu: int = None,
    max_memory_gi: int = None,
    max_concurrent_jobs: int = None,
    _admin_ok: bool = Depends(require_admin),
):
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
        if storage_type == "s3":
            prefix = (job.get('result_files') or {}).get("prefix") or job.get("result_location") or ""
            if prefix:
                files = list_s3_files(prefix)
                result_info["files"] = files
                if not files:
                    result_info["message"] = "No result files found in object storage yet"
            else:
                result_info["message"] = "Result prefix not available yet"
        elif job['result_files']:
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
        "queued": len([j for j in jobs if j['status'] == 'QUEUED']),
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
def cluster_status(_admin_ok: bool = Depends(require_admin)):
    """Get cluster status (admin endpoint)"""
    if not k8s_available:
        return {"error": "Kubernetes not available", "nodes": [], "total_nodes": 0}
    
    nodes = get_cluster_nodes()
    return {"nodes": nodes, "total_nodes": len(nodes)}

@app.get("/admin/jobs")
def all_jobs(status: str = None, _admin_ok: bool = Depends(require_admin)):
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
def cluster_activity(_admin_ok: bool = Depends(require_admin)):
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
# Cluster Warmup Endpoint
# ============================================================================

@app.post("/admin/warmup")
def warmup_cluster(
    cpu_request: int = 2,
    memory_gi: int = 4,
    keep_alive_seconds: int = 300,
    _admin_ok: bool = Depends(require_admin),
):
    """
    Pre-warm the cluster by spinning up a simulation node and pulling the SUMO image.
    
    - cpu_request: CPU cores for the warmup pod (determines node size)
    - memory_gi: Memory in GB for the warmup pod
    - keep_alive_seconds: How long to keep the warmup pod running (default 5 min)
    
    The node will remain available until Karpenter's consolidation kicks in (typically 30s after pod terminates).
    """
    if not k8s_available:
        raise HTTPException(status_code=503, detail="Kubernetes not available")
    
    from kubernetes import client
    from src.k8s_client import k8s_core, k8s_batch
    from src.config import SUMO_IMAGE
    import uuid
    
    warmup_id = str(uuid.uuid4())[:8]
    job_name = f"warmup-{warmup_id}"
    namespace = "sumo-k8"  # Use controller namespace for warmup jobs
    
    # Ensure namespace exists
    try:
        k8s_core.read_namespace(namespace)
    except:
        pass
    
    # Create warmup job that pulls image and stays alive briefly
    warmup_script = f"""#!/bin/sh
echo "Warmup started - pulling image and keeping node warm"
echo "SUMO image: {SUMO_IMAGE}"
echo "Keep alive for {keep_alive_seconds} seconds..."
sleep {keep_alive_seconds}
echo "Warmup complete"
"""
    
    # Scheduling: allow multiple node selector values via node affinity
    node_selector = None
    affinity = None
    key = getattr(config, 'SIMULATION_NODE_SELECTOR_KEY', 'node-type')
    values = getattr(config, 'SIMULATION_NODE_SELECTOR_VALUES', ['simulation'])
    if key and values:
        if len(values) == 1:
            node_selector = {key: values[0]}
        else:
            affinity = client.V1Affinity(
                node_affinity=client.V1NodeAffinity(
                    required_during_scheduling_ignored_during_execution=client.V1NodeSelector(
                        node_selector_terms=[
                            client.V1NodeSelectorTerm(
                                match_expressions=[
                                    client.V1NodeSelectorRequirement(
                                        key=key, operator='In', values=values
                                    )
                                ]
                            )
                        ]
                    )
                )
            )

    job_manifest = client.V1Job(
        metadata=client.V1ObjectMeta(
            name=job_name,
            namespace=namespace,
            labels={"app": "sumo-k8-warmup", "warmup-id": warmup_id}
        ),
        spec=client.V1JobSpec(
            ttl_seconds_after_finished=60,  # Auto-cleanup after 1 minute
            backoff_limit=0,
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(
                    labels={"app": "sumo-k8-warmup", "warmup-id": warmup_id}
                ),
                spec=client.V1PodSpec(
                    affinity=affinity,
                    node_selector=node_selector,
                    containers=[
                        client.V1Container(
                            name="warmup",
                            image=SUMO_IMAGE,
                            command=["/bin/sh", "-c"],
                            args=[warmup_script],
                            resources=client.V1ResourceRequirements(
                                requests={"cpu": str(cpu_request), "memory": f"{memory_gi}Gi"},
                                limits={"cpu": str(cpu_request), "memory": f"{memory_gi}Gi"}
                            )
                        )
                    ],
                    restart_policy="Never"
                )
            )
        )
    )
    
    try:
        k8s_batch.create_namespaced_job(namespace, job_manifest)
        logger.info(f"Created warmup job {job_name}")
        
        return {
            "status": "warming_up",
            "warmup_id": warmup_id,
            "job_name": job_name,
            "namespace": namespace,
            "cpu_request": cpu_request,
            "memory_gi": memory_gi,
            "keep_alive_seconds": keep_alive_seconds,
            "message": f"Warmup job created. Node will spin up and pull image. Pod will stay alive for {keep_alive_seconds}s."
        }
    except Exception as e:
        logger.error(f"Failed to create warmup job: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create warmup job: {str(e)}")

@app.get("/admin/warmup/status")
def warmup_status(_admin_ok: bool = Depends(require_admin)):
    """Check status of warmup jobs"""
    if not k8s_available:
        raise HTTPException(status_code=503, detail="Kubernetes not available")
    
    from src.k8s_client import k8s_batch, k8s_core
    
    namespace = "sumo-k8"
    warmup_jobs = []
    
    try:
        jobs = k8s_batch.list_namespaced_job(namespace, label_selector="app=sumo-k8-warmup")
        for job in jobs.items:
            status = "pending"
            if job.status.active:
                status = "running"
            elif job.status.succeeded:
                status = "completed"
            elif job.status.failed:
                status = "failed"
            
            warmup_jobs.append({
                "name": job.metadata.name,
                "warmup_id": job.metadata.labels.get("warmup-id"),
                "status": status,
                "created_at": job.metadata.creation_timestamp.isoformat() if job.metadata.creation_timestamp else None
            })
    except Exception as e:
        logger.error(f"Failed to list warmup jobs: {e}")
    
    # Also check for simulation nodes
    simulation_nodes = []
    try:
        nodes = k8s_core.list_node(label_selector="node-type=simulation")
        for node in nodes.items:
            simulation_nodes.append({
                "name": node.metadata.name,
                "ready": any(c.type == "Ready" and c.status == "True" for c in node.status.conditions),
                "created_at": node.metadata.creation_timestamp.isoformat() if node.metadata.creation_timestamp else None
            })
    except Exception as e:
        logger.error(f"Failed to list simulation nodes: {e}")
    
    return {
        "warmup_jobs": warmup_jobs,
        "simulation_nodes": simulation_nodes,
        "message": f"{len(simulation_nodes)} simulation node(s) available"
    }

@app.delete("/admin/warmup/{warmup_id}")
def cancel_warmup(warmup_id: str, _admin_ok: bool = Depends(require_admin)):
    """Cancel a warmup job"""
    if not k8s_available:
        raise HTTPException(status_code=503, detail="Kubernetes not available")
    
    from src.k8s_client import k8s_batch
    
    namespace = "sumo-k8"
    job_name = f"warmup-{warmup_id}"
    
    try:
        k8s_batch.delete_namespaced_job(job_name, namespace, propagation_policy="Foreground")
        return {"status": "cancelled", "warmup_id": warmup_id}
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Warmup job not found: {str(e)}")

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
    
    if os.getenv("ENABLE_RECONCILER", "false").lower() == "true":
        reconciler_thread = threading.Thread(target=sync_job_status, daemon=True)
        reconciler_thread.start()
        logger.info("Background job reconciler started")
    else:
        logger.warning("Background job reconciler disabled (set ENABLE_RECONCILER=true to enable)")
    
    if os.getenv("ENABLE_CONFIGMAP_CLEANUP", "false").lower() == "true":
        cleanup_thread = threading.Thread(target=cleanup_old_configmaps, daemon=True)
        cleanup_thread.start()
        logger.info("Background ConfigMap cleanup started")
    else:
        logger.warning("Background ConfigMap cleanup disabled (set ENABLE_CONFIGMAP_CLEANUP=true to enable)")

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
