"""Job submission and management"""
import uuid
import base64
import zipfile
import io
import logging
from fastapi import HTTPException, UploadFile
from datetime import datetime
from kubernetes import client
from .database import get_db
from .k8s_client import k8s_available, k8s_core, k8s_batch
from .scaling import ensure_tenant_namespace, cleanup_configmaps
from .config import MAX_FILE_SIZE_MB, MAX_JOB_DURATION_HOURS
from .storage import detect_storage_type
from psycopg2.extras import Json

logger = logging.getLogger(__name__)

def validate_resource_request(cpu_request: int, memory_gi: int, tenant: dict):
    """Validate resource request against tenant limits"""
    if cpu_request <= 0 or cpu_request > tenant['max_cpu']:
        raise HTTPException(
            status_code=400,
            detail=f"CPU request ({cpu_request}) must be between 1 and {tenant['max_cpu']}"
        )
    
    if memory_gi <= 0 or memory_gi > tenant['max_memory_gi']:
        raise HTTPException(
            status_code=400,
            detail=f"Memory request ({memory_gi}Gi) must be between 1 and {tenant['max_memory_gi']}Gi"
        )

def validate_and_extract_zip(zip_content: bytes):
    """Validate ZIP file and extract SUMO config"""
    if len(zip_content) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    
    zip_size_mb = len(zip_content) / 1024 / 1024
    if zip_size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {zip_size_mb:.2f}MB (max: {MAX_FILE_SIZE_MB}MB)"
        )
    
    try:
        zip_file = zipfile.ZipFile(io.BytesIO(zip_content))
        sumocfg_files = [f for f in zip_file.namelist() if f.endswith('.sumocfg')]
        if not sumocfg_files:
            raise HTTPException(status_code=400, detail="No .sumocfg file found in zip")
        config_file = sumocfg_files[0]
        logger.info(f"Found SUMO config: {config_file}")
        return config_file
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")

def check_concurrent_jobs(tenant_id: str, max_concurrent: int):
    """Check if tenant has exceeded concurrent job limit"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE tenant_id = %s AND status IN ('PENDING', 'RUNNING')",
            (tenant_id,)
        )
        count = cur.fetchone()['cnt']
        
        if count >= max_concurrent:
            raise HTTPException(
                status_code=429,
                detail=f"Too many concurrent jobs ({count}/{max_concurrent})"
            )

def create_k8s_job(tenant: dict, job_id: str, scenario_id: str, 
                   cpu_request: int, memory_gi: int, zip_b64: str, config_file: str):
    """Create Kubernetes Job with SUMO files"""
    if not k8s_available:
        raise HTTPException(status_code=503, detail="Kubernetes not available")
    
    k8s_name = f"sim-{job_id[:8]}"
    max_chunk_size = 900000  # Leave margin under 1MB limit
    
    # Handle large files by splitting into ConfigMaps
    if len(zip_b64) > max_chunk_size:
        num_chunks = (len(zip_b64) + max_chunk_size - 1) // max_chunk_size
        configmap_chunks = []
        
        for i in range(num_chunks):
            chunk_name = f"sumo-{job_id[:8]}-chunk{i}"
            start = i * max_chunk_size
            end = min((i + 1) * max_chunk_size, len(zip_b64))
            chunk_data = zip_b64[start:end]
            
            configmap = client.V1ConfigMap(
                metadata=client.V1ObjectMeta(
                    name=chunk_name,
                    namespace=tenant['namespace'],
                    labels={"job-id": job_id, "cleanup": "true"}
                ),
                data={"chunk": chunk_data}
            )
            try:
                k8s_core.create_namespaced_config_map(tenant['namespace'], configmap)
                configmap_chunks.append(chunk_name)
                logger.info(f"Created ConfigMap chunk {chunk_name} ({len(chunk_data)} bytes)")
            except Exception as e:
                logger.error(f"Failed to create ConfigMap chunk: {e}")
                # Cleanup already created chunks
                for cm_name in configmap_chunks:
                    try:
                        k8s_core.delete_namespaced_config_map(cm_name, tenant['namespace'])
                    except:
                        pass
                raise HTTPException(status_code=500, detail=f"Failed to store files: {str(e)}")
        
        # Build volumes for chunks
        volumes = []
        volume_mounts = []
        for i, chunk_name in enumerate(configmap_chunks):
            volumes.append(
                client.V1Volume(
                    name=f"sumo-chunk-{i}",
                    config_map=client.V1ConfigMapVolumeSource(name=chunk_name)
                )
            )
            volume_mounts.append(
                client.V1VolumeMount(name=f"sumo-chunk-{i}", mount_path=f"/config/chunk{i}")
            )
        
        run_script = f"""#!/bin/sh
set -e
echo "Setting up workspace..."
mkdir -p /workspace
cd /workspace

echo "Reassembling SUMO files from {num_chunks} chunks..."
# Sort chunks numerically (chunk0, chunk1, chunk2... not chunk0, chunk1, chunk10...)
for i in $(seq 0 {num_chunks - 1}); do
    cat /config/chunk$i/chunk >> sumo_files.zip.b64
done
base64 -d sumo_files.zip.b64 > sumo_files.zip
rm sumo_files.zip.b64

if ! command -v unzip >/dev/null 2>&1; then
    echo "Installing unzip..."
    apt-get update -qq && apt-get install -y -qq unzip >/dev/null 2>&1 || apk add --no-cache unzip >/dev/null 2>&1 || yum install -y -q unzip >/dev/null 2>&1
fi

unzip -q sumo_files.zip
rm sumo_files.zip

echo "Finding SUMO config file..."
CONFIG_FILE=$(find . -name "*.sumocfg" | head -1)
if [ -z "$CONFIG_FILE" ]; then
    echo "Error: No .sumocfg file found"
    find . -type f | head -10
    exit 1
fi

echo "Running SUMO simulation: sumo -c $CONFIG_FILE"
sumo -c "$CONFIG_FILE" || exit 1

echo "Simulation completed successfully"
ls -lah

# Copy results to persistent storage (if PVC mounted)
if [ -d /results ]; then
    echo "Copying results to persistent storage..."
    mkdir -p /results/{job_id}
    cp -r /workspace/*.xml /workspace/*.txt /workspace/*.log /results/{job_id}/ 2>/dev/null || true
    echo "Results saved to /results/{job_id}/"
    ls -lah /results/{job_id}/ || true
fi
"""
        container_env = [client.V1EnvVar(name="SCENARIO_ID", value=scenario_id)]
    else:
        # Single ConfigMap for small files
        configmap_name = f"sumo-{job_id[:8]}"
        configmap = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(
                name=configmap_name,
                namespace=tenant['namespace'],
                labels={"job-id": job_id, "cleanup": "true"}
            ),
            data={"sumo_files.zip.b64": zip_b64}
        )
        try:
            k8s_core.create_namespaced_config_map(tenant['namespace'], configmap)
            logger.info(f"Created ConfigMap {configmap_name} for SUMO files ({len(zip_b64)} bytes)")
        except Exception as e:
            logger.error(f"Failed to create ConfigMap: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to store files: {str(e)}")
        
        run_script = f"""#!/bin/sh
set -e
echo "Setting up workspace..."
mkdir -p /workspace
cd /workspace

echo "Extracting SUMO files from ConfigMap..."
cat /config/sumo_files.zip.b64 | base64 -d > sumo_files.zip

if ! command -v unzip >/dev/null 2>&1; then
    echo "Installing unzip..."
    apt-get update -qq && apt-get install -y -qq unzip >/dev/null 2>&1 || apk add --no-cache unzip >/dev/null 2>&1 || yum install -y -q unzip >/dev/null 2>&1
fi

unzip -q sumo_files.zip
rm sumo_files.zip

echo "Finding SUMO config file..."
CONFIG_FILE=$(find . -name "*.sumocfg" | head -1)
if [ -z "$CONFIG_FILE" ]; then
    echo "Error: No .sumocfg file found"
    find . -type f | head -10
    exit 1
fi

echo "Running SUMO simulation: sumo -c $CONFIG_FILE"
sumo -c "$CONFIG_FILE" || exit 1

echo "Simulation completed successfully"
ls -lah

# Copy results to persistent storage (if PVC mounted)
if [ -d /results ]; then
    echo "Copying results to persistent storage..."
    mkdir -p /results/{job_id}
    cp -r /workspace/*.xml /workspace/*.txt /workspace/*.log /results/{job_id}/ 2>/dev/null || true
    echo "Results saved to /results/{job_id}/"
    ls -lah /results/{job_id}/ || true
fi
"""
        
        volumes = [
            client.V1Volume(
                name="sumo-files",
                config_map=client.V1ConfigMapVolumeSource(name=configmap_name)
            )
        ]
        volume_mounts = [
            client.V1VolumeMount(name="sumo-files", mount_path="/config")
        ]
        container_env = [client.V1EnvVar(name="SCENARIO_ID", value=scenario_id)]
    
    # Always mount PVC for result storage (even for cloud, we write to PVC first then upload)
    storage_type = detect_storage_type()
    pvc_name = f"results-{tenant['namespace']}"
    volumes.append(
        client.V1Volume(
            name="results",
            persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                claim_name=pvc_name
            )
        )
    )
    volume_mounts.append(
        client.V1VolumeMount(
            name="results",
            mount_path="/results"
        )
    )
    
    # Create Kubernetes Job
    job_manifest = client.V1Job(
        metadata=client.V1ObjectMeta(
            name=k8s_name,
            namespace=tenant['namespace'],
            labels={"job-id": job_id, "tenant": tenant['tenant_id']}
        ),
        spec=client.V1JobSpec(
            ttl_seconds_after_finished=120,
            active_deadline_seconds=MAX_JOB_DURATION_HOURS * 3600,
            backoff_limit=0,
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(
                    labels={"job-id": job_id, "tenant": tenant['tenant_id']}
                ),
                spec=client.V1PodSpec(
                    # Schedule on simulation nodes (large, on-demand instances)
                    # Simulation nodes: c5.4xlarge or similar - scale from 0, created on-demand
                    node_selector={
                        "node-type": "simulation"
                    },
                    containers=[
                        client.V1Container(
                            name="sumo",
                            image="ghcr.io/eclipse-sumo/sumo:latest",
                            command=["/bin/sh", "-c"],
                            args=[run_script],
                            resources=client.V1ResourceRequirements(
                                requests={
                                    "cpu": str(cpu_request),
                                    "memory": f"{memory_gi}Gi"
                                },
                                limits={
                                    "cpu": str(cpu_request),
                                    "memory": f"{memory_gi}Gi"
                                }
                            ),
                            env=container_env,
                            volume_mounts=volume_mounts,
                            working_dir="/workspace"
                        )
                    ],
                    volumes=volumes,
                    restart_policy="Never"
                )
            )
        )
    )
    
    try:
        k8s_batch.create_namespaced_job(tenant['namespace'], job_manifest)
        logger.info(f"Created K8s Job {k8s_name} for tenant {tenant['tenant_id']}")
    except Exception as e:
        logger.error(f"Failed to create K8s job: {e}")
        cleanup_configmaps(tenant['namespace'], job_id, delay_seconds=0)
        raise HTTPException(status_code=500, detail=f"Failed to create job: {str(e)}")

async def submit_job(tenant: dict, scenario_id: str, cpu_request: int, 
                    memory_gi: int, sumo_files: UploadFile):
    """Submit a SUMO simulation job"""
    # Validate resource request
    validate_resource_request(cpu_request, memory_gi, tenant)
    
    # Read and validate file
    zip_content = await sumo_files.read()
    config_file = validate_and_extract_zip(zip_content)
    zip_b64 = base64.b64encode(zip_content).decode('utf-8')
    
    # Check concurrent jobs
    check_concurrent_jobs(tenant['tenant_id'], tenant['max_concurrent_jobs'])
    
    # Create job record
    job_id = str(uuid.uuid4())
    k8s_name = f"sim-{job_id[:8]}"
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO jobs (job_id, tenant_id, k8s_job_name, k8s_namespace, status, scenario_data, cpu_request, memory_gi)
               VALUES (%s, %s, %s, %s, 'PENDING', %s, %s, %s)""",
            (job_id, tenant['tenant_id'], k8s_name, tenant['namespace'], 
             Json({"scenario_id": scenario_id, "config_file": config_file}), cpu_request, memory_gi)
        )
        conn.commit()
    
    # Ensure namespace exists
    ensure_tenant_namespace(tenant)
    
    # Create Kubernetes Job
    create_k8s_job(tenant, job_id, scenario_id, cpu_request, memory_gi, zip_b64, config_file)
    
    return {"job_id": job_id, "status": "PENDING", "config_file": config_file}

def get_job_status(job_id: str, tenant_id: str):
    """Get job status"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM jobs WHERE job_id = %s AND tenant_id = %s",
            (job_id, tenant_id)
        )
        job = cur.fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        job = dict(job)
    
    # Try to get live status from K8s
    try:
        if k8s_available:
            k8s_job = k8s_batch.read_namespaced_job(job['k8s_job_name'], job['k8s_namespace'])
            if k8s_job.status.succeeded:
                status = "SUCCEEDED"
            elif k8s_job.status.failed:
                status = "FAILED"
            elif k8s_job.status.active:
                status = "RUNNING"
            else:
                status = job['status']
        else:
            status = job['status']
    except:
        status = job['status']
    
    return {
        "job_id": job_id,
        "status": status,
        "submitted_at": job['submitted_at'].isoformat() if job['submitted_at'] else None,
        "started_at": job['started_at'].isoformat() if job['started_at'] else None,
        "finished_at": job['finished_at'].isoformat() if job['finished_at'] else None
    }

def get_job_logs(job_id: str, tenant_id: str, namespace: str, k8s_job_name: str):
    """Get job logs"""
    if not k8s_available:
        return {"job_id": job_id, "logs": "Kubernetes not available"}
    
    try:
        pods = k8s_core.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"job-name={k8s_job_name}"
        )
        
        if not pods.items:
            return {"job_id": job_id, "logs": "No pod found yet"}
        
        pod_name = pods.items[0].metadata.name
        logs = k8s_core.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            tail_lines=500
        )
        
        return {
            "job_id": job_id,
            "pod_name": pod_name,
            "logs": logs
        }
    except Exception as e:
        return {"job_id": job_id, "error": str(e), "logs": ""}

