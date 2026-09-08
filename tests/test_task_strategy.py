from control_plane.routing import DataClassification, RiskLevel, WorkCapability
from control_plane.task_strategy import (
    ComplexityTier,
    ExecutionRestriction,
    InspectionSignal,
    StrategyStage,
    TaskProfile,
    TaskStrategyPlanner,
)


def task_profile(**overrides: object) -> TaskProfile:
    values: dict[str, object] = {
        "complexity": ComplexityTier.STANDARD,
        "risk": RiskLevel.MEDIUM,
        "data_classification": DataClassification.INTERNAL,
        "required_capabilities": frozenset({WorkCapability.CODE_GENERATION}),
    }
    values.update(overrides)
    return TaskProfile(**values)  # type: ignore[arg-type]


def test_simple_low_risk_work_uses_lean_path_but_keeps_review_and_approval() -> None:
    strategy = TaskStrategyPlanner().plan(
        task_profile(complexity=ComplexityTier.SIMPLE, risk=RiskLevel.LOW)
    )

    assert StrategyStage.ARCHITECTURE_REVIEW not in strategy.stages
    assert StrategyStage.SECURITY_REVIEW not in strategy.stages
    assert StrategyStage.CODE_REVIEW in strategy.stages
    assert strategy.stages[-1] is StrategyStage.HUMAN_APPROVAL
    assert strategy.requires_independent_review
    assert strategy.recommended_producer_count == 1
    assert strategy.recommended_reviewer_count == 1


def test_complex_work_receives_architecture_security_and_code_review() -> None:
    strategy = TaskStrategyPlanner().plan(task_profile(complexity=ComplexityTier.COMPLEX))

    assert StrategyStage.ARCHITECTURE_REVIEW in strategy.stages
    assert StrategyStage.SECURITY_REVIEW in strategy.stages
    assert StrategyStage.CODE_REVIEW in strategy.stages
    assert strategy.recommended_producer_count == 2


def test_obfuscated_input_is_contained_before_execution() -> None:
    strategy = TaskStrategyPlanner().plan(
        task_profile(
            complexity=ComplexityTier.ADVERSARIAL,
            risk=RiskLevel.HIGH,
            inspection_signals=frozenset({InspectionSignal.CONCEALED_INSTRUCTIONS}),
        )
    )

    assert strategy.stages == (
        StrategyStage.CONTEXT_INSPECTION,
        StrategyStage.HUMAN_DISPOSITION,
    )
    assert strategy.requires_human_disposition
    assert strategy.requires_cross_family_review
    assert strategy.recommended_producer_count == 0
    assert strategy.recommended_reviewer_count == 1
    assert strategy.restrictions == frozenset(ExecutionRestriction)


def test_confidential_work_disallows_external_egress() -> None:
    strategy = TaskStrategyPlanner().plan(
        task_profile(data_classification=DataClassification.CONFIDENTIAL)
    )

    assert ExecutionRestriction.NO_EXTERNAL_EGRESS in strategy.restrictions


def test_high_risk_work_requires_more_review_and_proven_routing_evidence() -> None:
    strategy = TaskStrategyPlanner().plan(
        task_profile(complexity=ComplexityTier.COMPLEX, risk=RiskLevel.HIGH)
    )

    assert strategy.recommended_producer_count == 2
    assert strategy.recommended_reviewer_count == 2
    assert strategy.minimum_routing_evidence_samples == 20
