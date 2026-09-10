from __future__ import annotations

import pytest

from control_plane.evaluation import (
    CandidateEvidence,
    DeterministicCheck,
    EvaluationBatch,
    EvaluationPolicy,
    EvaluationStatus,
    IndependentReview,
    MultiModelEvaluator,
)
from control_plane.routing import RiskLevel

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _candidate(
    candidate_id: str,
    *,
    provider_id: str | None = None,
    family: str = "family-a",
    routing_score: float = 0.8,
    cost: int = 0,
    latency: int = 100,
    checks: tuple[DeterministicCheck, ...] | None = None,
    reviews: tuple[IndependentReview, ...] = (),
    succeeded: bool = True,
    iteration: int = 1,
    prompt_variant_id: str = "baseline",
) -> CandidateEvidence:
    return CandidateEvidence(
        candidate_id=candidate_id,
        provider_id=provider_id or candidate_id,
        provider_family=family,
        model_version=f"{candidate_id}-model",
        profile_version="profile-v1",
        prompt_variant_id=prompt_variant_id,
        prompt_contract_version="prompt-v1",
        iteration=iteration,
        succeeded=succeeded,
        output_digest=DIGEST_A if succeeded else None,
        latency_ms=latency,
        cost_microunits=cost,
        routing_score=routing_score,
        checks=checks
        if checks is not None
        else (
            DeterministicCheck("schema", True, DIGEST_B),
            DeterministicCheck("security", True, DIGEST_C),
        ),
        reviews=reviews,
    )


def _policy(**changes: object) -> EvaluationPolicy:
    values: dict[str, object] = {
        "required_checks": frozenset({"schema", "security"}),
        "risk": RiskLevel.LOW,
        "max_total_cost_microunits": 1_000_000,
    }
    values.update(changes)
    return EvaluationPolicy(**values)  # type: ignore[arg-type]


def _batch(
    *candidates: CandidateEvidence, iteration: int = 1, prior_cost: int = 0
) -> EvaluationBatch:
    return EvaluationBatch(
        campaign_id="campaign-1",
        prompt_contract_version="prompt-v1",
        current_iteration=iteration,
        prior_cost_microunits=prior_cost,
        candidates=tuple(candidates),
    )


def test_selects_deterministic_winner_independent_of_input_order() -> None:
    stronger = _candidate("stronger", routing_score=0.9, latency=200)
    faster = _candidate("faster", routing_score=0.8, latency=50)
    evaluator = MultiModelEvaluator()

    forward = evaluator.evaluate(_policy(), _batch(faster, stronger))
    reverse = evaluator.evaluate(_policy(), _batch(stronger, faster))

    assert forward == reverse
    assert forward.status is EvaluationStatus.WINNER_SELECTED
    assert forward.winner_candidate_id == "stronger"
    assert [item.candidate_id for item in forward.ranked_candidates] == ["stronger", "faster"]


def test_rejects_failed_or_missing_checks_and_requests_bounded_refinement() -> None:
    candidate = _candidate(
        "failed-check",
        checks=(DeterministicCheck("schema", False, DIGEST_A),),
    )

    decision = MultiModelEvaluator().evaluate(_policy(), _batch(candidate))

    assert decision.status is EvaluationStatus.REFINEMENT_REQUIRED
    assert decision.next_iteration == 2
    assert decision.rejected_candidates == {
        "failed-check": ("missing_checks:security", "failed_checks:schema")
    }


def test_cost_ceiling_exhausts_without_promoting_a_valid_result() -> None:
    decision = MultiModelEvaluator().evaluate(
        _policy(max_total_cost_microunits=10),
        _batch(_candidate("over-budget", cost=11)),
    )

    assert decision.status is EvaluationStatus.EXHAUSTED
    assert decision.winner_candidate_id is None
    assert decision.rejected_candidates["over-budget"] == ("campaign_cost_ceiling_exceeded",)


def test_high_risk_requires_two_cross_family_reviews() -> None:
    reviews = (
        IndependentReview("reviewer-a", "family-b", True, DIGEST_B),
        IndependentReview("reviewer-b", "family-c", True, DIGEST_C),
    )
    policy = _policy(risk=RiskLevel.HIGH, minimum_independent_reviews=2)

    passed = MultiModelEvaluator().evaluate(
        policy,
        _batch(_candidate("producer", family="family-a", reviews=reviews)),
    )
    failed = MultiModelEvaluator().evaluate(
        policy,
        _batch(
            _candidate(
                "producer",
                family="family-a",
                reviews=(
                    IndependentReview("reviewer-a", "family-a", True, DIGEST_B),
                    IndependentReview("reviewer-b", "family-c", True, DIGEST_C),
                ),
            )
        ),
    )

    assert passed.status is EvaluationStatus.WINNER_SELECTED
    assert failed.status is EvaluationStatus.REFINEMENT_REQUIRED
    assert failed.rejected_candidates["producer"] == ("insufficient_independent_reviews",)


def test_campaign_exhausts_at_iteration_limit() -> None:
    failed = _candidate(
        "failed",
        checks=(
            DeterministicCheck("schema", True, DIGEST_A),
            DeterministicCheck("security", False, DIGEST_B),
        ),
        iteration=3,
    )

    decision = MultiModelEvaluator().evaluate(
        _policy(max_iterations=3), _batch(failed, iteration=3)
    )

    assert decision.status is EvaluationStatus.EXHAUSTED
    assert decision.next_iteration is None


def test_zero_cost_campaign_can_refine_without_expanding_its_budget() -> None:
    failed = _candidate(
        "local-model",
        checks=(
            DeterministicCheck("schema", True, DIGEST_A),
            DeterministicCheck("security", False, DIGEST_B),
        ),
    )

    decision = MultiModelEvaluator().evaluate(_policy(max_total_cost_microunits=0), _batch(failed))

    assert decision.status is EvaluationStatus.REFINEMENT_REQUIRED
    assert decision.total_cost_microunits == 0


def test_rejects_batch_that_exceeds_provider_or_variant_ceiling() -> None:
    evaluator = MultiModelEvaluator()
    candidates = (
        _candidate("one", prompt_variant_id="one"),
        _candidate("two", prompt_variant_id="two"),
    )

    with pytest.raises(ValueError, match="candidate ceiling"):
        evaluator.evaluate(_policy(max_candidates=1), _batch(*candidates))
    with pytest.raises(ValueError, match="prompt-variant ceiling"):
        evaluator.evaluate(_policy(max_prompt_variants=1), _batch(*candidates))


def test_high_risk_policy_cannot_reduce_review_floor() -> None:
    with pytest.raises(ValueError, match="two independent reviews"):
        _policy(risk=RiskLevel.CRITICAL, minimum_independent_reviews=1)


def test_evidence_boundary_rejects_non_boolean_results() -> None:
    with pytest.raises(ValueError, match="check passed must be a boolean"):
        DeterministicCheck("schema", 1, DIGEST_A)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="review passed must be a boolean"):
        IndependentReview("reviewer", "family-b", "yes", DIGEST_A)  # type: ignore[arg-type]


def test_candidate_boundary_rejects_boolean_numeric_fields() -> None:
    with pytest.raises(ValueError, match="candidate latency must be an integer"):
        _candidate("bad-latency", latency=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="candidate routing score must be numeric"):
        _candidate("bad-score", routing_score=False)  # type: ignore[arg-type]


def test_policy_boundary_requires_typed_risk_and_integer_limits() -> None:
    with pytest.raises(ValueError, match="risk must be a RiskLevel"):
        _policy(risk=2)
    with pytest.raises(ValueError, match="max_candidates must be an integer"):
        _policy(max_candidates=True)
