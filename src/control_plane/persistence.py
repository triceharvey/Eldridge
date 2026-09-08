from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)
from sqlalchemy.pool import StaticPool

from control_plane.domain import AgentRole, PrincipalType


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Principal(Base):
    __tablename__ = "principals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    principal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    risk_class: Mapped[str] = mapped_column(String(32), nullable=False, default="MEDIUM")
    complexity_tier: Mapped[str] = mapped_column(String(32), nullable=False, default="STANDARD")
    data_classification: Mapped[str] = mapped_column(String(32), nullable=False, default="INTERNAL")
    repository_scope: Mapped[str | None] = mapped_column(String(500))
    inspection_signals: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    containment_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    block_reason: Mapped[str | None] = mapped_column(String(64))
    requester_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False, default="phase1-v1")
    candidate_revision: Mapped[str | None] = mapped_column(String(128))
    merged_revision: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    tasks: Mapped[list[Task]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan", order_by="Task.position"
    )


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    required_role: Mapped[str] = mapped_column(String(64), nullable=False)
    required_capability: Mapped[str] = mapped_column(String(64), nullable=False)
    work_capability: Mapped[str] = mapped_column(String(64), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_token: Mapped[str | None] = mapped_column(String(128), unique=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    workflow: Mapped[Workflow] = relationship(back_populates="tasks")
    attempts: Mapped[list[TaskAttempt]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class TaskAttempt(Base):
    __tablename__ = "task_attempts"
    __table_args__ = (UniqueConstraint("task_id", "attempt_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    task: Mapped[Task] = relationship(back_populates="attempts")


class RoutingRecord(Base):
    __tablename__ = "routing_records"
    __table_args__ = (UniqueConstraint("attempt_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("task_attempts.id"), index=True)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    ranked_candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    rejected_candidates: Mapped[dict[str, list[str]]] = mapped_column(JSON, nullable=False)
    selected_provider_id: Mapped[str | None] = mapped_column(String(128))
    selected_provider_family: Mapped[str | None] = mapped_column(String(128))
    selected_model_version: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProviderObservation(Base):
    __tablename__ = "provider_observations"
    __table_args__ = (UniqueConstraint("attempt_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("task_attempts.id"), index=True)
    provider_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    provider_family: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    profile_version: Mapped[str] = mapped_column(String(64), nullable=False)
    work_capability: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    validation_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CiCheckEvidence(Base):
    __tablename__ = "ci_check_evidence"
    __table_args__ = (
        UniqueConstraint("source", "delivery_id", name="uq_ci_check_source_delivery"),
        UniqueConstraint("source", "check_run_id", name="uq_ci_check_source_run"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    delivery_id: Mapped[str] = mapped_column(String(128), nullable=False)
    repository: Mapped[str] = mapped_column(String(500), nullable=False)
    check_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    check_name: Mapped[str] = mapped_column(String(200), nullable=False)
    revision: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    conclusion: Mapped[str] = mapped_column(String(32), nullable=False)
    details_url: Mapped[str | None] = mapped_column(String(1000))
    app_slug: Mapped[str | None] = mapped_column(String(200))
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PullRequestProposalRecord(Base):
    __tablename__ = "pull_request_proposals"
    __table_args__ = (
        UniqueConstraint("actor_id", "idempotency_key", name="uq_pull_request_actor_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    repository: Mapped[str] = mapped_column(String(500), nullable=False)
    head_branch: Mapped[str] = mapped_column(String(200), nullable=False)
    base_branch: Mapped[str] = mapped_column(String(200), nullable=False)
    revision: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    pull_number: Mapped[int | None] = mapped_column(Integer)
    pull_url: Mapped[str | None] = mapped_column(String(1000))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MergeReadinessAssessmentRecord(Base):
    __tablename__ = "merge_readiness_assessments"
    __table_args__ = (
        UniqueConstraint(
            "actor_id", "idempotency_key", name="uq_merge_readiness_actor_idempotency"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("pull_request_proposals.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    ready: Mapped[bool | None] = mapped_column(Boolean)
    reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    checks: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MergeConfirmationRecord(Base):
    __tablename__ = "merge_confirmations"
    __table_args__ = (
        UniqueConstraint("actor_id", "idempotency_key", name="uq_merge_confirmation_actor_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("pull_request_proposals.id"), index=True)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("merge_readiness_assessments.id"), index=True
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    merge_commit_revision: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CapabilityGrant(Base):
    __tablename__ = "capability_grants"
    __table_args__ = (UniqueConstraint("principal_id", "task_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), index=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[str] = mapped_column(String(500), nullable=False)
    revision: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    environment_id: Mapped[str | None] = mapped_column(String(128))
    plan_digest: Mapped[str | None] = mapped_column(String(64))
    approver_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeploymentEnvironment(Base):
    __tablename__ = "deployment_environments"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    account_scope: Mapped[str] = mapped_column(String(256), nullable=False)
    region: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_scope: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    adapter_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    repository: Mapped[str] = mapped_column(String(500), nullable=False)
    base_branch: Mapped[str] = mapped_column(String(200), nullable=False)
    required_checks: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    required_attestations: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    verification_policy: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    rollback_policy: Mapped[str] = mapped_column(String(256), nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeploymentPlanRecord(Base):
    __tablename__ = "deployment_plans"
    __table_args__ = (
        UniqueConstraint("actor_id", "idempotency_key", name="uq_deployment_plan_actor_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    environment_id: Mapped[str] = mapped_column(
        ForeignKey("deployment_environments.id"), index=True
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[str] = mapped_column(String(128), nullable=False)
    artifact_digests: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    operations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    declared_impact: Mapped[str] = mapped_column(Text, nullable=False)
    verification_probes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    rollback_reference: Mapped[str] = mapped_column(String(256), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeploymentAttemptRecord(Base):
    __tablename__ = "deployment_attempts"
    __table_args__ = (
        UniqueConstraint("actor_id", "idempotency_key", name="uq_deployment_attempt_actor_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("deployment_plans.id"), index=True)
    approval_id: Mapped[str] = mapped_column(ForeignKey("approvals.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    operation_reference: Mapped[str | None] = mapped_column(String(256))
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeploymentVerificationRecord(Base):
    __tablename__ = "deployment_verifications"
    __table_args__ = (UniqueConstraint("attempt_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("deployment_attempts.id"), index=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeploymentRollbackRecord(Base):
    __tablename__ = "deployment_rollbacks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("deployment_attempts.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowDisposition(Base):
    __tablename__ = "workflow_dispositions"
    __table_args__ = (UniqueConstraint("workflow_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    original_signals: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExecutionReconciliation(Base):
    __tablename__ = "execution_reconciliations"
    __table_args__ = (UniqueConstraint("attempt_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("task_attempts.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("task_attempts.id"), index=True)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    digest: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[str | None] = mapped_column(String(128))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("workflow_id", "sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("principal_id", "command", "idempotency_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    principal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    command: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def make_engine(database_url: str) -> Engine:
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if database_url in {"sqlite://", "sqlite:///:memory:"}:
        kwargs.update({"connect_args": {"check_same_thread": False}, "poolclass": StaticPool})
    return create_engine(database_url, **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def initialize_database(engine: Engine) -> None:
    Base.metadata.create_all(engine)


BUILTIN_PRINCIPALS = (
    ("dev-operator", PrincipalType.HUMAN, AgentRole.HUMAN_APPROVER, "Development Operator"),
    ("orchestrator", PrincipalType.SERVICE, AgentRole.ORCHESTRATOR, "Workflow Orchestrator"),
    ("architect-agent", PrincipalType.AGENT, AgentRole.ARCHITECT, "Mock Architect"),
    ("reviewer-agent", PrincipalType.AGENT, AgentRole.REVIEWER, "Mock Independent Reviewer"),
    ("implementer-agent", PrincipalType.AGENT, AgentRole.IMPLEMENTER, "Mock Implementer"),
    ("test-agent", PrincipalType.AGENT, AgentRole.TEST_AGENT, "Mock Test Agent"),
    ("security-agent", PrincipalType.AGENT, AgentRole.SECURITY_AGENT, "Mock Security Agent"),
    (
        "windsurf-cascade",
        PrincipalType.INTEGRATION,
        AgentRole.IDE_INTEGRATION,
        "Windsurf Cascade MCP",
    ),
    (
        "github-ci",
        PrincipalType.INTEGRATION,
        AgentRole.CI_INTEGRATION,
        "GitHub CI Webhook",
    ),
)


def seed_principals(session: Session) -> None:
    for principal_id, principal_type, role, display_name in BUILTIN_PRINCIPALS:
        if session.get(Principal, principal_id) is None:
            session.add(
                Principal(
                    id=principal_id,
                    principal_type=principal_type.value,
                    role=role.value,
                    display_name=display_name,
                )
            )
    session.commit()
