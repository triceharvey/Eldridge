import json
import secrets

import httpx
import pytest

from control_plane.domain import IntegrationDisabledError, ValidationError
from control_plane.integrations import (
    DevinRuntime,
    DevinRuntimeConfig,
    RemoteAgentRequest,
    RemoteRunState,
)
from control_plane.secrets import StaticSecretResolver


def _request() -> RemoteAgentRequest:
    return RemoteAgentRequest(
        run_id="run-1",
        workflow_id="workflow-1",
        task_id="task-1",
        objective="Implement the scoped task.",
        repositories=("owner/repository",),
        max_cost_units=2,
        idempotency_key="attempt-1",
        structured_output_schema={
            "type": "object",
            "required": ["summary"],
            "properties": {"summary": {"type": "string"}},
        },
    )


def test_devin_is_disabled_by_default() -> None:
    secret_ref = secrets.token_urlsafe(12)
    runtime = DevinRuntime(
        DevinRuntimeConfig(organization_id="org", service_token_ref=secret_ref),
        StaticSecretResolver({}),
    )
    with pytest.raises(IntegrationDisabledError, match="disabled"):
        runtime.submit(_request())


def test_devin_session_lifecycle_uses_v3_and_never_bypasses_approval() -> None:
    observed_payload: dict[str, object] = {}
    secret_ref = secrets.token_urlsafe(12)
    secret_value = secrets.token_urlsafe(24)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {secret_value}"
        if request.method == "POST":
            observed_payload.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={"session_id": "devin-123", "url": "https://app.devin.ai/s/devin-123"},
            )
        if request.method == "GET":
            return httpx.Response(200, json={"session_id": "devin-123", "status": "running"})
        return httpx.Response(204)

    runtime = DevinRuntime(
        DevinRuntimeConfig(
            enabled=True,
            organization_id="org-1",
            service_token_ref=secret_ref,
        ),
        StaticSecretResolver({secret_ref: secret_value}),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    handle = runtime.submit(_request())
    status = runtime.poll(handle)
    assert handle.remote_id == "devin-123"
    assert status.state == RemoteRunState.RUNNING
    assert runtime.cancel(handle)
    assert observed_payload["repos"] == ["owner/repository"]
    assert observed_payload["max_acu_limit"] == 2
    assert observed_payload["structured_output_schema"] == {
        "type": "object",
        "required": ["summary"],
        "properties": {"summary": {"type": "string"}},
    }
    assert "bypass_approval" not in observed_payload
    assert "session_secrets" not in observed_payload
    assert "secret_ids" not in observed_payload


def test_devin_rejects_request_above_its_cost_limit_before_network() -> None:
    secret_ref = secrets.token_urlsafe(12)
    runtime = DevinRuntime(
        DevinRuntimeConfig(
            enabled=True,
            organization_id="org-1",
            service_token_ref=secret_ref,
            maximum_cost_units=1,
        ),
        StaticSecretResolver({secret_ref: secrets.token_urlsafe(24)}),
    )
    with pytest.raises(ValidationError, match="cost-unit limit"):
        runtime.submit(_request())
