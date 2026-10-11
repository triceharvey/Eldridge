"""Human-dispatched Devin implementation sessions with Git-verified evidence.

Devin works in its own cloud machine and pushes a branch to GitHub. The control plane therefore
never treats a Devin session result as evidence: it fetches the reported branch from the operator
registered repository, verifies the revision with the same Git checks applied to Windsurf
handoffs, and only then records implementation evidence. Devin's own test claim is stored as
non-authoritative, and the workflow still schedules its independent test, security, and code
review stages. Dispatch, sync, and cancel are explicit human actions; Devin has no control-plane
credential and is never placed in automatic routing.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from control_plane.audit import append_audit_event
from control_plane.domain import (
    Capability,
    ConflictError,
    IntegrationDisabledError,
    IntegrationResponseError,
    NotFoundError,
    TaskKind,
    TaskStatus,
    ValidationError,
    WorkflowState,
    WorkspaceError,
)
from control_plane.integrations import (
    RemoteAgentHandle,
    RemoteAgentRequest,
    RemoteAgentRuntime,
    RemoteRunState,
)
from control_plane.persistence import Artifact, Task, TaskAttempt, Workflow
from control_plane.policy import PolicyEngine
from control_plane.routing import DataClassification, RiskLevel
from control_plane.workflow_tasks import WorkflowTaskService
from control_plane.workspaces import REMOTE_AGENT_BRANCH, RepositoryRegistry

DEVIN_PRINCIPAL = "devin-remote"
DEVIN_PROVIDER = "devin"
COMMIT_DIGEST = re.compile(r"^[0-9a-f]{40}$")
MAX_SUMMARY_CHARS = 4_000
IN_FLIGHT_STATES = frozenset(
    {
        RemoteRunState.QUEUED,
        RemoteRunState.RUNNING,
        RemoteRunState.SUSPENDED,
        RemoteRunState.AWAITING_INPUT,
        RemoteRunState.UNKNOWN,
    }
)

EVIDENCE_FIELDS = ("branch", "result_revision", "tests_passed", "test_summary", "summary")
DEVIN_EVIDENCE_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(EVIDENCE_FIELDS),
    "properties": {
        "branch": {"type": "string", "maxLength": 200},
        "result_revision": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
        "tests_passed": {"type": "boolean"},
        "test_summary": {"type": "string", "maxLength": MAX_SUMMARY_CHARS},
        "summary": {"type": "string", "maxLength": MAX_SUMMARY_CHARS},
    },
}


class DevinSessionEnded(IntegrationResponseError):
    """The Devin session ended without usable implementation evidence."""


class DevinIntegrationService:
    """Dispatch, observe, ingest, and cancel Devin implementation sessions."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        policy: PolicyEngine,
        *,
        runtime: RemoteAgentRuntime | None,
        repository_registry: RepositoryRegistry | None,
        workflow_tasks: WorkflowTaskService,
        lease_seconds: int = 4 * 60 * 60,
    ) -> None:
        self.session_factory = session_factory
        self.policy = policy
        self.runtime = runtime
        self.repository_registry = repository_registry
        self.workflow_tasks = workflow_tasks
        self.lease_seconds = lease_seconds

    def dispatch_devin_task(
        self, *, task_id: str, principal_id: str, max_cost_units: int
    ) -> dict[str, Any]:
        runtime, registry = self._require_configured()
        with self.session_factory() as session:
            self._authorize(session, principal_id)
            task = self._get_task(session, task_id)
            workflow = self.workflow_tasks._get_workflow(session, task.workflow_id)
            self._validate_dispatchable(workflow, task)
            repository_scope = workflow.repository_scope
            assert repository_scope is not None
            requested_base = workflow.candidate_revision
            policy_version = workflow.policy_version
            objective = task.objective
            attempt_number = len(task.attempts) + 1
            data_classification = DataClassification[workflow.data_classification]
            risk = RiskLevel[workflow.risk_class]

        registration = registry.resolve(repository_scope)
        if registration.remote_agent_repository is None:
            raise ConflictError("repository scope is not registered for remote agents")
        base_revision = registry.resolve_commit(
            repository_scope, requested_base or registration.base_revision
        )
        local_branch = f"devin/task-{task_id}-a{attempt_number}"
        if registry.branch_exists(repository_scope, local_branch):
            raise ConflictError("Devin handoff branch already exists")
        handoff: dict[str, Any] = {
            "workflow_id": task.workflow_id,
            "task_id": task_id,
            "repository_scope": repository_scope,
            "remote_repository": registration.remote_agent_repository,
            "requested_branch": local_branch,
            "base_revision": base_revision,
            "objective": objective,
            "dispatched_by": principal_id,
            "policy_version": policy_version,
            "writable_paths": list(registration.writable_paths),
            "max_cost_units": max_cost_units,
            "evidence_schema": DEVIN_EVIDENCE_SCHEMA,
            "integration_mode": "devin-remote-session",
        }
        handoff["digest"] = self._digest(handoff)

        with self.session_factory() as session, session.begin():
            self._authorize(session, principal_id)
            task = self._get_task(session, task_id, lock=True)
            workflow = self.workflow_tasks._get_workflow(session, task.workflow_id, lock=True)
            self._validate_dispatchable(workflow, task)
            if (
                workflow.repository_scope != repository_scope
                or workflow.policy_version != policy_version
                or workflow.candidate_revision != requested_base
                or len(task.attempts) + 1 != attempt_number
            ):
                raise ConflictError("workflow changed while the Devin handoff was prepared")
            task.status = TaskStatus.RUNNING.value
            task.lease_owner = DEVIN_PRINCIPAL
            task.lease_token = str(uuid4())
            task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self.lease_seconds)
            attempt = TaskAttempt(
                task=task,
                attempt_number=attempt_number,
                agent_id=DEVIN_PRINCIPAL,
                provider=DEVIN_PROVIDER,
                model="devin-session",
                status=TaskStatus.RUNNING.value,
                output={"handoff": handoff, "remote": {"state": "DISPATCHING"}},
            )
            session.add(attempt)
            self.workflow_tasks._deactivate_task_grant(session, task.id)
            session.flush()
            attempt_id = attempt.id
            # Intent is committed before contact, so a crash during submit leaves a RUNNING
            # attempt whose lease expiry routes it to human reconciliation, never a blind retry.
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="devin.dispatch_committed",
                actor_id=principal_id,
                resource_type="task_attempt",
                resource_id=attempt_id,
                outcome="SUCCEEDED",
                payload={"handoff_digest": handoff["digest"], "max_cost_units": max_cost_units},
            )

        request = RemoteAgentRequest(
            run_id=attempt_id,
            workflow_id=handoff["workflow_id"],
            task_id=task_id,
            objective=self._prompt(handoff),
            repositories=(str(handoff["remote_repository"]),),
            max_cost_units=max_cost_units,
            idempotency_key=attempt_id,
            tags=("eldridge", f"handoff:{handoff['digest'][:16]}"),
            structured_output_schema=DEVIN_EVIDENCE_SCHEMA,
            data_classification=data_classification,
            risk=risk,
        )
        try:
            remote = runtime.submit(request)
        except (ValidationError, IntegrationDisabledError) as exc:
            # Rejected before any request left the process: release the task.
            self._release_undispatched(task_id, attempt_id, principal_id, exc)
            raise
        except Exception:
            # The session may or may not exist. Expire the lease so the existing reconciliation
            # path blocks the workflow for a human instead of retrying a possibly paid session.
            self._mark_dispatch_unknown(task_id, attempt_id, principal_id)
            raise

        with self.session_factory() as session, session.begin():
            task = self._get_task(session, task_id, lock=True)
            attempt = self._devin_attempt(session, task.id)
            if attempt.id != attempt_id:
                raise ConflictError("Devin attempt changed during dispatch")
            attempt.output = attempt.output | {
                "remote": {
                    "state": RemoteRunState.QUEUED.value,
                    "session_id": remote.remote_id,
                    "url": remote.url,
                }
            }
            append_audit_event(
                session,
                workflow_id=task.workflow_id,
                event_type="devin.session_created",
                actor_id=principal_id,
                resource_type="task_attempt",
                resource_id=attempt.id,
                outcome="SUCCEEDED",
                payload={"session_id": remote.remote_id},
            )
            return self._status_dict(task, attempt)

    def sync_devin_task(self, *, task_id: str, principal_id: str) -> dict[str, Any]:
        runtime, registry = self._require_configured()
        with self.session_factory() as session:
            self._authorize(session, principal_id)
            task = self._get_task(session, task_id)
            attempt = self._devin_attempt(session, task.id)
            self._require_running(task)
            handle = self._handle(attempt)
            handoff = dict(attempt.output["handoff"])
            attempt_id = attempt.id

        status = runtime.poll(handle)
        if status.state in IN_FLIGHT_STATES:
            return self._record_observation(task_id, attempt_id, principal_id, status.state, status)
        if status.state is not RemoteRunState.SUCCEEDED:
            return self._fail_attempt(
                task_id,
                attempt_id,
                principal_id,
                DevinSessionEnded(f"Devin session ended as {status.state.value}"),
                remote_state=status.state,
            )

        try:
            claim = self._parse_claim(status.output)
        except ValidationError as exc:
            return self._reject(task_id, attempt_id, principal_id, exc)
        # A failed fetch (network, credentials, origin mismatch) changes nothing; sync again.
        fetched = self._fetch(registry, handoff, claim["branch"])
        try:
            evidence = self._verify(registry, handoff, claim, fetched, status.output)
        except (ConflictError, WorkspaceError) as exc:
            return self._reject(task_id, attempt_id, principal_id, exc)
        return self._accept_evidence(task_id, attempt_id, principal_id, handoff, evidence)

    def _reject(
        self, task_id: str, attempt_id: str, principal_id: str, exc: Exception
    ) -> dict[str, Any]:
        return self._fail_attempt(
            task_id,
            attempt_id,
            principal_id,
            DevinSessionEnded(f"Devin evidence rejected: {exc}"),
            remote_state=RemoteRunState.SUCCEEDED,
        )

    def cancel_devin_task(self, *, task_id: str, principal_id: str) -> dict[str, Any]:
        runtime, _registry = self._require_configured()
        with self.session_factory() as session:
            self._authorize(session, principal_id)
            task = self._get_task(session, task_id)
            attempt = self._devin_attempt(session, task.id)
            self._require_running(task)
            handle = self._handle(attempt)
            attempt_id = attempt.id
        if not runtime.cancel(handle):
            raise IntegrationResponseError("Devin did not confirm cancellation")
        return self._fail_attempt(
            task_id,
            attempt_id,
            principal_id,
            DevinSessionEnded("Devin session cancelled by operator"),
            remote_state=RemoteRunState.CANCELLED,
        )

    @staticmethod
    def _parse_claim(output: dict[str, object]) -> dict[str, Any]:
        claimed = output.get("structured_output")
        if not isinstance(claimed, dict) or set(claimed) != set(EVIDENCE_FIELDS):
            raise ValidationError("Devin structured output is missing or has unexpected fields")
        branch = claimed["branch"]
        revision = claimed["result_revision"]
        test_summary = claimed["test_summary"]
        summary = claimed["summary"]
        if (
            not isinstance(branch, str)
            or not REMOTE_AGENT_BRANCH.fullmatch(branch)
            or ".." in branch
            or branch.endswith(".lock")
            or not isinstance(revision, str)
            or not COMMIT_DIGEST.fullmatch(revision)
            or not isinstance(claimed["tests_passed"], bool)
            or not isinstance(test_summary, str)
            or not isinstance(summary, str)
            or not test_summary.strip()
            or not summary.strip()
            or len(test_summary) > MAX_SUMMARY_CHARS
            or len(summary) > MAX_SUMMARY_CHARS
        ):
            raise ValidationError("Devin structured output has invalid values")
        return dict(claimed)

    @staticmethod
    def _fetch(registry: RepositoryRegistry, handoff: dict[str, Any], branch: str) -> str:
        scope = str(handoff["repository_scope"])
        local_branch = str(handoff["requested_branch"])
        if registry.branch_exists(scope, local_branch):
            # An earlier sync fetched the branch but did not finish; verification decides.
            return registry.resolve_commit(scope, f"refs/heads/{local_branch}")
        return registry.fetch_remote_agent_branch(
            scope, remote_branch=branch, local_branch=local_branch
        )

    @staticmethod
    def _verify(
        registry: RepositoryRegistry,
        handoff: dict[str, Any],
        claim: dict[str, Any],
        fetched: str,
        output: dict[str, object],
    ) -> dict[str, Any]:
        revision = claim["result_revision"]
        if fetched != revision:
            raise ConflictError("reported revision is not the head of the reported branch")
        local_branch = str(handoff["requested_branch"])
        files = registry.verify_revision_evidence(
            scope_id=str(handoff["repository_scope"]),
            branch=local_branch,
            base_revision=str(handoff["base_revision"]),
            result_revision=revision,
        )
        raw_pulls = output.get("pull_requests")
        pull_requests = [
            item["pr_url"]
            for item in (raw_pulls if isinstance(raw_pulls, list) else [])
            if isinstance(item, dict) and isinstance(item.get("pr_url"), str)
        ][:10]
        return {
            "handoff_digest": handoff["digest"],
            "remote_branch": claim["branch"],
            "local_branch": local_branch,
            "result_revision": revision,
            "files_changed": list(files),
            "tests_passed_claim": claim["tests_passed"],
            "test_summary": claim["test_summary"].strip(),
            "summary": claim["summary"].strip(),
            "test_claim_authoritative": False,
            "pull_requests_reported": pull_requests,
        }

    def _accept_evidence(
        self,
        task_id: str,
        attempt_id: str,
        principal_id: str,
        handoff: dict[str, Any],
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        evidence_digest = self._digest(evidence)
        with self.session_factory() as session, session.begin():
            self._authorize(session, principal_id)
            task = self._get_task(session, task_id, lock=True)
            attempt = self._devin_attempt(session, task.id)
            if attempt.id != attempt_id or attempt.output.get("handoff") != handoff:
                raise ConflictError("Devin attempt changed before evidence finalization")
            self._require_running(task)
            workflow = self.workflow_tasks._get_workflow(session, task.workflow_id, lock=True)
            if WorkflowState(workflow.state) is not WorkflowState.IMPLEMENTING:
                raise ConflictError("workflow is no longer accepting implementation evidence")
            remote = dict(attempt.output.get("remote", {}))
            remote["state"] = RemoteRunState.SUCCEEDED.value
            attempt.output = {"handoff": handoff, "remote": remote, "evidence": evidence}
            attempt.status = TaskStatus.SUCCEEDED.value
            attempt.completed_at = datetime.now(UTC)
            task.status = TaskStatus.SUCCEEDED.value
            task.lease_owner = None
            task.lease_token = None
            task.lease_expires_at = None
            workflow.candidate_revision = evidence["result_revision"]
            session.add(
                Artifact(
                    workflow_id=workflow.id,
                    task_id=task.id,
                    attempt_id=attempt.id,
                    artifact_type="DEVIN_IMPLEMENTATION_EVIDENCE",
                    digest=evidence_digest,
                    revision=evidence["result_revision"],
                    metadata_json=evidence,
                )
            )
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="devin.evidence_accepted",
                actor_id=principal_id,
                resource_type="task_attempt",
                resource_id=attempt.id,
                outcome="SUCCEEDED",
                payload={
                    "handoff_digest": handoff["digest"],
                    "evidence_digest": evidence_digest,
                    "result_revision": evidence["result_revision"],
                },
            )
            self.workflow_tasks._advance_after_task(session, workflow, TaskKind.IMPLEMENT)
            session.flush()
            return self._status_dict(task, attempt)

    def _record_observation(
        self,
        task_id: str,
        attempt_id: str,
        principal_id: str,
        state: RemoteRunState,
        status: Any,
    ) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            task = self._get_task(session, task_id, lock=True)
            attempt = self._devin_attempt(session, task.id)
            if attempt.id != attempt_id:
                raise ConflictError("Devin attempt changed during sync")
            self._require_running(task)
            remote = dict(attempt.output.get("remote", {}))
            previous = remote.get("state")
            remote |= {"state": state.value, "raw_status": status.raw_status}
            attempt.output = attempt.output | {"remote": remote}
            # Each successful observation renews the lease; an unobserved session still expires
            # into human reconciliation.
            task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self.lease_seconds)
            if previous != state.value:
                append_audit_event(
                    session,
                    workflow_id=task.workflow_id,
                    event_type="devin.session_observed",
                    actor_id=principal_id,
                    resource_type="task_attempt",
                    resource_id=attempt.id,
                    outcome="SUCCEEDED",
                    payload={"state": state.value, "raw_status": status.raw_status},
                )
            return self._status_dict(task, attempt)

    def _fail_attempt(
        self,
        task_id: str,
        attempt_id: str,
        principal_id: str,
        exc: Exception,
        *,
        remote_state: RemoteRunState,
    ) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            task = self._get_task(session, task_id, lock=True)
            attempt = self._devin_attempt(session, task.id)
            if attempt.id != attempt_id:
                raise ConflictError("Devin attempt changed before it could be closed")
            self._require_running(task)
            workflow = self.workflow_tasks._get_workflow(session, task.workflow_id, lock=True)
            preserved = dict(attempt.output)
            self.workflow_tasks._record_attempt_failure(
                session, workflow, task, attempt, exc, worker_id=principal_id
            )
            remote = dict(preserved.get("remote", {})) | {"state": remote_state.value}
            attempt.output = preserved | {"remote": remote, "error": str(exc)[:500]}
            return self._status_dict(task, attempt)

    def _release_undispatched(
        self, task_id: str, attempt_id: str, principal_id: str, exc: Exception
    ) -> None:
        with self.session_factory() as session, session.begin():
            task = self._get_task(session, task_id, lock=True)
            attempt = session.get(TaskAttempt, attempt_id)
            if attempt is None or task.lease_owner != DEVIN_PRINCIPAL:
                return
            attempt.status = TaskStatus.FAILED.value
            attempt.error_code = type(exc).__name__
            attempt.output = attempt.output | {
                "remote": {"state": "NOT_DISPATCHED"},
                "error": str(exc)[:500],
            }
            attempt.completed_at = datetime.now(UTC)
            task.status = TaskStatus.READY.value
            task.lease_owner = None
            task.lease_token = None
            task.lease_expires_at = None
            append_audit_event(
                session,
                workflow_id=task.workflow_id,
                event_type="devin.dispatch_rejected",
                actor_id=principal_id,
                resource_type="task_attempt",
                resource_id=attempt.id,
                outcome="FAILED",
                payload={"error_code": type(exc).__name__},
            )

    def _mark_dispatch_unknown(self, task_id: str, attempt_id: str, principal_id: str) -> None:
        with self.session_factory() as session, session.begin():
            task = self._get_task(session, task_id, lock=True)
            attempt = session.get(TaskAttempt, attempt_id)
            if attempt is None or task.lease_owner != DEVIN_PRINCIPAL:
                return
            attempt.output = attempt.output | {"remote": {"state": "DISPATCH_OUTCOME_UNKNOWN"}}
            task.lease_expires_at = datetime.now(UTC)
            append_audit_event(
                session,
                workflow_id=task.workflow_id,
                event_type="devin.dispatch_outcome_unknown",
                actor_id=principal_id,
                resource_type="task_attempt",
                resource_id=attempt.id,
                outcome="UNKNOWN",
                payload={"idempotency_key": attempt.id},
            )

    def _require_configured(self) -> tuple[RemoteAgentRuntime, RepositoryRegistry]:
        if self.runtime is None:
            raise IntegrationDisabledError("Devin runtime is not activated")
        if self.repository_registry is None:
            raise IntegrationDisabledError("Devin requires a repository registry")
        return self.runtime, self.repository_registry

    def _authorize(self, session: Session, principal_id: str) -> None:
        self.policy.authorize(
            session, principal_id, Capability.DISPATCH_REMOTE_AGENT, require_human=True
        )

    @staticmethod
    def _get_task(session: Session, task_id: str, *, lock: bool = False) -> Task:
        statement = select(Task).where(Task.id == task_id)
        task = session.scalar(statement.with_for_update() if lock else statement)
        if task is None:
            raise NotFoundError("task not found")
        return task

    @staticmethod
    def _validate_dispatchable(workflow: Workflow, task: Task) -> None:
        if TaskKind(task.kind) is not TaskKind.IMPLEMENT:
            raise ConflictError("Devin may be dispatched only for implementation tasks")
        if task.status != TaskStatus.READY.value:
            raise ConflictError("task is not ready for dispatch")
        if WorkflowState(workflow.state) is not WorkflowState.IMPLEMENTING:
            raise ConflictError("workflow is not accepting implementation work")
        if workflow.repository_scope is None:
            raise ConflictError("Devin dispatch requires a registered repository scope")
        if workflow.containment_required:
            raise ConflictError("contained workflows cannot be sent to a remote agent")

    @staticmethod
    def _require_running(task: Task) -> None:
        if task.status != TaskStatus.RUNNING.value or task.lease_owner != DEVIN_PRINCIPAL:
            raise ConflictError("task does not have a running Devin session")
        expires_at = task.lease_expires_at
        if expires_at is None:
            raise ConflictError("Devin task lease has no expiry")
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            raise ConflictError("Devin task lease has expired; reconcile the workflow")

    @staticmethod
    def _devin_attempt(session: Session, task_id: str) -> TaskAttempt:
        attempt = session.scalar(
            select(TaskAttempt)
            .where(TaskAttempt.task_id == task_id, TaskAttempt.provider == DEVIN_PROVIDER)
            .order_by(TaskAttempt.attempt_number.desc())
            .limit(1)
        )
        if attempt is None:
            raise ConflictError("task has no Devin attempt")
        return attempt

    @staticmethod
    def _handle(attempt: TaskAttempt) -> RemoteAgentHandle:
        remote = attempt.output.get("remote")
        session_id = remote.get("session_id") if isinstance(remote, dict) else None
        if not isinstance(session_id, str):
            raise ConflictError("Devin session was not confirmed; reconcile the workflow")
        url = remote.get("url") if isinstance(remote, dict) else None
        return RemoteAgentHandle(
            provider=DEVIN_PROVIDER,
            remote_id=session_id,
            url=url if isinstance(url, str) else None,
        )

    @staticmethod
    def _prompt(handoff: dict[str, Any]) -> str:
        paths = ", ".join(handoff["writable_paths"])
        return (
            f"{handoff['objective']}\n\n"
            "Constraints from the Eldridge control plane:\n"
            f"- Start from commit {handoff['base_revision']} of {handoff['remote_repository']}.\n"
            f"- Push your work to a new branch, preferably {handoff['requested_branch']}.\n"
            f"- Change files only under: {paths}.\n"
            "- Do not merge, force-push, change CI configuration, or touch other branches.\n"
            "- A draft pull request is welcome; it will be independently reviewed and tested.\n"
            "- Finish by returning the structured output: the branch you pushed, its full "
            "40-character head commit, whether tests passed, a test summary, and a summary."
        )

    @staticmethod
    def _status_dict(task: Task, attempt: TaskAttempt) -> dict[str, Any]:
        remote = attempt.output.get("remote", {})
        evidence = attempt.output.get("evidence")
        return {
            "task_id": task.id,
            "workflow_id": task.workflow_id,
            "task_status": task.status,
            "attempt_id": attempt.id,
            "attempt_status": attempt.status,
            "remote": remote,
            "evidence": evidence,
            "lease_expires_at": (
                task.lease_expires_at.isoformat() if task.lease_expires_at else None
            ),
        }

    @staticmethod
    def _digest(value: Any) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return sha256(encoded).hexdigest()
