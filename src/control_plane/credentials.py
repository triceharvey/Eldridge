from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from control_plane.domain import AuthorizationError, ValidationError


class CredentialOperation(StrEnum):
    APPLY_PLAN = "APPLY_PLAN"
    OBSERVE = "OBSERVE"
    VERIFY = "VERIFY"
    ROLLBACK = "ROLLBACK"


@dataclass(frozen=True)
class WorkloadCredentialRequest:
    environment_id: str
    adapter_id: str
    plan_digest: str
    operation: CredentialOperation
    audience: str
    subject: str
    resource_scope: tuple[str, ...]
    lifetime_seconds: int


@dataclass(frozen=True)
class WorkloadCredentialHandle:
    """Non-secret reference and issuance metadata; credential material is never exposed."""

    reference: str
    broker_id: str
    environment_id: str
    plan_digest: str
    operation: CredentialOperation
    audience: str
    subject: str
    resource_scope: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime
    simulated: bool


class CredentialBroker(Protocol):
    broker_id: str

    def issue(
        self,
        request: WorkloadCredentialRequest,
        *,
        now: datetime | None = None,
    ) -> WorkloadCredentialHandle: ...


class DenyCredentialBroker:
    """Default broker: fail closed until an explicit broker policy is installed."""

    broker_id = "deny-all-v1"

    def issue(
        self,
        request: WorkloadCredentialRequest,
        *,
        now: datetime | None = None,
    ) -> WorkloadCredentialHandle:
        del request, now
        raise AuthorizationError("workload credential issuance is disabled")


class FakeCredentialBroker:
    """Policy-enforcing broker that returns metadata only and never creates a credential."""

    broker_id = "fake-local-v1"

    def __init__(
        self,
        *,
        environment_id: str,
        adapter_id: str,
        audience: str,
        subject: str,
        resource_scope: frozenset[str],
        allowed_operations: frozenset[CredentialOperation],
        max_lifetime_seconds: int = 300,
    ) -> None:
        if not all(value.strip() for value in (environment_id, adapter_id, audience, subject)):
            raise ValidationError("complete fake credential-broker policy is required")
        if not resource_scope or not allowed_operations:
            raise ValidationError("fake credential-broker allowlists cannot be empty")
        if max_lifetime_seconds < 1 or max_lifetime_seconds > 900:
            raise ValidationError("fake credential lifetime must be between 1 and 900 seconds")
        self.environment_id = environment_id
        self.adapter_id = adapter_id
        self.audience = audience
        self.subject = subject
        self.resource_scope = resource_scope
        self.allowed_operations = allowed_operations
        self.max_lifetime_seconds = max_lifetime_seconds

    def issue(
        self,
        request: WorkloadCredentialRequest,
        *,
        now: datetime | None = None,
    ) -> WorkloadCredentialHandle:
        self._authorize(request)
        issued_at = (now or datetime.now(UTC)).astimezone(UTC)
        request_payload = asdict(request)
        request_payload["operation"] = request.operation.value
        request_payload["issued_at"] = issued_at.isoformat()
        digest = hashlib.sha256(
            json.dumps(request_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return WorkloadCredentialHandle(
            reference=f"fake-credential:{digest}",
            broker_id=self.broker_id,
            environment_id=request.environment_id,
            plan_digest=request.plan_digest,
            operation=request.operation,
            audience=request.audience,
            subject=request.subject,
            resource_scope=tuple(sorted(request.resource_scope)),
            issued_at=issued_at,
            expires_at=issued_at + timedelta(seconds=request.lifetime_seconds),
            simulated=True,
        )

    def _authorize(self, request: WorkloadCredentialRequest) -> None:
        if request.environment_id != self.environment_id:
            raise AuthorizationError("credential environment is not allowlisted")
        if request.adapter_id != self.adapter_id:
            raise AuthorizationError("credential adapter is not allowlisted")
        if request.audience != self.audience or request.subject != self.subject:
            raise AuthorizationError("credential trust binding does not match policy")
        if request.operation not in self.allowed_operations:
            raise AuthorizationError("credential operation is not allowlisted")
        if not request.resource_scope or len(request.resource_scope) != len(
            set(request.resource_scope)
        ):
            raise ValidationError("credential resource scope must be non-empty and unique")
        if not set(request.resource_scope).issubset(self.resource_scope):
            raise AuthorizationError("credential resource scope exceeds policy")
        if request.lifetime_seconds < 1 or request.lifetime_seconds > self.max_lifetime_seconds:
            raise AuthorizationError("credential lifetime exceeds policy")
        if len(request.plan_digest) != 64 or any(
            character not in "0123456789abcdef" for character in request.plan_digest
        ):
            raise ValidationError("credential request requires a lowercase SHA-256 plan digest")
