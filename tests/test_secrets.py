import secrets

import pytest

from control_plane.domain import AuthorizationError
from control_plane.secrets import EnvironmentSecretResolver


def test_environment_resolver_requires_explicit_reference_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    variable = "CONTROL_PLANE_TEST_PROVIDER_TOKEN"
    value = secrets.token_urlsafe(24)
    monkeypatch.setenv(variable, value)
    resolver = EnvironmentSecretResolver({"provider-key": variable})
    assert resolver.resolve("provider-key") == value
    with pytest.raises(AuthorizationError, match="not allowlisted"):
        resolver.resolve(variable)


def test_environment_resolver_rejects_missing_value() -> None:
    resolver = EnvironmentSecretResolver({"provider-key": "MISSING_PROVIDER_TOKEN"})
    with pytest.raises(AuthorizationError, match="unavailable"):
        resolver.resolve("provider-key")
