import hashlib
import hmac
import json
import secrets

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from control_plane.api import create_app
from control_plane.persistence import Workflow
from control_plane.service import ControlPlaneService

SECRET = secrets.token_urlsafe(32)
BAD_SECRET = secrets.token_urlsafe(32)
REVISION = "a" * 40
DELIVERY = "12345678-1234-1234-1234-123456789abc"


def _body(
    *,
    revision: str = REVISION,
    action: str = "completed",
    conclusion: str = "success",
    check_name: str = "test-and-security",
) -> bytes:
    return json.dumps(
        {
            "action": action,
            "check_run": {
                "id": 9001,
                "name": check_name,
                "head_sha": revision,
                "status": "completed" if action == "completed" else "in_progress",
                "conclusion": conclusion if action == "completed" else None,
                "details_url": "https://github.com/owner/repository/actions/runs/9001",
                "app": {"slug": "github-actions"},
            },
            "repository": {"full_name": "owner/repository"},
        },
        separators=(",", ":"),
    ).encode()


def _headers(body: bytes, *, delivery: str = DELIVERY, secret: str = SECRET) -> dict[str, str]:
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-GitHub-Event": "check_run",
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": signature,
    }


def _candidate_workflow(service: ControlPlaneService) -> dict[str, object]:
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="GitHub CI evidence",
        description="Bind CI evidence to an exact candidate revision.",
        idempotency_key="github-ci-workflow",
        repository_scope="owner/repository",
    )
    with service.session_factory() as session, session.begin():
        stored = session.get(Workflow, workflow["id"])
        assert stored is not None
        stored.candidate_revision = REVISION
    return workflow


def _app(service: ControlPlaneService) -> FastAPI:
    return create_app(
        service,
        github_webhook_enabled=True,
        github_webhook_secret=SecretStr(SECRET),
    )


def test_github_webhook_is_disabled_by_default(service: ControlPlaneService) -> None:
    body = _body()
    with TestClient(create_app(service)) as client:
        response = client.post("/integrations/github/webhook", content=body, headers=_headers(body))
    assert response.status_code == 404


def test_github_webhook_rejects_bad_signature_before_ingestion(
    service: ControlPlaneService,
) -> None:
    _candidate_workflow(service)
    body = _body()
    with TestClient(_app(service)) as client:
        response = client.post(
            "/integrations/github/webhook",
            content=body,
            headers=_headers(body, secret=BAD_SECRET),
        )
    assert response.status_code == 403
    assert response.json()["error"] == "AuthorizationError"


def test_completed_check_is_revision_bound_audited_and_idempotent(
    service: ControlPlaneService,
) -> None:
    workflow = _candidate_workflow(service)
    body = _body(conclusion="failure")
    headers = _headers(body)
    with TestClient(_app(service)) as client:
        first = client.post("/integrations/github/webhook", content=body, headers=headers)
        replay = client.post("/integrations/github/webhook", content=body, headers=headers)
        listed = client.get("/ci-checks", params={"workflow_id": workflow["id"]})
        events = client.get("/events", params={"workflow_id": workflow["id"]})
        unchanged = client.get(f"/workflows/{workflow['id']}")

    assert first.status_code == 200
    assert first.json()["conclusion"] == "failure"
    assert first.json()["revision"] == REVISION
    assert first.json()["replayed"] is False
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["replayed"] is True
    assert len(listed.json()) == 1
    ci_events = [event for event in events.json() if event["event_type"] == "ci.check_recorded"]
    assert len(ci_events) == 1
    assert ci_events[0]["payload"]["gate_eligible"] is False
    assert unchanged.json()["state"] == workflow["state"]


def test_oversized_webhook_is_rejected_before_parsing(service: ControlPlaneService) -> None:
    body = b"x" * 1_048_577
    with TestClient(_app(service)) as client:
        response = client.post("/integrations/github/webhook", content=body, headers=_headers(body))
    assert response.status_code == 413


def test_delivery_reuse_and_revision_mismatch_fail_closed(
    service: ControlPlaneService,
) -> None:
    _candidate_workflow(service)
    original = _body()
    altered = _body(check_name="altered-check")
    mismatch = _body(revision="b" * 40)
    with TestClient(_app(service)) as client:
        assert (
            client.post(
                "/integrations/github/webhook", content=original, headers=_headers(original)
            ).status_code
            == 200
        )
        reused = client.post(
            "/integrations/github/webhook", content=altered, headers=_headers(altered)
        )
        unmatched = client.post(
            "/integrations/github/webhook",
            content=mismatch,
            headers=_headers(mismatch, delivery="abcdefab-1234-1234-1234-123456789abc"),
        )

    assert reused.status_code == 409
    assert unmatched.status_code == 404


def test_nonterminal_check_is_authenticated_but_not_recorded(
    service: ControlPlaneService,
) -> None:
    workflow = _candidate_workflow(service)
    body = _body(action="created")
    with TestClient(_app(service)) as client:
        response = client.post("/integrations/github/webhook", content=body, headers=_headers(body))
        listed = client.get("/ci-checks", params={"workflow_id": workflow["id"]})

    assert response.json() == {"accepted": False, "reason": "check_run_not_completed"}
    assert listed.json() == []
