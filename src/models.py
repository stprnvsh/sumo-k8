"""Pydantic models for request/response validation"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class TenantCreate(BaseModel):
    """Request model for creating a tenant"""
    tenant_id: str = Field(..., min_length=1, max_length=100, description="Unique tenant identifier")
    # Allow large quotas for high-throughput tenants (e.g. 20x 16CPU/32Gi jobs => 320CPU/640Gi).
    max_cpu: Optional[int] = Field(32, ge=1, le=1000, description="Maximum CPU quota")
    max_memory_gi: Optional[int] = Field(128, ge=1, le=5000, description="Maximum memory quota in Gi")
    max_concurrent_jobs: Optional[int] = Field(20, ge=1, le=200, description="Maximum concurrent jobs")

class TenantResponse(BaseModel):
    """Response model for tenant"""
    tenant_id: str
    namespace: str
    api_key: str
    max_cpu: int
    max_memory_gi: int
    max_concurrent_jobs: int
    created_at: datetime

class APIKeyRegenerate(BaseModel):
    """Request model for regenerating API key"""
    tenant_id: str

class JobStatusResponse(BaseModel):
    """Response model for job status"""
    job_id: str
    status: str
    submitted_at: Optional[datetime]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]

class JobSubmitResponse(BaseModel):
    """Response model for job submission"""
    job_id: str
    status: str
    config_file: str


class ReservationCreate(BaseModel):
    """Request model for creating a node reservation"""
    cpu_request: int = Field(2, ge=1, le=32, description="CPU cores to reserve")
    memory_gi: int = Field(4, ge=1, le=128, description="Memory in Gi to reserve")
    ttl_seconds: int = Field(
        600,
        ge=60,
        le=1800,
        description="Reservation lifetime in seconds before auto-expiry (override via RESERVATION_DEFAULT_TTL_SECONDS env)",
    )


class ReservationResponse(BaseModel):
    """Response model for a node reservation"""
    reservation_id: str
    tenant_id: str
    cpu_request: int
    memory_gi: int
    placeholder_pod: Optional[str]
    namespace: Optional[str]
    status: str
    created_at: Optional[datetime]
    expires_at: Optional[datetime]
    claimed_job_id: Optional[str]

