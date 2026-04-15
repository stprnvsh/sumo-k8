"""Background job status reconciler"""
import time
import logging
import os
import json
from datetime import datetime, timedelta
from kubernetes import client
import boto3
from .database import get_db
from .k8s_client import k8s_available, k8s_batch, k8s_core
from .scaling import cleanup_configmaps
from .jobs import dispatch_queued_jobs
from .storage import detect_storage_type, get_result_storage_info, s3_prefix_has_files
from .config import (
    AWS_REGION,
    ENABLE_LEGACY_CONFIGMAP_SWEEPER,
    LEGACY_CONFIGMAP_SWEEPER_NAMESPACES,
    LEGACY_CONFIGMAP_SWEEPER_PREFIX,
    LEGACY_CONFIGMAP_SWEEPER_NAME_CONTAINS,
    LEGACY_CONFIGMAP_SWEEPER_MIN_AGE_HOURS,
    LEGACY_CONFIGMAP_SWEEPER_MAX_DELETES_PER_RUN,
)
from psycopg2.extras import Json

logger = logging.getLogger(__name__)


def _send_stepfunctions_callback(job, status: str, result_location=None, result_files=None, error_message=None):
    scenario = job.get("scenario_data") or {}
    if isinstance(scenario, str):
        try:
            scenario = json.loads(scenario)
        except Exception:
            scenario = {}
    task_token = scenario.get("task_token")
    if not task_token:
        return
    if hasattr(result_files, "adapted"):
        result_files = result_files.adapted
    client_sfn = boto3.client("stepfunctions", region_name=AWS_REGION or os.getenv("AWS_REGION"))
    try:
        if status == "SUCCEEDED":
            output = {
                "job_id": str(job["job_id"]),
                "status": status,
                "result_location": result_location,
                "result_files": result_files if isinstance(result_files, dict) else {},
            }
            client_sfn.send_task_success(taskToken=task_token, output=json.dumps(output))
        else:
            client_sfn.send_task_failure(
                taskToken=task_token,
                error="SUMOK8JobFailed",
                cause=(error_message or "SUMO-K8 job failed")[:32768],
            )
        logger.info(f"StepFunctions callback sent for job {job['job_id']} ({status})")
    except Exception as e:
        logger.error(f"Failed StepFunctions callback for job {job['job_id']}: {e}")

def _extract_failure_info(namespace: str, k8s_job_name: str):
    """Best-effort failure diagnostics to persist in DB."""
    info = {}
    try:
        pods = k8s_core.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"job-name={k8s_job_name}",
        )
        if not pods.items:
            return info
        pod = pods.items[0]
        pod_name = pod.metadata.name
        info["pod_name"] = pod_name

        # Capture first terminated state from main container, if present.
        if pod.status and pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                term = cs.state.terminated if cs.state else None
                if term:
                    if term.reason:
                        info["pod_reason"] = term.reason
                    if term.message:
                        info["pod_message"] = term.message[:2000]
                    break

        try:
            logs = k8s_core.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                tail_lines=120,
            )
            if logs:
                info["log_tail"] = logs[-4000:]
        except Exception:
            pass
    except Exception:
        pass
    return info


def _job_pod_phase_running(namespace: str, k8s_job_name: str) -> bool:
    """True if any Job pod exists and is in phase Running (not Pending/Succeeded/Failed)."""
    try:
        pods = k8s_core.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"job-name={k8s_job_name}",
        )
        for pod in pods.items:
            if pod.status and pod.status.phase == "Running":
                return True
        return False
    except Exception:
        return False


def sync_job_status():
    """Background reconciler to sync K8s job status with database"""
    while True:
        if not k8s_available:
            time.sleep(30)
            continue
            
        try:
            dispatch_queued_jobs()
            with get_db() as conn:
                cur = conn.cursor()
                storage_type = detect_storage_type()
                # First, backfill missing timestamps for completed jobs
                cur.execute(
                    """SELECT job_id, k8s_job_name, k8s_namespace, status, started_at, finished_at
                       FROM jobs 
                       WHERE status IN ('SUCCEEDED', 'FAILED') 
                       AND (started_at IS NULL OR finished_at IS NULL)
                       ORDER BY submitted_at DESC
                       LIMIT 100"""
                )
                completed_jobs = cur.fetchall()
                for job in completed_jobs:
                    try:
                        k8s_job = k8s_batch.read_namespaced_job(job['k8s_job_name'], job['k8s_namespace'])
                        update_cur = conn.cursor()
                        updates = []
                        params = []
                        
                        if job['status'] in ('SUCCEEDED', 'FAILED') and not job['finished_at']:
                            updates.append("finished_at = NOW()")
                        if not job['started_at']:
                            if k8s_job.status.start_time:
                                updates.append("started_at = %s")
                                params.append(k8s_job.status.start_time.replace(tzinfo=None))
                            else:
                                # Use submitted_at as fallback if K8s start_time not available
                                updates.append("started_at = COALESCE(started_at, submitted_at)")
                        
                        if updates:
                            params.append(job['job_id'])
                            update_cur.execute(
                                f"UPDATE jobs SET {', '.join(updates)} WHERE job_id = %s",
                                tuple(params)
                            )
                            conn.commit()
                            logger.info(f"Backfilled timestamps for job {job['job_id']}")
                    except client.exceptions.ApiException as e:
                        if e.status == 404:
                            # Job was deleted, use submitted_at as started_at fallback
                            update_cur = conn.cursor()
                            updates = []
                            if not job['finished_at']:
                                updates.append("finished_at = NOW()")
                            if not job['started_at']:
                                updates.append("started_at = COALESCE(started_at, submitted_at)")
                            if updates:
                                update_cur.execute(
                                    f"UPDATE jobs SET {', '.join(updates)} WHERE job_id = %s",
                                    (job['job_id'],)
                                )
                                conn.commit()
                                logger.info(f"Backfilled timestamps for deleted job {job['job_id']}")
                        else:
                            logger.debug(f"Could not backfill timestamps for {job['job_id']}: {e}")
                    except Exception as e:
                        logger.debug(f"Could not backfill timestamps for {job['job_id']}: {e}")
                
                # Backfill result_location for completed jobs missing it
                cur.execute(
                    """SELECT job_id, k8s_job_name, k8s_namespace, status, tenant_id
                       FROM jobs 
                       WHERE status = 'SUCCEEDED'
                       AND result_location IS NULL
                       ORDER BY submitted_at DESC
                       LIMIT 50"""
                )
                missing_results = cur.fetchall()
                for job in missing_results:
                    try:
                        storage_info = get_result_storage_info(str(job['job_id']), job['k8s_namespace'], storage_type)
                        
                        if job['status'] == 'SUCCEEDED':
                            # Keep reconciler lightweight: avoid listing all S3 files here.
                            # The results API can still use prefix-based discovery if needed.
                            if storage_type == "s3":
                                prefix = storage_info.get("prefix", "")
                                result_location = prefix
                                result_files = Json({
                                    "storage_type": "s3",
                                    "uploaded": True,
                                    "prefix": prefix,
                                })
                            else:
                                result_location = storage_info.get("path", "")
                                result_files = None
                        else:
                            result_location = None
                            result_files = None
                        
                        update_cur = conn.cursor()
                        update_cur.execute(
                            """UPDATE jobs 
                               SET result_location = %s, result_files = %s
                               WHERE job_id = %s""",
                            (result_location, result_files, job['job_id'])
                        )
                        conn.commit()
                        logger.info(f"Backfilled result_location for job {job['job_id']}")
                    except Exception as e:
                        logger.debug(f"Could not backfill result_location for {job['job_id']}: {e}")
                
                # Check for jobs with missing result_files and backfill from S3
                cur.execute(
                    """SELECT job_id, k8s_namespace, tenant_id
                       FROM jobs 
                       WHERE status = 'SUCCEEDED' 
                       AND result_files IS NULL
                       AND result_location IS NOT NULL
                       ORDER BY submitted_at DESC
                       LIMIT 50"""
                )
                pending_results = cur.fetchall()
                for job in pending_results:
                    try:
                        if storage_type == "s3":
                            storage_info = get_result_storage_info(str(job['job_id']), job['k8s_namespace'], storage_type)
                            prefix = storage_info.get("prefix", "")
                            result_files = Json({
                                "storage_type": "s3",
                                "uploaded": True,
                                "prefix": prefix,
                            })
                            update_cur = conn.cursor()
                            update_cur.execute(
                                """UPDATE jobs SET result_files = %s WHERE job_id = %s""",
                                (result_files, job['job_id'])
                            )
                            conn.commit()
                            logger.info(f"Backfilled result_files for job {job['job_id']}")
                    except Exception as e:
                        logger.debug(f"Could not backfill result_files for {job['job_id']}: {e}")

                # Repair jobs incorrectly marked FAILED when K8s Job was already GC'd
                # but direct S3 upload completed successfully.
                if storage_type == "s3":
                    cur.execute(
                        """SELECT job_id, k8s_namespace
                           FROM jobs
                           WHERE status = 'FAILED'
                           AND result_location IS NULL
                           ORDER BY submitted_at DESC
                           LIMIT 50"""
                    )
                    failed_jobs = cur.fetchall()
                    for job in failed_jobs:
                        try:
                            storage_info = get_result_storage_info(str(job['job_id']), job['k8s_namespace'], storage_type)
                            prefix = storage_info.get("prefix", "")
                            if not prefix or not s3_prefix_has_files(prefix):
                                continue
                            update_cur = conn.cursor()
                            update_cur.execute(
                                """UPDATE jobs
                                   SET status = 'SUCCEEDED',
                                       result_location = %s,
                                       result_files = %s,
                                       finished_at = COALESCE(finished_at, NOW()),
                                       started_at = COALESCE(started_at, NOW())
                                   WHERE job_id = %s""",
                                (
                                    prefix,
                                    Json({
                                        "storage_type": "s3",
                                        "uploaded": True,
                                        "prefix": prefix,
                                    }),
                                    job['job_id'],
                                )
                            )
                            conn.commit()
                            logger.info(f"Repaired FAILED->SUCCEEDED for job {job['job_id']} based on S3 results")
                        except Exception as e:
                            logger.debug(f"Could not repair failed job {job['job_id']}: {e}")
                
                # Then process active jobs
                cur.execute(
                    """SELECT job_id, k8s_job_name, k8s_namespace, status, tenant_id, scenario_data
                       FROM jobs
                       WHERE status IN ('PENDING', 'RUNNING')
                       ORDER BY submitted_at DESC
                       LIMIT 200"""
                )
                jobs = cur.fetchall()
                
                for job in jobs:
                    try:
                        k8s_job = k8s_batch.read_namespaced_job(job['k8s_job_name'], job['k8s_namespace'])
                        new_status = job['status']
                        
                        if k8s_job.status.conditions:
                            for cond in k8s_job.status.conditions:
                                if cond.type == "Failed" and cond.status == "True":
                                    new_status = "FAILED"
                                    break
                                elif cond.type == "Complete" and cond.status == "True":
                                    new_status = "SUCCEEDED"
                                    break

                        running_pod = _job_pod_phase_running(
                            job["k8s_namespace"], job["k8s_job_name"]
                        )
                        # Job.status.active counts uncompleted pods including Pending; use pod phase Running.
                        if new_status == "PENDING" and running_pod:
                            new_status = "RUNNING"
                        elif new_status == "RUNNING" and not running_pod:
                            new_status = "PENDING"

                        if new_status != job['status']:
                            update_cur = conn.cursor()
                            if new_status == "RUNNING":
                                update_cur.execute(
                                    "UPDATE jobs SET status = %s, started_at = NOW() WHERE job_id = %s",
                                    (new_status, job['job_id'])
                                )
                            elif new_status == "PENDING" and job["status"] == "RUNNING":
                                update_cur.execute(
                                    "UPDATE jobs SET status = %s, started_at = NULL WHERE job_id = %s",
                                    (new_status, job['job_id'])
                                )
                            elif new_status in ("SUCCEEDED", "FAILED"):
                                # Store result location info
                                storage_info = get_result_storage_info(str(job['job_id']), job['k8s_namespace'], storage_type)
                                
                                if new_status == "SUCCEEDED":
                                    # Direct S3 upload: keep metadata lightweight in reconciler.
                                    if storage_type == "s3":
                                        prefix = storage_info.get("prefix", "")
                                        result_location = prefix
                                        result_files = Json({
                                            "storage_type": "s3",
                                            "uploaded": True,
                                            "prefix": prefix,
                                        })
                                        logger.info(f"Job {job['job_id']} completed; S3 prefix set")
                                    else:
                                        result_location = storage_info.get("path", "")
                                        result_files = None
                                else:
                                    result_location = None
                                    failure = _extract_failure_info(job['k8s_namespace'], job['k8s_job_name'])
                                    result_files = Json({
                                        "storage_type": storage_type,
                                        "uploaded": False,
                                        "error_message": failure.get("pod_reason", "Job failed"),
                                        "failure": failure,
                                    })
                                
                                update_cur.execute(
                                    """UPDATE jobs 
                                       SET status = %s, 
                                           finished_at = NOW(),
                                           started_at = COALESCE(started_at, NOW()),
                                           result_location = %s,
                                           result_files = %s
                                       WHERE job_id = %s""",
                                    (new_status, result_location, result_files, job['job_id'])
                                )
                                # Schedule ConfigMap cleanup
                                cleanup_configmaps(job['k8s_namespace'], str(job['job_id']), delay_seconds=0)
                                callback_error = None
                                if new_status == "FAILED":
                                    rf = result_files.adapted if hasattr(result_files, "adapted") else result_files
                                    if isinstance(rf, dict):
                                        callback_error = rf.get("error_message")
                                _send_stepfunctions_callback(
                                    job,
                                    new_status,
                                    result_location=result_location,
                                    result_files=result_files,
                                    error_message=callback_error,
                                )
                            else:
                                update_cur.execute(
                                    "UPDATE jobs SET status = %s WHERE job_id = %s",
                                    (new_status, job['job_id'])
                                )
                            conn.commit()
                            logger.info(f"Updated job {job['job_id']} status: {job['status']} -> {new_status}")
                    except client.exceptions.ApiException as e:
                        if e.status == 404:
                            # If the K8s Job has already been deleted but results exist in S3,
                            # preserve correct terminal state as SUCCEEDED.
                            new_status = "FAILED"
                            result_location = None
                            failure = _extract_failure_info(job['k8s_namespace'], job['k8s_job_name'])
                            result_files = Json({
                                "storage_type": storage_type,
                                "uploaded": False,
                                "error_message": failure.get("pod_reason", "K8s job not found after submission"),
                                "failure": failure,
                            })
                            if storage_type == "s3":
                                storage_info = get_result_storage_info(str(job['job_id']), job['k8s_namespace'], storage_type)
                                prefix = storage_info.get("prefix", "")
                                if prefix and s3_prefix_has_files(prefix):
                                    new_status = "SUCCEEDED"
                                    result_location = prefix
                                    result_files = Json({
                                        "storage_type": "s3",
                                        "uploaded": True,
                                        "prefix": prefix,
                                    })

                            update_cur = conn.cursor()
                            update_cur.execute(
                                """UPDATE jobs
                                   SET status = %s,
                                       finished_at = NOW(),
                                       started_at = COALESCE(started_at, NOW()),
                                       result_location = COALESCE(%s, result_location),
                                       result_files = COALESCE(%s, result_files)
                                   WHERE job_id = %s""",
                                (new_status, result_location, result_files, job['job_id'])
                            )
                            conn.commit()
                            callback_error = None
                            if new_status == "FAILED":
                                rf = result_files.adapted if hasattr(result_files, "adapted") else result_files
                                if isinstance(rf, dict):
                                    callback_error = rf.get("error_message")
                            _send_stepfunctions_callback(
                                job,
                                new_status,
                                result_location=result_location,
                                result_files=result_files,
                                error_message=callback_error,
                            )
                            logger.warning(f"Job {job['job_id']} not found in K8s, marked as {new_status}")
                        else:
                            logger.error(f"Failed to sync job {job['job_id']}: {e}")
                    except Exception as e:
                        logger.error(f"Failed to sync job {job['job_id']}: {e}")
        except Exception as e:
            logger.error(f"Reconciler error: {e}")
        
        time.sleep(30)

def cleanup_old_configmaps():
    """Periodic cleanup of orphaned ConfigMaps"""
    from .k8s_client import k8s_core
    
    while True:
        if not k8s_available:
            time.sleep(300)
            continue
        
        try:
            namespaces = k8s_core.list_namespace()
            for ns in namespaces.items:
                if ns.metadata.name.startswith('kube-'):
                    continue
                
                try:
                    # Avoid loading all ConfigMaps (which may include large chunk payloads).
                    # We only need cleanup-marked ConfigMaps.
                    candidates = []
                    continue_token = None
                    while True:
                        configmaps = k8s_core.list_namespaced_config_map(
                            ns.metadata.name,
                            label_selector="cleanup=true",
                            limit=100,
                            _continue=continue_token,
                        )
                        now = datetime.now()
                        for cm in configmaps.items:
                            labels = cm.metadata.labels or {}
                            if labels.get('cleanup') != 'true':
                                continue
                            created_at = cm.metadata.creation_timestamp
                            if not created_at:
                                continue
                            age = now - created_at.replace(tzinfo=None)
                            if age <= timedelta(hours=1):
                                continue
                            job_id = labels.get('job-id')
                            if not job_id:
                                continue
                            candidates.append((cm.metadata.name, job_id))

                        continue_token = configmaps.metadata._continue
                        if not continue_token:
                            break

                    if not candidates:
                        continue

                    job_ids = list({job_id for _, job_id in candidates})
                    existing_job_ids = set()
                    with get_db() as conn:
                        cur = conn.cursor()
                        placeholders = ", ".join(["%s"] * len(job_ids))
                        cur.execute(
                            f"SELECT job_id::text FROM jobs WHERE job_id IN ({placeholders})",
                            tuple(job_ids),
                        )
                        existing_job_ids = {row[0] for row in cur.fetchall()}

                    for cm_name, job_id in candidates:
                        if job_id in existing_job_ids:
                            continue
                        try:
                            k8s_core.delete_namespaced_config_map(cm_name, ns.metadata.name)
                            logger.info(f"Cleaned up orphaned ConfigMap {cm_name}")
                        except client.exceptions.ApiException as e:
                            if e.status != 404:
                                logger.debug(f"Failed deleting ConfigMap {cm_name}: {e}")
                        except Exception as e:
                            logger.debug(f"Failed deleting ConfigMap {cm_name}: {e}")
                except Exception as e:
                    logger.debug(f"Error cleaning ConfigMaps in {ns.metadata.name}: {e}")

            if ENABLE_LEGACY_CONFIGMAP_SWEEPER and LEGACY_CONFIGMAP_SWEEPER_NAMESPACES:
                now = datetime.now()
                min_age = timedelta(hours=LEGACY_CONFIGMAP_SWEEPER_MIN_AGE_HOURS)
                deleted_count = 0
                requested_namespaces = set(LEGACY_CONFIGMAP_SWEEPER_NAMESPACES)
                existing_namespaces = {ns.metadata.name for ns in namespaces.items}

                for ns_name in sorted(requested_namespaces):
                    if deleted_count >= LEGACY_CONFIGMAP_SWEEPER_MAX_DELETES_PER_RUN:
                        break
                    if ns_name not in existing_namespaces:
                        logger.warning(f"Legacy sweeper namespace not found: {ns_name}")
                        continue

                    continue_token = None
                    while deleted_count < LEGACY_CONFIGMAP_SWEEPER_MAX_DELETES_PER_RUN:
                        configmaps = k8s_core.list_namespaced_config_map(
                            ns_name,
                            limit=100,
                            _continue=continue_token,
                        )
                        for cm in configmaps.items:
                            name = cm.metadata.name or ""
                            if not name.startswith(LEGACY_CONFIGMAP_SWEEPER_PREFIX):
                                continue
                            if LEGACY_CONFIGMAP_SWEEPER_NAME_CONTAINS not in name:
                                continue

                            created_at = cm.metadata.creation_timestamp
                            if not created_at:
                                continue
                            age = now - created_at.replace(tzinfo=None)
                            if age < min_age:
                                continue

                            try:
                                k8s_core.delete_namespaced_config_map(name, ns_name)
                                deleted_count += 1
                                logger.info(
                                    "Legacy sweeper deleted ConfigMap %s in %s (age=%s)",
                                    name,
                                    ns_name,
                                    age,
                                )
                            except client.exceptions.ApiException as e:
                                if e.status != 404:
                                    logger.debug(f"Legacy sweeper failed deleting {name} in {ns_name}: {e}")
                            except Exception as e:
                                logger.debug(f"Legacy sweeper failed deleting {name} in {ns_name}: {e}")

                            if deleted_count >= LEGACY_CONFIGMAP_SWEEPER_MAX_DELETES_PER_RUN:
                                break

                        continue_token = configmaps.metadata._continue
                        if not continue_token or deleted_count >= LEGACY_CONFIGMAP_SWEEPER_MAX_DELETES_PER_RUN:
                            break

                if deleted_count:
                    logger.info(
                        "Legacy sweeper deleted %s ConfigMap(s) this cycle (prefix=%s, contains=%s, min_age_hours=%s, namespaces=%s)",
                        deleted_count,
                        LEGACY_CONFIGMAP_SWEEPER_PREFIX,
                        LEGACY_CONFIGMAP_SWEEPER_NAME_CONTAINS,
                        LEGACY_CONFIGMAP_SWEEPER_MIN_AGE_HOURS,
                        ",".join(sorted(requested_namespaces)),
                    )
        except Exception as e:
            logger.error(f"ConfigMap cleanup error: {e}")
        
        time.sleep(300)  # Run every 5 minutes

