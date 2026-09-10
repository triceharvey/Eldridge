from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class WorkflowState(StrEnum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    ARCHITECTURE_REVIEW = "ARCHITECTURE_REVIEW"
    APPROVED_FOR_IMPLEMENTATION = "APPROVED_FOR_IMPLEMENTATION"
    IMPLEMENTING = "IMPLEMENTING"
    TESTING = "TESTING"
    SECURITY_REVIEW = "SECURITY_REVIEW"
    CODE_REVIEW = "CODE_REVIEW"
    AWAITING_HUMAN_APPROVAL = "AWAITING_HUMAN_APPROVAL"
    APPROVED = "APPROVED"
    MERGED = "MERGED"
    AWAITING_DEPLOYMENT_APPROVAL = "AWAITING_DEPLOYMENT_APPROVAL"
    DEPLOYED = "DEPLOYED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    ROLLBACK_REQUIRED = "ROLLBACK_REQUIRED"
    ROLLED_BACK = "ROLLED_BACK"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    RECONCILED = "RECONCILED"


class PrincipalType(StrEnum):
    HUMAN = "HUMAN"
    AGENT = "AGENT"
    SERVICE = "SERVICE"
    INTEGRATION = "INTEGRATION"


class AgentRole(StrEnum):
    ARCHITECT = "ARCHITECT"
    REVIEWER = "REVIEWER"
    IMPLEMENTER = "IMPLEMENTER"
    TEST_AGENT = "TEST_AGENT"
    SECURITY_AGENT = "SECURITY_AGENT"
    DOCUMENTATION_AGENT = "DOCUMENTATION_AGENT"
    ORCHESTRATOR = "ORCHESTRATOR"
    HUMAN_APPROVER = "HUMAN_APPROVER"
    AUDITOR = "AUDITOR"
    IDE_INTEGRATION = "IDE_INTEGRATION"
    CI_INTEGRATION = "CI_INTEGRATION"


class Capability(StrEnum):
    SUBMIT_WORKFLOW = "SUBMIT_WORKFLOW"
    READ_WORKFLOW = "READ_WORKFLOW"
    CANCEL_WORKFLOW = "CANCEL_WORKFLOW"
    LEASE_TASK = "LEASE_TASK"
    PRODUCE_PLAN = "PRODUCE_PLAN"
    REVIEW_ARCHITECTURE = "REVIEW_ARCHITECTURE"
    IMPLEMENT_CHANGE = "IMPLEMENT_CHANGE"
    RUN_TESTS = "RUN_TESTS"
    RUN_SECURITY_REVIEW = "RUN_SECURITY_REVIEW"
    RUN_CODE_REVIEW = "RUN_CODE_REVIEW"
    APPROVE_MERGE = "APPROVE_MERGE"
    APPROVE_DEPLOYMENT = "APPROVE_DEPLOYMENT"
    READ_AUDIT = "READ_AUDIT"
    DISPOSITION_WORKFLOW = "DISPOSITION_WORKFLOW"
    RECONCILE_EXECUTION = "RECONCILE_EXECUTION"
    CLAIM_IDE_TASK = "CLAIM_IDE_TASK"
    HEARTBEAT_IDE_TASK = "HEARTBEAT_IDE_TASK"
    SUBMIT_IDE_EVIDENCE = "SUBMIT_IDE_EVIDENCE"
    SUBMIT_CI_EVIDENCE = "SUBMIT_CI_EVIDENCE"
    PROPOSE_PULL_REQUEST = "PROPOSE_PULL_REQUEST"
    ASSESS_MERGE_READINESS = "ASSESS_MERGE_READINESS"
    RECONCILE_GIT_OPERATION = "RECONCILE_GIT_OPERATION"
    CONFIRM_GIT_MERGE = "CONFIRM_GIT_MERGE"
    MANAGE_DEPLOYMENT_ENVIRONMENTS = "MANAGE_DEPLOYMENT_ENVIRONMENTS"
    CREATE_DEPLOYMENT_PLAN = "CREATE_DEPLOYMENT_PLAN"
    EXECUTE_DEPLOYMENT = "EXECUTE_DEPLOYMENT"
    APPROVE_ROLLBACK = "APPROVE_ROLLBACK"
    EXECUTE_ROLLBACK = "EXECUTE_ROLLBACK"
    READ_DEPLOYMENT = "READ_DEPLOYMENT"


class ApprovalAction(StrEnum):
    MERGE = "MERGE"
    DEPLOY = "DEPLOY"
    ROLLBACK = "ROLLBACK"


class ApprovalDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DispositionDecision(StrEnum):
    RESUME_CONTAINED = "RESUME_CONTAINED"
    REJECT = "REJECT"


class ReconciliationDecision(StrEnum):
    RETRY = "RETRY"
    FAIL = "FAIL"


class TaskKind(StrEnum):
    PLAN = "PLAN"
    ARCHITECTURE_REVIEW = "ARCHITECTURE_REVIEW"
    IMPLEMENT = "IMPLEMENT"
    TEST = "TEST"
    SECURITY_REVIEW = "SECURITY_REVIEW"
    CODE_REVIEW = "CODE_REVIEW"


class ControlPlaneError(Exception):
    """Base class for stable, user-safe control-plane errors."""


class AuthorizationError(ControlPlaneError):
    pass


class AuthenticationError(ControlPlaneError):
    pass


class InvalidTransitionError(ControlPlaneError):
    pass


class ConflictError(ControlPlaneError):
    pass


class NotFoundError(ControlPlaneError):
    pass


class ValidationError(ControlPlaneError):
    pass


class SandboxError(ControlPlaneError):
    pass


class WorkspaceError(ControlPlaneError):
    pass


class IntegrationDisabledError(ControlPlaneError):
    pass


class DeploymentOutcomeUnknownError(ControlPlaneError):
    """The target may have changed and must be reconciled without an automatic retry."""


class DeploymentVerificationError(ControlPlaneError):
    """A known target change could not be verified and requires rollback disposition."""


class IntegrationResponseError(ControlPlaneError):
    pass


ROLE_CAPABILITIES: dict[AgentRole, frozenset[Capability]] = {
    AgentRole.ARCHITECT: frozenset({Capability.PRODUCE_PLAN, Capability.READ_WORKFLOW}),
    AgentRole.REVIEWER: frozenset(
        {Capability.REVIEW_ARCHITECTURE, Capability.RUN_CODE_REVIEW, Capability.READ_WORKFLOW}
    ),
    AgentRole.IMPLEMENTER: frozenset({Capability.IMPLEMENT_CHANGE, Capability.READ_WORKFLOW}),
    AgentRole.TEST_AGENT: frozenset({Capability.RUN_TESTS, Capability.READ_WORKFLOW}),
    AgentRole.SECURITY_AGENT: frozenset({Capability.RUN_SECURITY_REVIEW, Capability.READ_WORKFLOW}),
    AgentRole.DOCUMENTATION_AGENT: frozenset({Capability.READ_WORKFLOW}),
    AgentRole.ORCHESTRATOR: frozenset({Capability.LEASE_TASK, Capability.READ_WORKFLOW}),
    AgentRole.HUMAN_APPROVER: frozenset(
        {
            Capability.SUBMIT_WORKFLOW,
            Capability.READ_WORKFLOW,
            Capability.CANCEL_WORKFLOW,
            Capability.APPROVE_MERGE,
            Capability.APPROVE_DEPLOYMENT,
            Capability.READ_AUDIT,
            Capability.DISPOSITION_WORKFLOW,
            Capability.RECONCILE_EXECUTION,
            Capability.PROPOSE_PULL_REQUEST,
            Capability.ASSESS_MERGE_READINESS,
            Capability.RECONCILE_GIT_OPERATION,
            Capability.CONFIRM_GIT_MERGE,
            Capability.MANAGE_DEPLOYMENT_ENVIRONMENTS,
            Capability.CREATE_DEPLOYMENT_PLAN,
            Capability.EXECUTE_DEPLOYMENT,
            Capability.APPROVE_ROLLBACK,
            Capability.EXECUTE_ROLLBACK,
            Capability.READ_DEPLOYMENT,
        }
    ),
    AgentRole.AUDITOR: frozenset(
        {Capability.READ_WORKFLOW, Capability.READ_AUDIT, Capability.READ_DEPLOYMENT}
    ),
    AgentRole.IDE_INTEGRATION: frozenset(
        {
            Capability.CLAIM_IDE_TASK,
            Capability.HEARTBEAT_IDE_TASK,
            Capability.SUBMIT_IDE_EVIDENCE,
        }
    ),
    AgentRole.CI_INTEGRATION: frozenset({Capability.SUBMIT_CI_EVIDENCE}),
}


TASK_CAPABILITY: dict[TaskKind, Capability] = {
    TaskKind.PLAN: Capability.PRODUCE_PLAN,
    TaskKind.ARCHITECTURE_REVIEW: Capability.REVIEW_ARCHITECTURE,
    TaskKind.IMPLEMENT: Capability.IMPLEMENT_CHANGE,
    TaskKind.TEST: Capability.RUN_TESTS,
    TaskKind.SECURITY_REVIEW: Capability.RUN_SECURITY_REVIEW,
    TaskKind.CODE_REVIEW: Capability.RUN_CODE_REVIEW,
}


TASK_ROLE: dict[TaskKind, AgentRole] = {
    TaskKind.PLAN: AgentRole.ARCHITECT,
    TaskKind.ARCHITECTURE_REVIEW: AgentRole.REVIEWER,
    TaskKind.IMPLEMENT: AgentRole.IMPLEMENTER,
    TaskKind.TEST: AgentRole.TEST_AGENT,
    TaskKind.SECURITY_REVIEW: AgentRole.SECURITY_AGENT,
    TaskKind.CODE_REVIEW: AgentRole.REVIEWER,
}


@dataclass(frozen=True)
class ProviderRequest:
    run_id: str
    workflow_id: str
    task_id: str
    task_kind: TaskKind
    role: AgentRole
    objective: str
    context: dict[str, Any]
    required_capability: Capability
    idempotency_key: str


@dataclass(frozen=True)
class ProviderResult:
    status: str
    output: dict[str, Any]
    provider: str
    model: str
    usage: dict[str, int]


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    summary: str
    evidence: dict[str, Any]
    commands_executed: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionContext:
    workflow_id: str
    task_id: str
    repository_scope: str | None
    candidate_revision: str | None
    containment_required: bool
