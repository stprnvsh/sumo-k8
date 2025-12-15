"""Configuration management"""
import os
import logging

logger = logging.getLogger(__name__)

# Production configuration
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "100"))
MAX_JOB_DURATION_HOURS = int(os.getenv("MAX_JOB_DURATION_HOURS", "24"))
CONFIGMAP_CLEANUP_DELAY_SECONDS = int(os.getenv("CONFIGMAP_CLEANUP_DELAY_SECONDS", "300"))
MAX_CONCURRENT_JOBS_PER_TENANT = int(os.getenv("MAX_CONCURRENT_JOBS_PER_TENANT", "10"))
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

# Default tenant limits
DEFAULT_MAX_CPU = int(os.getenv("DEFAULT_MAX_CPU", "10"))
DEFAULT_MAX_MEMORY_GI = int(os.getenv("DEFAULT_MAX_MEMORY_GI", "20"))
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
GCS_BUCKET = os.getenv("GCS_BUCKET", "")
AZURE_STORAGE_ACCOUNT = os.getenv("AZURE_STORAGE_ACCOUNT", "")
AZURE_CONTAINER = os.getenv("AZURE_CONTAINER", "")

