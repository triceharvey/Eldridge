from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
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


class KubernetesTokenRequester(Protocol):
    def create_token(
        self,
        *,
        namespace: str,
        service_account: str,
        audience: str,
        duration_seconds: int,
    ) -> str: ...


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


class KubectlTokenRequester:
    """Narrow, local TokenRequest client with sanitized failures and no shell surface."""

    def __init__(
        self,
        *,
        kubeconfig: Path,
        kubectl_path: Path | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        resolved_kubeconfig = kubeconfig.expanduser().resolve()
        discovered_kubectl = shutil.which("kubectl") if kubectl_path is None else str(kubectl_path)
        resolved_kubectl = (
            Path(discovered_kubectl).expanduser().resolve() if discovered_kubectl else None
        )
        if not resolved_kubeconfig.is_file():
            raise ValidationError("Kubernetes token requester requires a kubeconfig file")
        if (
            resolved_kubectl is None
            or not resolved_kubectl.is_file()
            or not os.access(resolved_kubectl, os.X_OK)
        ):
            raise ValidationError("Kubernetes token requester requires an executable kubectl")
        if timeout_seconds < 1 or timeout_seconds > 60:
            raise ValidationError(
                "Kubernetes token requester timeout must be between 1 and 60 seconds"
            )
        self.kubeconfig = resolved_kubeconfig
        self.kubectl_path = resolved_kubectl
        self.timeout_seconds = timeout_seconds

    def create_token(
        self,
        *,
        namespace: str,
        service_account: str,
        audience: str,
        duration_seconds: int,
    ) -> str:
        if not all(_is_kubernetes_name(value) for value in (namespace, service_account)):
            raise ValidationError("Kubernetes TokenRequest target is invalid")
        if (
            not audience
            or len(audience) > 253
            or any(character.isspace() for character in audience)
        ):
            raise ValidationError("Kubernetes TokenRequest audience is invalid")
        command = [
            str(self.kubectl_path),
            "--kubeconfig",
            str(self.kubeconfig),
            "create",
            "token",
            service_account,
            "--namespace",
            namespace,
            "--audience",
            audience,
            "--duration",
            f"{duration_seconds}s",
        ]
        try:
            result = subprocess.run(  # noqa: S603 - executable and arguments are policy-bound
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env={"PATH": os.environ.get("PATH", "")},
            )
        except (OSError, subprocess.SubprocessError):
            raise ValidationError("Kubernetes TokenRequest invocation failed") from None
        if result.returncode != 0:
            raise ValidationError("Kubernetes TokenRequest was rejected")
        token = result.stdout.strip()
        if not token or len(token) > 16_384 or any(character.isspace() for character in token):
            raise ValidationError("Kubernetes TokenRequest returned an invalid credential")
        return token


class KubernetesTokenRequestBroker:
    """Explicitly enabled local broker that validates and immediately discards token material."""

    broker_id = "kubernetes-token-request-v1"

    def __init__(
        self,
        *,
        requester: KubernetesTokenRequester,
        environment_id: str,
        adapter_id: str,
        plan_digest: str,
        namespace: str,
        service_account: str,
        audience: str,
        issuer: str,
        resource_scope: tuple[str, ...],
        operation: CredentialOperation,
        enabled: bool = False,
        lifetime_seconds: int = 600,
    ) -> None:
        if not all(
            value.strip()
            for value in (
                environment_id,
                adapter_id,
                plan_digest,
                namespace,
                service_account,
                audience,
                issuer,
            )
        ):
            raise ValidationError("complete Kubernetes credential-broker policy is required")
        if not _is_kubernetes_name(namespace) or not _is_kubernetes_name(service_account):
            raise ValidationError("Kubernetes credential-broker identity is invalid")
        if not resource_scope or len(resource_scope) != len(set(resource_scope)):
            raise ValidationError(
                "Kubernetes credential resource scope must be non-empty and unique"
            )
        if lifetime_seconds != 600:
            raise ValidationError("local Kubernetes credential lifetime must equal 600 seconds")
        if not _is_sha256(plan_digest):
            raise ValidationError(
                "Kubernetes credential policy requires a lowercase SHA-256 digest"
            )
        self.requester = requester
        self.environment_id = environment_id
        self.adapter_id = adapter_id
        self.plan_digest = plan_digest
        self.namespace = namespace
        self.service_account = service_account
        self.audience = audience
        self.issuer = issuer
        self.subject = f"system:serviceaccount:{namespace}:{service_account}"
        self.resource_scope = tuple(sorted(resource_scope))
        self.operation = operation
        self.enabled = enabled
        self.lifetime_seconds = lifetime_seconds

    def issue(
        self,
        request: WorkloadCredentialRequest,
        *,
        now: datetime | None = None,
    ) -> WorkloadCredentialHandle:
        if not self.enabled:
            raise AuthorizationError("Kubernetes workload credential issuance is disabled")
        self._authorize(request)
        current_time = (now or datetime.now(UTC)).astimezone(UTC)
        try:
            token = self.requester.create_token(
                namespace=self.namespace,
                service_account=self.service_account,
                audience=self.audience,
                duration_seconds=self.lifetime_seconds,
            )
        except Exception:
            raise ValidationError("Kubernetes TokenRequest invocation failed") from None
        claims = _decode_jwt_claims(token)
        issued_at, expires_at = self._validate_claims(claims, current_time)
        reference = f"kubernetes-token:{uuid.uuid4()}"
        token = ""  # discard the only broker-owned reference before returning metadata
        del token
        return WorkloadCredentialHandle(
            reference=reference,
            broker_id=self.broker_id,
            environment_id=request.environment_id,
            plan_digest=request.plan_digest,
            operation=request.operation,
            audience=request.audience,
            subject=request.subject,
            resource_scope=tuple(sorted(request.resource_scope)),
            issued_at=issued_at,
            expires_at=expires_at,
            simulated=False,
        )

    def _authorize(self, request: WorkloadCredentialRequest) -> None:
        if request.environment_id != self.environment_id:
            raise AuthorizationError("credential environment is not allowlisted")
        if request.adapter_id != self.adapter_id:
            raise AuthorizationError("credential adapter is not allowlisted")
        if request.audience != self.audience or request.subject != self.subject:
            raise AuthorizationError("credential trust binding does not match policy")
        if request.operation is not self.operation:
            raise AuthorizationError("credential operation is not allowlisted")
        if tuple(sorted(request.resource_scope)) != self.resource_scope:
            raise AuthorizationError("credential resource scope does not exactly match policy")
        if request.lifetime_seconds != self.lifetime_seconds:
            raise AuthorizationError("credential lifetime does not exactly match policy")
        if request.plan_digest != self.plan_digest:
            raise AuthorizationError("credential plan digest does not exactly match policy")

    def _validate_claims(
        self, claims: dict[str, object], current_time: datetime
    ) -> tuple[datetime, datetime]:
        audience = claims.get("aud")
        audiences = [audience] if isinstance(audience, str) else audience
        if (
            audiences != [self.audience]
            or claims.get("sub") != self.subject
            or claims.get("iss") != self.issuer
        ):
            raise AuthorizationError("issued credential trust binding does not match policy")
        kubernetes_claims = claims.get("kubernetes.io")
        if not isinstance(kubernetes_claims, dict):
            raise AuthorizationError("issued credential workload identity is invalid")
        service_account_claims = kubernetes_claims.get("serviceaccount")
        if (
            kubernetes_claims.get("namespace") != self.namespace
            or not isinstance(service_account_claims, dict)
            or service_account_claims.get("name") != self.service_account
            or not service_account_claims.get("uid")
        ):
            raise AuthorizationError("issued credential workload identity is invalid")
        issued_value = claims.get("iat")
        expires_value = claims.get("exp")
        if (
            isinstance(issued_value, bool)
            or not isinstance(issued_value, int)
            or isinstance(expires_value, bool)
            or not isinstance(expires_value, int)
        ):
            raise ValidationError("issued credential timestamps are invalid")
        if expires_value - issued_value != self.lifetime_seconds:
            raise AuthorizationError("issued credential lifetime does not match policy")
        try:
            issued_at = datetime.fromtimestamp(issued_value, UTC)
            expires_at = datetime.fromtimestamp(expires_value, UTC)
        except (OverflowError, OSError, ValueError):
            raise ValidationError("issued credential timestamps are invalid") from None
        if abs((current_time - issued_at).total_seconds()) > 60 or expires_at <= current_time:
            raise AuthorizationError("issued credential validity window is invalid")
        return issued_at, expires_at


def _is_kubernetes_name(value: str) -> bool:
    return bool(len(value) <= 63 and re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", value))


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _decode_jwt_claims(token: str) -> dict[str, object]:
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise ValidationError("Kubernetes TokenRequest returned a malformed credential")
    try:
        encoded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(encoded).decode())
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValidationError("Kubernetes TokenRequest returned a malformed credential") from None
    if not isinstance(claims, dict):
        raise ValidationError("Kubernetes TokenRequest returned malformed claims")
    return claims
