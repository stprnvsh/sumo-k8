# SUMO-K8 Python Client

A Python client for the SUMO-K8 Kubernetes simulation controller.

## Installation

```bash
# From the sumo-k8 directory (editable install)
pip install -e ./client

# Or install dependencies directly
pip install requests
```

### Install from a Git repo (recommended for other machines)

If `sumo-k8` is in a git repository, you can install the client subdirectory directly:

```bash
pip install "git+https://github.com/transcality/sumo-k8.git#subdirectory=client"
```

## Quick Start

```python
from sumo_k8_client import SumoK8Client

# Initialize client
client = SumoK8Client(
    base_url="http://your-load-balancer-url",
    api_key="sk_your_api_key"
)

# Submit a job
job = client.submit_job(
    scenario_id="geneva_morning",
    sumo_files="/path/to/scenario.zip",
    cpu_request=4,
    memory_gi=8
)

print(f"Job submitted: {job['job_id']}")

# Wait for completion
result = client.wait_for_completion(job["job_id"], timeout=3600)

if result.is_success:
    print(f"Results at: {result.result_location}")
else:
    print(f"Job failed: {result.error}")
```

## Environment Variables

Instead of passing credentials directly, you can use environment variables:

```bash
export SUMO_K8_URL="http://your-cluster-url:8000"
export SUMO_K8_API_KEY="sk_your_api_key"
export SUMO_K8_ADMIN_KEY="admin_secret_key"  # For admin operations
```

```python
from sumo_k8_client import SumoK8Client

# Uses environment variables automatically
client = SumoK8Client()
```

## API Reference

### Job Operations

```python
# Submit a job
job = client.submit_job(
    scenario_id="test",
    sumo_files="/path/to/scenario.zip",
    cpu_request=2,
    memory_gi=4
)

# Get job status
status = client.get_job_status(job["job_id"])
print(status.status)  # PENDING, RUNNING, SUCCEEDED, FAILED

# Get job logs
logs = client.get_job_logs(job["job_id"])

# Stream logs in real-time
for line in client.stream_job_logs(job["job_id"]):
    print(line)

# Get results
results = client.get_job_results(job["job_id"])

# Wait for completion with progress callback
def on_progress(status):
    print(f"Status: {status.status}")

result = client.wait_for_completion(
    job["job_id"],
    timeout=3600,
    poll_interval=10,
    progress_callback=on_progress
)

# Submit and wait in one call
result = client.submit_and_wait(
    scenario_id="test",
    sumo_files="/path/to/scenario.zip",
    timeout=3600
)
```

### Dashboard

```python
# Get tenant dashboard
dashboard = client.get_dashboard()

print(f"Running jobs: {dashboard.stats['running']}")
print(f"CPU usage: {dashboard.current_usage.get('requests.cpu', '0')}")
```

### Admin Operations

Admin operations require the `admin_key` to be set:

```python
client = SumoK8Client(
    base_url="http://your-cluster-url:8000",
    admin_key="your_admin_key"
)

# Register a new tenant
tenant = client.register_tenant(
    tenant_id="new_customer",
    max_cpu=16,
    max_memory_gi=64,
    max_concurrent_jobs=10
)
print(f"API Key: {tenant.api_key}")

# List all tenants
tenants = client.list_tenants()

# Get cluster status
cluster = client.get_cluster_status()
print(f"Nodes: {cluster['total_nodes']}")

# List all jobs (optionally filter by status)
jobs = client.list_all_jobs(status="RUNNING")
```

## Error Handling

```python
from sumo_k8_client import (
    SumoK8Client,
    SumoK8Error,
    AuthenticationError,
    QuotaExceededError,
    JobNotFoundError
)

try:
    result = client.submit_job("test", "/path/to/scenario.zip")
except AuthenticationError:
    print("Invalid API key")
except QuotaExceededError:
    print("Resource quota exceeded - wait for jobs to complete")
except JobNotFoundError:
    print("Job not found")
except SumoK8Error as e:
    print(f"API error: {e}")
```

## Integration Examples

### Celery Task

```python
from celery import shared_task
from sumo_k8_client import SumoK8Client

@shared_task
def run_simulation(scenario_path, scenario_id):
    client = SumoK8Client()
    
    result = client.submit_and_wait(
        scenario_id=scenario_id,
        sumo_files=scenario_path,
        cpu_request=4,
        memory_gi=8,
        timeout=7200
    )
    
    return {
        "status": result.status,
        "results": result.result_location
    }
```

### Django View

```python
from django.http import JsonResponse
from sumo_k8_client import SumoK8Client

def submit_simulation(request):
    client = SumoK8Client()
    
    job = client.submit_job(
        scenario_id=request.POST["scenario_id"],
        sumo_files=request.FILES["scenario"].temporary_file_path(),
        cpu_request=int(request.POST.get("cpu", 2)),
        memory_gi=int(request.POST.get("memory", 4))
    )
    
    return JsonResponse({"job_id": job["job_id"]})
```

### Batch Processing

```python
import concurrent.futures
from sumo_k8_client import SumoK8Client

def run_batch_simulations(scenarios):
    client = SumoK8Client()
    
    # Submit all jobs
    jobs = []
    for scenario in scenarios:
        job = client.submit_job(
            scenario_id=scenario["id"],
            sumo_files=scenario["path"]
        )
        jobs.append(job["job_id"])
    
    # Wait for all to complete
    results = []
    for job_id in jobs:
        result = client.wait_for_completion(job_id)
        results.append(result)
    
    return results
```

## Autoscaler

The client includes built-in autoscaler support for AWS EKS, Google GKE, and Azure AKS.

### Installation

```bash
# For AWS EKS
pip install -e "./client[aws]"

# For Google GKE
pip install -e "./client[gcp]"

# For Azure AKS
pip install -e "./client[azure]"

# All cloud providers
pip install -e "./client[all-clouds]"
```

### Quick Start

```python
from sumo_k8_client import SumoK8Client, EKSNodeGroupScaler, run_autoscaler_loop

# Create client with admin access
client = SumoK8Client(admin_key="your_admin_key")

# Create scaler for your cloud
scaler = EKSNodeGroupScaler(
    cluster_name="my-cluster",
    nodegroup_name="sumo-workers",
    region="us-west-2"
)

# Run autoscaler (blocks forever)
run_autoscaler_loop(client, scaler, interval=60)
```

### Custom Configuration

```python
from sumo_k8_client import ScalingConfig, AutoscalerMetrics

config = ScalingConfig(
    min_nodes=2,              # Minimum nodes to maintain
    max_nodes=20,             # Maximum nodes to scale to
    jobs_per_node=2.0,        # Target jobs per node
    scale_up_threshold=1.5,   # Scale up when queue > nodes * 1.5
    scale_down_threshold=0.5, # Scale down when queue < nodes * 0.5
    scale_up_cooldown=60,     # Seconds between scale up actions
    scale_down_cooldown=300,  # Seconds between scale down actions
)

metrics = AutoscalerMetrics(client, config)
decision = metrics.get_scaling_decision()

print(f"Action: {decision.action}")
print(f"Current nodes: {decision.current_nodes}")
print(f"Target nodes: {decision.target_nodes}")
print(f"Pending jobs: {decision.pending_jobs}")
```

### Cloud-Specific Scalers

**AWS EKS:**
```python
from sumo_k8_client import EKSNodeGroupScaler

scaler = EKSNodeGroupScaler(
    cluster_name="my-cluster",
    nodegroup_name="sumo-workers",
    region="eu-central-1"  # Optional, uses AWS_DEFAULT_REGION if not set
)
```

**Google GKE:**
```python
from sumo_k8_client import GKENodePoolScaler

scaler = GKENodePoolScaler(
    project_id="my-project",
    zone="us-central1-a",
    cluster_name="my-cluster",
    nodepool_name="sumo-pool"
)
```

**Azure AKS:**
```python
from sumo_k8_client import AKSNodePoolScaler

scaler = AKSNodePoolScaler(
    subscription_id="your-subscription-id",
    resource_group="my-resource-group",
    cluster_name="my-cluster",
    nodepool_name="sumopool"
)
```

### Manual Scaling Control

```python
from sumo_k8_client import AutoscalerMetrics

client = SumoK8Client(admin_key="...")
metrics = AutoscalerMetrics(client)

# Get current metrics
m = metrics.get_metrics()
print(f"Pending: {m['pending_jobs']}, Running: {m['running_jobs']}, Nodes: {m['node_count']}")

# Get scaling recommendation
decision = metrics.get_scaling_decision()
if decision.action == "scale_up":
    print(f"Recommend scaling to {decision.target_nodes} nodes")
```

### Custom Scaler Implementation

```python
from sumo_k8_client import BaseNodeScaler

class MyCloudScaler(BaseNodeScaler):
    def get_current_count(self) -> int:
        # Return current node count
        return my_api.get_node_count()
    
    def scale_to(self, count: int) -> bool:
        # Scale to specified count
        return my_api.set_node_count(count)

# Use with autoscaler
scaler = MyCloudScaler()
run_autoscaler_loop(client, scaler)
```

### Running as a Service

```python
#!/usr/bin/env python3
"""autoscaler_service.py - Run as a systemd service or container"""

import logging
import os
from sumo_k8_client import (
    SumoK8Client, 
    ScalingConfig,
    EKSNodeGroupScaler, 
    run_autoscaler_loop
)

logging.basicConfig(level=logging.INFO)

def main():
    client = SumoK8Client(
        base_url=os.environ["SUMO_K8_URL"],
        admin_key=os.environ["SUMO_K8_ADMIN_KEY"]
    )
    
    scaler = EKSNodeGroupScaler(
        cluster_name=os.environ["EKS_CLUSTER"],
        nodegroup_name=os.environ["EKS_NODEGROUP"]
    )
    
    config = ScalingConfig(
        min_nodes=int(os.environ.get("MIN_NODES", 2)),
        max_nodes=int(os.environ.get("MAX_NODES", 20)),
    )
    
    def on_decision(decision):
        logging.info(f"Scaling decision: {decision.action} -> {decision.target_nodes} nodes")
    
    run_autoscaler_loop(
        client, 
        scaler, 
        config=config,
        interval=60,
        callback=on_decision
    )

if __name__ == "__main__":
    main()
```
