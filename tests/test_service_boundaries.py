from __future__ import annotations

from unittest.mock import Mock

from control_plane.evaluation import EvaluationRecoveryDecision, PromptVariant
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
