from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from control_plane.audit import append_audit_event
from control_plane.domain import (
    ApprovalAction,
    ApprovalDecision,
    Capability,
    ConflictError,
    IntegrationDisabledError,
    InvalidTransitionError,
    NotFoundError,
    ValidationError,
    WorkflowState,
)
from control_plane.github_app import (
    GitHubAppClient,
    MergeConfirmation,
    MergeReadiness,
    PullRequestProposal,
)
from control_plane.persistence import (
    Approval,
    CiCheckEvidence,
    MergeConfirmationRecord,
    MergeReadinessAssessmentRecord,
    PullRequestProposalRecord,
    Workflow,
)
from control_plane.policy import PolicyEngine


class GitPullRequestService:
    """GitHub CI evidence, pull-request, readiness, and merge-confirmation boundary."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        policy: PolicyEngine,
        *,
        github_app: GitHubAppClient | None,
        transition: Callable[..., None],
    ) -> None:
        self.session_factory = session_factory
        self.policy = policy
        self.github_app = github_app
        self._transition = transition

    def list_ci_check_evidence(
        self, workflow_id: str, *, principal_id: str
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_AUDIT)
            self._get_workflow(session, workflow_id)
            checks = session.scalars(
                select(CiCheckEvidence)
                .where(CiCheckEvidence.workflow_id == workflow_id)
                .order_by(CiCheckEvidence.created_at, CiCheckEvidence.id)
            ).all()
            return [self._ci_check_dict(check) for check in checks]

    def ingest_ci_check_evidence(
        self,
        *,
        actor_id: str,
        delivery_id: str,
        repository: str,
        check_run_id: str,
        check_name: str,
        revision: str,
        status: str,
        conclusion: str,
        details_url: str | None,
        app_slug: str | None,
        payload_digest: str,
    ) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, actor_id, Capability.SUBMIT_CI_EVIDENCE)
            existing_delivery = session.scalar(
                select(CiCheckEvidence).where(
                    CiCheckEvidence.source == "github",
                    CiCheckEvidence.delivery_id == delivery_id,
                )
            )
            if existing_delivery is not None:
                if existing_delivery.payload_digest != payload_digest:
                    raise ConflictError("CI delivery ID was reused with a different payload")
                return {**self._ci_check_dict(existing_delivery), "replayed": True}

            workflows = session.scalars(
                select(Workflow)
                .where(
                    Workflow.repository_scope == repository,
                    Workflow.candidate_revision == revision,
                )
                .with_for_update()
            ).all()
            if not workflows:
                raise NotFoundError("CI evidence does not match a workflow candidate revision")
            if len(workflows) != 1:
                raise ConflictError("CI evidence matches multiple workflows")
            workflow = workflows[0]

            existing_run = session.scalar(
                select(CiCheckEvidence).where(
                    CiCheckEvidence.source == "github",
                    CiCheckEvidence.check_run_id == check_run_id,
                )
            )
            if existing_run is not None:
                raise ConflictError("CI check run was already recorded by another delivery")

            evidence = CiCheckEvidence(
                workflow_id=workflow.id,
                source="github",
                delivery_id=delivery_id,
                repository=repository,
                check_run_id=check_run_id,
                check_name=check_name,
                revision=revision,
                status=status,
                conclusion=conclusion,
                details_url=details_url,
                app_slug=app_slug,
                payload_digest=payload_digest,
            )
            session.add(evidence)
            session.flush()
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="ci.check_recorded",
                actor_id=actor_id,
                resource_type="ci_check",
                resource_id=evidence.id,
                outcome="SUCCEEDED",
                payload={
                    "source": evidence.source,
                    "repository": repository,
                    "check_run_id": check_run_id,
                    "check_name": check_name,
                    "revision": revision,
                    "status": status,
                    "conclusion": conclusion,
                    "payload_digest": payload_digest,
                    "gate_eligible": False,
                },
            )
            session.flush()
            return {**self._ci_check_dict(evidence), "replayed": False}

    def propose_pull_request(
        self,
        *,
        workflow_id: str,
        actor_id: str,
        head_branch: str,
        title: str,
        body: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        github = self._require_github_app()
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, actor_id, Capability.PROPOSE_PULL_REQUEST)
            workflow = self._get_workflow(session, workflow_id, lock=True)
            if WorkflowState(workflow.state) != WorkflowState.AWAITING_HUMAN_APPROVAL:
                raise InvalidTransitionError("workflow is not ready for a pull-request proposal")
            if not workflow.repository_scope or not workflow.candidate_revision:
                raise ValidationError("workflow lacks repository and candidate revision")
            repo_policy = github.validate_pull_request(
                repository=workflow.repository_scope,
                head_branch=head_branch,
                expected_revision=workflow.candidate_revision,
                title=title,
                body=body,
            )
            request_digest = self._digest(
                {
                    "workflow_id": workflow_id,
                    "repository": workflow.repository_scope,
                    "revision": workflow.candidate_revision,
                    "head_branch": head_branch,
                    "title": title,
                    "body": body,
                }
            )
            existing = session.scalar(
                select(PullRequestProposalRecord).where(
                    PullRequestProposalRecord.actor_id == actor_id,
                    PullRequestProposalRecord.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError("pull-request idempotency key was reused")
                if existing.status == "SUCCEEDED":
                    return self._pull_request_record_dict(existing)
                raise ConflictError(f"pull-request proposal requires reconciliation: {existing.id}")
            record = PullRequestProposalRecord(
                workflow_id=workflow.id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                repository=workflow.repository_scope,
                head_branch=head_branch,
                base_branch=repo_policy.base_branch,
                revision=workflow.candidate_revision,
                title=title.strip(),
                status="RUNNING",
            )
            session.add(record)
            session.flush()
            record_id = record.id
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="git.pull_request_requested",
                actor_id=actor_id,
                resource_type="pull_request_proposal",
                resource_id=record.id,
                outcome="STARTED",
                payload={"revision": record.revision, "head_branch": head_branch},
            )

        try:
            proposal = github.create_draft_pull_request(
                repository=record.repository,
                head_branch=head_branch,
                expected_revision=record.revision,
                title=title,
                body=body,
            )
        except Exception as exc:
            self._mark_pull_request_unknown(record_id, type(exc).__name__)
            raise
        return self._complete_pull_request(record_id, proposal)

    def reconcile_pull_request(
        self, *, workflow_id: str, proposal_id: str, actor_id: str
    ) -> dict[str, Any]:
        github = self._require_github_app()
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, actor_id, Capability.RECONCILE_GIT_OPERATION)
            record = session.get(PullRequestProposalRecord, proposal_id)
            if record is None or record.workflow_id != workflow_id:
                raise NotFoundError("pull-request proposal was not found")
            if record.status == "SUCCEEDED":
                return self._pull_request_record_dict(record)
            repository, branch, revision, base = (
                record.repository,
                record.head_branch,
                record.revision,
                record.base_branch,
            )
        matches = [
            item
            for item in github.find_pull_requests(repository=repository, head_branch=branch)
            if item.head_revision == revision and item.base_branch == base
        ]
        if len(matches) > 1:
            raise ConflictError("multiple remote pull requests match the proposal")
        if matches:
            return self._complete_pull_request(proposal_id, matches[0], reconciled=True)
        with self.session_factory() as session, session.begin():
            record = session.get(PullRequestProposalRecord, proposal_id, with_for_update=True)
            assert record is not None
            record.status = "FAILED"
            record.error_code = "RemotePullRequestNotFound"
            record.completed_at = datetime.now(UTC)
            append_audit_event(
                session,
                workflow_id=record.workflow_id,
                event_type="git.pull_request_reconciled",
                actor_id=actor_id,
                resource_type="pull_request_proposal",
                resource_id=record.id,
                outcome="FAILED",
                payload={"revision": record.revision, "remote_match_count": 0},
            )
            return self._pull_request_record_dict(record)

    def assess_merge_readiness(
        self,
        *,
        workflow_id: str,
        proposal_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        github = self._require_github_app()
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, actor_id, Capability.ASSESS_MERGE_READINESS)
            workflow = self._get_workflow(session, workflow_id)
            proposal = session.get(PullRequestProposalRecord, proposal_id)
            if proposal is None or proposal.workflow_id != workflow_id:
                raise NotFoundError("pull-request proposal was not found")
            if proposal.status != "SUCCEEDED" or proposal.pull_number is None:
                raise ConflictError("pull-request proposal is not confirmed")
            if workflow.candidate_revision != proposal.revision:
                raise ConflictError("pull-request proposal revision is stale")
            existing = session.scalar(
                select(MergeReadinessAssessmentRecord).where(
                    MergeReadinessAssessmentRecord.actor_id == actor_id,
                    MergeReadinessAssessmentRecord.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if (
                    existing.workflow_id != workflow_id
                    or existing.proposal_id != proposal_id
                    or existing.revision != proposal.revision
                ):
                    raise ConflictError("readiness idempotency key was reused")
                if existing.status == "SUCCEEDED":
                    return self._merge_readiness_dict(existing)
                raise ConflictError("readiness assessment requires a new idempotency key")
            repository, pull_number, revision = (
                proposal.repository,
                proposal.pull_number,
                proposal.revision,
            )
            assessment = MergeReadinessAssessmentRecord(
                workflow_id=workflow_id,
                proposal_id=proposal_id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                revision=revision,
                status="RUNNING",
                ready=None,
                reasons=[],
                checks={},
                policy_version=github.policy.policy_version,
            )
            session.add(assessment)
            session.flush()
            assessment_id = assessment.id
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="git.merge_readiness_requested",
                actor_id=actor_id,
                resource_type="merge_readiness",
                resource_id=assessment.id,
                outcome="STARTED",
                payload={"revision": revision, "pull_number": pull_number},
            )
        try:
            result = github.assess_merge_readiness(
                repository=repository,
                pull_number=pull_number,
                expected_revision=revision,
            )
        except Exception as exc:
            self._mark_readiness_failed(assessment_id, type(exc).__name__)
            raise
        return self._record_merge_readiness(assessment_id, result)

    def list_pull_request_proposals(
        self, workflow_id: str, *, principal_id: str
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_AUDIT)
            self._get_workflow(session, workflow_id)
            records = session.scalars(
                select(PullRequestProposalRecord)
                .where(PullRequestProposalRecord.workflow_id == workflow_id)
                .order_by(PullRequestProposalRecord.created_at)
            ).all()
            return [self._pull_request_record_dict(item) for item in records]

    def confirm_pull_request_merged(
        self,
        *,
        workflow_id: str,
        proposal_id: str,
        assessment_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        github = self._require_github_app()
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session, actor_id, Capability.CONFIRM_GIT_MERGE, require_human=True
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            if WorkflowState(workflow.state) == WorkflowState.MERGED:
                existing = session.scalar(
                    select(MergeConfirmationRecord).where(
                        MergeConfirmationRecord.actor_id == actor_id,
                        MergeConfirmationRecord.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None and existing.workflow_id == workflow_id:
                    return self._merge_confirmation_dict(existing)
                raise InvalidTransitionError("workflow merge was already confirmed")
            if WorkflowState(workflow.state) != WorkflowState.APPROVED:
                raise InvalidTransitionError("workflow lacks exact human merge approval")
            proposal = session.get(PullRequestProposalRecord, proposal_id)
            assessment = session.get(MergeReadinessAssessmentRecord, assessment_id)
            if (
                proposal is None
                or proposal.workflow_id != workflow_id
                or proposal.status != "SUCCEEDED"
                or proposal.pull_number is None
            ):
                raise ConflictError("pull-request proposal is not confirmed")
            if (
                assessment is None
                or assessment.workflow_id != workflow_id
                or assessment.proposal_id != proposal_id
                or assessment.status != "SUCCEEDED"
                or assessment.ready is not True
            ):
                raise ConflictError("successful merge-readiness evidence is required")
            if not workflow.candidate_revision or not (
                proposal.revision == assessment.revision == workflow.candidate_revision
            ):
                raise ConflictError("merge evidence is stale")
            approval = session.scalar(
                select(Approval)
                .where(
                    Approval.workflow_id == workflow_id,
                    Approval.action == ApprovalAction.MERGE.value,
                    Approval.decision == ApprovalDecision.APPROVED.value,
                    Approval.revision == workflow.candidate_revision,
                    Approval.policy_version == workflow.policy_version,
                    Approval.consumed_at.is_not(None),
                )
                .order_by(Approval.created_at.desc())
                .limit(1)
            )
            if approval is None:
                raise ConflictError("consumed human approval evidence is missing")
            existing = session.scalar(
                select(MergeConfirmationRecord).where(
                    MergeConfirmationRecord.actor_id == actor_id,
                    MergeConfirmationRecord.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if (
                    existing.workflow_id != workflow_id
                    or existing.proposal_id != proposal_id
                    or existing.assessment_id != assessment_id
                    or existing.revision != workflow.candidate_revision
                ):
                    raise ConflictError("merge-confirmation idempotency key was reused")
                if existing.status == "SUCCEEDED":
                    return self._merge_confirmation_dict(existing)
                raise ConflictError("merge confirmation requires a new idempotency key")
            record = MergeConfirmationRecord(
                workflow_id=workflow_id,
                proposal_id=proposal_id,
                assessment_id=assessment_id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                revision=workflow.candidate_revision,
                status="RUNNING",
            )
            session.add(record)
            session.flush()
            record_id = record.id
            repository, pull_number, revision = (
                proposal.repository,
                proposal.pull_number,
                proposal.revision,
            )
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="git.merge_confirmation_requested",
                actor_id=actor_id,
                resource_type="merge_confirmation",
                resource_id=record.id,
                outcome="STARTED",
                payload={"revision": revision, "pull_number": pull_number},
            )
        try:
            result = github.confirm_pull_request_merged(
                repository=repository,
                pull_number=pull_number,
                expected_revision=revision,
            )
        except Exception as exc:
            self._mark_merge_confirmation_failed(record_id, type(exc).__name__)
            raise
        try:
            return self._complete_merge_confirmation(record_id, result)
        except Exception as exc:
            self._mark_merge_confirmation_failed(record_id, type(exc).__name__)
            raise

    def list_merge_confirmations(
        self, workflow_id: str, *, principal_id: str
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_AUDIT)
            self._get_workflow(session, workflow_id)
            records = session.scalars(
                select(MergeConfirmationRecord)
                .where(MergeConfirmationRecord.workflow_id == workflow_id)
                .order_by(MergeConfirmationRecord.created_at)
            ).all()
            return [self._merge_confirmation_dict(item) for item in records]

    @staticmethod
    def _ci_check_dict(check: CiCheckEvidence) -> dict[str, Any]:
        return {
            "id": check.id,
            "workflow_id": check.workflow_id,
            "source": check.source,
            "delivery_id": check.delivery_id,
            "repository": check.repository,
            "check_run_id": check.check_run_id,
            "check_name": check.check_name,
            "revision": check.revision,
            "status": check.status,
            "conclusion": check.conclusion,
            "details_url": check.details_url,
            "app_slug": check.app_slug,
            "payload_digest": check.payload_digest,
            "created_at": check.created_at.isoformat(),
        }

    def _require_github_app(self) -> GitHubAppClient:
        if self.github_app is None:
            raise IntegrationDisabledError("GitHub App integration is disabled")
        return self.github_app

    def _mark_pull_request_unknown(self, record_id: str, error_code: str) -> None:
        with self.session_factory() as session, session.begin():
            record = session.get(PullRequestProposalRecord, record_id, with_for_update=True)
            if record is None or record.status != "RUNNING":
                return
            record.status = "UNKNOWN"
            record.error_code = error_code[:64]
            record.completed_at = datetime.now(UTC)
            append_audit_event(
                session,
                workflow_id=record.workflow_id,
                event_type="git.pull_request_outcome_unknown",
                actor_id="orchestrator",
                resource_type="pull_request_proposal",
                resource_id=record.id,
                outcome="UNKNOWN",
                payload={"revision": record.revision, "error_code": record.error_code},
            )

    def _complete_pull_request(
        self,
        record_id: str,
        proposal: PullRequestProposal,
        *,
        reconciled: bool = False,
    ) -> dict[str, Any]:
        stale = False
        with self.session_factory() as session, session.begin():
            record = session.get(PullRequestProposalRecord, record_id, with_for_update=True)
            if record is None:
                raise NotFoundError("pull-request proposal was not found")
            workflow = self._get_workflow(session, record.workflow_id, lock=True)
            if workflow.candidate_revision != record.revision:
                record.status = "STALE"
                record.pull_number = proposal.number
                record.pull_url = proposal.url
                record.error_code = "CandidateRevisionChanged"
                record.completed_at = datetime.now(UTC)
                stale = True
            else:
                record.status = "SUCCEEDED"
                record.pull_number = proposal.number
                record.pull_url = proposal.url
                record.error_code = None
                record.completed_at = datetime.now(UTC)
            append_audit_event(
                session,
                workflow_id=record.workflow_id,
                event_type=(
                    "git.pull_request_reconciled" if reconciled else "git.pull_request_created"
                ),
                actor_id="orchestrator",
                resource_type="pull_request_proposal",
                resource_id=record.id,
                outcome="STALE" if stale else "SUCCEEDED",
                payload={
                    "revision": record.revision,
                    "pull_number": proposal.number,
                    "draft": proposal.draft,
                    "reconciled": reconciled,
                },
            )
            result = self._pull_request_record_dict(record)
        if stale:
            raise ConflictError("candidate revision changed during pull-request creation")
        return result

    def _record_merge_readiness(
        self,
        assessment_id: str,
        result: MergeReadiness,
    ) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            record = session.get(
                MergeReadinessAssessmentRecord, assessment_id, with_for_update=True
            )
            if record is None:
                raise NotFoundError("readiness assessment was not found")
            workflow_id = record.workflow_id
            workflow = self._get_workflow(session, workflow_id, lock=True)
            if workflow.candidate_revision != result.revision:
                raise ConflictError("candidate revision changed during readiness assessment")
            record.status = "SUCCEEDED"
            record.ready = result.ready
            record.reasons = list(result.reasons)
            record.checks = result.checks
            record.policy_version = result.policy_version
            record.completed_at = datetime.now(UTC)
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="git.merge_readiness_assessed",
                actor_id=record.actor_id,
                resource_type="merge_readiness",
                resource_id=record.id,
                outcome="SUCCEEDED" if result.ready else "BLOCKED",
                payload={
                    "revision": result.revision,
                    "ready": result.ready,
                    "reasons": list(result.reasons),
                    "policy_version": result.policy_version,
                },
            )
            return self._merge_readiness_dict(record)

    def _mark_readiness_failed(self, assessment_id: str, error_code: str) -> None:
        with self.session_factory() as session, session.begin():
            record = session.get(
                MergeReadinessAssessmentRecord, assessment_id, with_for_update=True
            )
            if record is None or record.status != "RUNNING":
                return
            record.status = "FAILED"
            record.error_code = error_code[:64]
            record.completed_at = datetime.now(UTC)
            append_audit_event(
                session,
                workflow_id=record.workflow_id,
                event_type="git.merge_readiness_failed",
                actor_id="orchestrator",
                resource_type="merge_readiness",
                resource_id=record.id,
                outcome="FAILED",
                payload={"revision": record.revision, "error_code": record.error_code},
            )

    def _complete_merge_confirmation(
        self, record_id: str, result: MergeConfirmation
    ) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            record = session.get(MergeConfirmationRecord, record_id, with_for_update=True)
            if record is None or record.status != "RUNNING":
                raise ConflictError("merge confirmation is not pending")
            workflow = self._get_workflow(session, record.workflow_id, lock=True)
            proposal = session.get(PullRequestProposalRecord, record.proposal_id)
            if proposal is None or proposal.pull_number is None:
                raise ConflictError("pull-request proposal disappeared")
            if (
                WorkflowState(workflow.state) != WorkflowState.APPROVED
                or workflow.candidate_revision != record.revision
                or result.repository != proposal.repository
                or result.pull_number != proposal.pull_number
                or result.head_revision != record.revision
                or result.base_branch != proposal.base_branch
            ):
                raise ConflictError("merge confirmation no longer matches the approved workflow")
            record.status = "SUCCEEDED"
            record.merge_commit_revision = result.merge_commit_revision
            record.completed_at = datetime.now(UTC)
            workflow.merged_revision = result.merge_commit_revision
            self._transition(
                session,
                workflow,
                WorkflowState.MERGED,
                actor_id=record.actor_id,
                reason="GitHub independently confirmed the protected pull-request merge",
                extra={
                    "proposal_id": record.proposal_id,
                    "assessment_id": record.assessment_id,
                    "pull_number": result.pull_number,
                    "revision": record.revision,
                    "merge_commit_revision": result.merge_commit_revision,
                },
            )
            append_audit_event(
                session,
                workflow_id=record.workflow_id,
                event_type="git.merge_confirmed",
                actor_id=record.actor_id,
                resource_type="merge_confirmation",
                resource_id=record.id,
                outcome="SUCCEEDED",
                payload={
                    "revision": record.revision,
                    "merge_commit_revision": result.merge_commit_revision,
                },
            )
            return self._merge_confirmation_dict(record)

    def _mark_merge_confirmation_failed(self, record_id: str, error_code: str) -> None:
        with self.session_factory() as session, session.begin():
            record = session.get(MergeConfirmationRecord, record_id, with_for_update=True)
            if record is None or record.status != "RUNNING":
                return
            record.status = "FAILED"
            record.error_code = error_code[:64]
            record.completed_at = datetime.now(UTC)
            append_audit_event(
                session,
                workflow_id=record.workflow_id,
                event_type="git.merge_confirmation_failed",
                actor_id=record.actor_id,
                resource_type="merge_confirmation",
                resource_id=record.id,
                outcome="FAILED",
                payload={"revision": record.revision, "error_code": record.error_code},
            )

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
    def _pull_request_record_dict(record: PullRequestProposalRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "workflow_id": record.workflow_id,
            "repository": record.repository,
            "head_branch": record.head_branch,
            "base_branch": record.base_branch,
            "revision": record.revision,
            "status": record.status,
            "pull_number": record.pull_number,
            "pull_url": record.pull_url,
            "error_code": record.error_code,
            "created_at": record.created_at.isoformat(),
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        }

    @staticmethod
    def _merge_readiness_dict(record: MergeReadinessAssessmentRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "workflow_id": record.workflow_id,
            "proposal_id": record.proposal_id,
            "revision": record.revision,
            "status": record.status,
            "ready": record.ready,
            "reasons": record.reasons,
            "checks": record.checks,
            "policy_version": record.policy_version,
            "error_code": record.error_code,
            "created_at": record.created_at.isoformat(),
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        }

    @staticmethod
    def _merge_confirmation_dict(record: MergeConfirmationRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "workflow_id": record.workflow_id,
            "proposal_id": record.proposal_id,
            "assessment_id": record.assessment_id,
            "revision": record.revision,
            "status": record.status,
            "merge_commit_revision": record.merge_commit_revision,
            "error_code": record.error_code,
            "created_at": record.created_at.isoformat(),
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        }
