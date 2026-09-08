from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import SecretStr

from control_plane.api import create_app
from control_plane.config import Settings
from control_plane.domain import AuthenticationError
from control_plane.identity import OidcAuthenticator, OidcPolicy
from control_plane.runtime import build_runtime


class StaticJwksClient:
    def __init__(self, key: Any) -> None:
        self.key = key

    def get_signing_key_from_jwt(self, _token: str) -> Any:
        return SimpleNamespace(key=self.key)


@pytest.fixture
def oidc() -> tuple[OidcAuthenticator, Any]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    policy = OidcPolicy(
        policy_version="oidc/test-v1",
        enabled=True,
        issuer="https://identity.example.com",
        audience="control-plane",
        jwks_url="https://identity.example.com/.well-known/jwks.json",
        subject_principals={"operator-subject": "dev-operator"},
    )
    return OidcAuthenticator(
        policy, jwks_client=StaticJwksClient(private_key.public_key())
    ), private_key


def _token(private_key: Any, **overrides: Any) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": "https://identity.example.com",
        "aud": "control-plane",
        "sub": "operator-subject",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256")


def test_oidc_authenticator_validates_and_maps_subject(oidc: tuple[OidcAuthenticator, Any]) -> None:
    authenticator, private_key = oidc
    assert authenticator.authenticate(f"Bearer {_token(private_key)}") == "dev-operator"


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://attacker.example.com"},
        {"aud": "another-service"},
        {"aud": ["control-plane", "another-service"]},
        {"sub": "unknown-subject"},
        {"exp": datetime.now(UTC) - timedelta(minutes=10)},
        {"exp": datetime.now(UTC) + timedelta(hours=2)},
    ],
)
def test_oidc_authenticator_rejects_invalid_claims(
    oidc: tuple[OidcAuthenticator, Any], overrides: dict[str, Any]
) -> None:
    authenticator, private_key = oidc
    with pytest.raises(AuthenticationError):
        authenticator.authenticate(f"Bearer {_token(private_key, **overrides)}")


def test_api_ignores_development_header_when_oidc_is_active(
    service: Any, oidc: tuple[OidcAuthenticator, Any]
) -> None:
    authenticator, private_key = oidc
    with TestClient(create_app(service, oidc_authenticator=authenticator)) as client:
        denied = client.get("/provider-evidence", headers={"X-Principal-ID": "dev-operator"})
        accepted = client.get(
            "/provider-evidence",
            headers={
                "Authorization": f"Bearer {_token(private_key)}",
                "X-Principal-ID": "unknown",
            },
        )
    assert denied.status_code == 401
    assert accepted.status_code == 200


def test_production_runtime_fails_closed_without_oidc(tmp_path: Any) -> None:
    settings = Settings(
        environment="production",
        database_url="sqlite://",
        worktree_root=tmp_path / "worktrees",
    )
    with pytest.raises(ValueError, match="OIDC authentication is required"):
        build_runtime(settings, create_schema=True)


def test_metrics_activation_requires_a_strong_separate_credential(tmp_path: Any) -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite://",
        worktree_root=tmp_path / "worktrees",
        metrics_enabled=True,
    )
    with pytest.raises(ValueError, match="metrics bearer authentication"):
        build_runtime(settings, create_schema=True)

    weak = settings.model_copy(update={"metrics_bearer_token": SecretStr("too-short")})
    with pytest.raises(ValueError, match="at least 32"):
        build_runtime(weak, create_schema=True)


def test_oidc_policy_rejects_algorithm_confusion() -> None:
    with pytest.raises(ValueError, match="RS256"):
        OidcPolicy(
            policy_version="oidc/test-v1",
            enabled=True,
            issuer="https://identity.example.com",
            audience="control-plane",
            jwks_url="https://identity.example.com/jwks",
            algorithms=("HS256",),
            subject_principals={"operator-subject": "dev-operator"},
        )
