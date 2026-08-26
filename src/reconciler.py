"""Background job status reconciler"""
import time
import logging
import os
import json
import ssl
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from kubernetes import client
import boto3
from botocore.exceptions import ClientError
from .database import get_db
from .k8s_client import k8s_available, k8s_batch, k8s_core
from .scaling import cleanup_configmaps
from .jobs import dispatch_queued_jobs
from .reservations import expire_stale_reservations, sync_reservation_statuses
from .storage import detect_storage_type, get_result_storage_info, s3_prefix_has_files
from .cost import refresh_job_estimated_cost
from .config import (
    AWS_REGION,
    ENABLE_LEGACY_CONFIGMAP_SWEEPER,
    LEGACY_CONFIGMAP_SWEEPER_NAMESPACES,
    LEGACY_CONFIGMAP_SWEEPER_PREFIX,
    LEGACY_CONFIGMAP_SWEEPER_NAME_CONTAINS,
    LEGACY_CONFIGMAP_SWEEPER_MIN_AGE_HOURS,
    LEGACY_CONFIGMAP_SWEEPER_MAX_DELETES_PER_RUN,
    STALE_ACTIVE_JOB_HOURS,
    GHOST_IDLE_GRACE_SECONDS,
)
from psycopg2.extras import Json

logger = logging.getLogger(__name__)
_LAST_PROGRESS_SENT: dict[str, float] = {}


def _send_stepfunctions_callback(job, status: str, result_location=None, result_files=None, error_message=None):
    scenario = job.get("scenario_data") or {}
    if isinstance(scenario, str):
        try:
            scenario = json.loads(scenario)
        except Exception:
            scenario = {}
    task_token = scenario.get("task_token")
    if not task_token:
        return "no_token"
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
        logger.debug("StepFunctions callback sent for job %s (%s)", job["job_id"], status)
        return "sent"
    except ClientError as e:
        code = (e.response or {}).get("Error", {}).get("Code", "")
        if code in ("TaskTimedOut", "TaskDoesNotExist", "InvalidToken"):
            logger.info(
                "Skipping stale StepFunctions callback for job %s: %s",
                job["job_id"],
                code,
            )
            return "stale_token"
        logger.warning("Failed StepFunctions callback for job %s: %s", job["job_id"], e)
        return "error"
    except Exception as e:
        logger.warning("Failed StepFunctions callback for job %s: %s", job["job_id"], e)
        return "error"


def _clear_task_token(cur, job_id):
    cur.execute(
        """
        UPDATE jobs
        SET scenario_data =
            (
                (COALESCE(scenario_data::jsonb, '{}'::jsonb) - 'task_token')
                || '{"task_token_stale": true}'::jsonb
            )
        WHERE job_id = %s
        """,
        (job_id,),
    )

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


def _classify_k8s_workload(namespace: str, k8s_job_name: str) -> str:
    """
    Classify whether a DB-active job has real cluster work behind it.
    Returns: live | terminal | idle | missing | unknown
    """
    if not k8s_available:
        return "unknown"
    try:
        k8s_job = k8s_batch.read_namespaced_job(k8s_job_name, namespace)
    except client.exceptions.ApiException as e:
        if e.status == 404:
            return "missing"
        logger.debug("Could not read K8s job %s/%s: %s", namespace, k8s_job_name, e)
        return "unknown"
    except Exception as e:
        logger.debug("Could not read K8s job %s/%s: %s", namespace, k8s_job_name, e)
        return "unknown"

    status = k8s_job.status
    if status:
        if status.succeeded or status.failed:
            return "terminal"
        if status.active:
            return "live"
    if status and status.conditions:
        for cond in status.conditions:
            if cond.type in ("Complete", "Failed") and cond.status == "True":
                return "terminal"
    if _job_pod_phase_running(namespace, k8s_job_name):
        return "live"
    return "idle"


def _eligible_for_s3_repair(workload: str, has_files: bool) -> bool:
    """Whether a DB-active job may be finalized to SUCCEEDED purely from S3 results.

    Only when results exist AND the pod is gone/terminal -- never while it is still
    LIVE. A running pod mid-upload has only its first (smallest) files in S3 (summary
    + inputs upload before the large routes/fcd), so s3_prefix_has_files() goes True
    long before the upload completes; finalizing then fires SendTaskSuccess early and
    the downstream per-seed sync copies a PARTIAL result set (the dropped-seed bug).
    A live job must be finalized by the active-jobs loop on real pod-exit, where the
    run script's `set -e` + sequential upload_results.py guarantees completeness.
    'unknown' (K8s API read failed) is excluded: we cannot confirm the pod is gone.
    """
    return has_files and workload in ("missing", "idle", "terminal")


def _terminal_status_from_k8s_job(k8s_job) -> str:
    if k8s_job.status and k8s_job.status.succeeded:
        return "SUCCEEDED"
    if k8s_job.status and k8s_job.status.failed:
        return "FAILED"
    if k8s_job.status and k8s_job.status.conditions:
        for cond in k8s_job.status.conditions:
            if cond.type == "Complete" and cond.status == "True":
                return "SUCCEEDED"
            if cond.type == "Failed" and cond.status == "True":
                return "FAILED"
    return "FAILED"


def _job_active_age_seconds(job) -> float:
    ts = job.get("started_at") or job.get("submitted_at")
    if not ts:
        return 0.0
    if getattr(ts, "tzinfo", None):
        ts = ts.replace(tzinfo=None)
    return max(0.0, (datetime.now() - ts).total_seconds())


def _terminal_from_s3(storage_type: str, job) -> tuple[str, str | None, object | None]:
    """If results exist in object storage, treat job as SUCCEEDED."""
    if storage_type != "s3":
        return "FAILED", None, None
    storage_info = get_result_storage_info(str(job["job_id"]), job["k8s_namespace"], storage_type)
    prefix = storage_info.get("prefix", "")
    if prefix and s3_prefix_has_files(prefix):
        return (
            "SUCCEEDED",
            prefix,
            Json({"storage_type": "s3", "uploaded": True, "prefix": prefix}),
        )
    return "FAILED", None, None


def _build_terminal_result(
    job,
    storage_type: str,
    new_status: str,
    error_message: str | None = None,
):
    job_id_str = str(job["job_id"])
    if new_status == "SUCCEEDED":
        storage_info = get_result_storage_info(job_id_str, job["k8s_namespace"], storage_type)
        if storage_type == "s3":
            prefix = storage_info.get("prefix", "")
            return prefix, Json({"storage_type": "s3", "uploaded": True, "prefix": prefix})
        return storage_info.get("path", ""), None
    failure = _extract_failure_info(job["k8s_namespace"], job["k8s_job_name"])
    return None, Json({
        "storage_type": storage_type,
        "uploaded": False,
        "error_message": error_message or failure.get("pod_reason", "Job failed"),
        "failure": failure,
    })


def _finalize_job_terminal(cur, conn, job, new_status: str, storage_type: str, error_message: str | None = None):
    """Persist terminal status, release slot, commit, then Step Functions callback."""
    job_id_str = str(job["job_id"])
    result_location, result_files = _build_terminal_result(
        job, storage_type, new_status, error_message=error_message
    )

    cur.execute(
        """UPDATE jobs
           SET status = %s,
               occupies_slot = FALSE,
               finished_at = NOW(),
               started_at = COALESCE(started_at, submitted_at),
               result_location = COALESCE(%s, result_location),
               result_files = COALESCE(%s, result_files)
           WHERE job_id = %s""",
        (new_status, result_location, result_files, job["job_id"]),
    )
    if cur.rowcount < 1:
        logger.error("Finalize job %s: UPDATE matched no rows", job_id_str)
        return
    conn.commit()
    try:
        refresh_job_estimated_cost(cur, job["job_id"])
        conn.commit()
    except Exception as exc:
        logger.debug("Cost refresh after finalize skipped for %s: %s", job_id_str, exc)
        try:
            conn.rollback()
        except Exception:
            pass
    cleanup_configmaps(job["k8s_namespace"], job_id_str, delay_seconds=0)

    callback_error = None
    if new_status == "FAILED":
        rf = result_files.adapted if hasattr(result_files, "adapted") else result_files
        if isinstance(rf, dict):
            callback_error = rf.get("error_message")
    callback_state = _send_stepfunctions_callback(
        job,
        new_status,
        result_location=result_location,
        result_files=result_files,
        error_message=callback_error,
    )
    if callback_state == "stale_token":
        with get_db() as conn2:
            cur2 = conn2.cursor()
            _clear_task_token(cur2, job["job_id"])
    _LAST_PROGRESS_SENT.pop(job_id_str, None)
    logger.info("Finalized job %s as %s", job_id_str, new_status)


def _reconcile_non_live_active_job(cur, conn, job, storage_type: str, workload: str, reason: str):
    """Terminalize a PENDING/RUNNING row that has no live cluster work."""
    if workload == "unknown":
        return False
    if workload == "live":
        return False

    if workload == "terminal":
        k8s_job = k8s_batch.read_namespaced_job(job["k8s_job_name"], job["k8s_namespace"])
        new_status = _terminal_status_from_k8s_job(k8s_job)
        _finalize_job_terminal(cur, conn, job, new_status, storage_type)
        logger.warning("Synced non-live job %s from K8s terminal (%s)", job["job_id"], reason)
        return True

    new_status, _, _ = _terminal_from_s3(storage_type, job)
    error_message = None
    if new_status == "FAILED":
        error_message = f"{reason} (k8s_state={workload})"
    _finalize_job_terminal(cur, conn, job, new_status, storage_type, error_message=error_message)
    logger.warning(
        "Reconciled non-live job %s -> %s (%s, k8s_state=%s)",
        job["job_id"],
        new_status,
        reason,
        workload,
    )
    return True


def reconcile_ghost_active_jobs():
    """
    Every cycle: fix PENDING/RUNNING rows with no live K8s workload so they do not block the queue.
    """
    if not k8s_available:
        return

    storage_type = detect_storage_type()
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT job_id, k8s_job_name, k8s_namespace, status, tenant_id, scenario_data,
                          cpu_request, memory_gi, submitted_at, started_at
                   FROM jobs
                   WHERE status IN ('PENDING', 'RUNNING')
                   ORDER BY submitted_at ASC
                   LIMIT 100"""
            )
            for job in cur.fetchall():
                workload = _classify_k8s_workload(job["k8s_namespace"], job["k8s_job_name"])
                if workload in ("live", "unknown"):
                    continue
                if workload == "idle":
                    s3_status, _, _ = _terminal_from_s3(storage_type, job)
                    if s3_status != "SUCCEEDED" and _job_active_age_seconds(job) < GHOST_IDLE_GRACE_SECONDS:
                        continue
                try:
                    with get_db() as job_conn:
                        job_cur = job_conn.cursor()
                        _reconcile_non_live_active_job(
                            job_cur,
                            job_conn,
                            job,
                            storage_type,
                            workload,
                            "ghost_reconcile",
                        )
                except Exception as e:
                    logger.error("Ghost reconcile failed for %s: %s", job["job_id"], e)
    except Exception as e:
        logger.error("Ghost active job sweep error: %s", e)


def expire_stale_active_jobs():
    """
    Fail PENDING/RUNNING jobs that have held a concurrency slot for STALE_ACTIVE_JOB_HOURS
    without a live Kubernetes workload (no Job, idle Job, or completed Job not synced).
    """
    if not k8s_available or STALE_ACTIVE_JOB_HOURS <= 0:
        return

    storage_type = detect_storage_type()
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT job_id, k8s_job_name, k8s_namespace, status, tenant_id, scenario_data,
                          cpu_request, memory_gi, submitted_at, started_at
                   FROM jobs
                   WHERE status IN ('PENDING', 'RUNNING')
                     AND COALESCE(started_at, submitted_at)
                         < NOW() - (%s * INTERVAL '1 hour')
                   ORDER BY COALESCE(started_at, submitted_at) ASC
                   LIMIT 50""",
                (STALE_ACTIVE_JOB_HOURS,),
            )
            stale_jobs = cur.fetchall()

            for job in stale_jobs:
                workload = _classify_k8s_workload(job["k8s_namespace"], job["k8s_job_name"])
                reason = (
                    f"Stale {job['status']} job cleared after {STALE_ACTIVE_JOB_HOURS}h "
                    "with no live Kubernetes workload"
                )
                try:
                    _reconcile_non_live_active_job(
                        cur, conn, job, storage_type, workload, reason
                    )
                except Exception as e:
                    conn.rollback()
                    logger.error("Failed expiring stale job %s: %s", job["job_id"], e)
    except Exception as e:
        logger.error("Stale active job sweep error: %s", e)


def _to_int_or_none(value):
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _extract_latest_sumo_step(namespace: str, k8s_job_name: str):
    """Read latest numeric SUMO step from pod JSON logs (event=sumo_progress)."""
    try:
        pods = k8s_core.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"job-name={k8s_job_name}",
        )
        if not pods.items:
            return None
        pod_name = pods.items[0].metadata.name
        logs = k8s_core.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            tail_lines=400,
        )
        latest_step = None
        for line in (logs or "").splitlines():
            text = (line or "").strip()
            if not text.startswith("{"):
                continue
            try:
                payload = json.loads(text)
            except Exception:
                continue
            if payload.get("event") != "sumo_progress":
                continue
            try:
                step = float(payload.get("step"))
            except Exception:
                continue
            if latest_step is None or step > latest_step:
                latest_step = step
        return latest_step
    except Exception:
        return None


def _format_progress_percent(percent: float) -> str:
    if float(percent).is_integer():
        return str(int(percent))
    return f"{percent:.2f}".rstrip("0").rstrip(".")


def _send_progress_webhook(job, percent: float, step: float) -> bool:
    scenario = job.get("scenario_data") or {}
    if isinstance(scenario, str):
        try:
            scenario = json.loads(scenario)
        except Exception:
            scenario = {}
    webhook_url = (scenario.get("progress_webhook_url") or "").strip()
    simulation_id = scenario.get("progress_simulation_id")
    if not webhook_url or simulation_id is None:
        logger.debug(
            "Progress webhook config missing for job %s (url=%s, simulation_id=%s)",
            job.get("job_id"),
            bool(webhook_url),
            simulation_id,
        )
        return False

    payload = {
        "simulation_id": int(simulation_id),
        "status_type": "progress",
        "simulation_status": "Running",
        "advanced_status": "Not Started",
        "message": f"Simulation progress: {_format_progress_percent(percent)}% (step {int(step)})",
    }
    token = (os.getenv("WEBHOOK_SHARED_TOKEN") or "").strip()
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=webhook_url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Webhook-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=10):
            return True
    except urllib.error.HTTPError as e:
        logger.warning("Progress webhook HTTP %s for job %s", e.code, job.get("job_id"))
        return False
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        allow_insecure = str(os.getenv("PROGRESS_WEBHOOK_INSECURE_TLS", "true")).lower() in (
            "1",
            "true",
            "yes",
            "y",
        )
        if allow_insecure and isinstance(reason, ssl.SSLCertVerificationError):
            try:
                with urllib.request.urlopen(
                    req,
                    timeout=10,
                    context=ssl._create_unverified_context(),
                ):
                    logger.warning(
                        "Progress webhook sent insecurely for job %s after cert verification failure",
                        job.get("job_id"),
                    )
                    return True
            except Exception as retry_exc:
                logger.warning(
                    "Progress webhook insecure retry failed for job %s: %s",
                    job.get("job_id"),
                    retry_exc,
                )
                return False
        logger.warning("Progress webhook failed for job %s: %s", job.get("job_id"), e)
        return False
    except Exception as e:
        logger.warning("Progress webhook failed for job %s: %s", job.get("job_id"), e)
        return False


def _send_terminal_progress_if_needed(job, scenario: dict, new_status: str, job_id_str: str) -> None:
    """Emit a final progress update for fast jobs that finish between poll cycles."""
    start_sec = _to_int_or_none(scenario.get("progress_start_sec"))
    end_sec = _to_int_or_none(scenario.get("progress_end_sec"))
    if start_sec is None or end_sec is None or end_sec <= start_sec:
        return

    step = _extract_latest_sumo_step(job["k8s_namespace"], job["k8s_job_name"])
    if step is None:
        if new_status != "SUCCEEDED":
            return
        step = float(end_sec)

    raw = ((float(step) - float(start_sec)) * 100.0) / float(end_sec - start_sec)
    percent = max(0.0, min(100.0, round(raw, 2)))
    if new_status == "SUCCEEDED":
        percent = 100.0
        step = max(float(step), float(end_sec))

    last_sent = _LAST_PROGRESS_SENT.get(job_id_str, -1.0)
    if percent > last_sent:
        if _send_progress_webhook(job, percent, step):
            _LAST_PROGRESS_SENT[job_id_str] = percent


def sync_job_status():
    """Background reconciler to sync K8s job status with database"""
    while True:
        if not k8s_available:
            time.sleep(30)
            continue
            
        try:
            reconcile_ghost_active_jobs()
            expire_stale_active_jobs()
            dispatch_queued_jobs()
            try:
                expire_stale_reservations()
                sync_reservation_statuses()
            except Exception as res_exc:
                logger.warning("Reservation maintenance skipped: %s", res_exc)
            with get_db() as conn:
                cur = conn.cursor()
                storage_type = detect_storage_type()
                # First, backfill missing timestamps for completed jobs
                cur.execute(
                    """SELECT job_id, k8s_job_name, k8s_namespace, status, started_at, finished_at,
                              cpu_request, memory_gi
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
                            refresh_job_estimated_cost(update_cur, job["job_id"])
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
                                refresh_job_estimated_cost(update_cur, job["job_id"])
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

                # Repair terminal status from S3 when K8s is gone or DB drifted.
                if storage_type == "s3":
                    cur.execute(
                        """SELECT job_id, k8s_namespace, k8s_job_name, status, tenant_id,
                                  scenario_data, cpu_request, memory_gi
                           FROM jobs
                           WHERE status IN ('FAILED', 'RUNNING', 'PENDING')
                           AND result_location IS NULL
                           ORDER BY submitted_at DESC
                           LIMIT 50"""
                    )
                    failed_jobs = cur.fetchall()
                    for job in failed_jobs:
                        try:
                            storage_info = get_result_storage_info(str(job['job_id']), job['k8s_namespace'], storage_type)
                            prefix = storage_info.get("prefix", "")
                            has_files = bool(prefix) and s3_prefix_has_files(prefix)
                            # Never finalize a job whose pod is still LIVE (e.g. mid-upload):
                            # S3 holds only its first/smallest files then (summary + inputs),
                            # so repairing fires SendTaskSuccess early and the downstream
                            # per-seed sync copies a PARTIAL result set -> ConvertRoutes 404
                            # (the dropped-seed bug). Let the active-jobs loop finalize live
                            # jobs on real pod-exit, when the upload is provably complete.
                            workload = _classify_k8s_workload(job["k8s_namespace"], job["k8s_job_name"])
                            if not _eligible_for_s3_repair(workload, has_files):
                                continue
                            update_cur = conn.cursor()
                            update_cur.execute(
                                """UPDATE jobs
                                   SET status = 'SUCCEEDED',
                                       occupies_slot = FALSE,
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
                            refresh_job_estimated_cost(update_cur, job["job_id"])
                            conn.commit()
                            _send_stepfunctions_callback(
                                job,
                                "SUCCEEDED",
                                result_location=prefix,
                                result_files=Json({
                                    "storage_type": "s3",
                                    "uploaded": True,
                                    "prefix": prefix,
                                }),
                            )
                            logger.info(
                                "Repaired %s->SUCCEEDED for job %s based on S3 results",
                                job["status"],
                                job["job_id"],
                            )
                        except Exception as e:
                            logger.debug(f"Could not repair failed job {job['job_id']}: {e}")
                
                # Then process active jobs
                cur.execute(
                    """SELECT job_id, k8s_job_name, k8s_namespace, status, tenant_id, scenario_data,
                              cpu_request, memory_gi, occupies_slot
                       FROM jobs
                       WHERE status IN ('PENDING', 'RUNNING')
                       ORDER BY submitted_at DESC
                       LIMIT 200"""
                )
                jobs = cur.fetchall()
                
                for job in jobs:
                    try:
                        job_id_str = str(job["job_id"])
                        scenario = job.get("scenario_data") or {}
                        if isinstance(scenario, str):
                            try:
                                scenario = json.loads(scenario)
                            except Exception:
                                scenario = {}
                        k8s_job = k8s_batch.read_namespaced_job(job['k8s_job_name'], job['k8s_namespace'])
                        new_status = job['status']
                        workload = _classify_k8s_workload(
                            job["k8s_namespace"], job["k8s_job_name"]
                        )

                        if workload == "terminal":
                            new_status = _terminal_status_from_k8s_job(k8s_job)
                        elif k8s_job.status.conditions:
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
                        if new_status in ("PENDING", "RUNNING") and workload == "live":
                            new_status = "RUNNING"
                        elif new_status == "PENDING" and running_pod:
                            new_status = "RUNNING"

                        if (new_status == "RUNNING" or (job["status"] == "RUNNING" and running_pod)):
                            start_sec = _to_int_or_none(scenario.get("progress_start_sec"))
                            end_sec = _to_int_or_none(scenario.get("progress_end_sec"))
                            if start_sec is not None and end_sec is not None and end_sec > start_sec:
                                step = _extract_latest_sumo_step(job["k8s_namespace"], job["k8s_job_name"])
                                if step is not None:
                                    raw = ((float(step) - float(start_sec)) * 100.0) / float(end_sec - start_sec)
                                    percent = max(0.0, min(100.0, round(raw, 2)))
                                    last_sent = _LAST_PROGRESS_SENT.get(job_id_str, -1.0)
                                    if percent > last_sent:
                                        if _send_progress_webhook(job, percent, step):
                                            _LAST_PROGRESS_SENT[job_id_str] = percent
                                else:
                                    # Ensure at least one running progress webhook is emitted.
                                    if _LAST_PROGRESS_SENT.get(job_id_str, -1) < 0:
                                        if _send_progress_webhook(job, 0, float(start_sec)):
                                            _LAST_PROGRESS_SENT[job_id_str] = 0

                        if new_status != job['status'] or (
                            new_status == "RUNNING" and not job.get("occupies_slot")
                        ):
                            update_cur = conn.cursor()
                            if new_status == "RUNNING":
                                update_cur.execute(
                                    """UPDATE jobs SET status = %s, occupies_slot = TRUE,
                                       started_at = COALESCE(started_at, NOW()) WHERE job_id = %s""",
                                    (new_status, job['job_id'])
                                )
                                conn.commit()
                            elif new_status in ("SUCCEEDED", "FAILED"):
                                _send_terminal_progress_if_needed(job, scenario, new_status, job_id_str)
                                failure = _extract_failure_info(
                                    job['k8s_namespace'], job['k8s_job_name']
                                ) if new_status == "FAILED" else None
                                err = (failure or {}).get("pod_reason", "Job failed") if failure else None
                                _finalize_job_terminal(
                                    update_cur, conn, job, new_status, storage_type, error_message=err
                                )
                            else:
                                update_cur.execute(
                                    "UPDATE jobs SET status = %s WHERE job_id = %s",
                                    (new_status, job['job_id'])
                                )
                                conn.commit()
                            logger.info(
                                "Updated job %s status: %s -> %s",
                                job['job_id'],
                                job['status'],
                                new_status,
                            )
                    except client.exceptions.ApiException as e:
                        if e.status == 404:
                            update_cur = conn.cursor()
                            new_status, _, _ = _terminal_from_s3(storage_type, job)
                            err = "K8s job not found after submission"
                            if new_status == "FAILED":
                                _finalize_job_terminal(
                                    update_cur, conn, job, "FAILED", storage_type, error_message=err
                                )
                            else:
                                _reconcile_non_live_active_job(
                                    update_cur,
                                    conn,
                                    job,
                                    storage_type,
                                    "missing",
                                    "k8s_job_not_found",
                                )
                            logger.debug(
                                "Job %s not found in K8s, reconciled as %s",
                                job['job_id'],
                                new_status,
                            )
                        else:
                            try:
                                conn.rollback()
                            except Exception:
                                pass
                            logger.error(f"Failed to sync job {job['job_id']}: {e}")
                    except Exception as e:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
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
                        existing_job_ids = {row["job_id"] for row in cur.fetchall()}

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

