from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from control_plane.integrations import DevinRuntime, DevinRuntimeConfig
from control_plane.providers import AnthropicProvider, AnthropicProviderConfig, MockProvider
from control_plane.routing import (
    DataClassification,
    EgressBoundary,
    RiskLevel,
    interoperability_profiles,
    mock_profiles,
)
from control_plane.secrets import EnvironmentSecretResolver, SecretResolver
from control_plane.service import ProviderBinding

ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")


def _parse_named_enum(
    value: object, enum_type: type[DataClassification] | type[RiskLevel]
) -> object:
    if not isinstance(value, str):
        return value
    try:
        return enum_type[value]
    except KeyError as exc:
        raise ValueError(f"unknown {enum_type.__name__} name") from exc


class AnthropicActivation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    model: str = Field(min_length=1, max_length=128)
    api_key_ref: str = Field(min_length=1, max_length=128)
    max_tokens: int = Field(default=4096, ge=256, le=32_000)
    timeout_seconds: float = Field(default=60, gt=0, le=600)
    maximum_data_classification: DataClassification = DataClassification.INTERNAL
    maximum_risk: RiskLevel = RiskLevel.MEDIUM

    @field_validator("maximum_data_classification", "maximum_risk", mode="before")
    @classmethod
    def parse_named_limits(cls, value: object, info: ValidationInfo) -> object:
        enum_type = (
            DataClassification if info.field_name == "maximum_data_classification" else RiskLevel
        )
        return _parse_named_enum(value, enum_type)


class DevinActivation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    organization_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    service_token_ref: str = Field(min_length=1, max_length=128)
    timeout_seconds: float = Field(default=60, gt=0, le=600)
    maximum_cost_units: int = Field(default=2, ge=1, le=100)
    maximum_data_classification: DataClassification = DataClassification.INTERNAL
    maximum_risk: RiskLevel = RiskLevel.MEDIUM

    @field_validator("maximum_data_classification", "maximum_risk", mode="before")
    @classmethod
    def parse_named_limits(cls, value: object, info: ValidationInfo) -> object:
        enum_type = (
            DataClassification if info.field_name == "maximum_data_classification" else RiskLevel
        )
        return _parse_named_enum(value, enum_type)


class ProviderActivationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(min_length=1, max_length=64)
    allow_external_egress: bool = False
    include_mock_providers: bool = True
    secret_environment: dict[str, str] = Field(default_factory=dict)
    anthropic: AnthropicActivation | None = None
    devin: DevinActivation | None = None

    @model_validator(mode="after")
    def enforce_activation_invariants(self) -> ProviderActivationPolicy:
        enabled = any(
            activation is not None and activation.enabled
            for activation in (self.anthropic, self.devin)
        )
        if enabled and not self.allow_external_egress:
            raise ValueError("external provider activation requires approved egress")
        if self.anthropic is not None and self.anthropic.enabled and self.include_mock_providers:
            raise ValueError("live-provider routing cannot silently fall back to mock providers")
        for reference, variable in self.secret_environment.items():
            if not reference or not ENVIRONMENT_NAME.fullmatch(variable):
                raise ValueError("secret environment mapping is invalid")
        if self.anthropic is not None and self.anthropic.enabled:
            if self.anthropic.api_key_ref not in self.secret_environment:
                raise ValueError("enabled Anthropic provider lacks an allowlisted secret reference")
            if self.anthropic.maximum_data_classification > DataClassification.INTERNAL:
                raise ValueError("Phase 2 external providers are limited to INTERNAL data")
        if self.devin is not None and self.devin.enabled:
            if self.devin.service_token_ref not in self.secret_environment:
                raise ValueError("enabled Devin runtime lacks an allowlisted secret reference")
            if self.devin.maximum_data_classification > DataClassification.INTERNAL:
                raise ValueError("Phase 2 external providers are limited to INTERNAL data")
        return self

    @classmethod
    def from_file(cls, path: Path) -> ProviderActivationPolicy:
        resolved = path.resolve()
        if not resolved.is_file() or resolved.stat().st_size > 65_536:
            raise ValueError("provider policy file is missing or too large")
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("provider policy file is invalid") from exc
        return cls.model_validate(payload)


@dataclass(frozen=True)
class ActivatedIntegrations:
    bindings: tuple[ProviderBinding, ...]
    allowed_egress: frozenset[EgressBoundary]
    devin_runtime: DevinRuntime | None
    policy_version: str


def activate_integrations(
    policy: ProviderActivationPolicy,
    *,
    secret_resolver: SecretResolver | None = None,
) -> ActivatedIntegrations:
    resolver = secret_resolver or EnvironmentSecretResolver(policy.secret_environment)
    bindings: list[ProviderBinding] = []
    if policy.include_mock_providers:
        mock = MockProvider()
        bindings.extend(
            ProviderBinding(profile=profile, provider=mock) for profile in mock_profiles()
        )

    profiles = {profile.provider_id: profile for profile in interoperability_profiles()}
    anthropic = policy.anthropic
    if anthropic is not None and anthropic.enabled:
        resolver.resolve(anthropic.api_key_ref)
        provider = AnthropicProvider(
            AnthropicProviderConfig(
                enabled=True,
                model=anthropic.model,
                api_key_ref=anthropic.api_key_ref,
                max_tokens=anthropic.max_tokens,
                timeout_seconds=anthropic.timeout_seconds,
            ),
            resolver,
        )
        profile = replace(
            profiles["anthropic-claude"],
            enabled=True,
            healthy=True,
            model_version=anthropic.model,
            profile_version=policy.policy_version,
            maximum_data_classification=anthropic.maximum_data_classification,
            maximum_risk=anthropic.maximum_risk,
        )
        bindings.append(ProviderBinding(profile=profile, provider=provider))

    devin_runtime: DevinRuntime | None = None
    devin = policy.devin
    if devin is not None and devin.enabled:
        resolver.resolve(devin.service_token_ref)
        devin_runtime = DevinRuntime(
            DevinRuntimeConfig(
                enabled=True,
                organization_id=devin.organization_id,
                service_token_ref=devin.service_token_ref,
                timeout_seconds=devin.timeout_seconds,
                maximum_cost_units=devin.maximum_cost_units,
                maximum_data_classification=devin.maximum_data_classification,
                maximum_risk=devin.maximum_risk,
            ),
            resolver,
        )

    if not bindings:
        raise ValueError("provider policy enables no model provider")
    allowed = {EgressBoundary.LOCAL}
    if policy.allow_external_egress:
        allowed.add(EgressBoundary.APPROVED_EXTERNAL)
    return ActivatedIntegrations(
        bindings=tuple(bindings),
        allowed_egress=frozenset(allowed),
        devin_runtime=devin_runtime,
        policy_version=policy.policy_version,
    )
