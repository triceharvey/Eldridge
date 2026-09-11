from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from control_plane.routing import RiskLevel

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
EVALUATION_POLICY_VERSION = "multi-model-evaluation/v1"


class EvaluationStatus(StrEnum):
    WINNER_SELECTED = "WINNER_SELECTED"
    REFINEMENT_REQUIRED = "REFINEMENT_REQUIRED"
    EXHAUSTED = "EXHAUSTED"


class EvaluationExecutionStatus(StrEnum):
    PREPARED = "PREPARED"
    RUNNING = "RUNNING"
    OUTPUTS_READY = "OUTPUTS_READY"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"


class EvaluationAssessmentStatus(StrEnum):
    PREPARED = "PREPARED"
    RUNNING = "RUNNING"
    CHECKS_READY = "CHECKS_READY"
    REVIEW_PREPARED = "REVIEW_PREPARED"
    REVIEWS_RUNNING = "REVIEWS_RUNNING"
    REVIEW_UNKNOWN = "REVIEW_UNKNOWN"
    REVIEWS_READY = "REVIEWS_READY"
    DECIDED = "DECIDED"
    FAILED = "FAILED"


class EvaluationReconciliationDecision(StrEnum):
    MARK_FAILED = "MARK_FAILED"


@dataclass(frozen=True)
class PromptVariant:
    variant_id: str
    instruction: str

    def __post_init__(self) -> None:
        _require_identifier("prompt variant ID", self.variant_id)
        if not self.instruction.strip() or len(self.instruction) > 4_000:
            raise ValueError("prompt variant instruction is invalid")


def _require_identifier(name: str, value: str) -> None:
    if IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid")


def validate_evaluation_identifier(name: str, value: str) -> None:
    """Validate a public evaluation contract identifier at an API or service boundary."""
    _require_identifier(name, value)


def _require_digest(name: str, value: str) -> None:
    if DIGEST.fullmatch(value) is None:
        raise ValueError(f"{name} must be a full sha256 digest")


def _require_bool(name: str, value: object) -> None:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean")


def _require_int(name: str, value: object) -> None:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")


@dataclass(frozen=True)
class DeterministicCheck:
    name: str
    passed: bool
    evidence_digest: str
    validated_output_digest: str

    def __post_init__(self) -> None:
        _require_identifier("check name", self.name)
        _require_bool("check passed", self.passed)
        _require_digest("check evidence", self.evidence_digest)
        _require_digest("validated output", self.validated_output_digest)


@dataclass(frozen=True)
class IndependentReview:
    reviewer_provider_id: str
    reviewer_provider_family: str
    reviewer_model_version: str
    reviewer_profile_version: str
    reviewed_output_digest: str
    passed: bool
    evidence_digest: str

    def __post_init__(self) -> None:
        _require_identifier("reviewer provider ID", self.reviewer_provider_id)
        _require_identifier("reviewer provider family", self.reviewer_provider_family)
        _require_identifier("reviewer model version", self.reviewer_model_version)
        _require_identifier("reviewer profile version", self.reviewer_profile_version)
        _require_digest("reviewed output", self.reviewed_output_digest)
        _require_bool("review passed", self.passed)
        _require_digest("review evidence", self.evidence_digest)


@dataclass(frozen=True)
class CandidateEvidence:
    candidate_id: str
    provider_id: str
    provider_family: str
    model_version: str
    profile_version: str
    prompt_variant_id: str
    prompt_contract_version: str
    iteration: int
    succeeded: bool
    output_digest: str | None
    latency_ms: int
    cost_microunits: int
    routing_score: float
    checks: tuple[DeterministicCheck, ...]
    reviews: tuple[IndependentReview, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("candidate ID", self.candidate_id),
            ("provider ID", self.provider_id),
            ("provider family", self.provider_family),
            ("model version", self.model_version),
            ("profile version", self.profile_version),
            ("prompt variant ID", self.prompt_variant_id),
            ("prompt contract version", self.prompt_contract_version),
        ):
            _require_identifier(name, value)
        _require_int("candidate iteration", self.iteration)
        _require_bool("candidate succeeded", self.succeeded)
        _require_int("candidate latency", self.latency_ms)
        _require_int("candidate cost", self.cost_microunits)
        if isinstance(self.routing_score, bool) or not isinstance(self.routing_score, (int, float)):
            raise ValueError("candidate routing score must be numeric")
        if self.iteration < 1:
            raise ValueError("candidate iteration must be positive")
        if self.succeeded:
            if self.output_digest is None:
                raise ValueError("successful candidate requires an output digest")
            _require_digest("candidate output", self.output_digest)
        elif self.output_digest is not None:
            _require_digest("candidate output", self.output_digest)
        if self.latency_ms < 0 or self.cost_microunits < 0:
            raise ValueError("candidate latency and cost cannot be negative")
        if not 0.0 <= self.routing_score <= 1.0:
            raise ValueError("candidate routing score must be between zero and one")
        check_names = [check.name for check in self.checks]
        if len(check_names) != len(set(check_names)):
            raise ValueError("candidate check names must be unique")
        if any(check.validated_output_digest != self.output_digest for check in self.checks):
            raise ValueError("candidate checks must bind the candidate output digest")
        reviewer_ids = [review.reviewer_provider_id for review in self.reviews]
        if len(reviewer_ids) != len(set(reviewer_ids)):
            raise ValueError("reviewer provider IDs must be unique per candidate")
        if any(review.reviewed_output_digest != self.output_digest for review in self.reviews):
            raise ValueError("candidate reviews must bind the candidate output digest")

    @property
    def provider_model_key(self) -> tuple[str, str]:
        return self.provider_id, self.model_version


@dataclass(frozen=True)
class EvaluationPolicy:
    required_checks: frozenset[str]
    risk: RiskLevel
    max_candidates: int = 4
    max_prompt_variants: int = 3
    max_iterations: int = 3
    max_total_cost_microunits: int = 0
    minimum_independent_reviews: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.risk, RiskLevel):
            raise ValueError("risk must be a RiskLevel")
        if not self.required_checks:
            raise ValueError("at least one deterministic check is required")
        for check in self.required_checks:
            _require_identifier("required check", check)
        for name, value in (
            ("max_candidates", self.max_candidates),
            ("max_prompt_variants", self.max_prompt_variants),
            ("max_iterations", self.max_iterations),
            ("max_total_cost_microunits", self.max_total_cost_microunits),
            ("minimum_independent_reviews", self.minimum_independent_reviews),
        ):
            _require_int(name, value)
        if not 1 <= self.max_candidates <= 8:
            raise ValueError("max_candidates must be between 1 and 8")
        if not 1 <= self.max_prompt_variants <= 8:
            raise ValueError("max_prompt_variants must be between 1 and 8")
        if not 1 <= self.max_iterations <= 5:
            raise ValueError("max_iterations must be between 1 and 5")
        if self.max_total_cost_microunits < 0:
            raise ValueError("max_total_cost_microunits cannot be negative")
        if not 0 <= self.minimum_independent_reviews <= 2:
            raise ValueError("minimum_independent_reviews must be between 0 and 2")
        if self.risk >= RiskLevel.HIGH and self.minimum_independent_reviews < 2:
            raise ValueError("high-risk evaluation requires two independent reviews")


@dataclass(frozen=True)
class EvaluationBatch:
    campaign_id: str
    prompt_contract_version: str
    current_iteration: int
    prior_cost_microunits: int
    candidates: tuple[CandidateEvidence, ...]

    def __post_init__(self) -> None:
        _require_identifier("campaign ID", self.campaign_id)
        _require_identifier("prompt contract version", self.prompt_contract_version)
        _require_int("current_iteration", self.current_iteration)
        _require_int("prior_cost_microunits", self.prior_cost_microunits)
        if self.current_iteration < 1:
            raise ValueError("current_iteration must be positive")
        if self.prior_cost_microunits < 0:
            raise ValueError("prior campaign cost cannot be negative")
        if not self.candidates:
            raise ValueError("evaluation batch requires candidates")


@dataclass(frozen=True)
class RankedEvaluationCandidate:
    candidate_id: str
    provider_id: str
    model_version: str
    routing_score: float
    cost_microunits: int
    latency_ms: int


@dataclass(frozen=True)
class EvaluationDecision:
    status: EvaluationStatus
    winner_candidate_id: str | None
    ranked_candidates: tuple[RankedEvaluationCandidate, ...]
    rejected_candidates: dict[str, tuple[str, ...]]
    next_iteration: int | None
    total_cost_microunits: int
    policy_version: str = EVALUATION_POLICY_VERSION


class MultiModelEvaluator:
    """Fail-closed selection over externally produced, deterministic validation evidence."""

    def evaluate(self, policy: EvaluationPolicy, batch: EvaluationBatch) -> EvaluationDecision:
        self._validate_batch(policy, batch)
        total_cost = batch.prior_cost_microunits + sum(
            candidate.cost_microunits for candidate in batch.candidates
        )
        if total_cost > policy.max_total_cost_microunits:
            return EvaluationDecision(
                status=EvaluationStatus.EXHAUSTED,
                winner_candidate_id=None,
                ranked_candidates=(),
                rejected_candidates={
                    candidate.candidate_id: ("campaign_cost_ceiling_exceeded",)
                    for candidate in batch.candidates
                },
                next_iteration=None,
                total_cost_microunits=total_cost,
            )

        eligible: list[CandidateEvidence] = []
        rejected: dict[str, tuple[str, ...]] = {}
        for candidate in batch.candidates:
            reasons = self._rejection_reasons(policy, candidate)
            if reasons:
                rejected[candidate.candidate_id] = tuple(reasons)
            else:
                eligible.append(candidate)

        ranked = tuple(
            RankedEvaluationCandidate(
                candidate_id=candidate.candidate_id,
                provider_id=candidate.provider_id,
                model_version=candidate.model_version,
                routing_score=candidate.routing_score,
                cost_microunits=candidate.cost_microunits,
                latency_ms=candidate.latency_ms,
            )
            for candidate in sorted(
                eligible,
                key=lambda item: (
                    -item.routing_score,
                    item.cost_microunits,
                    item.latency_ms,
                    item.provider_id,
                    item.model_version,
                    item.prompt_variant_id,
                    item.candidate_id,
                ),
            )
        )
        if ranked:
            return EvaluationDecision(
                status=EvaluationStatus.WINNER_SELECTED,
                winner_candidate_id=ranked[0].candidate_id,
                ranked_candidates=ranked,
                rejected_candidates=rejected,
                next_iteration=None,
                total_cost_microunits=total_cost,
            )

        can_refine = (
            batch.current_iteration < policy.max_iterations
            and total_cost <= policy.max_total_cost_microunits
        )
        return EvaluationDecision(
            status=(
                EvaluationStatus.REFINEMENT_REQUIRED if can_refine else EvaluationStatus.EXHAUSTED
            ),
            winner_candidate_id=None,
            ranked_candidates=(),
            rejected_candidates=rejected,
            next_iteration=batch.current_iteration + 1 if can_refine else None,
            total_cost_microunits=total_cost,
        )

    @staticmethod
    def _validate_batch(policy: EvaluationPolicy, batch: EvaluationBatch) -> None:
        if batch.current_iteration > policy.max_iterations:
            raise ValueError("evaluation batch exceeds the iteration ceiling")
        candidate_ids = [candidate.candidate_id for candidate in batch.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate IDs must be unique")
        provider_models = {candidate.provider_model_key for candidate in batch.candidates}
        if len(provider_models) > policy.max_candidates:
            raise ValueError("evaluation batch exceeds the candidate ceiling")
        variants = {candidate.prompt_variant_id for candidate in batch.candidates}
        if len(variants) > policy.max_prompt_variants:
            raise ValueError("evaluation batch exceeds the prompt-variant ceiling")
        for candidate in batch.candidates:
            if candidate.iteration != batch.current_iteration:
                raise ValueError("candidate iteration does not match the evaluation batch")
            if candidate.prompt_contract_version != batch.prompt_contract_version:
                raise ValueError("candidate prompt contract does not match the evaluation batch")

    @staticmethod
    def _rejection_reasons(policy: EvaluationPolicy, candidate: CandidateEvidence) -> list[str]:
        reasons: list[str] = []
        if not candidate.succeeded:
            reasons.append("provider_execution_failed")
        checks = {check.name: check for check in candidate.checks}
        missing = sorted(policy.required_checks - checks.keys())
        if missing:
            reasons.append("missing_checks:" + ",".join(missing))
        failed = sorted(
            check
            for check in policy.required_checks
            if check in checks and not checks[check].passed
        )
        if failed:
            reasons.append("failed_checks:" + ",".join(failed))

        passed_reviews = [review for review in candidate.reviews if review.passed]
        independent = [
            review
            for review in passed_reviews
            if review.reviewer_provider_id != candidate.provider_id
        ]
        if policy.risk >= RiskLevel.HIGH:
            independent = [
                review
                for review in independent
                if review.reviewer_provider_family != candidate.provider_family
            ]
        if len(independent) < policy.minimum_independent_reviews:
            reasons.append("insufficient_independent_reviews")
        return reasons
