"""SUMO-K8 pip client package."""

from .client import (
    SumoK8Client,
    SumoK8Error,
    AuthenticationError,
    JobNotFoundError,
    QuotaExceededError,
    JobStatus,
    get_client,
)

__version__ = "1.1.0"

__all__ = [
    "SumoK8Client",
    "SumoK8Error",
    "AuthenticationError",
    "JobNotFoundError",
    "QuotaExceededError",
    "JobStatus",
    "get_client",
]
