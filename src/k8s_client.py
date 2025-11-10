"""Kubernetes client initialization"""
from kubernetes import client, config
import logging

logger = logging.getLogger(__name__)

k8s_available = False
k8s_core = None
k8s_batch = None

def init_k8s_client():
    """Initialize Kubernetes client"""
    global k8s_available, k8s_core, k8s_batch
    
    try:
        config.load_incluster_config()
        k8s_available = True
        logger.info("Loaded in-cluster Kubernetes config")
    except:
        try:
            config.load_kube_config()
            k8s_available = True
            logger.info("Loaded kubeconfig")
        except Exception as e:
            logger.warning(f"Kubernetes not available: {e}. Running in local mode.")
            k8s_available = False
    
    if k8s_available:
        k8s_core = client.CoreV1Api()
        k8s_batch = client.BatchV1Api()
    else:
        k8s_core = None
        k8s_batch = None

# Initialize on import
init_k8s_client()

