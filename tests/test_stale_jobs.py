from types import SimpleNamespace

from kubernetes import client

from src.reconciler import _classify_k8s_workload, _terminal_status_from_k8s_job


def test_classify_missing_job(monkeypatch):
    def _read(*_a, **_k):
        raise client.exceptions.ApiException(status=404)

    monkeypatch.setattr("src.reconciler.k8s_available", True)
    monkeypatch.setattr("src.reconciler.k8s_batch.read_namespaced_job", _read)
    assert _classify_k8s_workload("org-1-prod", "sim-dead") == "missing"


def test_classify_live_active_job(monkeypatch):
    job = SimpleNamespace(
        status=SimpleNamespace(succeeded=0, failed=0, active=1, conditions=[]),
    )

    monkeypatch.setattr("src.reconciler.k8s_available", True)
    monkeypatch.setattr("src.reconciler.k8s_batch.read_namespaced_job", lambda *_a, **_k: job)
    monkeypatch.setattr("src.reconciler._job_pod_phase_running", lambda *_a, **_k: False)
    assert _classify_k8s_workload("org-1-prod", "sim-live") == "live"


def test_classify_idle_job(monkeypatch):
    job = SimpleNamespace(
        status=SimpleNamespace(succeeded=0, failed=0, active=0, conditions=[]),
    )

    monkeypatch.setattr("src.reconciler.k8s_available", True)
    monkeypatch.setattr("src.reconciler.k8s_batch.read_namespaced_job", lambda *_a, **_k: job)
    monkeypatch.setattr("src.reconciler._job_pod_phase_running", lambda *_a, **_k: False)
    assert _classify_k8s_workload("org-1-prod", "sim-zombie") == "idle"


def test_terminal_status_from_complete_condition():
    job = SimpleNamespace(
        status=SimpleNamespace(
            succeeded=0,
            failed=0,
            conditions=[SimpleNamespace(type="Complete", status="True")],
        ),
    )
    assert _terminal_status_from_k8s_job(job) == "SUCCEEDED"
