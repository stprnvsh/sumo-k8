"""Kubernetes resource management and scaling"""
import logging
from typing import Optional
from kubernetes import client
from .k8s_client import k8s_available, k8s_core, k8s_batch
from .config import CONFIGMAP_CLEANUP_DELAY_SECONDS, RESULT_STORAGE_SIZE_GI
import threading
import time

logger = logging.getLogger(__name__)

def ensure_tenant_namespace(tenant):
    """Ensure namespace, ResourceQuota, and LimitRange exist for tenant"""
    if not k8s_available:
        logger.warning("Kubernetes not available - skipping namespace/quota creation")
        return
    
    ns_name = tenant['namespace']
    
    # Create namespace if needed
    try:
        k8s_core.read_namespace(ns_name)
    except client.exceptions.ApiException as e:
        if e.status == 404:
            ns = client.V1Namespace(metadata=client.V1ObjectMeta(name=ns_name))
            k8s_core.create_namespace(ns)
            logger.info(f"Created namespace {ns_name}")
    
    # Create ResourceQuota if needed
    quota_name = f"{ns_name}-quota"
    try:
        existing_quota = k8s_core.read_namespaced_resource_quota(quota_name, ns_name)
        # Update if limits changed
        needs_update = (
            existing_quota.spec.hard.get("requests.cpu") != str(tenant['max_cpu']) or
            existing_quota.spec.hard.get("requests.memory") != f"{tenant['max_memory_gi']}Gi"
        )
        if needs_update:
            quota = client.V1ResourceQuota(
                metadata=client.V1ObjectMeta(name=quota_name, namespace=ns_name),
                spec=client.V1ResourceQuotaSpec(
                    hard={
                        "requests.cpu": str(tenant['max_cpu']),
                        "requests.memory": f"{tenant['max_memory_gi']}Gi",
                        "limits.cpu": str(tenant['max_cpu']),
                        "limits.memory": f"{tenant['max_memory_gi']}Gi",
                        "pods": str(tenant.get('max_concurrent_jobs', 10))
                    }
                )
            )
            k8s_core.patch_namespaced_resource_quota(quota_name, ns_name, quota)
            logger.info(f"Updated ResourceQuota {quota_name}")
    except client.exceptions.ApiException as e:
        if e.status == 404:
            quota = client.V1ResourceQuota(
                metadata=client.V1ObjectMeta(name=quota_name, namespace=ns_name),
                spec=client.V1ResourceQuotaSpec(
                    hard={
                        "requests.cpu": str(tenant['max_cpu']),
                        "requests.memory": f"{tenant['max_memory_gi']}Gi",
                        "limits.cpu": str(tenant['max_cpu']),
                        "limits.memory": f"{tenant['max_memory_gi']}Gi",
                        "pods": str(tenant.get('max_concurrent_jobs', 10))
                    }
                )
            )
            k8s_core.create_namespaced_resource_quota(ns_name, quota)
            logger.info(f"Created ResourceQuota {quota_name}")
    
    # Create LimitRange to enforce per-pod limits
    limitrange_name = f"{ns_name}-limits"
    try:
        existing_limitrange = k8s_core.read_namespaced_limit_range(limitrange_name, ns_name)
        needs_update = (
            existing_limitrange.spec.limits[0].max.get("cpu") != str(tenant['max_cpu']) or
            existing_limitrange.spec.limits[0].max.get("memory") != f"{tenant['max_memory_gi']}Gi"
        )
        if needs_update:
            limit_range = client.V1LimitRange(
                metadata=client.V1ObjectMeta(name=limitrange_name, namespace=ns_name),
                spec=client.V1LimitRangeSpec(
                    limits=[
                        client.V1LimitRangeItem(
                            default={"cpu": "1", "memory": "2Gi"},
                            default_request={"cpu": "100m", "memory": "256Mi"},
                            max={
                                "cpu": str(tenant['max_cpu']),
                                "memory": f"{tenant['max_memory_gi']}Gi"
                            },
                            type="Container"
                        )
                    ]
                )
            )
            k8s_core.patch_namespaced_limit_range(limitrange_name, ns_name, limit_range)
            logger.info(f"Updated LimitRange {limitrange_name}")
    except client.exceptions.ApiException as e:
        if e.status == 404:
            limit_range = client.V1LimitRange(
                metadata=client.V1ObjectMeta(name=limitrange_name, namespace=ns_name),
                spec=client.V1LimitRangeSpec(
                    limits=[
                        client.V1LimitRangeItem(
                            default={"cpu": "1", "memory": "2Gi"},
                            default_request={"cpu": "100m", "memory": "256Mi"},
                            max={
                                "cpu": str(tenant['max_cpu']),
                                "memory": f"{tenant['max_memory_gi']}Gi"
                            },
                            type="Container"
                        )
                    ]
                )
            )
            k8s_core.create_namespaced_limit_range(ns_name, limit_range)
            logger.info(f"Created LimitRange {limitrange_name}")
    
    # Always ensure PVC exists (even for cloud storage, we write to PVC first then upload)
    from .storage import detect_storage_type
    storage_type = detect_storage_type()
    logger.info(f"Detected storage type: {storage_type} for namespace {ns_name}")
    logger.info(f"Creating PVC for namespace {ns_name} (required for result storage)")
    ensure_tenant_pvc(ns_name)

def get_default_storage_class() -> Optional[str]:
    """Get the default storage class name"""
    if not k8s_available:
        return None
    try:
        storage_api = client.StorageV1Api()
        storage_classes = storage_api.list_storage_class()
        for sc in storage_classes.items:
            annotations = sc.metadata.annotations or {}
            if annotations.get("storageclass.kubernetes.io/is-default-class") == "true":
                return sc.metadata.name
        if storage_classes.items:
            return storage_classes.items[0].metadata.name
        return "ebs-gp3"  # EKS EBS CSI driver (Kubernetes 1.32+ compatible)
    except Exception as e:
        logger.warning(f"Error getting storage class: {e}, using default 'ebs-gp3'")
        return "ebs-gp3"

def ensure_tenant_pvc(tenant_namespace: str):
    """Ensure PVC exists for tenant results storage"""
    if not k8s_available:
        logger.warning(f"Kubernetes not available, skipping PVC creation for {tenant_namespace}")
        return
    
    pvc_name = f"results-{tenant_namespace}"
    storage_class = get_default_storage_class()
    logger.debug(f"Checking for PVC {pvc_name} in namespace {tenant_namespace}")
    
    try:
        k8s_core.read_namespaced_persistent_volume_claim(pvc_name, tenant_namespace)
        logger.info(f"PVC {pvc_name} already exists")
    except client.exceptions.ApiException as e:
        if e.status == 404:
            # Use ReadWriteOnce for EBS (ReadWriteMany not supported)
            logger.info(f"PVC {pvc_name} not found, creating with storage class {storage_class}...")
            try:
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
                k8s_core.create_namespaced_persistent_volume_claim(tenant_namespace, pvc)
                logger.info(f"Created PVC {pvc_name} with ReadWriteOnce and storage class {storage_class}")
            except Exception as create_error:
                logger.error(f"Failed to create PVC {pvc_name}: {create_error}")
        else:
            logger.error(f"Error checking PVC {pvc_name}: {e}")

def cleanup_configmaps(namespace: str, job_id: str, delay_seconds: int = CONFIGMAP_CLEANUP_DELAY_SECONDS):
    """Clean up ConfigMaps after job completion (with delay)"""
    def _cleanup():
        if not k8s_available:
            return
        time.sleep(delay_seconds)
        try:
            configmaps = k8s_core.list_namespaced_config_map(namespace)
            job_prefix = f"sumo-{job_id[:8]}"
            
            for cm in configmaps.items:
                if cm.metadata.name.startswith(job_prefix):
                    try:
                        k8s_core.delete_namespaced_config_map(cm.metadata.name, namespace)
                        logger.info(f"Cleaned up ConfigMap {cm.metadata.name}")
                    except Exception as e:
                        logger.warning(f"Failed to delete ConfigMap {cm.metadata.name}: {e}")
        except Exception as e:
            logger.error(f"ConfigMap cleanup error: {e}")
    
    threading.Thread(target=_cleanup, daemon=True).start()

def get_cluster_nodes():
    """Get all cluster nodes with status"""
    if not k8s_available:
        return []
    
    try:
        nodes = k8s_core.list_node()
        node_info = []
        
        for node in nodes.items:
            allocatable = node.status.allocatable
            capacity = node.status.capacity
            
            pods = k8s_core.list_pod_for_all_namespaces(
                field_selector=f"spec.nodeName={node.metadata.name}"
            )
            
            running_pods = len([p for p in pods.items if p.status.phase == 'Running'])
            
            node_info.append({
                "name": node.metadata.name,
                "status": [c.type for c in node.status.conditions if c.status == "True"],
                "capacity": {
                    "cpu": capacity.get('cpu', '0'),
                    "memory": capacity.get('memory', '0')
                },
                "allocatable": {
                    "cpu": allocatable.get('cpu', '0'),
                    "memory": allocatable.get('memory', '0')
                },
                "pods_running": running_pods,
                "created": node.metadata.creation_timestamp.isoformat() if node.metadata.creation_timestamp else None
            })
        
        return node_info
    except Exception as e:
        logger.error(f"Error fetching nodes: {e}")
        return []

def get_cluster_activity():
    """Get cluster activity metrics"""
    pods_by_status = {}
    k8s_jobs_stats = {"total": 0, "active": 0, "succeeded": 0}
    nodes = 0
    
    if k8s_available:
        try:
            all_pods = k8s_core.list_pod_for_all_namespaces()
            for pod in all_pods.items:
                phase = pod.status.phase
                pods_by_status[phase] = pods_by_status.get(phase, 0) + 1
            
            all_jobs = k8s_batch.list_job_for_all_namespaces()
            k8s_jobs_stats = {
                "total": len(all_jobs.items),
                "active": len([j for j in all_jobs.items if j.status.active]),
                "succeeded": len([j for j in all_jobs.items if j.status.succeeded]),
            }
            
            nodes = len(k8s_core.list_node().items)
        except Exception as e:
            logger.error(f"Error fetching K8s data: {e}")
    
    return {
        "nodes": nodes,
        "pods": pods_by_status,
        "k8s_jobs": k8s_jobs_stats
    }

