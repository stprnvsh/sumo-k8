"""Lightweight SUMO-K8 API client."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union
import json
import os
import time

import requests


class SumoK8Error(Exception):
    """Base exception for client errors."""


class AuthenticationError(SumoK8Error):
    """Raised when API/admin key is invalid."""


class JobNotFoundError(SumoK8Error):
    """Raised when a job is not found."""


class QuotaExceededError(SumoK8Error):
    """Raised when resource limits are exceeded."""


@dataclass
class JobStatus:
    """Current state of a SUMO-K8 job."""

    job_id: str
    status: str
    submitted_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result_location: Optional[str] = None
    result_files: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    @property
    def is_complete(self) -> bool:
        return self.status in {"SUCCEEDED", "FAILED"}

    @property
    def is_success(self) -> bool:
        return self.status == "SUCCEEDED"


class SumoK8Client:
    """
    Minimal pip-ready client for SUMO-K8.

    Reads defaults from:
    - SUMO_K8_URL
    - SUMO_K8_API_KEY
    - SUMO_K8_ADMIN_KEY
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        admin_key: Optional[str] = None,
        timeout: int = 30,
    ) -> None:
        self.base_url = (base_url or os.getenv("SUMO_K8_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("SUMO_K8_API_KEY", "")
        self.admin_key = admin_key or os.getenv("SUMO_K8_ADMIN_KEY", "")
        self.timeout = timeout

        if not self.base_url:
            raise ValueError("base_url required (or set SUMO_K8_URL)")

    def _headers(self, use_admin: bool = False) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if use_admin:
            if not self.admin_key:
                raise AuthenticationError("Admin key required for this endpoint")
            headers["X-Admin-Key"] = self.admin_key
        else:
            # Not all endpoints require tenant auth (e.g. /health, /ready).
            # Job/tenant endpoints validate separately.
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _require_api_key(self) -> None:
        if not self.api_key:
            raise AuthenticationError("API key required (set SUMO_K8_API_KEY or pass api_key=...)")

    @staticmethod
    def _error_detail(response: requests.Response) -> str:
        try:
            payload = response.json()
            return payload.get("detail", response.text)
        except Exception:
            return response.text

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        use_admin: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        headers = kwargs.pop("headers", {})
        headers.update(self._headers(use_admin=use_admin))
        kwargs["headers"] = headers
        kwargs.setdefault("timeout", self.timeout)

        try:
            response = requests.request(method, url, **kwargs)
        except requests.RequestException as exc:
            raise SumoK8Error(f"Request failed: {exc}") from exc

        if response.status_code == 401:
            raise AuthenticationError(self._error_detail(response))
        if response.status_code == 404:
            raise JobNotFoundError(self._error_detail(response))
        if response.status_code == 429:
            raise QuotaExceededError(self._error_detail(response))
        if response.status_code >= 400:
            raise SumoK8Error(f"API error ({response.status_code}): {self._error_detail(response)}")

        try:
            return response.json()
        except json.JSONDecodeError:
            return {"raw": response.text}

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health")

    def ready(self) -> bool:
        try:
            return self._request("GET", "/ready").get("status") == "ready"
        except SumoK8Error:
            return False

    def submit_job(
        self,
        scenario_id: str,
        sumo_files: Union[str, Path],
        cpu_request: int = 2,
        memory_gi: int = 4,
    ) -> Dict[str, Any]:
        self._require_api_key()
        path = Path(sumo_files)
        if not path.exists():
            raise FileNotFoundError(f"SUMO zip not found: {sumo_files}")

        with path.open("rb") as handle:
            files = {"sumo_files": (path.name, handle, "application/zip")}
            data = {
                "scenario_id": scenario_id,
                "cpu_request": cpu_request,
                "memory_gi": memory_gi,
            }
            return self._request("POST", "/jobs", data=data, files=files)

    def get_job_status(self, job_id: str) -> JobStatus:
        self._require_api_key()
        result = self._request("GET", f"/jobs/{job_id}")
        return JobStatus(
            job_id=result.get("job_id", job_id),
            status=result.get("status", "UNKNOWN"),
            submitted_at=result.get("submitted_at"),
            started_at=result.get("started_at"),
            finished_at=result.get("finished_at"),
            result_location=result.get("result_location"),
            result_files=result.get("result_files"),
            error=result.get("error"),
        )

    def get_job_logs(self, job_id: str) -> Dict[str, Any]:
        self._require_api_key()
        return self._request("GET", f"/jobs/{job_id}/logs")

    def get_job_results(self, job_id: str) -> Dict[str, Any]:
        self._require_api_key()
        return self._request("GET", f"/jobs/{job_id}/results")

    def wait_for_completion(
        self,
        job_id: str,
        timeout: int = 3600,
        poll_interval: int = 10,
    ) -> JobStatus:
        start = time.time()
        while True:
            status = self.get_job_status(job_id)
            if status.is_complete:
                return status
            if (time.time() - start) >= timeout:
                raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")
            time.sleep(poll_interval)

    def warmup(self, cpu_request: int = 2, memory_gi: int = 4, keep_alive_seconds: int = 300) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/admin/warmup?cpu_request={cpu_request}&memory_gi={memory_gi}&keep_alive_seconds={keep_alive_seconds}",
            use_admin=True,
        )

    def warmup_status(self) -> Dict[str, Any]:
        return self._request("GET", "/admin/warmup/status", use_admin=True)


_default_client: Optional[SumoK8Client] = None


def get_client() -> SumoK8Client:
    """Singleton client based on environment variables."""
    global _default_client
    if _default_client is None:
        _default_client = SumoK8Client()
    return _default_client
