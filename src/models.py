"""Pydantic models for request/response validation"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class TenantCreate(BaseModel):
    """Request model for creating a tenant"""
    tenant_id: str = Field(..., min_length=1, max_length=100, description="Unique tenant identifier")
    max_cpu: Optional[int] = Field(10, ge=1, le=100, description="Maximum CPU quota")
    max_memory_gi: Optional[int] = Field(20, ge=1, le=500, description="Maximum memory quota in Gi")
    max_concurrent_jobs: Optional[int] = Field(2, ge=1, le=50, description="Maximum concurrent jobs")

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

