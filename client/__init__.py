"""
SUMO-K8 Python Client

A Python client for the SUMO-K8 Kubernetes simulation controller.

Quick Start:
    from sumo_k8_client import SumoK8Client
    
    client = SumoK8Client(
        base_url="http://your-cluster-url",
        api_key="sk_your_api_key"
    )
    
    # Submit a job
    job = client.submit_job("my_scenario", "/path/to/scenario.zip")
    
    # Wait for completion
    result = client.wait_for_completion(job["job_id"])

Installation:
    pip install -e ./client
    
    # Or copy client/ to your project
"""

from .client import (
    SumoK8Client,
    SumoK8Error,
    AuthenticationError,
    JobNotFoundError,
    QuotaExceededError,
    TenantNotFoundError,
    JobStatus,
    TenantInfo,
    DashboardInfo,
    get_client,
)

from .autoscaler import (
    AutoscalerMetrics,
    ScalingConfig,
    ScalingDecision,
    BaseNodeScaler,
    EKSNodeGroupScaler,
    GKENodePoolScaler,
    AKSNodePoolScaler,
    run_autoscaler_loop,
)

__version__ = "1.0.0"
__all__ = [
    # Client
    "SumoK8Client",
    "get_client",
    # Exceptions
    "SumoK8Error",
    "AuthenticationError",
    "JobNotFoundError",
    "QuotaExceededError",
    "TenantNotFoundError",
    # Data classes
    "JobStatus",
    "TenantInfo",
    "DashboardInfo",
    # Autoscaler
    "AutoscalerMetrics",
    "ScalingConfig",
    "ScalingDecision",
    "BaseNodeScaler",
    "EKSNodeGroupScaler",
    "GKENodePoolScaler",
    "AKSNodePoolScaler",
    "run_autoscaler_loop",
]
