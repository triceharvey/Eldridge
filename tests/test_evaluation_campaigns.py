from __future__ import annotations

from dataclasses import replace

import pytest

from control_plane.domain import AuthorizationError, ConflictError, ValidationError
from control_plane.evaluation import CandidateEvidence, DeterministicCheck, IndependentReview
from control_plane.routing import RiskLevel, WorkCapability
from control_plane.service import ControlPlaneService

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _workflow(service: ControlPlaneService) -> dict[str, object]:
    return service.create_workflow(
        requester_id="dev-operator",
        title="Durable evaluation",
        description="Exercise replayable campaign evidence.",
        idempotency_key="durable-evaluation-workflow",
    )


def _campaign(
    service: ControlPlaneService, workflow_id: str, **changes: object
) -> dict[str, object]:
    values: dict[str, object] = {
        "workflow_id": workflow_id,
        "actor_id": "dev-operator",
        "idempotency_key": "evaluation-campaign-key",
        "prompt_contract_version": "prompt-v1",
        "work_capability": WorkCapability.PLANNING,
        "required_checks": frozenset({"schema", "security"}),
        "max_total_cost_microunits": 0,
    }
    values.update(changes)
    return service.create_evaluation_campaign(**values)  # type: ignore[arg-type]


def _candidate(
    candidate_id: str,
    *,
    iteration: int = 1,
    security_passed: bool = True,
    routing_score: float = 0.575,
    provider_id: str = "mock-producer",
    reviews: tuple[IndependentReview, ...] = (),
    cost: int = 0,
) -> CandidateEvidence:
    return CandidateEvidence(
        candidate_id=candidate_id,
        provider_id=provider_id,
        provider_family="deterministic-mock",
        model_version="deterministic-mock-v1",
        profile_version="v1",
        prompt_variant_id=f"variant-{iteration}",
        prompt_contract_version="prompt-v1",
        iteration=iteration,
        succeeded=True,
        output_digest=DIGEST_A,
        latency_ms=25,
        cost_microunits=cost,
        routing_score=routing_score,
        checks=(
            DeterministicCheck("schema", True, DIGEST_B, DIGEST_A),
            DeterministicCheck("security", security_passed, DIGEST_C, DIGEST_A),
        ),
        reviews=reviews,
    )


def test_campaign_decision_is_durable_audited_and_replay_safe(
    service: ControlPlaneService,
) -> None:
    workflow = _workflow(service)
    campaign = _campaign(service, str(workflow["id"]))
    candidate = _candidate("candidate-a")

    first = service.submit_evaluation_evidence(
        workflow_id=str(workflow["id"]),
        campaign_id=str(campaign["id"]),
        actor_id="dev-operator",
        idempotency_key="evaluation-batch-key",
        candidates=(candidate,),
    )
    replay = service.submit_evaluation_evidence(
        workflow_id=str(workflow["id"]),
        campaign_id=str(campaign["id"]),
        actor_id="dev-operator",
        idempotency_key="evaluation-batch-key",
        candidates=(replace(candidate, checks=tuple(reversed(candidate.checks))),),
    )
    stored = service.get_evaluation_campaign(
        str(workflow["id"]), str(campaign["id"]), principal_id="dev-operator"
    )
    events = service.list_events(str(workflow["id"]), principal_id="dev-operator")

    assert first["status"] == "WINNER_SELECTED"
    assert first["winner_candidate_id"] == "candidate-a"
    assert first["routing_snapshot"]["eligible"][0]["score"] == 0.575
    assert first["replayed"] is False
    assert replay["id"] == first["id"]
    assert replay["replayed"] is True
    assert stored["status"] == "WINNER_SELECTED"
    assert stored["batches"][0]["candidates"][0]["output_digest"] == DIGEST_A
    assert stored["batches"][0]["candidates"][0]["rank"] == 1
    assert [event["event_type"] for event in events].count("evaluation.batch_decided") == 1


def test_campaign_persists_bounded_refinement_across_iterations(
    service: ControlPlaneService,
) -> None:
    workflow = _workflow(service)
    campaign = _campaign(service, str(workflow["id"]), max_iterations=2)

    failed = service.submit_evaluation_evidence(
        workflow_id=str(workflow["id"]),
        campaign_id=str(campaign["id"]),
        actor_id="dev-operator",
        idempotency_key="evaluation-first-batch",
        candidates=(_candidate("candidate-first", security_passed=False),),
    )
    passed = service.submit_evaluation_evidence(
        workflow_id=str(workflow["id"]),
        campaign_id=str(campaign["id"]),
        actor_id="dev-operator",
        idempotency_key="evaluation-second-batch",
        candidates=(_candidate("candidate-second", iteration=2),),
    )
    stored = service.get_evaluation_campaign(
        str(workflow["id"]), str(campaign["id"]), principal_id="dev-operator"
    )

    assert failed["status"] == "REFINEMENT_REQUIRED"
    assert passed["status"] == "WINNER_SELECTED"
    assert stored["current_iteration"] == 2
    assert [batch["iteration"] for batch in stored["batches"]] == [1, 2]


def test_campaign_rejects_policy_identity_and_idempotency_drift(
    service: ControlPlaneService,
) -> None:
    workflow = _workflow(service)
    campaign = _campaign(service, str(workflow["id"]))

    controller_scored = service.submit_evaluation_evidence(
        workflow_id=str(workflow["id"]),
        campaign_id=str(campaign["id"]),
        actor_id="dev-operator",
        idempotency_key="controller-score-batch",
        candidates=(_candidate("controller-scored", routing_score=0.9),),
    )
    assert controller_scored["ranked_candidates"][0]["routing_score"] == 0.575

    second_campaign = _campaign(
        service,
        str(workflow["id"]),
        idempotency_key="second-evaluation-campaign",
    )
    with pytest.raises(ValidationError, match="policy eligible"):
        service.submit_evaluation_evidence(
            workflow_id=str(workflow["id"]),
            campaign_id=str(second_campaign["id"]),
            actor_id="dev-operator",
            idempotency_key="invalid-provider-batch",
            candidates=(_candidate("bad-provider", provider_id="unknown"),),
        )
    with pytest.raises(ValidationError, match="reviewer provider is not policy eligible"):
        service.submit_evaluation_evidence(
            workflow_id=str(workflow["id"]),
            campaign_id=str(second_campaign["id"]),
            actor_id="dev-operator",
            idempotency_key="invalid-reviewer-batch",
            candidates=(
                _candidate(
                    "bad-reviewer",
                    reviews=(
                        IndependentReview(
                            "invented-reviewer",
                            "invented-family",
                            "invented-model",
                            "invented-profile",
                            DIGEST_A,
                            True,
                            DIGEST_A,
                        ),
                    ),
                ),
            ),
        )

    _campaign(service, str(workflow["id"]))
    with pytest.raises(ConflictError, match="idempotency key was reused"):
        _campaign(
            service,
            str(workflow["id"]),
            prompt_contract_version="prompt-v2",
        )


def test_only_human_operator_can_create_or_submit_campaign_evidence(
    service: ControlPlaneService,
) -> None:
    workflow = _workflow(service)
    with pytest.raises(AuthorizationError):
        _campaign(service, str(workflow["id"]), actor_id="implementer-agent")

    campaign = _campaign(service, str(workflow["id"]))
    with pytest.raises(AuthorizationError):
        service.submit_evaluation_evidence(
            workflow_id=str(workflow["id"]),
            campaign_id=str(campaign["id"]),
            actor_id="implementer-agent",
            idempotency_key="agent-evidence-key",
            candidates=(_candidate("agent-candidate"),),
        )


def test_campaign_derives_high_risk_and_cannot_reduce_review_floor(
    service: ControlPlaneService,
) -> None:
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="High-risk evaluation",
        description="Retain the workflow risk at the evaluation boundary.",
        idempotency_key="high-risk-evaluation-workflow",
        risk=RiskLevel.HIGH,
    )
    campaign = _campaign(
        service,
        str(workflow["id"]),
        idempotency_key="high-risk-campaign",
    )

    assert campaign["risk"] == "HIGH"
    assert campaign["minimum_independent_reviews"] == 2
    with pytest.raises(ValidationError, match="two independent reviews"):
        _campaign(
            service,
            str(workflow["id"]),
            idempotency_key="downgraded-high-risk-campaign",
            minimum_independent_reviews=0,
        )


def test_campaign_contains_replay_drift_candidate_reuse_and_terminal_cost(
    service: ControlPlaneService,
) -> None:
    workflow = _workflow(service)
    campaign = _campaign(service, str(workflow["id"]), max_iterations=2)
    first = _candidate("stable-id", security_passed=False)
    service.submit_evaluation_evidence(
        workflow_id=str(workflow["id"]),
        campaign_id=str(campaign["id"]),
        actor_id="dev-operator",
        idempotency_key="stable-first-batch",
        candidates=(first,),
    )

    with pytest.raises(ConflictError, match="batch idempotency key was reused"):
        service.submit_evaluation_evidence(
            workflow_id=str(workflow["id"]),
            campaign_id=str(campaign["id"]),
            actor_id="dev-operator",
            idempotency_key="stable-first-batch",
            candidates=(_candidate("different-id", iteration=2),),
        )
    with pytest.raises(ConflictError, match="candidate ID was already recorded"):
        service.submit_evaluation_evidence(
            workflow_id=str(workflow["id"]),
            campaign_id=str(campaign["id"]),
            actor_id="dev-operator",
            idempotency_key="reused-candidate-batch",
            candidates=(_candidate("stable-id", iteration=2),),
        )

    cost_campaign = _campaign(
        service,
        str(workflow["id"]),
        idempotency_key="cost-campaign-key",
        max_total_cost_microunits=5,
    )
    exhausted = service.submit_evaluation_evidence(
        workflow_id=str(workflow["id"]),
        campaign_id=str(cost_campaign["id"]),
        actor_id="dev-operator",
        idempotency_key="over-cost-batch",
        candidates=(_candidate("over-cost", cost=6),),
    )
    assert exhausted["status"] == "EXHAUSTED"
    assert exhausted["total_cost_microunits"] == 6
    with pytest.raises(ConflictError, match="campaign is terminal"):
        service.submit_evaluation_evidence(
            workflow_id=str(workflow["id"]),
            campaign_id=str(cost_campaign["id"]),
            actor_id="dev-operator",
            idempotency_key="after-terminal-batch",
            candidates=(_candidate("after-terminal"),),
        )
