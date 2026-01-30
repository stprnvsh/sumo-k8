"""
SUMO-K8 API Client

A Python client for interacting with the SUMO-K8 Kubernetes simulation controller.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, List, Iterator, Callable, Union
import os
import time
import logging
import json

try:
    import requests
except ImportError:
    raise ImportError("requests is required: pip install requests")

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class JobStatus:
    """Job status response."""
    job_id: str
    status: str  # PENDING, RUNNING, SUCCEEDED, FAILED
    scenario_id: str
    tenant_id: str = ""
    cpu_request: int = 0
    memory_gi: int = 0
    submitted_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result_location: Optional[str] = None
    result_files: Optional[Dict] = None
    error: Optional[str] = None
    
    @property
    def is_complete(self) -> bool:
        return self.status in ("SUCCEEDED", "FAILED")
    
    @property
    def is_success(self) -> bool:
        return self.status == "SUCCEEDED"


@dataclass
class TenantInfo:
    """Tenant information."""
    tenant_id: str
    namespace: str
    max_cpu: int
    max_memory_gi: int
    max_concurrent_jobs: int
    api_key: Optional[str] = None
    created_at: Optional[str] = None


@dataclass
class DashboardInfo:
    """Tenant dashboard information."""
    tenant_id: str
    plan_limits: Dict[str, int]
    current_usage: Dict[str, str]
    running_pods: int
    recent_jobs: List[Dict]
    stats: Dict[str, int]


@dataclass
class ClusterNode:
    """Cluster node information."""
    name: str
    status: str
    cpu_capacity: str
    memory_capacity: str
    cpu_allocatable: str
    memory_allocatable: str
    labels: Dict[str, str] = field(default_factory=dict)


# =============================================================================
# Exceptions
# =============================================================================

class SumoK8Error(Exception):
    """Base exception for SUMO-K8 client errors."""
    pass


class AuthenticationError(SumoK8Error):
    """Authentication failed."""
    pass


class JobNotFoundError(SumoK8Error):
    """Job not found."""
    pass


class QuotaExceededError(SumoK8Error):
    """Resource quota exceeded."""
    pass


class TenantNotFoundError(SumoK8Error):
    """Tenant not found."""
    pass


# =============================================================================
# Client
# =============================================================================

class SumoK8Client:
    """
    Client for SUMO-K8 Controller API.
    
    Args:
        base_url: API base URL (e.g., "http://localhost:8000")
        api_key: Tenant API key (for tenant operations)
        admin_key: Admin key (for admin operations)
        timeout: Request timeout in seconds
        
    Environment Variables:
        SUMO_K8_URL: Default base URL
        SUMO_K8_API_KEY: Default API key
        SUMO_K8_ADMIN_KEY: Default admin key
        
    Example:
        >>> client = SumoK8Client(
        ...     base_url="http://localhost:8000",
        ...     api_key="sk_abc123"
        ... )
        >>> job = client.submit_job("test", "/path/to/scenario.zip")
        >>> result = client.wait_for_completion(job["job_id"])
    """
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        admin_key: Optional[str] = None,
        timeout: int = 30
    ):
        self.base_url = (base_url or os.getenv("SUMO_K8_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("SUMO_K8_API_KEY", "")
        self.admin_key = admin_key or os.getenv("SUMO_K8_ADMIN_KEY", "")
        self.timeout = timeout
        
        if not self.base_url:
            raise ValueError("base_url required (or set SUMO_K8_URL env var)")
    
    # =========================================================================
    # Internal Methods
    # =========================================================================
    
    def _headers(self, use_admin: bool = False) -> Dict[str, str]:
        """Get headers for API requests."""
        headers = {}
        if use_admin and self.admin_key:
            headers["X-Admin-Key"] = self.admin_key
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
    
    def _request(
        self,
        method: str,
        endpoint: str,
        use_admin: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """Make API request with error handling."""
        url = f"{self.base_url}{endpoint}"
        
        kwargs.setdefault("timeout", self.timeout)
        headers = kwargs.pop("headers", {})
        headers.update(self._headers(use_admin))
        kwargs["headers"] = headers
        
        try:
            response = requests.request(method, url, **kwargs)
        except requests.RequestException as e:
            raise SumoK8Error(f"Request failed: {e}")
        
        return self._handle_response(response)
    
    def _handle_response(self, response: requests.Response) -> Dict[str, Any]:
        """Handle API response."""
        if response.status_code == 401:
            raise AuthenticationError("Invalid API key or admin key")
        elif response.status_code == 404:
            detail = self._get_error_detail(response)
            if "job" in detail.lower():
                raise JobNotFoundError(detail)
            elif "tenant" in detail.lower():
                raise TenantNotFoundError(detail)
            raise SumoK8Error(detail)
        elif response.status_code == 429:
            raise QuotaExceededError("Resource quota exceeded")
        elif response.status_code >= 400:
            raise SumoK8Error(f"API error ({response.status_code}): {self._get_error_detail(response)}")
        
        try:
            return response.json()
        except json.JSONDecodeError:
            return {"raw": response.text}
    
    def _get_error_detail(self, response: requests.Response) -> str:
        """Extract error detail from response."""
        try:
            return response.json().get("detail", response.text)
        except:
            return response.text
    
    # =========================================================================
    # Health & Status
    # =========================================================================
    
    def health(self) -> Dict[str, Any]:
        """
        Check API health status.
        
        Returns:
            Health status dict with k8s_available, db_available, etc.
        """
        return self._request("GET", "/health")
    
    def ready(self) -> bool:
        """
        Check if API is ready to accept requests.
        
        Returns:
            True if ready, False otherwise
        """
        try:
            result = self._request("GET", "/ready")
            return result.get("status") == "ready"
        except SumoK8Error:
            return False
    
    # =========================================================================
    # Job Operations (Tenant)
    # =========================================================================
    
    def submit_job(
        self,
        scenario_id: str,
        sumo_files: Union[str, Path],
        cpu_request: int = 2,
        memory_gi: int = 4
    ) -> Dict[str, Any]:
        """
        Submit a SUMO simulation job.
        
        Args:
            scenario_id: Unique scenario identifier (1-100 chars)
            sumo_files: Path to ZIP file containing SUMO scenario
            cpu_request: CPU cores to request (1-32)
            memory_gi: Memory in GiB (1-128)
            
        Returns:
            Job submission response with job_id, status, etc.
            
        Raises:
            FileNotFoundError: If sumo_files doesn't exist
            QuotaExceededError: If resource limits exceeded
            AuthenticationError: If API key is invalid
            
        Example:
            >>> job = client.submit_job(
            ...     scenario_id="geneva_morning",
            ...     sumo_files="/data/scenarios/geneva.zip",
            ...     cpu_request=4,
            ...     memory_gi=8
            ... )
            >>> print(job["job_id"])
        """
        if not self.api_key:
            raise AuthenticationError("API key required for job submission")
        
        path = Path(sumo_files)
        if not path.exists():
            raise FileNotFoundError(f"SUMO files not found: {sumo_files}")
        
        with open(path, "rb") as f:
            files = {"sumo_files": (path.name, f, "application/zip")}
            data = {
                "scenario_id": scenario_id,
                "cpu_request": cpu_request,
                "memory_gi": memory_gi
            }
            
            response = requests.post(
                f"{self.base_url}/jobs",
                headers=self._headers(),
                data=data,
                files=files,
                timeout=self.timeout
            )
        
        return self._handle_response(response)
    
    def get_job_status(self, job_id: str) -> JobStatus:
        """
        Get job status.
        
        Args:
            job_id: Job UUID
            
        Returns:
            JobStatus object with current state
        """
        result = self._request("GET", f"/jobs/{job_id}")
        return JobStatus(
            job_id=result.get("job_id", job_id),
            status=result.get("status", "UNKNOWN"),
            scenario_id=result.get("scenario_id", ""),
            tenant_id=result.get("tenant_id", ""),
            cpu_request=result.get("cpu_request", 0),
            memory_gi=result.get("memory_gi", 0),
            submitted_at=result.get("submitted_at"),
            started_at=result.get("started_at"),
            completed_at=result.get("completed_at"),
            result_location=result.get("result_location"),
            result_files=result.get("result_files"),
            error=result.get("error")
        )
    
    def get_job_logs(self, job_id: str) -> Dict[str, Any]:
        """
        Get job logs snapshot.
        
        Args:
            job_id: Job UUID
            
        Returns:
            Logs response with pod_name, logs, etc.
        """
        return self._request("GET", f"/jobs/{job_id}/logs")
    
    def stream_job_logs(
        self,
        job_id: str,
        callback: Optional[Callable[[str], None]] = None
    ) -> Iterator[str]:
        """
        Stream job logs in real-time via Server-Sent Events.
        
        Args:
            job_id: Job UUID
            callback: Optional callback for each log line
            
        Yields:
            Log lines as they arrive
            
        Example:
            >>> for line in client.stream_job_logs(job_id):
            ...     print(line)
        """
        url = f"{self.base_url}/jobs/{job_id}/logs/stream"
        
        with requests.get(
            url,
            headers=self._headers(),
            stream=True,
            timeout=None
        ) as response:
            self._handle_response(response) if response.status_code >= 400 else None
            
            for line in response.iter_lines(decode_unicode=True):
                if line and line.startswith("data: "):
                    log_line = line[6:]
                    if callback:
                        callback(log_line)
                    yield log_line
    
    def get_job_results(self, job_id: str) -> Dict[str, Any]:
        """
        Get job results.
        
        Args:
            job_id: Job UUID
            
        Returns:
            Results with storage_type, result_location, files, etc.
        """
        return self._request("GET", f"/jobs/{job_id}/results")
    
    # =========================================================================
    # Convenience Methods
    # =========================================================================
    
    def wait_for_completion(
        self,
        job_id: str,
        timeout: int = 3600,
        poll_interval: int = 10,
        progress_callback: Optional[Callable[[JobStatus], None]] = None
    ) -> JobStatus:
        """
        Wait for job to complete.
        
        Args:
            job_id: Job UUID
            timeout: Maximum wait time in seconds (default: 1 hour)
            poll_interval: Seconds between status checks
            progress_callback: Optional callback on each poll
            
        Returns:
            Final JobStatus
            
        Raises:
            TimeoutError: If job doesn't complete within timeout
            
        Example:
            >>> result = client.wait_for_completion(
            ...     job_id,
            ...     timeout=7200,
            ...     progress_callback=lambda s: print(f"Status: {s.status}")
            ... )
        """
        start_time = time.time()
        
        while True:
            status = self.get_job_status(job_id)
            
            if progress_callback:
                progress_callback(status)
            
            if status.is_complete:
                return status
            
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")
            
            time.sleep(poll_interval)
    
    def submit_and_wait(
        self,
        scenario_id: str,
        sumo_files: Union[str, Path],
        cpu_request: int = 2,
        memory_gi: int = 4,
        timeout: int = 3600,
        progress_callback: Optional[Callable[[JobStatus], None]] = None
    ) -> JobStatus:
        """
        Submit job and wait for completion in one call.
        
        Args:
            scenario_id: Unique scenario identifier
            sumo_files: Path to ZIP file
            cpu_request: CPU cores
            memory_gi: Memory in GiB
            timeout: Maximum wait time
            progress_callback: Optional progress callback
            
        Returns:
            Final JobStatus
            
        Example:
            >>> result = client.submit_and_wait(
            ...     "test_scenario",
            ...     "/path/to/scenario.zip",
            ...     cpu_request=4,
            ...     memory_gi=8
            ... )
            >>> if result.is_success:
            ...     print(f"Results at: {result.result_location}")
        """
        job = self.submit_job(scenario_id, sumo_files, cpu_request, memory_gi)
        job_id = job["job_id"]
        
        logger.info(f"Job submitted: {job_id}")
        
        return self.wait_for_completion(
            job_id,
            timeout=timeout,
            progress_callback=progress_callback
        )
    
    # =========================================================================
    # Dashboard (Tenant)
    # =========================================================================
    
    def get_dashboard(self) -> DashboardInfo:
        """
        Get tenant dashboard with quota usage and recent jobs.
        
        Returns:
            DashboardInfo with limits, usage, and job stats
        """
        result = self._request("GET", "/tenants/me/dashboard")
        return DashboardInfo(
            tenant_id=result.get("tenant_id", ""),
            plan_limits=result.get("plan_limits", {}),
            current_usage=result.get("current_usage", {}),
            running_pods=result.get("running_pods", 0),
            recent_jobs=result.get("recent_jobs", []),
            stats=result.get("stats", {})
        )
    
    # =========================================================================
    # Admin Operations
    # =========================================================================
    
    def register_tenant(
        self,
        tenant_id: str,
        max_cpu: int = 10,
        max_memory_gi: int = 32,
        max_concurrent_jobs: int = 5
    ) -> TenantInfo:
        """
        Register a new tenant (admin operation).
        
        Args:
            tenant_id: Unique tenant identifier
            max_cpu: Maximum CPU cores for tenant
            max_memory_gi: Maximum memory in GiB
            max_concurrent_jobs: Maximum concurrent jobs
            
        Returns:
            TenantInfo with api_key for the new tenant
            
        Requires:
            admin_key to be set
        """
        if not self.admin_key:
            raise AuthenticationError("Admin key required")
        
        result = self._request(
            "POST",
            "/auth/register",
            use_admin=True,
            json={
                "tenant_id": tenant_id,
                "max_cpu": max_cpu,
                "max_memory_gi": max_memory_gi,
                "max_concurrent_jobs": max_concurrent_jobs
            }
        )
        
        return TenantInfo(
            tenant_id=result.get("tenant_id", tenant_id),
            namespace=result.get("namespace", ""),
            max_cpu=result.get("max_cpu", max_cpu),
            max_memory_gi=result.get("max_memory_gi", max_memory_gi),
            max_concurrent_jobs=result.get("max_concurrent_jobs", max_concurrent_jobs),
            api_key=result.get("api_key"),
            created_at=result.get("created_at")
        )
    
    def list_tenants(self) -> List[TenantInfo]:
        """
        List all tenants (admin operation).
        
        Returns:
            List of TenantInfo objects
        """
        result = self._request("GET", "/auth/tenants", use_admin=True)
        tenants = result.get("tenants", [])
        
        return [
            TenantInfo(
                tenant_id=t.get("tenant_id", ""),
                namespace=t.get("namespace", ""),
                max_cpu=t.get("max_cpu", 0),
                max_memory_gi=t.get("max_memory_gi", 0),
                max_concurrent_jobs=t.get("max_concurrent_jobs", 0),
                created_at=t.get("created_at")
            )
            for t in tenants
        ]
    
    def get_cluster_status(self) -> Dict[str, Any]:
        """
        Get cluster status (admin operation).
        
        Returns:
            Cluster status with nodes list
        """
        return self._request("GET", "/admin/cluster", use_admin=True)
    
    def get_cluster_activity(self) -> Dict[str, Any]:
        """
        Get cluster activity stats (admin operation).
        
        Returns:
            Activity stats with nodes, pods, jobs counts
        """
        return self._request("GET", "/admin/activity", use_admin=True)
    
    def list_all_jobs(self, status: Optional[str] = None) -> List[Dict]:
        """
        List all jobs across tenants (admin operation).
        
        Args:
            status: Optional filter by status (PENDING, RUNNING, SUCCEEDED, FAILED)
            
        Returns:
            List of job dicts
        """
        params = {}
        if status:
            params["status"] = status
        
        result = self._request("GET", "/admin/jobs", use_admin=True, params=params)
        return result.get("jobs", [])


# =============================================================================
# Convenience Functions
# =============================================================================

_default_client: Optional[SumoK8Client] = None


def get_client() -> SumoK8Client:
    """
    Get default SUMO-K8 client using environment variables.
    
    Environment Variables:
        SUMO_K8_URL: API base URL (required)
        SUMO_K8_API_KEY: Tenant API key
        SUMO_K8_ADMIN_KEY: Admin key
        
    Returns:
        Configured SumoK8Client
    """
    global _default_client
    
    if _default_client is None:
        _default_client = SumoK8Client()
    
    return _default_client
