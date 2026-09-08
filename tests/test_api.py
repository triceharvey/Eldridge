from fastapi.testclient import TestClient

from control_plane.api import create_app
from control_plane.service import ControlPlaneService


def test_api_create_read_and_health(service: ControlPlaneService) -> None:
    with TestClient(create_app(service)) as client:
        response = client.post(
            "/workflows",
            json={
                "title": "API workflow",
                "description": "Created through the command API.",
                "idempotency_key": "api-workflow-key",
            },
        )
        assert response.status_code == 201
        workflow = response.json()
        assert workflow["state"] == "PLANNING"
        read = client.get(f"/workflows/{workflow['id']}")
        assert read.status_code == 200
        assert read.json()["id"] == workflow["id"]
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}


def test_api_denies_agent_workflow_submission(service: ControlPlaneService) -> None:
    with TestClient(create_app(service)) as client:
        response = client.post(
            "/workflows",
            headers={"X-Principal-ID": "implementer-agent"},
            json={
                "title": "Unauthorized",
                "description": "Agent should not submit workflows.",
                "idempotency_key": "unauthorized-key",
            },
        )
    assert response.status_code == 403
    assert response.json()["error"] == "AuthorizationError"


def test_api_accepts_explicit_classification_and_contains_suspicious_input(
    service: ControlPlaneService,
) -> None:
    with TestClient(create_app(service)) as client:
        response = client.post(
            "/workflows",
            json={
                "title": "Contained workflow",
                "description": "Inspect before granting execution authority.",
                "idempotency_key": "contained-api-key",
                "complexity": "ADVERSARIAL",
                "risk": "HIGH",
                "data_classification": "CONFIDENTIAL",
                "repository_scope": "owner/repository",
                "inspection_signals": ["CONCEALED_INSTRUCTIONS"],
            },
        )
        resumed_response = client.post(
            f"/workflows/{response.json()['id']}/disposition",
            json={
                "decision": "RESUME_CONTAINED",
                "rationale": "Reviewed and approved contained local planning.",
            },
        )

    assert response.status_code == 201
    workflow = response.json()
    assert workflow["state"] == "BLOCKED"
    assert workflow["risk_class"] == "HIGH"
    assert workflow["data_classification"] == "CONFIDENTIAL"
    assert workflow["repository_scope"] == "owner/repository"
    assert workflow["tasks"] == []
    assert resumed_response.status_code == 200
    assert resumed_response.json()["state"] == "PLANNING"
    assert resumed_response.json()["containment_required"] is True


def test_api_exposes_auditable_routing_and_evidence(service: ControlPlaneService) -> None:
    with TestClient(create_app(service)) as client:
        created = client.post(
            "/workflows",
            json={
                "title": "Routing visibility",
                "description": "Expose decisions without prompt bodies.",
                "idempotency_key": "routing-visibility-key",
            },
        ).json()
        task = service.lease_next_task(worker_id="orchestrator")
        assert task is not None
        service.execute_leased_task(
            task_id=task["id"],
            lease_token=task["lease_token"],
            worker_id="orchestrator",
        )

        routing = client.get("/routing-decisions", params={"workflow_id": created["id"]})
        evidence = client.get("/provider-evidence")

    assert routing.status_code == 200
    assert routing.json()[0]["selected_provider_id"] == "mock-producer"
    assert evidence.status_code == 200
    producer = next(item for item in evidence.json() if item["provider_id"] == "mock-producer")
    assert producer["evidence"]["PLANNING"]["sample_count"] == 1
