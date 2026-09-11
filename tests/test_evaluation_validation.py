from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from control_plane.api import create_app
from control_plane.domain import (
    AuthorizationError,
    ConflictError,
    ProviderRequest,
    ProviderResult,
    TaskKind,
)
from control_plane.evaluation import (
    EvaluationReconciliationDecision,
    EvaluationRecoveryDecision,
    PromptVariant,
)
from control_plane.evaluation_validation import (
    EvaluationArtifact,
    EvaluationValidator,
    SecurityBoundaryValidator,
    ValidationOutcome,
)
from control_plane.persistence import (
    EvaluationAssessmentRecord,
    EvaluationCheckRecord,
    EvaluationObservationRecord,
    EvaluationPromotionRecord,
    EvaluationProviderRunRecord,
    EvaluationReconciliationRecord,
    EvaluationRecoveryRecord,
    EvaluationRepairRecord,
    EvaluationReviewRunRecord,
    Workflow,
)
from control_plane.providers import MockProvider
from control_plane.routing import (
    CapabilityEvidence,
    CostTier,
    DataClassification,
    EgressBoundary,
    ExecutionMode,
    ProviderProfile,
    RiskLevel,
    WorkCapability,
    mock_profiles,
)
from control_plane.service import ControlPlaneService, ProviderBinding


class CountingProvider(MockProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def submit(self, request: ProviderRequest) -> ProviderResult:
        self.calls += 1
        return super().submit(request)


class FixedValidator:
    def __init__(self, name: str, *, passed: bool = True) -> None:
        self.name = name
        self.version = f"{name}/test-v1"
        self.passed = passed
        self.calls = 0

    def validate(self, artifact: EvaluationArtifact) -> ValidationOutcome:
        self.calls += 1
        return ValidationOutcome(self.passed, {"reason": "test_fixture"})


class StaticEvidenceStore:
    def hydrate_profiles(
        self, _session: Session, profiles: tuple[ProviderProfile, ...]
    ) -> tuple[ProviderProfile, ...]:
        return profiles


def _independent_profiles() -> tuple[ProviderProfile, ...]:
    return tuple(
        ProviderProfile(
            provider_id=f"review-model-{index}",
            provider_family=f"review-family-{index}",
            execution_mode=ExecutionMode.LOCAL_MODEL,
            capabilities=frozenset(WorkCapability),
            egress_boundary=EgressBoundary.LOCAL,
            maximum_data_classification=DataClassification.RESTRICTED,
            cost_tier=CostTier.LOW,
            model_version="deterministic-mock-v1",
            enabled=True,
            healthy=True,
            evidence={
                capability: CapabilityEvidence(
                    sample_count=20,
                    success_rate=1.0,
                    validation_pass_rate=1.0,
                )
                for capability in (
                    WorkCapability.PLANNING,
                    WorkCapability.CODE_REVIEW,
                )
            },
        )
        for index in range(3)
    )


def _setup(
    service: ControlPlaneService,
    *,
    key: str,
    risk: RiskLevel = RiskLevel.MEDIUM,
    required_checks: frozenset[str] = frozenset({"schema", "security"}),
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    workflow = service.create_workflow(
        requester_id="dev-operator",
        title="Trusted evaluation",
        description="Bind deterministic validation evidence to captured model output.",
        idempotency_key=f"{key}-workflow",
        risk=risk,
    )
    tasks = workflow["tasks"]
    assert isinstance(tasks, list)
    campaign = service.create_evaluation_campaign(
        workflow_id=str(workflow["id"]),
        actor_id="dev-operator",
        idempotency_key=f"{key}-campaign",
        prompt_contract_version="prompt-v1",
        work_capability=WorkCapability.PLANNING,
        required_checks=required_checks,
    )
    execution = service.execute_evaluation_campaign(
        workflow_id=str(workflow["id"]),
        campaign_id=str(campaign["id"]),
        task_id=str(tasks[0]["id"]),
        actor_id="dev-operator",
        idempotency_key=f"{key}-execution",
        prompt_variants=(
            PromptVariant("baseline", "Produce a bounded plan."),
            PromptVariant("challenge", "Challenge assumptions, then produce a bounded plan."),
        ),
    )
    return workflow, campaign, execution


def test_trusted_validation_creates_artifacts_checks_and_campaign_decision(
    session_factory: sessionmaker[Session],
) -> None:
    provider = CountingProvider()
    bindings = tuple(
        ProviderBinding(profile=profile, provider=provider) for profile in mock_profiles()
    )
    service = ControlPlaneService(session_factory, provider_bindings=bindings)
    workflow, campaign, execution = _setup(service, key="trusted-success")

    assessment = service.validate_evaluation_execution(
        workflow_id=str(workflow["id"]),
        execution_id=str(execution["id"]),
        actor_id="dev-operator",
        idempotency_key="trusted-success-assessment",
    )
    replay = service.validate_evaluation_execution(
        workflow_id=str(workflow["id"]),
        execution_id=str(execution["id"]),
        actor_id="dev-operator",
        idempotency_key="trusted-success-assessment",
    )
    stored_campaign = service.get_evaluation_campaign(
        str(workflow["id"]), str(campaign["id"]), principal_id="dev-operator"
    )

    assert assessment["status"] == "DECIDED"
    assert assessment["decision"]["status"] == "WINNER_SELECTED"
    assert len(assessment["artifacts"]) == 4
    assert all(len(artifact["checks"]) == 2 for artifact in assessment["artifacts"])
    assert all(
        check["passed"] is True
        and check["evidence_digest"].startswith("sha256:")
        and check["validated_output_digest"] == artifact["digest"]
        for artifact in assessment["artifacts"]
        for check in artifact["checks"]
    )
    assert stored_campaign["status"] == "WINNER_SELECTED"
    assert replay["id"] == assessment["id"]
    assert replay["replayed"] is True
    assert replay["decision"]["status"] == "WINNER_SELECTED"
    assert provider.calls == 4
    evidence = service.list_provider_evidence(principal_id="dev-operator")
    assert all(item["evidence"]["PLANNING"]["sample_count"] == 2 for item in evidence)
    assert all(item["evidence"]["PLANNING"]["validation_pass_rate"] == 1.0 for item in evidence)
    with service.session_factory() as session:
        observations = session.scalars(select(EvaluationObservationRecord)).all()
        assert len(observations) == 4
        assert sum(item.selected_winner for item in observations) == 1


def test_failed_trusted_check_requires_bounded_refinement(
    session_factory: sessionmaker[Session],
) -> None:
    validator = FixedValidator("schema", passed=False)
    service = ControlPlaneService(
        session_factory,
        evaluation_validators=(validator,),
    )
    workflow, campaign, execution = _setup(
        service,
        key="trusted-failure",
        required_checks=frozenset({"schema"}),
    )

    assessment = service.validate_evaluation_execution(
        workflow_id=str(workflow["id"]),
        execution_id=str(execution["id"]),
        actor_id="dev-operator",
        idempotency_key="trusted-failure-assessment",
    )

    assert assessment["decision"]["status"] == "REFINEMENT_REQUIRED"
    assert validator.calls == 4
    stored = service.get_evaluation_campaign(
        str(workflow["id"]), str(campaign["id"]), principal_id="dev-operator"
    )
    assert stored["current_iteration"] == 2
    assert all(
        candidate["rejection_reasons"] == ["failed_checks:schema"]
        for candidate in stored["batches"][0]["candidates"]
    )


def test_refinement_requires_exact_committed_repair_plan(
    session_factory: sessionmaker[Session],
) -> None:
    service = ControlPlaneService(
        session_factory,
        evaluation_validators=(FixedValidator("schema", passed=False),),
    )
    workflow, campaign, execution = _setup(
        service,
        key="bounded-repair",
        required_checks=frozenset({"schema"}),
    )
    service.validate_evaluation_execution(
        workflow_id=str(workflow["id"]),
        execution_id=str(execution["id"]),
        actor_id="dev-operator",
        idempotency_key="bounded-repair-assessment",
    )
    variants = (PromptVariant("repair-v1", "Correct the failed schema evidence."),)
    with pytest.raises(ConflictError, match="committed repair plan"):
        service.execute_evaluation_campaign(
            workflow_id=str(workflow["id"]),
            campaign_id=str(campaign["id"]),
            task_id=str(execution["task_id"]),
            actor_id="dev-operator",
            idempotency_key="repair-missing-plan-execution",
            prompt_variants=variants,
        )
    repair = service.plan_evaluation_repair(
        workflow_id=str(workflow["id"]),
        campaign_id=str(campaign["id"]),
        actor_id="dev-operator",
        idempotency_key="bounded-repair-plan",
        prompt_variants=variants,
        rationale="Bind the correction to the exact failed evidence and snapshot.",
    )
    with pytest.raises(ConflictError, match="do not match"):
        service.execute_evaluation_campaign(
            workflow_id=str(workflow["id"]),
            campaign_id=str(campaign["id"]),
            task_id=str(execution["task_id"]),
            actor_id="dev-operator",
            idempotency_key="repair-drifted-plan-execution",
            prompt_variants=(PromptVariant("repair-v2", "Use an unapproved correction."),),
            repair_id=str(repair["id"]),
        )
    repaired_execution = service.execute_evaluation_campaign(
        workflow_id=str(workflow["id"]),
        campaign_id=str(campaign["id"]),
        task_id=str(execution["task_id"]),
        actor_id="dev-operator",
        idempotency_key="repair-exact-plan-execution",
        prompt_variants=variants,
        repair_id=str(repair["id"]),
    )
    assert repaired_execution["iteration"] == 2
    assert repaired_execution["repair_id"] == repair["id"]
    assert repair["failure_snapshot"]
    with service.session_factory() as session:
        assert len(session.scalars(select(EvaluationRepairRecord)).all()) == 1


def test_human_promotion_binds_exact_validated_winner_without_extra_authority(
    session_factory: sessionmaker[Session],
) -> None:
    service = ControlPlaneService(session_factory)
    workflow, campaign, execution = _setup(service, key="winner-promotion")
    assessment = service.validate_evaluation_execution(
        workflow_id=str(workflow["id"]),
        execution_id=str(execution["id"]),
        actor_id="dev-operator",
        idempotency_key="winner-promotion-assessment",
    )
    rationale = "Promote only the exact controller-validated winner artifact."
    with pytest.raises(AuthorizationError):
        service.promote_evaluation_winner(
            workflow_id=str(workflow["id"]),
            assessment_id=str(assessment["id"]),
            actor_id="architect-agent",
            idempotency_key="agent-winner-promotion",
            rationale=rationale,
        )
    with TestClient(create_app(service)) as client:
        response = client.post(
            f"/workflows/{workflow['id']}/evaluation-assessments/{assessment['id']}/promotions",
            headers={"X-Principal-ID": "dev-operator"},
            json={"idempotency_key": "winner-promotion-record", "rationale": rationale},
        )
    assert response.status_code == 201
    promotion = response.json()
    assert promotion["campaign_id"] == campaign["id"]
    assert promotion["candidate_id"] == assessment["decision"]["winner_candidate_id"]
    assert promotion["artifact_digest"].startswith("sha256:")
    replay = service.promote_evaluation_winner(
        workflow_id=str(workflow["id"]),
        assessment_id=str(assessment["id"]),
        actor_id="dev-operator",
        idempotency_key="winner-promotion-record",
        rationale=rationale,
    )
    assert replay["replayed"] is True
    with service.session_factory() as session:
        assert len(session.scalars(select(EvaluationPromotionRecord)).all()) == 1


def test_interrupted_running_reviews_recover_to_unknown_without_retry(
    session_factory: sessionmaker[Session],
) -> None:
    class UnknownReviewProvider(MockProvider):
        def submit(self, request: ProviderRequest) -> ProviderResult:
            if request.task_kind is TaskKind.CODE_REVIEW:
                raise TimeoutError("ambiguous review")
            return super().submit(request)

    service = ControlPlaneService(
        session_factory,
        provider_bindings=tuple(
            ProviderBinding(profile, UnknownReviewProvider()) for profile in _independent_profiles()
        ),
        evidence_store=StaticEvidenceStore(),  # type: ignore[arg-type]
    )
    workflow, _campaign, execution = _setup(
        service, key="interrupted-review-recovery", risk=RiskLevel.HIGH
    )
    assessment = service.validate_evaluation_execution(
        workflow_id=str(workflow["id"]),
        execution_id=str(execution["id"]),
        actor_id="dev-operator",
        idempotency_key="interrupted-review-assessment",
    )
    with service.session_factory() as session, session.begin():
        stored = session.get(EvaluationAssessmentRecord, str(assessment["id"]))
        assert stored is not None
        stored.status = "REVIEWS_RUNNING"
        for review in session.scalars(select(EvaluationReviewRunRecord)).all():
            review.status = "REVIEWS_RUNNING"
            review.error_code = None
    recovered = service.recover_evaluation_assessment(
        workflow_id=str(workflow["id"]),
        assessment_id=str(assessment["id"]),
        actor_id="dev-operator",
        idempotency_key="interrupted-review-recovery",
        decision=EvaluationRecoveryDecision.MARK_INTERRUPTED_FAILED,
        rationale="The process stopped after dispatch, so preserve ambiguity for reconciliation.",
    )
    assert recovered["status"] == "REVIEW_UNKNOWN"
    assert recovered["recovery"]["prior_status"] == "REVIEWS_RUNNING"
    assert recovered["recovery"]["outcome_status"] == "REVIEW_UNKNOWN"
    with service.session_factory() as session:
        assert len(session.scalars(select(EvaluationRecoveryRecord)).all()) == 1
        assert all(
            review.status == "UNKNOWN" and review.error_code == "InterruptedReviewOutcomeUnknown"
            for review in session.scalars(select(EvaluationReviewRunRecord)).all()
        )


def test_recovery_replay_resumes_after_commit_before_continuation(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ControlPlaneService(session_factory)
    workflow, _campaign, execution = _setup(service, key="recovery-resume")
    original_continue = service._review_or_submit_trusted_assessment

    def interrupt_continuation(_assessment_id: str, _actor_id: str) -> dict[str, object]:
        raise RuntimeError("simulated process stop after committed state")

    monkeypatch.setattr(service, "_review_or_submit_trusted_assessment", interrupt_continuation)
    with pytest.raises(RuntimeError, match="simulated process stop"):
        service.validate_evaluation_execution(
            workflow_id=str(workflow["id"]),
            execution_id=str(execution["id"]),
            actor_id="dev-operator",
            idempotency_key="recovery-resume-assessment",
        )
    with service.session_factory() as session, session.begin():
        assessment = session.scalar(
            select(EvaluationAssessmentRecord).where(
                EvaluationAssessmentRecord.execution_id == str(execution["id"])
            )
        )
        assert assessment is not None
        assessment.status = "RUNNING"
        for check in session.scalars(
            select(EvaluationCheckRecord).where(
                EvaluationCheckRecord.assessment_id == assessment.id
            )
        ).all():
            check.status = "RUNNING"
            check.passed = None
            check.evidence_digest = None
            check.completed_at = None
        assessment_id = assessment.id
    with pytest.raises(RuntimeError, match="simulated process stop"):
        service.recover_evaluation_assessment(
            workflow_id=str(workflow["id"]),
            assessment_id=assessment_id,
            actor_id="dev-operator",
            idempotency_key="recovery-resume-command",
            decision=EvaluationRecoveryDecision.MARK_INTERRUPTED_FAILED,
            rationale="Conservatively close interrupted controller checks.",
        )
    monkeypatch.setattr(service, "_review_or_submit_trusted_assessment", original_continue)
    replay = service.recover_evaluation_assessment(
        workflow_id=str(workflow["id"]),
        assessment_id=assessment_id,
        actor_id="dev-operator",
        idempotency_key="recovery-resume-command",
        decision=EvaluationRecoveryDecision.MARK_INTERRUPTED_FAILED,
        rationale="Conservatively close interrupted controller checks.",
    )
    assert replay["replayed"] is True
    assert replay["status"] == "DECIDED"
    assert replay["decision"]["status"] == "REFINEMENT_REQUIRED"


def test_missing_validator_stale_snapshot_and_unknown_execution_fail_closed(
    session_factory: sessionmaker[Session],
) -> None:
    service = ControlPlaneService(
        session_factory,
        evaluation_validators=(FixedValidator("schema"),),
    )
    workflow, _campaign, execution = _setup(
        service,
        key="missing-validator",
        required_checks=frozenset({"schema", "security"}),
    )
    with pytest.raises(ConflictError, match="not configured: security"):
        service.validate_evaluation_execution(
            workflow_id=str(workflow["id"]),
            execution_id=str(execution["id"]),
            actor_id="dev-operator",
            idempotency_key="missing-validator-assessment",
        )
    with service.session_factory() as session:
        assert session.scalar(select(EvaluationAssessmentRecord)) is None

    healthy_service = ControlPlaneService(session_factory)
    stale_workflow, _stale_campaign, stale_execution = _setup(healthy_service, key="stale-snapshot")
    with session_factory() as session, session.begin():
        record = session.get(Workflow, str(stale_workflow["id"]))
        assert record is not None
        record.version += 1
    with pytest.raises(ConflictError, match="snapshot is stale"):
        healthy_service.validate_evaluation_execution(
            workflow_id=str(stale_workflow["id"]),
            execution_id=str(stale_execution["id"]),
            actor_id="dev-operator",
            idempotency_key="stale-snapshot-assessment",
        )

    class TimeoutProvider(MockProvider):
        def submit(self, request: ProviderRequest) -> ProviderResult:
            raise TimeoutError("ambiguous")

    unknown_service = ControlPlaneService(
        session_factory,
        provider_bindings=tuple(
            ProviderBinding(profile, TimeoutProvider()) for profile in mock_profiles()
        ),
    )
    unknown_workflow, _unknown_campaign, unknown_execution = _setup(
        unknown_service, key="unknown-validation"
    )
    with pytest.raises(ConflictError, match="requires reconciliation"):
        unknown_service.validate_evaluation_execution(
            workflow_id=str(unknown_workflow["id"]),
            execution_id=str(unknown_execution["id"]),
            actor_id="dev-operator",
            idempotency_key="unknown-validation-assessment",
        )
    with pytest.raises(AuthorizationError):
        unknown_service.reconcile_evaluation_execution(
            workflow_id=str(unknown_workflow["id"]),
            execution_id=str(unknown_execution["id"]),
            actor_id="implementer-agent",
            idempotency_key="agent-provider-reconciliation",
            decision=EvaluationReconciliationDecision.MARK_FAILED,
            rationale="Agents cannot resolve ambiguous provider effects.",
        )
    with TestClient(create_app(unknown_service)) as client:
        response = client.post(
            (
                f"/workflows/{unknown_workflow['id']}/evaluation-executions/"
                f"{unknown_execution['id']}/reconcile"
            ),
            headers={"X-Principal-ID": "dev-operator"},
            json={
                "idempotency_key": "unknown-provider-reconciliation",
                "decision": "MARK_FAILED",
                "rationale": (
                    "The provider has no read-only result lookup; fail closed without retry."
                ),
            },
        )
    assert response.status_code == 200
    reconciled = response.json()
    assessment = unknown_service.validate_evaluation_execution(
        workflow_id=str(unknown_workflow["id"]),
        execution_id=str(unknown_execution["id"]),
        actor_id="dev-operator",
        idempotency_key="unknown-validation-assessment",
    )
    assert reconciled["status"] == "FAILED"
    assert assessment["decision"]["status"] == "REFINEMENT_REQUIRED"


def test_high_risk_fails_closed_without_independent_reviewers(
    session_factory: sessionmaker[Session],
) -> None:
    qualified_profiles = tuple(
        replace(
            profile,
            evidence={
                WorkCapability.PLANNING: CapabilityEvidence(
                    sample_count=20,
                    success_rate=1.0,
                    validation_pass_rate=1.0,
                )
            },
        )
        for profile in mock_profiles()
    )
    service = ControlPlaneService(
        session_factory,
        provider_bindings=tuple(
            ProviderBinding(profile, MockProvider()) for profile in qualified_profiles
        ),
        evidence_store=StaticEvidenceStore(),  # type: ignore[arg-type]
    )
    workflow, _campaign, execution = _setup(
        service,
        key="high-risk-review-gate",
        risk=RiskLevel.HIGH,
    )

    with pytest.raises(ConflictError, match="insufficient policy-eligible"):
        service.validate_evaluation_execution(
            workflow_id=str(workflow["id"]),
            execution_id=str(execution["id"]),
            actor_id="dev-operator",
            idempotency_key="high-risk-review-assessment",
        )
    with service.session_factory() as session:
        observations = session.scalars(select(EvaluationObservationRecord)).all()
        assert observations == []


def test_high_risk_executes_two_durable_cross_family_reviews_per_artifact(
    session_factory: sessionmaker[Session],
) -> None:
    profiles = _independent_profiles()
    service = ControlPlaneService(
        session_factory,
        provider_bindings=tuple(ProviderBinding(profile, MockProvider()) for profile in profiles),
        evidence_store=StaticEvidenceStore(),  # type: ignore[arg-type]
    )
    workflow, _campaign, execution = _setup(
        service,
        key="durable-independent-reviews",
        risk=RiskLevel.HIGH,
    )

    assessment = service.validate_evaluation_execution(
        workflow_id=str(workflow["id"]),
        execution_id=str(execution["id"]),
        actor_id="dev-operator",
        idempotency_key="durable-independent-review-assessment",
    )

    assert assessment["status"] == "DECIDED"
    assert assessment["decision"]["status"] == "WINNER_SELECTED"
    assert all(len(artifact["reviews"]) == 2 for artifact in assessment["artifacts"])
    assert all(
        review["status"] == "SUCCEEDED"
        and review["passed"] is True
        and review["reviewed_output_digest"] == artifact["digest"]
        and review["evidence_digest"].startswith("sha256:")
        for artifact in assessment["artifacts"]
        for review in artifact["reviews"]
    )
    with service.session_factory() as session:
        reviews = session.scalars(select(EvaluationReviewRunRecord)).all()
        producers = {
            run.id: run for run in session.scalars(select(EvaluationProviderRunRecord)).all()
        }
        assert len(reviews) == len(assessment["artifacts"]) * 2
        assert all(
            review.reviewer_provider_id != producers[review.candidate_provider_run_id].provider_id
            and review.reviewer_provider_family
            != producers[review.candidate_provider_run_id].provider_family
            for review in reviews
        )


def test_unknown_reviews_require_human_failed_reconciliation(
    session_factory: sessionmaker[Session],
) -> None:
    class UnknownReviewProvider(MockProvider):
        def submit(self, request: ProviderRequest) -> ProviderResult:
            if request.task_kind is TaskKind.CODE_REVIEW:
                raise TimeoutError("ambiguous review")
            return super().submit(request)

    service = ControlPlaneService(
        session_factory,
        provider_bindings=tuple(
            ProviderBinding(profile, UnknownReviewProvider()) for profile in _independent_profiles()
        ),
        evidence_store=StaticEvidenceStore(),  # type: ignore[arg-type]
    )
    workflow, _campaign, execution = _setup(
        service,
        key="unknown-independent-reviews",
        risk=RiskLevel.HIGH,
    )
    assessment = service.validate_evaluation_execution(
        workflow_id=str(workflow["id"]),
        execution_id=str(execution["id"]),
        actor_id="dev-operator",
        idempotency_key="unknown-review-assessment",
    )

    assert assessment["status"] == "REVIEW_UNKNOWN"
    rationale = "No read-only lookup exists, so conservatively treat ambiguous reviews as failed."
    with TestClient(create_app(service)) as client:
        response = client.post(
            (f"/workflows/{workflow['id']}/evaluation-assessments/{assessment['id']}/reconcile"),
            headers={"X-Principal-ID": "dev-operator"},
            json={
                "idempotency_key": "unknown-review-reconciliation",
                "decision": "MARK_FAILED",
                "rationale": rationale,
            },
        )
    assert response.status_code == 200
    reconciled = response.json()
    replay = service.reconcile_evaluation_reviews(
        workflow_id=str(workflow["id"]),
        assessment_id=str(assessment["id"]),
        actor_id="dev-operator",
        idempotency_key="unknown-review-reconciliation",
        decision=EvaluationReconciliationDecision.MARK_FAILED,
        rationale=rationale,
    )

    assert reconciled["status"] == "DECIDED"
    assert reconciled["decision"]["status"] == "REFINEMENT_REQUIRED"
    assert replay["replayed"] is True
    with service.session_factory() as session:
        assert len(session.scalars(select(EvaluationReconciliationRecord)).all()) == 1
        reviews = session.scalars(select(EvaluationReviewRunRecord)).all()
        assert reviews
        assert all(review.status == "FAILED" for review in reviews)


def test_known_negative_reviews_are_evidence_not_provider_failures(
    session_factory: sessionmaker[Session],
) -> None:
    class NegativeReviewProvider(MockProvider):
        def submit(self, request: ProviderRequest) -> ProviderResult:
            if request.task_kind is TaskKind.CODE_REVIEW:
                return ProviderResult(
                    status="SUCCEEDED",
                    output={
                        "review_passed": False,
                        "blocking_findings": ["candidate requires correction"],
                    },
                    provider=self.name,
                    model="deterministic-mock-v1",
                    usage={"input_tokens": 0, "output_tokens": 0},
                )
            return super().submit(request)

    service = ControlPlaneService(
        session_factory,
        provider_bindings=tuple(
            ProviderBinding(profile, NegativeReviewProvider())
            for profile in _independent_profiles()
        ),
        evidence_store=StaticEvidenceStore(),  # type: ignore[arg-type]
    )
    workflow, _campaign, execution = _setup(
        service,
        key="negative-independent-reviews",
        risk=RiskLevel.HIGH,
    )

    assessment = service.validate_evaluation_execution(
        workflow_id=str(workflow["id"]),
        execution_id=str(execution["id"]),
        actor_id="dev-operator",
        idempotency_key="negative-review-assessment",
    )

    assert assessment["decision"]["status"] == "REFINEMENT_REQUIRED"
    assert all(
        review["status"] == "SUCCEEDED" and review["passed"] is False
        for artifact in assessment["artifacts"]
        for review in artifact["reviews"]
    )


def test_failed_deterministic_checks_do_not_spend_reviewer_capacity(
    session_factory: sessionmaker[Session],
) -> None:
    service = ControlPlaneService(
        session_factory,
        provider_bindings=tuple(
            ProviderBinding(profile, MockProvider()) for profile in _independent_profiles()
        ),
        evidence_store=StaticEvidenceStore(),  # type: ignore[arg-type]
        evaluation_validators=(FixedValidator("schema", passed=False),),
    )
    workflow, _campaign, execution = _setup(
        service,
        key="failed-check-skips-reviews",
        risk=RiskLevel.HIGH,
        required_checks=frozenset({"schema"}),
    )

    assessment = service.validate_evaluation_execution(
        workflow_id=str(workflow["id"]),
        execution_id=str(execution["id"]),
        actor_id="dev-operator",
        idempotency_key="failed-check-skips-review-assessment",
    )

    assert assessment["decision"]["status"] == "REFINEMENT_REQUIRED"
    assert all(artifact["reviews"] == [] for artifact in assessment["artifacts"])


def test_validation_is_human_only_and_api_is_replay_safe(
    service: ControlPlaneService,
) -> None:
    workflow, _campaign, execution = _setup(service, key="validation-api")
    with pytest.raises(AuthorizationError):
        service.validate_evaluation_execution(
            workflow_id=str(workflow["id"]),
            execution_id=str(execution["id"]),
            actor_id="implementer-agent",
            idempotency_key="agent-validation-assessment",
        )

    with TestClient(create_app(service)) as client:
        response = client.post(
            f"/workflows/{workflow['id']}/evaluation-executions/{execution['id']}/assessments",
            headers={"X-Principal-ID": "dev-operator"},
            json={"idempotency_key": "validation-api-assessment"},
        )
        assert response.status_code == 201
        assessment = response.json()
        stored = client.get(
            f"/workflows/{workflow['id']}/evaluation-assessments/{assessment['id']}",
            headers={"X-Principal-ID": "dev-operator"},
        )

    assert stored.status_code == 200
    assert stored.json()["status"] == "DECIDED"


def test_security_validator_rejects_sensitive_and_authority_fields() -> None:
    validator: EvaluationValidator = SecurityBoundaryValidator()
    artifact = EvaluationArtifact(
        artifact_id="artifact-1",
        output={"nested": {"token": "redacted"}, "merge_authorized": True},
        output_digest="sha256:" + "a" * 64,
        task_kind=TaskKind.PLAN,
        workflow_version=1,
        candidate_revision=None,
    )

    outcome = validator.validate(artifact)

    assert outcome.passed is False
    assert outcome.details["forbidden_fields"] == ["token"]
    assert outcome.details["authority_fields"] == ["merge_authorized"]
