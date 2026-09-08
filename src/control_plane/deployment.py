from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from control_plane.domain import ValidationError


class EnvironmentClassification(StrEnum):
    DEVELOPMENT = "DEVELOPMENT"
    STAGING = "STAGING"
    PRODUCTION = "PRODUCTION"


class DeploymentOperationKind(StrEnum):
    VERIFY_ARTIFACT = "VERIFY_ARTIFACT"
    APPLY_RELEASE = "APPLY_RELEASE"
    UPDATE_SERVICE = "UPDATE_SERVICE"


class DeploymentAttemptStatus(StrEnum):
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    ROLLBACK_REQUIRED = "ROLLBACK_REQUIRED"
    RECONCILED = "RECONCILED"


@dataclass(frozen=True)
class DeploymentPlan:
    plan_id: str
    workflow_id: str
    environment_id: str
    revision: str
    artifact_digests: tuple[str, ...]
    operations: tuple[dict[str, Any], ...]
    verification_probes: tuple[str, ...]
    rollback_reference: str
    policy_version: str
    digest: str


@dataclass(frozen=True)
class DeploymentExecution:
    operation_reference: str
    simulated: bool
    changed: bool
    evidence: dict[str, Any]


@dataclass(frozen=True)
class DeploymentObservation:
    observed_revision: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class DeploymentVerification:
    passed: bool
    observed_revision: str
    evidence: dict[str, Any]


class DeploymentAdapter(Protocol):
    adapter_id: str
    requires_credentials: bool

    def validate_plan(self, plan: DeploymentPlan) -> None: ...

    def execute(self, plan: DeploymentPlan, *, idempotency_key: str) -> DeploymentExecution: ...

    def observe(
        self, plan: DeploymentPlan, execution: DeploymentExecution
    ) -> DeploymentObservation: ...

    def verify(
        self, plan: DeploymentPlan, observation: DeploymentObservation
    ) -> DeploymentVerification: ...


class DryRunDeploymentAdapter:
    """Deterministic adapter that never obtains credentials or changes a target."""

    adapter_id = "dry-run-v1"
    requires_credentials = False

    def validate_plan(self, plan: DeploymentPlan) -> None:
        if not plan.operations:
            raise ValidationError("deployment plan requires at least one typed operation")
        for operation in plan.operations:
            try:
                DeploymentOperationKind(str(operation.get("kind", "")))
            except ValueError as exc:
                raise ValidationError("deployment operation kind is not allowlisted") from exc
            if set(operation) - {"kind", "resource_id", "artifact_digest"}:
                raise ValidationError("deployment operation contains unsupported fields")
            resource_id = str(operation.get("resource_id", "")).strip()
            if not resource_id or len(resource_id) > 256:
                raise ValidationError("deployment operation requires a resource ID")
            artifact_digest = str(operation.get("artifact_digest", ""))
            if artifact_digest not in plan.artifact_digests:
                raise ValidationError("deployment operation references an unbound artifact")

    def execute(self, plan: DeploymentPlan, *, idempotency_key: str) -> DeploymentExecution:
        self.validate_plan(plan)
        return DeploymentExecution(
            operation_reference=f"dry-run:{plan.digest}:{idempotency_key}",
            simulated=True,
            changed=False,
            evidence={
                "adapter_id": self.adapter_id,
                "credential_requested": False,
                "external_target_contacted": False,
                "operation_count": len(plan.operations),
                "plan_digest": plan.digest,
            },
        )

    def observe(
        self, plan: DeploymentPlan, execution: DeploymentExecution
    ) -> DeploymentObservation:
        if not execution.simulated or execution.changed:
            raise ValidationError("dry-run execution evidence is invalid")
        return DeploymentObservation(
            observed_revision=plan.revision,
            evidence={
                "simulated": True,
                "external_target_contacted": False,
                "observed_plan_digest": plan.digest,
            },
        )

    def verify(
        self, plan: DeploymentPlan, observation: DeploymentObservation
    ) -> DeploymentVerification:
        passed = (
            observation.observed_revision == plan.revision
            and observation.evidence.get("observed_plan_digest") == plan.digest
        )
        return DeploymentVerification(
            passed=passed,
            observed_revision=observation.observed_revision,
            evidence={
                "simulated": True,
                "revision_matches": observation.observed_revision == plan.revision,
                "plan_digest_matches": (
                    observation.evidence.get("observed_plan_digest") == plan.digest
                ),
                "probes": list(plan.verification_probes),
            },
        )
