"""Job submission and management"""
import uuid
import base64
import zipfile
import io
import logging
import tempfile
import json
from pathlib import Path
from urllib.parse import urlparse, unquote
from fastapi import HTTPException, UploadFile
from kubernetes import client
from .database import get_db
from .k8s_client import k8s_available, k8s_core, k8s_batch
from .scaling import ensure_tenant_namespace, cleanup_configmaps
from .config import (
    MAX_FILE_SIZE_MB,
    MAX_JOB_DURATION_HOURS,
    SUMO_IMAGE,
    S3_BUCKET,
    S3_REGION,
    SIMULATION_NODE_SELECTOR_KEY,
    SIMULATION_NODE_SELECTOR_VALUES,
    QUEUE_S3_PREFIX,
    MAX_QUEUED_JOBS_PER_TENANT,
)
import os
from .storage import detect_storage_type
from psycopg2.extras import Json
import boto3

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

def validate_and_extract_zip_path(zip_path: str) -> str:
    """Validate ZIP file on disk and extract SUMO config without loading to memory."""
    p = Path(zip_path)
    if not p.exists() or p.stat().st_size == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    zip_size_mb = p.stat().st_size / 1024 / 1024
    if zip_size_mb > MAX_FILE_SIZE_MB:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {zip_size_mb:.2f}MB (max: {MAX_FILE_SIZE_MB}MB)"
        )

    try:
        with zipfile.ZipFile(zip_path) as zip_file:
            sumocfg_files = [f for f in zip_file.namelist() if f.endswith(".sumocfg")]
            if not sumocfg_files:
                raise HTTPException(status_code=400, detail="No .sumocfg file found in zip")
            config_file = sumocfg_files[0]
            logger.info(f"Found SUMO config: {config_file}")
            return config_file
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")

async def _save_upload_to_tempfile(upload: UploadFile) -> str:
    """Stream UploadFile to disk to avoid holding ZIP in memory."""
    max_bytes = int(MAX_FILE_SIZE_MB) * 1024 * 1024
    read_size = 1024 * 1024  # 1MiB

    tmp = tempfile.NamedTemporaryFile(prefix="sumo_upload_", suffix=".zip", delete=False)
    tmp_path = tmp.name
    total = 0
    try:
        while True:
            chunk = await upload.read(read_size)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large: {total / 1024 / 1024:.2f}MB (max: {MAX_FILE_SIZE_MB}MB)"
                )
            tmp.write(chunk)
        tmp.flush()
        return tmp_path
    except Exception:
        try:
            tmp.close()
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        raise
    finally:
        try:
            tmp.close()
        except Exception:
            pass


def _parse_s3_url(s3_url: str) -> tuple[str, str]:
    """Parse a supported S3 URL into (bucket, key)."""
    parsed = urlparse((s3_url or "").strip())
    if parsed.scheme != "s3":
        raise HTTPException(
            status_code=400,
            detail="sumo_files_s3_url must be an s3:// URL (e.g., s3://my-bucket/path/file.zip)",
        )

    bucket = parsed.netloc.strip()
    key = unquote(parsed.path.lstrip("/"))
    if not bucket or not key:
        raise HTTPException(
            status_code=400,
            detail="Invalid s3:// URL: bucket and object key are required",
        )
    return bucket, key


def _save_s3_zip_to_tempfile(s3_url: str) -> str:
    """Download ZIP from S3 URL to tempfile with max-size guard."""
    bucket, key = _parse_s3_url(s3_url)
    s3 = boto3.client("s3", region_name=S3_REGION)

    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot access S3 object: {e}")

    size_bytes = int(head.get("ContentLength") or 0)
    max_bytes = int(MAX_FILE_SIZE_MB) * 1024 * 1024
    if size_bytes <= 0:
        raise HTTPException(status_code=400, detail="S3 object is empty")
    if size_bytes > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {size_bytes / 1024 / 1024:.2f}MB (max: {MAX_FILE_SIZE_MB}MB)",
        )

    tmp = tempfile.NamedTemporaryFile(prefix="sumo_s3_", suffix=".zip", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        s3.download_file(bucket, key, tmp_path)
        return tmp_path
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=f"Failed to download S3 object: {e}")

def _iter_base64_from_file(file_path: str, read_bytes: int = 1024 * 1024):
    """Yield base64 text chunks for file content without buffering entire file."""
    with open(file_path, "rb") as f:
        leftover = b""
        while True:
            data = f.read(read_bytes)
            if not data:
                break
            data = leftover + data
            n = (len(data) // 3) * 3
            to_encode = data[:n]
            leftover = data[n:]
            if to_encode:
                yield base64.b64encode(to_encode).decode("ascii")
        if leftover:
            yield base64.b64encode(leftover).decode("ascii")

def check_queued_capacity(tenant_id: str):
    """Reject only when queue is full (submission accepted until then)."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE tenant_id = %s AND status = 'QUEUED'",
            (tenant_id,),
        )
        if cur.fetchone()["cnt"] >= MAX_QUEUED_JOBS_PER_TENANT:
            raise HTTPException(
                status_code=503,
                detail=f"Job queue full ({MAX_QUEUED_JOBS_PER_TENANT} queued); retry later",
            )


def _queue_s3_key(tenant_id: str, job_id: str) -> str:
    return f"{QUEUE_S3_PREFIX}/{tenant_id}/{job_id}.zip"


def _upload_queue_zip_to_s3(local_path: str, tenant_id: str, job_id: str) -> str:
    if not S3_BUCKET:
        raise HTTPException(status_code=503, detail="S3 queue storage is not configured")
    key = _queue_s3_key(tenant_id, job_id)
    s3 = boto3.client("s3", region_name=S3_REGION)
    try:
        s3.upload_file(local_path, S3_BUCKET, key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to persist queued zip to S3: {e}")
    return key


def _download_queue_zip_from_s3(queue_key: str, job_id: str):
    if not S3_BUCKET:
        raise HTTPException(status_code=503, detail="S3 queue storage is not configured")
    s3 = boto3.client("s3", region_name=S3_REGION)
    tmp = tempfile.NamedTemporaryFile(prefix=f"queue_{job_id}_", suffix=".zip", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        s3.download_file(S3_BUCKET, queue_key, tmp_path)
        return tmp_path
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to download queued zip from S3: {e}")


def _delete_queue_zip_from_s3(queue_key: str):
    if not S3_BUCKET:
        return
    s3 = boto3.client("s3", region_name=S3_REGION)
    try:
        s3.delete_object(Bucket=S3_BUCKET, Key=queue_key)
    except Exception:
        pass


def dispatch_queued_jobs():
    """Promote QUEUED jobs to K8s when under max_concurrent_jobs (call from reconciler)."""
    if not k8s_available:
        return
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT tenant_id FROM jobs WHERE status = 'QUEUED'",
        )
        tenant_ids = [r["tenant_id"] for r in cur.fetchall()]
    for tid in tenant_ids:
        try:
            while _dispatch_one_queued(tid):
                pass
        except Exception as e:
            logger.error("dispatch queued for %s: %s", tid, e)


def _dispatch_one_queued(tenant_id: str) -> bool:
    if not k8s_available:
        return False
    job_id = None
    queue_path = None
    queue_key = None
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT j.* FROM jobs j
            INNER JOIN tenants t ON t.tenant_id = j.tenant_id
            WHERE j.tenant_id = %s AND j.status = 'QUEUED'
            AND (
              SELECT COUNT(*) FROM jobs j2
              WHERE j2.tenant_id = j.tenant_id
              AND j2.status IN ('PENDING', 'RUNNING')
            ) < t.max_concurrent_jobs
            ORDER BY j.submitted_at ASC
            LIMIT 1
            FOR UPDATE OF j SKIP LOCKED
            """,
            (tenant_id,),
        )
        job = cur.fetchone()
        if not job:
            return False
        job = dict(job)
        job_id = str(job["job_id"])
        cur.execute(
            "SELECT * FROM tenants WHERE tenant_id = %s",
            (tenant_id,),
        )
        tenant = dict(cur.fetchone())
        scenario = job["scenario_data"] or {}
        if isinstance(scenario, str):
            scenario = json.loads(scenario)
        scenario_id = scenario.get("scenario_id", "")
        config_file = scenario.get("config_file", "network.sumocfg")
        queue_key = scenario.get("queue_s3_key") or _queue_s3_key(tenant_id, job_id)
        try:
            queue_path = _download_queue_zip_from_s3(queue_key, job_id)
        except HTTPException:
            # Keep QUEUED for retry; do not drop jobs on transient/object-store issues.
            logger.warning("Queue zip not available yet for %s (%s)", job_id, queue_key)
            return False
        zip_size = os.path.getsize(queue_path)
        ensure_tenant_namespace(tenant)
        create_k8s_job(
            tenant,
            job_id,
            scenario_id,
            job["cpu_request"],
            job["memory_gi"],
            queue_path,
            zip_size,
            config_file,
        )
        cur.execute(
            "UPDATE jobs SET status = 'PENDING' WHERE job_id = %s",
            (job_id,),
        )
    if queue_path and os.path.isfile(queue_path):
        try:
            os.unlink(queue_path)
        except OSError:
            pass
    if queue_key:
        _delete_queue_zip_from_s3(queue_key)
    return True

def create_k8s_job(
    tenant: dict,
    job_id: str,
    scenario_id: str,
    cpu_request: int,
    memory_gi: int,
    zip_path: str,
    zip_size_bytes: int,
    config_file: str,
):
    """Create Kubernetes Job with SUMO files"""
    if not k8s_available:
        raise HTTPException(status_code=503, detail="Kubernetes not available")
    
    k8s_name = f"sim-{job_id[:8]}"
    cm_prefix = f"sumo-{job_id[:8]}-{uuid.uuid4().hex[:6]}"
    max_chunk_size = 900000  # Leave margin under 1MB limit

    # Estimate base64 length without encoding entire file in memory.
    est_b64_len = ((zip_size_bytes + 2) // 3) * 4

    # Handle large files by splitting into ConfigMaps (streaming base64 from disk).
    if est_b64_len > max_chunk_size:
        configmap_chunks = []

        buf = ""
        chunk_idx = 0
        try:
            for b64_part in _iter_base64_from_file(zip_path):
                buf += b64_part
                while len(buf) >= max_chunk_size:
                    chunk_data = buf[:max_chunk_size]
                    buf = buf[max_chunk_size:]

                    chunk_name = f"{cm_prefix}-chunk{chunk_idx}"
                    chunk_idx += 1
                    configmap = client.V1ConfigMap(
                        metadata=client.V1ObjectMeta(
                            name=chunk_name,
                            namespace=tenant["namespace"],
                            labels={"job-id": job_id, "cleanup": "true"},
                        ),
                        data={"chunk": chunk_data},
                    )
                    k8s_core.create_namespaced_config_map(tenant["namespace"], configmap)
                    configmap_chunks.append(chunk_name)
                    logger.info(f"Created ConfigMap chunk {chunk_name} ({len(chunk_data)} bytes)")

            if buf:
                chunk_name = f"{cm_prefix}-chunk{chunk_idx}"
                configmap = client.V1ConfigMap(
                    metadata=client.V1ObjectMeta(
                        name=chunk_name,
                        namespace=tenant["namespace"],
                        labels={"job-id": job_id, "cleanup": "true"},
                    ),
                    data={"chunk": buf},
                )
                k8s_core.create_namespaced_config_map(tenant["namespace"], configmap)
                configmap_chunks.append(chunk_name)
                logger.info(f"Created ConfigMap chunk {chunk_name} ({len(buf)} bytes)")
        except Exception as e:
            logger.error(f"Failed to create ConfigMap chunks: {e}")
            for cm_name in configmap_chunks:
                try:
                    k8s_core.delete_namespaced_config_map(cm_name, tenant["namespace"])
                except Exception:
                    pass
            raise HTTPException(status_code=500, detail=f"Failed to store files: {str(e)}")
        
        # Build volumes for chunks
        num_chunks = len(configmap_chunks)
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
for i in $(seq 0 {num_chunks - 1}); do
    cat /config/chunk$i/chunk >> sumo_files.zip.b64
done
base64 -d sumo_files.zip.b64 > sumo_files.zip
rm sumo_files.zip.b64

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

# Upload results to S3
python3 /scripts/upload_results.py
"""
        container_env = [
            client.V1EnvVar(name="SCENARIO_ID", value=scenario_id),
            client.V1EnvVar(name="JOB_ID", value=job_id),
            client.V1EnvVar(name="TENANT_ID", value=tenant['tenant_id']),
        ]
    else:
        # Single ConfigMap for small files
        configmap_name = f"{cm_prefix}-bundle"
        zip_b64 = ""
        try:
            for part in _iter_base64_from_file(zip_path):
                zip_b64 += part
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read upload: {str(e)}")

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

# Upload results to S3
python3 /scripts/upload_results.py
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
        container_env = [
            client.V1EnvVar(name="SCENARIO_ID", value=scenario_id),
            client.V1EnvVar(name="JOB_ID", value=job_id),
            client.V1EnvVar(name="TENANT_ID", value=tenant['tenant_id']),
        ]
    
    # Add S3 environment variables for direct upload
    storage_type = detect_storage_type()
    if storage_type == "s3" and S3_BUCKET:
        container_env.append(client.V1EnvVar(name="S3_BUCKET", value=S3_BUCKET))
        container_env.append(client.V1EnvVar(name="S3_REGION", value=S3_REGION))
        # Pass AWS credentials if available (alternative to IRSA)
        aws_key = os.getenv("AWS_ACCESS_KEY_ID", "")
        aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY", "")
        if aws_key and aws_secret:
            container_env.append(client.V1EnvVar(name="AWS_ACCESS_KEY_ID", value=aws_key))
            container_env.append(client.V1EnvVar(name="AWS_SECRET_ACCESS_KEY", value=aws_secret))
    
    # Scheduling: allow multiple node selector values via node affinity
    node_selector = None
    affinity = None
    if SIMULATION_NODE_SELECTOR_KEY and SIMULATION_NODE_SELECTOR_VALUES:
        if len(SIMULATION_NODE_SELECTOR_VALUES) == 1:
            node_selector = {SIMULATION_NODE_SELECTOR_KEY: SIMULATION_NODE_SELECTOR_VALUES[0]}
        else:
            affinity = client.V1Affinity(
                node_affinity=client.V1NodeAffinity(
                    required_during_scheduling_ignored_during_execution=client.V1NodeSelector(
                        node_selector_terms=[
                            client.V1NodeSelectorTerm(
                                match_expressions=[
                                    client.V1NodeSelectorRequirement(
                                        key=SIMULATION_NODE_SELECTOR_KEY,
                                        operator='In',
                                        values=SIMULATION_NODE_SELECTOR_VALUES,
                                    )
                                ]
                            )
                        ]
                    )
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
                    # Use service account with IRSA for S3 access
                    service_account_name="simulation-runner" if storage_type == "s3" else None,
                    affinity=affinity,
                    node_selector=node_selector,
                    containers=[
                        client.V1Container(
                            name="sumo",
                            image=SUMO_IMAGE,
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

async def submit_job(
    tenant: dict,
    scenario_id: str,
    cpu_request: int,
    memory_gi: int,
    sumo_files: UploadFile | None = None,
    sumo_files_s3_url: str | None = None,
):
    """Submit a SUMO simulation job"""
    # Validate resource request
    validate_resource_request(cpu_request, memory_gi, tenant)

    check_queued_capacity(tenant["tenant_id"])

    has_upload = sumo_files is not None
    has_s3_url = bool((sumo_files_s3_url or "").strip())
    if has_upload == has_s3_url:
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one input: sumo_files upload or sumo_files_s3_url",
        )

    # Resolve source ZIP to local tempfile (no large in-memory buffers).
    if has_upload:
        zip_path = await _save_upload_to_tempfile(sumo_files)
    else:
        zip_path = _save_s3_zip_to_tempfile(sumo_files_s3_url or "")

    try:
        config_file = validate_and_extract_zip_path(zip_path)
    except Exception:
        try:
            os.unlink(zip_path)
        except Exception:
            pass
        raise

    job_id = str(uuid.uuid4())
    k8s_name = f"sim-{job_id[:8]}"
    queue_key = _upload_queue_zip_to_s3(zip_path, tenant["tenant_id"], job_id)
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO jobs (job_id, tenant_id, k8s_job_name, k8s_namespace, status, scenario_data, cpu_request, memory_gi)
                   VALUES (%s, %s, %s, %s, 'QUEUED', %s, %s, %s)""",
                (
                    job_id,
                    tenant["tenant_id"],
                    k8s_name,
                    tenant["namespace"],
                    Json({"scenario_id": scenario_id, "config_file": config_file, "queue_s3_key": queue_key}),
                    cpu_request,
                    memory_gi,
                ),
            )
    except Exception:
        _delete_queue_zip_from_s3(queue_key)
        raise
    finally:
        try:
            os.unlink(zip_path)
        except Exception:
            pass

    ensure_tenant_namespace(tenant)
    dispatch_queued_jobs()

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM jobs WHERE job_id = %s", (job_id,))
        row = cur.fetchone()
        out_status = row["status"] if row else "QUEUED"

    return {"job_id": job_id, "status": out_status, "config_file": config_file}

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
    
    if job["status"] == "QUEUED":
        return {
            "job_id": job_id,
            "status": "QUEUED",
            "submitted_at": job["submitted_at"].isoformat() if job["submitted_at"] else None,
            "started_at": None,
            "finished_at": None,
        }

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
    
    error_message = None
    rf = job.get("result_files")
    if isinstance(rf, dict):
        error_message = rf.get("error_message")

    return {
        "job_id": job_id,
        "status": status,
        "submitted_at": job['submitted_at'].isoformat() if job['submitted_at'] else None,
        "started_at": job['started_at'].isoformat() if job['started_at'] else None,
        "finished_at": job['finished_at'].isoformat() if job['finished_at'] else None,
        "error": error_message,
    }

def get_job_logs(job_id: str, tenant_id: str, namespace: str, k8s_job_name: str):
    """Get job logs"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT status FROM jobs WHERE job_id = %s AND tenant_id = %s",
            (job_id, tenant_id),
        )
        row = cur.fetchone()
    if row and row["status"] == "QUEUED":
        return {"job_id": job_id, "logs": "Job queued; logs available after dispatch"}

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

