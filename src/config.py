"""Configuration management"""
import os
import logging
import tempfile

logger = logging.getLogger(__name__)

# Production configuration
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "100"))
MAX_JOB_DURATION_HOURS = int(os.getenv("MAX_JOB_DURATION_HOURS", "24"))
CONFIGMAP_CLEANUP_DELAY_SECONDS = int(os.getenv("CONFIGMAP_CLEANUP_DELAY_SECONDS", "300"))
MAX_CONCURRENT_JOBS_PER_TENANT = int(os.getenv("MAX_CONCURRENT_JOBS_PER_TENANT", "10"))
# Accepted-but-not-yet-dispatched jobs
MAX_QUEUED_JOBS_PER_TENANT = int(os.getenv("MAX_QUEUED_JOBS_PER_TENANT", "500"))
# Legacy local queue path (unused when S3 queue is enabled)
JOB_QUEUE_DIR = os.getenv("JOB_QUEUE_DIR", os.path.join(tempfile.gettempdir(), "sumo_job_queue"))
# Queue zip storage in object storage
QUEUE_S3_PREFIX = os.getenv("QUEUE_S3_PREFIX", "queued-zips")
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

# Default tenant limits
DEFAULT_MAX_CPU = int(os.getenv("DEFAULT_MAX_CPU", "32"))
DEFAULT_MAX_MEMORY_GI = int(os.getenv("DEFAULT_MAX_MEMORY_GI", "128"))
DEFAULT_MAX_CONCURRENT_JOBS = int(os.getenv("DEFAULT_MAX_CONCURRENT_JOBS", "2"))

# API key settings
API_KEY_PREFIX = os.getenv("API_KEY_PREFIX", "sk-")
API_KEY_LENGTH = int(os.getenv("API_KEY_LENGTH", "32"))

# Admin authentication
ADMIN_KEY = os.getenv("ADMIN_KEY", "")  # Set via environment variable or secret

# CORS
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Result storage
RESULT_STORAGE_TYPE = os.getenv("RESULT_STORAGE_TYPE", "auto")  # auto, pvc, s3, gcs, azure
RESULT_STORAGE_SIZE_GI = int(os.getenv("RESULT_STORAGE_SIZE_GI", "10"))

# Object storage (cloud)
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_REGION = os.getenv("S3_REGION", "us-east-1")
S3_IAM_ROLE_ARN = os.getenv("S3_IAM_ROLE_ARN", "")  # IAM role for IRSA
GCS_BUCKET = os.getenv("GCS_BUCKET", "")
AZURE_STORAGE_ACCOUNT = os.getenv("AZURE_STORAGE_ACCOUNT", "")
AZURE_CONTAINER = os.getenv("AZURE_CONTAINER", "")

# SUMO simulation image
SUMO_IMAGE = os.getenv("SUMO_IMAGE", "ghcr.io/eclipse-sumo/sumo:latest")

# Legacy ConfigMap sweeper (for old sumo-*-chunk* objects that missed cleanup)
ENABLE_LEGACY_CONFIGMAP_SWEEPER = os.getenv("ENABLE_LEGACY_CONFIGMAP_SWEEPER", "false").lower() == "true"
LEGACY_CONFIGMAP_SWEEPER_NAMESPACES = [
    v.strip() for v in os.getenv("LEGACY_CONFIGMAP_SWEEPER_NAMESPACES", "").split(",") if v.strip()
]
LEGACY_CONFIGMAP_SWEEPER_PREFIX = os.getenv("LEGACY_CONFIGMAP_SWEEPER_PREFIX", "sumo-")
LEGACY_CONFIGMAP_SWEEPER_NAME_CONTAINS = os.getenv("LEGACY_CONFIGMAP_SWEEPER_NAME_CONTAINS", "-chunk")
LEGACY_CONFIGMAP_SWEEPER_MIN_AGE_HOURS = int(os.getenv("LEGACY_CONFIGMAP_SWEEPER_MIN_AGE_HOURS", "6"))
LEGACY_CONFIGMAP_SWEEPER_MAX_DELETES_PER_RUN = int(os.getenv("LEGACY_CONFIGMAP_SWEEPER_MAX_DELETES_PER_RUN", "100"))

# Scheduling for simulation pods
# Default keeps current behavior (node-type=simulation). You can pass multiple values
# (comma-separated) to allow multiple node pools.
SIMULATION_NODE_SELECTOR_KEY = os.getenv("SIMULATION_NODE_SELECTOR_KEY", "node-type")
SIMULATION_NODE_SELECTOR_VALUES = [
    v.strip() for v in os.getenv("SIMULATION_NODE_SELECTOR_VALUES", "simulation").split(",") if v.strip()
]
