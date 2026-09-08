from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum

from control_plane.routing import DataClassification, RiskLevel, WorkCapability


class ComplexityTier(IntEnum):
    SIMPLE = 0
    STANDARD = 1
    COMPLEX = 2
    ADVERSARIAL = 3


class InspectionSignal(StrEnum):
    ENCODED_OR_PACKED_CONTENT = "ENCODED_OR_PACKED_CONTENT"
    CONCEALED_INSTRUCTIONS = "CONCEALED_INSTRUCTIONS"
    PROMPT_INJECTION = "PROMPT_INJECTION"
    UNKNOWN_BINARY = "UNKNOWN_BINARY"
    SUSPECTED_MALICIOUS_BEHAVIOR = "SUSPECTED_MALICIOUS_BEHAVIOR"


class StrategyStage(StrEnum):
    CONTEXT_INSPECTION = "CONTEXT_INSPECTION"
    HUMAN_DISPOSITION = "HUMAN_DISPOSITION"
    PLAN = "PLAN"
    ARCHITECTURE_REVIEW = "ARCHITECTURE_REVIEW"
    IMPLEMENT = "IMPLEMENT"
    TEST = "TEST"
    SECURITY_REVIEW = "SECURITY_REVIEW"
    CODE_REVIEW = "CODE_REVIEW"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"


class ExecutionRestriction(StrEnum):
    NO_EXTERNAL_EGRESS = "NO_EXTERNAL_EGRESS"
    NO_SECRET_RESOLUTION = "NO_SECRET_RESOLUTION"  # noqa: S105 - policy label, not a secret
    NO_NETWORK = "NO_NETWORK"
    NO_WRITE_TOOLS = "NO_WRITE_TOOLS"


@dataclass(frozen=True)
class TaskProfile:
    complexity: ComplexityTier
    risk: RiskLevel
    data_classification: DataClassification
    required_capabilities: frozenset[WorkCapability]
    inspection_signals: frozenset[InspectionSignal] = frozenset()


@dataclass(frozen=True)
class ExecutionStrategy:
    stages: tuple[StrategyStage, ...]
    restrictions: frozenset[ExecutionRestriction]
    requires_independent_review: bool
    requires_cross_family_review: bool
    requires_human_disposition: bool
    recommended_producer_count: int
    recommended_reviewer_count: int
    minimum_routing_evidence_samples: int


class TaskStrategyPlanner:
    """Select workflow depth without allowing complexity to bypass safety gates."""

    def plan(self, profile: TaskProfile) -> ExecutionStrategy:
        suspicious = bool(profile.inspection_signals) or (
            profile.complexity is ComplexityTier.ADVERSARIAL
        )
        if suspicious:
            return ExecutionStrategy(
                stages=(
                    StrategyStage.CONTEXT_INSPECTION,
                    StrategyStage.HUMAN_DISPOSITION,
                ),
                restrictions=frozenset(
                    {
                        ExecutionRestriction.NO_EXTERNAL_EGRESS,
                        ExecutionRestriction.NO_SECRET_RESOLUTION,
                        ExecutionRestriction.NO_NETWORK,
                        ExecutionRestriction.NO_WRITE_TOOLS,
                    }
                ),
                requires_independent_review=True,
                requires_cross_family_review=profile.risk >= RiskLevel.HIGH,
                requires_human_disposition=True,
                recommended_producer_count=0,
                recommended_reviewer_count=1,
                minimum_routing_evidence_samples=0,
            )

        stages: tuple[StrategyStage, ...]
        if profile.complexity is ComplexityTier.SIMPLE and profile.risk is RiskLevel.LOW:
            stages = (
                StrategyStage.PLAN,
                StrategyStage.IMPLEMENT,
                StrategyStage.TEST,
                StrategyStage.CODE_REVIEW,
                StrategyStage.HUMAN_APPROVAL,
            )
        else:
            stages = (
                StrategyStage.PLAN,
                StrategyStage.ARCHITECTURE_REVIEW,
                StrategyStage.IMPLEMENT,
                StrategyStage.TEST,
                StrategyStage.SECURITY_REVIEW,
                StrategyStage.CODE_REVIEW,
                StrategyStage.HUMAN_APPROVAL,
            )

        restrictions = set[ExecutionRestriction]()
        if profile.data_classification >= DataClassification.CONFIDENTIAL:
            restrictions.add(ExecutionRestriction.NO_EXTERNAL_EGRESS)
        return ExecutionStrategy(
            stages=stages,
            restrictions=frozenset(restrictions),
            requires_independent_review=True,
            requires_cross_family_review=profile.risk >= RiskLevel.HIGH,
            requires_human_disposition=False,
            recommended_producer_count=(2 if profile.complexity >= ComplexityTier.COMPLEX else 1),
            recommended_reviewer_count=2 if profile.risk >= RiskLevel.HIGH else 1,
            minimum_routing_evidence_samples=20 if profile.risk >= RiskLevel.HIGH else 0,
        )
