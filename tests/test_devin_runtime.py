import json
import secrets
from dataclasses import replace

import httpx
import pytest

from control_plane.domain import IntegrationDisabledError, IntegrationResponseError, ValidationError
from control_plane.integrations import (
    DevinRuntime,
    DevinRuntimeConfig,
    RemoteAgentRequest,
    RemoteRunState,
)
from control_plane.integrations.base import RemoteAgentHandle
from control_plane.routing import DataClassification, RiskLevel
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


def _runtime(handler: httpx.MockTransport) -> DevinRuntime:
    secret_ref = secrets.token_urlsafe(12)
    return DevinRuntime(
        DevinRuntimeConfig(
            enabled=True,
            organization_id="org-1",
            service_token_ref=secret_ref,
        ),
        StaticSecretResolver({secret_ref: "synthetic-token"}),
        client=httpx.Client(transport=handler),
    )


@pytest.mark.parametrize(
    ("remote_status", "expected"),
    [
        ("new", RemoteRunState.QUEUED),
        ("queued", RemoteRunState.QUEUED),
        ("claimed", RemoteRunState.RUNNING),
        ("running", RemoteRunState.RUNNING),
        ("resuming", RemoteRunState.RUNNING),
        ("suspended", RemoteRunState.SUSPENDED),
        ("exit", RemoteRunState.SUCCEEDED),
        ("error", RemoteRunState.FAILED),
        ("terminated", RemoteRunState.CANCELLED),
        ("future-state", RemoteRunState.UNKNOWN),
    ],
)
def test_devin_local_lifecycle_matrix(remote_status: str, expected: RemoteRunState) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.url.host == "api.devin.ai"
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload["max_acu_limit"] == 2
            assert payload["repos"] == ["owner/repository"]
            assert "bypass_approval" not in payload
            return httpx.Response(200, json={"session_id": "devin-123"})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "session_id": "devin-123",
                    "status": remote_status,
                    "token": "do-not-return",
                    "messages": [{"text": "secret", "credentials": {"key": "do-not-return"}}],
                    "pull_requests": ["unvalidated-revision"],
                },
            )
        return httpx.Response(204)

    runtime = _runtime(httpx.MockTransport(handler))
    handle = runtime.submit(_request())
    status = runtime.poll(handle)
    assert status.state == expected
    assert status.output == {"session_id": "devin-123", "status": remote_status}
    assert runtime.cancel(handle)
    assert calls == [
        ("POST", "/v3/organizations/org-1/sessions"),
        ("GET", "/v3/organizations/org-1/sessions/devin-123"),
        ("DELETE", "/v3/organizations/org-1/sessions/devin-123"),
    ]


@pytest.mark.parametrize("bad_id", ["", "other-123", "devin-../other", "devin-a?x=1"])
def test_devin_submit_rejects_untrusted_session_id(bad_id: str) -> None:
    runtime = _runtime(
        httpx.MockTransport(lambda _: httpx.Response(200, json={"session_id": bad_id}))
    )
    with pytest.raises(IntegrationResponseError, match="session ID"):
        runtime.submit(_request())


def test_devin_poll_rejects_mismatched_session_before_accepting_status() -> None:
    runtime = _runtime(
        httpx.MockTransport(
            lambda _: httpx.Response(200, json={"session_id": "devin-else", "status": "exit"})
        )
    )
    with pytest.raises(IntegrationResponseError, match="session ID"):
        runtime.poll(RemoteAgentHandle(provider="devin", remote_id="devin-123", url=None))


@pytest.mark.parametrize("bad_status", [None, 1, True, [], {}])
def test_devin_poll_rejects_non_string_status(bad_status: object) -> None:
    runtime = _runtime(
        httpx.MockTransport(
            lambda _: httpx.Response(200, json={"session_id": "devin-123", "status": bad_status})
        )
    )
    with pytest.raises(IntegrationResponseError, match="status"):
        runtime.poll(RemoteAgentHandle(provider="devin", remote_id="devin-123", url=None))


@pytest.mark.parametrize("bad_detail", [1, {}, "", "x" * 65])
def test_devin_poll_rejects_invalid_status_detail(bad_detail: object) -> None:
    runtime = _runtime(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "session_id": "devin-123",
                    "status": "running",
                    "status_detail": bad_detail,
                },
            )
        )
    )
    with pytest.raises(IntegrationResponseError, match="status detail"):
        runtime.poll(RemoteAgentHandle(provider="devin", remote_id="devin-123", url=None))


def test_devin_poll_rejects_oversized_structured_output() -> None:
    runtime = _runtime(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "session_id": "devin-123",
                    "status": "running",
                    "status_detail": "finished",
                    "structured_output": {"summary": "x" * 16_385},
                },
            )
        )
    )
    with pytest.raises(IntegrationResponseError, match="too large"):
        runtime.poll(RemoteAgentHandle(provider="devin", remote_id="devin-123", url=None))


def test_devin_rejects_oversized_response_before_json_parsing() -> None:
    runtime = _runtime(httpx.MockTransport(lambda _: httpx.Response(200, text="x" * 1_048_577)))
    with pytest.raises(IntegrationResponseError, match="oversized"):
        runtime.submit(_request())


@pytest.mark.parametrize(
    ("agent_request", "error"),
    [
        (replace(_request(), repositories=()), "repository scope"),
        (replace(_request(), max_cost_units=0), "positive cost-unit"),
        (
            replace(_request(), data_classification=DataClassification.CONFIDENTIAL),
            "classification",
        ),
        (replace(_request(), risk=RiskLevel.HIGH), "risk limit"),
    ],
)
def test_devin_rejects_out_of_policy_requests_without_network(
    agent_request: RemoteAgentRequest, error: str
) -> None:
    def unexpected_network(_: httpx.Request) -> httpx.Response:
        pytest.fail("out-of-policy request reached network")

    with pytest.raises(ValidationError, match=error):
        _runtime(httpx.MockTransport(unexpected_network)).submit(agent_request)


@pytest.mark.parametrize("method", ["GET", "DELETE"])
@pytest.mark.parametrize("remote_id", ["wrong-123", "devin-../other", "devin-a?x=1"])
def test_devin_rejects_invalid_handle_before_network(method: str, remote_id: str) -> None:
    def unexpected_network(_: httpx.Request) -> httpx.Response:
        pytest.fail("invalid handle reached network")

    runtime = _runtime(httpx.MockTransport(unexpected_network))
    handle = RemoteAgentHandle(provider="devin", remote_id=remote_id, url=None)
    with pytest.raises(IntegrationResponseError, match="handle"):
        if method == "GET":
            runtime.poll(handle)
        else:
            runtime.cancel(handle)


@pytest.mark.parametrize(
    "response", [httpx.Response(200, text="not-json"), httpx.Response(200, json=[])]
)
def test_devin_rejects_malformed_json(response: httpx.Response) -> None:
    runtime = _runtime(httpx.MockTransport(lambda _: response))
    with pytest.raises(IntegrationResponseError, match="invalid JSON|invalid response object"):
        runtime.submit(_request())


@pytest.mark.parametrize("status_code", [401, 403, 429, 500])
def test_devin_reports_http_failure_without_response_body(status_code: int) -> None:
    runtime = _runtime(
        httpx.MockTransport(
            lambda _: httpx.Response(status_code, text="synthetic private provider response")
        )
    )
    with pytest.raises(IntegrationResponseError, match=f"HTTP {status_code}") as error:
        runtime.submit(_request())
    assert "synthetic private provider response" not in str(error.value)


def test_devin_restricts_configured_endpoint() -> None:
    with pytest.raises(ValueError, match="official Devin v3 API endpoint"):
        DevinRuntimeConfig(
            enabled=True,
            organization_id="org-1",
            service_token_ref=secrets.token_urlsafe(12),
            api_base="https://example.invalid/v3",
        )
