from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from time import monotonic
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from control_plane.audit import append_audit_event
from control_plane.credentials import (
    CredentialBroker,
    CredentialSessionBroker,
    CredentialSessionBrokerFactory,
    WorkloadCredentialHandle,
)
from control_plane.deployment import (
    CredentialedDeploymentAdapter,
    CredentialedDeploymentRun,
    CredentialedRollbackRun,
    DeploymentAdapter,
    DeploymentAttemptStatus,
    DeploymentPlan,
    EnvironmentClassification,
    RecoverableDeploymentAdapter,
    RollbackStatus,
)
from control_plane.domain import (
    ApprovalAction,
    ApprovalDecision,
    AuthorizationError,
    Capability,
    ConflictError,
    DeploymentOutcomeUnknownError,
    DeploymentVerificationError,
    InvalidTransitionError,
    NotFoundError,
    ValidationError,
    WorkflowState,
)
from control_plane.persistence import (
    Approval,
    DeploymentAttemptRecord,
    DeploymentEnvironment,
    DeploymentPlanRecord,
    DeploymentRollbackRecord,
    DeploymentVerificationRecord,
    Workflow,
)
from control_plane.policy import PolicyEngine


class DeploymentRecoveryService:
    """Immutable deployment planning, execution, credential use, and recovery boundary."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        policy: PolicyEngine,
        *,
        deployment_adapters: Mapping[str, DeploymentAdapter | CredentialedDeploymentAdapter],
        credential_broker: CredentialBroker,
        credential_broker_factory: CredentialSessionBrokerFactory | None,
        enable_local_deployment: bool,
        transition: Callable[..., None],
    ) -> None:
        self.session_factory = session_factory
        self.policy = policy
        self.deployment_adapters = deployment_adapters
        self.credential_broker = credential_broker
        self.credential_broker_factory = credential_broker_factory
        self.enable_local_deployment = enable_local_deployment
        self._transition = transition

    def register_deployment_environment(
        self,
        *,
        actor_id: str,
        environment_id: str,
        name: str,
        classification: EnvironmentClassification,
        repository: str,
        base_branch: str,
        resource_scope: tuple[str, ...],
        required_checks: tuple[str, ...],
        required_attestations: tuple[str, ...],
        verification_policy: tuple[str, ...],
        rollback_policy: str,
        policy_version: str,
        provider: str = "dry-run",
        account_scope: str = "none",
        region: str = "none",
        adapter_id: str = "dry-run-v1",
    ) -> dict[str, Any]:
        values = {
            "environment_id": environment_id.strip(),
            "name": name.strip(),
            "classification": classification.value,
            "provider": provider.strip(),
            "account_scope": account_scope.strip(),
            "region": region.strip(),
            "resource_scope": sorted(set(resource_scope)),
            "adapter_id": adapter_id.strip(),
            "policy_version": policy_version.strip(),
            "repository": repository.strip(),
            "base_branch": base_branch.strip(),
            "required_checks": sorted(set(required_checks)),
            "required_attestations": sorted(set(required_attestations)),
            "verification_policy": list(verification_policy),
            "rollback_policy": rollback_policy.strip(),
        }
        if not all(
            values[key]
            for key in (
                "environment_id",
                "name",
                "repository",
                "base_branch",
                "policy_version",
                "rollback_policy",
            )
        ):
            raise ValidationError("complete deployment environment policy is required")
        if (
            len(environment_id) > 128
            or len(name) > 200
            or len(repository) > 500
            or len(base_branch) > 200
            or len(policy_version) > 64
            or len(rollback_policy) > 256
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
                for character in environment_id
            )
        ):
            raise ValidationError("deployment environment policy exceeds its boundary")
        collections = (
            resource_scope,
            required_checks,
            required_attestations,
            verification_policy,
        )
        if any(
            len(items) > 100 or any(not item.strip() or len(item) > 256 for item in items)
            for items in collections
        ):
            raise ValidationError("deployment environment collection is invalid")
        if len(resource_scope) != len(set(resource_scope)):
            raise ValidationError("deployment resource scope contains duplicates")
        if len(required_checks) != len(set(required_checks)) or len(required_attestations) != len(
            set(required_attestations)
        ):
            raise ValidationError("deployment environment requirements contain duplicates")
        if len(verification_policy) != len(set(verification_policy)):
            raise ValidationError("deployment verification policy contains duplicates")
        if classification is EnvironmentClassification.PRODUCTION:
            raise AuthorizationError("Phase 4 does not permit production environments")
        adapter = self.deployment_adapters.get(adapter_id)
        if adapter is None:
            raise AuthorizationError("deployment adapter is not approved")
        if adapter.requires_credentials:
            if (
                not self.enable_local_deployment
                or classification is not EnvironmentClassification.DEVELOPMENT
                or provider != "local-k3d"
                or account_scope != "local"
                or region != "local"
            ):
                raise AuthorizationError("credentialed deployment is restricted to local k3d")
        elif provider != "dry-run" or account_scope != "none" or region != "none":
            raise AuthorizationError("dry-run environment must have no external target")
        config_digest = self._digest(values)
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session,
                actor_id,
                Capability.MANAGE_DEPLOYMENT_ENVIRONMENTS,
                require_human=True,
            )
            existing = session.get(DeploymentEnvironment, environment_id)
            if existing is not None:
                if existing.config_digest != config_digest:
                    raise ConflictError("deployment environment ID is already bound")
                return self._deployment_environment_dict(existing)
            environment = DeploymentEnvironment(
                id=values["environment_id"],
                name=values["name"],
                classification=values["classification"],
                provider=values["provider"],
                account_scope=values["account_scope"],
                region=values["region"],
                resource_scope=values["resource_scope"],
                adapter_id=values["adapter_id"],
                policy_version=values["policy_version"],
                repository=values["repository"],
                base_branch=values["base_branch"],
                required_checks=values["required_checks"],
                required_attestations=values["required_attestations"],
                verification_policy=values["verification_policy"],
                rollback_policy=values["rollback_policy"],
                config_digest=config_digest,
                active=True,
                created_by=actor_id,
            )
            session.add(environment)
            session.flush()
            return self._deployment_environment_dict(environment)

    def list_deployment_environments(self, *, principal_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_DEPLOYMENT)
            environments = session.scalars(
                select(DeploymentEnvironment).order_by(DeploymentEnvironment.id)
            ).all()
            return [self._deployment_environment_dict(item) for item in environments]

    def create_deployment_plan(
        self,
        *,
        workflow_id: str,
        actor_id: str,
        environment_id: str,
        artifact_digests: tuple[str, ...],
        operations: tuple[dict[str, Any], ...],
        declared_impact: str,
        verification_probes: tuple[str, ...],
        rollback_reference: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if (
            not idempotency_key.strip()
            or not declared_impact.strip()
            or not rollback_reference.strip()
        ):
            raise ValidationError("deployment plan intent, rollback, and idempotency are required")
        if (
            len(idempotency_key) > 128
            or len(declared_impact) > 4_000
            or len(rollback_reference) > 256
            or len(artifact_digests) > 100
            or len(operations) > 100
            or len(verification_probes) > 100
            or any(not probe.strip() or len(probe) > 256 for probe in verification_probes)
        ):
            raise ValidationError("deployment plan exceeds its boundary")
        if not artifact_digests or len(artifact_digests) != len(set(artifact_digests)):
            raise ValidationError("deployment plan requires unique artifact digests")
        if not operations or not verification_probes:
            raise ValidationError("deployment operations and verification probes are required")
        if any(not self._is_sha256_digest(item) for item in artifact_digests):
            raise ValidationError("artifact digests must use sha256:<64 lowercase hex>")
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session, actor_id, Capability.CREATE_DEPLOYMENT_PLAN, require_human=True
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            existing = session.scalar(
                select(DeploymentPlanRecord).where(
                    DeploymentPlanRecord.actor_id == actor_id,
                    DeploymentPlanRecord.idempotency_key == idempotency_key,
                )
            )
            environment = session.get(DeploymentEnvironment, environment_id)
            if environment is None or not environment.active:
                raise NotFoundError("active deployment environment not found")
            revision = workflow.merged_revision
            request_payload = {
                "workflow_id": workflow_id,
                "environment_id": environment_id,
                "environment_config_digest": environment.config_digest,
                "revision": revision,
                "artifact_digests": sorted(artifact_digests),
                "operations": list(operations),
                "declared_impact": declared_impact.strip(),
                "verification_probes": list(verification_probes),
                "rollback_reference": rollback_reference.strip(),
                "policy_version": environment.policy_version,
            }
            request_digest = self._digest(request_payload)
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError("deployment-plan idempotency key was reused")
                return self._deployment_plan_dict(existing)
            if WorkflowState(workflow.state) is not WorkflowState.MERGED:
                raise InvalidTransitionError("workflow is not ready for a deployment plan")
            if not revision:
                raise ConflictError("workflow lacks an independently confirmed merge revision")
            if workflow.repository_scope != environment.repository:
                raise ConflictError("deployment environment repository does not match workflow")
            operation_resources = {
                str(operation.get("resource_id", "")).strip() for operation in operations
            }
            if not operation_resources.issubset(set(environment.resource_scope)):
                raise AuthorizationError("deployment operation exceeds environment resource scope")
            if not set(verification_probes).issubset(set(environment.verification_policy)):
                raise AuthorizationError("deployment probe exceeds environment verification policy")
            if rollback_reference.strip() != environment.rollback_policy:
                raise ConflictError(
                    "deployment rollback reference does not match environment policy"
                )
            plan_id = str(uuid4())
            plan_digest = self._digest(request_payload)
            plan = DeploymentPlan(
                plan_id=plan_id,
                workflow_id=workflow_id,
                environment_id=environment_id,
                revision=revision,
                artifact_digests=tuple(sorted(artifact_digests)),
                operations=operations,
                verification_probes=verification_probes,
                rollback_reference=rollback_reference.strip(),
                policy_version=environment.policy_version,
                digest=plan_digest,
            )
            adapter = self.deployment_adapters.get(environment.adapter_id)
            if adapter is None:
                raise AuthorizationError("deployment environment adapter is disabled")
            adapter.validate_plan(plan)
            record = DeploymentPlanRecord(
                id=plan_id,
                workflow_id=workflow_id,
                environment_id=environment_id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                revision=revision,
                artifact_digests=list(plan.artifact_digests),
                operations=list(operations),
                declared_impact=declared_impact.strip(),
                verification_probes=list(verification_probes),
                rollback_reference=rollback_reference.strip(),
                policy_version=environment.policy_version,
                digest=plan_digest,
            )
            session.add(record)
            self._transition(
                session,
                workflow,
                WorkflowState.AWAITING_DEPLOYMENT_APPROVAL,
                actor_id=actor_id,
                reason="immutable deployment plan created for confirmed merge revision",
                extra={
                    "deployment_plan_id": plan_id,
                    "environment_id": environment_id,
                    "plan_digest": plan_digest,
                    "revision": revision,
                    "simulated": not adapter.requires_credentials,
                },
            )
            session.flush()
            return self._deployment_plan_dict(record)

    def list_deployment_plans(self, workflow_id: str, *, principal_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_DEPLOYMENT)
            self._get_workflow(session, workflow_id)
            records = session.scalars(
                select(DeploymentPlanRecord)
                .where(DeploymentPlanRecord.workflow_id == workflow_id)
                .order_by(DeploymentPlanRecord.created_at)
            ).all()
            return [self._deployment_plan_dict(item) for item in records]

    def execute_deployment_dry_run(
        self,
        *,
        workflow_id: str,
        plan_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request_digest = self._digest(
            {"workflow_id": workflow_id, "plan_id": plan_id, "mode": "DRY_RUN"}
        )
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session, actor_id, Capability.EXECUTE_DEPLOYMENT, require_human=True
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            existing = session.scalar(
                select(DeploymentAttemptRecord).where(
                    DeploymentAttemptRecord.actor_id == actor_id,
                    DeploymentAttemptRecord.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError("deployment idempotency key was reused")
                if existing.status == DeploymentAttemptStatus.SUCCEEDED.value:
                    return self._deployment_attempt_dict(existing)
                raise ConflictError("deployment attempt is not replayable")
            if WorkflowState(workflow.state) is not WorkflowState.AWAITING_DEPLOYMENT_APPROVAL:
                raise InvalidTransitionError("workflow is not awaiting deployment approval")
            plan_record = session.get(DeploymentPlanRecord, plan_id)
            if plan_record is None or plan_record.workflow_id != workflow_id:
                raise NotFoundError("deployment plan not found")
            environment = session.get(DeploymentEnvironment, plan_record.environment_id)
            if environment is None or not environment.active:
                raise ConflictError("deployment environment is unavailable")
            adapter = self.deployment_adapters.get(environment.adapter_id)
            if adapter is None or adapter.requires_credentials:
                raise AuthorizationError("only the no-credential dry-run adapter is permitted")
            approval = session.scalar(
                select(Approval)
                .where(
                    Approval.workflow_id == workflow_id,
                    Approval.action == ApprovalAction.DEPLOY.value,
                    Approval.decision == ApprovalDecision.APPROVED.value,
                    Approval.environment_id == environment.id,
                    Approval.plan_digest == plan_record.digest,
                    Approval.revision == plan_record.revision,
                    Approval.policy_version == plan_record.policy_version,
                    Approval.consumed_at.is_(None),
                )
                .order_by(Approval.created_at.desc())
                .limit(1)
            )
            if approval is None:
                raise AuthorizationError("exact unconsumed deployment approval is required")
            if self._as_utc(approval.expires_at) <= datetime.now(UTC):
                raise AuthorizationError("deployment approval has expired")
            plan = self._deployment_plan_value(plan_record)
            adapter.validate_plan(plan)
            attempt = DeploymentAttemptRecord(
                workflow_id=workflow_id,
                plan_id=plan_id,
                approval_id=approval.id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                adapter_id=adapter.adapter_id,
                status=DeploymentAttemptStatus.RUNNING.value,
                simulated=True,
                result_json={"intent_committed": True, "credential_requested": False},
            )
            session.add(attempt)
            session.flush()
            approval.consumed_at = datetime.now(UTC)
            attempt_id = attempt.id
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="deployment.dry_run_started",
                actor_id=actor_id,
                resource_type="deployment_attempt",
                resource_id=attempt_id,
                outcome="STARTED",
                payload={
                    "plan_id": plan_id,
                    "plan_digest": plan.digest,
                    "environment_id": plan.environment_id,
                    "revision": plan.revision,
                    "approval_id": approval.id,
                    "credential_requested": False,
                },
            )
        try:
            dry_run_adapter = cast(DeploymentAdapter, adapter)
            execution = dry_run_adapter.execute(plan, idempotency_key=attempt_id)
            observation = dry_run_adapter.observe(plan, execution)
            verification = dry_run_adapter.verify(plan, observation)
        except Exception as exc:
            self._fail_deployment_dry_run(attempt_id, type(exc).__name__)
            raise
        with self.session_factory() as session, session.begin():
            finalized_attempt = session.get(
                DeploymentAttemptRecord, attempt_id, with_for_update=True
            )
            if (
                finalized_attempt is None
                or finalized_attempt.status != DeploymentAttemptStatus.RUNNING.value
            ):
                raise ConflictError("deployment attempt is no longer running")
            workflow = self._get_workflow(session, workflow_id, lock=True)
            finalized_attempt.operation_reference = execution.operation_reference
            finalized_attempt.simulated = execution.simulated
            finalized_attempt.result_json = {
                "execution": execution.evidence,
                "observation": observation.evidence,
                "changed": execution.changed,
            }
            finalized_attempt.completed_at = datetime.now(UTC)
            status = (
                DeploymentAttemptStatus.SUCCEEDED
                if verification.passed and not execution.changed and execution.simulated
                else DeploymentAttemptStatus.ROLLBACK_REQUIRED
            )
            finalized_attempt.status = status.value
            session.add(
                DeploymentVerificationRecord(
                    workflow_id=workflow_id,
                    attempt_id=finalized_attempt.id,
                    passed=verification.passed,
                    observed_revision=verification.observed_revision,
                    evidence=verification.evidence,
                )
            )
            if status is DeploymentAttemptStatus.ROLLBACK_REQUIRED:
                self._transition(
                    session,
                    workflow,
                    WorkflowState.ROLLBACK_REQUIRED,
                    actor_id="orchestrator",
                    reason="deployment dry-run evidence violated its no-change invariant",
                    extra={"deployment_attempt_id": finalized_attempt.id},
                )
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="deployment.dry_run_completed",
                actor_id="orchestrator",
                resource_type="deployment_attempt",
                resource_id=finalized_attempt.id,
                outcome=status.value,
                payload={
                    "plan_digest": plan.digest,
                    "revision": plan.revision,
                    "verification_passed": verification.passed,
                    "simulated": execution.simulated,
                    "changed": execution.changed,
                    "credential_requested": False,
                },
            )
            session.flush()
            return self._deployment_attempt_dict(finalized_attempt)

    def execute_local_deployment(
        self,
        *,
        workflow_id: str,
        plan_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not idempotency_key.strip() or len(idempotency_key) > 128:
            raise ValidationError("deployment idempotency key is invalid")
        if not self.enable_local_deployment or (
            self.credential_broker_factory is None
            and not isinstance(self.credential_broker, CredentialSessionBroker)
        ):
            raise AuthorizationError("local credentialed deployment is disabled")
        request_digest = self._digest(
            {"workflow_id": workflow_id, "plan_id": plan_id, "mode": "LOCAL_K3D"}
        )
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session, actor_id, Capability.EXECUTE_DEPLOYMENT, require_human=True
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            existing = session.scalar(
                select(DeploymentAttemptRecord).where(
                    DeploymentAttemptRecord.actor_id == actor_id,
                    DeploymentAttemptRecord.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError("deployment idempotency key was reused")
                if existing.status == DeploymentAttemptStatus.SUCCEEDED.value:
                    return self._deployment_attempt_dict(existing)
                raise ConflictError("deployment attempt is not replayable")
            if WorkflowState(workflow.state) is not WorkflowState.AWAITING_DEPLOYMENT_APPROVAL:
                raise InvalidTransitionError("workflow is not awaiting deployment approval")
            plan_record = session.get(DeploymentPlanRecord, plan_id)
            if plan_record is None or plan_record.workflow_id != workflow_id:
                raise NotFoundError("deployment plan not found")
            environment = session.get(DeploymentEnvironment, plan_record.environment_id)
            if (
                environment is None
                or not environment.active
                or environment.classification != EnvironmentClassification.DEVELOPMENT.value
                or environment.provider != "local-k3d"
                or environment.account_scope != "local"
                or environment.region != "local"
            ):
                raise AuthorizationError("deployment environment is not an active local target")
            adapter = self.deployment_adapters.get(environment.adapter_id)
            if adapter is None or not adapter.requires_credentials:
                raise AuthorizationError("credentialed local deployment adapter is unavailable")
            approval = session.scalar(
                select(Approval)
                .where(
                    Approval.workflow_id == workflow_id,
                    Approval.action == ApprovalAction.DEPLOY.value,
                    Approval.decision == ApprovalDecision.APPROVED.value,
                    Approval.environment_id == environment.id,
                    Approval.plan_digest == plan_record.digest,
                    Approval.revision == plan_record.revision,
                    Approval.policy_version == plan_record.policy_version,
                    Approval.consumed_at.is_(None),
                )
                .order_by(Approval.created_at.desc())
                .limit(1)
            )
            if approval is None:
                raise AuthorizationError("exact unconsumed deployment approval is required")
            if self._as_utc(approval.expires_at) <= datetime.now(UTC):
                raise AuthorizationError("deployment approval has expired")
            plan = self._deployment_plan_value(plan_record)
            adapter.validate_plan(plan)
            attempt = DeploymentAttemptRecord(
                workflow_id=workflow_id,
                plan_id=plan_id,
                approval_id=approval.id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                adapter_id=adapter.adapter_id,
                status=DeploymentAttemptStatus.RUNNING.value,
                simulated=False,
                result_json={"intent_committed": True, "credential_requested": True},
            )
            session.add(attempt)
            session.flush()
            approval.consumed_at = datetime.now(UTC)
            attempt_id = attempt.id
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="deployment.local_started",
                actor_id=actor_id,
                resource_type="deployment_attempt",
                resource_id=attempt_id,
                outcome="STARTED",
                payload={
                    "adapter_id": adapter.adapter_id,
                    "approval_id": approval.id,
                    "environment_id": plan.environment_id,
                    "plan_digest": plan.digest,
                    "revision": plan.revision,
                },
            )

        credentialed_adapter = cast(CredentialedDeploymentAdapter, adapter)
        target_contacted = False

        def run_adapter(
            credential: str, handle: WorkloadCredentialHandle
        ) -> CredentialedDeploymentRun:
            nonlocal target_contacted
            target_contacted = True
            return credentialed_adapter.run_with_credential(
                plan,
                credential,
                handle,
                idempotency_key=attempt_id,
            )

        try:
            credential_request = credentialed_adapter.credential_request(plan)
            session_broker = (
                self.credential_broker_factory.for_request(credential_request)
                if self.credential_broker_factory is not None
                else cast(CredentialSessionBroker, self.credential_broker)
            )
            credential_use = session_broker.run(credential_request, run_adapter)
            run = credential_use.result
        except DeploymentOutcomeUnknownError as exc:
            self._fail_local_deployment(
                attempt_id,
                DeploymentAttemptStatus.UNKNOWN,
                type(exc).__name__,
                target_contacted=True,
            )
            raise
        except DeploymentVerificationError as exc:
            self._fail_local_deployment(
                attempt_id,
                DeploymentAttemptStatus.ROLLBACK_REQUIRED,
                type(exc).__name__,
                target_contacted=True,
            )
            raise
        except Exception as exc:
            self._fail_local_deployment(
                attempt_id,
                DeploymentAttemptStatus.FAILED,
                type(exc).__name__,
                target_contacted=target_contacted,
            )
            raise

        execution = run.execution
        observation = run.observation
        verification = run.verification
        with self.session_factory() as session, session.begin():
            finalized = session.get(DeploymentAttemptRecord, attempt_id, with_for_update=True)
            if finalized is None or finalized.status != DeploymentAttemptStatus.RUNNING.value:
                raise ConflictError("deployment attempt is no longer running")
            workflow = self._get_workflow(session, workflow_id, lock=True)
            finalized.operation_reference = execution.operation_reference
            finalized.result_json = {
                "credential": self._credential_handle_dict(credential_use.handle),
                "execution": execution.evidence,
                "observation": observation.evidence,
                "changed": execution.changed,
            }
            finalized.completed_at = datetime.now(UTC)
            status = (
                DeploymentAttemptStatus.SUCCEEDED
                if verification.passed and not execution.simulated
                else DeploymentAttemptStatus.ROLLBACK_REQUIRED
            )
            finalized.status = status.value
            session.add(
                DeploymentVerificationRecord(
                    workflow_id=workflow_id,
                    attempt_id=finalized.id,
                    passed=verification.passed,
                    observed_revision=verification.observed_revision,
                    evidence=verification.evidence,
                )
            )
            target_state = (
                WorkflowState.DEPLOYED
                if status is DeploymentAttemptStatus.SUCCEEDED
                else WorkflowState.ROLLBACK_REQUIRED
            )
            self._transition(
                session,
                workflow,
                target_state,
                actor_id="orchestrator",
                reason="local deployment verification completed",
                extra={"deployment_attempt_id": finalized.id},
            )
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="deployment.local_completed",
                actor_id="orchestrator",
                resource_type="deployment_attempt",
                resource_id=finalized.id,
                outcome=status.value,
                payload={
                    "changed": execution.changed,
                    "credential_reference": credential_use.handle.reference,
                    "plan_digest": plan.digest,
                    "revision": plan.revision,
                    "verification_passed": verification.passed,
                },
            )
            session.flush()
            return self._deployment_attempt_dict(finalized)

    def list_deployment_attempts(
        self, workflow_id: str, *, principal_id: str
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_DEPLOYMENT)
            self._get_workflow(session, workflow_id)
            records = session.scalars(
                select(DeploymentAttemptRecord)
                .where(DeploymentAttemptRecord.workflow_id == workflow_id)
                .order_by(DeploymentAttemptRecord.started_at)
            ).all()
            return [self._deployment_attempt_dict(item) for item in records]

    def execute_local_rollback(
        self,
        *,
        workflow_id: str,
        attempt_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not idempotency_key.strip() or len(idempotency_key) > 128:
            raise ValidationError("rollback idempotency key is invalid")
        if not self.enable_local_deployment or (
            self.credential_broker_factory is None
            and not isinstance(self.credential_broker, CredentialSessionBroker)
        ):
            raise AuthorizationError("local credentialed recovery is disabled")
        request_digest = self._digest(
            {"workflow_id": workflow_id, "attempt_id": attempt_id, "mode": "LOCAL_ROLLBACK"}
        )
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session, actor_id, Capability.EXECUTE_ROLLBACK, require_human=True
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            existing = session.scalar(
                select(DeploymentRollbackRecord).where(
                    DeploymentRollbackRecord.actor_id == actor_id,
                    DeploymentRollbackRecord.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError("rollback idempotency key was reused")
                if existing.status == RollbackStatus.SUCCEEDED.value:
                    return self._deployment_rollback_dict(existing)
                raise ConflictError("rollback attempt is not replayable")
            if WorkflowState(workflow.state) is not WorkflowState.ROLLBACK_REQUIRED:
                raise InvalidTransitionError("workflow is not awaiting controlled recovery")
            attempt = session.get(DeploymentAttemptRecord, attempt_id)
            if (
                attempt is None
                or attempt.workflow_id != workflow_id
                or attempt.status != DeploymentAttemptStatus.ROLLBACK_REQUIRED.value
            ):
                raise ConflictError("rollback target is not a contained deployment attempt")
            plan_record = session.get(DeploymentPlanRecord, attempt.plan_id)
            if plan_record is None:
                raise ConflictError("rollback plan evidence is unavailable")
            environment = session.get(DeploymentEnvironment, plan_record.environment_id)
            if (
                environment is None
                or not environment.active
                or environment.classification != EnvironmentClassification.DEVELOPMENT.value
                or environment.provider != "local-k3d"
                or environment.account_scope != "local"
                or environment.region != "local"
            ):
                raise AuthorizationError("rollback environment is not an active local target")
            adapter = self.deployment_adapters.get(environment.adapter_id)
            if adapter is None or not isinstance(adapter, RecoverableDeploymentAdapter):
                raise AuthorizationError("recoverable local deployment adapter is unavailable")
            approval = session.scalar(
                select(Approval)
                .where(
                    Approval.workflow_id == workflow_id,
                    Approval.action == ApprovalAction.ROLLBACK.value,
                    Approval.decision == ApprovalDecision.APPROVED.value,
                    Approval.environment_id == environment.id,
                    Approval.plan_digest == plan_record.digest,
                    Approval.deployment_attempt_id == attempt_id,
                    Approval.revision == plan_record.revision,
                    Approval.policy_version == plan_record.policy_version,
                    Approval.target == plan_record.rollback_reference,
                    Approval.consumed_at.is_(None),
                )
                .order_by(Approval.created_at.desc())
                .limit(1)
            )
            if approval is None:
                raise AuthorizationError("exact unconsumed rollback approval is required")
            if self._as_utc(approval.expires_at) <= datetime.now(UTC):
                raise AuthorizationError("rollback approval has expired")
            result_json = attempt.result_json
            execution_evidence = result_json.get("execution")
            previous_release = (
                execution_evidence.get("previous_release")
                if isinstance(execution_evidence, dict)
                else None
            )
            if not isinstance(previous_release, dict) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in previous_release.items()
            ):
                raise ConflictError("recorded rollback snapshot is unavailable or invalid")
            plan = self._deployment_plan_value(plan_record)
            adapter.validate_plan(plan)
            rollback = DeploymentRollbackRecord(
                workflow_id=workflow_id,
                attempt_id=attempt_id,
                approval_id=approval.id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                rollback_reference=plan.rollback_reference,
                decision=ApprovalDecision.APPROVED.value,
                rationale=approval.rationale,
                status=RollbackStatus.RUNNING.value,
                evidence={"intent_committed": True, "credential_requested": True},
            )
            session.add(rollback)
            session.flush()
            approval.consumed_at = datetime.now(UTC)
            rollback_id = rollback.id
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="deployment.rollback_started",
                actor_id=actor_id,
                resource_type="deployment_rollback",
                resource_id=rollback_id,
                outcome="STARTED",
                payload={
                    "approval_id": approval.id,
                    "deployment_attempt_id": attempt_id,
                    "environment_id": environment.id,
                    "plan_digest": plan.digest,
                    "rollback_reference": plan.rollback_reference,
                },
            )

        recoverable_adapter = adapter
        started = monotonic()
        target_contacted = False

        def run_rollback(
            credential: str, handle: WorkloadCredentialHandle
        ) -> CredentialedRollbackRun:
            nonlocal target_contacted
            target_contacted = True
            return recoverable_adapter.rollback_with_credential(
                plan,
                dict(previous_release),
                credential,
                handle,
                idempotency_key=rollback_id,
            )

        try:
            credential_request = recoverable_adapter.rollback_credential_request(plan)
            session_broker = (
                self.credential_broker_factory.for_request(credential_request)
                if self.credential_broker_factory is not None
                else cast(CredentialSessionBroker, self.credential_broker)
            )
            credential_use = session_broker.run(credential_request, run_rollback)
            run = credential_use.result
        except (DeploymentOutcomeUnknownError, DeploymentVerificationError) as exc:
            self._fail_local_rollback(
                rollback_id,
                RollbackStatus.UNKNOWN,
                type(exc).__name__,
                target_contacted=True,
                recovery_duration_ms=int((monotonic() - started) * 1000),
            )
            raise
        except Exception as exc:
            self._fail_local_rollback(
                rollback_id,
                RollbackStatus.FAILED,
                type(exc).__name__,
                target_contacted=target_contacted,
                recovery_duration_ms=int((monotonic() - started) * 1000),
            )
            raise

        duration_ms = int((monotonic() - started) * 1000)
        with self.session_factory() as session, session.begin():
            finalized = session.get(DeploymentRollbackRecord, rollback_id, with_for_update=True)
            if finalized is None or finalized.status != RollbackStatus.RUNNING.value:
                raise ConflictError("rollback attempt is no longer running")
            workflow = self._get_workflow(session, workflow_id, lock=True)
            finalized.operation_reference = run.execution.operation_reference
            finalized.completed_at = datetime.now(UTC)
            finalized.recovery_duration_ms = duration_ms
            finalized.evidence = {
                "credential": self._credential_handle_dict(credential_use.handle),
                "execution": run.execution.evidence,
                "observation": run.observation.evidence,
                "verification": run.verification.evidence,
                "changed": run.execution.changed,
            }
            recovered = run.verification.passed and not run.execution.simulated
            finalized.status = (
                RollbackStatus.SUCCEEDED.value if recovered else RollbackStatus.FAILED.value
            )
            if recovered:
                self._transition(
                    session,
                    workflow,
                    WorkflowState.ROLLED_BACK,
                    actor_id="orchestrator",
                    reason="approved local rollback independently verified recovery",
                    extra={
                        "deployment_attempt_id": attempt_id,
                        "deployment_rollback_id": rollback_id,
                        "recovery_duration_ms": duration_ms,
                    },
                )
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="deployment.rollback_completed",
                actor_id="orchestrator",
                resource_type="deployment_rollback",
                resource_id=rollback_id,
                outcome=finalized.status,
                payload={
                    "changed": run.execution.changed,
                    "deployment_attempt_id": attempt_id,
                    "recovery_duration_ms": duration_ms,
                    "verification_passed": run.verification.passed,
                },
            )
            session.flush()
            result = self._deployment_rollback_dict(finalized)
        if not recovered:
            raise DeploymentVerificationError("local rollback did not verify recovery")
        return result

    def list_deployment_rollbacks(
        self, workflow_id: str, *, principal_id: str
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_DEPLOYMENT)
            self._get_workflow(session, workflow_id)
            records = session.scalars(
                select(DeploymentRollbackRecord)
                .where(DeploymentRollbackRecord.workflow_id == workflow_id)
                .order_by(DeploymentRollbackRecord.created_at)
            ).all()
            return [self._deployment_rollback_dict(item) for item in records]

    def _fail_deployment_dry_run(self, attempt_id: str, error_code: str) -> None:
        with self.session_factory() as session, session.begin():
            attempt = session.get(DeploymentAttemptRecord, attempt_id, with_for_update=True)
            if attempt is None or attempt.status != DeploymentAttemptStatus.RUNNING.value:
                return
            attempt.status = DeploymentAttemptStatus.FAILED.value
            attempt.error_code = error_code[:64]
            attempt.completed_at = datetime.now(UTC)
            attempt.result_json = {
                "credential_requested": False,
                "external_target_contacted": False,
                "error": "dry-run adapter failed before any target change",
            }
            append_audit_event(
                session,
                workflow_id=attempt.workflow_id,
                event_type="deployment.dry_run_failed",
                actor_id="orchestrator",
                resource_type="deployment_attempt",
                resource_id=attempt.id,
                outcome="FAILED",
                payload={
                    "error_code": attempt.error_code,
                    "credential_requested": False,
                    "external_target_contacted": False,
                },
            )

    def _fail_local_deployment(
        self,
        attempt_id: str,
        status: DeploymentAttemptStatus,
        error_code: str,
        *,
        target_contacted: bool,
    ) -> None:
        if status not in {
            DeploymentAttemptStatus.FAILED,
            DeploymentAttemptStatus.UNKNOWN,
            DeploymentAttemptStatus.ROLLBACK_REQUIRED,
        }:
            raise ValueError("invalid local deployment failure status")
        with self.session_factory() as session, session.begin():
            attempt = session.get(DeploymentAttemptRecord, attempt_id, with_for_update=True)
            if attempt is None or attempt.status != DeploymentAttemptStatus.RUNNING.value:
                return
            attempt.status = status.value
            attempt.error_code = error_code[:64]
            attempt.completed_at = datetime.now(UTC)
            attempt.result_json = {
                "credential_requested": True,
                "error": "local deployment did not produce verified success",
                "target_contacted": target_contacted,
            }
            if status is DeploymentAttemptStatus.ROLLBACK_REQUIRED:
                workflow = self._get_workflow(session, attempt.workflow_id, lock=True)
                self._transition(
                    session,
                    workflow,
                    WorkflowState.ROLLBACK_REQUIRED,
                    actor_id="orchestrator",
                    reason="local deployment changed but verification was not established",
                    extra={"deployment_attempt_id": attempt.id},
                )
            append_audit_event(
                session,
                workflow_id=attempt.workflow_id,
                event_type="deployment.local_failed",
                actor_id="orchestrator",
                resource_type="deployment_attempt",
                resource_id=attempt.id,
                outcome=status.value,
                payload={
                    "error_code": attempt.error_code,
                    "target_contacted": target_contacted,
                },
            )

    def _fail_local_rollback(
        self,
        rollback_id: str,
        status: RollbackStatus,
        error_code: str,
        *,
        target_contacted: bool,
        recovery_duration_ms: int,
    ) -> None:
        if status not in {RollbackStatus.FAILED, RollbackStatus.UNKNOWN}:
            raise ValueError("invalid local rollback failure status")
        with self.session_factory() as session, session.begin():
            rollback = session.get(DeploymentRollbackRecord, rollback_id, with_for_update=True)
            if rollback is None or rollback.status != RollbackStatus.RUNNING.value:
                return
            rollback.status = status.value
            rollback.error_code = error_code[:64]
            rollback.completed_at = datetime.now(UTC)
            rollback.recovery_duration_ms = max(recovery_duration_ms, 0)
            rollback.evidence = {
                "credential_requested": True,
                "error": "local rollback did not produce verified recovery",
                "target_contacted": target_contacted,
            }
            append_audit_event(
                session,
                workflow_id=rollback.workflow_id,
                event_type="deployment.rollback_failed",
                actor_id="orchestrator",
                resource_type="deployment_rollback",
                resource_id=rollback.id,
                outcome=status.value,
                payload={
                    "error_code": rollback.error_code,
                    "recovery_duration_ms": rollback.recovery_duration_ms,
                    "target_contacted": target_contacted,
                },
            )

    @staticmethod
    def _credential_handle_dict(handle: WorkloadCredentialHandle) -> dict[str, Any]:
        return {
            "reference": handle.reference,
            "broker_id": handle.broker_id,
            "environment_id": handle.environment_id,
            "plan_digest": handle.plan_digest,
            "operation": handle.operation.value,
            "audience": handle.audience,
            "subject": handle.subject,
            "resource_scope": list(handle.resource_scope),
            "issued_at": handle.issued_at.isoformat(),
            "expires_at": handle.expires_at.isoformat(),
            "simulated": handle.simulated,
        }

    @staticmethod
    def _deployment_plan_value(record: DeploymentPlanRecord) -> DeploymentPlan:
        return DeploymentPlan(
            plan_id=record.id,
            workflow_id=record.workflow_id,
            environment_id=record.environment_id,
            revision=record.revision,
            artifact_digests=tuple(record.artifact_digests),
            operations=tuple(record.operations),
            verification_probes=tuple(record.verification_probes),
            rollback_reference=record.rollback_reference,
            policy_version=record.policy_version,
            digest=record.digest,
        )

    @staticmethod
    def _deployment_environment_dict(record: DeploymentEnvironment) -> dict[str, Any]:
        return {
            "id": record.id,
            "name": record.name,
            "classification": record.classification,
            "provider": record.provider,
            "account_scope": record.account_scope,
            "region": record.region,
            "resource_scope": record.resource_scope,
            "adapter_id": record.adapter_id,
            "policy_version": record.policy_version,
            "repository": record.repository,
            "base_branch": record.base_branch,
            "required_checks": record.required_checks,
            "required_attestations": record.required_attestations,
            "verification_policy": record.verification_policy,
            "rollback_policy": record.rollback_policy,
            "config_digest": record.config_digest,
            "active": record.active,
            "created_by": record.created_by,
            "created_at": record.created_at.isoformat(),
        }

    @staticmethod
    def _deployment_plan_dict(record: DeploymentPlanRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "workflow_id": record.workflow_id,
            "environment_id": record.environment_id,
            "revision": record.revision,
            "artifact_digests": record.artifact_digests,
            "operations": record.operations,
            "declared_impact": record.declared_impact,
            "verification_probes": record.verification_probes,
            "rollback_reference": record.rollback_reference,
            "policy_version": record.policy_version,
            "digest": record.digest,
            "created_at": record.created_at.isoformat(),
        }

    @staticmethod
    def _deployment_attempt_dict(record: DeploymentAttemptRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "workflow_id": record.workflow_id,
            "plan_id": record.plan_id,
            "approval_id": record.approval_id,
            "adapter_id": record.adapter_id,
            "status": record.status,
            "operation_reference": record.operation_reference,
            "simulated": record.simulated,
            "result": record.result_json,
            "error_code": record.error_code,
            "started_at": record.started_at.isoformat(),
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        }

    @staticmethod
    def _deployment_rollback_dict(record: DeploymentRollbackRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "workflow_id": record.workflow_id,
            "attempt_id": record.attempt_id,
            "approval_id": record.approval_id,
            "actor_id": record.actor_id,
            "rollback_reference": record.rollback_reference,
            "decision": record.decision,
            "rationale": record.rationale,
            "status": record.status,
            "operation_reference": record.operation_reference,
            "evidence": record.evidence,
            "error_code": record.error_code,
            "recovery_duration_ms": record.recovery_duration_ms,
            "created_at": record.created_at.isoformat(),
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        }

    @staticmethod
    def _is_sha256_digest(value: str) -> bool:
        suffix = value.removeprefix("sha256:")
        return (
            value.startswith("sha256:")
            and len(suffix) == 64
            and suffix == suffix.lower()
            and set(suffix) <= set("0123456789abcdef")
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _get_workflow(session: Session, workflow_id: str, *, lock: bool = False) -> Workflow:
        query = select(Workflow).where(Workflow.id == workflow_id)
        if lock:
            query = query.with_for_update()
        workflow = session.scalar(query)
        if workflow is None:
            raise NotFoundError("workflow not found")
        return workflow

    @staticmethod
    def _digest(value: Any) -> str:
        return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
