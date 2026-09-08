import secrets
from typing import Any

from fastapi.testclient import TestClient
from prometheus_client import CONTENT_TYPE_LATEST
from pydantic import SecretStr

from control_plane.api import create_app
from control_plane.service import ControlPlaneService

METRICS_TOKEN = secrets.token_urlsafe(32)


def test_operational_snapshot_and_dashboard_are_aggregate_only(
    service: ControlPlaneService,
) -> None:
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Sensitive workflow title",
        description="This content must not appear in the dashboard.",
        idempotency_key="observable-workflow-key",
    )
    task = service.lease_next_task(worker_id="orchestrator")
    assert task is not None
    service.execute_leased_task(
        task_id=task["id"], lease_token=task["lease_token"], worker_id="orchestrator"
    )

    snapshot = service.operational_snapshot(principal_id="dev-operator")
    with TestClient(create_app(service)) as client:
        summary = client.get("/operations/summary")
        dashboard = client.get("/dashboard")

    assert snapshot["workflows_by_state"]["ARCHITECTURE_REVIEW"] == 1
    assert snapshot["providers"][0]["observations"] == 1
    assert summary.status_code == 200
    assert dashboard.status_code == 200
    assert "AI Engineering Control Plane" in dashboard.text
    assert workflow["id"] not in dashboard.text
    assert "Sensitive workflow title" not in dashboard.text
    assert dashboard.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in dashboard.headers["content-security-policy"]


def test_metrics_are_disabled_by_default_and_require_a_separate_secret(
    service: ControlPlaneService,
) -> None:
    with TestClient(create_app(service)) as client:
        assert client.get("/metrics").status_code == 404

    app = create_app(
        service,
        metrics_enabled=True,
        metrics_bearer_token=SecretStr(METRICS_TOKEN),
    )
    with TestClient(app) as client:
        missing = client.get("/metrics")
        wrong = client.get("/metrics", headers={"Authorization": "Bearer wrong"})
        accepted = client.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"})
        lowercase_scheme = client.get(
            "/metrics", headers={"Authorization": f"bearer {METRICS_TOKEN}"}
        )

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert wrong.status_code == 401
    assert accepted.status_code == 200
    assert lowercase_scheme.status_code == 200
    assert accepted.headers["content-type"] == CONTENT_TYPE_LATEST
    assert "control_plane_workflows" in accepted.text
    assert "control_plane_worker_leases" in accepted.text
    assert "Sensitive workflow title" not in accepted.text


def test_dashboard_authorization_uses_control_plane_roles(
    service: ControlPlaneService,
) -> None:
    with TestClient(create_app(service)) as client:
        denied = client.get("/dashboard", headers={"X-Principal-ID": "implementer-agent"})
    assert denied.status_code == 403


def test_snapshot_has_stable_empty_shape(service: Any) -> None:
    snapshot = service.operational_snapshot(principal_id="dev-operator")
    assert snapshot["workflows_by_state"] == {}
    assert snapshot["tasks_by_status"] == {}
    assert snapshot["providers"] == []
    assert snapshot["leases"] == {"active": 0, "expired": 0}
    assert snapshot["approval_gates"] == {"waiting": 0, "oldest_wait_seconds": 0.0}
