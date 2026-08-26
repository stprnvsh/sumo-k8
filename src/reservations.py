"""Node reservation management — pre-warm a simulation node before submitting a job."""
import uuid
import logging
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
from kubernetes import client as k8s_client
from .database import get_db
from .k8s_client import k8s_available, k8s_core
from .config import (
    RESERVATION_DEFAULT_TTL_SECONDS,
    RESERVATION_MAX_TTL_SECONDS,
    PLACEHOLDER_IMAGE,
    SIMULATION_NODE_SELECTOR_KEY,
    SIMULATION_NODE_SELECTOR_VALUES,
    SIMULATION_PREFERRED_ZONES,
)

logger = logging.getLogger(__name__)

_PLACEHOLDER_APP_LABEL = "sumo-k8-placeholder"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_reservation(tenant: dict, cpu_request: int, memory_gi: int, ttl_seconds: int) -> dict:
    """Create a DB reservation and launch a lightweight placeholder pod to pre-warm a node."""
    if not k8s_available:
        raise HTTPException(status_code=503, detail="Kubernetes not available")

    ttl_seconds = min(ttl_seconds, RESERVATION_MAX_TTL_SECONDS)
    reservation_id = str(uuid.uuid4())
    pod_name = f"rsv-{reservation_id[:8]}"
    namespace = tenant["namespace"]
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

    pod = _build_placeholder_pod(pod_name, namespace, cpu_request, memory_gi, reservation_id)
    try:
        k8s_core.create_namespaced_pod(namespace, pod)
        logger.info("Created placeholder pod %s for reservation %s", pod_name, reservation_id)
    except Exception as e:
        logger.error("Failed to create placeholder pod for reservation %s: %s", reservation_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to create placeholder pod: {e}")

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO reservations
               (reservation_id, tenant_id, cpu_request, memory_gi,
                placeholder_pod, namespace, status, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s, 'PENDING_NODE', %s)""",
            (reservation_id, tenant["tenant_id"], cpu_request, memory_gi,
             pod_name, namespace, expires_at),
        )

    return _to_response(
        reservation_id=reservation_id,
        tenant_id=tenant["tenant_id"],
        cpu_request=cpu_request,
        memory_gi=memory_gi,
        placeholder_pod=pod_name,
        namespace=namespace,
        status="PENDING_NODE",
        created_at=datetime.now(timezone.utc),
        expires_at=expires_at,
        claimed_job_id=None,
    )


def get_reservation(reservation_id: str, tenant_id: str) -> dict:
    """Return reservation details, promoting PENDING_NODE → READY if the pod is now Running."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM reservations WHERE reservation_id = %s AND tenant_id = %s",
            (reservation_id, tenant_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Reservation not found")
        row = dict(row)

    if row["status"] == "PENDING_NODE" and row.get("placeholder_pod") and k8s_available:
        try:
            pod = k8s_core.read_namespaced_pod(row["placeholder_pod"], row["namespace"])
            if pod.status and pod.status.phase == "Running":
                with get_db() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        """UPDATE reservations SET status = 'READY'
                           WHERE reservation_id = %s AND status = 'PENDING_NODE'""",
                        (reservation_id,),
                    )
                row["status"] = "READY"
        except Exception:
            pass

    return _format_row(row)


def delete_reservation(reservation_id: str, tenant_id: str) -> None:
    """Cancel a reservation and delete its placeholder pod."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM reservations WHERE reservation_id = %s AND tenant_id = %s",
            (reservation_id, tenant_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Reservation not found")
        row = dict(row)
        if row["status"] in ("CLAIMED", "EXPIRED"):
            raise HTTPException(
                status_code=409,
                detail=f"Reservation already {row['status'].lower()}"
            )

        _delete_placeholder_pod(row.get("placeholder_pod"), row.get("namespace"))
        cur.execute(
            "UPDATE reservations SET status = 'EXPIRED' WHERE reservation_id = %s",
            (reservation_id,),
        )


def claim_reservation(reservation_id: str, tenant_id: str, job_id: str) -> None:
    """
    Atomically claim a reservation for a job submission.

    Deletes the placeholder pod so its resources are freed before the simulation
    pod is scheduled, then marks the reservation CLAIMED.  Must be called before
    inserting the job row so that a crash between the two leaves a clearly
    consumed reservation rather than dangling state.
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT * FROM reservations
               WHERE reservation_id = %s AND tenant_id = %s
               FOR UPDATE""",
            (reservation_id, tenant_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Reservation not found")
        row = dict(row)

        if row["status"] == "CLAIMED":
            raise HTTPException(status_code=409, detail="Reservation already claimed")
        if row["status"] in ("EXPIRED", "FAILED"):
            raise HTTPException(
                status_code=410,
                detail=f"Reservation is {row['status'].lower()} and can no longer be used",
            )

        expires_at = row["expires_at"]
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            cur.execute(
                "UPDATE reservations SET status = 'EXPIRED' WHERE reservation_id = %s",
                (reservation_id,),
            )
            raise HTTPException(status_code=410, detail="Reservation has expired")

        _delete_placeholder_pod(row.get("placeholder_pod"), row.get("namespace"))

        cur.execute(
            """UPDATE reservations
               SET status = 'CLAIMED', claimed_job_id = %s
               WHERE reservation_id = %s""",
            (job_id, reservation_id),
        )


# ---------------------------------------------------------------------------
# Reconciler helpers
# ---------------------------------------------------------------------------

def expire_stale_reservations() -> None:
    """Delete placeholder pods and mark EXPIRED for reservations past their TTL."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT reservation_id, placeholder_pod, namespace
               FROM reservations
               WHERE status IN ('PENDING_NODE', 'READY')
                 AND expires_at < NOW()""",
        )
        rows = [dict(r) for r in cur.fetchall()]

    for row in rows:
        try:
            _delete_placeholder_pod(row["placeholder_pod"], row["namespace"])
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute(
                    """UPDATE reservations SET status = 'EXPIRED'
                       WHERE reservation_id = %s AND status IN ('PENDING_NODE', 'READY')""",
                    (row["reservation_id"],),
                )
            logger.info("Expired reservation %s", row["reservation_id"])
        except Exception as e:
            logger.warning("Failed to expire reservation %s: %s", row["reservation_id"], e)


def sync_reservation_statuses() -> None:
    """Promote PENDING_NODE → READY when the placeholder pod transitions to Running."""
    if not k8s_available:
        return

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT reservation_id, placeholder_pod, namespace
               FROM reservations WHERE status = 'PENDING_NODE'""",
        )
        rows = [dict(r) for r in cur.fetchall()]

    for row in rows:
        if not row.get("placeholder_pod") or not row.get("namespace"):
            continue
        try:
            pod = k8s_core.read_namespaced_pod(row["placeholder_pod"], row["namespace"])
            if pod.status and pod.status.phase == "Running":
                with get_db() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        """UPDATE reservations SET status = 'READY'
                           WHERE reservation_id = %s AND status = 'PENDING_NODE'""",
                        (row["reservation_id"],),
                    )
                logger.info("Reservation %s node is READY", row["reservation_id"])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_placeholder_pod(
    name: str,
    namespace: str,
    cpu: int,
    memory_gi: int,
    reservation_id: str,
) -> k8s_client.V1Pod:
    """Build a busybox pod that holds a node slot without pulling the SUMO image."""
    node_selector = None
    affinity = None

    if SIMULATION_NODE_SELECTOR_KEY and SIMULATION_NODE_SELECTOR_VALUES:
        if len(SIMULATION_NODE_SELECTOR_VALUES) == 1:
            node_selector = {SIMULATION_NODE_SELECTOR_KEY: SIMULATION_NODE_SELECTOR_VALUES[0]}
        else:
            affinity = k8s_client.V1Affinity(
                node_affinity=k8s_client.V1NodeAffinity(
                    required_during_scheduling_ignored_during_execution=k8s_client.V1NodeSelector(
                        node_selector_terms=[
                            k8s_client.V1NodeSelectorTerm(
                                match_expressions=[
                                    k8s_client.V1NodeSelectorRequirement(
                                        key=SIMULATION_NODE_SELECTOR_KEY,
                                        operator="In",
                                        values=SIMULATION_NODE_SELECTOR_VALUES,
                                    )
                                ]
                            )
                        ]
                    )
                )
            )

    if SIMULATION_PREFERRED_ZONES:
        preferred = [
            k8s_client.V1PreferredSchedulingTerm(
                weight=max(1, len(SIMULATION_PREFERRED_ZONES) - idx),
                preference=k8s_client.V1NodeSelectorTerm(
                    match_expressions=[
                        k8s_client.V1NodeSelectorRequirement(
                            key="topology.kubernetes.io/zone",
                            operator="In",
                            values=[zone],
                        )
                    ]
                ),
            )
            for idx, zone in enumerate(SIMULATION_PREFERRED_ZONES)
        ]
        if affinity is None:
            affinity = k8s_client.V1Affinity(
                node_affinity=k8s_client.V1NodeAffinity(
                    preferred_during_scheduling_ignored_during_execution=preferred
                )
            )
        else:
            if affinity.node_affinity is None:
                affinity.node_affinity = k8s_client.V1NodeAffinity()
            affinity.node_affinity.preferred_during_scheduling_ignored_during_execution = preferred

    return k8s_client.V1Pod(
        metadata=k8s_client.V1ObjectMeta(
            name=name,
            namespace=namespace,
            labels={
                "app": _PLACEHOLDER_APP_LABEL,
                "reservation-id": reservation_id,
            },
            annotations={
                # Prevent Karpenter from consolidating the node while the reservation is live.
                "karpenter.sh/do-not-disrupt": "true",
            },
        ),
        spec=k8s_client.V1PodSpec(
            node_selector=node_selector,
            affinity=affinity,
            restart_policy="Never",
            containers=[
                k8s_client.V1Container(
                    name="placeholder",
                    image=PLACEHOLDER_IMAGE,
                    command=["sh", "-c", "sleep infinity"],
                    resources=k8s_client.V1ResourceRequirements(
                        requests={"cpu": str(cpu), "memory": f"{memory_gi}Gi"},
                        limits={"cpu": str(cpu), "memory": f"{memory_gi}Gi"},
                    ),
                )
            ],
        ),
    )


def _delete_placeholder_pod(pod_name: str | None, namespace: str | None) -> None:
    if not pod_name or not namespace or not k8s_available:
        return
    try:
        k8s_core.delete_namespaced_pod(
            pod_name,
            namespace,
            body=k8s_client.V1DeleteOptions(grace_period_seconds=0),
        )
        logger.info("Deleted placeholder pod %s/%s", namespace, pod_name)
    except k8s_client.exceptions.ApiException as e:
        if e.status != 404:
            logger.warning("Could not delete placeholder pod %s/%s: %s", namespace, pod_name, e)
    except Exception as e:
        logger.warning("Could not delete placeholder pod %s/%s: %s", namespace, pod_name, e)


def _to_response(
    reservation_id, tenant_id, cpu_request, memory_gi,
    placeholder_pod, namespace, status, created_at, expires_at, claimed_job_id,
) -> dict:
    return {
        "reservation_id": str(reservation_id),
        "tenant_id": tenant_id,
        "cpu_request": cpu_request,
        "memory_gi": memory_gi,
        "placeholder_pod": placeholder_pod,
        "namespace": namespace,
        "status": status,
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
        "expires_at": expires_at.isoformat() if hasattr(expires_at, "isoformat") else expires_at,
        "claimed_job_id": str(claimed_job_id) if claimed_job_id else None,
    }


def _format_row(row: dict) -> dict:
    return {
        "reservation_id": str(row["reservation_id"]),
        "tenant_id": row["tenant_id"],
        "cpu_request": row["cpu_request"],
        "memory_gi": row["memory_gi"],
        "placeholder_pod": row.get("placeholder_pod"),
        "namespace": row.get("namespace"),
        "status": row["status"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "expires_at": row["expires_at"].isoformat() if row.get("expires_at") else None,
        "claimed_job_id": str(row["claimed_job_id"]) if row.get("claimed_job_id") else None,
    }
