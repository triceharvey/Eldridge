from __future__ import annotations

from unittest.mock import Mock

from control_plane.deployment import EnvironmentClassification
from control_plane.domain import (
    ApprovalAction,
    ApprovalDecision,
    DispositionDecision,
    ReconciliationDecision,
)
from control_plane.evaluation import (
    EvaluationReconciliationDecision,
    EvaluationRecoveryDecision,
    PromptVariant,
)
from control_plane.routing import DataClassification, RiskLevel, WorkCapability
from control_plane.service import ControlPlaneService
from control_plane.task_strategy import ComplexityTier, InspectionSignal


def test_evaluation_lifecycle_commands_delegate_without_changing_the_facade(
    service: ControlPlaneService,
) -> None:
    lifecycle = Mock()
    lifecycle.plan_evaluation_repair.return_value = {"command": "repair"}
    lifecycle.recover_evaluation_assessment.return_value = {"command": "recover"}
    lifecycle.promote_evaluation_winner.return_value = {"command": "promote"}
    service.evaluation_lifecycle = lifecycle

    variants = (PromptVariant("repair-v1", "Correct failed evidence."),)
    repair = service.plan_evaluation_repair(
        workflow_id="workflow-1",
        campaign_id="campaign-1",
        actor_id="operator-1",
        idempotency_key="repair-key",
        prompt_variants=variants,
        rationale="Bound the next attempt to the failed evidence.",
    )
    recovery = service.recover_evaluation_assessment(
        workflow_id="workflow-1",
        assessment_id="assessment-1",
        actor_id="operator-1",
        idempotency_key="recovery-key",
        decision=EvaluationRecoveryDecision.MARK_INTERRUPTED_FAILED,
        rationale="Record interrupted work conservatively.",
    )
    promotion = service.promote_evaluation_winner(
        workflow_id="workflow-1",
        assessment_id="assessment-1",
        actor_id="operator-1",
        idempotency_key="promotion-key",
        rationale="Promote the validated winner digest.",
    )

    assert repair == {"command": "repair"}
    assert recovery == {"command": "recover"}
    assert promotion == {"command": "promote"}
    lifecycle.plan_evaluation_repair.assert_called_once_with(
        workflow_id="workflow-1",
        campaign_id="campaign-1",
        actor_id="operator-1",
        idempotency_key="repair-key",
        prompt_variants=variants,
        rationale="Bound the next attempt to the failed evidence.",
    )
    lifecycle.recover_evaluation_assessment.assert_called_once_with(
        workflow_id="workflow-1",
        assessment_id="assessment-1",
        actor_id="operator-1",
        idempotency_key="recovery-key",
        decision=EvaluationRecoveryDecision.MARK_INTERRUPTED_FAILED,
        rationale="Record interrupted work conservatively.",
    )
    lifecycle.promote_evaluation_winner.assert_called_once_with(
        workflow_id="workflow-1",
        assessment_id="assessment-1",
        actor_id="operator-1",
        idempotency_key="promotion-key",
        rationale="Promote the validated winner digest.",
    )


def test_evaluation_pipeline_commands_delegate_without_changing_the_facade(
    service: ControlPlaneService,
) -> None:
    pipeline = Mock()
    pipeline.execute_evaluation_campaign.return_value = {"command": "execute"}
    pipeline.get_evaluation_execution.return_value = {"query": "execution"}
    pipeline.reconcile_evaluation_execution.return_value = {"command": "reconcile-execution"}
    pipeline.validate_evaluation_execution.return_value = {"command": "validate"}
    pipeline.get_evaluation_assessment.return_value = {"query": "assessment"}
    pipeline.reconcile_evaluation_reviews.return_value = {"command": "reconcile-reviews"}
    service.evaluation_pipeline = pipeline

    variants = (PromptVariant("baseline", "Produce the requested bounded artifact."),)
    assert service.execute_evaluation_campaign(
        workflow_id="workflow-1",
        campaign_id="campaign-1",
        task_id="task-1",
        actor_id="operator-1",
        idempotency_key="execution-key",
        prompt_variants=variants,
        repair_id="repair-1",
    ) == {"command": "execute"}
    assert service.get_evaluation_execution(
        "workflow-1", "execution-1", principal_id="operator-1"
    ) == {"query": "execution"}
    assert service.reconcile_evaluation_execution(
        workflow_id="workflow-1",
        execution_id="execution-1",
        actor_id="operator-1",
        idempotency_key="reconcile-execution-key",
        decision=EvaluationReconciliationDecision.MARK_FAILED,
        rationale="Resolve an ambiguous provider outcome conservatively.",
    ) == {"command": "reconcile-execution"}
    assert service.validate_evaluation_execution(
        workflow_id="workflow-1",
        execution_id="execution-1",
        actor_id="operator-1",
        idempotency_key="validation-key",
    ) == {"command": "validate"}
    assert service.get_evaluation_assessment(
        "workflow-1", "assessment-1", principal_id="operator-1"
    ) == {"query": "assessment"}
    assert service.reconcile_evaluation_reviews(
        workflow_id="workflow-1",
        assessment_id="assessment-1",
        actor_id="operator-1",
        idempotency_key="reconcile-review-key",
        decision=EvaluationReconciliationDecision.MARK_FAILED,
        rationale="Resolve an ambiguous reviewer outcome conservatively.",
    ) == {"command": "reconcile-reviews"}

    pipeline.execute_evaluation_campaign.assert_called_once_with(
        workflow_id="workflow-1",
        campaign_id="campaign-1",
        task_id="task-1",
        actor_id="operator-1",
        idempotency_key="execution-key",
        prompt_variants=variants,
        repair_id="repair-1",
    )
    pipeline.get_evaluation_execution.assert_called_once_with(
        "workflow-1", "execution-1", principal_id="operator-1"
    )
    pipeline.reconcile_evaluation_execution.assert_called_once_with(
        workflow_id="workflow-1",
        execution_id="execution-1",
        actor_id="operator-1",
        idempotency_key="reconcile-execution-key",
        decision=EvaluationReconciliationDecision.MARK_FAILED,
        rationale="Resolve an ambiguous provider outcome conservatively.",
    )
    pipeline.validate_evaluation_execution.assert_called_once_with(
        workflow_id="workflow-1",
        execution_id="execution-1",
        actor_id="operator-1",
        idempotency_key="validation-key",
    )
    pipeline.get_evaluation_assessment.assert_called_once_with(
        "workflow-1", "assessment-1", principal_id="operator-1"
    )
    pipeline.reconcile_evaluation_reviews.assert_called_once_with(
        workflow_id="workflow-1",
        assessment_id="assessment-1",
        actor_id="operator-1",
        idempotency_key="reconcile-review-key",
        decision=EvaluationReconciliationDecision.MARK_FAILED,
        rationale="Resolve an ambiguous reviewer outcome conservatively.",
    )


def test_evaluation_campaign_commands_delegate_without_changing_the_facade(
    service: ControlPlaneService,
) -> None:
    campaigns = Mock()
    campaigns.create_evaluation_campaign.return_value = {"command": "create"}
    campaigns.submit_evaluation_evidence.return_value = {"command": "decide"}
    campaigns.get_evaluation_campaign.return_value = {"query": "campaign"}
    campaigns.list_evaluation_campaigns.return_value = [{"query": "campaign"}]
    service.evaluation_campaigns = campaigns

    assert service.create_evaluation_campaign(
        workflow_id="workflow-1",
        actor_id="operator-1",
        idempotency_key="campaign-key",
        prompt_contract_version="prompt-v1",
        work_capability=WorkCapability.PLANNING,
        required_checks=frozenset({"schema", "security"}),
        max_candidates=3,
        max_prompt_variants=2,
        max_iterations=2,
        max_total_cost_microunits=0,
        minimum_independent_reviews=1,
    ) == {"command": "create"}
    assert service.submit_evaluation_evidence(
        workflow_id="workflow-1",
        campaign_id="campaign-1",
        actor_id="operator-1",
        idempotency_key="decision-key",
        candidates=(),
        repair_id="repair-1",
    ) == {"command": "decide"}
    assert service.get_evaluation_campaign(
        "workflow-1", "campaign-1", principal_id="operator-1"
    ) == {"query": "campaign"}
    assert service.list_evaluation_campaigns("workflow-1", principal_id="operator-1") == [
        {"query": "campaign"}
    ]

    campaigns.create_evaluation_campaign.assert_called_once_with(
        workflow_id="workflow-1",
        actor_id="operator-1",
        idempotency_key="campaign-key",
        prompt_contract_version="prompt-v1",
        work_capability=WorkCapability.PLANNING,
        required_checks=frozenset({"schema", "security"}),
        max_candidates=3,
        max_prompt_variants=2,
        max_iterations=2,
        max_total_cost_microunits=0,
        minimum_independent_reviews=1,
    )
    campaigns.submit_evaluation_evidence.assert_called_once_with(
        workflow_id="workflow-1",
        campaign_id="campaign-1",
        actor_id="operator-1",
        idempotency_key="decision-key",
        candidates=(),
        repair_id="repair-1",
    )
    campaigns.get_evaluation_campaign.assert_called_once_with(
        "workflow-1", "campaign-1", principal_id="operator-1"
    )
    campaigns.list_evaluation_campaigns.assert_called_once_with(
        "workflow-1", principal_id="operator-1"
    )


def test_workflow_task_commands_delegate_without_changing_the_facade(
    service: ControlPlaneService,
) -> None:
    workflows = Mock()
    workflows.create_workflow.return_value = {"command": "create"}
    workflows.get_workflow.return_value = {"query": "workflow"}
    workflows.lease_next_task.return_value = {"command": "lease"}
    workflows.reclaim_expired_tasks.return_value = 2
    workflows.execute_leased_task.return_value = {"command": "execute"}
    workflows.disposition_workflow.return_value = {"command": "disposition"}
    workflows.reconcile_execution.return_value = {"command": "reconcile"}
    workflows.approve.return_value = {"command": "approve"}
    workflows.cancel_workflow.return_value = {"command": "cancel"}
    service.workflow_tasks = workflows

    signals = frozenset({InspectionSignal.CONCEALED_INSTRUCTIONS})
    lease_reference = "lease-1"
    assert service.create_workflow(
        requester_id="requester-1",
        title="Bounded change",
        description="Exercise the workflow boundary.",
        idempotency_key="workflow-key",
        complexity=ComplexityTier.COMPLEX,
        risk=RiskLevel.HIGH,
        data_classification=DataClassification.RESTRICTED,
        repository_scope="repo-1",
        inspection_signals=signals,
    ) == {"command": "create"}
    assert service.get_workflow("workflow-1", principal_id="operator-1") == {"query": "workflow"}
    assert service.lease_next_task(worker_id="worker-1") == {"command": "lease"}
    assert service.reclaim_expired_tasks(worker_id="worker-1") == 2
    assert service.execute_leased_task(
        task_id="task-1", lease_token=lease_reference, worker_id="worker-1"
    ) == {"command": "execute"}
    service.heartbeat_task(task_id="task-1", lease_token=lease_reference, worker_id="worker-1")
    assert service.disposition_workflow(
        workflow_id="workflow-1",
        actor_id="operator-1",
        decision=DispositionDecision.RESUME_CONTAINED,
        rationale="Proceed with containment.",
    ) == {"command": "disposition"}
    assert service.reconcile_execution(
        workflow_id="workflow-1",
        task_id="task-1",
        actor_id="operator-1",
        decision=ReconciliationDecision.RETRY,
        rationale="Retry after conservative reconciliation.",
    ) == {"command": "reconcile"}
    assert service.approve(
        workflow_id="workflow-1",
        approver_id="operator-1",
        action=ApprovalAction.DEPLOY,
        target="environment-1",
        revision="revision-1",
        decision=ApprovalDecision.APPROVED,
        rationale="Approve the immutable plan.",
        expires_in_minutes=10,
        environment_id="environment-1",
        plan_digest="plan-digest",
        deployment_attempt_id=None,
    ) == {"command": "approve"}
    assert service.cancel_workflow("workflow-1", principal_id="operator-1") == {"command": "cancel"}

    workflows.create_workflow.assert_called_once_with(
        requester_id="requester-1",
        title="Bounded change",
        description="Exercise the workflow boundary.",
        idempotency_key="workflow-key",
        complexity=ComplexityTier.COMPLEX,
        risk=RiskLevel.HIGH,
        data_classification=DataClassification.RESTRICTED,
        repository_scope="repo-1",
        inspection_signals=signals,
    )
    workflows.get_workflow.assert_called_once_with("workflow-1", principal_id="operator-1")
    workflows.lease_next_task.assert_called_once_with(worker_id="worker-1")
    workflows.reclaim_expired_tasks.assert_called_once_with(worker_id="worker-1")
    workflows.execute_leased_task.assert_called_once_with(
        task_id="task-1", lease_token=lease_reference, worker_id="worker-1"
    )
    workflows.heartbeat_task.assert_called_once_with(
        task_id="task-1", lease_token=lease_reference, worker_id="worker-1"
    )
    workflows.disposition_workflow.assert_called_once_with(
        workflow_id="workflow-1",
        actor_id="operator-1",
        decision=DispositionDecision.RESUME_CONTAINED,
        rationale="Proceed with containment.",
    )
    workflows.reconcile_execution.assert_called_once_with(
        workflow_id="workflow-1",
        task_id="task-1",
        actor_id="operator-1",
        decision=ReconciliationDecision.RETRY,
        rationale="Retry after conservative reconciliation.",
    )
    workflows.approve.assert_called_once_with(
        workflow_id="workflow-1",
        approver_id="operator-1",
        action=ApprovalAction.DEPLOY,
        target="environment-1",
        revision="revision-1",
        decision=ApprovalDecision.APPROVED,
        rationale="Approve the immutable plan.",
        expires_in_minutes=10,
        environment_id="environment-1",
        plan_digest="plan-digest",
        deployment_attempt_id=None,
    )
    workflows.cancel_workflow.assert_called_once_with("workflow-1", principal_id="operator-1")


def test_git_pull_request_commands_delegate_without_changing_the_facade(
    service: ControlPlaneService,
) -> None:
    git = Mock()
    git.list_ci_check_evidence.return_value = [{"query": "ci"}]
    git.ingest_ci_check_evidence.return_value = {"command": "ingest"}
    git.propose_pull_request.return_value = {"command": "propose"}
    git.reconcile_pull_request.return_value = {"command": "reconcile"}
    git.assess_merge_readiness.return_value = {"command": "assess"}
    git.list_pull_request_proposals.return_value = [{"query": "proposal"}]
    git.confirm_pull_request_merged.return_value = {"command": "confirm"}
    git.list_merge_confirmations.return_value = [{"query": "confirmation"}]
    service.git_pull_requests = git

    assert service.list_ci_check_evidence("workflow-1", principal_id="operator-1") == [
        {"query": "ci"}
    ]
    assert service.ingest_ci_check_evidence(
        actor_id="github-app",
        delivery_id="delivery-1",
        repository="owner/repository",
        check_run_id="check-1",
        check_name="test",
        revision="a" * 40,
        status="completed",
        conclusion="success",
        details_url="https://github.example/check-1",
        app_slug="github-actions",
        payload_digest="b" * 64,
    ) == {"command": "ingest"}
    assert service.propose_pull_request(
        workflow_id="workflow-1",
        actor_id="operator-1",
        head_branch="codex/change",
        title="Controlled change",
        body="Revision-bound evidence.",
        idempotency_key="proposal-key",
    ) == {"command": "propose"}
    assert service.reconcile_pull_request(
        workflow_id="workflow-1",
        proposal_id="proposal-1",
        actor_id="operator-1",
    ) == {"command": "reconcile"}
    assert service.assess_merge_readiness(
        workflow_id="workflow-1",
        proposal_id="proposal-1",
        actor_id="operator-1",
        idempotency_key="readiness-key",
    ) == {"command": "assess"}
    assert service.list_pull_request_proposals("workflow-1", principal_id="operator-1") == [
        {"query": "proposal"}
    ]
    assert service.confirm_pull_request_merged(
        workflow_id="workflow-1",
        proposal_id="proposal-1",
        assessment_id="assessment-1",
        actor_id="operator-1",
        idempotency_key="confirmation-key",
    ) == {"command": "confirm"}
    assert service.list_merge_confirmations("workflow-1", principal_id="operator-1") == [
        {"query": "confirmation"}
    ]

    git.list_ci_check_evidence.assert_called_once_with("workflow-1", principal_id="operator-1")
    git.ingest_ci_check_evidence.assert_called_once_with(
        actor_id="github-app",
        delivery_id="delivery-1",
        repository="owner/repository",
        check_run_id="check-1",
        check_name="test",
        revision="a" * 40,
        status="completed",
        conclusion="success",
        details_url="https://github.example/check-1",
        app_slug="github-actions",
        payload_digest="b" * 64,
    )
    git.propose_pull_request.assert_called_once_with(
        workflow_id="workflow-1",
        actor_id="operator-1",
        head_branch="codex/change",
        title="Controlled change",
        body="Revision-bound evidence.",
        idempotency_key="proposal-key",
    )
    git.reconcile_pull_request.assert_called_once_with(
        workflow_id="workflow-1",
        proposal_id="proposal-1",
        actor_id="operator-1",
    )
    git.assess_merge_readiness.assert_called_once_with(
        workflow_id="workflow-1",
        proposal_id="proposal-1",
        actor_id="operator-1",
        idempotency_key="readiness-key",
    )
    git.list_pull_request_proposals.assert_called_once_with("workflow-1", principal_id="operator-1")
    git.confirm_pull_request_merged.assert_called_once_with(
        workflow_id="workflow-1",
        proposal_id="proposal-1",
        assessment_id="assessment-1",
        actor_id="operator-1",
        idempotency_key="confirmation-key",
    )
    git.list_merge_confirmations.assert_called_once_with("workflow-1", principal_id="operator-1")


def test_deployment_recovery_commands_delegate_without_changing_the_facade(
    service: ControlPlaneService,
) -> None:
    deployment = Mock()
    deployment.register_deployment_environment.return_value = {"command": "register"}
    deployment.list_deployment_environments.return_value = [{"query": "environment"}]
    deployment.create_deployment_plan.return_value = {"command": "plan"}
    deployment.list_deployment_plans.return_value = [{"query": "plan"}]
    deployment.execute_deployment_dry_run.return_value = {"command": "dry-run"}
    deployment.execute_local_deployment.return_value = {"command": "deploy"}
    deployment.list_deployment_attempts.return_value = [{"query": "attempt"}]
    deployment.execute_local_rollback.return_value = {"command": "rollback"}
    deployment.list_deployment_rollbacks.return_value = [{"query": "rollback"}]
    service.deployment_recovery = deployment

    environment_args = {
        "actor_id": "operator-1",
        "environment_id": "development",
        "name": "Local development",
        "classification": EnvironmentClassification.DEVELOPMENT,
        "repository": "owner/repository",
        "base_branch": "main",
        "resource_scope": ("deployment/app",),
        "required_checks": ("test",),
        "required_attestations": ("image",),
        "verification_policy": ("rollout",),
        "rollback_policy": "previous-release",
        "policy_version": "deployment/test-v1",
        "provider": "local-k3d",
        "account_scope": "local",
        "region": "local",
        "adapter_id": "local-k3d-v1",
    }
    assert service.register_deployment_environment(**environment_args) == {"command": "register"}
    assert service.list_deployment_environments(principal_id="operator-1") == [
        {"query": "environment"}
    ]

    plan_args = {
        "workflow_id": "workflow-1",
        "actor_id": "operator-1",
        "environment_id": "development",
        "artifact_digests": ("sha256:" + "a" * 64,),
        "operations": ({"resource_id": "deployment/app"},),
        "declared_impact": "Update the local deployment.",
        "verification_probes": ("rollout",),
        "rollback_reference": "previous-release",
        "idempotency_key": "plan-key",
    }
    assert service.create_deployment_plan(**plan_args) == {"command": "plan"}
    assert service.list_deployment_plans("workflow-1", principal_id="operator-1") == [
        {"query": "plan"}
    ]

    execution_args = {
        "workflow_id": "workflow-1",
        "plan_id": "plan-1",
        "actor_id": "operator-1",
        "idempotency_key": "execution-key",
    }
    assert service.execute_deployment_dry_run(**execution_args) == {"command": "dry-run"}
    assert service.execute_local_deployment(**execution_args) == {"command": "deploy"}
    assert service.list_deployment_attempts("workflow-1", principal_id="operator-1") == [
        {"query": "attempt"}
    ]

    rollback_args = {
        "workflow_id": "workflow-1",
        "attempt_id": "attempt-1",
        "actor_id": "operator-1",
        "idempotency_key": "rollback-key",
    }
    assert service.execute_local_rollback(**rollback_args) == {"command": "rollback"}
    assert service.list_deployment_rollbacks("workflow-1", principal_id="operator-1") == [
        {"query": "rollback"}
    ]

    deployment.register_deployment_environment.assert_called_once_with(**environment_args)
    deployment.list_deployment_environments.assert_called_once_with(principal_id="operator-1")
    deployment.create_deployment_plan.assert_called_once_with(**plan_args)
    deployment.list_deployment_plans.assert_called_once_with(
        "workflow-1", principal_id="operator-1"
    )
    deployment.execute_deployment_dry_run.assert_called_once_with(**execution_args)
    deployment.execute_local_deployment.assert_called_once_with(**execution_args)
    deployment.list_deployment_attempts.assert_called_once_with(
        "workflow-1", principal_id="operator-1"
    )
    deployment.execute_local_rollback.assert_called_once_with(**rollback_args)
    deployment.list_deployment_rollbacks.assert_called_once_with(
        "workflow-1", principal_id="operator-1"
    )


def test_windsurf_integration_commands_delegate_without_changing_the_facade(
    service: ControlPlaneService,
) -> None:
    windsurf = Mock()
    windsurf.claim_windsurf_task.return_value = {"command": "claim"}
    windsurf.heartbeat_windsurf_task.return_value = {"command": "heartbeat"}
    windsurf.submit_windsurf_evidence.return_value = {"command": "submit"}
    service.windsurf_integration = windsurf
    lease_reference = "lease-1"

    assert service.claim_windsurf_task(task_id="task-1", principal_id="windsurf-cascade") == {
        "command": "claim"
    }
    assert service.heartbeat_windsurf_task(
        task_id="task-1",
        lease_token=lease_reference,
        principal_id="windsurf-cascade",
    ) == {"command": "heartbeat"}
    assert service.submit_windsurf_evidence(
        task_id="task-1",
        lease_token=lease_reference,
        principal_id="windsurf-cascade",
        handoff_digest="a" * 64,
        result_revision="b" * 40,
        files_changed=("src/change.py",),
        tests_passed=True,
        test_summary="The bounded checks passed.",
        tool_activity_summary="Only the registered repository was modified.",
    ) == {"command": "submit"}

    windsurf.claim_windsurf_task.assert_called_once_with(
        task_id="task-1", principal_id="windsurf-cascade"
    )
    windsurf.heartbeat_windsurf_task.assert_called_once_with(
        task_id="task-1",
        lease_token=lease_reference,
        principal_id="windsurf-cascade",
    )
    windsurf.submit_windsurf_evidence.assert_called_once_with(
        task_id="task-1",
        lease_token=lease_reference,
        principal_id="windsurf-cascade",
        handoff_digest="a" * 64,
        result_revision="b" * 40,
        files_changed=("src/change.py",),
        tests_passed=True,
        test_summary="The bounded checks passed.",
        tool_activity_summary="Only the registered repository was modified.",
    )
