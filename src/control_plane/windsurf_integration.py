from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from control_plane.audit import append_audit_event
from control_plane.domain import (
    AuthorizationError,
    Capability,
    ConflictError,
    IntegrationDisabledError,
    NotFoundError,
    TaskKind,
    TaskStatus,
    ValidationError,
    WorkflowState,
)
from control_plane.integrations import WindsurfHandoff
from control_plane.persistence import Artifact, CapabilityGrant, Task, TaskAttempt, Workflow
from control_plane.policy import PolicyEngine
from control_plane.workflow_tasks import WorkflowTaskService
from control_plane.workspaces import RepositoryRegistry


class WindsurfIntegrationService:
    """Revision-bound Windsurf task handoff and evidence boundary."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        policy: PolicyEngine,
        *,
        lease_seconds: int,
        repository_registry: RepositoryRegistry | None,
        workflow_tasks: WorkflowTaskService,
    ) -> None:
        self.session_factory = session_factory
        self.policy = policy
        self.lease_seconds = lease_seconds
        self.repository_registry = repository_registry
        self.workflow_tasks = workflow_tasks

    def claim_windsurf_task(self, *, task_id: str, principal_id: str) -> dict[str, Any]:
        if self.repository_registry is None:
            raise IntegrationDisabledError("Windsurf repository handoff is not configured")
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.CLAIM_IDE_TASK)
            task = session.get(Task, task_id)
            if task is None:
                raise NotFoundError("task not found")
            existing = self._existing_windsurf_claim(task, principal_id)
            if existing is not None:
                return existing
            workflow = self.workflow_tasks._get_workflow(session, task.workflow_id)
            self._validate_windsurf_claimable(workflow, task)
            repository_scope = workflow.repository_scope
            assert repository_scope is not None
            requested_base = workflow.candidate_revision
            policy_version = workflow.policy_version
            objective = task.objective

        registration = self.repository_registry.resolve(repository_scope)
        base_revision = self.repository_registry.resolve_commit(
            repository_scope, requested_base or registration.base_revision
        )
        branch = f"codex/task-{task_id}"
        if self.repository_registry.branch_exists(repository_scope, branch):
            raise ConflictError("Windsurf handoff branch already exists")
        handoff = WindsurfHandoff().create(
            workflow_id=task.workflow_id,
            task_id=task.id,
            repository=repository_scope,
            branch=branch,
            base_revision=base_revision,
            objective=objective,
            assigned_principal=principal_id,
            policy_version=policy_version,
            writable_paths=registration.writable_paths,
        )
        handoff_payload = asdict(handoff)

        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, principal_id, Capability.CLAIM_IDE_TASK)
            task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
            if task is None:
                raise NotFoundError("task not found")
            existing = self._existing_windsurf_claim(task, principal_id)
            if existing is not None:
                return existing
            workflow = self.workflow_tasks._get_workflow(session, task.workflow_id, lock=True)
            self._validate_windsurf_claimable(workflow, task)
            if (
                workflow.repository_scope != repository_scope
                or workflow.policy_version != policy_version
                or workflow.candidate_revision != requested_base
            ):
                raise ConflictError("workflow changed while the Windsurf handoff was prepared")
            task.status = TaskStatus.RUNNING.value
            task.lease_owner = principal_id
            task.lease_token = str(uuid4())
            task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self.lease_seconds)
            attempt = TaskAttempt(
                task=task,
                attempt_number=len(task.attempts) + 1,
                agent_id=principal_id,
                provider="windsurf",
                model="cascade-interactive",
                status=TaskStatus.RUNNING.value,
                output={"handoff": handoff_payload},
            )
            session.add(attempt)
            self.workflow_tasks._deactivate_task_grant(session, task.id)
            session.add(
                CapabilityGrant(
                    principal_id=principal_id,
                    workflow_id=workflow.id,
                    task_id=task.id,
                    capabilities=[
                        Capability.HEARTBEAT_IDE_TASK.value,
                        Capability.SUBMIT_IDE_EVIDENCE.value,
                    ],
                    policy_version=workflow.policy_version,
                    expires_at=task.lease_expires_at,
                )
            )
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="windsurf.task_claimed",
                actor_id=principal_id,
                resource_type="task",
                resource_id=task.id,
                outcome="SUCCEEDED",
                payload={"attempt_id": attempt.id, "handoff_digest": handoff.digest},
            )
            session.flush()
            return self._windsurf_claim_dict(task, handoff_payload)

    def heartbeat_windsurf_task(
        self, *, task_id: str, lease_token: str, principal_id: str
    ) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
            if task is None:
                raise NotFoundError("task not found")
            self.policy.authorize(
                session,
                principal_id,
                Capability.HEARTBEAT_IDE_TASK,
                workflow_id=task.workflow_id,
                task_id=task.id,
            )
            self.workflow_tasks._validate_running_lease(task, lease_token, principal_id)
            attempt = self._windsurf_attempt(session, task.id)
            task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self.lease_seconds)
            grant = session.scalar(
                select(CapabilityGrant).where(
                    CapabilityGrant.principal_id == principal_id,
                    CapabilityGrant.task_id == task.id,
                    CapabilityGrant.active.is_(True),
                )
            )
            if grant is not None:
                grant.expires_at = task.lease_expires_at
            self.workflow_tasks._get_workflow(session, task.workflow_id, lock=True)
            append_audit_event(
                session,
                workflow_id=task.workflow_id,
                event_type="windsurf.task_heartbeat",
                actor_id=principal_id,
                resource_type="task_attempt",
                resource_id=attempt.id,
                outcome="SUCCEEDED",
                payload={"lease_expires_at": task.lease_expires_at.isoformat()},
            )
            return self.workflow_tasks._task_dict(task)

    def submit_windsurf_evidence(
        self,
        *,
        task_id: str,
        lease_token: str,
        principal_id: str,
        handoff_digest: str,
        result_revision: str,
        files_changed: tuple[str, ...],
        tests_passed: bool,
        test_summary: str,
        tool_activity_summary: str,
    ) -> dict[str, Any]:
        if self.repository_registry is None:
            raise IntegrationDisabledError("Windsurf repository handoff is not configured")
        if (
            not handoff_digest
            or not result_revision
            or not test_summary.strip()
            or not tool_activity_summary.strip()
        ):
            raise ValidationError("complete Windsurf evidence is required")
        if len(test_summary) > 4_000 or len(tool_activity_summary) > 4_000:
            raise ValidationError("Windsurf evidence summary exceeds the size limit")
        if len(files_changed) != len(set(files_changed)):
            raise ValidationError("files_changed contains duplicates")

        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.CLAIM_IDE_TASK)
            task = session.get(Task, task_id)
            if task is None:
                raise NotFoundError("task not found")
            attempt = self._windsurf_attempt(session, task.id)
            handoff = attempt.output.get("handoff")
            if not isinstance(handoff, dict) or handoff.get("digest") != handoff_digest:
                raise ConflictError("Windsurf evidence does not match the handoff digest")
            if handoff.get("assigned_principal") != principal_id:
                raise AuthorizationError("Windsurf handoff belongs to a different principal")
            if task.status == TaskStatus.RUNNING.value:
                self.policy.authorize(
                    session,
                    principal_id,
                    Capability.SUBMIT_IDE_EVIDENCE,
                    workflow_id=task.workflow_id,
                    task_id=task.id,
                )
                self.workflow_tasks._validate_running_lease(task, lease_token, principal_id)
            elif task.status != TaskStatus.SUCCEEDED.value:
                raise ConflictError("Windsurf task is not accepting evidence")
            workflow_id = task.workflow_id
            repository_scope = handoff.get("repository")
            branch = handoff.get("branch")
            base_revision = handoff.get("base_revision")
            if (
                not isinstance(repository_scope, str)
                or not isinstance(branch, str)
                or not isinstance(base_revision, str)
            ):
                raise ConflictError("stored Windsurf handoff is invalid")

        actual_files = self.repository_registry.verify_revision_evidence(
            scope_id=repository_scope,
            branch=branch,
            base_revision=base_revision,
            result_revision=result_revision,
        )
        if tuple(files_changed) != actual_files:
            raise ConflictError("reported files do not match the Git revision")
        evidence = {
            "handoff_digest": handoff_digest,
            "result_revision": result_revision,
            "files_changed": list(actual_files),
            "tests_passed_claim": tests_passed,
            "test_summary": test_summary.strip(),
            "tool_activity_summary": tool_activity_summary.strip(),
            "test_claim_authoritative": False,
        }
        evidence_digest = self._digest(evidence)

        with self.session_factory() as session, session.begin():
            task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
            if task is None:
                raise NotFoundError("task not found")
            attempt = self._windsurf_attempt(session, task.id)
            prior_evidence = attempt.output.get("evidence")
            if task.status == TaskStatus.SUCCEEDED.value and isinstance(prior_evidence, dict):
                if self._digest(prior_evidence) != evidence_digest:
                    raise ConflictError("Windsurf task already has different evidence")
                return self.workflow_tasks._task_dict(task)
            self.policy.authorize(
                session,
                principal_id,
                Capability.SUBMIT_IDE_EVIDENCE,
                workflow_id=workflow_id,
                task_id=task.id,
            )
            self.workflow_tasks._validate_running_lease(task, lease_token, principal_id)
            stored_handoff = attempt.output.get("handoff")
            if (
                not isinstance(stored_handoff, dict)
                or stored_handoff.get("digest") != handoff_digest
            ):
                raise ConflictError("Windsurf handoff changed before evidence finalization")
            workflow = self.workflow_tasks._get_workflow(session, workflow_id, lock=True)
            if WorkflowState(workflow.state) is not WorkflowState.IMPLEMENTING:
                raise ConflictError("workflow is no longer accepting implementation evidence")
            attempt.output = {"handoff": stored_handoff, "evidence": evidence}
            attempt.status = TaskStatus.SUCCEEDED.value
            attempt.completed_at = datetime.now(UTC)
            task.status = TaskStatus.SUCCEEDED.value
            task.lease_owner = None
            task.lease_token = None
            task.lease_expires_at = None
            self.workflow_tasks._deactivate_task_grant(session, task.id)
            workflow.candidate_revision = result_revision
            session.add(
                Artifact(
                    workflow_id=workflow.id,
                    task_id=task.id,
                    attempt_id=attempt.id,
                    artifact_type="WINDSURF_IMPLEMENTATION_EVIDENCE",
                    digest=evidence_digest,
                    revision=result_revision,
                    metadata_json=evidence,
                )
            )
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="windsurf.evidence_accepted",
                actor_id=principal_id,
                resource_type="task_attempt",
                resource_id=attempt.id,
                outcome="SUCCEEDED",
                payload={
                    "handoff_digest": handoff_digest,
                    "evidence_digest": evidence_digest,
                    "result_revision": result_revision,
                },
            )
            self.workflow_tasks._advance_after_task(session, workflow, TaskKind.IMPLEMENT)
            session.flush()
            return self.workflow_tasks._task_dict(task)

    @staticmethod
    def _validate_windsurf_claimable(workflow: Workflow, task: Task) -> None:
        if TaskKind(task.kind) is not TaskKind.IMPLEMENT:
            raise ConflictError("Windsurf may claim only implementation tasks")
        if task.status != TaskStatus.READY.value:
            raise ConflictError("Windsurf task is not ready to claim")
        if WorkflowState(workflow.state) is not WorkflowState.IMPLEMENTING:
            raise ConflictError("workflow is not accepting implementation work")
        if workflow.repository_scope is None:
            raise ConflictError("Windsurf handoff requires a registered repository scope")

    @staticmethod
    def _windsurf_attempt(session: Session, task_id: str) -> TaskAttempt:
        attempt = session.scalar(
            select(TaskAttempt)
            .where(TaskAttempt.task_id == task_id, TaskAttempt.provider == "windsurf")
            .order_by(TaskAttempt.attempt_number.desc())
            .limit(1)
        )
        if attempt is None:
            raise ConflictError("task has no Windsurf handoff attempt")
        return attempt

    def _existing_windsurf_claim(self, task: Task, principal_id: str) -> dict[str, Any] | None:
        if task.status != TaskStatus.RUNNING.value or task.lease_owner != principal_id:
            return None
        self.workflow_tasks._validate_running_lease(task, str(task.lease_token), principal_id)
        attempts = sorted(task.attempts, key=lambda item: item.attempt_number, reverse=True)
        attempt = next((item for item in attempts if item.provider == "windsurf"), None)
        if attempt is None:
            raise ConflictError("running task is not a Windsurf handoff")
        handoff = attempt.output.get("handoff")
        if not isinstance(handoff, dict):
            raise ConflictError("stored Windsurf handoff is invalid")
        return self._windsurf_claim_dict(task, handoff)

    @staticmethod
    def _windsurf_claim_dict(task: Task, handoff: dict[str, Any]) -> dict[str, Any]:
        if task.lease_token is None or task.lease_expires_at is None:
            raise ConflictError("Windsurf task has no active lease")
        return {
            "task_id": task.id,
            "workflow_id": task.workflow_id,
            "lease_token": task.lease_token,
            "lease_expires_at": task.lease_expires_at.isoformat(),
            "handoff": handoff,
        }

    @staticmethod
    def _digest(value: Any) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return sha256(encoded).hexdigest()
