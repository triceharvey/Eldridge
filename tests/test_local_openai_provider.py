import json

import httpx
import pytest
from pydantic import ValidationError

from control_plane.domain import (
    AgentRole,
    Capability,
    IntegrationDisabledError,
    IntegrationResponseError,
    ProviderRequest,
    TaskKind,
)
from control_plane.providers import LocalOpenAIProvider, LocalOpenAIProviderConfig


def _request() -> ProviderRequest:
    return ProviderRequest(
        run_id="run-local-1",
        workflow_id="workflow-local-1",
        task_id="task-local-1",
        task_kind=TaskKind.PLAN,
        role=AgentRole.ARCHITECT,
        objective="Create a constrained plan.",
        context={"repository": "untrusted"},
        required_capability=Capability.PRODUCE_PLAN,
        idempotency_key="attempt-local-1",
    )


@pytest.mark.parametrize(
    "endpoint",
    (
        "https://127.0.0.1:11434/v1/chat/completions",
        "http://example.com:11434/v1/chat/completions",
        "http://localhost:11434/v1/chat/completions",
        "http://127.0.0.1/v1/chat/completions",
        "http://user:password@127.0.0.1:11434/v1/chat/completions",
        "http://127.0.0.1:11434/other",
        "http://127.0.0.1:11434/v1/chat/completions?redirect=1",
    ),
)
def test_local_provider_rejects_non_loopback_or_ambiguous_endpoints(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="local model endpoint"):
        LocalOpenAIProviderConfig(model="local-model", endpoint=endpoint)


@pytest.mark.parametrize("model", (" model", "model name", "model\nname", ""))
def test_local_provider_rejects_ambiguous_model_identifiers(model: str) -> None:
    with pytest.raises(ValidationError):
        LocalOpenAIProviderConfig(model=model)


def test_local_provider_is_disabled_by_default() -> None:
    provider = LocalOpenAIProvider(LocalOpenAIProviderConfig(model="local-model"))

    with pytest.raises(IntegrationDisabledError, match="disabled"):
        provider.submit(_request())
    assert provider.health() is False


def test_local_provider_normalizes_structured_response_without_credentials() -> None:
    requests: list[httpx.Request] = []
    model = "local-model@sha256:test"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": model}]})
        payload = json.loads(request.content)
        assert request.url.host == "127.0.0.1"
        assert request.headers.get("authorization") is None
        assert payload["temperature"] == 0
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["messages"][0]["role"] == "system"
        return httpx.Response(
            200,
            json={
                "model": model,
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"plan": ["step"], "assumptions": []}),
                        }
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            },
        )

    provider = LocalOpenAIProvider(
        LocalOpenAIProviderConfig(enabled=True, model=model),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert provider.health() is True
    result = provider.submit(_request())

    assert result.provider == "local-openai-compatible"
    assert result.model == "local-model@sha256:test"
    assert result.output == {"plan": ["step"], "assumptions": []}
    assert result.usage == {"input_tokens": 12, "output_tokens": 8}
    assert [request.url.path for request in requests] == ["/v1/models", "/v1/chat/completions"]


def test_local_provider_health_requires_the_exact_configured_model() -> None:
    provider = LocalOpenAIProvider(
        LocalOpenAIProviderConfig(enabled=True, model="required-model"),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"data": [{"id": "other-model"}]})
            )
        ),
    )

    assert provider.health() is False


@pytest.mark.parametrize(
    "response",
    (
        httpx.Response(302, headers={"location": "http://example.com"}),
        httpx.Response(200, json={"model": "local-model", "choices": []}),
        httpx.Response(
            200,
            json={"model": "unexpected-model", "choices": [{"message": {"content": "{}"}}]},
        ),
        httpx.Response(
            200,
            json={
                "model": "local-model",
                "choices": [{"message": {"content": "not-json"}}],
            },
        ),
        httpx.Response(
            200,
            json={
                "model": "local-model",
                "choices": [{"message": {"content": "[]"}}],
                "usage": {"prompt_tokens": 1},
            },
        ),
        httpx.Response(
            200,
            json={
                "model": "local-model",
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": -1},
            },
        ),
    ),
)
def test_local_provider_fails_closed_on_redirect_or_invalid_response(
    response: httpx.Response,
) -> None:
    provider = LocalOpenAIProvider(
        LocalOpenAIProviderConfig(enabled=True, model="local-model"),
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: response)),
    )

    with pytest.raises(IntegrationResponseError):
        provider.submit(_request())
