import json

import pytest
from pydantic import ValidationError

from control_plane.activation import ProviderActivationPolicy, activate_integrations
from control_plane.config import Settings
from control_plane.domain import AuthorizationError
from control_plane.routing import DataClassification, EgressBoundary, RiskLevel
from control_plane.runtime import build_runtime
from control_plane.secrets import StaticSecretResolver


def _policy(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "policy_version": "provider-activation/test-v1",
        "allow_external_egress": False,
        "include_mock_providers": True,
        "secret_environment": {"anthropic-key": "ANTHROPIC_API_KEY"},
        "anthropic": {
            "enabled": False,
            "model": "claude-sonnet-5",
            "api_key_ref": "anthropic-key",
            "maximum_data_classification": "INTERNAL",
            "maximum_risk": "MEDIUM",
        },
    }
    payload.update(overrides)
    return payload


def test_disabled_policy_keeps_only_local_mock_providers() -> None:
    activated = activate_integrations(ProviderActivationPolicy.model_validate(_policy()))
    assert {binding.profile.provider_id for binding in activated.bindings} == {
        "mock-producer",
        "mock-reviewer",
    }
    assert activated.allowed_egress == frozenset({EgressBoundary.LOCAL})
    assert activated.devin_runtime is None


def test_anthropic_activation_requires_explicit_egress_and_no_mock_fallback() -> None:
    anthropic = dict(_policy()["anthropic"])  # type: ignore[arg-type]
    anthropic["enabled"] = True
    with pytest.raises(ValidationError, match="approved egress"):
        ProviderActivationPolicy.model_validate(_policy(anthropic=anthropic))
    with pytest.raises(ValidationError, match="silently fall back"):
        ProviderActivationPolicy.model_validate(
            _policy(allow_external_egress=True, anthropic=anthropic)
        )


def test_external_activation_is_limited_by_data_risk_and_allowlisted_secret() -> None:
    anthropic = dict(_policy()["anthropic"])  # type: ignore[arg-type]
    anthropic.update({"enabled": True, "maximum_data_classification": "CONFIDENTIAL"})
    with pytest.raises(ValidationError, match="limited to INTERNAL"):
        ProviderActivationPolicy.model_validate(
            _policy(
                allow_external_egress=True,
                include_mock_providers=False,
                anthropic=anthropic,
            )
        )
    anthropic.update({"maximum_data_classification": "INTERNAL", "api_key_ref": "missing"})
    with pytest.raises(ValidationError, match="allowlisted secret"):
        ProviderActivationPolicy.model_validate(
            _policy(
                allow_external_egress=True,
                include_mock_providers=False,
                anthropic=anthropic,
            )
        )


def test_anthropic_activation_resolves_secret_before_enabling_binding() -> None:
    anthropic = dict(_policy()["anthropic"])  # type: ignore[arg-type]
    anthropic["enabled"] = True
    policy = ProviderActivationPolicy.model_validate(
        _policy(
            allow_external_egress=True,
            include_mock_providers=False,
            anthropic=anthropic,
        )
    )
    with pytest.raises(AuthorizationError, match="unavailable"):
        activate_integrations(policy, secret_resolver=StaticSecretResolver({}))
    activated = activate_integrations(
        policy,
        secret_resolver=StaticSecretResolver({"anthropic-key": "test-value"}),
    )
    profile = activated.bindings[0].profile
    assert profile.provider_id == "anthropic-claude"
    assert profile.maximum_data_classification is DataClassification.INTERNAL
    assert profile.maximum_risk is RiskLevel.MEDIUM
    assert EgressBoundary.APPROVED_EXTERNAL in activated.allowed_egress


def test_local_model_activation_needs_no_secret_or_external_egress() -> None:
    local_model = {
        "enabled": True,
        "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
        "model": "qwen-local",
        "maximum_data_classification": "RESTRICTED",
        "maximum_risk": "MEDIUM",
    }
    policy = ProviderActivationPolicy.model_validate(
        _policy(include_mock_providers=False, local_model=local_model)
    )

    activated = activate_integrations(policy, secret_resolver=StaticSecretResolver({}))

    assert activated.allowed_egress == frozenset({EgressBoundary.LOCAL})
    assert len(activated.bindings) == 1
    profile = activated.bindings[0].profile
    assert profile.provider_id == "local-openai-compatible"
    assert profile.model_version == "qwen-local"
    assert profile.maximum_data_classification is DataClassification.RESTRICTED


def test_local_model_activation_rejects_non_loopback_endpoint() -> None:
    with pytest.raises(ValidationError, match="local model endpoint"):
        ProviderActivationPolicy.model_validate(
            _policy(
                local_model={
                    "enabled": True,
                    "endpoint": "http://192.168.1.10:11434/v1/chat/completions",
                    "model": "remote-model",
                }
            )
        )


def test_runtime_loads_disabled_policy_and_records_its_version(tmp_path: object) -> None:
    from pathlib import Path

    directory = Path(str(tmp_path))
    policy_file = directory / "provider-policy.json"
    policy_file.write_text(json.dumps(_policy()), encoding="utf-8")
    runtime = build_runtime(
        Settings(
            database_url=f"sqlite:///{directory / 'runtime.db'}",
            provider_policy_file=policy_file,
        ),
        create_schema=True,
    )
    assert runtime.service.provider_policy_version == "provider-activation/test-v1"
    assert runtime.devin_runtime is None
    runtime.engine.dispose()
