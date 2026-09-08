from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from control_plane.domain import (
    ApprovalAction,
    ApprovalDecision,
    AuthorizationError,
    Capability,
    ConflictError,
)
from control_plane.persistence import CapabilityGrant, Principal, Task
from control_plane.policy import PolicyEngine
from control_plane.service import ControlPlaneService
from tests.conftest import drive_to_human_gate


def test_agent_cannot_create_human_approval(service: ControlPlaneService) -> None:
    workflow = drive_to_human_gate(service)
    with pytest.raises(AuthorizationError, match="authenticated human"):
        service.approve(
            workflow_id=str(workflow["id"]),
            approver_id="reviewer-agent",
            action=ApprovalAction.MERGE,
            target="owner/repo:main",
            revision=str(workflow["candidate_revision"]),
            decision=ApprovalDecision.APPROVED,
            rationale="Agent attempts self approval.",
        )


def test_wrong_revision_invalidates_approval(service: ControlPlaneService) -> None:
    workflow = drive_to_human_gate(service)
    with pytest.raises(ConflictError, match="does not match"):
        service.approve(
            workflow_id=str(workflow["id"]),
            approver_id="dev-operator",
            action=ApprovalAction.MERGE,
            target="owner/repo:main",
            revision="different-revision",
            decision=ApprovalDecision.APPROVED,
            rationale="This must not be accepted.",
        )


def test_agent_grant_is_task_scoped(service: ControlPlaneService) -> None:
    first = service.create_workflow(
        requester_id="dev-operator",
        title="First",
        description="First workflow.",
        idempotency_key="first-scope-key",
    )
    second = service.create_workflow(
        requester_id="dev-operator",
        title="Second",
        description="Second workflow.",
        idempotency_key="second-scope-key",
    )
    with service.session_factory() as session:
        first_task = session.scalar(select(Task).where(Task.workflow_id == first["id"]))
        second_task = session.scalar(select(Task).where(Task.workflow_id == second["id"]))
        assert first_task is not None and second_task is not None
        with pytest.raises(AuthorizationError, match="no active"):
            PolicyEngine().authorize(
                session,
                "architect-agent",
                Capability.PRODUCE_PLAN,
                workflow_id=str(first["id"]),
                task_id=second_task.id,
            )


def test_expired_agent_grant_is_denied(service: ControlPlaneService) -> None:
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Expired grant",
        description="Expired task capability.",
        idempotency_key="expired-grant-key",
    )
    with service.session_factory() as session, session.begin():
        task = session.scalar(select(Task).where(Task.workflow_id == workflow["id"]))
        assert task is not None
        grant = session.scalar(select(CapabilityGrant).where(CapabilityGrant.task_id == task.id))
        assert grant is not None
        grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with service.session_factory() as session:
        with pytest.raises(AuthorizationError, match="no active"):
            PolicyEngine().authorize(
                session,
                "architect-agent",
                Capability.PRODUCE_PLAN,
                workflow_id=str(workflow["id"]),
                task_id=task.id,
            )


def test_unknown_principal_is_denied(service: ControlPlaneService) -> None:
    with service.session_factory() as session:
        assert session.get(Principal, "unknown") is None
        with pytest.raises(AuthorizationError, match="unknown"):
            PolicyEngine().authorize(session, "unknown", Capability.READ_WORKFLOW)
