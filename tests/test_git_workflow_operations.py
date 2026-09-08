from typing import Any

import pytest

from control_plane.domain import (
    ApprovalAction,
    ApprovalDecision,
    AuthorizationError,
    ConflictError,
    IntegrationResponseError,
)
from control_plane.github_app import (
    GitHubRepositoryPolicy,
    MergeConfirmation,
    MergeReadiness,
    PullRequestProposal,
)
from control_plane.persistence import Workflow
from control_plane.service import ControlPlaneService

REVISION = "a" * 40


class FakeGitHubApp:
    def __init__(self) -> None:
        self.create_calls = 0
        self.readiness_calls = 0
        self.create_error = False
        self.confirm_error = False
        self.confirm_calls = 0
        self.remote_pulls: tuple[PullRequestProposal, ...] = ()
        self.policy = type("Policy", (), {"policy_version": "github-app/test-v1"})()

    def repository_policy(self, repository: str) -> GitHubRepositoryPolicy:
        assert repository == "owner/repository"
        return GitHubRepositoryPolicy(
            full_name=repository,
            base_branch="main",
            required_checks=("test", "security"),
        )

    def create_draft_pull_request(self, **_kwargs: Any) -> PullRequestProposal:
        self.create_calls += 1
        if self.create_error:
            raise IntegrationResponseError("outcome unknown")
        return _pull()

    def validate_pull_request(self, **kwargs: Any) -> GitHubRepositoryPolicy:
        return self.repository_policy(str(kwargs["repository"]))

    def find_pull_requests(self, **_kwargs: Any) -> tuple[PullRequestProposal, ...]:
        return self.remote_pulls

    def assess_merge_readiness(self, **_kwargs: Any) -> MergeReadiness:
        self.readiness_calls += 1
        return MergeReadiness(
            repository="owner/repository",
            pull_number=42,
            revision=REVISION,
            ready=True,
            reasons=(),
            checks={"security": "success", "test": "success"},
            policy_version="github-app/test-v1",
        )

    def confirm_pull_request_merged(self, **_kwargs: Any) -> MergeConfirmation:
        self.confirm_calls += 1
        if self.confirm_error:
            raise IntegrationResponseError("merge was not confirmed")
        return MergeConfirmation(
            repository="owner/repository",
            pull_number=42,
            head_revision=REVISION,
            base_branch="main",
            merge_commit_revision="b" * 40,
        )


def _pull() -> PullRequestProposal:
    return PullRequestProposal(
        repository="owner/repository",
        number=42,
        url="https://github.com/owner/repository/pull/42",
        head_branch="codex/task-1",
        base_branch="main",
        head_revision=REVISION,
        draft=True,
    )


def _service(session_factory: Any, github: FakeGitHubApp) -> ControlPlaneService:
    service = ControlPlaneService(session_factory, github_app=github)  # type: ignore[arg-type]
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Durable Git operation",
        description="Persist and reconcile a GitHub operation.",
        idempotency_key="durable-git-workflow",
        repository_scope="owner/repository",
    )
    with session_factory() as session, session.begin():
        stored = session.get(Workflow, workflow["id"])
        assert stored is not None
        stored.state = "AWAITING_HUMAN_APPROVAL"
        stored.candidate_revision = REVISION
    service.test_workflow_id = workflow["id"]  # type: ignore[attr-defined]
    return service


def test_pull_request_and_readiness_are_durable_and_idempotent(session_factory: Any) -> None:
    github = FakeGitHubApp()
    service = _service(session_factory, github)
    workflow_id = service.test_workflow_id  # type: ignore[attr-defined]

    proposal = service.propose_pull_request(
        workflow_id=workflow_id,
        actor_id="dev-operator",
        head_branch="codex/task-1",
        title="Controlled change",
        body="Evidence is revision-bound.",
        idempotency_key="proposal-idempotency",
    )
    replay = service.propose_pull_request(
        workflow_id=workflow_id,
        actor_id="dev-operator",
        head_branch="codex/task-1",
        title="Controlled change",
        body="Evidence is revision-bound.",
        idempotency_key="proposal-idempotency",
    )
    readiness = service.assess_merge_readiness(
        workflow_id=workflow_id,
        proposal_id=proposal["id"],
        actor_id="dev-operator",
        idempotency_key="readiness-idempotency",
    )
    readiness_replay = service.assess_merge_readiness(
        workflow_id=workflow_id,
        proposal_id=proposal["id"],
        actor_id="dev-operator",
        idempotency_key="readiness-idempotency",
    )

    assert proposal["status"] == "SUCCEEDED"
    assert replay["id"] == proposal["id"]
    assert github.create_calls == 1
    assert readiness["ready"] is True
    assert readiness_replay["id"] == readiness["id"]
    assert github.readiness_calls == 1
    assert service.get_workflow(workflow_id, principal_id="dev-operator")["state"] == (
        "AWAITING_HUMAN_APPROVAL"
    )


def test_unknown_creation_requires_read_only_reconciliation(session_factory: Any) -> None:
    github = FakeGitHubApp()
    github.create_error = True
    service = _service(session_factory, github)
    workflow_id = service.test_workflow_id  # type: ignore[attr-defined]

    with pytest.raises(IntegrationResponseError):
        service.propose_pull_request(
            workflow_id=workflow_id,
            actor_id="dev-operator",
            head_branch="codex/task-1",
            title="Controlled change",
            body="Body",
            idempotency_key="unknown-proposal",
        )
    records = service.list_pull_request_proposals(workflow_id, principal_id="dev-operator")
    assert records[0]["status"] == "UNKNOWN"

    github.remote_pulls = (_pull(),)
    reconciled = service.reconcile_pull_request(
        workflow_id=workflow_id,
        proposal_id=records[0]["id"],
        actor_id="dev-operator",
    )
    assert reconciled["status"] == "SUCCEEDED"
    assert reconciled["pull_number"] == 42


def test_agent_cannot_propose_or_reconcile_pull_request(session_factory: Any) -> None:
    github = FakeGitHubApp()
    service = _service(session_factory, github)
    workflow_id = service.test_workflow_id  # type: ignore[attr-defined]
    with pytest.raises(AuthorizationError):
        service.propose_pull_request(
            workflow_id=workflow_id,
            actor_id="implementer-agent",
            head_branch="codex/task-1",
            title="Unauthorized",
            body="Body",
            idempotency_key="unauthorized-proposal",
        )
    assert github.create_calls == 0


def test_changed_idempotent_request_is_rejected(session_factory: Any) -> None:
    github = FakeGitHubApp()
    service = _service(session_factory, github)
    workflow_id = service.test_workflow_id  # type: ignore[attr-defined]
    service.propose_pull_request(
        workflow_id=workflow_id,
        actor_id="dev-operator",
        head_branch="codex/task-1",
        title="Original",
        body="Body",
        idempotency_key="reused-proposal-key",
    )
    with pytest.raises(ConflictError, match="reused"):
        service.propose_pull_request(
            workflow_id=workflow_id,
            actor_id="dev-operator",
            head_branch="codex/task-1",
            title="Altered",
            body="Body",
            idempotency_key="reused-proposal-key",
        )


def _approved_merge_evidence(
    service: ControlPlaneService, workflow_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    proposal = service.propose_pull_request(
        workflow_id=workflow_id,
        actor_id="dev-operator",
        head_branch="codex/task-1",
        title="Controlled change",
        body="Evidence is revision-bound.",
        idempotency_key="merge-proposal-key",
    )
    readiness = service.assess_merge_readiness(
        workflow_id=workflow_id,
        proposal_id=proposal["id"],
        actor_id="dev-operator",
        idempotency_key="merge-readiness-key",
    )
    service.approve(
        workflow_id=workflow_id,
        approver_id="dev-operator",
        action=ApprovalAction.MERGE,
        target=proposal["pull_url"],
        revision=REVISION,
        decision=ApprovalDecision.APPROVED,
        rationale="All exact-revision evidence is satisfactory.",
    )
    return proposal, readiness


def test_human_can_confirm_external_merge_without_merge_authority(session_factory: Any) -> None:
    github = FakeGitHubApp()
    service = _service(session_factory, github)
    workflow_id = service.test_workflow_id  # type: ignore[attr-defined]
    proposal, readiness = _approved_merge_evidence(service, workflow_id)

    confirmation = service.confirm_pull_request_merged(
        workflow_id=workflow_id,
        proposal_id=proposal["id"],
        assessment_id=readiness["id"],
        actor_id="dev-operator",
        idempotency_key="merge-confirm-key",
    )
    replay = service.confirm_pull_request_merged(
        workflow_id=workflow_id,
        proposal_id=proposal["id"],
        assessment_id=readiness["id"],
        actor_id="dev-operator",
        idempotency_key="merge-confirm-key",
    )

    assert confirmation["status"] == "SUCCEEDED"
    assert confirmation["merge_commit_revision"] == "b" * 40
    assert replay["id"] == confirmation["id"]
    assert github.confirm_calls == 1
    assert service.get_workflow(workflow_id, principal_id="dev-operator")["state"] == "MERGED"


def test_failed_merge_confirmation_is_durable_and_does_not_advance_state(
    session_factory: Any,
) -> None:
    github = FakeGitHubApp()
    service = _service(session_factory, github)
    workflow_id = service.test_workflow_id  # type: ignore[attr-defined]
    proposal, readiness = _approved_merge_evidence(service, workflow_id)
    github.confirm_error = True

    with pytest.raises(IntegrationResponseError):
        service.confirm_pull_request_merged(
            workflow_id=workflow_id,
            proposal_id=proposal["id"],
            assessment_id=readiness["id"],
            actor_id="dev-operator",
            idempotency_key="failed-confirm-key",
        )

    confirmations = service.list_merge_confirmations(workflow_id, principal_id="dev-operator")
    assert confirmations[0]["status"] == "FAILED"
    assert service.get_workflow(workflow_id, principal_id="dev-operator")["state"] == "APPROVED"


def test_agent_cannot_confirm_merge(session_factory: Any) -> None:
    github = FakeGitHubApp()
    service = _service(session_factory, github)
    workflow_id = service.test_workflow_id  # type: ignore[attr-defined]
    proposal, readiness = _approved_merge_evidence(service, workflow_id)
    with pytest.raises(AuthorizationError):
        service.confirm_pull_request_merged(
            workflow_id=workflow_id,
            proposal_id=proposal["id"],
            assessment_id=readiness["id"],
            actor_id="implementer-agent",
            idempotency_key="agent-confirm-key",
        )
    assert github.confirm_calls == 0
