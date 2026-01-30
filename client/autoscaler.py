"""
Autoscaler utilities for SUMO-K8.

This module provides helpers for building custom autoscalers that can scale
Kubernetes node pools based on job queue depth.

Example usage with AWS EKS:
    from sumo_k8_client import SumoK8Client
    from sumo_k8_client.autoscaler import AutoscalerMetrics, EKSNodeGroupScaler
    
    client = SumoK8Client(admin_key="your_admin_key")
    metrics = AutoscalerMetrics(client)
    scaler = EKSNodeGroupScaler("my-cluster", "sumo-workers")
    
    # Run scaling loop
    while True:
        decision = metrics.get_scaling_decision()
        if decision["action"] == "scale_up":
            scaler.scale_up(decision["target_nodes"])
        elif decision["action"] == "scale_down":
            scaler.scale_down(decision["target_nodes"])
        time.sleep(60)
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, Callable
import logging
import time

logger = logging.getLogger(__name__)


@dataclass
class ScalingConfig:
    """Configuration for autoscaling behavior."""
    
    # Minimum and maximum nodes
    min_nodes: int = 1
    max_nodes: int = 20
    
    # Jobs per node thresholds
    jobs_per_node: float = 2.0  # Target jobs per node
    scale_up_threshold: float = 1.5  # Scale up when queue > nodes * threshold
    scale_down_threshold: float = 0.5  # Scale down when queue < nodes * threshold
    
    # Cooldown periods (seconds)
    scale_up_cooldown: int = 60
    scale_down_cooldown: int = 300
    
    # Grace period for new nodes to become ready
    node_ready_timeout: int = 300


@dataclass
class ScalingDecision:
    """Result of scaling decision."""
    action: str  # "scale_up", "scale_down", "none"
    current_nodes: int
    target_nodes: int
    pending_jobs: int
    running_jobs: int
    reason: str


class AutoscalerMetrics:
    """
    Collects metrics from SUMO-K8 for autoscaling decisions.
    
    Args:
        client: SumoK8Client with admin access
        config: ScalingConfig for thresholds
    """
    
    def __init__(
        self,
        client,  # SumoK8Client
        config: Optional[ScalingConfig] = None
    ):
        self.client = client
        self.config = config or ScalingConfig()
        self._last_scale_up = 0
        self._last_scale_down = 0
    
    def get_metrics(self) -> Dict[str, Any]:
        """
        Get current cluster metrics.
        
        Returns:
            Dict with pending_jobs, running_jobs, node_count, etc.
        """
        try:
            activity = self.client.get_cluster_activity()
            cluster = self.client.get_cluster_status()
            
            db_jobs = activity.get("db_jobs", {})
            
            return {
                "pending_jobs": db_jobs.get("PENDING", 0),
                "running_jobs": db_jobs.get("RUNNING", 0),
                "total_jobs": sum(db_jobs.values()),
                "node_count": cluster.get("total_nodes", 0),
                "k8s_jobs": activity.get("k8s_jobs", {}),
                "pods": activity.get("pods", {}),
                "timestamp": activity.get("timestamp")
            }
        except Exception as e:
            logger.error(f"Failed to get metrics: {e}")
            return {
                "pending_jobs": 0,
                "running_jobs": 0,
                "total_jobs": 0,
                "node_count": 0,
                "error": str(e)
            }
    
    def get_scaling_decision(self) -> ScalingDecision:
        """
        Determine scaling action based on current metrics.
        
        Returns:
            ScalingDecision with recommended action
        """
        metrics = self.get_metrics()
        
        pending = metrics["pending_jobs"]
        running = metrics["running_jobs"]
        current_nodes = metrics["node_count"]
        queue_depth = pending + running
        
        now = time.time()
        
        # Calculate target nodes based on queue
        desired_nodes = max(
            self.config.min_nodes,
            min(
                self.config.max_nodes,
                int((queue_depth / self.config.jobs_per_node) + 0.5)
            )
        )
        
        # Check cooldowns
        can_scale_up = (now - self._last_scale_up) >= self.config.scale_up_cooldown
        can_scale_down = (now - self._last_scale_down) >= self.config.scale_down_cooldown
        
        # Scale up decision
        if queue_depth > current_nodes * self.config.scale_up_threshold:
            if can_scale_up and desired_nodes > current_nodes:
                self._last_scale_up = now
                return ScalingDecision(
                    action="scale_up",
                    current_nodes=current_nodes,
                    target_nodes=desired_nodes,
                    pending_jobs=pending,
                    running_jobs=running,
                    reason=f"Queue depth {queue_depth} exceeds threshold"
                )
        
        # Scale down decision
        if queue_depth < current_nodes * self.config.scale_down_threshold:
            if can_scale_down and desired_nodes < current_nodes:
                self._last_scale_down = now
                return ScalingDecision(
                    action="scale_down",
                    current_nodes=current_nodes,
                    target_nodes=max(desired_nodes, self.config.min_nodes),
                    pending_jobs=pending,
                    running_jobs=running,
                    reason=f"Queue depth {queue_depth} below threshold"
                )
        
        # No scaling needed
        return ScalingDecision(
            action="none",
            current_nodes=current_nodes,
            target_nodes=current_nodes,
            pending_jobs=pending,
            running_jobs=running,
            reason="Within thresholds"
        )


class BaseNodeScaler:
    """
    Base class for node scaling implementations.
    
    Subclass this to implement scaling for your cloud provider.
    """
    
    def get_current_count(self) -> int:
        """Get current node count."""
        raise NotImplementedError
    
    def scale_to(self, count: int) -> bool:
        """
        Scale to specified node count.
        
        Args:
            count: Target node count
            
        Returns:
            True if scaling initiated successfully
        """
        raise NotImplementedError
    
    def scale_up(self, target: int) -> bool:
        """Scale up to target nodes."""
        current = self.get_current_count()
        if target > current:
            logger.info(f"Scaling up from {current} to {target} nodes")
            return self.scale_to(target)
        return False
    
    def scale_down(self, target: int) -> bool:
        """Scale down to target nodes."""
        current = self.get_current_count()
        if target < current:
            logger.info(f"Scaling down from {current} to {target} nodes")
            return self.scale_to(target)
        return False


class EKSNodeGroupScaler(BaseNodeScaler):
    """
    AWS EKS Node Group scaler.
    
    Requires: boto3
    
    Args:
        cluster_name: EKS cluster name
        nodegroup_name: Node group to scale
        region: AWS region (default: from environment)
    """
    
    def __init__(
        self,
        cluster_name: str,
        nodegroup_name: str,
        region: Optional[str] = None
    ):
        self.cluster_name = cluster_name
        self.nodegroup_name = nodegroup_name
        self.region = region
        self._client = None
    
    @property
    def client(self):
        if self._client is None:
            try:
                import boto3
            except ImportError:
                raise ImportError("boto3 required: pip install boto3")
            
            self._client = boto3.client('eks', region_name=self.region)
        return self._client
    
    def get_current_count(self) -> int:
        """Get current desired size of node group."""
        response = self.client.describe_nodegroup(
            clusterName=self.cluster_name,
            nodegroupName=self.nodegroup_name
        )
        scaling = response['nodegroup']['scalingConfig']
        return scaling['desiredSize']
    
    def scale_to(self, count: int) -> bool:
        """Scale node group to specified size."""
        try:
            # Get current config
            response = self.client.describe_nodegroup(
                clusterName=self.cluster_name,
                nodegroupName=self.nodegroup_name
            )
            scaling = response['nodegroup']['scalingConfig']
            
            # Respect min/max limits
            count = max(scaling['minSize'], min(scaling['maxSize'], count))
            
            self.client.update_nodegroup_config(
                clusterName=self.cluster_name,
                nodegroupName=self.nodegroup_name,
                scalingConfig={
                    'minSize': scaling['minSize'],
                    'maxSize': scaling['maxSize'],
                    'desiredSize': count
                }
            )
            logger.info(f"Scaled EKS node group {self.nodegroup_name} to {count}")
            return True
        except Exception as e:
            logger.error(f"Failed to scale EKS: {e}")
            return False


class GKENodePoolScaler(BaseNodeScaler):
    """
    Google GKE Node Pool scaler.
    
    Requires: google-cloud-container
    
    Args:
        project_id: GCP project ID
        zone: GCP zone (e.g., "us-central1-a")
        cluster_name: GKE cluster name
        nodepool_name: Node pool to scale
    """
    
    def __init__(
        self,
        project_id: str,
        zone: str,
        cluster_name: str,
        nodepool_name: str
    ):
        self.project_id = project_id
        self.zone = zone
        self.cluster_name = cluster_name
        self.nodepool_name = nodepool_name
        self._client = None
    
    @property
    def client(self):
        if self._client is None:
            try:
                from google.cloud import container_v1
            except ImportError:
                raise ImportError("google-cloud-container required")
            
            self._client = container_v1.ClusterManagerClient()
        return self._client
    
    def _get_nodepool_name(self) -> str:
        return f"projects/{self.project_id}/locations/{self.zone}/clusters/{self.cluster_name}/nodePools/{self.nodepool_name}"
    
    def get_current_count(self) -> int:
        """Get current node count in pool."""
        pool = self.client.get_node_pool(name=self._get_nodepool_name())
        return pool.initial_node_count
    
    def scale_to(self, count: int) -> bool:
        """Scale node pool to specified size."""
        try:
            from google.cloud import container_v1
            
            self.client.set_node_pool_size(
                request=container_v1.SetNodePoolSizeRequest(
                    name=self._get_nodepool_name(),
                    node_count=count
                )
            )
            logger.info(f"Scaled GKE node pool {self.nodepool_name} to {count}")
            return True
        except Exception as e:
            logger.error(f"Failed to scale GKE: {e}")
            return False


class AKSNodePoolScaler(BaseNodeScaler):
    """
    Azure AKS Node Pool scaler.
    
    Requires: azure-mgmt-containerservice
    
    Args:
        subscription_id: Azure subscription ID
        resource_group: Resource group name
        cluster_name: AKS cluster name
        nodepool_name: Node pool to scale
    """
    
    def __init__(
        self,
        subscription_id: str,
        resource_group: str,
        cluster_name: str,
        nodepool_name: str
    ):
        self.subscription_id = subscription_id
        self.resource_group = resource_group
        self.cluster_name = cluster_name
        self.nodepool_name = nodepool_name
        self._client = None
    
    @property
    def client(self):
        if self._client is None:
            try:
                from azure.identity import DefaultAzureCredential
                from azure.mgmt.containerservice import ContainerServiceClient
            except ImportError:
                raise ImportError("azure-mgmt-containerservice and azure-identity required")
            
            credential = DefaultAzureCredential()
            self._client = ContainerServiceClient(credential, self.subscription_id)
        return self._client
    
    def get_current_count(self) -> int:
        """Get current node count."""
        pool = self.client.agent_pools.get(
            self.resource_group,
            self.cluster_name,
            self.nodepool_name
        )
        return pool.count
    
    def scale_to(self, count: int) -> bool:
        """Scale agent pool to specified size."""
        try:
            pool = self.client.agent_pools.get(
                self.resource_group,
                self.cluster_name,
                self.nodepool_name
            )
            pool.count = count
            
            self.client.agent_pools.begin_create_or_update(
                self.resource_group,
                self.cluster_name,
                self.nodepool_name,
                pool
            )
            logger.info(f"Scaled AKS agent pool {self.nodepool_name} to {count}")
            return True
        except Exception as e:
            logger.error(f"Failed to scale AKS: {e}")
            return False


def run_autoscaler_loop(
    client,  # SumoK8Client
    scaler: BaseNodeScaler,
    config: Optional[ScalingConfig] = None,
    interval: int = 60,
    callback: Optional[Callable[[ScalingDecision], None]] = None
):
    """
    Run autoscaler in a loop.
    
    Args:
        client: SumoK8Client with admin access
        scaler: BaseNodeScaler implementation
        config: ScalingConfig for thresholds
        interval: Seconds between checks
        callback: Optional callback for decisions
        
    Example:
        >>> from sumo_k8_client import SumoK8Client
        >>> from sumo_k8_client.autoscaler import run_autoscaler_loop, EKSNodeGroupScaler
        >>> 
        >>> client = SumoK8Client(admin_key="...")
        >>> scaler = EKSNodeGroupScaler("my-cluster", "sumo-workers")
        >>> 
        >>> run_autoscaler_loop(client, scaler)
    """
    metrics = AutoscalerMetrics(client, config)
    
    logger.info("Starting autoscaler loop")
    
    while True:
        try:
            decision = metrics.get_scaling_decision()
            
            if callback:
                callback(decision)
            
            if decision.action == "scale_up":
                logger.info(f"Scaling up: {decision.reason}")
                scaler.scale_up(decision.target_nodes)
            elif decision.action == "scale_down":
                logger.info(f"Scaling down: {decision.reason}")
                scaler.scale_down(decision.target_nodes)
            else:
                logger.debug(f"No scaling needed: {decision.reason}")
        
        except Exception as e:
            logger.error(f"Autoscaler error: {e}")
        
        time.sleep(interval)
