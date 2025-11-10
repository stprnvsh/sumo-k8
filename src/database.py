"""Database connection pooling and utilities"""
import os
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
import logging
from .config import DB_POOL_MIN, DB_POOL_MAX

logger = logging.getLogger(__name__)

# Database connection pool
db_pool = None

def init_db_pool():
    """Initialize database connection pool"""
    global db_pool
    db_url = os.getenv("DATABASE_URL", "postgresql://localhost/sumo_k8")
    try:
        db_pool = psycopg2.pool.ThreadedConnectionPool(
            DB_POOL_MIN,
            DB_POOL_MAX,
            db_url,
            cursor_factory=RealDictCursor
        )
        logger.info(f"Database connection pool initialized ({DB_POOL_MIN}-{DB_POOL_MAX} connections)")
    except Exception as e:
        logger.error(f"Failed to create database pool: {e}")
        raise

@contextmanager
def get_db():
    """Get database connection from pool with automatic cleanup"""
    global db_pool
    if db_pool is None:
        init_db_pool()
    
    conn = None
    try:
        conn = db_pool.getconn()
        yield conn
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        if conn:
            db_pool.putconn(conn)

def close_db_pool():
    """Close database connection pool"""
    global db_pool
    if db_pool:
        db_pool.closeall()
        logger.info("Database pool closed")

