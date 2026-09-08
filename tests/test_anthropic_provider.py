import json
import secrets

import httpx
import pytest

from control_plane.domain import (
    AgentRole,
    Capability,
    IntegrationDisabledError,
    ProviderRequest,
    TaskKind,
)
from control_plane.providers import AnthropicProvider, AnthropicProviderConfig
from control_plane.secrets import StaticSecretResolver


def _request() -> ProviderRequest:
    return ProviderRequest(
        run_id="run-1",
        workflow_id="workflow-1",
        task_id="task-1",
        task_kind=TaskKind.PLAN,
        role=AgentRole.ARCHITECT,
        objective="Create a constrained plan.",
        context={"repository": "untrusted"},
        required_capability=Capability.PRODUCE_PLAN,
        idempotency_key="attempt-1",
    )


def test_anthropic_is_disabled_by_default() -> None:
    provider = AnthropicProvider(
        AnthropicProviderConfig(model="configured-model", api_key_ref="anthropic-key"),
        StaticSecretResolver({}),
    )
    with pytest.raises(IntegrationDisabledError, match="disabled"):
        provider.submit(_request())


def test_anthropic_normalizes_structured_response_without_exposing_secret() -> None:
    secret = secrets.token_urlsafe(24)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == secret
        payload = json.loads(request.content)
        assert payload["model"] == "configured-model"
        assert secret not in request.content.decode()
        return httpx.Response(
            200,
            json={
                "model": "configured-model",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"plan": ["step"], "assumptions": []}),
                    }
                ],
                "usage": {"input_tokens": 12, "output_tokens": 8},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = AnthropicProvider(
        AnthropicProviderConfig(
            enabled=True,
            model="configured-model",
            api_key_ref="anthropic-key",
        ),
        StaticSecretResolver({"anthropic-key": secret}),
        client=client,
    )
    result = provider.submit(_request())
    assert result.provider == "anthropic"
    assert result.output == {"plan": ["step"], "assumptions": []}
    assert result.usage == {"input_tokens": 12, "output_tokens": 8}
    assert secret not in repr(result)


def test_anthropic_tool_use_is_only_a_proposal() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "configured-model",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "request_control_plane_tool",
                        "input": {"name": "LIST_FILES"},
                    }
                ],
                "usage": {},
            },
        )

    provider = AnthropicProvider(
        AnthropicProviderConfig(
            enabled=True,
            model="configured-model",
            api_key_ref="anthropic-key",
        ),
        StaticSecretResolver({"anthropic-key": "test-secret"}),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = provider.submit(_request())
    assert result.output["tool_requests"][0]["name"] == "LIST_FILES"
    assert "commands_executed" not in result.output
