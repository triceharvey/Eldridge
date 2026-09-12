import json
import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from control_plane.activation import ProviderActivationPolicy, activate_integrations
from control_plane.evaluation import PromptVariant
from control_plane.routing import DataClassification, RiskLevel, RoutingObjective, WorkCapability
from control_plane.service import ControlPlaneService

LOCAL_MODEL = "qwen3.5:9b-q4_K_M"
LOCAL_MODEL_DIGEST = "sha256:6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7"
OPERATION_KEYS = (
    "live-vertical-slice-workflow-v1",
    "live-vertical-slice-campaign-v1",
    "live-vertical-slice-execution-v1",
    "live-vertical-slice-assessment-v1",
    "live-vertical-slice-promotion-v1",
)


@pytest.mark.live_provider
def test_live_claude_producer_local_reviewer_vertical_slice(
    session_factory: sessionmaker[Session],
) -> None:
    if os.getenv("CONTROL_PLANE_RUN_LIVE_VERTICAL_SLICE") != "true":
        pytest.skip("explicit live multi-model vertical-slice opt-in is not enabled")
    policy = ProviderActivationPolicy.model_validate(
        {
            "policy_version": "provider-activation/live-vertical-slice-v1",
            "allow_external_egress": True,
            "include_mock_providers": False,
            "secret_environment": {},
            "claude_code": {
                "enabled": True,
                "model": "sonnet",
                "timeout_seconds": 120,
                "maximum_data_classification": "PUBLIC",
                "maximum_risk": "LOW",
                "max_invocations_per_execution": 1,
            },
            "local_model": {
                "enabled": True,
                "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
                "model": LOCAL_MODEL,
                "artifact_digest": LOCAL_MODEL_DIGEST,
                "max_tokens": 2048,
                "timeout_seconds": 120,
                "reasoning_effort": "none",
                "maximum_data_classification": "PUBLIC",
                "maximum_risk": "LOW",
            },
        }
    )
    activated = activate_integrations(policy)
    service = ControlPlaneService(
        session_factory,
        provider_bindings=activated.bindings,
        allowed_egress=activated.allowed_egress,
        provider_policy_version=activated.policy_version,
        routing_objective=RoutingObjective.QUALITY,
    )
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Public Eldridge vertical-slice release note",
        description=(
            "Using only this supplied public context, plan a short Markdown release note recording "
            "that Eldridge separates subscription-backed Claude production from an independent "
            "local reviewer. Do not request commands, credentials, approval, merge, or deployment."
        ),
        idempotency_key=OPERATION_KEYS[0],
        risk=RiskLevel.LOW,
        data_classification=DataClassification.PUBLIC,
        repository_scope="triceharvey/Eldridge",
    )
    tasks = workflow["tasks"]
    assert isinstance(tasks, list)
    campaign = service.create_evaluation_campaign(
        workflow_id=str(workflow["id"]),
        actor_id="dev-operator",
        idempotency_key=OPERATION_KEYS[1],
        prompt_contract_version="eldridge-public-release-note/v1",
        work_capability=WorkCapability.PLANNING,
        required_checks=frozenset({"schema", "security", "revision_binding"}),
        max_candidates=1,
        max_prompt_variants=1,
        max_iterations=1,
        max_total_cost_microunits=0,
        minimum_independent_reviews=1,
    )
    execution = service.execute_evaluation_campaign(
        workflow_id=str(workflow["id"]),
        campaign_id=str(campaign["id"]),
        task_id=str(tasks[0]["id"]),
        actor_id="dev-operator",
        idempotency_key=OPERATION_KEYS[2],
        prompt_variants=(
            PromptVariant(
                "bounded-public-context",
                "Produce the smallest complete plan and state every assumption explicitly.",
            ),
        ),
    )
    assert execution["status"] == "OUTPUTS_READY", execution
    selected = execution["routing_snapshot"]["selected"]
    assert [item["provider_id"] for item in selected] == ["claude-code-subscription"]
    assert selected[0]["funding_mode"] == "SUBSCRIPTION"
    assert selected[0]["max_invocations_per_execution"] == 1

    assessment = service.validate_evaluation_execution(
        workflow_id=str(workflow["id"]),
        execution_id=str(execution["id"]),
        actor_id="dev-operator",
        idempotency_key=OPERATION_KEYS[3],
    )
    assert assessment["status"] == "DECIDED"
    assert assessment["decision"]["status"] == "WINNER_SELECTED"
    artifacts = assessment["artifacts"]
    assert len(artifacts) == 1
    assert all(check["passed"] is True for check in artifacts[0]["checks"])
    reviews = artifacts[0]["reviews"]
    assert len(reviews) == 1
    assert reviews[0]["reviewer_provider_id"] == "local-openai-compatible"
    assert reviews[0]["reviewer_provider_family"] == "operator-local"
    assert reviews[0]["passed"] is True
    assert reviews[0]["reviewed_output_digest"] == artifacts[0]["digest"]

    promotion = service.promote_evaluation_winner(
        workflow_id=str(workflow["id"]),
        assessment_id=str(assessment["id"]),
        actor_id="dev-operator",
        idempotency_key=OPERATION_KEYS[4],
        rationale=(
            "Promote the exact deterministically validated and independently reviewed digest."
        ),
    )
    assert promotion["artifact_digest"] == artifacts[0]["digest"]

    report_path = os.getenv("CONTROL_PLANE_VERTICAL_SLICE_REPORT")
    if report_path:
        report = {
            "policy_version": activated.policy_version,
            "workflow_id": workflow["id"],
            "campaign_id": campaign["id"],
            "execution_id": execution["id"],
            "assessment_id": assessment["id"],
            "producer_provider_id": "claude-code-subscription",
            "reviewer_provider_id": reviews[0]["reviewer_provider_id"],
            "reviewer_model_version": reviews[0]["reviewer_model_version"],
            "artifact_digest": artifacts[0]["digest"],
            "check_evidence_digests": {
                check["name"]: check["evidence_digest"] for check in artifacts[0]["checks"]
            },
            "review_evidence_digest": reviews[0]["evidence_digest"],
            "promotion_id": promotion["id"],
            "result": "PASSED",
        }
        Path(report_path).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
