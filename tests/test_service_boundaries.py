from __future__ import annotations

from unittest.mock import Mock

from control_plane.evaluation import (
    EvaluationReconciliationDecision,
    EvaluationRecoveryDecision,
    PromptVariant,
)
from control_plane.routing import WorkCapability
from control_plane.service import ControlPlaneService


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
