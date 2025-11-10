"""Background job status reconciler"""
import time
import logging
from datetime import datetime, timedelta
from kubernetes import client
from .database import get_db
from .k8s_client import k8s_available, k8s_batch, k8s_core
from .scaling import cleanup_configmaps
from .storage import detect_storage_type, get_result_storage_info, upload_results_from_pvc
from psycopg2.extras import Json

logger = logging.getLogger(__name__)

def sync_job_status():
    """Background reconciler to sync K8s job status with database"""
    while True:
        if not k8s_available:
            time.sleep(30)
            continue
            
        try:
            with get_db() as conn:
                cur = conn.cursor()
                # First, backfill missing timestamps for completed jobs
                cur.execute(
                    """SELECT job_id, k8s_job_name, k8s_namespace, status, started_at, finished_at
                       FROM jobs 
                       WHERE status IN ('SUCCEEDED', 'FAILED') 
                       AND (started_at IS NULL OR finished_at IS NULL)"""
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
                       WHERE status IN ('SUCCEEDED', 'FAILED') 
                       AND result_location IS NULL"""
                )
                missing_results = cur.fetchall()
                for job in missing_results:
                    try:
                        storage_type = detect_storage_type()
                        storage_info = get_result_storage_info(str(job['job_id']), job['k8s_namespace'], storage_type)
                        
                        if job['status'] == 'SUCCEEDED':
                            # Trigger upload for cloud storage if not already done
                            if storage_type in ("s3", "gcs", "azure"):
                                upload_result = upload_results_from_pvc(
                                    str(job['job_id']), 
                                    job['tenant_id'], 
                                    job['k8s_namespace'], 
                                    storage_type
                                )
                                result_location = storage_info.get("prefix", "")
                                result_files = None
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
                
                # Check for completed upload jobs and update result_files
                cur.execute(
                    """SELECT job_id, k8s_namespace, tenant_id, result_files
                       FROM jobs 
                       WHERE status = 'SUCCEEDED' 
                       AND result_files IS NULL
                       AND result_location IS NOT NULL
                       AND result_location LIKE '%results/%'"""
                )
                pending_uploads = cur.fetchall()
                for job in pending_uploads:
                    try:
                        # Check if upload job completed
                        upload_job_name = f"upload-{str(job['job_id'])[:8]}"
                        upload_job = k8s_batch.read_namespaced_job(upload_job_name, job['k8s_namespace'])
                        
                        if upload_job.status.succeeded:
                            # Upload completed, get storage info
                            storage_type = detect_storage_type()
                            storage_info = get_result_storage_info(str(job['job_id']), job['k8s_namespace'], storage_type)
                            # For now, just mark as uploaded (files list would need to be extracted from upload job logs)
                            result_files = Json({
                                "storage_type": storage_type,
                                "uploaded": True,
                                "prefix": storage_info.get("prefix", "")
                            })
                            
                            update_cur = conn.cursor()
                            update_cur.execute(
                                """UPDATE jobs 
                                   SET result_files = %s
                                   WHERE job_id = %s""",
                                (result_files, job['job_id'])
                            )
                            conn.commit()
                            logger.info(f"Updated result_files for job {job['job_id']} after upload completion")
                            
                            # Clean up PVC after successful cloud storage upload
                            if storage_type in ("s3", "gcs", "azure"):
                                from .storage import cleanup_pvc_after_upload
                                cleanup_pvc_after_upload(job['k8s_namespace'], str(job['job_id']))
                    except client.exceptions.ApiException as e:
                        if e.status != 404:
                            logger.debug(f"Could not check upload job for {job['job_id']}: {e}")
                    except Exception as e:
                        logger.debug(f"Could not update result_files for {job['job_id']}: {e}")
                
                # Then process active jobs
                cur.execute(
                    "SELECT job_id, k8s_job_name, k8s_namespace, status, tenant_id FROM jobs WHERE status IN ('PENDING', 'RUNNING')"
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
                        
                        if k8s_job.status.active and job['status'] == 'PENDING':
                            new_status = "RUNNING"
                        
                        if new_status != job['status']:
                            update_cur = conn.cursor()
                            if new_status == "RUNNING":
                                update_cur.execute(
                                    "UPDATE jobs SET status = %s, started_at = NOW() WHERE job_id = %s",
                                    (new_status, job['job_id'])
                                )
                            elif new_status in ("SUCCEEDED", "FAILED"):
                                # Set started_at if not already set (job might have completed very quickly)
                                # Store result location info
                                storage_type = detect_storage_type()
                                # Use k8s_namespace (which matches tenant namespace) for storage path
                                storage_info = get_result_storage_info(str(job['job_id']), job['k8s_namespace'], storage_type)
                                
                                if new_status == "SUCCEEDED":
                                    # For cloud storage, trigger upload job
                                    if storage_type in ("s3", "gcs", "azure"):
                                        upload_result = upload_results_from_pvc(
                                            str(job['job_id']), 
                                            job['tenant_id'], 
                                            job['k8s_namespace'], 
                                            storage_type
                                        )
                                        if upload_result:
                                            logger.info(f"Started upload job for {job['job_id']} to {storage_type}")
                                            # Store prefix for now, upload job will update result_files when done
                                            result_location = storage_info.get("prefix", "")
                                            result_files = None  # Will be updated by upload job completion
                                        else:
                                            # Upload failed, fall back to PVC info
                                            result_location = storage_info.get("path", "")
                                            result_files = None
                                    else:
                                        # PVC storage
                                        result_location = storage_info.get("path", "")
                                        result_files = None
                                else:
                                    result_location = None
                                    result_files = None
                                
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
                                cleanup_configmaps(job['k8s_namespace'], str(job['job_id']))
                            else:
                                update_cur.execute(
                                    "UPDATE jobs SET status = %s WHERE job_id = %s",
                                    (new_status, job['job_id'])
                                )
                            conn.commit()
                            logger.info(f"Updated job {job['job_id']} status: {job['status']} -> {new_status}")
                    except client.exceptions.ApiException as e:
                        if e.status == 404:
                            # Job doesn't exist in K8s, mark as failed
                            update_cur = conn.cursor()
                            update_cur.execute(
                                "UPDATE jobs SET status = 'FAILED', finished_at = NOW() WHERE job_id = %s",
                                (job['job_id'],)
                            )
                            conn.commit()
                            logger.warning(f"Job {job['job_id']} not found in K8s, marked as FAILED")
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
                    configmaps = k8s_core.list_namespaced_config_map(ns.metadata.name)
                    for cm in configmaps.items:
                        if cm.metadata.labels and cm.metadata.labels.get('cleanup') == 'true':
                            age = datetime.now() - cm.metadata.creation_timestamp.replace(tzinfo=None)
                            if age > timedelta(hours=1):
                                job_id = cm.metadata.labels.get('job-id')
                                if job_id:
                                    with get_db() as conn:
                                        cur = conn.cursor()
                                        cur.execute("SELECT job_id FROM jobs WHERE job_id = %s", (job_id,))
                                        if not cur.fetchone():
                                            try:
                                                k8s_core.delete_namespaced_config_map(cm.metadata.name, ns.metadata.name)
                                                logger.info(f"Cleaned up orphaned ConfigMap {cm.metadata.name}")
                                            except:
                                                pass
                except Exception as e:
                    logger.debug(f"Error cleaning ConfigMaps in {ns.metadata.name}: {e}")
        except Exception as e:
            logger.error(f"ConfigMap cleanup error: {e}")
        
        time.sleep(300)  # Run every 5 minutes

