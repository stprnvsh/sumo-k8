"""Result storage management - PVC for local clusters, object storage for cloud"""
import os
import logging
import json
from typing import Optional, Dict, List
from kubernetes import client
from .k8s_client import k8s_available, k8s_core
from .config import (
    RESULT_STORAGE_TYPE, RESULT_STORAGE_SIZE_GI,
    S3_BUCKET, S3_REGION, GCS_BUCKET,
    AZURE_STORAGE_ACCOUNT, AZURE_CONTAINER
)
from .k8s_client import k8s_batch

logger = logging.getLogger(__name__)

def detect_storage_type() -> str:
    """Detect storage type based on cluster and environment"""
    if RESULT_STORAGE_TYPE != "auto":
        return RESULT_STORAGE_TYPE
    
    if not k8s_available:
        return "pvc"
    
    try:
        # Check cluster context/provider
        nodes = k8s_core.list_node()
        if not nodes.items:
            return "pvc"
        
        # Check node labels/provider IDs
        for node in nodes.items:
            labels = node.metadata.labels or {}
            provider_id = labels.get("kubernetes.io/hostname", "")
            
            # GKE detection
            if "gke" in provider_id.lower() or any("gke" in k.lower() for k in labels.keys()):
                if GCS_BUCKET:
                    return "gcs"
                return "pvc"
            
            # EKS detection
            if "eks" in provider_id.lower() or "ec2" in provider_id.lower():
                if S3_BUCKET:
                    return "s3"
                return "pvc"
            
            # AKS detection
            if "aks" in provider_id.lower() or any("azure" in k.lower() for k in labels.keys()):
                if AZURE_STORAGE_ACCOUNT and AZURE_CONTAINER:
                    return "azure"
                return "pvc"
        
        # Default to PVC for local clusters (kind/minikube)
        return "pvc"
    except Exception as e:
        logger.warning(f"Error detecting storage type: {e}, defaulting to PVC")
        return "pvc"

def get_default_storage_class() -> Optional[str]:
    """Get the default storage class name"""
    if not k8s_available:
        return None
    try:
        storage_api = client.StorageV1Api()
        storage_classes = storage_api.list_storage_class()
        for sc in storage_classes.items:
            # Check if it's the default (annotation or common names)
            annotations = sc.metadata.annotations or {}
            if annotations.get("storageclass.kubernetes.io/is-default-class") == "true":
                return sc.metadata.name
        # Fallback: return first storage class or common defaults
        if storage_classes.items:
            return storage_classes.items[0].metadata.name
        # Common defaults for cloud providers
        return "ebs-gp3"  # EKS EBS CSI driver (Kubernetes 1.32+ compatible)
    except Exception as e:
        logger.warning(f"Error getting storage class: {e}, using default 'ebs-gp3'")
        return "ebs-gp3"

def ensure_tenant_pvc(tenant_namespace: str) -> Optional[str]:
    """Ensure PVC exists for tenant results storage"""
    if not k8s_available:
        return None
    
    pvc_name = f"results-{tenant_namespace}"
    storage_class = get_default_storage_class()
    
    try:
        k8s_core.read_namespaced_persistent_volume_claim(pvc_name, tenant_namespace)
        logger.debug(f"PVC {pvc_name} already exists")
        return pvc_name
    except client.exceptions.ApiException as e:
        if e.status == 404:
            # Use ReadWriteOnce (works with EBS and most block storage)
            pvc_spec = client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                resources=client.V1ResourceRequirements(
                    requests={"storage": f"{RESULT_STORAGE_SIZE_GI}Gi"}
                )
            )
            if storage_class:
                pvc_spec.storage_class_name = storage_class
            
            pvc = client.V1PersistentVolumeClaim(
                metadata=client.V1ObjectMeta(
                    name=pvc_name,
                    namespace=tenant_namespace
                ),
                spec=pvc_spec
            )
            try:
                k8s_core.create_namespaced_persistent_volume_claim(tenant_namespace, pvc)
                logger.info(f"Created PVC {pvc_name} for tenant {tenant_namespace}")
                return pvc_name
            except Exception as create_error:
                logger.error(f"Failed to create PVC {pvc_name}: {create_error}")
                return None
        else:
            logger.error(f"Error checking PVC {pvc_name}: {e}")
            return None

def upload_to_s3(job_id: str, tenant_id: str, local_path: str) -> Optional[Dict[str, str]]:
    """Upload results to S3"""
    if not S3_BUCKET:
        logger.warning("S3_BUCKET not configured")
        return None
    
    try:
        import boto3
        from botocore.exceptions import ClientError
        
        s3_client = boto3.client('s3', region_name=S3_REGION)
        prefix = f"sumo-k8-results/{tenant_id}/{job_id}/"
        
        uploaded_files = []
        
        if os.path.isfile(local_path):
            files = [local_path]
        else:
            files = []
            for root, dirs, filenames in os.walk(local_path):
                for filename in filenames:
                    files.append(os.path.join(root, filename))
        
        for file_path in files:
            rel_path = os.path.relpath(file_path, local_path)
            s3_key = f"{prefix}{rel_path}"
            
            s3_client.upload_file(file_path, S3_BUCKET, s3_key)
            uploaded_files.append({
                "name": rel_path,
                "url": f"s3://{S3_BUCKET}/{s3_key}",
                "size": os.path.getsize(file_path)
            })
            logger.info(f"Uploaded {rel_path} to S3")
        
        return {
            "storage_type": "s3",
            "bucket": S3_BUCKET,
            "prefix": prefix,
            "files": uploaded_files
        }
    except ImportError:
        logger.error("boto3 not installed. Install with: pip install boto3")
        return None
    except Exception as e:
        logger.error(f"Failed to upload to S3: {e}")
        return None

def upload_to_gcs(job_id: str, tenant_id: str, local_path: str) -> Optional[Dict[str, str]]:
    """Upload results to Google Cloud Storage"""
    if not GCS_BUCKET:
        logger.warning("GCS_BUCKET not configured")
        return None
    
    try:
        from google.cloud import storage
        
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        prefix = f"results/{tenant_id}/{job_id}/"
        
        uploaded_files = []
        
        if os.path.isfile(local_path):
            files = [local_path]
        else:
            files = []
            for root, dirs, filenames in os.walk(local_path):
                for filename in filenames:
                    files.append(os.path.join(root, filename))
        
        for file_path in files:
            rel_path = os.path.relpath(file_path, local_path)
            blob_name = f"{prefix}{rel_path}"
            blob = bucket.blob(blob_name)
            
            blob.upload_from_filename(file_path)
            
            uploaded_files.append({
                "name": rel_path,
                "url": f"gs://{GCS_BUCKET}/{blob_name}",
                "size": os.path.getsize(file_path)
            })
            logger.info(f"Uploaded {rel_path} to GCS")
        
        return {
            "storage_type": "gcs",
            "bucket": GCS_BUCKET,
            "prefix": prefix,
            "files": uploaded_files
        }
    except ImportError:
        logger.error("google-cloud-storage not installed. Install with: pip install google-cloud-storage")
        return None
    except Exception as e:
        logger.error(f"Failed to upload to GCS: {e}")
        return None

def upload_to_azure(job_id: str, tenant_id: str, local_path: str) -> Optional[Dict[str, str]]:
    """Upload results to Azure Blob Storage"""
    if not AZURE_STORAGE_ACCOUNT or not AZURE_CONTAINER:
        logger.warning("Azure storage not configured")
        return None
    
    try:
        from azure.storage.blob import BlobServiceClient
        
        connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
        if not connection_string:
            logger.error("AZURE_STORAGE_CONNECTION_STRING not set")
            return None
        
        blob_service = BlobServiceClient.from_connection_string(connection_string)
        container_client = blob_service.get_container_client(AZURE_CONTAINER)
        prefix = f"results/{tenant_id}/{job_id}/"
        
        uploaded_files = []
        
        if os.path.isfile(local_path):
            files = [local_path]
        else:
            files = []
            for root, dirs, filenames in os.walk(local_path):
                for filename in filenames:
                    files.append(os.path.join(root, filename))
        
        for file_path in files:
            rel_path = os.path.relpath(file_path, local_path)
            blob_name = f"{prefix}{rel_path}"
            
            with open(file_path, "rb") as data:
                container_client.upload_blob(name=blob_name, data=data, overwrite=True)
            
            uploaded_files.append({
                "name": rel_path,
                "url": f"https://{AZURE_STORAGE_ACCOUNT}.blob.core.windows.net/{AZURE_CONTAINER}/{blob_name}",
                "size": os.path.getsize(file_path)
            })
            logger.info(f"Uploaded {rel_path} to Azure")
        
        return {
            "storage_type": "azure",
            "account": AZURE_STORAGE_ACCOUNT,
            "container": AZURE_CONTAINER,
            "prefix": prefix,
            "files": uploaded_files
        }
    except ImportError:
        logger.error("azure-storage-blob not installed. Install with: pip install azure-storage-blob")
        return None
    except Exception as e:
        logger.error(f"Failed to upload to Azure: {e}")
        return None

def upload_results_from_pvc(job_id: str, tenant_id: str, tenant_namespace: str, storage_type: str) -> Optional[Dict]:
    """Create a temporary pod to upload results from PVC to object storage"""
    if storage_type not in ("s3", "gcs", "azure"):
        return None
    
    if not k8s_available:
        logger.warning("Kubernetes not available, cannot upload from PVC")
        return None
    
    pvc_name = f"results-{tenant_namespace}"
    upload_job_name = f"upload-{job_id[:8]}"
    
    # Build upload script based on storage type
    if storage_type == "s3":
        upload_script = f"""#!/bin/sh
set -e
echo "Installing boto3..."
pip install -q boto3
echo "Uploading results from PVC to S3..."
python3 <<EOF
import boto3
import os
from pathlib import Path

s3 = boto3.client('s3', region_name='{S3_REGION}')
bucket = '{S3_BUCKET}'
prefix = 'sumo-k8-results/{tenant_id}/{job_id}/'
results_dir = Path('/results/{job_id}')

if not results_dir.exists():
    print(f"Results directory not found: {{results_dir}}")
    exit(1)

uploaded = []
for file_path in results_dir.rglob('*'):
    if file_path.is_file():
        rel_path = file_path.relative_to(results_dir)
        s3_key = f"{{prefix}}{{rel_path}}"
        s3.upload_file(str(file_path), bucket, s3_key)
        uploaded.append({{
            'name': str(rel_path),
            'url': f's3://{{bucket}}/{{s3_key}}',
            'size': file_path.stat().st_size
        }})
        print(f"Uploaded {{rel_path}}")

print(f"Uploaded {{len(uploaded)}} files")
EOF
"""
    elif storage_type == "gcs":
        upload_script = f"""#!/bin/sh
set -e
echo "Installing google-cloud-storage..."
pip install -q google-cloud-storage
echo "Uploading results from PVC to GCS..."
python3 <<EOF
from google.cloud import storage
from pathlib import Path

client = storage.Client()
bucket = client.bucket('{GCS_BUCKET}')
prefix = 'results/{tenant_id}/{job_id}/'
results_dir = Path('/results/{job_id}')

if not results_dir.exists():
    print(f"Results directory not found: {{results_dir}}")
    exit(1)

uploaded = []
for file_path in results_dir.rglob('*'):
    if file_path.is_file():
        rel_path = file_path.relative_to(results_dir)
        blob_name = f"{{prefix}}{{rel_path}}"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(str(file_path))
        uploaded.append({{
            'name': str(rel_path),
            'url': f'gs://{GCS_BUCKET}/{{blob_name}}',
            'size': file_path.stat().st_size
        }})
        print(f"Uploaded {{rel_path}}")

print(f"Uploaded {{len(uploaded)}} files")
EOF
"""
    else:  # azure
        upload_script = f"""#!/bin/sh
set -e
echo "Installing azure-storage-blob..."
pip install -q azure-storage-blob
echo "Uploading results from PVC to Azure..."
python3 <<EOF
from azure.storage.blob import BlobServiceClient
from pathlib import Path
import os

conn_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
if not conn_str:
    print("AZURE_STORAGE_CONNECTION_STRING not set")
    exit(1)

blob_service = BlobServiceClient.from_connection_string(conn_str)
container_client = blob_service.get_container_client('{AZURE_CONTAINER}')
prefix = 'results/{tenant_id}/{job_id}/'
results_dir = Path('/results/{job_id}')

if not results_dir.exists():
    print(f"Results directory not found: {{results_dir}}")
    exit(1)

uploaded = []
for file_path in results_dir.rglob('*'):
    if file_path.is_file():
        rel_path = file_path.relative_to(results_dir)
        blob_name = f"{{prefix}}{{rel_path}}"
        with open(file_path, 'rb') as data:
            container_client.upload_blob(name=blob_name, data=data, overwrite=True)
        uploaded.append({{
            'name': str(rel_path),
            'url': f'https://{AZURE_STORAGE_ACCOUNT}.blob.core.windows.net/{AZURE_CONTAINER}/{{blob_name}}',
            'size': file_path.stat().st_size
        }})
        print(f"Uploaded {{rel_path}}")

print(f"Uploaded {{len(uploaded)}} files")
EOF
"""
    
    # Create ConfigMap with upload script
    configmap_name = f"upload-script-{job_id[:8]}"
    try:
        upload_configmap = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(
                name=configmap_name,
                namespace=tenant_namespace
            ),
            data={"upload.sh": upload_script}
        )
        k8s_core.create_namespaced_config_map(tenant_namespace, upload_configmap)
    except Exception as e:
        logger.error(f"Failed to create upload ConfigMap: {e}")
        return None
    
    # Create upload job pod
    env_vars = []
    if storage_type == "s3":
        # S3 uses IAM roles (on EKS) or credentials from environment
        aws_access_key = os.getenv("AWS_ACCESS_KEY_ID", "")
        aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "")
        if aws_access_key:
            env_vars.append(client.V1EnvVar(name="AWS_ACCESS_KEY_ID", value=aws_access_key))
        if aws_secret_key:
            env_vars.append(client.V1EnvVar(name="AWS_SECRET_ACCESS_KEY", value=aws_secret_key))
        aws_session_token = os.getenv("AWS_SESSION_TOKEN", "")
        if aws_session_token:
            env_vars.append(client.V1EnvVar(name="AWS_SESSION_TOKEN", value=aws_session_token))
    elif storage_type == "gcs":
        # GCS uses service account (on GKE) or credentials from environment
        gcs_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
        if gcs_creds:
            env_vars.append(client.V1EnvVar(name="GOOGLE_APPLICATION_CREDENTIALS", value=gcs_creds))
        # Or use service account key as env var
        gcs_key_json = os.getenv("GCS_SERVICE_ACCOUNT_KEY", "")
        if gcs_key_json:
            env_vars.append(client.V1EnvVar(name="GOOGLE_APPLICATION_CREDENTIALS_JSON", value=gcs_key_json))
    elif storage_type == "azure":
        # Azure needs connection string
        azure_conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
        if azure_conn_str:
            env_vars.append(client.V1EnvVar(name="AZURE_STORAGE_CONNECTION_STRING", value=azure_conn_str))
    
    upload_job = client.V1Job(
        metadata=client.V1ObjectMeta(
            name=upload_job_name,
            namespace=tenant_namespace,
            labels={"job-id": job_id, "type": "upload"}
        ),
        spec=client.V1JobSpec(
            ttl_seconds_after_finished=60,
            template=client.V1PodTemplateSpec(
                spec=client.V1PodSpec(
                    restart_policy="Never",
                    containers=[
                        client.V1Container(
                            name="uploader",
                            image="python:3.11-slim",
                            command=["/bin/sh", "/config/upload.sh"],
                            volume_mounts=[
                                client.V1VolumeMount(
                                    name="results",
                                    mount_path="/results"
                                ),
                                client.V1VolumeMount(
                                    name="upload-script",
                                    mount_path="/config"
                                )
                            ],
                            env=env_vars
                        )
                    ],
                    volumes=[
                        client.V1Volume(
                            name="results",
                            persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                                claim_name=pvc_name
                            )
                        ),
                        client.V1Volume(
                            name="upload-script",
                            config_map=client.V1ConfigMapVolumeSource(name=configmap_name)
                        )
                    ]
                )
            )
        )
    )
    
    try:
        k8s_batch.create_namespaced_job(tenant_namespace, upload_job)
        logger.info(f"Created upload job {upload_job_name} for {storage_type}")
        return {"upload_job": upload_job_name, "status": "started"}
    except Exception as e:
        logger.error(f"Failed to create upload job: {e}")
        return None

def cleanup_pvc_after_upload(tenant_namespace: str, job_id: str):
    """Delete result files from PVC after successful cloud storage upload"""
    if not k8s_available:
        return
    
    pvc_name = f"results-{tenant_namespace}"
    cleanup_job_name = f"cleanup-{job_id[:8]}"
    
    # Create a temporary pod to delete job-specific results from PVC
    cleanup_script = f"""#!/bin/sh
set -e
echo "Cleaning up results from PVC for job {job_id}..."
if [ -d /results/{job_id} ]; then
    rm -rf /results/{job_id}
    echo "Deleted /results/{job_id} from PVC"
else
    echo "Results directory /results/{job_id} not found, nothing to clean"
fi
"""
    
    # Create ConfigMap with cleanup script
    configmap_name = f"cleanup-script-{job_id[:8]}"
    try:
        cleanup_configmap = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(
                name=configmap_name,
                namespace=tenant_namespace
            ),
            data={"cleanup.sh": cleanup_script}
        )
        k8s_core.create_namespaced_config_map(tenant_namespace, cleanup_configmap)
    except Exception as e:
        logger.error(f"Failed to create cleanup ConfigMap: {e}")
        return
    
    # Create cleanup job pod
    cleanup_job = client.V1Job(
        metadata=client.V1ObjectMeta(
            name=cleanup_job_name,
            namespace=tenant_namespace,
            labels={"job-id": job_id, "type": "cleanup"}
        ),
        spec=client.V1JobSpec(
            ttl_seconds_after_finished=60,
            template=client.V1PodTemplateSpec(
                spec=client.V1PodSpec(
                    restart_policy="Never",
                    containers=[
                        client.V1Container(
                            name="cleanup",
                            image="busybox:latest",
                            command=["/bin/sh", "/config/cleanup.sh"],
                            volume_mounts=[
                                client.V1VolumeMount(
                                    name="results",
                                    mount_path="/results"
                                ),
                                client.V1VolumeMount(
                                    name="cleanup-script",
                                    mount_path="/config"
                                )
                            ]
                        )
                    ],
                    volumes=[
                        client.V1Volume(
                            name="results",
                            persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                                claim_name=pvc_name
                            )
                        ),
                        client.V1Volume(
                            name="cleanup-script",
                            config_map=client.V1ConfigMapVolumeSource(name=configmap_name)
                        )
                    ]
                )
            )
        )
    )
    
    try:
        k8s_batch.create_namespaced_job(tenant_namespace, cleanup_job)
        logger.info(f"Created cleanup job {cleanup_job_name} to remove results from PVC")
    except Exception as e:
        logger.error(f"Failed to create cleanup job: {e}")

def get_result_storage_info(job_id: str, tenant_namespace: str, storage_type: str) -> Dict[str, str]:
    """Get storage information for job results"""
    if storage_type == "pvc":
        return {
            "storage_type": "pvc",
            "path": f"/results/{job_id}",
            "pvc_name": f"results-{tenant_namespace}"
        }
    elif storage_type == "s3":
        return {
            "storage_type": "s3",
            "bucket": S3_BUCKET,
            "prefix": f"sumo-k8-results/{tenant_namespace}/{job_id}/"
        }
    elif storage_type == "gcs":
        return {
            "storage_type": "gcs",
            "bucket": GCS_BUCKET,
            "prefix": f"results/{tenant_namespace}/{job_id}/"
        }
    elif storage_type == "azure":
        return {
            "storage_type": "azure",
            "account": AZURE_STORAGE_ACCOUNT,
            "container": AZURE_CONTAINER,
            "prefix": f"results/{tenant_namespace}/{job_id}/"
        }
    else:
        return {"storage_type": "unknown"}

