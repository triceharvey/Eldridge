from __future__ import annotations

import os
from typing import Protocol

from control_plane.domain import AuthorizationError


class SecretResolver(Protocol):
    def resolve(self, secret_ref: str) -> str: ...


class EnvironmentSecretResolver:
    """Development-only resolver with an explicit reference-to-variable allowlist."""

    def __init__(self, references: dict[str, str]) -> None:
        self.references = dict(references)

    def resolve(self, secret_ref: str) -> str:
        variable = self.references.get(secret_ref)
        if variable is None:
            raise AuthorizationError("secret reference is not allowlisted")
        value = os.getenv(variable)
        if not value:
            raise AuthorizationError("referenced development secret is unavailable")
        return value


class StaticSecretResolver:
    """Test-only resolver; production code should use short-lived secret references."""

    def __init__(self, values: dict[str, str]) -> None:
        self.values = dict(values)

    def resolve(self, secret_ref: str) -> str:
        value = self.values.get(secret_ref)
        if value is None:
            raise AuthorizationError("secret reference is unavailable")
        return value
