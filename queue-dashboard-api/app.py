import os
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from fastapi import FastAPI, Query
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool


DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required")

DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "5"))

pool: Optional[ThreadedConnectionPool] = None


class TTLCache:
    def __init__(self, ttl_seconds: int):
        self.ttl_seconds = ttl_seconds
        self._data: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str) -> Any:
        item = self._data.get(key)
        if not item:
            return None
        ts, value = item
        if time.time() - ts > self.ttl_seconds:
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._data[key] = (time.time(), value)


cache = TTLCache(CACHE_TTL_SECONDS)


def init_pool() -> None:
    global pool
    if pool is not None:
        return
    pool = ThreadedConnectionPool(
        DB_POOL_MIN,
        DB_POOL_MAX,
        DATABASE_URL,
        cursor_factory=RealDictCursor,
    )


@contextmanager
def get_conn():
    global pool
    if pool is None:
        init_pool()
    assert pool is not None
    conn = pool.getconn()
    try:
        yield conn
    finally:
        pool.putconn(conn)


def cache_key(path: str, params: Dict[str, Any]) -> str:
    parts = [path]
    for k in sorted(params.keys()):
        parts.append(f"{k}={params[k]}")
    return "&".join(parts)


app = FastAPI(title="Queue Dashboard API", version="0.1.0")


@app.get("/stats")
def stats():
    key = cache_key("/stats", {})
    cached = cache.get(key)
    if cached is not None:
        return cached

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT status, COUNT(*)::int AS count FROM jobs GROUP BY status"
        )
        by_status = {row["status"]: row["count"] for row in cur.fetchall()}

        cur.execute(
            "SELECT MIN(submitted_at) AS queue_start_at "
            "FROM jobs WHERE status IN ('QUEUED','PENDING')"
        )
        queue_start_at = cur.fetchone()["queue_start_at"]

    out = {
        "counts": {
            "queued": by_status.get("QUEUED", 0),
            "pending": by_status.get("PENDING", 0),
            "running": by_status.get("RUNNING", 0),
            "succeeded": by_status.get("SUCCEEDED", 0),
            "failed": by_status.get("FAILED", 0),
        },
        "queue_start_at": queue_start_at.isoformat() if queue_start_at else None,
    }
    cache.set(key, out)
    return out


@app.get("/time_series")
def time_series(minutes: int = Query(60, ge=1, le=720)):
    key = cache_key("/time_series", {"minutes": minutes})
    cached = cache.get(key)
    if cached is not None:
        return cached

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
              date_trunc('minute', submitted_at) AS minute,
              COUNT(*)::int AS submitted_count
            FROM jobs
            WHERE submitted_at >= now() - (%s || ' minutes')::interval
            GROUP BY minute
            ORDER BY minute ASC
            """,
            (minutes,),
        )
        rows = cur.fetchall()

    out = {
        "minutes": minutes,
        "series": [
            {"minute": r["minute"].isoformat(), "submitted": r["submitted_count"]}
            for r in rows
        ],
    }
    cache.set(key, out)
    return out


@app.get("/recent_jobs")
def recent_jobs(limit: int = Query(50, ge=1, le=200)):
    key = cache_key("/recent_jobs", {"limit": limit})
    cached = cache.get(key)
    if cached is not None:
        return cached

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
              job_id::text AS job_id,
              tenant_id,
              status,
              submitted_at,
              started_at,
              finished_at,
              cpu_request,
              memory_gi,
              scenario_data->>'scenario_id' AS scenario_id
            FROM jobs
            ORDER BY submitted_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()

    out = {
        "limit": limit,
        "jobs": [
            {
                "job_id": r["job_id"],
                "tenant_id": r["tenant_id"],
                "status": r["status"],
                "submitted_at": r["submitted_at"].isoformat() if r["submitted_at"] else None,
                "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
                "cpu_request": r["cpu_request"],
                "memory_gi": r["memory_gi"],
                "scenario_id": r["scenario_id"],
            }
            for r in rows
        ],
    }
    cache.set(key, out)
    return out

