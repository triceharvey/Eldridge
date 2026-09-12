from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum


class ExecutionMode(StrEnum):
    MODEL = "MODEL"
    REMOTE_AGENT = "REMOTE_AGENT"
    IDE_HANDOFF = "IDE_HANDOFF"
    LOCAL_MODEL = "LOCAL_MODEL"


class EgressBoundary(StrEnum):
    LOCAL = "LOCAL"
    APPROVED_EXTERNAL = "APPROVED_EXTERNAL"


class DataClassification(IntEnum):
    PUBLIC = 0
    INTERNAL = 1
    CONFIDENTIAL = 2
    RESTRICTED = 3


class RiskLevel(IntEnum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2
    CRITICAL = 3


class CostTier(IntEnum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2


class WorkCapability(StrEnum):
    PLANNING = "PLANNING"
    ARCHITECTURE = "ARCHITECTURE"
    CODE_GENERATION = "CODE_GENERATION"
    TEST_DESIGN = "TEST_DESIGN"
    TEST_EXECUTION = "TEST_EXECUTION"
    CODE_REVIEW = "CODE_REVIEW"
    SECURITY_ANALYSIS = "SECURITY_ANALYSIS"
    TOOL_PROPOSALS = "TOOL_PROPOSALS"
    LONG_RUNNING_EXECUTION = "LONG_RUNNING_EXECUTION"
    INTERACTIVE_IDE = "INTERACTIVE_IDE"


class RoutingPurpose(StrEnum):
    PRODUCE = "PRODUCE"
    REVIEW = "REVIEW"


class RoutingObjective(StrEnum):
    """Operator-selected optimization goal applied only after policy eligibility."""

    BALANCED = "BALANCED"
    QUALITY = "QUALITY"
    SPEED = "SPEED"
    FRUGAL = "FRUGAL"


@dataclass(frozen=True)
class ObjectiveWeights:
    quality: float
    cost: float
    latency: float

    def __post_init__(self) -> None:
        values = (self.quality, self.cost, self.latency)
        if any(value < 0.0 or value > 1.0 for value in values):
            raise ValueError("objective weights must be between zero and one")
        if abs(sum(values) - 1.0) > 1e-9:
            raise ValueError("objective weights must sum to one")


OBJECTIVE_PROFILE_VERSION = "routing-objectives/v1"
OBJECTIVE_WEIGHTS: dict[RoutingObjective, ObjectiveWeights] = {
    RoutingObjective.BALANCED: ObjectiveWeights(quality=0.75, cost=0.15, latency=0.10),
    RoutingObjective.QUALITY: ObjectiveWeights(quality=0.90, cost=0.05, latency=0.05),
    RoutingObjective.SPEED: ObjectiveWeights(quality=0.55, cost=0.10, latency=0.35),
    RoutingObjective.FRUGAL: ObjectiveWeights(quality=0.55, cost=0.40, latency=0.05),
}


@dataclass(frozen=True)
class CapabilityEvidence:
    sample_count: int = 0
    success_rate: float = 0.0
    validation_pass_rate: float = 0.0
    p95_latency_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.sample_count < 0:
            raise ValueError("sample_count cannot be negative")
        for name, value in (
            ("success_rate", self.success_rate),
            ("validation_pass_rate", self.validation_pass_rate),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.p95_latency_seconds is not None and self.p95_latency_seconds < 0:
            raise ValueError("p95_latency_seconds cannot be negative")

    def conservative_quality(self, minimum_samples: int = 20) -> float:
        """Return a prior-weighted score so tiny samples cannot dominate routing."""
        if minimum_samples <= 0:
            raise ValueError("minimum_samples must be positive")
        observed = (self.success_rate + self.validation_pass_rate) / 2
        evidence_weight = min(self.sample_count / minimum_samples, 1.0)
        return (evidence_weight * observed) + ((1.0 - evidence_weight) * 0.5)


@dataclass(frozen=True)
class ProviderProfile:
    provider_id: str
    provider_family: str
    execution_mode: ExecutionMode
    capabilities: frozenset[WorkCapability]
    egress_boundary: EgressBoundary
    maximum_data_classification: DataClassification
    cost_tier: CostTier
    maximum_risk: RiskLevel = RiskLevel.CRITICAL
    model_version: str = "unqualified"
    profile_version: str = "v1"
    enabled: bool = False
    healthy: bool = False
    evidence: dict[WorkCapability, CapabilityEvidence] = field(default_factory=dict)


@dataclass(frozen=True)
class RoutingRequest:
    required_capabilities: frozenset[WorkCapability]
    data_classification: DataClassification
    risk: RiskLevel
    purpose: RoutingPurpose = RoutingPurpose.PRODUCE
    allowed_egress: frozenset[EgressBoundary] = frozenset({EgressBoundary.LOCAL})
    allowed_modes: frozenset[ExecutionMode] = frozenset()
    maximum_cost_tier: CostTier = CostTier.HIGH
    minimum_evidence_samples: int = 0
    producer_provider_id: str | None = None
    producer_family: str | None = None
    objective: RoutingObjective = RoutingObjective.BALANCED


@dataclass(frozen=True)
class RankedCandidate:
    provider_id: str
    score: float
    quality_utility: float
    cost_utility: float
    latency_utility: float


@dataclass(frozen=True)
class RoutingDecision:
    selected_provider_id: str | None
    ranked_candidates: tuple[RankedCandidate, ...]
    rejected: dict[str, tuple[str, ...]]
    policy_version: str
    objective: RoutingObjective
    objective_profile_version: str

    @property
    def blocked(self) -> bool:
        return self.selected_provider_id is None


class CapabilityRouter:
    """Fail-closed provider selection using policy filters before quality scoring."""

    policy_version = "capability-routing/v2"

    def route(
        self, request: RoutingRequest, profiles: tuple[ProviderProfile, ...]
    ) -> RoutingDecision:
        if request.minimum_evidence_samples < 0:
            raise ValueError("minimum_evidence_samples cannot be negative")
        provider_ids = [profile.provider_id for profile in profiles]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("provider_id values must be unique")
        ranked: list[RankedCandidate] = []
        rejected: dict[str, tuple[str, ...]] = {}

        for profile in profiles:
            reasons = self._rejection_reasons(request, profile)
            if reasons:
                rejected[profile.provider_id] = tuple(reasons)
                continue
            ranked.append(self._score(request, profile))

        ranked.sort(key=lambda candidate: (-candidate.score, candidate.provider_id))
        return RoutingDecision(
            selected_provider_id=ranked[0].provider_id if ranked else None,
            ranked_candidates=tuple(ranked),
            rejected=rejected,
            policy_version=self.policy_version,
            objective=request.objective,
            objective_profile_version=OBJECTIVE_PROFILE_VERSION,
        )

    @staticmethod
    def _rejection_reasons(request: RoutingRequest, profile: ProviderProfile) -> list[str]:
        reasons: list[str] = []
        if not profile.enabled:
            reasons.append("provider_disabled")
        if not profile.healthy:
            reasons.append("provider_unhealthy")
        missing = request.required_capabilities - profile.capabilities
        if missing:
            reasons.append("missing_capabilities:" + ",".join(sorted(missing)))
        if profile.egress_boundary not in request.allowed_egress:
            reasons.append("egress_boundary_not_approved")
        if request.data_classification > profile.maximum_data_classification:
            reasons.append("data_classification_not_allowed")
        if profile.cost_tier > request.maximum_cost_tier:
            reasons.append("cost_tier_exceeded")
        if request.risk > profile.maximum_risk:
            reasons.append("risk_level_not_allowed")
        if request.allowed_modes and profile.execution_mode not in request.allowed_modes:
            reasons.append("execution_mode_not_allowed")
        if request.minimum_evidence_samples:
            under_sampled = sorted(
                capability
                for capability in request.required_capabilities
                if profile.evidence.get(capability, CapabilityEvidence()).sample_count
                < request.minimum_evidence_samples
            )
            if under_sampled:
                reasons.append("insufficient_evidence:" + ",".join(under_sampled))
        if request.purpose is RoutingPurpose.REVIEW:
            if profile.provider_id == request.producer_provider_id:
                reasons.append("reviewer_must_differ_from_producer")
            if (
                request.risk >= RiskLevel.HIGH
                and request.producer_family is not None
                and profile.provider_family == request.producer_family
            ):
                reasons.append("high_risk_review_requires_provider_family_diversity")
        return reasons

    @staticmethod
    def _score(request: RoutingRequest, profile: ProviderProfile) -> RankedCandidate:
        if not request.required_capabilities:
            quality = 0.5
        else:
            scores = [
                profile.evidence.get(capability, CapabilityEvidence()).conservative_quality()
                for capability in request.required_capabilities
            ]
            quality = sum(scores) / len(scores)
        cost_utility = (int(CostTier.HIGH) - int(profile.cost_tier)) / int(CostTier.HIGH)
        observed_latencies = [
            evidence.p95_latency_seconds
            for capability in request.required_capabilities
            if (evidence := profile.evidence.get(capability)) is not None
            and evidence.p95_latency_seconds is not None
        ]
        latency_utility = 0.5
        if observed_latencies:
            raw_latency_utility = 1.0 / (
                1.0 + (sum(observed_latencies) / len(observed_latencies)) / 30.0
            )
            # A quick failure is not useful speed. Reliability-adjust the observed latency.
            latency_utility = raw_latency_utility * quality
        weights = OBJECTIVE_WEIGHTS[request.objective]
        score = (
            quality * weights.quality
            + cost_utility * weights.cost
            + latency_utility * weights.latency
        )
        return RankedCandidate(
            provider_id=profile.provider_id,
            score=round(score, 6),
            quality_utility=round(quality, 6),
            cost_utility=round(cost_utility, 6),
            latency_utility=round(latency_utility, 6),
        )


def interoperability_profiles() -> tuple[ProviderProfile, ...]:
    """Describe implemented boundaries; activation and performance evidence are separate."""
    general_model = frozenset(
        {
            WorkCapability.PLANNING,
            WorkCapability.ARCHITECTURE,
            WorkCapability.CODE_GENERATION,
            WorkCapability.TEST_DESIGN,
            WorkCapability.TEST_EXECUTION,
            WorkCapability.CODE_REVIEW,
            WorkCapability.SECURITY_ANALYSIS,
            WorkCapability.TOOL_PROPOSALS,
        }
    )
    return (
        ProviderProfile(
            provider_id="local-openai-compatible",
            provider_family="operator-local",
            execution_mode=ExecutionMode.LOCAL_MODEL,
            capabilities=general_model,
            egress_boundary=EgressBoundary.LOCAL,
            maximum_data_classification=DataClassification.RESTRICTED,
            cost_tier=CostTier.LOW,
            model_version="operator-configured",
        ),
        ProviderProfile(
            provider_id="anthropic-claude",
            provider_family="anthropic",
            execution_mode=ExecutionMode.MODEL,
            capabilities=general_model,
            egress_boundary=EgressBoundary.APPROVED_EXTERNAL,
            maximum_data_classification=DataClassification.INTERNAL,
            cost_tier=CostTier.MEDIUM,
            model_version="operator-configured",
        ),
        ProviderProfile(
            provider_id="claude-code-subscription",
            provider_family="anthropic",
            execution_mode=ExecutionMode.MODEL,
            capabilities=general_model - {WorkCapability.TOOL_PROPOSALS},
            egress_boundary=EgressBoundary.APPROVED_EXTERNAL,
            maximum_data_classification=DataClassification.INTERNAL,
            cost_tier=CostTier.LOW,
            model_version="operator-configured",
        ),
        ProviderProfile(
            provider_id="devin",
            provider_family="cognition",
            execution_mode=ExecutionMode.REMOTE_AGENT,
            capabilities=frozenset(
                {
                    WorkCapability.PLANNING,
                    WorkCapability.CODE_GENERATION,
                    WorkCapability.TEST_DESIGN,
                    WorkCapability.TEST_EXECUTION,
                    WorkCapability.LONG_RUNNING_EXECUTION,
                }
            ),
            egress_boundary=EgressBoundary.APPROVED_EXTERNAL,
            maximum_data_classification=DataClassification.INTERNAL,
            cost_tier=CostTier.HIGH,
            model_version="operator-configured",
        ),
        ProviderProfile(
            provider_id="windsurf-cascade",
            provider_family="windsurf",
            execution_mode=ExecutionMode.IDE_HANDOFF,
            capabilities=frozenset(
                {
                    WorkCapability.CODE_GENERATION,
                    WorkCapability.CODE_REVIEW,
                    WorkCapability.INTERACTIVE_IDE,
                }
            ),
            egress_boundary=EgressBoundary.APPROVED_EXTERNAL,
            maximum_data_classification=DataClassification.INTERNAL,
            cost_tier=CostTier.MEDIUM,
            model_version="operator-configured",
        ),
    )


def mock_profiles() -> tuple[ProviderProfile, ...]:
    """Separate deterministic producer/reviewer identities for the local demonstration."""
    capabilities = frozenset(WorkCapability)
    common = {
        "provider_family": "deterministic-mock",
        "execution_mode": ExecutionMode.LOCAL_MODEL,
        "capabilities": capabilities,
        "egress_boundary": EgressBoundary.LOCAL,
        "maximum_data_classification": DataClassification.RESTRICTED,
        "cost_tier": CostTier.LOW,
        "model_version": "deterministic-mock-v1",
        "enabled": True,
        "healthy": True,
    }
    return (
        ProviderProfile(provider_id="mock-producer", **common),  # type: ignore[arg-type]
        ProviderProfile(provider_id="mock-reviewer", **common),  # type: ignore[arg-type]
    )
