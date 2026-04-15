"""Estimated job cost from resource requests and wall-clock duration."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .cost_aws import get_job_cost_rates


def _normalize_dt(dt: Any) -> Optional[datetime]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    return None


def estimated_cost_usd(
    cpu_request: Optional[int],
    memory_gi: Optional[int],
    started_at: Any,
    finished_at: Any,
) -> Optional[float]:
    rates = get_job_cost_rates()
    if not rates:
        return None
    cpu_rate, mem_rate = rates
    if cpu_rate <= 0 and mem_rate <= 0:
        return None
    if not cpu_request or not memory_gi:
        return None
    s = _normalize_dt(started_at)
    e = _normalize_dt(finished_at)
    if not s or not e:
        return None
    hours = (e - s).total_seconds() / 3600.0
    if hours < 0:
        hours = 0.0
    total = hours * (
        float(cpu_request) * cpu_rate + float(memory_gi) * mem_rate
    )
    return round(total, 6)


def refresh_job_estimated_cost(cur, job_id) -> None:
    cur.execute(
        """SELECT cpu_request, memory_gi, started_at, finished_at
           FROM jobs WHERE job_id = %s""",
        (job_id,),
    )
    row = cur.fetchone()
    if not row:
        return
    cost = estimated_cost_usd(
        row["cpu_request"],
        row["memory_gi"],
        row["started_at"],
        row["finished_at"],
    )
    cur.execute(
        "UPDATE jobs SET estimated_cost_usd = %s WHERE job_id = %s",
        (cost, job_id),
    )
