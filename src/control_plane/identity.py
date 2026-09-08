from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import jwt
from pydantic import BaseModel, ConfigDict, Field, model_validator

from control_plane.domain import AuthenticationError


class SigningKey(Protocol):
    key: Any


class JwksClient(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> SigningKey: ...


class OidcPolicy(BaseModel):
    """Operator-owned trust anchors and explicit subject-to-principal bindings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(min_length=1, max_length=64)
    enabled: bool = False
    issuer: str = Field(min_length=1, max_length=500)
    audience: str = Field(min_length=1, max_length=500)
    jwks_url: str = Field(min_length=1, max_length=1000)
    algorithms: tuple[str, ...] = ("RS256",)
    clock_skew_seconds: int = Field(default=60, ge=0, le=300)
    max_token_lifetime_seconds: int = Field(default=900, ge=60, le=3600)
    subject_principals: dict[str, str] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_trust_anchors(self) -> OidcPolicy:
        for name, value in (("issuer", self.issuer), ("JWKS URL", self.jwks_url)):
            parsed = urlparse(value)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError(f"OIDC {name} must be an HTTPS URL without user information")
            if parsed.fragment or (name == "issuer" and parsed.query):
                raise ValueError(f"OIDC {name} contains unsupported URL components")
        if self.algorithms != ("RS256",):
            raise ValueError("OIDC policy currently permits only the fixed RS256 algorithm")
        if any(
            not subject
            or len(subject) > 255
            or not subject.isascii()
            or not principal
            or len(principal) > 64
            for subject, principal in self.subject_principals.items()
        ):
            raise ValueError("OIDC subject mapping is invalid")
        return self

    @classmethod
    def from_file(cls, path: Path) -> OidcPolicy:
        resolved = path.resolve()
        if not resolved.is_file() or resolved.stat().st_size > 65_536:
            raise ValueError("OIDC policy file is missing or too large")
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("OIDC policy file is invalid") from exc
        return cls.model_validate(payload)


class OidcAuthenticator:
    def __init__(self, policy: OidcPolicy, *, jwks_client: JwksClient | None = None) -> None:
        self.policy = policy
        self.jwks_client: JwksClient = jwks_client or jwt.PyJWKClient(
            policy.jwks_url,
            timeout=10,
            cache_keys=True,
            max_cached_keys=16,
        )

    def authenticate(self, authorization: str | None) -> str:
        if authorization is None or len(authorization) > 16_384:
            raise AuthenticationError("a valid OIDC bearer token is required")
        parts = authorization.split(" ")
        if len(parts) != 2 or parts[0] != "Bearer" or not parts[1] or "," in authorization:
            raise AuthenticationError("a valid OIDC bearer token is required")
        token = parts[1]
        try:
            key = self.jwks_client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=list(self.policy.algorithms),
                audience=self.policy.audience,
                issuer=self.policy.issuer,
                leeway=self.policy.clock_skew_seconds,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            raise AuthenticationError("a valid OIDC bearer token is required") from exc
        subject = claims.get("sub")
        audience = claims.get("aud")
        issued_at = claims.get("iat")
        expires_at = claims.get("exp")
        trusted_audience = (
            audience == self.policy.audience
            or audience == [self.policy.audience]
            or audience == (self.policy.audience,)
        )
        if not (
            isinstance(issued_at, int)
            and not isinstance(issued_at, bool)
            and isinstance(expires_at, int)
            and not isinstance(expires_at, bool)
        ):
            raise AuthenticationError("OIDC token claims exceed the configured trust boundary")
        if (
            not trusted_audience
            or expires_at - issued_at > self.policy.max_token_lifetime_seconds
            or issued_at > int(time.time()) + self.policy.clock_skew_seconds
            or (claims.get("azp") is not None and claims.get("azp") != self.policy.audience)
        ):
            raise AuthenticationError("OIDC token claims exceed the configured trust boundary")
        if not isinstance(subject, str) or subject not in self.policy.subject_principals:
            raise AuthenticationError("OIDC subject is not mapped to a control-plane principal")
        return self.policy.subject_principals[subject]


def activate_oidc(policy: OidcPolicy) -> OidcAuthenticator | None:
    return OidcAuthenticator(policy) if policy.enabled else None
