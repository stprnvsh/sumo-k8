"""Per-simulation env + org-audit log group routing.

The shared cluster serves both envs; the env is encoded in the tenant_id
(``org-{id}-{env}``), so the org-audit log GROUP must be derived from it — not
from the controller-wide DEPLOY_ENV/ENV (which previously routed ALL sims,
including prod, to the ``-dev`` group).
"""
from src.org_audit import (
    env_from_tenant_id,
    org_audit_log_group_for_tenant,
    org_id_from_tenant_id,
)


def test_env_from_tenant_id_prod_and_dev():
    assert env_from_tenant_id("org-8-prod") == "prod"
    assert env_from_tenant_id("org-8-dev") == "dev"
    assert env_from_tenant_id("org-80-prod") == "prod"  # not confused by org-8
    assert env_from_tenant_id("Org-8-Prod") == "prod"  # case-insensitive


def test_env_from_tenant_id_falls_back_to_controller_env(monkeypatch):
    # Bare org-{id} (no suffix) -> controller env (default dev here).
    monkeypatch.delenv("DEPLOY_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    assert env_from_tenant_id("org-8") == "dev"
    monkeypatch.setenv("DEPLOY_ENV", "prod")
    assert env_from_tenant_id("org-8") == "prod"


def test_log_group_is_per_sim_env():
    # A prod tenant logs to the PROD group even if the controller env is dev.
    assert org_audit_log_group_for_tenant("org-8-prod") == "/transcality/org-8-prod/audit"
    assert org_audit_log_group_for_tenant("org-8-dev") == "/transcality/org-8-dev/audit"


def test_log_group_none_for_bad_tenant():
    assert org_audit_log_group_for_tenant(None) is None
    assert org_audit_log_group_for_tenant("garbage") is None


def test_org_id_parsing_unchanged():
    assert org_id_from_tenant_id("org-8-prod") == 8
    assert org_id_from_tenant_id("org-80-dev") == 80
