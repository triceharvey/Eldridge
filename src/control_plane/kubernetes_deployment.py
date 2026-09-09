from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from control_plane.credentials import (
    CredentialOperation,
    WorkloadCredentialHandle,
    WorkloadCredentialRequest,
)
from control_plane.deployment import (
    CredentialedDeploymentRun,
    DeploymentExecution,
    DeploymentObservation,
    DeploymentOperationKind,
    DeploymentPlan,
    DeploymentVerification,
)
from control_plane.domain import (
    DeploymentOutcomeUnknownError,
    DeploymentVerificationError,
    ValidationError,
)


class KubernetesApiClient(Protocol):
    def get(self, url: str, **kwargs: object) -> httpx.Response: ...

    def put(self, url: str, **kwargs: object) -> httpx.Response: ...


class LocalK3dConfigMapAdapter:
    """Exact-scope local adapter that updates one pre-created release marker."""

    adapter_id = "local-k3d-configmap-v1"
    requires_credentials = True
    resource_id = "configmap/eldridge-release"
    verification_probes = (
        "revision-match",
        "plan-digest-match",
        "artifact-digest-match",
    )
    rollback_reference = "restore-previous-release-marker-v1"
    policy_version = "deployment/local-k3d-v1"

    def __init__(
        self,
        *,
        client: KubernetesApiClient,
        api_server: str,
        environment_id: str = "eldridge-local-k3d",
        namespace: str = "eldridge-validation",
        audience: str = "https://kubernetes.default.svc.cluster.local",
        subject: str = "system:serviceaccount:eldridge-validation:deployment-worker",
        lifetime_seconds: int = 600,
    ) -> None:
        parsed_server = urlsplit(api_server)
        if (
            parsed_server.scheme != "https"
            or parsed_server.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed_server.username is not None
            or parsed_server.password is not None
            or parsed_server.path not in {"", "/"}
            or parsed_server.query
            or parsed_server.fragment
        ):
            raise ValidationError("local k3d adapter requires an explicit loopback HTTPS API")
        if environment_id != "eldridge-local-k3d" or namespace != "eldridge-validation":
            raise ValidationError("local k3d adapter target is not allowlisted")
        if lifetime_seconds != 600:
            raise ValidationError("local k3d adapter credential lifetime must equal 600 seconds")
        self.client = client
        self.environment_id = environment_id
        self.namespace = namespace
        self.audience = audience
        self.subject = subject
        self.lifetime_seconds = lifetime_seconds
        self.path = (
            f"{api_server.rstrip('/')}/api/v1/namespaces/{namespace}/configmaps/eldridge-release"
        )

    def validate_plan(self, plan: DeploymentPlan) -> None:
        if plan.environment_id != self.environment_id:
            raise ValidationError("deployment plan environment does not match local k3d adapter")
        if plan.policy_version != self.policy_version:
            raise ValidationError("deployment plan policy does not match local k3d adapter")
        if plan.rollback_reference != self.rollback_reference:
            raise ValidationError("deployment rollback policy does not match local k3d adapter")
        if tuple(plan.verification_probes) != self.verification_probes:
            raise ValidationError("deployment verification probes do not match local k3d adapter")
        if (
            not re.fullmatch(r"[0-9a-f]{40}", plan.revision)
            or not re.fullmatch(r"[0-9a-f]{64}", plan.digest)
            or len(plan.artifact_digests) != 1
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", plan.artifact_digests[0])
        ):
            raise ValidationError("local k3d plan revision or digest binding is invalid")
        if len(plan.operations) != 1:
            raise ValidationError("local k3d deployment requires exactly one operation")
        operation = plan.operations[0]
        if set(operation) != {"kind", "resource_id", "artifact_digest"}:
            raise ValidationError("local k3d operation fields do not exactly match policy")
        if operation.get("kind") != DeploymentOperationKind.UPDATE_SERVICE.value:
            raise ValidationError("local k3d operation kind is not allowlisted")
        if operation.get("resource_id") != self.resource_id:
            raise ValidationError("local k3d operation resource does not match policy")
        if operation.get("artifact_digest") not in plan.artifact_digests:
            raise ValidationError("local k3d operation references an unbound artifact")

    def credential_request(self, plan: DeploymentPlan) -> WorkloadCredentialRequest:
        self.validate_plan(plan)
        return WorkloadCredentialRequest(
            environment_id=self.environment_id,
            adapter_id=self.adapter_id,
            plan_digest=plan.digest,
            operation=CredentialOperation.APPLY_PLAN,
            audience=self.audience,
            subject=self.subject,
            resource_scope=(f"namespace/{self.namespace}/{self.resource_id}",),
            lifetime_seconds=self.lifetime_seconds,
        )

    def run_with_credential(
        self,
        plan: DeploymentPlan,
        credential: str,
        handle: WorkloadCredentialHandle,
        *,
        idempotency_key: str,
    ) -> CredentialedDeploymentRun:
        self.validate_plan(plan)
        if (
            not idempotency_key
            or len(idempotency_key) > 128
            or not re.fullmatch(r"[A-Za-z0-9._-]+", idempotency_key)
        ):
            raise ValidationError("local k3d idempotency key is invalid")
        request = self.credential_request(plan)
        if (
            handle.environment_id != request.environment_id
            or handle.plan_digest != request.plan_digest
            or handle.operation is not request.operation
            or handle.audience != request.audience
            or handle.subject != request.subject
            or handle.resource_scope != request.resource_scope
            or handle.simulated
            or handle.expires_at - handle.issued_at != timedelta(seconds=600)
            or handle.expires_at <= datetime.now(UTC)
        ):
            raise ValidationError("credential handle does not match deployment plan")
        headers = {"Authorization": f"Bearer {credential}", "Accept": "application/json"}
        current = self._read(headers, after_change=False)
        previous = self._release_data(current)
        artifact_digest = str(plan.operations[0]["artifact_digest"])
        desired = {
            "artifact_digest": artifact_digest,
            "idempotency_key": idempotency_key,
            "plan_digest": plan.digest,
            "revision": plan.revision,
        }
        changed = previous != desired
        if changed:
            metadata = current.get("metadata")
            if not isinstance(metadata, dict) or not metadata.get("resourceVersion"):
                raise ValidationError("release marker lacks a Kubernetes resource version")
            body = {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": "eldridge-release",
                    "namespace": self.namespace,
                    "resourceVersion": metadata["resourceVersion"],
                    "labels": {"app.kubernetes.io/managed-by": "eldridge"},
                },
                "data": desired,
            }
            try:
                response = self.client.put(self.path, headers=headers, json=body)
            except (httpx.TimeoutException, httpx.TransportError):
                raise DeploymentOutcomeUnknownError(
                    "local k3d update outcome is unknown and requires reconciliation"
                ) from None
            if response.status_code != 200:
                raise ValidationError("local k3d update was rejected before confirmation")

        observed = self._read(headers, after_change=changed)
        observed_data = self._release_data(observed)
        observation = DeploymentObservation(
            observed_revision=str(observed_data.get("revision", "")),
            evidence={
                "artifact_digest": observed_data.get("artifact_digest"),
                "idempotency_key": observed_data.get("idempotency_key"),
                "plan_digest": observed_data.get("plan_digest"),
                "resource_id": self.resource_id,
            },
        )
        verification = DeploymentVerification(
            passed=observed_data == desired,
            observed_revision=observation.observed_revision,
            evidence={
                "artifact_digest_matches": observed_data.get("artifact_digest") == artifact_digest,
                "plan_digest_matches": observed_data.get("plan_digest") == plan.digest,
                "revision_matches": observed_data.get("revision") == plan.revision,
                "probes": list(self.verification_probes),
            },
        )
        execution = DeploymentExecution(
            operation_reference=f"k3d-configmap:{plan.digest}:{idempotency_key}",
            simulated=False,
            changed=changed,
            evidence={
                "adapter_id": self.adapter_id,
                "credential_reference": handle.reference,
                "external_target_contacted": True,
                "plan_digest": plan.digest,
                "previous_release": previous,
                "resource": self.resource_id,
            },
        )
        return CredentialedDeploymentRun(
            execution=execution,
            observation=observation,
            verification=verification,
        )

    def _read(self, headers: dict[str, str], *, after_change: bool) -> dict[str, object]:
        try:
            response = self.client.get(self.path, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError):
            if after_change:
                raise DeploymentVerificationError(
                    "local k3d observation failed after a confirmed change"
                ) from None
            raise ValidationError("local k3d target was unavailable before mutation") from None
        if response.status_code != 200:
            if after_change:
                raise DeploymentVerificationError(
                    "local k3d release marker could not be observed after change"
                )
            raise ValidationError("local k3d release marker is unavailable")
        try:
            payload = response.json()
        except ValueError:
            raise ValidationError("local k3d returned malformed release-marker evidence") from None
        if not isinstance(payload, dict):
            raise ValidationError("local k3d returned malformed release-marker evidence")
        return payload

    @staticmethod
    def _release_data(payload: dict[str, object]) -> dict[str, str]:
        data = payload.get("data", {})
        if not isinstance(data, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in data.items()
        ):
            raise ValidationError("local k3d release-marker data is malformed")
        return dict(data)
