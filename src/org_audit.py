"""Org-scoped audit lines to CloudWatch /transcality/org-{id}-{env}/audit (same group as PLAN Django)."""
import json
import os
import socket
import threading
from datetime import datetime, timezone

import boto3

from . import config

_cw_ensured: set = set()
_cw_lock = threading.Lock()


def org_id_from_tenant_id(tenant_id: str | None) -> int | None:
    if not tenant_id:
        return None
    text = str(tenant_id).strip()
    if text.startswith("org-"):
        parts = text.split("-")
        if len(parts) >= 2 and parts[1].isdigit():
            return int(parts[1])
    return None


def audit_env_suffix() -> str:
    env = (os.getenv("DEPLOY_ENV") or os.getenv("ENV") or "").lower().strip()
    if env in ("prod", "production", "plan"):
        return "prod"
    return "dev"


def env_from_tenant_id(tenant_id: str | None) -> str:
    """Per-simulation env from the tenant_id suffix (``org-{id}-{env}``).

    On the SHARED cluster the controller-wide DEPLOY_ENV/ENV cannot distinguish a
    prod sim from a dev sim — but the env is authoritatively encoded in the
    tenant_id (tenants are provisioned one-per-(org, env), and the pod's log
    STREAM ``org-{id}-{env}/{uuid}`` is already built from it). Fall back to the
    controller env only for a legacy bare ``org-{id}`` with no suffix.
    """
    if tenant_id:
        parts = str(tenant_id).strip().lower().split("-")
        if len(parts) >= 3 and parts[0] == "org":
            suffix = parts[2]
            if suffix in ("prod", "production", "plan"):
                return "prod"
            if suffix in ("dev", "development", "plantest", "test"):
                return "dev"
    return audit_env_suffix()


def org_audit_log_group_for_tenant(tenant_id: str | None) -> str | None:
    oid = org_id_from_tenant_id(tenant_id)
    if oid is None:
        return None
    # Derive the env from the tenant_id (per-sim) so prod sims log to the prod
    # group even on the shared cluster. This one function feeds BOTH sinks: the
    # controller audit lines AND the pod's ORG_AUDIT_LOG_GROUP (set in jobs.py),
    # so the SUMO pod logs land in the matching group.
    return f"/transcality/org-{oid}-{env_from_tenant_id(tenant_id)}/audit"


def _ensure_stream(client, group: str, stream: str) -> None:
    key = (group, stream)
    if key in _cw_ensured:
        return
    with _cw_lock:
        if key in _cw_ensured:
            return
        try:
            client.create_log_group(logGroupName=group)
        except Exception:
            pass
        try:
            client.create_log_stream(logGroupName=group, logStreamName=stream)
        except Exception:
            pass
        _cw_ensured.add(key)


def put_org_audit_line(tenant_id: str | None, record: dict) -> None:
    group = org_audit_log_group_for_tenant(tenant_id)
    if not group:
        return
    oid = org_id_from_tenant_id(tenant_id)
    payload = {
        "audit_key": record.get("audit_key") or "k8s.event",
        "component": record.get("component") or "k8s_controller",
        "log_type": record.get("log_type") or "k8s_controller",
        **record,
        "organisation_id": oid,
    }
    stream = f"sumo-k8-{socket.gethostname()}-{os.getpid()}"
    try:
        client = boto3.client("logs", region_name=config.AWS_REGION)
        _ensure_stream(client, group, stream)
        client.put_log_events(
            logGroupName=group,
            logStreamName=stream,
            logEvents=[{
                "timestamp": int(datetime.now(tz=timezone.utc).timestamp() * 1000),
                "message": json.dumps(payload, default=str),
            }],
        )
    except Exception:
        pass
