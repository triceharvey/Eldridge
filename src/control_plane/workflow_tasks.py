from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from time import monotonic
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from control_plane.audit import append_audit_event
from control_plane.deployment import DeploymentAttemptStatus
from control_plane.domain import (
    TASK_CAPABILITY,
    TASK_ROLE,
    AgentRole,
    ApprovalAction,
    ApprovalDecision,
    AuthorizationError,
    Capability,
    ConflictError,
    DispositionDecision,
    ExecutionContext,
    ExecutionResult,
    InvalidTransitionError,
    NotFoundError,
    ProviderRequest,
    ProviderResult,
    ReconciliationDecision,
    TaskKind,
    TaskStatus,
    ValidationError,
    WorkflowState,
)
from control_plane.executors import TaskExecutor
from control_plane.learning import ProviderEvidenceStore
from control_plane.leases import LeaseHeartbeat
from control_plane.persistence import (
    Approval,
    Artifact,
    CapabilityGrant,
    DeploymentAttemptRecord,
    DeploymentPlanRecord,
    ExecutionReconciliation,
    IdempotencyRecord,
    ProviderObservation,
    RoutingRecord,
    Task,
    TaskAttempt,
    Workflow,
    WorkflowDisposition,
)
from control_plane.policy import PolicyEngine
from control_plane.providers import ModelProvider
from control_plane.routing import (
    CapabilityRouter,
    DataClassification,
    EgressBoundary,
    FundingMode,
    ProviderProfile,
    RiskLevel,
    RoutingObjective,
    RoutingPurpose,
    RoutingRequest,
    WorkCapability,
)
from control_plane.state_machine import assert_transition_allowed
from control_plane.task_strategy import (
    ComplexityTier,
    InspectionSignal,
    TaskProfile,
    TaskStrategyPlanner,
)
from control_plane.validation import validate_provider_result

AGENT_FOR_ROLE: dict[AgentRole, str] = {
    AgentRole.ARCHITECT: "architect-agent",
    AgentRole.REVIEWER: "reviewer-agent",
    AgentRole.IMPLEMENTER: "implementer-agent",
    AgentRole.TEST_AGENT: "test-agent",
    AgentRole.SECURITY_AGENT: "security-agent",
}

TASK_OBJECTIVES: dict[TaskKind, str] = {
    TaskKind.PLAN: "Create a bounded engineering plan and declare assumptions.",
    TaskKind.ARCHITECTURE_REVIEW: (
        "Independently challenge the plan for security, operational, and failure risks."
    ),
    TaskKind.IMPLEMENT: "Produce the assigned change artifact without executing host commands.",
    TaskKind.TEST: "Validate the candidate revision and produce structured test evidence.",
    TaskKind.SECURITY_REVIEW: "Evaluate the candidate revision against the security policy.",
    TaskKind.CODE_REVIEW: "Perform final independent review and identify blocking findings.",
}

TASK_WORK_CAPABILITY: dict[TaskKind, WorkCapability] = {
    TaskKind.PLAN: WorkCapability.PLANNING,
    TaskKind.ARCHITECTURE_REVIEW: WorkCapability.ARCHITECTURE,
    TaskKind.IMPLEMENT: WorkCapability.CODE_GENERATION,
    TaskKind.TEST: WorkCapability.TEST_EXECUTION,
    TaskKind.SECURITY_REVIEW: WorkCapability.SECURITY_ANALYSIS,
    TaskKind.CODE_REVIEW: WorkCapability.CODE_REVIEW,
}

REVIEW_PRODUCER_KIND: dict[TaskKind, TaskKind] = {
    TaskKind.ARCHITECTURE_REVIEW: TaskKind.PLAN,
    TaskKind.SECURITY_REVIEW: TaskKind.IMPLEMENT,
    TaskKind.CODE_REVIEW: TaskKind.IMPLEMENT,
}

TASK_WORKFLOW_STATE: dict[TaskKind, WorkflowState] = {
    TaskKind.PLAN: WorkflowState.PLANNING,
    TaskKind.ARCHITECTURE_REVIEW: WorkflowState.ARCHITECTURE_REVIEW,
    TaskKind.IMPLEMENT: WorkflowState.IMPLEMENTING,
    TaskKind.TEST: WorkflowState.TESTING,
    TaskKind.SECURITY_REVIEW: WorkflowState.SECURITY_REVIEW,
    TaskKind.CODE_REVIEW: WorkflowState.CODE_REVIEW,
}


class WorkflowProviderBinding(Protocol):
    @property
    def profile(self) -> ProviderProfile: ...

    @property
    def provider(self) -> ModelProvider: ...


@dataclass(frozen=True)
class PreparedExecution:
    workflow_id: str
    task_id: str
    attempt_id: str
    lease_token: str
    worker_id: str
    agent_id: str
    task_kind: TaskKind
    binding: WorkflowProviderBinding
    profile: ProviderProfile
    request: ProviderRequest
    context: ExecutionContext


class WorkflowTaskService:
    """Workflow intake, task execution, and human decision boundary."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        policy: PolicyEngine,
        *,
        executor: TaskExecutor,
        lease_seconds: int,
        heartbeat_interval_seconds: float,
        provider_bindings: Mapping[str, WorkflowProviderBinding],
        router: CapabilityRouter,
        evidence_store: ProviderEvidenceStore,
        strategy_planner: TaskStrategyPlanner,
        allowed_egress: frozenset[EgressBoundary],
        high_risk_min_evidence_samples: int,
        provider_policy_version: str,
        routing_objective: RoutingObjective,
    ) -> None:
        self.session_factory = session_factory
        self.policy = policy
        self.executor = executor
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.provider_bindings = provider_bindings
        self.router = router
        self.evidence_store = evidence_store
        self.strategy_planner = strategy_planner
        self.allowed_egress = allowed_egress
        self.high_risk_min_evidence_samples = high_risk_min_evidence_samples
        self.provider_policy_version = provider_policy_version
        self.routing_objective = routing_objective

    def create_workflow(
        self,
        *,
        requester_id: str,
        title: str,
        description: str,
        idempotency_key: str,
        complexity: ComplexityTier = ComplexityTier.STANDARD,
        risk: RiskLevel = RiskLevel.MEDIUM,
        data_classification: DataClassification = DataClassification.INTERNAL,
        repository_scope: str | None = None,
        inspection_signals: frozenset[InspectionSignal] = frozenset(),
    ) -> dict[str, Any]:
        if not title.strip() or not description.strip() or not idempotency_key.strip():
            raise ValidationError("title, description, and idempotency key are required")
        normalized_repository = repository_scope.strip() if repository_scope else None
        if normalized_repository == "":
            normalized_repository = None
        task_profile = TaskProfile(
            complexity=complexity,
            risk=risk,
            data_classification=data_classification,
            required_capabilities=frozenset({WorkCapability.PLANNING}),
            inspection_signals=inspection_signals,
        )
        strategy = self.strategy_planner.plan(task_profile)
        request_digest = self._digest(
            {
                "title": title.strip(),
                "description": description.strip(),
                "complexity": complexity.name,
                "risk": risk.name,
                "data_classification": data_classification.name,
                "repository_scope": normalized_repository,
                "inspection_signals": sorted(signal.value for signal in inspection_signals),
            }
        )
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, requester_id, Capability.SUBMIT_WORKFLOW)
            existing = session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.principal_id == requester_id,
                    IdempotencyRecord.command == "create_workflow",
                    IdempotencyRecord.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError("idempotency key was already used for a different request")
                workflow = session.get(Workflow, existing.resource_id)
                if workflow is None:
                    raise ConflictError("idempotency record references a missing workflow")
                return self._workflow_dict(workflow)

            workflow = Workflow(
                title=title.strip(),
                description=description.strip(),
                state=WorkflowState.CREATED.value,
                risk_class=risk.name,
                complexity_tier=complexity.name,
                data_classification=data_classification.name,
                repository_scope=normalized_repository,
                inspection_signals=sorted(signal.value for signal in inspection_signals),
                containment_required=strategy.requires_human_disposition,
                block_reason=(
                    "INPUT_DISPOSITION_REQUIRED" if strategy.requires_human_disposition else None
                ),
                requester_id=requester_id,
            )
            session.add(workflow)
            session.flush()
            session.add(
                IdempotencyRecord(
                    principal_id=requester_id,
                    command="create_workflow",
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                    resource_id=workflow.id,
                )
            )
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="workflow.created",
                actor_id=requester_id,
                resource_type="workflow",
                resource_id=workflow.id,
                outcome="SUCCEEDED",
                payload={"state": WorkflowState.CREATED.value},
            )
            if strategy.requires_human_disposition:
                self._transition(
                    session,
                    workflow,
                    WorkflowState.BLOCKED,
                    actor_id="orchestrator",
                    reason="suspicious or adversarial input contained pending human disposition",
                    extra={"restrictions": sorted(item.value for item in strategy.restrictions)},
                )
            else:
                self._transition(
                    session,
                    workflow,
                    WorkflowState.PLANNING,
                    actor_id="orchestrator",
                    reason="validated request queued for planning",
                )
                self._schedule_task(session, workflow, TaskKind.PLAN)
            session.flush()
            return self._workflow_dict(workflow)

    def get_workflow(self, workflow_id: str, *, principal_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_WORKFLOW)
            workflow = self._get_workflow(session, workflow_id)
            return self._workflow_dict(workflow)

    def lease_next_task(self, *, worker_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, worker_id, Capability.LEASE_TASK)
            task = session.scalar(
                select(Task)
                .where(Task.status == TaskStatus.READY.value)
                .order_by(Task.created_at, Task.position)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if task is None:
                return None
            task.status = TaskStatus.LEASED.value
            task.lease_owner = worker_id
            task.lease_token = str(uuid4())
            task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self.lease_seconds)
            self._get_workflow(session, task.workflow_id, lock=True)
            append_audit_event(
                session,
                workflow_id=task.workflow_id,
                event_type="task.leased",
                actor_id=worker_id,
                resource_type="task",
                resource_id=task.id,
                outcome="SUCCEEDED",
                payload={"lease_expires_at": task.lease_expires_at.isoformat()},
            )
            session.flush()
            return self._task_dict(task, include_lease_token=True)

    def reclaim_expired_tasks(self, *, worker_id: str) -> int:
        now = datetime.now(UTC)
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, worker_id, Capability.LEASE_TASK)
            tasks = session.scalars(
                select(Task)
                .where(
                    Task.status.in_({TaskStatus.LEASED.value, TaskStatus.RUNNING.value}),
                    Task.lease_expires_at < now,
                )
                .with_for_update(skip_locked=True)
            ).all()
            for task in tasks:
                workflow = self._get_workflow(session, task.workflow_id, lock=True)
                prior_owner = task.lease_owner
                prior_status = TaskStatus(task.status)
                task.lease_owner = None
                task.lease_token = None
                task.lease_expires_at = None
                if prior_status is TaskStatus.LEASED:
                    task.status = TaskStatus.READY.value
                    event_type = "task.lease_reclaimed"
                    outcome = "SUCCEEDED"
                    payload = {
                        "prior_owner": prior_owner,
                        "execution_started": False,
                    }
                else:
                    task.status = TaskStatus.BLOCKED.value
                    running_attempt = session.scalar(
                        select(TaskAttempt)
                        .where(
                            TaskAttempt.task_id == task.id,
                            TaskAttempt.status == TaskStatus.RUNNING.value,
                        )
                        .order_by(TaskAttempt.attempt_number.desc())
                        .limit(1)
                    )
                    if running_attempt is not None:
                        running_attempt.status = TaskStatus.TIMED_OUT.value
                        running_attempt.error_code = "LeaseExpired"
                        running_attempt.output = running_attempt.output | {
                            "reconciliation": {"error": "execution outcome requires reconciliation"}
                        }
                        running_attempt.completed_at = now
                    self._deactivate_task_grant(session, task.id)
                    workflow.block_reason = "EXECUTION_RECONCILIATION_REQUIRED"
                    self._transition(
                        session,
                        workflow,
                        WorkflowState.BLOCKED,
                        actor_id=worker_id,
                        reason="running task lease expired with unknown external outcome",
                        extra={
                            "task_id": task.id,
                            "attempt_id": getattr(running_attempt, "id", None),
                        },
                    )
                    event_type = "task.reconciliation_required"
                    outcome = "BLOCKED"
                    payload = {
                        "prior_owner": prior_owner,
                        "execution_started": True,
                        "attempt_id": getattr(running_attempt, "id", None),
                    }
                append_audit_event(
                    session,
                    workflow_id=task.workflow_id,
                    event_type=event_type,
                    actor_id=worker_id,
                    resource_type="task",
                    resource_id=task.id,
                    outcome=outcome,
                    payload=payload,
                )
            return len(tasks)

    def execute_leased_task(
        self, *, task_id: str, lease_token: str, worker_id: str
    ) -> dict[str, Any]:
        prepared = self._prepare_execution(task_id, lease_token, worker_id)
        if isinstance(prepared, dict):
            return prepared
        started = monotonic()
        validation_passed = False
        with LeaseHeartbeat(
            lambda: self.heartbeat_task(
                task_id=prepared.task_id,
                lease_token=prepared.lease_token,
                worker_id=prepared.worker_id,
            ),
            interval_seconds=self.heartbeat_interval_seconds,
        ):
            try:
                provider_result = prepared.binding.provider.submit(prepared.request)
                if provider_result.model != prepared.profile.model_version:
                    raise ValidationError("provider model does not match routed model version")
                validate_provider_result(prepared.task_kind, provider_result)
                validation_passed = True
                execution = self.executor.execute(
                    prepared.task_kind,
                    provider_result,
                    prepared.context,
                )
                candidate_revision: str | None = None
                if prepared.task_kind is TaskKind.IMPLEMENT:
                    candidate = execution.evidence.get(
                        "result_revision", provider_result.output.get("candidate_revision")
                    )
                    if not isinstance(candidate, str) or not candidate:
                        raise ValidationError("implementation result lacks candidate revision")
                    candidate_revision = candidate
            except Exception as exc:
                return self._finalize_execution_failure(
                    prepared,
                    exc,
                    validation_passed=validation_passed,
                    latency_ms=int((monotonic() - started) * 1000),
                )
        return self._finalize_execution_success(
            prepared,
            provider_result,
            execution,
            candidate_revision=candidate_revision,
            latency_ms=int((monotonic() - started) * 1000),
        )

    def _prepare_execution(
        self, task_id: str, lease_token: str, worker_id: str
    ) -> PreparedExecution | dict[str, Any]:
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, worker_id, Capability.LEASE_TASK)
            task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
            if task is None:
                raise NotFoundError("task not found")
            self._validate_lease(task, lease_token, worker_id)
            workflow = self._get_workflow(session, task.workflow_id, lock=True)
            task.status = TaskStatus.RUNNING.value
            attempt_number = len(task.attempts) + 1
            role = AgentRole(task.required_role)
            agent_id = AGENT_FOR_ROLE[role]
            attempt = TaskAttempt(
                task=task,
                attempt_number=attempt_number,
                agent_id=agent_id,
                provider="pending-routing",
                model="pending",
                status=TaskStatus.RUNNING.value,
            )
            session.add(attempt)
            session.flush()
            self.policy.authorize(
                session,
                agent_id,
                Capability(task.required_capability),
                workflow_id=workflow.id,
                task_id=task.id,
            )
            routed = self._route_attempt(session, workflow, task, attempt)
            if routed is None:
                return self._record_routing_block(session, workflow, task, attempt)
            binding, profile = routed
            if profile.funding_mode is FundingMode.SUBSCRIPTION:
                prior_invocations = session.scalar(
                    select(func.count(TaskAttempt.id))
                    .join(Task, TaskAttempt.task_id == Task.id)
                    .where(
                        Task.workflow_id == workflow.id,
                        TaskAttempt.provider == profile.provider_id,
                    )
                )
                if (
                    profile.max_invocations_per_workflow <= 0
                    or int(prior_invocations or 0) >= profile.max_invocations_per_workflow
                ):
                    raise ConflictError("subscription invocation ceiling exhausted for workflow")
            attempt.provider = profile.provider_id
            attempt.model = profile.model_version
            producer_context: dict[str, Any] = {}
            task_kind = TaskKind(task.kind)
            if task_kind in REVIEW_PRODUCER_KIND:
                producer_attempt = session.scalar(
                    select(TaskAttempt)
                    .join(Task, TaskAttempt.task_id == Task.id)
                    .where(
                        Task.workflow_id == workflow.id,
                        Task.kind == REVIEW_PRODUCER_KIND[task_kind].value,
                        TaskAttempt.status == TaskStatus.SUCCEEDED.value,
                    )
                    .order_by(TaskAttempt.completed_at.desc())
                    .limit(1)
                )
                if producer_attempt is None:
                    raise ConflictError("review source output is unavailable")
                canonical_producer_output = json.dumps(
                    producer_attempt.output, sort_keys=True, separators=(",", ":")
                )
                if len(canonical_producer_output.encode()) > 1_048_576:
                    raise ValidationError("review source output exceeds one MiB")
                producer_context = {
                    "producer_output": producer_attempt.output,
                    "producer_output_digest": "sha256:"
                    + sha256(canonical_producer_output.encode()).hexdigest(),
                }
            request = ProviderRequest(
                run_id=attempt.id,
                workflow_id=workflow.id,
                task_id=task.id,
                task_kind=task_kind,
                role=role,
                objective=task.objective,
                context={
                    "workflow_title": workflow.title,
                    "workflow_description": workflow.description,
                    "candidate_revision": workflow.candidate_revision,
                    "content_trust": "UNTRUSTED_REPOSITORY_CONTEXT",
                    "data_classification": workflow.data_classification,
                    "repository_scope": workflow.repository_scope,
                }
                | producer_context,
                required_capability=Capability(task.required_capability),
                idempotency_key=attempt.id,
            )
            context = ExecutionContext(
                workflow_id=workflow.id,
                task_id=task.id,
                repository_scope=workflow.repository_scope,
                candidate_revision=workflow.candidate_revision,
                containment_required=workflow.containment_required,
            )
            task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self.lease_seconds)
            session.flush()
            return PreparedExecution(
                workflow_id=workflow.id,
                task_id=task.id,
                attempt_id=attempt.id,
                lease_token=lease_token,
                worker_id=worker_id,
                agent_id=agent_id,
                task_kind=task_kind,
                binding=binding,
                profile=profile,
                request=request,
                context=context,
            )

    def heartbeat_task(self, *, task_id: str, lease_token: str, worker_id: str) -> None:
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, worker_id, Capability.LEASE_TASK)
            task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
            if task is None:
                raise NotFoundError("task not found")
            self._validate_running_lease(task, lease_token, worker_id)
            task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self.lease_seconds)
            self._get_workflow(session, task.workflow_id, lock=True)
            append_audit_event(
                session,
                workflow_id=task.workflow_id,
                event_type="task.lease_heartbeat",
                actor_id=worker_id,
                resource_type="task",
                resource_id=task.id,
                outcome="SUCCEEDED",
                payload={"lease_expires_at": task.lease_expires_at.isoformat()},
            )

    def _finalize_execution_success(
        self,
        prepared: PreparedExecution,
        provider_result: ProviderResult,
        execution: ExecutionResult,
        *,
        candidate_revision: str | None,
        latency_ms: int,
    ) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            task = session.scalar(select(Task).where(Task.id == prepared.task_id).with_for_update())
            attempt = session.get(TaskAttempt, prepared.attempt_id)
            if task is None or attempt is None:
                raise NotFoundError("running task or attempt not found")
            self._validate_running_lease(task, prepared.lease_token, prepared.worker_id)
            if attempt.status != TaskStatus.RUNNING.value:
                raise ConflictError("task attempt is no longer running")
            workflow = self._get_workflow(session, prepared.workflow_id, lock=True)
            attempt.model = provider_result.model
            attempt.output = provider_result.output | {"execution_evidence": execution.evidence}
            attempt.status = TaskStatus.SUCCEEDED.value
            attempt.completed_at = datetime.now(UTC)
            task.status = TaskStatus.SUCCEEDED.value
            task.lease_owner = None
            task.lease_token = None
            task.lease_expires_at = None
            self._deactivate_task_grant(session, task.id)
            self._record_provider_observation(
                session,
                workflow,
                task,
                attempt,
                prepared.profile,
                succeeded=True,
                validation_passed=True,
                latency_ms=latency_ms,
                error_code=None,
            )
            artifact_digest = self._digest(attempt.output)
            if prepared.task_kind is TaskKind.IMPLEMENT:
                workflow.candidate_revision = candidate_revision
            session.add(
                Artifact(
                    workflow_id=workflow.id,
                    task_id=task.id,
                    attempt_id=attempt.id,
                    artifact_type=f"{task.kind}_EVIDENCE",
                    digest=artifact_digest,
                    revision=workflow.candidate_revision,
                    metadata_json={
                        "provider": prepared.profile.provider_id,
                        "provider_reported": provider_result.provider,
                        "model": provider_result.model,
                        "commands_executed": list(execution.commands_executed),
                    },
                )
            )
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="task.succeeded",
                actor_id=prepared.agent_id,
                resource_type="task",
                resource_id=task.id,
                outcome="SUCCEEDED",
                payload={
                    "attempt_id": attempt.id,
                    "artifact_digest": artifact_digest,
                    "candidate_revision": workflow.candidate_revision,
                    "commands_executed": list(execution.commands_executed),
                },
            )
            self._advance_after_task(session, workflow, prepared.task_kind)
            session.flush()
            return self._task_dict(task)

    def _finalize_execution_failure(
        self,
        prepared: PreparedExecution,
        exc: Exception,
        *,
        validation_passed: bool,
        latency_ms: int,
    ) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            task = session.scalar(select(Task).where(Task.id == prepared.task_id).with_for_update())
            attempt = session.get(TaskAttempt, prepared.attempt_id)
            if task is None or attempt is None:
                raise NotFoundError("running task or attempt not found")
            self._validate_running_lease(task, prepared.lease_token, prepared.worker_id)
            if attempt.status != TaskStatus.RUNNING.value:
                raise ConflictError("task attempt is no longer running")
            workflow = self._get_workflow(session, prepared.workflow_id, lock=True)
            self._record_provider_observation(
                session,
                workflow,
                task,
                attempt,
                prepared.profile,
                succeeded=False,
                validation_passed=validation_passed,
                latency_ms=latency_ms,
                error_code=type(exc).__name__,
            )
            return self._record_attempt_failure(
                session,
                workflow,
                task,
                attempt,
                exc,
                worker_id=prepared.worker_id,
            )

    def disposition_workflow(
        self,
        *,
        workflow_id: str,
        actor_id: str,
        decision: DispositionDecision,
        rationale: str,
    ) -> dict[str, Any]:
        if not rationale.strip():
            raise ValidationError("disposition rationale is required")
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session,
                actor_id,
                Capability.DISPOSITION_WORKFLOW,
                require_human=True,
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            if WorkflowState(workflow.state) is not WorkflowState.BLOCKED:
                raise ConflictError("workflow is not blocked")
            if workflow.block_reason != "INPUT_DISPOSITION_REQUIRED":
                raise ConflictError("workflow block is not eligible for input disposition")
            existing = session.scalar(
                select(WorkflowDisposition).where(WorkflowDisposition.workflow_id == workflow.id)
            )
            if existing is not None:
                raise ConflictError("workflow already has an input disposition")
            disposition = WorkflowDisposition(
                workflow_id=workflow.id,
                actor_id=actor_id,
                decision=decision.value,
                original_signals=list(workflow.inspection_signals),
                rationale=rationale.strip(),
            )
            session.add(disposition)
            session.flush()
            if decision is DispositionDecision.REJECT:
                workflow.block_reason = "INPUT_REJECTED"
                self._transition(
                    session,
                    workflow,
                    WorkflowState.REJECTED,
                    actor_id=actor_id,
                    reason="human rejected contained input",
                    extra={"disposition_id": disposition.id},
                )
            else:
                workflow.block_reason = None
                workflow.containment_required = True
                self._transition(
                    session,
                    workflow,
                    WorkflowState.PLANNING,
                    actor_id=actor_id,
                    reason="human authorized contained planning",
                    extra={"disposition_id": disposition.id},
                )
                self._schedule_task(session, workflow, TaskKind.PLAN)
            session.flush()
            return self._workflow_dict(workflow)

    def reconcile_execution(
        self,
        *,
        workflow_id: str,
        task_id: str,
        actor_id: str,
        decision: ReconciliationDecision,
        rationale: str,
    ) -> dict[str, Any]:
        if not rationale.strip():
            raise ValidationError("reconciliation rationale is required")
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session,
                actor_id,
                Capability.RECONCILE_EXECUTION,
                require_human=True,
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            if (
                WorkflowState(workflow.state) is not WorkflowState.BLOCKED
                or workflow.block_reason != "EXECUTION_RECONCILIATION_REQUIRED"
            ):
                raise ConflictError("workflow is not awaiting execution reconciliation")
            task = session.scalar(
                select(Task)
                .where(Task.id == task_id, Task.workflow_id == workflow.id)
                .with_for_update()
            )
            if task is None or task.status != TaskStatus.BLOCKED.value:
                raise ConflictError("task is not awaiting execution reconciliation")
            attempt = session.scalar(
                select(TaskAttempt)
                .where(
                    TaskAttempt.task_id == task.id,
                    TaskAttempt.status == TaskStatus.TIMED_OUT.value,
                    TaskAttempt.error_code == "LeaseExpired",
                )
                .order_by(TaskAttempt.attempt_number.desc())
                .limit(1)
            )
            if attempt is None:
                raise ConflictError("task has no abandoned attempt to reconcile")
            if session.scalar(
                select(ExecutionReconciliation).where(
                    ExecutionReconciliation.attempt_id == attempt.id
                )
            ):
                raise ConflictError("execution attempt was already reconciled")
            record = ExecutionReconciliation(
                workflow_id=workflow.id,
                task_id=task.id,
                attempt_id=attempt.id,
                actor_id=actor_id,
                decision=decision.value,
                rationale=rationale.strip(),
            )
            session.add(record)
            session.flush()
            task.status = TaskStatus.RECONCILED.value
            if decision is ReconciliationDecision.RETRY:
                workflow.block_reason = None
                resume_state = TASK_WORKFLOW_STATE[TaskKind(task.kind)]
                self._transition(
                    session,
                    workflow,
                    resume_state,
                    actor_id=actor_id,
                    reason="human reconciled abandoned execution for retry",
                    extra={"reconciliation_id": record.id, "abandoned_attempt_id": attempt.id},
                )
                self._schedule_task(session, workflow, TaskKind(task.kind))
            else:
                workflow.block_reason = "EXECUTION_RECONCILED_FAILED"
                self._transition(
                    session,
                    workflow,
                    WorkflowState.FAILED,
                    actor_id=actor_id,
                    reason="human reconciled abandoned execution as failed",
                    extra={"reconciliation_id": record.id, "abandoned_attempt_id": attempt.id},
                )
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="execution.reconciled",
                actor_id=actor_id,
                resource_type="task_attempt",
                resource_id=attempt.id,
                outcome=decision.value,
                payload={
                    "reconciliation_id": record.id,
                    "task_id": task.id,
                    "decision": decision.value,
                },
            )
            session.flush()
            return self._workflow_dict(workflow)

    def _route_attempt(
        self,
        session: Session,
        workflow: Workflow,
        task: Task,
        attempt: TaskAttempt,
    ) -> tuple[WorkflowProviderBinding, ProviderProfile] | None:
        kind = TaskKind(task.kind)
        producer_id: str | None = None
        producer_family: str | None = None
        purpose = RoutingPurpose.PRODUCE
        if kind in REVIEW_PRODUCER_KIND:
            purpose = RoutingPurpose.REVIEW
            producer_route = session.scalar(
                select(RoutingRecord)
                .join(Task, RoutingRecord.task_id == Task.id)
                .where(
                    Task.workflow_id == workflow.id,
                    Task.kind == REVIEW_PRODUCER_KIND[kind].value,
                    RoutingRecord.selected_provider_id.is_not(None),
                )
                .order_by(RoutingRecord.created_at.desc())
                .limit(1)
            )
            if producer_route is not None:
                producer_id = producer_route.selected_provider_id
                producer_family = producer_route.selected_provider_family

        profiles_list: list[ProviderProfile] = []
        exhausted_subscriptions: set[str] = set()
        for binding in self.provider_bindings.values():
            profile = binding.profile
            healthy = profile.enabled and self._provider_is_healthy(binding.provider)
            if healthy and profile.funding_mode is FundingMode.SUBSCRIPTION:
                prior_invocations = session.scalar(
                    select(func.count(TaskAttempt.id))
                    .join(Task, TaskAttempt.task_id == Task.id)
                    .where(
                        Task.workflow_id == workflow.id,
                        TaskAttempt.provider == profile.provider_id,
                    )
                )
                healthy = (
                    profile.max_invocations_per_workflow > 0
                    and int(prior_invocations or 0) < profile.max_invocations_per_workflow
                )
                if not healthy:
                    exhausted_subscriptions.add(profile.provider_id)
            profiles_list.append(replace(profile, healthy=healthy))
        profiles = tuple(profiles_list)
        profiles = self.evidence_store.hydrate_profiles(session, profiles)
        risk = RiskLevel[workflow.risk_class]
        routing_request = RoutingRequest(
            required_capabilities=frozenset({WorkCapability(task.work_capability)}),
            data_classification=DataClassification[workflow.data_classification],
            risk=risk,
            purpose=purpose,
            allowed_egress=self.allowed_egress,
            minimum_evidence_samples=(
                self.high_risk_min_evidence_samples if risk >= RiskLevel.HIGH else 0
            ),
            producer_provider_id=producer_id,
            producer_family=producer_family,
            objective=self.routing_objective,
        )
        decision = self.router.route(routing_request, profiles)
        selected_profile = next(
            (
                profile
                for profile in profiles
                if profile.provider_id == decision.selected_provider_id
            ),
            None,
        )
        rejected_candidates = {
            provider_id: (
                ["subscription_invocation_ceiling_exhausted"]
                if provider_id in exhausted_subscriptions
                else list(reasons)
            )
            for provider_id, reasons in decision.rejected.items()
        }
        session.add(
            RoutingRecord(
                workflow_id=workflow.id,
                task_id=task.id,
                attempt_id=attempt.id,
                policy_version=decision.policy_version,
                request_json={
                    "provider_policy_version": self.provider_policy_version,
                    "required_capabilities": sorted(
                        item.value for item in routing_request.required_capabilities
                    ),
                    "data_classification": routing_request.data_classification.name,
                    "risk": routing_request.risk.name,
                    "purpose": routing_request.purpose.value,
                    "allowed_egress": sorted(item.value for item in routing_request.allowed_egress),
                    "minimum_evidence_samples": routing_request.minimum_evidence_samples,
                    "producer_provider_id": producer_id,
                    "producer_family": producer_family,
                    "objective": decision.objective.value,
                    "objective_profile_version": decision.objective_profile_version,
                },
                ranked_candidates=[
                    {
                        "provider_id": item.provider_id,
                        "score": item.score,
                        "quality_utility": item.quality_utility,
                        "cost_utility": item.cost_utility,
                        "latency_utility": item.latency_utility,
                        "funding_mode": next(
                            profile.funding_mode.value
                            for profile in profiles
                            if profile.provider_id == item.provider_id
                        ),
                        "max_invocations_per_workflow": next(
                            profile.max_invocations_per_workflow
                            for profile in profiles
                            if profile.provider_id == item.provider_id
                        ),
                    }
                    for item in decision.ranked_candidates
                ],
                rejected_candidates=rejected_candidates,
                selected_provider_id=decision.selected_provider_id,
                selected_provider_family=(
                    selected_profile.provider_family if selected_profile else None
                ),
                selected_model_version=(
                    selected_profile.model_version if selected_profile else None
                ),
            )
        )
        append_audit_event(
            session,
            workflow_id=workflow.id,
            event_type="task.routed" if selected_profile else "task.routing_blocked",
            actor_id="orchestrator",
            resource_type="task",
            resource_id=task.id,
            outcome="SUCCEEDED" if selected_profile else "BLOCKED",
            payload={
                "attempt_id": attempt.id,
                "policy_version": decision.policy_version,
                "provider_policy_version": self.provider_policy_version,
                "selected_provider_id": decision.selected_provider_id,
                "rejected": rejected_candidates,
            },
        )
        if selected_profile is None:
            return None
        return self.provider_bindings[selected_profile.provider_id], selected_profile

    @staticmethod
    def _provider_is_healthy(provider: ModelProvider) -> bool:
        try:
            return provider.health()
        except Exception:
            return False

    def _record_routing_block(
        self,
        session: Session,
        workflow: Workflow,
        task: Task,
        attempt: TaskAttempt,
    ) -> dict[str, Any]:
        attempt.status = TaskStatus.FAILED.value
        attempt.provider = "none"
        attempt.model = "none"
        attempt.error_code = "RoutingBlocked"
        attempt.output = {"error": "no policy-eligible provider"}
        attempt.completed_at = datetime.now(UTC)
        task.status = TaskStatus.BLOCKED.value
        task.lease_owner = None
        task.lease_token = None
        task.lease_expires_at = None
        self._deactivate_task_grant(session, task.id)
        workflow.block_reason = "NO_ELIGIBLE_PROVIDER"
        self._transition(
            session,
            workflow,
            WorkflowState.BLOCKED,
            actor_id="orchestrator",
            reason="no policy-eligible provider for task",
            extra={"attempt_id": attempt.id},
        )
        session.flush()
        return self._task_dict(task)

    @staticmethod
    def _record_provider_observation(
        session: Session,
        workflow: Workflow,
        task: Task,
        attempt: TaskAttempt,
        profile: ProviderProfile,
        *,
        succeeded: bool,
        validation_passed: bool,
        latency_ms: int,
        error_code: str | None,
    ) -> None:
        session.add(
            ProviderObservation(
                workflow_id=workflow.id,
                task_id=task.id,
                attempt_id=attempt.id,
                provider_id=profile.provider_id,
                provider_family=profile.provider_family,
                model_version=profile.model_version,
                profile_version=profile.profile_version,
                work_capability=task.work_capability,
                succeeded=succeeded,
                validation_passed=validation_passed,
                latency_ms=max(latency_ms, 0),
                error_code=error_code,
            )
        )

    def approve(
        self,
        *,
        workflow_id: str,
        approver_id: str,
        action: ApprovalAction,
        target: str,
        revision: str,
        decision: ApprovalDecision,
        rationale: str,
        expires_in_minutes: int = 15,
        environment_id: str | None = None,
        plan_digest: str | None = None,
        deployment_attempt_id: str | None = None,
    ) -> dict[str, Any]:
        capability = {
            ApprovalAction.MERGE: Capability.APPROVE_MERGE,
            ApprovalAction.DEPLOY: Capability.APPROVE_DEPLOYMENT,
            ApprovalAction.ROLLBACK: Capability.APPROVE_ROLLBACK,
        }[action]
        if expires_in_minutes < 1:
            raise ValidationError("approval expiry must be at least one minute")
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, approver_id, capability, require_human=True)
            workflow = self._get_workflow(session, workflow_id, lock=True)
            if not target.strip() or not rationale.strip():
                raise ValidationError("approval target and rationale are required")
            if action is ApprovalAction.MERGE:
                if (
                    environment_id is not None
                    or plan_digest is not None
                    or deployment_attempt_id is not None
                ):
                    raise ValidationError("merge approval cannot include deployment bindings")
                if WorkflowState(workflow.state) != WorkflowState.AWAITING_HUMAN_APPROVAL:
                    raise InvalidTransitionError("workflow is not awaiting merge approval")
                if not workflow.candidate_revision or revision != workflow.candidate_revision:
                    raise ConflictError("approval revision does not match the candidate revision")
                policy_version = workflow.policy_version
            elif action is ApprovalAction.DEPLOY:
                if WorkflowState(workflow.state) != WorkflowState.AWAITING_DEPLOYMENT_APPROVAL:
                    raise InvalidTransitionError("workflow is not awaiting deployment approval")
                if deployment_attempt_id is not None:
                    raise ValidationError("deployment approval cannot bind a prior attempt")
                if not environment_id or not plan_digest:
                    raise ValidationError(
                        "deployment approval requires environment and plan digest"
                    )
                plan = session.scalar(
                    select(DeploymentPlanRecord).where(
                        DeploymentPlanRecord.workflow_id == workflow_id,
                        DeploymentPlanRecord.environment_id == environment_id,
                        DeploymentPlanRecord.digest == plan_digest,
                    )
                )
                if plan is None:
                    raise ConflictError("deployment approval does not match an immutable plan")
                if target.strip() != environment_id:
                    raise ConflictError("deployment target must equal the environment ID")
                if revision != plan.revision or revision != workflow.merged_revision:
                    raise ConflictError("deployment approval revision is stale")
                policy_version = plan.policy_version
            else:
                if WorkflowState(workflow.state) is not WorkflowState.ROLLBACK_REQUIRED:
                    raise InvalidTransitionError("workflow is not awaiting rollback disposition")
                if not environment_id or not plan_digest or not deployment_attempt_id:
                    raise ValidationError(
                        "rollback approval requires environment, plan, and attempt bindings"
                    )
                attempt = session.get(DeploymentAttemptRecord, deployment_attempt_id)
                if (
                    attempt is None
                    or attempt.workflow_id != workflow_id
                    or attempt.status != DeploymentAttemptStatus.ROLLBACK_REQUIRED.value
                ):
                    raise ConflictError("rollback approval does not match a contained attempt")
                plan = session.get(DeploymentPlanRecord, attempt.plan_id)
                if (
                    plan is None
                    or plan.environment_id != environment_id
                    or plan.digest != plan_digest
                ):
                    raise ConflictError("rollback approval does not match the failed plan")
                if target.strip() != plan.rollback_reference:
                    raise ConflictError(
                        "rollback target must equal the recorded rollback reference"
                    )
                if revision != plan.revision or revision != workflow.merged_revision:
                    raise ConflictError("rollback approval revision is stale")
                policy_version = plan.policy_version
            approval = Approval(
                workflow_id=workflow.id,
                action=action.value,
                target=target.strip(),
                revision=revision,
                policy_version=policy_version,
                environment_id=environment_id,
                plan_digest=plan_digest,
                deployment_attempt_id=deployment_attempt_id,
                approver_id=approver_id,
                decision=decision.value,
                rationale=rationale.strip(),
                expires_at=datetime.now(UTC) + timedelta(minutes=expires_in_minutes),
            )
            session.add(approval)
            session.flush()
            if action is ApprovalAction.MERGE or (
                action is ApprovalAction.DEPLOY and decision is ApprovalDecision.REJECTED
            ):
                approval.consumed_at = datetime.now(UTC)
                target_state = (
                    WorkflowState.APPROVED
                    if decision is ApprovalDecision.APPROVED
                    else WorkflowState.REJECTED
                )
                self._transition(
                    session,
                    workflow,
                    target_state,
                    actor_id=approver_id,
                    reason=(
                        f"human {decision.value.lower()} {action.value.lower()} for exact revision"
                    ),
                    extra={"approval_id": approval.id, "revision": revision, "target": target},
                )
            elif action is ApprovalAction.DEPLOY:
                append_audit_event(
                    session,
                    workflow_id=workflow.id,
                    event_type="deployment.approved",
                    actor_id=approver_id,
                    resource_type="approval",
                    resource_id=approval.id,
                    outcome="APPROVED",
                    payload={
                        "environment_id": environment_id,
                        "plan_digest": plan_digest,
                        "revision": revision,
                        "policy_version": policy_version,
                        "expires_at": approval.expires_at.isoformat(),
                    },
                )
            else:
                if decision is ApprovalDecision.REJECTED:
                    approval.consumed_at = datetime.now(UTC)
                append_audit_event(
                    session,
                    workflow_id=workflow.id,
                    event_type=(
                        "deployment.rollback_approved"
                        if decision is ApprovalDecision.APPROVED
                        else "deployment.rollback_rejected"
                    ),
                    actor_id=approver_id,
                    resource_type="approval",
                    resource_id=approval.id,
                    outcome=decision.value,
                    payload={
                        "deployment_attempt_id": deployment_attempt_id,
                        "environment_id": environment_id,
                        "plan_digest": plan_digest,
                        "revision": revision,
                        "rollback_reference": target.strip(),
                        "expires_at": approval.expires_at.isoformat(),
                    },
                )
            session.flush()
            return self._approval_dict(approval)

    def cancel_workflow(self, workflow_id: str, *, principal_id: str) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, principal_id, Capability.CANCEL_WORKFLOW)
            workflow = self._get_workflow(session, workflow_id, lock=True)
            self._transition(
                session,
                workflow,
                WorkflowState.CANCELLED,
                actor_id=principal_id,
                reason="human cancellation requested",
            )
            for task in workflow.tasks:
                if task.status in {
                    TaskStatus.PENDING.value,
                    TaskStatus.READY.value,
                    TaskStatus.LEASED.value,
                    TaskStatus.RUNNING.value,
                }:
                    task.status = TaskStatus.CANCELLED.value
                    self._deactivate_task_grant(session, task.id)
            session.flush()
            return self._workflow_dict(workflow)

    def _schedule_task(self, session: Session, workflow: Workflow, kind: TaskKind) -> Task:
        role = TASK_ROLE[kind]
        agent_id = AGENT_FOR_ROLE[role]
        task = Task(
            workflow=workflow,
            kind=kind.value,
            required_role=role.value,
            required_capability=TASK_CAPABILITY[kind].value,
            work_capability=TASK_WORK_CAPABILITY[kind].value,
            objective=TASK_OBJECTIVES[kind],
            status=TaskStatus.READY.value,
            position=len(workflow.tasks) + 1,
        )
        session.add(task)
        session.flush()
        session.add(
            CapabilityGrant(
                principal_id=agent_id,
                workflow_id=workflow.id,
                task_id=task.id,
                capabilities=[TASK_CAPABILITY[kind].value],
                policy_version=workflow.policy_version,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        append_audit_event(
            session,
            workflow_id=workflow.id,
            event_type="task.scheduled",
            actor_id="orchestrator",
            resource_type="task",
            resource_id=task.id,
            outcome="SUCCEEDED",
            payload={"kind": kind.value, "role": role.value, "agent_id": agent_id},
        )
        return task

    def _advance_after_task(
        self, session: Session, workflow: Workflow, completed_kind: TaskKind
    ) -> None:
        if completed_kind == TaskKind.PLAN:
            self._transition(
                session,
                workflow,
                WorkflowState.ARCHITECTURE_REVIEW,
                actor_id="orchestrator",
                reason="plan evidence recorded",
            )
            self._schedule_task(session, workflow, TaskKind.ARCHITECTURE_REVIEW)
        elif completed_kind == TaskKind.ARCHITECTURE_REVIEW:
            self._transition(
                session,
                workflow,
                WorkflowState.APPROVED_FOR_IMPLEMENTATION,
                actor_id="orchestrator",
                reason="independent critique completed with no blocking findings",
            )
            self._transition(
                session,
                workflow,
                WorkflowState.IMPLEMENTING,
                actor_id="orchestrator",
                reason="implementation task scoped and granted",
            )
            self._schedule_task(session, workflow, TaskKind.IMPLEMENT)
        elif completed_kind == TaskKind.IMPLEMENT:
            self._transition(
                session,
                workflow,
                WorkflowState.TESTING,
                actor_id="orchestrator",
                reason="candidate revision and change evidence recorded",
            )
            self._schedule_task(session, workflow, TaskKind.TEST)
        elif completed_kind == TaskKind.TEST:
            self._require_candidate_revision(workflow)
            self._transition(
                session,
                workflow,
                WorkflowState.SECURITY_REVIEW,
                actor_id="orchestrator",
                reason="test evidence passed for candidate revision",
            )
            self._schedule_task(session, workflow, TaskKind.SECURITY_REVIEW)
        elif completed_kind == TaskKind.SECURITY_REVIEW:
            self._require_candidate_revision(workflow)
            self._transition(
                session,
                workflow,
                WorkflowState.CODE_REVIEW,
                actor_id="orchestrator",
                reason="security evidence passed for candidate revision",
            )
            self._schedule_task(session, workflow, TaskKind.CODE_REVIEW)
        elif completed_kind == TaskKind.CODE_REVIEW:
            self._require_candidate_revision(workflow)
            self._transition(
                session,
                workflow,
                WorkflowState.AWAITING_HUMAN_APPROVAL,
                actor_id="orchestrator",
                reason="all deterministic evidence recorded for one revision",
            )

    def _transition(
        self,
        session: Session,
        workflow: Workflow,
        target: WorkflowState,
        *,
        actor_id: str,
        reason: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        current = WorkflowState(workflow.state)
        assert_transition_allowed(current, target)
        workflow.state = target.value
        workflow.version += 1
        payload: dict[str, Any] = {
            "from_state": current.value,
            "to_state": target.value,
            "reason": reason,
            "workflow_version": workflow.version,
        }
        if extra:
            payload.update(extra)
        append_audit_event(
            session,
            workflow_id=workflow.id,
            event_type="workflow.transitioned",
            actor_id=actor_id,
            resource_type="workflow",
            resource_id=workflow.id,
            outcome="SUCCEEDED",
            payload=payload,
        )

    def _record_attempt_failure(
        self,
        session: Session,
        workflow: Workflow,
        task: Task,
        attempt: TaskAttempt,
        exc: Exception,
        *,
        worker_id: str,
    ) -> dict[str, Any]:
        attempt.status = TaskStatus.FAILED.value
        if attempt.model == "pending":
            attempt.model = "unknown"
        attempt.error_code = type(exc).__name__
        attempt.output = {"error": "provider or executor operation failed"}
        attempt.completed_at = datetime.now(UTC)
        retrying = attempt.attempt_number < task.max_attempts
        task.status = TaskStatus.READY.value if retrying else TaskStatus.FAILED.value
        task.lease_owner = None
        task.lease_token = None
        task.lease_expires_at = None
        if not retrying:
            self._deactivate_task_grant(session, task.id)
        append_audit_event(
            session,
            workflow_id=workflow.id,
            event_type="task.retry_scheduled" if retrying else "task.failed",
            actor_id=worker_id,
            resource_type="task",
            resource_id=task.id,
            outcome="FAILED",
            payload={
                "attempt_id": attempt.id,
                "attempt_number": attempt.attempt_number,
                "error_code": type(exc).__name__,
                "retrying": retrying,
            },
        )
        if not retrying:
            self._transition(
                session,
                workflow,
                WorkflowState.FAILED,
                actor_id="orchestrator",
                reason="task retry limit exhausted",
            )
        session.flush()
        return self._task_dict(task)

    @staticmethod
    def _validate_lease(task: Task, lease_token: str, worker_id: str) -> None:
        if task.status != TaskStatus.LEASED.value:
            raise ConflictError("task does not have an active lease")
        if task.lease_owner != worker_id or task.lease_token != lease_token:
            raise AuthorizationError("lease owner or token does not match")
        if task.lease_expires_at is None:
            raise ConflictError("task lease has no expiry")
        expires_at = task.lease_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            raise ConflictError("task lease has expired")

    @staticmethod
    def _validate_running_lease(task: Task, lease_token: str, worker_id: str) -> None:
        if task.status != TaskStatus.RUNNING.value:
            raise ConflictError("task does not have an active running lease")
        if task.lease_owner != worker_id or task.lease_token != lease_token:
            raise AuthorizationError("running lease owner or token does not match")
        if task.lease_expires_at is None:
            raise ConflictError("running task lease has no expiry")
        expires_at = task.lease_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            raise ConflictError("running task lease has expired")

    @staticmethod
    def _require_candidate_revision(workflow: Workflow) -> None:
        if not workflow.candidate_revision:
            raise ConflictError("candidate revision is required for evidence binding")

    @staticmethod
    def _deactivate_task_grant(session: Session, task_id: str) -> None:
        grants = session.scalars(
            select(CapabilityGrant).where(
                CapabilityGrant.task_id == task_id,
                CapabilityGrant.active.is_(True),
            )
        ).all()
        for grant in grants:
            grant.active = False

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

    @staticmethod
    def _task_dict(task: Task, *, include_lease_token: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": task.id,
            "workflow_id": task.workflow_id,
            "kind": task.kind,
            "required_role": task.required_role,
            "status": task.status,
            "position": task.position,
            "attempts": len(task.attempts),
            "lease_owner": task.lease_owner,
            "lease_expires_at": (
                task.lease_expires_at.isoformat() if task.lease_expires_at else None
            ),
        }
        if include_lease_token:
            result["lease_token"] = task.lease_token
        return result

    @classmethod
    def _workflow_dict(cls, workflow: Workflow) -> dict[str, Any]:
        return {
            "id": workflow.id,
            "title": workflow.title,
            "description": workflow.description,
            "state": workflow.state,
            "version": workflow.version,
            "risk_class": workflow.risk_class,
            "complexity_tier": workflow.complexity_tier,
            "data_classification": workflow.data_classification,
            "repository_scope": workflow.repository_scope,
            "inspection_signals": workflow.inspection_signals,
            "containment_required": workflow.containment_required,
            "block_reason": workflow.block_reason,
            "requester_id": workflow.requester_id,
            "policy_version": workflow.policy_version,
            "candidate_revision": workflow.candidate_revision,
            "merged_revision": workflow.merged_revision,
            "tasks": [cls._task_dict(task) for task in workflow.tasks],
            "created_at": workflow.created_at.isoformat(),
            "updated_at": workflow.updated_at.isoformat(),
        }

    @staticmethod
    def _approval_dict(approval: Approval) -> dict[str, Any]:
        return {
            "id": approval.id,
            "workflow_id": approval.workflow_id,
            "action": approval.action,
            "target": approval.target,
            "revision": approval.revision,
            "policy_version": approval.policy_version,
            "environment_id": approval.environment_id,
            "plan_digest": approval.plan_digest,
            "deployment_attempt_id": approval.deployment_attempt_id,
            "approver_id": approval.approver_id,
            "decision": approval.decision,
            "rationale": approval.rationale,
            "expires_at": approval.expires_at.isoformat(),
            "consumed_at": approval.consumed_at.isoformat() if approval.consumed_at else None,
        }
