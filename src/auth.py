"""Authentication and tenant management"""
import secrets
import string
import psycopg2
from fastapi import HTTPException, Header
from typing import Optional
import logging
from .database import get_db
from .config import API_KEY_PREFIX, API_KEY_LENGTH, DEFAULT_MAX_CPU, DEFAULT_MAX_MEMORY_GI, DEFAULT_MAX_CONCURRENT_JOBS

logger = logging.getLogger(__name__)

def generate_api_key() -> str:
    """Generate a secure API key"""
    # Generate random string
    alphabet = string.ascii_letters + string.digits
    random_part = ''.join(secrets.choice(alphabet) for _ in range(API_KEY_LENGTH))
    return f"{API_KEY_PREFIX}{random_part}"

def auth_tenant(api_key: str):
    """Authenticate tenant by API key"""
    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid API key format")
    
    # Clean up API key (remove whitespace)
    api_key = api_key.strip()
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tenants WHERE api_key = %s", (api_key,))
        tenant = cur.fetchone()
        if not tenant:
            # Log for debugging (don't expose in production)
            logger.debug(f"API key lookup failed for key starting with: {api_key[:10]}...")
            raise HTTPException(status_code=401, detail="Invalid API key")
        return dict(tenant)

def get_tenant_from_header(authorization: Optional[str] = Header(None, alias="Authorization")):
    """Extract and authenticate tenant from Authorization header"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    
    # Handle both "Bearer <key>" and just "<key>" formats
    api_key = authorization.replace("Bearer ", "").strip()
    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid API key format")
    
    logger.debug(f"Authenticating with API key starting with: {api_key[:10]}...")
    return auth_tenant(api_key)

def create_tenant(tenant_id: str, max_cpu: Optional[int] = None, 
                  max_memory_gi: Optional[int] = None, 
                  max_concurrent_jobs: Optional[int] = None):
    """Create a new tenant with generated API key"""
    # Use defaults if not provided
    max_cpu = max_cpu or DEFAULT_MAX_CPU
    max_memory_gi = max_memory_gi or DEFAULT_MAX_MEMORY_GI
    max_concurrent_jobs = max_concurrent_jobs or DEFAULT_MAX_CONCURRENT_JOBS
    
    # Generate API key
    api_key = generate_api_key()
    namespace = tenant_id.lower().replace('_', '-').replace(' ', '-')
    
    with get_db() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """INSERT INTO tenants (tenant_id, namespace, api_key, max_cpu, max_memory_gi, max_concurrent_jobs)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING *""",
                (tenant_id, namespace, api_key, max_cpu, max_memory_gi, max_concurrent_jobs)
            )
            tenant = cur.fetchone()
            conn.commit()
            logger.info(f"Created tenant {tenant_id} with namespace {namespace}")
            return dict(tenant)
        except psycopg2.IntegrityError as e:
            conn.rollback()
            if 'tenant_id' in str(e):
                raise HTTPException(status_code=409, detail=f"Tenant {tenant_id} already exists")
            elif 'namespace' in str(e):
                raise HTTPException(status_code=409, detail=f"Namespace {namespace} already exists")
            raise HTTPException(status_code=400, detail=f"Failed to create tenant: {str(e)}")

def regenerate_api_key(tenant_id: str):
    """Regenerate API key for a tenant"""
    new_api_key = generate_api_key()
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE tenants SET api_key = %s WHERE tenant_id = %s RETURNING *",
            (new_api_key, tenant_id)
        )
        tenant = cur.fetchone()
        if not tenant:
            raise HTTPException(status_code=404, detail=f"Tenant {tenant_id} not found")
        conn.commit()
        logger.info(f"Regenerated API key for tenant {tenant_id}")
        return dict(tenant)

def get_tenant(tenant_id: str):
    """Get tenant by ID"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tenants WHERE tenant_id = %s", (tenant_id,))
        tenant = cur.fetchone()
        if not tenant:
            raise HTTPException(status_code=404, detail=f"Tenant {tenant_id} not found")
        return dict(tenant)

def list_tenants():
    """List all tenants"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT tenant_id, namespace, max_cpu, max_memory_gi, max_concurrent_jobs, created_at FROM tenants ORDER BY created_at DESC")
        tenants = [dict(t) for t in cur.fetchall()]
        return tenants

def update_tenant_limits(tenant_id: str, max_cpu: Optional[int] = None,
                         max_memory_gi: Optional[int] = None,
                         max_concurrent_jobs: Optional[int] = None):
    """Update tenant resource limits"""
    updates = []
    values = []
    
    if max_cpu is not None:
        updates.append("max_cpu = %s")
        values.append(max_cpu)
    if max_memory_gi is not None:
        updates.append("max_memory_gi = %s")
        values.append(max_memory_gi)
    if max_concurrent_jobs is not None:
        updates.append("max_concurrent_jobs = %s")
        values.append(max_concurrent_jobs)
    
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")
    
    values.append(tenant_id)
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE tenants SET {', '.join(updates)} WHERE tenant_id = %s RETURNING *",
            values
        )
        tenant = cur.fetchone()
        if not tenant:
            raise HTTPException(status_code=404, detail=f"Tenant {tenant_id} not found")
        conn.commit()
        logger.info(f"Updated limits for tenant {tenant_id}")
        return dict(tenant)

