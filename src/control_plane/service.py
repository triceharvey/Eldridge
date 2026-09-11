from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from time import monotonic
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, sessionmaker

from control_plane.audit import append_audit_event
from control_plane.credentials import (
    CredentialBroker,
    CredentialSessionBroker,
    CredentialSessionBrokerFactory,
    DenyCredentialBroker,
    WorkloadCredentialHandle,
)
from control_plane.deployment import (
    CredentialedDeploymentAdapter,
    CredentialedDeploymentRun,
    CredentialedRollbackRun,
    DeploymentAdapter,
    DeploymentAttemptStatus,
    DeploymentPlan,
    DryRunDeploymentAdapter,
    EnvironmentClassification,
    RecoverableDeploymentAdapter,
    RollbackStatus,
)
from control_plane.domain import (
    TASK_CAPABILITY,
    TASK_ROLE,
    AgentRole,
    ApprovalAction,
    ApprovalDecision,
    AuthorizationError,
    Capability,
    ConflictError,
    DeploymentOutcomeUnknownError,
    DeploymentVerificationError,
    DispositionDecision,
    ExecutionContext,
    ExecutionResult,
    IntegrationDisabledError,
    InvalidTransitionError,
    NotFoundError,
    ProviderRequest,
    ProviderResult,
    ReconciliationDecision,
    TaskKind,
    TaskStatus,
    ValidationError,
    WorkflowState,
)
from control_plane.evaluation import (
    EVALUATION_POLICY_VERSION,
    CandidateEvidence,
    EvaluationBatch,
    EvaluationPolicy,
    EvaluationReconciliationDecision,
    EvaluationRecoveryDecision,
    EvaluationStatus,
    MultiModelEvaluator,
    PromptVariant,
    validate_evaluation_identifier,
)
from control_plane.evaluation_lifecycle import EvaluationLifecycleService
from control_plane.evaluation_pipeline import EvaluationPipelineService
from control_plane.evaluation_validation import (
    EvaluationValidator,
    default_evaluation_validators,
)
from control_plane.executors import FakeExecutor, TaskExecutor
from control_plane.github_app import (
    GitHubAppClient,
    MergeConfirmation,
    MergeReadiness,
    PullRequestProposal,
)
from control_plane.integrations import WindsurfHandoff
from control_plane.learning import ProviderEvidenceStore
from control_plane.leases import LeaseHeartbeat
from control_plane.persistence import (
    Approval,
    Artifact,
    AuditEvent,
    CapabilityGrant,
    CiCheckEvidence,
    DeploymentAttemptRecord,
    DeploymentEnvironment,
    DeploymentPlanRecord,
    DeploymentRollbackRecord,
    DeploymentVerificationRecord,
    EvaluationArtifactRecord,
    EvaluationAssessmentRecord,
    EvaluationBatchRecord,
    EvaluationCampaignRecord,
    EvaluationCandidateRecord,
    EvaluationCheckRecord,
    EvaluationExecutionRecord,
    EvaluationPromotionRecord,
    EvaluationProviderRunRecord,
    EvaluationReconciliationRecord,
    EvaluationRecoveryRecord,
    EvaluationRepairRecord,
    EvaluationReviewRunRecord,
    ExecutionReconciliation,
    IdempotencyRecord,
    MergeConfirmationRecord,
    MergeReadinessAssessmentRecord,
    ProviderObservation,
    PullRequestProposalRecord,
    RoutingRecord,
    Task,
    TaskAttempt,
    Workflow,
    WorkflowDisposition,
)
from control_plane.policy import PolicyEngine
from control_plane.providers import MockProvider, ModelProvider
from control_plane.routing import (
    CapabilityRouter,
    DataClassification,
    EgressBoundary,
    ProviderProfile,
    RiskLevel,
    RoutingObjective,
    RoutingPurpose,
    RoutingRequest,
    WorkCapability,
    mock_profiles,
)
from control_plane.state_machine import assert_transition_allowed
from control_plane.task_strategy import (
    ComplexityTier,
    InspectionSignal,
    TaskProfile,
    TaskStrategyPlanner,
)
from control_plane.validation import validate_provider_result
from control_plane.workspaces import RepositoryRegistry

AGENT_FOR_ROLE: dict[AgentRole, str] = {
    AgentRole.ARCHITECT: "architect-agent",
    AgentRole.REVIEWER: "reviewer-agent",
    AgentRole.IMPLEMENTER: "implementer-agent",
    AgentRole.TEST_AGENT: "test-agent",
    AgentRole.SECURITY_AGENT: "security-agent",
}

TASK_OBJECTIVES: dict[TaskKind, str] = {
    TaskKind.PLAN: "Create a bounded engineering plan and declare assumptions.",
    TaskKind.ARCHITECTURE_REVIEW: (
        "Independently challenge the plan for security, operational, and failure risks."
    ),
    TaskKind.IMPLEMENT: "Produce the assigned change artifact without executing host commands.",
    TaskKind.TEST: "Validate the candidate revision and produce structured test evidence.",
    TaskKind.SECURITY_REVIEW: "Evaluate the candidate revision against the security policy.",
    TaskKind.CODE_REVIEW: "Perform final independent review and identify blocking findings.",
}

TASK_WORK_CAPABILITY: dict[TaskKind, WorkCapability] = {
    TaskKind.PLAN: WorkCapability.PLANNING,
    TaskKind.ARCHITECTURE_REVIEW: WorkCapability.ARCHITECTURE,
    TaskKind.IMPLEMENT: WorkCapability.CODE_GENERATION,
    TaskKind.TEST: WorkCapability.TEST_EXECUTION,
    TaskKind.SECURITY_REVIEW: WorkCapability.SECURITY_ANALYSIS,
    TaskKind.CODE_REVIEW: WorkCapability.CODE_REVIEW,
}

REVIEW_PRODUCER_KIND: dict[TaskKind, TaskKind] = {
    TaskKind.ARCHITECTURE_REVIEW: TaskKind.PLAN,
    TaskKind.SECURITY_REVIEW: TaskKind.IMPLEMENT,
    TaskKind.CODE_REVIEW: TaskKind.IMPLEMENT,
}

TASK_WORKFLOW_STATE: dict[TaskKind, WorkflowState] = {
    TaskKind.PLAN: WorkflowState.PLANNING,
    TaskKind.ARCHITECTURE_REVIEW: WorkflowState.ARCHITECTURE_REVIEW,
    TaskKind.IMPLEMENT: WorkflowState.IMPLEMENTING,
    TaskKind.TEST: WorkflowState.TESTING,
    TaskKind.SECURITY_REVIEW: WorkflowState.SECURITY_REVIEW,
    TaskKind.CODE_REVIEW: WorkflowState.CODE_REVIEW,
}


@dataclass(frozen=True)
class ProviderBinding:
    profile: ProviderProfile
    provider: ModelProvider


@dataclass(frozen=True)
class PreparedExecution:
    workflow_id: str
    task_id: str
    attempt_id: str
    lease_token: str
    worker_id: str
    agent_id: str
    task_kind: TaskKind
    binding: ProviderBinding
    profile: ProviderProfile
    request: ProviderRequest
    context: ExecutionContext


class ControlPlaneService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        provider: ModelProvider | None = None,
        executor: TaskExecutor | None = None,
        policy: PolicyEngine | None = None,
        lease_seconds: int = 30,
        provider_bindings: tuple[ProviderBinding, ...] | None = None,
        router: CapabilityRouter | None = None,
        evidence_store: ProviderEvidenceStore | None = None,
        strategy_planner: TaskStrategyPlanner | None = None,
        allowed_egress: frozenset[EgressBoundary] = frozenset({EgressBoundary.LOCAL}),
        high_risk_min_evidence_samples: int = 20,
        heartbeat_interval_seconds: float | None = None,
        repository_registry: RepositoryRegistry | None = None,
        provider_policy_version: str = "built-in/mock-v1",
        routing_objective: RoutingObjective = RoutingObjective.BALANCED,
        github_app: GitHubAppClient | None = None,
        deployment_adapters: tuple[DeploymentAdapter | CredentialedDeploymentAdapter, ...]
        | None = None,
        credential_broker: CredentialBroker | None = None,
        credential_broker_factory: CredentialSessionBrokerFactory | None = None,
        enable_local_deployment: bool = False,
        evaluator: MultiModelEvaluator | None = None,
        evaluation_validators: tuple[EvaluationValidator, ...] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.provider = provider or MockProvider()
        if provider_bindings is None:
            provider_bindings = tuple(
                ProviderBinding(profile=profile, provider=self.provider)
                for profile in mock_profiles()
            )
        binding_ids = [binding.profile.provider_id for binding in provider_bindings]
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("provider binding IDs must be unique")
        self.provider_bindings = {
            binding.profile.provider_id: binding for binding in provider_bindings
        }
        self.router = router or CapabilityRouter()
        self.evidence_store = evidence_store or ProviderEvidenceStore()
        self.strategy_planner = strategy_planner or TaskStrategyPlanner()
        self.allowed_egress = allowed_egress
        if lease_seconds < 2:
            raise ValueError("lease_seconds must be at least two")
        self.lease_seconds = lease_seconds
        if high_risk_min_evidence_samples < 1:
            raise ValueError("high_risk_min_evidence_samples must be positive")
        self.high_risk_min_evidence_samples = high_risk_min_evidence_samples
        self.heartbeat_interval_seconds = (
            heartbeat_interval_seconds
            if heartbeat_interval_seconds is not None
            else max(1.0, min(self.lease_seconds / 3, 10.0))
        )
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        if self.heartbeat_interval_seconds >= self.lease_seconds:
            raise ValueError("heartbeat interval must be shorter than the lease")
        self.executor = executor or FakeExecutor()
        self.policy = policy or PolicyEngine()
        self.repository_registry = repository_registry
        self.provider_policy_version = provider_policy_version
        self.routing_objective = routing_objective
        self.github_app = github_app
        configured_adapters = deployment_adapters or (DryRunDeploymentAdapter(),)
        if any(adapter.requires_credentials for adapter in configured_adapters) and not (
            enable_local_deployment
            and (
                (
                    credential_broker is not None
                    and isinstance(credential_broker, CredentialSessionBroker)
                )
                or credential_broker_factory is not None
            )
        ):
            raise ValueError(
                "credential-requiring adapters need explicit local activation and a session broker"
            )
        adapter_ids = [adapter.adapter_id for adapter in configured_adapters]
        if len(adapter_ids) != len(set(adapter_ids)):
            raise ValueError("deployment adapter IDs must be unique")
        self.deployment_adapters = {adapter.adapter_id: adapter for adapter in configured_adapters}
        self.credential_broker = credential_broker or DenyCredentialBroker()
        self.credential_broker_factory = credential_broker_factory
        self.enable_local_deployment = enable_local_deployment
        self.evaluator = evaluator or MultiModelEvaluator()
        validators = evaluation_validators or default_evaluation_validators()
        validator_names = [validator.name for validator in validators]
        if len(validator_names) != len(set(validator_names)):
            raise ValueError("evaluation validator names must be unique")
        self.evaluation_validators = {validator.name: validator for validator in validators}
        self.evaluation_pipeline = EvaluationPipelineService(
            self.session_factory,
            self.policy,
            provider_bindings=self.provider_bindings,
            router=self.router,
            evidence_store=self.evidence_store,
            allowed_egress=self.allowed_egress,
            high_risk_min_evidence_samples=self.high_risk_min_evidence_samples,
            provider_policy_version=self.provider_policy_version,
            routing_objective=self.routing_objective,
            evaluation_validators=self.evaluation_validators,
            serialize_execution=self._evaluation_execution_dict,
            serialize_assessment=self._evaluation_assessment_dict,
            serialize_reconciliation=self._evaluation_reconciliation_dict,
            submit_evaluation_evidence=lambda **kwargs: self.submit_evaluation_evidence(**kwargs),
            resume_assessment=lambda assessment_id, actor_id: (
                self._review_or_submit_trusted_assessment(assessment_id, actor_id)
            ),
        )
        self.evaluation_lifecycle = EvaluationLifecycleService(
            self.session_factory,
            self.policy,
            resume_assessment=lambda assessment_id, actor_id: (
                self._review_or_submit_trusted_assessment(assessment_id, actor_id)
            ),
            read_assessment=lambda workflow_id, assessment_id, actor_id: (
                self.get_evaluation_assessment(
                    workflow_id,
                    assessment_id,
                    principal_id=actor_id,
                )
            ),
        )

    def create_workflow(
        self,
        *,
        requester_id: str,
        title: str,
        description: str,
        idempotency_key: str,
        complexity: ComplexityTier = ComplexityTier.STANDARD,
        risk: RiskLevel = RiskLevel.MEDIUM,
        data_classification: DataClassification = DataClassification.INTERNAL,
        repository_scope: str | None = None,
        inspection_signals: frozenset[InspectionSignal] = frozenset(),
    ) -> dict[str, Any]:
        if not title.strip() or not description.strip() or not idempotency_key.strip():
            raise ValidationError("title, description, and idempotency key are required")
        normalized_repository = repository_scope.strip() if repository_scope else None
        if normalized_repository == "":
            normalized_repository = None
        task_profile = TaskProfile(
            complexity=complexity,
            risk=risk,
            data_classification=data_classification,
            required_capabilities=frozenset({WorkCapability.PLANNING}),
            inspection_signals=inspection_signals,
        )
        strategy = self.strategy_planner.plan(task_profile)
        request_digest = self._digest(
            {
                "title": title.strip(),
                "description": description.strip(),
                "complexity": complexity.name,
                "risk": risk.name,
                "data_classification": data_classification.name,
                "repository_scope": normalized_repository,
                "inspection_signals": sorted(signal.value for signal in inspection_signals),
            }
        )
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, requester_id, Capability.SUBMIT_WORKFLOW)
            existing = session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.principal_id == requester_id,
                    IdempotencyRecord.command == "create_workflow",
                    IdempotencyRecord.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError("idempotency key was already used for a different request")
                workflow = session.get(Workflow, existing.resource_id)
                if workflow is None:
                    raise ConflictError("idempotency record references a missing workflow")
                return self._workflow_dict(workflow)

            workflow = Workflow(
                title=title.strip(),
                description=description.strip(),
                state=WorkflowState.CREATED.value,
                risk_class=risk.name,
                complexity_tier=complexity.name,
                data_classification=data_classification.name,
                repository_scope=normalized_repository,
                inspection_signals=sorted(signal.value for signal in inspection_signals),
                containment_required=strategy.requires_human_disposition,
                block_reason=(
                    "INPUT_DISPOSITION_REQUIRED" if strategy.requires_human_disposition else None
                ),
                requester_id=requester_id,
            )
            session.add(workflow)
            session.flush()
            session.add(
                IdempotencyRecord(
                    principal_id=requester_id,
                    command="create_workflow",
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                    resource_id=workflow.id,
                )
            )
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="workflow.created",
                actor_id=requester_id,
                resource_type="workflow",
                resource_id=workflow.id,
                outcome="SUCCEEDED",
                payload={"state": WorkflowState.CREATED.value},
            )
            if strategy.requires_human_disposition:
                self._transition(
                    session,
                    workflow,
                    WorkflowState.BLOCKED,
                    actor_id="orchestrator",
                    reason="suspicious or adversarial input contained pending human disposition",
                    extra={"restrictions": sorted(item.value for item in strategy.restrictions)},
                )
            else:
                self._transition(
                    session,
                    workflow,
                    WorkflowState.PLANNING,
                    actor_id="orchestrator",
                    reason="validated request queued for planning",
                )
                self._schedule_task(session, workflow, TaskKind.PLAN)
            session.flush()
            return self._workflow_dict(workflow)

    def get_workflow(self, workflow_id: str, *, principal_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_WORKFLOW)
            workflow = self._get_workflow(session, workflow_id)
            return self._workflow_dict(workflow)

    def list_events(self, workflow_id: str, *, principal_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_AUDIT)
            self._get_workflow(session, workflow_id)
            events = session.scalars(
                select(AuditEvent)
                .where(AuditEvent.workflow_id == workflow_id)
                .order_by(AuditEvent.sequence)
            ).all()
            return [self._event_dict(event) for event in events]

    def list_routing_records(self, workflow_id: str, *, principal_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_AUDIT)
            self._get_workflow(session, workflow_id)
            records = session.scalars(
                select(RoutingRecord)
                .where(RoutingRecord.workflow_id == workflow_id)
                .order_by(RoutingRecord.created_at)
            ).all()
            return [self._routing_record_dict(record) for record in records]

    def list_provider_evidence(self, *, principal_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_AUDIT)
            profiles = self.evidence_store.hydrate_profiles(
                session,
                tuple(binding.profile for binding in self.provider_bindings.values()),
            )
            return [
                {
                    "provider_id": profile.provider_id,
                    "provider_family": profile.provider_family,
                    "model_version": profile.model_version,
                    "profile_version": profile.profile_version,
                    "evidence": {
                        capability.value: {
                            "sample_count": evidence.sample_count,
                            "success_rate": evidence.success_rate,
                            "validation_pass_rate": evidence.validation_pass_rate,
                            "p95_latency_seconds": evidence.p95_latency_seconds,
                        }
                        for capability, evidence in sorted(
                            profile.evidence.items(), key=lambda item: item[0].value
                        )
                    },
                }
                for profile in profiles
            ]

    def create_evaluation_campaign(
        self,
        *,
        workflow_id: str,
        actor_id: str,
        idempotency_key: str,
        prompt_contract_version: str,
        work_capability: WorkCapability,
        required_checks: frozenset[str],
        max_candidates: int = 4,
        max_prompt_variants: int = 3,
        max_iterations: int = 3,
        max_total_cost_microunits: int = 0,
        minimum_independent_reviews: int | None = None,
    ) -> dict[str, Any]:
        if not 8 <= len(idempotency_key.strip()) <= 128:
            raise ValidationError("evaluation campaign idempotency key is invalid")
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session,
                actor_id,
                Capability.CREATE_EVALUATION,
                require_human=True,
            )
            workflow = self._get_workflow(session, workflow_id)
            risk = RiskLevel[workflow.risk_class]
            review_floor = (
                2
                if minimum_independent_reviews is None and risk >= RiskLevel.HIGH
                else (minimum_independent_reviews or 0)
            )
            try:
                evaluation_policy = EvaluationPolicy(
                    required_checks=required_checks,
                    risk=risk,
                    max_candidates=max_candidates,
                    max_prompt_variants=max_prompt_variants,
                    max_iterations=max_iterations,
                    max_total_cost_microunits=max_total_cost_microunits,
                    minimum_independent_reviews=review_floor,
                )
                validate_evaluation_identifier("prompt contract version", prompt_contract_version)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc

            normalized_checks = sorted(evaluation_policy.required_checks)
            request_digest = self._digest(
                {
                    "workflow_id": workflow_id,
                    "prompt_contract_version": prompt_contract_version,
                    "work_capability": work_capability.value,
                    "required_checks": normalized_checks,
                    "risk": risk.name,
                    "max_candidates": max_candidates,
                    "max_prompt_variants": max_prompt_variants,
                    "max_iterations": max_iterations,
                    "max_total_cost_microunits": max_total_cost_microunits,
                    "minimum_independent_reviews": review_floor,
                }
            )
            existing = session.scalar(
                select(EvaluationCampaignRecord).where(
                    EvaluationCampaignRecord.actor_id == actor_id,
                    EvaluationCampaignRecord.idempotency_key == idempotency_key.strip(),
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError("evaluation campaign idempotency key was reused")
                return self._evaluation_campaign_dict(existing)
            self._require_active_evaluation_workflow(workflow)

            campaign = EvaluationCampaignRecord(
                workflow_id=workflow.id,
                actor_id=actor_id,
                idempotency_key=idempotency_key.strip(),
                request_digest=request_digest,
                prompt_contract_version=prompt_contract_version,
                work_capability=work_capability.value,
                required_checks=normalized_checks,
                risk=risk.name,
                max_candidates=evaluation_policy.max_candidates,
                max_prompt_variants=evaluation_policy.max_prompt_variants,
                max_iterations=evaluation_policy.max_iterations,
                max_total_cost_microunits=evaluation_policy.max_total_cost_microunits,
                minimum_independent_reviews=evaluation_policy.minimum_independent_reviews,
                policy_version=EVALUATION_POLICY_VERSION,
                status="OPEN",
                current_iteration=1,
                total_cost_microunits=0,
            )
            session.add(campaign)
            session.flush()
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="evaluation.campaign_created",
                actor_id=actor_id,
                resource_type="evaluation_campaign",
                resource_id=campaign.id,
                outcome="SUCCEEDED",
                payload={
                    "policy_version": campaign.policy_version,
                    "prompt_contract_version": campaign.prompt_contract_version,
                    "work_capability": campaign.work_capability,
                    "required_checks": campaign.required_checks,
                    "risk": campaign.risk,
                    "max_total_cost_microunits": campaign.max_total_cost_microunits,
                },
            )
            session.flush()
            return self._evaluation_campaign_dict(campaign)

    def submit_evaluation_evidence(
        self,
        *,
        workflow_id: str,
        campaign_id: str,
        actor_id: str,
        idempotency_key: str,
        candidates: tuple[CandidateEvidence, ...],
        repair_id: str | None = None,
    ) -> dict[str, Any]:
        if not 8 <= len(idempotency_key.strip()) <= 128:
            raise ValidationError("evaluation batch idempotency key is invalid")
        candidate_payload = []
        for candidate in candidates:
            payload = asdict(candidate)
            payload.pop("routing_score")
            payload["checks"] = sorted(payload["checks"], key=lambda item: item["name"])
            payload["reviews"] = sorted(
                payload["reviews"], key=lambda item: item["reviewer_provider_id"]
            )
            candidate_payload.append(payload)
        candidate_payload.sort(key=lambda item: str(item["candidate_id"]))
        request_digest = self._digest(
            {
                "campaign_id": campaign_id,
                "candidates": candidate_payload,
                "repair_id": repair_id,
            }
        )
        with self.session_factory() as session:
            self.policy.authorize(
                session,
                actor_id,
                Capability.SUBMIT_EVALUATION_EVIDENCE,
                require_human=True,
            )
            existing = session.scalar(
                select(EvaluationBatchRecord).where(
                    EvaluationBatchRecord.actor_id == actor_id,
                    EvaluationBatchRecord.idempotency_key == idempotency_key.strip(),
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest or existing.workflow_id != workflow_id:
                    raise ConflictError("evaluation batch idempotency key was reused")
                return {**self._evaluation_batch_dict(existing), "replayed": True}
        provider_health = {
            provider_id: binding.profile.enabled and self._provider_is_healthy(binding.provider)
            for provider_id, binding in self.provider_bindings.items()
        }
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session,
                actor_id,
                Capability.SUBMIT_EVALUATION_EVIDENCE,
                require_human=True,
            )
            workflow = self._get_workflow(session, workflow_id)
            campaign = session.get(EvaluationCampaignRecord, campaign_id, with_for_update=True)
            if campaign is None or campaign.workflow_id != workflow_id:
                raise NotFoundError("evaluation campaign was not found")
            existing = session.scalar(
                select(EvaluationBatchRecord).where(
                    EvaluationBatchRecord.actor_id == actor_id,
                    EvaluationBatchRecord.idempotency_key == idempotency_key.strip(),
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError("evaluation batch idempotency key was reused")
                return {**self._evaluation_batch_dict(existing), "replayed": True}
            self._require_active_evaluation_workflow(workflow)
            if campaign.status not in {"OPEN", EvaluationStatus.REFINEMENT_REQUIRED.value}:
                raise ConflictError("evaluation campaign is terminal")
            if not candidates:
                raise ValidationError("evaluation batch requires candidates")
            if any(candidate.iteration != campaign.current_iteration for candidate in candidates):
                raise ConflictError("candidate iteration does not match campaign state")
            candidate_ids = [candidate.candidate_id for candidate in candidates]
            previously_recorded = session.scalars(
                select(EvaluationCandidateRecord.candidate_id).where(
                    EvaluationCandidateRecord.campaign_id == campaign.id,
                    EvaluationCandidateRecord.candidate_id.in_(candidate_ids),
                )
            ).all()
            if previously_recorded:
                raise ConflictError("candidate ID was already recorded in this campaign")
            repair: EvaluationRepairRecord | None = None
            if campaign.current_iteration == 1:
                if repair_id is not None:
                    raise ValidationError("initial evaluation evidence cannot use a repair plan")
            else:
                if repair_id is None:
                    raise ConflictError("refinement evidence requires a committed repair plan")
                repair = session.get(EvaluationRepairRecord, repair_id)
                if (
                    repair is None
                    or repair.campaign_id != campaign.id
                    or repair.target_iteration != campaign.current_iteration
                ):
                    raise ConflictError("evaluation repair plan does not match this iteration")
                if (
                    repair.workflow_version != workflow.version
                    or repair.candidate_revision != workflow.candidate_revision
                ):
                    raise ConflictError("evaluation repair plan workflow snapshot is stale")
                approved_variant_ids = {str(item["variant_id"]) for item in repair.prompt_variants}
                submitted_variant_ids = {candidate.prompt_variant_id for candidate in candidates}
                if submitted_variant_ids != approved_variant_ids:
                    raise ConflictError(
                        "evaluation candidate variants do not match the repair plan"
                    )

            workflow = self._get_workflow(session, workflow_id)
            profiles = tuple(
                replace(
                    binding.profile,
                    healthy=provider_health[binding.profile.provider_id],
                )
                for binding in self.provider_bindings.values()
            )
            profiles = self.evidence_store.hydrate_profiles(session, profiles)
            routing_request = RoutingRequest(
                required_capabilities=frozenset({WorkCapability(campaign.work_capability)}),
                data_classification=DataClassification[workflow.data_classification],
                risk=RiskLevel[campaign.risk],
                allowed_egress=self.allowed_egress,
                minimum_evidence_samples=(
                    self.high_risk_min_evidence_samples
                    if RiskLevel[campaign.risk] >= RiskLevel.HIGH
                    else 0
                ),
                objective=self.routing_objective,
            )
            routing = self.router.route(routing_request, profiles)
            ranked_by_id = {item.provider_id: item for item in routing.ranked_candidates}
            profiles_by_id = {profile.provider_id: profile for profile in profiles}
            normalized_candidates: list[CandidateEvidence] = []
            for candidate in candidates:
                ranked = ranked_by_id.get(candidate.provider_id)
                profile = profiles_by_id.get(candidate.provider_id)
                if ranked is None or profile is None:
                    raise ValidationError("candidate provider is not policy eligible")
                if (
                    candidate.provider_family != profile.provider_family
                    or candidate.model_version != profile.model_version
                    or candidate.profile_version != profile.profile_version
                ):
                    raise ValidationError("candidate provider identity does not match policy")
                review_routing = self.router.route(
                    replace(
                        routing_request,
                        purpose=RoutingPurpose.REVIEW,
                        producer_provider_id=candidate.provider_id,
                        producer_family=candidate.provider_family,
                    ),
                    profiles,
                )
                eligible_reviewer_ids = {
                    item.provider_id for item in review_routing.ranked_candidates
                }
                for review in candidate.reviews:
                    reviewer = profiles_by_id.get(review.reviewer_provider_id)
                    if reviewer is None or review.reviewer_provider_id not in eligible_reviewer_ids:
                        raise ValidationError("reviewer provider is not policy eligible")
                    if (
                        review.reviewer_provider_family != reviewer.provider_family
                        or review.reviewer_model_version != reviewer.model_version
                        or review.reviewer_profile_version != reviewer.profile_version
                    ):
                        raise ValidationError("reviewer provider identity does not match policy")
                normalized_candidates.append(replace(candidate, routing_score=ranked.score))

            try:
                decision = self.evaluator.evaluate(
                    self._evaluation_policy(campaign),
                    EvaluationBatch(
                        campaign_id=campaign.id,
                        prompt_contract_version=campaign.prompt_contract_version,
                        current_iteration=campaign.current_iteration,
                        prior_cost_microunits=campaign.total_cost_microunits,
                        candidates=tuple(normalized_candidates),
                    ),
                )
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc

            routing_snapshot = {
                "policy_version": routing.policy_version,
                "provider_policy_version": self.provider_policy_version,
                "objective": routing.objective.value,
                "objective_profile_version": routing.objective_profile_version,
                "eligible": [asdict(item) for item in routing.ranked_candidates],
                "rejected": {
                    provider_id: list(reasons) for provider_id, reasons in routing.rejected.items()
                },
            }
            batch = EvaluationBatchRecord(
                campaign_id=campaign.id,
                workflow_id=workflow.id,
                actor_id=actor_id,
                idempotency_key=idempotency_key.strip(),
                request_digest=request_digest,
                repair_id=repair.id if repair is not None else None,
                iteration=campaign.current_iteration,
                status=decision.status.value,
                winner_candidate_id=decision.winner_candidate_id,
                ranked_candidates=[asdict(item) for item in decision.ranked_candidates],
                rejected_candidates={
                    candidate_id: list(reasons)
                    for candidate_id, reasons in decision.rejected_candidates.items()
                },
                total_cost_microunits=decision.total_cost_microunits,
                policy_version=decision.policy_version,
                routing_snapshot=routing_snapshot,
            )
            session.add(batch)
            session.flush()
            ranks = {
                item.candidate_id: index
                for index, item in enumerate(decision.ranked_candidates, start=1)
            }
            for candidate in normalized_candidates:
                session.add(
                    EvaluationCandidateRecord(
                        campaign_id=campaign.id,
                        batch_id=batch.id,
                        candidate_id=candidate.candidate_id,
                        provider_id=candidate.provider_id,
                        provider_family=candidate.provider_family,
                        model_version=candidate.model_version,
                        profile_version=candidate.profile_version,
                        prompt_variant_id=candidate.prompt_variant_id,
                        prompt_contract_version=candidate.prompt_contract_version,
                        iteration=candidate.iteration,
                        succeeded=candidate.succeeded,
                        output_digest=candidate.output_digest,
                        latency_ms=candidate.latency_ms,
                        cost_microunits=candidate.cost_microunits,
                        routing_score=candidate.routing_score,
                        checks=[asdict(check) for check in candidate.checks],
                        reviews=[asdict(review) for review in candidate.reviews],
                        rejection_reasons=list(
                            decision.rejected_candidates.get(candidate.candidate_id, ())
                        ),
                        rank=ranks.get(candidate.candidate_id),
                    )
                )
            campaign.status = decision.status.value
            campaign.total_cost_microunits = decision.total_cost_microunits
            campaign.winner_candidate_id = decision.winner_candidate_id
            if decision.next_iteration is not None:
                campaign.current_iteration = decision.next_iteration
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="evaluation.batch_decided",
                actor_id=actor_id,
                resource_type="evaluation_batch",
                resource_id=batch.id,
                outcome=decision.status.value,
                payload={
                    "campaign_id": campaign.id,
                    "iteration": batch.iteration,
                    "candidate_ids": sorted(
                        candidate.candidate_id for candidate in normalized_candidates
                    ),
                    "winner_candidate_id": decision.winner_candidate_id,
                    "total_cost_microunits": decision.total_cost_microunits,
                    "policy_version": decision.policy_version,
                    "routing_policy_version": routing.policy_version,
                    "repair_id": repair.id if repair is not None else None,
                },
            )
            session.flush()
            return {**self._evaluation_batch_dict(batch), "replayed": False}

    def get_evaluation_campaign(
        self, workflow_id: str, campaign_id: str, *, principal_id: str
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_EVALUATION)
            self._get_workflow(session, workflow_id)
            campaign = session.get(EvaluationCampaignRecord, campaign_id)
            if campaign is None or campaign.workflow_id != workflow_id:
                raise NotFoundError("evaluation campaign was not found")
            result = self._evaluation_campaign_dict(campaign)
            batches = session.scalars(
                select(EvaluationBatchRecord)
                .where(EvaluationBatchRecord.campaign_id == campaign_id)
                .order_by(EvaluationBatchRecord.iteration)
            ).all()
            serialized_batches: list[dict[str, Any]] = []
            for batch in batches:
                serialized = self._evaluation_batch_dict(batch)
                candidates = session.scalars(
                    select(EvaluationCandidateRecord)
                    .where(EvaluationCandidateRecord.batch_id == batch.id)
                    .order_by(
                        EvaluationCandidateRecord.rank.asc().nulls_last(),
                        EvaluationCandidateRecord.candidate_id,
                    )
                ).all()
                serialized["candidates"] = [
                    self._evaluation_candidate_dict(candidate) for candidate in candidates
                ]
                serialized_batches.append(serialized)
            result["batches"] = serialized_batches
            result["repairs"] = [
                self._evaluation_repair_dict(record)
                for record in session.scalars(
                    select(EvaluationRepairRecord)
                    .where(EvaluationRepairRecord.campaign_id == campaign_id)
                    .order_by(EvaluationRepairRecord.target_iteration)
                ).all()
            ]
            result["promotions"] = [
                self._evaluation_promotion_dict(record)
                for record in session.scalars(
                    select(EvaluationPromotionRecord).where(
                        EvaluationPromotionRecord.campaign_id == campaign_id
                    )
                ).all()
            ]
            return result

    def list_evaluation_campaigns(
        self, workflow_id: str, *, principal_id: str
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_EVALUATION)
            self._get_workflow(session, workflow_id)
            campaigns = session.scalars(
                select(EvaluationCampaignRecord)
                .where(EvaluationCampaignRecord.workflow_id == workflow_id)
                .order_by(EvaluationCampaignRecord.created_at, EvaluationCampaignRecord.id)
            ).all()
            return [self._evaluation_campaign_dict(campaign) for campaign in campaigns]

    def plan_evaluation_repair(
        self,
        *,
        workflow_id: str,
        campaign_id: str,
        actor_id: str,
        idempotency_key: str,
        prompt_variants: tuple[PromptVariant, ...],
        rationale: str,
    ) -> dict[str, Any]:
        return self.evaluation_lifecycle.plan_evaluation_repair(
            workflow_id=workflow_id,
            campaign_id=campaign_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            prompt_variants=prompt_variants,
            rationale=rationale,
        )

    def execute_evaluation_campaign(
        self,
        *,
        workflow_id: str,
        campaign_id: str,
        task_id: str,
        actor_id: str,
        idempotency_key: str,
        prompt_variants: tuple[PromptVariant, ...],
        repair_id: str | None = None,
    ) -> dict[str, Any]:
        return self.evaluation_pipeline.execute_evaluation_campaign(
            workflow_id=workflow_id,
            campaign_id=campaign_id,
            task_id=task_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            prompt_variants=prompt_variants,
            repair_id=repair_id,
        )

    def get_evaluation_execution(
        self, workflow_id: str, execution_id: str, *, principal_id: str
    ) -> dict[str, Any]:
        return self.evaluation_pipeline.get_evaluation_execution(
            workflow_id, execution_id, principal_id=principal_id
        )

    def reconcile_evaluation_execution(
        self,
        *,
        workflow_id: str,
        execution_id: str,
        actor_id: str,
        idempotency_key: str,
        decision: EvaluationReconciliationDecision,
        rationale: str,
    ) -> dict[str, Any]:
        return self.evaluation_pipeline.reconcile_evaluation_execution(
            workflow_id=workflow_id,
            execution_id=execution_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            decision=decision,
            rationale=rationale,
        )

    def validate_evaluation_execution(
        self,
        *,
        workflow_id: str,
        execution_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self.evaluation_pipeline.validate_evaluation_execution(
            workflow_id=workflow_id,
            execution_id=execution_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )

    def get_evaluation_assessment(
        self, workflow_id: str, assessment_id: str, *, principal_id: str
    ) -> dict[str, Any]:
        return self.evaluation_pipeline.get_evaluation_assessment(
            workflow_id, assessment_id, principal_id=principal_id
        )

    def reconcile_evaluation_reviews(
        self,
        *,
        workflow_id: str,
        assessment_id: str,
        actor_id: str,
        idempotency_key: str,
        decision: EvaluationReconciliationDecision,
        rationale: str,
    ) -> dict[str, Any]:
        return self.evaluation_pipeline.reconcile_evaluation_reviews(
            workflow_id=workflow_id,
            assessment_id=assessment_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            decision=decision,
            rationale=rationale,
        )

    def recover_evaluation_assessment(
        self,
        *,
        workflow_id: str,
        assessment_id: str,
        actor_id: str,
        idempotency_key: str,
        decision: EvaluationRecoveryDecision,
        rationale: str,
    ) -> dict[str, Any]:
        return self.evaluation_lifecycle.recover_evaluation_assessment(
            workflow_id=workflow_id,
            assessment_id=assessment_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            decision=decision,
            rationale=rationale,
        )

    def promote_evaluation_winner(
        self,
        *,
        workflow_id: str,
        assessment_id: str,
        actor_id: str,
        idempotency_key: str,
        rationale: str,
    ) -> dict[str, Any]:
        return self.evaluation_lifecycle.promote_evaluation_winner(
            workflow_id=workflow_id,
            assessment_id=assessment_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            rationale=rationale,
        )

    def _review_or_submit_trusted_assessment(
        self, assessment_id: str, actor_id: str
    ) -> dict[str, Any]:
        return self.evaluation_pipeline.resume_trusted_assessment(assessment_id, actor_id)

    def _submit_trusted_assessment(self, assessment_id: str, actor_id: str) -> dict[str, Any]:
        return self.evaluation_pipeline.submit_trusted_assessment(assessment_id, actor_id)

    def list_ci_check_evidence(
        self, workflow_id: str, *, principal_id: str
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_AUDIT)
            self._get_workflow(session, workflow_id)
            checks = session.scalars(
                select(CiCheckEvidence)
                .where(CiCheckEvidence.workflow_id == workflow_id)
                .order_by(CiCheckEvidence.created_at, CiCheckEvidence.id)
            ).all()
            return [self._ci_check_dict(check) for check in checks]

    def ingest_ci_check_evidence(
        self,
        *,
        actor_id: str,
        delivery_id: str,
        repository: str,
        check_run_id: str,
        check_name: str,
        revision: str,
        status: str,
        conclusion: str,
        details_url: str | None,
        app_slug: str | None,
        payload_digest: str,
    ) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, actor_id, Capability.SUBMIT_CI_EVIDENCE)
            existing_delivery = session.scalar(
                select(CiCheckEvidence).where(
                    CiCheckEvidence.source == "github",
                    CiCheckEvidence.delivery_id == delivery_id,
                )
            )
            if existing_delivery is not None:
                if existing_delivery.payload_digest != payload_digest:
                    raise ConflictError("CI delivery ID was reused with a different payload")
                return {**self._ci_check_dict(existing_delivery), "replayed": True}

            workflows = session.scalars(
                select(Workflow)
                .where(
                    Workflow.repository_scope == repository,
                    Workflow.candidate_revision == revision,
                )
                .with_for_update()
            ).all()
            if not workflows:
                raise NotFoundError("CI evidence does not match a workflow candidate revision")
            if len(workflows) != 1:
                raise ConflictError("CI evidence matches multiple workflows")
            workflow = workflows[0]

            existing_run = session.scalar(
                select(CiCheckEvidence).where(
                    CiCheckEvidence.source == "github",
                    CiCheckEvidence.check_run_id == check_run_id,
                )
            )
            if existing_run is not None:
                raise ConflictError("CI check run was already recorded by another delivery")

            evidence = CiCheckEvidence(
                workflow_id=workflow.id,
                source="github",
                delivery_id=delivery_id,
                repository=repository,
                check_run_id=check_run_id,
                check_name=check_name,
                revision=revision,
                status=status,
                conclusion=conclusion,
                details_url=details_url,
                app_slug=app_slug,
                payload_digest=payload_digest,
            )
            session.add(evidence)
            session.flush()
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="ci.check_recorded",
                actor_id=actor_id,
                resource_type="ci_check",
                resource_id=evidence.id,
                outcome="SUCCEEDED",
                payload={
                    "source": evidence.source,
                    "repository": repository,
                    "check_run_id": check_run_id,
                    "check_name": check_name,
                    "revision": revision,
                    "status": status,
                    "conclusion": conclusion,
                    "payload_digest": payload_digest,
                    "gate_eligible": False,
                },
            )
            session.flush()
            return {**self._ci_check_dict(evidence), "replayed": False}

    def propose_pull_request(
        self,
        *,
        workflow_id: str,
        actor_id: str,
        head_branch: str,
        title: str,
        body: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        github = self._require_github_app()
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, actor_id, Capability.PROPOSE_PULL_REQUEST)
            workflow = self._get_workflow(session, workflow_id, lock=True)
            if WorkflowState(workflow.state) != WorkflowState.AWAITING_HUMAN_APPROVAL:
                raise InvalidTransitionError("workflow is not ready for a pull-request proposal")
            if not workflow.repository_scope or not workflow.candidate_revision:
                raise ValidationError("workflow lacks repository and candidate revision")
            repo_policy = github.validate_pull_request(
                repository=workflow.repository_scope,
                head_branch=head_branch,
                expected_revision=workflow.candidate_revision,
                title=title,
                body=body,
            )
            request_digest = self._digest(
                {
                    "workflow_id": workflow_id,
                    "repository": workflow.repository_scope,
                    "revision": workflow.candidate_revision,
                    "head_branch": head_branch,
                    "title": title,
                    "body": body,
                }
            )
            existing = session.scalar(
                select(PullRequestProposalRecord).where(
                    PullRequestProposalRecord.actor_id == actor_id,
                    PullRequestProposalRecord.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError("pull-request idempotency key was reused")
                if existing.status == "SUCCEEDED":
                    return self._pull_request_record_dict(existing)
                raise ConflictError(f"pull-request proposal requires reconciliation: {existing.id}")
            record = PullRequestProposalRecord(
                workflow_id=workflow.id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                repository=workflow.repository_scope,
                head_branch=head_branch,
                base_branch=repo_policy.base_branch,
                revision=workflow.candidate_revision,
                title=title.strip(),
                status="RUNNING",
            )
            session.add(record)
            session.flush()
            record_id = record.id
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="git.pull_request_requested",
                actor_id=actor_id,
                resource_type="pull_request_proposal",
                resource_id=record.id,
                outcome="STARTED",
                payload={"revision": record.revision, "head_branch": head_branch},
            )

        try:
            proposal = github.create_draft_pull_request(
                repository=record.repository,
                head_branch=head_branch,
                expected_revision=record.revision,
                title=title,
                body=body,
            )
        except Exception as exc:
            self._mark_pull_request_unknown(record_id, type(exc).__name__)
            raise
        return self._complete_pull_request(record_id, proposal)

    def reconcile_pull_request(
        self, *, workflow_id: str, proposal_id: str, actor_id: str
    ) -> dict[str, Any]:
        github = self._require_github_app()
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, actor_id, Capability.RECONCILE_GIT_OPERATION)
            record = session.get(PullRequestProposalRecord, proposal_id)
            if record is None or record.workflow_id != workflow_id:
                raise NotFoundError("pull-request proposal was not found")
            if record.status == "SUCCEEDED":
                return self._pull_request_record_dict(record)
            repository, branch, revision, base = (
                record.repository,
                record.head_branch,
                record.revision,
                record.base_branch,
            )
        matches = [
            item
            for item in github.find_pull_requests(repository=repository, head_branch=branch)
            if item.head_revision == revision and item.base_branch == base
        ]
        if len(matches) > 1:
            raise ConflictError("multiple remote pull requests match the proposal")
        if matches:
            return self._complete_pull_request(proposal_id, matches[0], reconciled=True)
        with self.session_factory() as session, session.begin():
            record = session.get(PullRequestProposalRecord, proposal_id, with_for_update=True)
            assert record is not None
            record.status = "FAILED"
            record.error_code = "RemotePullRequestNotFound"
            record.completed_at = datetime.now(UTC)
            append_audit_event(
                session,
                workflow_id=record.workflow_id,
                event_type="git.pull_request_reconciled",
                actor_id=actor_id,
                resource_type="pull_request_proposal",
                resource_id=record.id,
                outcome="FAILED",
                payload={"revision": record.revision, "remote_match_count": 0},
            )
            return self._pull_request_record_dict(record)

    def assess_merge_readiness(
        self,
        *,
        workflow_id: str,
        proposal_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        github = self._require_github_app()
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, actor_id, Capability.ASSESS_MERGE_READINESS)
            workflow = self._get_workflow(session, workflow_id)
            proposal = session.get(PullRequestProposalRecord, proposal_id)
            if proposal is None or proposal.workflow_id != workflow_id:
                raise NotFoundError("pull-request proposal was not found")
            if proposal.status != "SUCCEEDED" or proposal.pull_number is None:
                raise ConflictError("pull-request proposal is not confirmed")
            if workflow.candidate_revision != proposal.revision:
                raise ConflictError("pull-request proposal revision is stale")
            existing = session.scalar(
                select(MergeReadinessAssessmentRecord).where(
                    MergeReadinessAssessmentRecord.actor_id == actor_id,
                    MergeReadinessAssessmentRecord.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if (
                    existing.workflow_id != workflow_id
                    or existing.proposal_id != proposal_id
                    or existing.revision != proposal.revision
                ):
                    raise ConflictError("readiness idempotency key was reused")
                if existing.status == "SUCCEEDED":
                    return self._merge_readiness_dict(existing)
                raise ConflictError("readiness assessment requires a new idempotency key")
            repository, pull_number, revision = (
                proposal.repository,
                proposal.pull_number,
                proposal.revision,
            )
            assessment = MergeReadinessAssessmentRecord(
                workflow_id=workflow_id,
                proposal_id=proposal_id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                revision=revision,
                status="RUNNING",
                ready=None,
                reasons=[],
                checks={},
                policy_version=github.policy.policy_version,
            )
            session.add(assessment)
            session.flush()
            assessment_id = assessment.id
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="git.merge_readiness_requested",
                actor_id=actor_id,
                resource_type="merge_readiness",
                resource_id=assessment.id,
                outcome="STARTED",
                payload={"revision": revision, "pull_number": pull_number},
            )
        try:
            result = github.assess_merge_readiness(
                repository=repository,
                pull_number=pull_number,
                expected_revision=revision,
            )
        except Exception as exc:
            self._mark_readiness_failed(assessment_id, type(exc).__name__)
            raise
        return self._record_merge_readiness(assessment_id, result)

    def list_pull_request_proposals(
        self, workflow_id: str, *, principal_id: str
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_AUDIT)
            self._get_workflow(session, workflow_id)
            records = session.scalars(
                select(PullRequestProposalRecord)
                .where(PullRequestProposalRecord.workflow_id == workflow_id)
                .order_by(PullRequestProposalRecord.created_at)
            ).all()
            return [self._pull_request_record_dict(item) for item in records]

    def confirm_pull_request_merged(
        self,
        *,
        workflow_id: str,
        proposal_id: str,
        assessment_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        github = self._require_github_app()
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session, actor_id, Capability.CONFIRM_GIT_MERGE, require_human=True
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            if WorkflowState(workflow.state) == WorkflowState.MERGED:
                existing = session.scalar(
                    select(MergeConfirmationRecord).where(
                        MergeConfirmationRecord.actor_id == actor_id,
                        MergeConfirmationRecord.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None and existing.workflow_id == workflow_id:
                    return self._merge_confirmation_dict(existing)
                raise InvalidTransitionError("workflow merge was already confirmed")
            if WorkflowState(workflow.state) != WorkflowState.APPROVED:
                raise InvalidTransitionError("workflow lacks exact human merge approval")
            proposal = session.get(PullRequestProposalRecord, proposal_id)
            assessment = session.get(MergeReadinessAssessmentRecord, assessment_id)
            if (
                proposal is None
                or proposal.workflow_id != workflow_id
                or proposal.status != "SUCCEEDED"
                or proposal.pull_number is None
            ):
                raise ConflictError("pull-request proposal is not confirmed")
            if (
                assessment is None
                or assessment.workflow_id != workflow_id
                or assessment.proposal_id != proposal_id
                or assessment.status != "SUCCEEDED"
                or assessment.ready is not True
            ):
                raise ConflictError("successful merge-readiness evidence is required")
            if not workflow.candidate_revision or not (
                proposal.revision == assessment.revision == workflow.candidate_revision
            ):
                raise ConflictError("merge evidence is stale")
            approval = session.scalar(
                select(Approval)
                .where(
                    Approval.workflow_id == workflow_id,
                    Approval.action == ApprovalAction.MERGE.value,
                    Approval.decision == ApprovalDecision.APPROVED.value,
                    Approval.revision == workflow.candidate_revision,
                    Approval.policy_version == workflow.policy_version,
                    Approval.consumed_at.is_not(None),
                )
                .order_by(Approval.created_at.desc())
                .limit(1)
            )
            if approval is None:
                raise ConflictError("consumed human approval evidence is missing")
            existing = session.scalar(
                select(MergeConfirmationRecord).where(
                    MergeConfirmationRecord.actor_id == actor_id,
                    MergeConfirmationRecord.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if (
                    existing.workflow_id != workflow_id
                    or existing.proposal_id != proposal_id
                    or existing.assessment_id != assessment_id
                    or existing.revision != workflow.candidate_revision
                ):
                    raise ConflictError("merge-confirmation idempotency key was reused")
                if existing.status == "SUCCEEDED":
                    return self._merge_confirmation_dict(existing)
                raise ConflictError("merge confirmation requires a new idempotency key")
            record = MergeConfirmationRecord(
                workflow_id=workflow_id,
                proposal_id=proposal_id,
                assessment_id=assessment_id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                revision=workflow.candidate_revision,
                status="RUNNING",
            )
            session.add(record)
            session.flush()
            record_id = record.id
            repository, pull_number, revision = (
                proposal.repository,
                proposal.pull_number,
                proposal.revision,
            )
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="git.merge_confirmation_requested",
                actor_id=actor_id,
                resource_type="merge_confirmation",
                resource_id=record.id,
                outcome="STARTED",
                payload={"revision": revision, "pull_number": pull_number},
            )
        try:
            result = github.confirm_pull_request_merged(
                repository=repository,
                pull_number=pull_number,
                expected_revision=revision,
            )
        except Exception as exc:
            self._mark_merge_confirmation_failed(record_id, type(exc).__name__)
            raise
        try:
            return self._complete_merge_confirmation(record_id, result)
        except Exception as exc:
            self._mark_merge_confirmation_failed(record_id, type(exc).__name__)
            raise

    def list_merge_confirmations(
        self, workflow_id: str, *, principal_id: str
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_AUDIT)
            self._get_workflow(session, workflow_id)
            records = session.scalars(
                select(MergeConfirmationRecord)
                .where(MergeConfirmationRecord.workflow_id == workflow_id)
                .order_by(MergeConfirmationRecord.created_at)
            ).all()
            return [self._merge_confirmation_dict(item) for item in records]

    def register_deployment_environment(
        self,
        *,
        actor_id: str,
        environment_id: str,
        name: str,
        classification: EnvironmentClassification,
        repository: str,
        base_branch: str,
        resource_scope: tuple[str, ...],
        required_checks: tuple[str, ...],
        required_attestations: tuple[str, ...],
        verification_policy: tuple[str, ...],
        rollback_policy: str,
        policy_version: str,
        provider: str = "dry-run",
        account_scope: str = "none",
        region: str = "none",
        adapter_id: str = "dry-run-v1",
    ) -> dict[str, Any]:
        values = {
            "environment_id": environment_id.strip(),
            "name": name.strip(),
            "classification": classification.value,
            "provider": provider.strip(),
            "account_scope": account_scope.strip(),
            "region": region.strip(),
            "resource_scope": sorted(set(resource_scope)),
            "adapter_id": adapter_id.strip(),
            "policy_version": policy_version.strip(),
            "repository": repository.strip(),
            "base_branch": base_branch.strip(),
            "required_checks": sorted(set(required_checks)),
            "required_attestations": sorted(set(required_attestations)),
            "verification_policy": list(verification_policy),
            "rollback_policy": rollback_policy.strip(),
        }
        if not all(
            values[key]
            for key in (
                "environment_id",
                "name",
                "repository",
                "base_branch",
                "policy_version",
                "rollback_policy",
            )
        ):
            raise ValidationError("complete deployment environment policy is required")
        if (
            len(environment_id) > 128
            or len(name) > 200
            or len(repository) > 500
            or len(base_branch) > 200
            or len(policy_version) > 64
            or len(rollback_policy) > 256
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
                for character in environment_id
            )
        ):
            raise ValidationError("deployment environment policy exceeds its boundary")
        collections = (
            resource_scope,
            required_checks,
            required_attestations,
            verification_policy,
        )
        if any(
            len(items) > 100 or any(not item.strip() or len(item) > 256 for item in items)
            for items in collections
        ):
            raise ValidationError("deployment environment collection is invalid")
        if len(resource_scope) != len(set(resource_scope)):
            raise ValidationError("deployment resource scope contains duplicates")
        if len(required_checks) != len(set(required_checks)) or len(required_attestations) != len(
            set(required_attestations)
        ):
            raise ValidationError("deployment environment requirements contain duplicates")
        if len(verification_policy) != len(set(verification_policy)):
            raise ValidationError("deployment verification policy contains duplicates")
        if classification is EnvironmentClassification.PRODUCTION:
            raise AuthorizationError("Phase 4 does not permit production environments")
        adapter = self.deployment_adapters.get(adapter_id)
        if adapter is None:
            raise AuthorizationError("deployment adapter is not approved")
        if adapter.requires_credentials:
            if (
                not self.enable_local_deployment
                or classification is not EnvironmentClassification.DEVELOPMENT
                or provider != "local-k3d"
                or account_scope != "local"
                or region != "local"
            ):
                raise AuthorizationError("credentialed deployment is restricted to local k3d")
        elif provider != "dry-run" or account_scope != "none" or region != "none":
            raise AuthorizationError("dry-run environment must have no external target")
        config_digest = self._digest(values)
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session,
                actor_id,
                Capability.MANAGE_DEPLOYMENT_ENVIRONMENTS,
                require_human=True,
            )
            existing = session.get(DeploymentEnvironment, environment_id)
            if existing is not None:
                if existing.config_digest != config_digest:
                    raise ConflictError("deployment environment ID is already bound")
                return self._deployment_environment_dict(existing)
            environment = DeploymentEnvironment(
                id=values["environment_id"],
                name=values["name"],
                classification=values["classification"],
                provider=values["provider"],
                account_scope=values["account_scope"],
                region=values["region"],
                resource_scope=values["resource_scope"],
                adapter_id=values["adapter_id"],
                policy_version=values["policy_version"],
                repository=values["repository"],
                base_branch=values["base_branch"],
                required_checks=values["required_checks"],
                required_attestations=values["required_attestations"],
                verification_policy=values["verification_policy"],
                rollback_policy=values["rollback_policy"],
                config_digest=config_digest,
                active=True,
                created_by=actor_id,
            )
            session.add(environment)
            session.flush()
            return self._deployment_environment_dict(environment)

    def list_deployment_environments(self, *, principal_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_DEPLOYMENT)
            environments = session.scalars(
                select(DeploymentEnvironment).order_by(DeploymentEnvironment.id)
            ).all()
            return [self._deployment_environment_dict(item) for item in environments]

    def create_deployment_plan(
        self,
        *,
        workflow_id: str,
        actor_id: str,
        environment_id: str,
        artifact_digests: tuple[str, ...],
        operations: tuple[dict[str, Any], ...],
        declared_impact: str,
        verification_probes: tuple[str, ...],
        rollback_reference: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if (
            not idempotency_key.strip()
            or not declared_impact.strip()
            or not rollback_reference.strip()
        ):
            raise ValidationError("deployment plan intent, rollback, and idempotency are required")
        if (
            len(idempotency_key) > 128
            or len(declared_impact) > 4_000
            or len(rollback_reference) > 256
            or len(artifact_digests) > 100
            or len(operations) > 100
            or len(verification_probes) > 100
            or any(not probe.strip() or len(probe) > 256 for probe in verification_probes)
        ):
            raise ValidationError("deployment plan exceeds its boundary")
        if not artifact_digests or len(artifact_digests) != len(set(artifact_digests)):
            raise ValidationError("deployment plan requires unique artifact digests")
        if not operations or not verification_probes:
            raise ValidationError("deployment operations and verification probes are required")
        if any(not self._is_sha256_digest(item) for item in artifact_digests):
            raise ValidationError("artifact digests must use sha256:<64 lowercase hex>")
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session, actor_id, Capability.CREATE_DEPLOYMENT_PLAN, require_human=True
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            existing = session.scalar(
                select(DeploymentPlanRecord).where(
                    DeploymentPlanRecord.actor_id == actor_id,
                    DeploymentPlanRecord.idempotency_key == idempotency_key,
                )
            )
            environment = session.get(DeploymentEnvironment, environment_id)
            if environment is None or not environment.active:
                raise NotFoundError("active deployment environment not found")
            revision = workflow.merged_revision
            request_payload = {
                "workflow_id": workflow_id,
                "environment_id": environment_id,
                "environment_config_digest": environment.config_digest,
                "revision": revision,
                "artifact_digests": sorted(artifact_digests),
                "operations": list(operations),
                "declared_impact": declared_impact.strip(),
                "verification_probes": list(verification_probes),
                "rollback_reference": rollback_reference.strip(),
                "policy_version": environment.policy_version,
            }
            request_digest = self._digest(request_payload)
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError("deployment-plan idempotency key was reused")
                return self._deployment_plan_dict(existing)
            if WorkflowState(workflow.state) is not WorkflowState.MERGED:
                raise InvalidTransitionError("workflow is not ready for a deployment plan")
            if not revision:
                raise ConflictError("workflow lacks an independently confirmed merge revision")
            if workflow.repository_scope != environment.repository:
                raise ConflictError("deployment environment repository does not match workflow")
            operation_resources = {
                str(operation.get("resource_id", "")).strip() for operation in operations
            }
            if not operation_resources.issubset(set(environment.resource_scope)):
                raise AuthorizationError("deployment operation exceeds environment resource scope")
            if not set(verification_probes).issubset(set(environment.verification_policy)):
                raise AuthorizationError("deployment probe exceeds environment verification policy")
            if rollback_reference.strip() != environment.rollback_policy:
                raise ConflictError(
                    "deployment rollback reference does not match environment policy"
                )
            plan_id = str(uuid4())
            plan_digest = self._digest(request_payload)
            plan = DeploymentPlan(
                plan_id=plan_id,
                workflow_id=workflow_id,
                environment_id=environment_id,
                revision=revision,
                artifact_digests=tuple(sorted(artifact_digests)),
                operations=operations,
                verification_probes=verification_probes,
                rollback_reference=rollback_reference.strip(),
                policy_version=environment.policy_version,
                digest=plan_digest,
            )
            adapter = self.deployment_adapters.get(environment.adapter_id)
            if adapter is None:
                raise AuthorizationError("deployment environment adapter is disabled")
            adapter.validate_plan(plan)
            record = DeploymentPlanRecord(
                id=plan_id,
                workflow_id=workflow_id,
                environment_id=environment_id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                revision=revision,
                artifact_digests=list(plan.artifact_digests),
                operations=list(operations),
                declared_impact=declared_impact.strip(),
                verification_probes=list(verification_probes),
                rollback_reference=rollback_reference.strip(),
                policy_version=environment.policy_version,
                digest=plan_digest,
            )
            session.add(record)
            self._transition(
                session,
                workflow,
                WorkflowState.AWAITING_DEPLOYMENT_APPROVAL,
                actor_id=actor_id,
                reason="immutable deployment plan created for confirmed merge revision",
                extra={
                    "deployment_plan_id": plan_id,
                    "environment_id": environment_id,
                    "plan_digest": plan_digest,
                    "revision": revision,
                    "simulated": not adapter.requires_credentials,
                },
            )
            session.flush()
            return self._deployment_plan_dict(record)

    def list_deployment_plans(self, workflow_id: str, *, principal_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_DEPLOYMENT)
            self._get_workflow(session, workflow_id)
            records = session.scalars(
                select(DeploymentPlanRecord)
                .where(DeploymentPlanRecord.workflow_id == workflow_id)
                .order_by(DeploymentPlanRecord.created_at)
            ).all()
            return [self._deployment_plan_dict(item) for item in records]

    def execute_deployment_dry_run(
        self,
        *,
        workflow_id: str,
        plan_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request_digest = self._digest(
            {"workflow_id": workflow_id, "plan_id": plan_id, "mode": "DRY_RUN"}
        )
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session, actor_id, Capability.EXECUTE_DEPLOYMENT, require_human=True
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            existing = session.scalar(
                select(DeploymentAttemptRecord).where(
                    DeploymentAttemptRecord.actor_id == actor_id,
                    DeploymentAttemptRecord.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError("deployment idempotency key was reused")
                if existing.status == DeploymentAttemptStatus.SUCCEEDED.value:
                    return self._deployment_attempt_dict(existing)
                raise ConflictError("deployment attempt is not replayable")
            if WorkflowState(workflow.state) is not WorkflowState.AWAITING_DEPLOYMENT_APPROVAL:
                raise InvalidTransitionError("workflow is not awaiting deployment approval")
            plan_record = session.get(DeploymentPlanRecord, plan_id)
            if plan_record is None or plan_record.workflow_id != workflow_id:
                raise NotFoundError("deployment plan not found")
            environment = session.get(DeploymentEnvironment, plan_record.environment_id)
            if environment is None or not environment.active:
                raise ConflictError("deployment environment is unavailable")
            adapter = self.deployment_adapters.get(environment.adapter_id)
            if adapter is None or adapter.requires_credentials:
                raise AuthorizationError("only the no-credential dry-run adapter is permitted")
            approval = session.scalar(
                select(Approval)
                .where(
                    Approval.workflow_id == workflow_id,
                    Approval.action == ApprovalAction.DEPLOY.value,
                    Approval.decision == ApprovalDecision.APPROVED.value,
                    Approval.environment_id == environment.id,
                    Approval.plan_digest == plan_record.digest,
                    Approval.revision == plan_record.revision,
                    Approval.policy_version == plan_record.policy_version,
                    Approval.consumed_at.is_(None),
                )
                .order_by(Approval.created_at.desc())
                .limit(1)
            )
            if approval is None:
                raise AuthorizationError("exact unconsumed deployment approval is required")
            if self._as_utc(approval.expires_at) <= datetime.now(UTC):
                raise AuthorizationError("deployment approval has expired")
            plan = self._deployment_plan_value(plan_record)
            adapter.validate_plan(plan)
            attempt = DeploymentAttemptRecord(
                workflow_id=workflow_id,
                plan_id=plan_id,
                approval_id=approval.id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                adapter_id=adapter.adapter_id,
                status=DeploymentAttemptStatus.RUNNING.value,
                simulated=True,
                result_json={"intent_committed": True, "credential_requested": False},
            )
            session.add(attempt)
            session.flush()
            approval.consumed_at = datetime.now(UTC)
            attempt_id = attempt.id
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="deployment.dry_run_started",
                actor_id=actor_id,
                resource_type="deployment_attempt",
                resource_id=attempt_id,
                outcome="STARTED",
                payload={
                    "plan_id": plan_id,
                    "plan_digest": plan.digest,
                    "environment_id": plan.environment_id,
                    "revision": plan.revision,
                    "approval_id": approval.id,
                    "credential_requested": False,
                },
            )
        try:
            dry_run_adapter = cast(DeploymentAdapter, adapter)
            execution = dry_run_adapter.execute(plan, idempotency_key=attempt_id)
            observation = dry_run_adapter.observe(plan, execution)
            verification = dry_run_adapter.verify(plan, observation)
        except Exception as exc:
            self._fail_deployment_dry_run(attempt_id, type(exc).__name__)
            raise
        with self.session_factory() as session, session.begin():
            finalized_attempt = session.get(
                DeploymentAttemptRecord, attempt_id, with_for_update=True
            )
            if (
                finalized_attempt is None
                or finalized_attempt.status != DeploymentAttemptStatus.RUNNING.value
            ):
                raise ConflictError("deployment attempt is no longer running")
            workflow = self._get_workflow(session, workflow_id, lock=True)
            finalized_attempt.operation_reference = execution.operation_reference
            finalized_attempt.simulated = execution.simulated
            finalized_attempt.result_json = {
                "execution": execution.evidence,
                "observation": observation.evidence,
                "changed": execution.changed,
            }
            finalized_attempt.completed_at = datetime.now(UTC)
            status = (
                DeploymentAttemptStatus.SUCCEEDED
                if verification.passed and not execution.changed and execution.simulated
                else DeploymentAttemptStatus.ROLLBACK_REQUIRED
            )
            finalized_attempt.status = status.value
            session.add(
                DeploymentVerificationRecord(
                    workflow_id=workflow_id,
                    attempt_id=finalized_attempt.id,
                    passed=verification.passed,
                    observed_revision=verification.observed_revision,
                    evidence=verification.evidence,
                )
            )
            if status is DeploymentAttemptStatus.ROLLBACK_REQUIRED:
                self._transition(
                    session,
                    workflow,
                    WorkflowState.ROLLBACK_REQUIRED,
                    actor_id="orchestrator",
                    reason="deployment dry-run evidence violated its no-change invariant",
                    extra={"deployment_attempt_id": finalized_attempt.id},
                )
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="deployment.dry_run_completed",
                actor_id="orchestrator",
                resource_type="deployment_attempt",
                resource_id=finalized_attempt.id,
                outcome=status.value,
                payload={
                    "plan_digest": plan.digest,
                    "revision": plan.revision,
                    "verification_passed": verification.passed,
                    "simulated": execution.simulated,
                    "changed": execution.changed,
                    "credential_requested": False,
                },
            )
            session.flush()
            return self._deployment_attempt_dict(finalized_attempt)

    def execute_local_deployment(
        self,
        *,
        workflow_id: str,
        plan_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not idempotency_key.strip() or len(idempotency_key) > 128:
            raise ValidationError("deployment idempotency key is invalid")
        if not self.enable_local_deployment or (
            self.credential_broker_factory is None
            and not isinstance(self.credential_broker, CredentialSessionBroker)
        ):
            raise AuthorizationError("local credentialed deployment is disabled")
        request_digest = self._digest(
            {"workflow_id": workflow_id, "plan_id": plan_id, "mode": "LOCAL_K3D"}
        )
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session, actor_id, Capability.EXECUTE_DEPLOYMENT, require_human=True
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            existing = session.scalar(
                select(DeploymentAttemptRecord).where(
                    DeploymentAttemptRecord.actor_id == actor_id,
                    DeploymentAttemptRecord.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError("deployment idempotency key was reused")
                if existing.status == DeploymentAttemptStatus.SUCCEEDED.value:
                    return self._deployment_attempt_dict(existing)
                raise ConflictError("deployment attempt is not replayable")
            if WorkflowState(workflow.state) is not WorkflowState.AWAITING_DEPLOYMENT_APPROVAL:
                raise InvalidTransitionError("workflow is not awaiting deployment approval")
            plan_record = session.get(DeploymentPlanRecord, plan_id)
            if plan_record is None or plan_record.workflow_id != workflow_id:
                raise NotFoundError("deployment plan not found")
            environment = session.get(DeploymentEnvironment, plan_record.environment_id)
            if (
                environment is None
                or not environment.active
                or environment.classification != EnvironmentClassification.DEVELOPMENT.value
                or environment.provider != "local-k3d"
                or environment.account_scope != "local"
                or environment.region != "local"
            ):
                raise AuthorizationError("deployment environment is not an active local target")
            adapter = self.deployment_adapters.get(environment.adapter_id)
            if adapter is None or not adapter.requires_credentials:
                raise AuthorizationError("credentialed local deployment adapter is unavailable")
            approval = session.scalar(
                select(Approval)
                .where(
                    Approval.workflow_id == workflow_id,
                    Approval.action == ApprovalAction.DEPLOY.value,
                    Approval.decision == ApprovalDecision.APPROVED.value,
                    Approval.environment_id == environment.id,
                    Approval.plan_digest == plan_record.digest,
                    Approval.revision == plan_record.revision,
                    Approval.policy_version == plan_record.policy_version,
                    Approval.consumed_at.is_(None),
                )
                .order_by(Approval.created_at.desc())
                .limit(1)
            )
            if approval is None:
                raise AuthorizationError("exact unconsumed deployment approval is required")
            if self._as_utc(approval.expires_at) <= datetime.now(UTC):
                raise AuthorizationError("deployment approval has expired")
            plan = self._deployment_plan_value(plan_record)
            adapter.validate_plan(plan)
            attempt = DeploymentAttemptRecord(
                workflow_id=workflow_id,
                plan_id=plan_id,
                approval_id=approval.id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                adapter_id=adapter.adapter_id,
                status=DeploymentAttemptStatus.RUNNING.value,
                simulated=False,
                result_json={"intent_committed": True, "credential_requested": True},
            )
            session.add(attempt)
            session.flush()
            approval.consumed_at = datetime.now(UTC)
            attempt_id = attempt.id
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="deployment.local_started",
                actor_id=actor_id,
                resource_type="deployment_attempt",
                resource_id=attempt_id,
                outcome="STARTED",
                payload={
                    "adapter_id": adapter.adapter_id,
                    "approval_id": approval.id,
                    "environment_id": plan.environment_id,
                    "plan_digest": plan.digest,
                    "revision": plan.revision,
                },
            )

        credentialed_adapter = cast(CredentialedDeploymentAdapter, adapter)
        target_contacted = False

        def run_adapter(
            credential: str, handle: WorkloadCredentialHandle
        ) -> CredentialedDeploymentRun:
            nonlocal target_contacted
            target_contacted = True
            return credentialed_adapter.run_with_credential(
                plan,
                credential,
                handle,
                idempotency_key=attempt_id,
            )

        try:
            credential_request = credentialed_adapter.credential_request(plan)
            session_broker = (
                self.credential_broker_factory.for_request(credential_request)
                if self.credential_broker_factory is not None
                else cast(CredentialSessionBroker, self.credential_broker)
            )
            credential_use = session_broker.run(credential_request, run_adapter)
            run = credential_use.result
        except DeploymentOutcomeUnknownError as exc:
            self._fail_local_deployment(
                attempt_id,
                DeploymentAttemptStatus.UNKNOWN,
                type(exc).__name__,
                target_contacted=True,
            )
            raise
        except DeploymentVerificationError as exc:
            self._fail_local_deployment(
                attempt_id,
                DeploymentAttemptStatus.ROLLBACK_REQUIRED,
                type(exc).__name__,
                target_contacted=True,
            )
            raise
        except Exception as exc:
            self._fail_local_deployment(
                attempt_id,
                DeploymentAttemptStatus.FAILED,
                type(exc).__name__,
                target_contacted=target_contacted,
            )
            raise

        execution = run.execution
        observation = run.observation
        verification = run.verification
        with self.session_factory() as session, session.begin():
            finalized = session.get(DeploymentAttemptRecord, attempt_id, with_for_update=True)
            if finalized is None or finalized.status != DeploymentAttemptStatus.RUNNING.value:
                raise ConflictError("deployment attempt is no longer running")
            workflow = self._get_workflow(session, workflow_id, lock=True)
            finalized.operation_reference = execution.operation_reference
            finalized.result_json = {
                "credential": self._credential_handle_dict(credential_use.handle),
                "execution": execution.evidence,
                "observation": observation.evidence,
                "changed": execution.changed,
            }
            finalized.completed_at = datetime.now(UTC)
            status = (
                DeploymentAttemptStatus.SUCCEEDED
                if verification.passed and not execution.simulated
                else DeploymentAttemptStatus.ROLLBACK_REQUIRED
            )
            finalized.status = status.value
            session.add(
                DeploymentVerificationRecord(
                    workflow_id=workflow_id,
                    attempt_id=finalized.id,
                    passed=verification.passed,
                    observed_revision=verification.observed_revision,
                    evidence=verification.evidence,
                )
            )
            target_state = (
                WorkflowState.DEPLOYED
                if status is DeploymentAttemptStatus.SUCCEEDED
                else WorkflowState.ROLLBACK_REQUIRED
            )
            self._transition(
                session,
                workflow,
                target_state,
                actor_id="orchestrator",
                reason="local deployment verification completed",
                extra={"deployment_attempt_id": finalized.id},
            )
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="deployment.local_completed",
                actor_id="orchestrator",
                resource_type="deployment_attempt",
                resource_id=finalized.id,
                outcome=status.value,
                payload={
                    "changed": execution.changed,
                    "credential_reference": credential_use.handle.reference,
                    "plan_digest": plan.digest,
                    "revision": plan.revision,
                    "verification_passed": verification.passed,
                },
            )
            session.flush()
            return self._deployment_attempt_dict(finalized)

    def list_deployment_attempts(
        self, workflow_id: str, *, principal_id: str
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_DEPLOYMENT)
            self._get_workflow(session, workflow_id)
            records = session.scalars(
                select(DeploymentAttemptRecord)
                .where(DeploymentAttemptRecord.workflow_id == workflow_id)
                .order_by(DeploymentAttemptRecord.started_at)
            ).all()
            return [self._deployment_attempt_dict(item) for item in records]

    def execute_local_rollback(
        self,
        *,
        workflow_id: str,
        attempt_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not idempotency_key.strip() or len(idempotency_key) > 128:
            raise ValidationError("rollback idempotency key is invalid")
        if not self.enable_local_deployment or (
            self.credential_broker_factory is None
            and not isinstance(self.credential_broker, CredentialSessionBroker)
        ):
            raise AuthorizationError("local credentialed recovery is disabled")
        request_digest = self._digest(
            {"workflow_id": workflow_id, "attempt_id": attempt_id, "mode": "LOCAL_ROLLBACK"}
        )
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session, actor_id, Capability.EXECUTE_ROLLBACK, require_human=True
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            existing = session.scalar(
                select(DeploymentRollbackRecord).where(
                    DeploymentRollbackRecord.actor_id == actor_id,
                    DeploymentRollbackRecord.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError("rollback idempotency key was reused")
                if existing.status == RollbackStatus.SUCCEEDED.value:
                    return self._deployment_rollback_dict(existing)
                raise ConflictError("rollback attempt is not replayable")
            if WorkflowState(workflow.state) is not WorkflowState.ROLLBACK_REQUIRED:
                raise InvalidTransitionError("workflow is not awaiting controlled recovery")
            attempt = session.get(DeploymentAttemptRecord, attempt_id)
            if (
                attempt is None
                or attempt.workflow_id != workflow_id
                or attempt.status != DeploymentAttemptStatus.ROLLBACK_REQUIRED.value
            ):
                raise ConflictError("rollback target is not a contained deployment attempt")
            plan_record = session.get(DeploymentPlanRecord, attempt.plan_id)
            if plan_record is None:
                raise ConflictError("rollback plan evidence is unavailable")
            environment = session.get(DeploymentEnvironment, plan_record.environment_id)
            if (
                environment is None
                or not environment.active
                or environment.classification != EnvironmentClassification.DEVELOPMENT.value
                or environment.provider != "local-k3d"
                or environment.account_scope != "local"
                or environment.region != "local"
            ):
                raise AuthorizationError("rollback environment is not an active local target")
            adapter = self.deployment_adapters.get(environment.adapter_id)
            if adapter is None or not isinstance(adapter, RecoverableDeploymentAdapter):
                raise AuthorizationError("recoverable local deployment adapter is unavailable")
            approval = session.scalar(
                select(Approval)
                .where(
                    Approval.workflow_id == workflow_id,
                    Approval.action == ApprovalAction.ROLLBACK.value,
                    Approval.decision == ApprovalDecision.APPROVED.value,
                    Approval.environment_id == environment.id,
                    Approval.plan_digest == plan_record.digest,
                    Approval.deployment_attempt_id == attempt_id,
                    Approval.revision == plan_record.revision,
                    Approval.policy_version == plan_record.policy_version,
                    Approval.target == plan_record.rollback_reference,
                    Approval.consumed_at.is_(None),
                )
                .order_by(Approval.created_at.desc())
                .limit(1)
            )
            if approval is None:
                raise AuthorizationError("exact unconsumed rollback approval is required")
            if self._as_utc(approval.expires_at) <= datetime.now(UTC):
                raise AuthorizationError("rollback approval has expired")
            result_json = attempt.result_json
            execution_evidence = result_json.get("execution")
            previous_release = (
                execution_evidence.get("previous_release")
                if isinstance(execution_evidence, dict)
                else None
            )
            if not isinstance(previous_release, dict) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in previous_release.items()
            ):
                raise ConflictError("recorded rollback snapshot is unavailable or invalid")
            plan = self._deployment_plan_value(plan_record)
            adapter.validate_plan(plan)
            rollback = DeploymentRollbackRecord(
                workflow_id=workflow_id,
                attempt_id=attempt_id,
                approval_id=approval.id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                rollback_reference=plan.rollback_reference,
                decision=ApprovalDecision.APPROVED.value,
                rationale=approval.rationale,
                status=RollbackStatus.RUNNING.value,
                evidence={"intent_committed": True, "credential_requested": True},
            )
            session.add(rollback)
            session.flush()
            approval.consumed_at = datetime.now(UTC)
            rollback_id = rollback.id
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="deployment.rollback_started",
                actor_id=actor_id,
                resource_type="deployment_rollback",
                resource_id=rollback_id,
                outcome="STARTED",
                payload={
                    "approval_id": approval.id,
                    "deployment_attempt_id": attempt_id,
                    "environment_id": environment.id,
                    "plan_digest": plan.digest,
                    "rollback_reference": plan.rollback_reference,
                },
            )

        recoverable_adapter = adapter
        started = monotonic()
        target_contacted = False

        def run_rollback(
            credential: str, handle: WorkloadCredentialHandle
        ) -> CredentialedRollbackRun:
            nonlocal target_contacted
            target_contacted = True
            return recoverable_adapter.rollback_with_credential(
                plan,
                dict(previous_release),
                credential,
                handle,
                idempotency_key=rollback_id,
            )

        try:
            credential_request = recoverable_adapter.rollback_credential_request(plan)
            session_broker = (
                self.credential_broker_factory.for_request(credential_request)
                if self.credential_broker_factory is not None
                else cast(CredentialSessionBroker, self.credential_broker)
            )
            credential_use = session_broker.run(credential_request, run_rollback)
            run = credential_use.result
        except (DeploymentOutcomeUnknownError, DeploymentVerificationError) as exc:
            self._fail_local_rollback(
                rollback_id,
                RollbackStatus.UNKNOWN,
                type(exc).__name__,
                target_contacted=True,
                recovery_duration_ms=int((monotonic() - started) * 1000),
            )
            raise
        except Exception as exc:
            self._fail_local_rollback(
                rollback_id,
                RollbackStatus.FAILED,
                type(exc).__name__,
                target_contacted=target_contacted,
                recovery_duration_ms=int((monotonic() - started) * 1000),
            )
            raise

        duration_ms = int((monotonic() - started) * 1000)
        with self.session_factory() as session, session.begin():
            finalized = session.get(DeploymentRollbackRecord, rollback_id, with_for_update=True)
            if finalized is None or finalized.status != RollbackStatus.RUNNING.value:
                raise ConflictError("rollback attempt is no longer running")
            workflow = self._get_workflow(session, workflow_id, lock=True)
            finalized.operation_reference = run.execution.operation_reference
            finalized.completed_at = datetime.now(UTC)
            finalized.recovery_duration_ms = duration_ms
            finalized.evidence = {
                "credential": self._credential_handle_dict(credential_use.handle),
                "execution": run.execution.evidence,
                "observation": run.observation.evidence,
                "verification": run.verification.evidence,
                "changed": run.execution.changed,
            }
            recovered = run.verification.passed and not run.execution.simulated
            finalized.status = (
                RollbackStatus.SUCCEEDED.value if recovered else RollbackStatus.FAILED.value
            )
            if recovered:
                self._transition(
                    session,
                    workflow,
                    WorkflowState.ROLLED_BACK,
                    actor_id="orchestrator",
                    reason="approved local rollback independently verified recovery",
                    extra={
                        "deployment_attempt_id": attempt_id,
                        "deployment_rollback_id": rollback_id,
                        "recovery_duration_ms": duration_ms,
                    },
                )
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="deployment.rollback_completed",
                actor_id="orchestrator",
                resource_type="deployment_rollback",
                resource_id=rollback_id,
                outcome=finalized.status,
                payload={
                    "changed": run.execution.changed,
                    "deployment_attempt_id": attempt_id,
                    "recovery_duration_ms": duration_ms,
                    "verification_passed": run.verification.passed,
                },
            )
            session.flush()
            result = self._deployment_rollback_dict(finalized)
        if not recovered:
            raise DeploymentVerificationError("local rollback did not verify recovery")
        return result

    def list_deployment_rollbacks(
        self, workflow_id: str, *, principal_id: str
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_DEPLOYMENT)
            self._get_workflow(session, workflow_id)
            records = session.scalars(
                select(DeploymentRollbackRecord)
                .where(DeploymentRollbackRecord.workflow_id == workflow_id)
                .order_by(DeploymentRollbackRecord.created_at)
            ).all()
            return [self._deployment_rollback_dict(item) for item in records]

    def operational_snapshot(self, *, principal_id: str | None = None) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.session_factory() as session:
            if principal_id is not None:
                self.policy.authorize(session, principal_id, Capability.READ_AUDIT)
            workflow_rows = session.execute(
                select(Workflow.state, func.count(Workflow.id)).group_by(Workflow.state)
            ).all()
            task_rows = session.execute(
                select(Task.status, func.count(Task.id)).group_by(Task.status)
            ).all()
            provider_rows = session.execute(
                select(
                    ProviderObservation.provider_id,
                    func.count(ProviderObservation.id),
                    func.sum(case((ProviderObservation.succeeded.is_(True), 1), else_=0)),
                    func.sum(case((ProviderObservation.validation_passed.is_(True), 1), else_=0)),
                    func.avg(ProviderObservation.latency_ms),
                ).group_by(ProviderObservation.provider_id)
            ).all()
            active_leases = session.scalar(
                select(func.count(Task.id)).where(
                    Task.status.in_({TaskStatus.LEASED.value, TaskStatus.RUNNING.value}),
                    Task.lease_expires_at >= now,
                )
            )
            expired_leases = session.scalar(
                select(func.count(Task.id)).where(
                    Task.status.in_({TaskStatus.LEASED.value, TaskStatus.RUNNING.value}),
                    Task.lease_expires_at < now,
                )
            )
            approval_waiting = session.scalar(
                select(func.count(Workflow.id)).where(
                    Workflow.state == WorkflowState.AWAITING_HUMAN_APPROVAL.value
                )
            )
            oldest_approval_gate = session.scalar(
                select(func.min(Workflow.updated_at)).where(
                    Workflow.state == WorkflowState.AWAITING_HUMAN_APPROVAL.value
                )
            )
            ci_rows = session.execute(
                select(CiCheckEvidence.conclusion, func.count(CiCheckEvidence.id)).group_by(
                    CiCheckEvidence.conclusion
                )
            ).all()
        providers = []
        for (
            provider_id,
            observations,
            succeeded,
            validation_passed,
            average_latency,
        ) in provider_rows:
            total = int(observations)
            successful = int(succeeded or 0)
            validated = int(validation_passed or 0)
            providers.append(
                {
                    "provider_id": str(provider_id),
                    "observations": total,
                    "succeeded": successful,
                    "failed": total - successful,
                    "success_rate": successful / total,
                    "validation_rate": validated / total,
                    "average_latency_ms": float(average_latency or 0),
                }
            )
        return {
            "generated_at": now.isoformat(),
            "workflows_by_state": {str(state): int(count) for state, count in workflow_rows},
            "tasks_by_status": {str(task_status): int(count) for task_status, count in task_rows},
            "providers": providers,
            "leases": {"active": int(active_leases or 0), "expired": int(expired_leases or 0)},
            "approval_gates": {
                "waiting": int(approval_waiting or 0),
                "oldest_wait_seconds": (
                    max(
                        0.0,
                        (
                            now
                            - (
                                oldest_approval_gate.replace(tzinfo=UTC)
                                if oldest_approval_gate.tzinfo is None
                                else oldest_approval_gate.astimezone(UTC)
                            )
                        ).total_seconds(),
                    )
                    if oldest_approval_gate is not None
                    else 0.0
                ),
            },
            "ci_checks_by_conclusion": {
                str(conclusion): int(count) for conclusion, count in ci_rows
            },
        }

    def lease_next_task(self, *, worker_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, worker_id, Capability.LEASE_TASK)
            task = session.scalar(
                select(Task)
                .where(Task.status == TaskStatus.READY.value)
                .order_by(Task.created_at, Task.position)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if task is None:
                return None
            task.status = TaskStatus.LEASED.value
            task.lease_owner = worker_id
            task.lease_token = str(uuid4())
            task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self.lease_seconds)
            self._get_workflow(session, task.workflow_id, lock=True)
            append_audit_event(
                session,
                workflow_id=task.workflow_id,
                event_type="task.leased",
                actor_id=worker_id,
                resource_type="task",
                resource_id=task.id,
                outcome="SUCCEEDED",
                payload={"lease_expires_at": task.lease_expires_at.isoformat()},
            )
            session.flush()
            return self._task_dict(task, include_lease_token=True)

    def reclaim_expired_tasks(self, *, worker_id: str) -> int:
        now = datetime.now(UTC)
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, worker_id, Capability.LEASE_TASK)
            tasks = session.scalars(
                select(Task)
                .where(
                    Task.status.in_({TaskStatus.LEASED.value, TaskStatus.RUNNING.value}),
                    Task.lease_expires_at < now,
                )
                .with_for_update(skip_locked=True)
            ).all()
            for task in tasks:
                workflow = self._get_workflow(session, task.workflow_id, lock=True)
                prior_owner = task.lease_owner
                prior_status = TaskStatus(task.status)
                task.lease_owner = None
                task.lease_token = None
                task.lease_expires_at = None
                if prior_status is TaskStatus.LEASED:
                    task.status = TaskStatus.READY.value
                    event_type = "task.lease_reclaimed"
                    outcome = "SUCCEEDED"
                    payload = {
                        "prior_owner": prior_owner,
                        "execution_started": False,
                    }
                else:
                    task.status = TaskStatus.BLOCKED.value
                    running_attempt = session.scalar(
                        select(TaskAttempt)
                        .where(
                            TaskAttempt.task_id == task.id,
                            TaskAttempt.status == TaskStatus.RUNNING.value,
                        )
                        .order_by(TaskAttempt.attempt_number.desc())
                        .limit(1)
                    )
                    if running_attempt is not None:
                        running_attempt.status = TaskStatus.TIMED_OUT.value
                        running_attempt.error_code = "LeaseExpired"
                        running_attempt.output = running_attempt.output | {
                            "reconciliation": {"error": "execution outcome requires reconciliation"}
                        }
                        running_attempt.completed_at = now
                    self._deactivate_task_grant(session, task.id)
                    workflow.block_reason = "EXECUTION_RECONCILIATION_REQUIRED"
                    self._transition(
                        session,
                        workflow,
                        WorkflowState.BLOCKED,
                        actor_id=worker_id,
                        reason="running task lease expired with unknown external outcome",
                        extra={
                            "task_id": task.id,
                            "attempt_id": getattr(running_attempt, "id", None),
                        },
                    )
                    event_type = "task.reconciliation_required"
                    outcome = "BLOCKED"
                    payload = {
                        "prior_owner": prior_owner,
                        "execution_started": True,
                        "attempt_id": getattr(running_attempt, "id", None),
                    }
                append_audit_event(
                    session,
                    workflow_id=task.workflow_id,
                    event_type=event_type,
                    actor_id=worker_id,
                    resource_type="task",
                    resource_id=task.id,
                    outcome=outcome,
                    payload=payload,
                )
            return len(tasks)

    def claim_windsurf_task(self, *, task_id: str, principal_id: str) -> dict[str, Any]:
        if self.repository_registry is None:
            raise IntegrationDisabledError("Windsurf repository handoff is not configured")
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.CLAIM_IDE_TASK)
            task = session.get(Task, task_id)
            if task is None:
                raise NotFoundError("task not found")
            existing = self._existing_windsurf_claim(task, principal_id)
            if existing is not None:
                return existing
            workflow = self._get_workflow(session, task.workflow_id)
            self._validate_windsurf_claimable(workflow, task)
            repository_scope = workflow.repository_scope
            assert repository_scope is not None
            requested_base = workflow.candidate_revision
            policy_version = workflow.policy_version
            objective = task.objective

        registration = self.repository_registry.resolve(repository_scope)
        base_revision = self.repository_registry.resolve_commit(
            repository_scope, requested_base or registration.base_revision
        )
        branch = f"codex/task-{task_id}"
        if self.repository_registry.branch_exists(repository_scope, branch):
            raise ConflictError("Windsurf handoff branch already exists")
        handoff = WindsurfHandoff().create(
            workflow_id=task.workflow_id,
            task_id=task.id,
            repository=repository_scope,
            branch=branch,
            base_revision=base_revision,
            objective=objective,
            assigned_principal=principal_id,
            policy_version=policy_version,
            writable_paths=registration.writable_paths,
        )
        handoff_payload = asdict(handoff)

        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, principal_id, Capability.CLAIM_IDE_TASK)
            task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
            if task is None:
                raise NotFoundError("task not found")
            existing = self._existing_windsurf_claim(task, principal_id)
            if existing is not None:
                return existing
            workflow = self._get_workflow(session, task.workflow_id, lock=True)
            self._validate_windsurf_claimable(workflow, task)
            if (
                workflow.repository_scope != repository_scope
                or workflow.policy_version != policy_version
                or workflow.candidate_revision != requested_base
            ):
                raise ConflictError("workflow changed while the Windsurf handoff was prepared")
            task.status = TaskStatus.RUNNING.value
            task.lease_owner = principal_id
            task.lease_token = str(uuid4())
            task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self.lease_seconds)
            attempt = TaskAttempt(
                task=task,
                attempt_number=len(task.attempts) + 1,
                agent_id=principal_id,
                provider="windsurf",
                model="cascade-interactive",
                status=TaskStatus.RUNNING.value,
                output={"handoff": handoff_payload},
            )
            session.add(attempt)
            self._deactivate_task_grant(session, task.id)
            session.add(
                CapabilityGrant(
                    principal_id=principal_id,
                    workflow_id=workflow.id,
                    task_id=task.id,
                    capabilities=[
                        Capability.HEARTBEAT_IDE_TASK.value,
                        Capability.SUBMIT_IDE_EVIDENCE.value,
                    ],
                    policy_version=workflow.policy_version,
                    expires_at=task.lease_expires_at,
                )
            )
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="windsurf.task_claimed",
                actor_id=principal_id,
                resource_type="task",
                resource_id=task.id,
                outcome="SUCCEEDED",
                payload={"attempt_id": attempt.id, "handoff_digest": handoff.digest},
            )
            session.flush()
            return self._windsurf_claim_dict(task, handoff_payload)

    def heartbeat_windsurf_task(
        self, *, task_id: str, lease_token: str, principal_id: str
    ) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
            if task is None:
                raise NotFoundError("task not found")
            self.policy.authorize(
                session,
                principal_id,
                Capability.HEARTBEAT_IDE_TASK,
                workflow_id=task.workflow_id,
                task_id=task.id,
            )
            self._validate_running_lease(task, lease_token, principal_id)
            attempt = self._windsurf_attempt(session, task.id)
            task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self.lease_seconds)
            grant = session.scalar(
                select(CapabilityGrant).where(
                    CapabilityGrant.principal_id == principal_id,
                    CapabilityGrant.task_id == task.id,
                    CapabilityGrant.active.is_(True),
                )
            )
            if grant is not None:
                grant.expires_at = task.lease_expires_at
            self._get_workflow(session, task.workflow_id, lock=True)
            append_audit_event(
                session,
                workflow_id=task.workflow_id,
                event_type="windsurf.task_heartbeat",
                actor_id=principal_id,
                resource_type="task_attempt",
                resource_id=attempt.id,
                outcome="SUCCEEDED",
                payload={"lease_expires_at": task.lease_expires_at.isoformat()},
            )
            return self._task_dict(task)

    def submit_windsurf_evidence(
        self,
        *,
        task_id: str,
        lease_token: str,
        principal_id: str,
        handoff_digest: str,
        result_revision: str,
        files_changed: tuple[str, ...],
        tests_passed: bool,
        test_summary: str,
        tool_activity_summary: str,
    ) -> dict[str, Any]:
        if self.repository_registry is None:
            raise IntegrationDisabledError("Windsurf repository handoff is not configured")
        if (
            not handoff_digest
            or not result_revision
            or not test_summary.strip()
            or not tool_activity_summary.strip()
        ):
            raise ValidationError("complete Windsurf evidence is required")
        if len(test_summary) > 4_000 or len(tool_activity_summary) > 4_000:
            raise ValidationError("Windsurf evidence summary exceeds the size limit")
        if len(files_changed) != len(set(files_changed)):
            raise ValidationError("files_changed contains duplicates")

        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.CLAIM_IDE_TASK)
            task = session.get(Task, task_id)
            if task is None:
                raise NotFoundError("task not found")
            attempt = self._windsurf_attempt(session, task.id)
            handoff = attempt.output.get("handoff")
            if not isinstance(handoff, dict) or handoff.get("digest") != handoff_digest:
                raise ConflictError("Windsurf evidence does not match the handoff digest")
            if handoff.get("assigned_principal") != principal_id:
                raise AuthorizationError("Windsurf handoff belongs to a different principal")
            if task.status == TaskStatus.RUNNING.value:
                self.policy.authorize(
                    session,
                    principal_id,
                    Capability.SUBMIT_IDE_EVIDENCE,
                    workflow_id=task.workflow_id,
                    task_id=task.id,
                )
                self._validate_running_lease(task, lease_token, principal_id)
            elif task.status != TaskStatus.SUCCEEDED.value:
                raise ConflictError("Windsurf task is not accepting evidence")
            workflow_id = task.workflow_id
            repository_scope = handoff.get("repository")
            branch = handoff.get("branch")
            base_revision = handoff.get("base_revision")
            if (
                not isinstance(repository_scope, str)
                or not isinstance(branch, str)
                or not isinstance(base_revision, str)
            ):
                raise ConflictError("stored Windsurf handoff is invalid")

        actual_files = self.repository_registry.verify_revision_evidence(
            scope_id=repository_scope,
            branch=branch,
            base_revision=base_revision,
            result_revision=result_revision,
        )
        if tuple(files_changed) != actual_files:
            raise ConflictError("reported files do not match the Git revision")
        evidence = {
            "handoff_digest": handoff_digest,
            "result_revision": result_revision,
            "files_changed": list(actual_files),
            "tests_passed_claim": tests_passed,
            "test_summary": test_summary.strip(),
            "tool_activity_summary": tool_activity_summary.strip(),
            "test_claim_authoritative": False,
        }
        evidence_digest = self._digest(evidence)

        with self.session_factory() as session, session.begin():
            task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
            if task is None:
                raise NotFoundError("task not found")
            attempt = self._windsurf_attempt(session, task.id)
            prior_evidence = attempt.output.get("evidence")
            if task.status == TaskStatus.SUCCEEDED.value and isinstance(prior_evidence, dict):
                if self._digest(prior_evidence) != evidence_digest:
                    raise ConflictError("Windsurf task already has different evidence")
                return self._task_dict(task)
            self.policy.authorize(
                session,
                principal_id,
                Capability.SUBMIT_IDE_EVIDENCE,
                workflow_id=workflow_id,
                task_id=task.id,
            )
            self._validate_running_lease(task, lease_token, principal_id)
            stored_handoff = attempt.output.get("handoff")
            if (
                not isinstance(stored_handoff, dict)
                or stored_handoff.get("digest") != handoff_digest
            ):
                raise ConflictError("Windsurf handoff changed before evidence finalization")
            workflow = self._get_workflow(session, workflow_id, lock=True)
            if WorkflowState(workflow.state) is not WorkflowState.IMPLEMENTING:
                raise ConflictError("workflow is no longer accepting implementation evidence")
            attempt.output = {"handoff": stored_handoff, "evidence": evidence}
            attempt.status = TaskStatus.SUCCEEDED.value
            attempt.completed_at = datetime.now(UTC)
            task.status = TaskStatus.SUCCEEDED.value
            task.lease_owner = None
            task.lease_token = None
            task.lease_expires_at = None
            self._deactivate_task_grant(session, task.id)
            workflow.candidate_revision = result_revision
            session.add(
                Artifact(
                    workflow_id=workflow.id,
                    task_id=task.id,
                    attempt_id=attempt.id,
                    artifact_type="WINDSURF_IMPLEMENTATION_EVIDENCE",
                    digest=evidence_digest,
                    revision=result_revision,
                    metadata_json=evidence,
                )
            )
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="windsurf.evidence_accepted",
                actor_id=principal_id,
                resource_type="task_attempt",
                resource_id=attempt.id,
                outcome="SUCCEEDED",
                payload={
                    "handoff_digest": handoff_digest,
                    "evidence_digest": evidence_digest,
                    "result_revision": result_revision,
                },
            )
            self._advance_after_task(session, workflow, TaskKind.IMPLEMENT)
            session.flush()
            return self._task_dict(task)

    def execute_leased_task(
        self, *, task_id: str, lease_token: str, worker_id: str
    ) -> dict[str, Any]:
        prepared = self._prepare_execution(task_id, lease_token, worker_id)
        if isinstance(prepared, dict):
            return prepared
        started = monotonic()
        validation_passed = False
        with LeaseHeartbeat(
            lambda: self.heartbeat_task(
                task_id=prepared.task_id,
                lease_token=prepared.lease_token,
                worker_id=prepared.worker_id,
            ),
            interval_seconds=self.heartbeat_interval_seconds,
        ):
            try:
                provider_result = prepared.binding.provider.submit(prepared.request)
                if provider_result.model != prepared.profile.model_version:
                    raise ValidationError("provider model does not match routed model version")
                validate_provider_result(prepared.task_kind, provider_result)
                validation_passed = True
                execution = self.executor.execute(
                    prepared.task_kind,
                    provider_result,
                    prepared.context,
                )
                candidate_revision: str | None = None
                if prepared.task_kind is TaskKind.IMPLEMENT:
                    candidate = execution.evidence.get(
                        "result_revision", provider_result.output.get("candidate_revision")
                    )
                    if not isinstance(candidate, str) or not candidate:
                        raise ValidationError("implementation result lacks candidate revision")
                    candidate_revision = candidate
            except Exception as exc:
                return self._finalize_execution_failure(
                    prepared,
                    exc,
                    validation_passed=validation_passed,
                    latency_ms=int((monotonic() - started) * 1000),
                )
        return self._finalize_execution_success(
            prepared,
            provider_result,
            execution,
            candidate_revision=candidate_revision,
            latency_ms=int((monotonic() - started) * 1000),
        )

    def _prepare_execution(
        self, task_id: str, lease_token: str, worker_id: str
    ) -> PreparedExecution | dict[str, Any]:
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, worker_id, Capability.LEASE_TASK)
            task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
            if task is None:
                raise NotFoundError("task not found")
            self._validate_lease(task, lease_token, worker_id)
            workflow = self._get_workflow(session, task.workflow_id, lock=True)
            task.status = TaskStatus.RUNNING.value
            attempt_number = len(task.attempts) + 1
            role = AgentRole(task.required_role)
            agent_id = AGENT_FOR_ROLE[role]
            attempt = TaskAttempt(
                task=task,
                attempt_number=attempt_number,
                agent_id=agent_id,
                provider="pending-routing",
                model="pending",
                status=TaskStatus.RUNNING.value,
            )
            session.add(attempt)
            session.flush()
            self.policy.authorize(
                session,
                agent_id,
                Capability(task.required_capability),
                workflow_id=workflow.id,
                task_id=task.id,
            )
            routed = self._route_attempt(session, workflow, task, attempt)
            if routed is None:
                return self._record_routing_block(session, workflow, task, attempt)
            binding, profile = routed
            attempt.provider = profile.provider_id
            attempt.model = profile.model_version
            request = ProviderRequest(
                run_id=attempt.id,
                workflow_id=workflow.id,
                task_id=task.id,
                task_kind=TaskKind(task.kind),
                role=role,
                objective=task.objective,
                context={
                    "workflow_title": workflow.title,
                    "workflow_description": workflow.description,
                    "candidate_revision": workflow.candidate_revision,
                    "content_trust": "UNTRUSTED_REPOSITORY_CONTEXT",
                    "data_classification": workflow.data_classification,
                    "repository_scope": workflow.repository_scope,
                },
                required_capability=Capability(task.required_capability),
                idempotency_key=attempt.id,
            )
            context = ExecutionContext(
                workflow_id=workflow.id,
                task_id=task.id,
                repository_scope=workflow.repository_scope,
                candidate_revision=workflow.candidate_revision,
                containment_required=workflow.containment_required,
            )
            task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self.lease_seconds)
            session.flush()
            return PreparedExecution(
                workflow_id=workflow.id,
                task_id=task.id,
                attempt_id=attempt.id,
                lease_token=lease_token,
                worker_id=worker_id,
                agent_id=agent_id,
                task_kind=TaskKind(task.kind),
                binding=binding,
                profile=profile,
                request=request,
                context=context,
            )

    def heartbeat_task(self, *, task_id: str, lease_token: str, worker_id: str) -> None:
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, worker_id, Capability.LEASE_TASK)
            task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
            if task is None:
                raise NotFoundError("task not found")
            self._validate_running_lease(task, lease_token, worker_id)
            task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self.lease_seconds)
            self._get_workflow(session, task.workflow_id, lock=True)
            append_audit_event(
                session,
                workflow_id=task.workflow_id,
                event_type="task.lease_heartbeat",
                actor_id=worker_id,
                resource_type="task",
                resource_id=task.id,
                outcome="SUCCEEDED",
                payload={"lease_expires_at": task.lease_expires_at.isoformat()},
            )

    def _finalize_execution_success(
        self,
        prepared: PreparedExecution,
        provider_result: ProviderResult,
        execution: ExecutionResult,
        *,
        candidate_revision: str | None,
        latency_ms: int,
    ) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            task = session.scalar(select(Task).where(Task.id == prepared.task_id).with_for_update())
            attempt = session.get(TaskAttempt, prepared.attempt_id)
            if task is None or attempt is None:
                raise NotFoundError("running task or attempt not found")
            self._validate_running_lease(task, prepared.lease_token, prepared.worker_id)
            if attempt.status != TaskStatus.RUNNING.value:
                raise ConflictError("task attempt is no longer running")
            workflow = self._get_workflow(session, prepared.workflow_id, lock=True)
            attempt.model = provider_result.model
            attempt.output = provider_result.output | {"execution_evidence": execution.evidence}
            attempt.status = TaskStatus.SUCCEEDED.value
            attempt.completed_at = datetime.now(UTC)
            task.status = TaskStatus.SUCCEEDED.value
            task.lease_owner = None
            task.lease_token = None
            task.lease_expires_at = None
            self._deactivate_task_grant(session, task.id)
            self._record_provider_observation(
                session,
                workflow,
                task,
                attempt,
                prepared.profile,
                succeeded=True,
                validation_passed=True,
                latency_ms=latency_ms,
                error_code=None,
            )
            artifact_digest = self._digest(attempt.output)
            if prepared.task_kind is TaskKind.IMPLEMENT:
                workflow.candidate_revision = candidate_revision
            session.add(
                Artifact(
                    workflow_id=workflow.id,
                    task_id=task.id,
                    attempt_id=attempt.id,
                    artifact_type=f"{task.kind}_EVIDENCE",
                    digest=artifact_digest,
                    revision=workflow.candidate_revision,
                    metadata_json={
                        "provider": prepared.profile.provider_id,
                        "provider_reported": provider_result.provider,
                        "model": provider_result.model,
                        "commands_executed": list(execution.commands_executed),
                    },
                )
            )
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="task.succeeded",
                actor_id=prepared.agent_id,
                resource_type="task",
                resource_id=task.id,
                outcome="SUCCEEDED",
                payload={
                    "attempt_id": attempt.id,
                    "artifact_digest": artifact_digest,
                    "candidate_revision": workflow.candidate_revision,
                    "commands_executed": list(execution.commands_executed),
                },
            )
            self._advance_after_task(session, workflow, prepared.task_kind)
            session.flush()
            return self._task_dict(task)

    def _finalize_execution_failure(
        self,
        prepared: PreparedExecution,
        exc: Exception,
        *,
        validation_passed: bool,
        latency_ms: int,
    ) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            task = session.scalar(select(Task).where(Task.id == prepared.task_id).with_for_update())
            attempt = session.get(TaskAttempt, prepared.attempt_id)
            if task is None or attempt is None:
                raise NotFoundError("running task or attempt not found")
            self._validate_running_lease(task, prepared.lease_token, prepared.worker_id)
            if attempt.status != TaskStatus.RUNNING.value:
                raise ConflictError("task attempt is no longer running")
            workflow = self._get_workflow(session, prepared.workflow_id, lock=True)
            self._record_provider_observation(
                session,
                workflow,
                task,
                attempt,
                prepared.profile,
                succeeded=False,
                validation_passed=validation_passed,
                latency_ms=latency_ms,
                error_code=type(exc).__name__,
            )
            return self._record_attempt_failure(
                session,
                workflow,
                task,
                attempt,
                exc,
                worker_id=prepared.worker_id,
            )

    def disposition_workflow(
        self,
        *,
        workflow_id: str,
        actor_id: str,
        decision: DispositionDecision,
        rationale: str,
    ) -> dict[str, Any]:
        if not rationale.strip():
            raise ValidationError("disposition rationale is required")
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session,
                actor_id,
                Capability.DISPOSITION_WORKFLOW,
                require_human=True,
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            if WorkflowState(workflow.state) is not WorkflowState.BLOCKED:
                raise ConflictError("workflow is not blocked")
            if workflow.block_reason != "INPUT_DISPOSITION_REQUIRED":
                raise ConflictError("workflow block is not eligible for input disposition")
            existing = session.scalar(
                select(WorkflowDisposition).where(WorkflowDisposition.workflow_id == workflow.id)
            )
            if existing is not None:
                raise ConflictError("workflow already has an input disposition")
            disposition = WorkflowDisposition(
                workflow_id=workflow.id,
                actor_id=actor_id,
                decision=decision.value,
                original_signals=list(workflow.inspection_signals),
                rationale=rationale.strip(),
            )
            session.add(disposition)
            session.flush()
            if decision is DispositionDecision.REJECT:
                workflow.block_reason = "INPUT_REJECTED"
                self._transition(
                    session,
                    workflow,
                    WorkflowState.REJECTED,
                    actor_id=actor_id,
                    reason="human rejected contained input",
                    extra={"disposition_id": disposition.id},
                )
            else:
                workflow.block_reason = None
                workflow.containment_required = True
                self._transition(
                    session,
                    workflow,
                    WorkflowState.PLANNING,
                    actor_id=actor_id,
                    reason="human authorized contained planning",
                    extra={"disposition_id": disposition.id},
                )
                self._schedule_task(session, workflow, TaskKind.PLAN)
            session.flush()
            return self._workflow_dict(workflow)

    def reconcile_execution(
        self,
        *,
        workflow_id: str,
        task_id: str,
        actor_id: str,
        decision: ReconciliationDecision,
        rationale: str,
    ) -> dict[str, Any]:
        if not rationale.strip():
            raise ValidationError("reconciliation rationale is required")
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session,
                actor_id,
                Capability.RECONCILE_EXECUTION,
                require_human=True,
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            if (
                WorkflowState(workflow.state) is not WorkflowState.BLOCKED
                or workflow.block_reason != "EXECUTION_RECONCILIATION_REQUIRED"
            ):
                raise ConflictError("workflow is not awaiting execution reconciliation")
            task = session.scalar(
                select(Task)
                .where(Task.id == task_id, Task.workflow_id == workflow.id)
                .with_for_update()
            )
            if task is None or task.status != TaskStatus.BLOCKED.value:
                raise ConflictError("task is not awaiting execution reconciliation")
            attempt = session.scalar(
                select(TaskAttempt)
                .where(
                    TaskAttempt.task_id == task.id,
                    TaskAttempt.status == TaskStatus.TIMED_OUT.value,
                    TaskAttempt.error_code == "LeaseExpired",
                )
                .order_by(TaskAttempt.attempt_number.desc())
                .limit(1)
            )
            if attempt is None:
                raise ConflictError("task has no abandoned attempt to reconcile")
            if session.scalar(
                select(ExecutionReconciliation).where(
                    ExecutionReconciliation.attempt_id == attempt.id
                )
            ):
                raise ConflictError("execution attempt was already reconciled")
            record = ExecutionReconciliation(
                workflow_id=workflow.id,
                task_id=task.id,
                attempt_id=attempt.id,
                actor_id=actor_id,
                decision=decision.value,
                rationale=rationale.strip(),
            )
            session.add(record)
            session.flush()
            task.status = TaskStatus.RECONCILED.value
            if decision is ReconciliationDecision.RETRY:
                workflow.block_reason = None
                resume_state = TASK_WORKFLOW_STATE[TaskKind(task.kind)]
                self._transition(
                    session,
                    workflow,
                    resume_state,
                    actor_id=actor_id,
                    reason="human reconciled abandoned execution for retry",
                    extra={"reconciliation_id": record.id, "abandoned_attempt_id": attempt.id},
                )
                self._schedule_task(session, workflow, TaskKind(task.kind))
            else:
                workflow.block_reason = "EXECUTION_RECONCILED_FAILED"
                self._transition(
                    session,
                    workflow,
                    WorkflowState.FAILED,
                    actor_id=actor_id,
                    reason="human reconciled abandoned execution as failed",
                    extra={"reconciliation_id": record.id, "abandoned_attempt_id": attempt.id},
                )
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="execution.reconciled",
                actor_id=actor_id,
                resource_type="task_attempt",
                resource_id=attempt.id,
                outcome=decision.value,
                payload={
                    "reconciliation_id": record.id,
                    "task_id": task.id,
                    "decision": decision.value,
                },
            )
            session.flush()
            return self._workflow_dict(workflow)

    def _route_attempt(
        self,
        session: Session,
        workflow: Workflow,
        task: Task,
        attempt: TaskAttempt,
    ) -> tuple[ProviderBinding, ProviderProfile] | None:
        kind = TaskKind(task.kind)
        producer_id: str | None = None
        producer_family: str | None = None
        purpose = RoutingPurpose.PRODUCE
        if kind in REVIEW_PRODUCER_KIND:
            purpose = RoutingPurpose.REVIEW
            producer_route = session.scalar(
                select(RoutingRecord)
                .join(Task, RoutingRecord.task_id == Task.id)
                .where(
                    Task.workflow_id == workflow.id,
                    Task.kind == REVIEW_PRODUCER_KIND[kind].value,
                    RoutingRecord.selected_provider_id.is_not(None),
                )
                .order_by(RoutingRecord.created_at.desc())
                .limit(1)
            )
            if producer_route is not None:
                producer_id = producer_route.selected_provider_id
                producer_family = producer_route.selected_provider_family

        profiles = tuple(
            replace(
                binding.profile,
                healthy=(binding.profile.enabled and self._provider_is_healthy(binding.provider)),
            )
            for binding in self.provider_bindings.values()
        )
        profiles = self.evidence_store.hydrate_profiles(session, profiles)
        risk = RiskLevel[workflow.risk_class]
        routing_request = RoutingRequest(
            required_capabilities=frozenset({WorkCapability(task.work_capability)}),
            data_classification=DataClassification[workflow.data_classification],
            risk=risk,
            purpose=purpose,
            allowed_egress=self.allowed_egress,
            minimum_evidence_samples=(
                self.high_risk_min_evidence_samples if risk >= RiskLevel.HIGH else 0
            ),
            producer_provider_id=producer_id,
            producer_family=producer_family,
            objective=self.routing_objective,
        )
        decision = self.router.route(routing_request, profiles)
        selected_profile = next(
            (
                profile
                for profile in profiles
                if profile.provider_id == decision.selected_provider_id
            ),
            None,
        )
        session.add(
            RoutingRecord(
                workflow_id=workflow.id,
                task_id=task.id,
                attempt_id=attempt.id,
                policy_version=decision.policy_version,
                request_json={
                    "provider_policy_version": self.provider_policy_version,
                    "required_capabilities": sorted(
                        item.value for item in routing_request.required_capabilities
                    ),
                    "data_classification": routing_request.data_classification.name,
                    "risk": routing_request.risk.name,
                    "purpose": routing_request.purpose.value,
                    "allowed_egress": sorted(item.value for item in routing_request.allowed_egress),
                    "minimum_evidence_samples": routing_request.minimum_evidence_samples,
                    "producer_provider_id": producer_id,
                    "producer_family": producer_family,
                    "objective": decision.objective.value,
                    "objective_profile_version": decision.objective_profile_version,
                },
                ranked_candidates=[
                    {
                        "provider_id": item.provider_id,
                        "score": item.score,
                        "quality_utility": item.quality_utility,
                        "cost_utility": item.cost_utility,
                        "latency_utility": item.latency_utility,
                    }
                    for item in decision.ranked_candidates
                ],
                rejected_candidates={
                    provider_id: list(reasons) for provider_id, reasons in decision.rejected.items()
                },
                selected_provider_id=decision.selected_provider_id,
                selected_provider_family=(
                    selected_profile.provider_family if selected_profile else None
                ),
                selected_model_version=(
                    selected_profile.model_version if selected_profile else None
                ),
            )
        )
        append_audit_event(
            session,
            workflow_id=workflow.id,
            event_type="task.routed" if selected_profile else "task.routing_blocked",
            actor_id="orchestrator",
            resource_type="task",
            resource_id=task.id,
            outcome="SUCCEEDED" if selected_profile else "BLOCKED",
            payload={
                "attempt_id": attempt.id,
                "policy_version": decision.policy_version,
                "provider_policy_version": self.provider_policy_version,
                "selected_provider_id": decision.selected_provider_id,
                "rejected": {
                    provider_id: list(reasons) for provider_id, reasons in decision.rejected.items()
                },
            },
        )
        if selected_profile is None:
            return None
        return self.provider_bindings[selected_profile.provider_id], selected_profile

    @staticmethod
    def _provider_is_healthy(provider: ModelProvider) -> bool:
        try:
            return provider.health()
        except Exception:
            return False

    def _record_routing_block(
        self,
        session: Session,
        workflow: Workflow,
        task: Task,
        attempt: TaskAttempt,
    ) -> dict[str, Any]:
        attempt.status = TaskStatus.FAILED.value
        attempt.provider = "none"
        attempt.model = "none"
        attempt.error_code = "RoutingBlocked"
        attempt.output = {"error": "no policy-eligible provider"}
        attempt.completed_at = datetime.now(UTC)
        task.status = TaskStatus.BLOCKED.value
        task.lease_owner = None
        task.lease_token = None
        task.lease_expires_at = None
        self._deactivate_task_grant(session, task.id)
        workflow.block_reason = "NO_ELIGIBLE_PROVIDER"
        self._transition(
            session,
            workflow,
            WorkflowState.BLOCKED,
            actor_id="orchestrator",
            reason="no policy-eligible provider for task",
            extra={"attempt_id": attempt.id},
        )
        session.flush()
        return self._task_dict(task)

    @staticmethod
    def _record_provider_observation(
        session: Session,
        workflow: Workflow,
        task: Task,
        attempt: TaskAttempt,
        profile: ProviderProfile,
        *,
        succeeded: bool,
        validation_passed: bool,
        latency_ms: int,
        error_code: str | None,
    ) -> None:
        session.add(
            ProviderObservation(
                workflow_id=workflow.id,
                task_id=task.id,
                attempt_id=attempt.id,
                provider_id=profile.provider_id,
                provider_family=profile.provider_family,
                model_version=profile.model_version,
                profile_version=profile.profile_version,
                work_capability=task.work_capability,
                succeeded=succeeded,
                validation_passed=validation_passed,
                latency_ms=max(latency_ms, 0),
                error_code=error_code,
            )
        )

    def approve(
        self,
        *,
        workflow_id: str,
        approver_id: str,
        action: ApprovalAction,
        target: str,
        revision: str,
        decision: ApprovalDecision,
        rationale: str,
        expires_in_minutes: int = 15,
        environment_id: str | None = None,
        plan_digest: str | None = None,
        deployment_attempt_id: str | None = None,
    ) -> dict[str, Any]:
        capability = {
            ApprovalAction.MERGE: Capability.APPROVE_MERGE,
            ApprovalAction.DEPLOY: Capability.APPROVE_DEPLOYMENT,
            ApprovalAction.ROLLBACK: Capability.APPROVE_ROLLBACK,
        }[action]
        if expires_in_minutes < 1:
            raise ValidationError("approval expiry must be at least one minute")
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, approver_id, capability, require_human=True)
            workflow = self._get_workflow(session, workflow_id, lock=True)
            if not target.strip() or not rationale.strip():
                raise ValidationError("approval target and rationale are required")
            if action is ApprovalAction.MERGE:
                if (
                    environment_id is not None
                    or plan_digest is not None
                    or deployment_attempt_id is not None
                ):
                    raise ValidationError("merge approval cannot include deployment bindings")
                if WorkflowState(workflow.state) != WorkflowState.AWAITING_HUMAN_APPROVAL:
                    raise InvalidTransitionError("workflow is not awaiting merge approval")
                if not workflow.candidate_revision or revision != workflow.candidate_revision:
                    raise ConflictError("approval revision does not match the candidate revision")
                policy_version = workflow.policy_version
            elif action is ApprovalAction.DEPLOY:
                if WorkflowState(workflow.state) != WorkflowState.AWAITING_DEPLOYMENT_APPROVAL:
                    raise InvalidTransitionError("workflow is not awaiting deployment approval")
                if deployment_attempt_id is not None:
                    raise ValidationError("deployment approval cannot bind a prior attempt")
                if not environment_id or not plan_digest:
                    raise ValidationError(
                        "deployment approval requires environment and plan digest"
                    )
                plan = session.scalar(
                    select(DeploymentPlanRecord).where(
                        DeploymentPlanRecord.workflow_id == workflow_id,
                        DeploymentPlanRecord.environment_id == environment_id,
                        DeploymentPlanRecord.digest == plan_digest,
                    )
                )
                if plan is None:
                    raise ConflictError("deployment approval does not match an immutable plan")
                if target.strip() != environment_id:
                    raise ConflictError("deployment target must equal the environment ID")
                if revision != plan.revision or revision != workflow.merged_revision:
                    raise ConflictError("deployment approval revision is stale")
                policy_version = plan.policy_version
            else:
                if WorkflowState(workflow.state) is not WorkflowState.ROLLBACK_REQUIRED:
                    raise InvalidTransitionError("workflow is not awaiting rollback disposition")
                if not environment_id or not plan_digest or not deployment_attempt_id:
                    raise ValidationError(
                        "rollback approval requires environment, plan, and attempt bindings"
                    )
                attempt = session.get(DeploymentAttemptRecord, deployment_attempt_id)
                if (
                    attempt is None
                    or attempt.workflow_id != workflow_id
                    or attempt.status != DeploymentAttemptStatus.ROLLBACK_REQUIRED.value
                ):
                    raise ConflictError("rollback approval does not match a contained attempt")
                plan = session.get(DeploymentPlanRecord, attempt.plan_id)
                if (
                    plan is None
                    or plan.environment_id != environment_id
                    or plan.digest != plan_digest
                ):
                    raise ConflictError("rollback approval does not match the failed plan")
                if target.strip() != plan.rollback_reference:
                    raise ConflictError(
                        "rollback target must equal the recorded rollback reference"
                    )
                if revision != plan.revision or revision != workflow.merged_revision:
                    raise ConflictError("rollback approval revision is stale")
                policy_version = plan.policy_version
            approval = Approval(
                workflow_id=workflow.id,
                action=action.value,
                target=target.strip(),
                revision=revision,
                policy_version=policy_version,
                environment_id=environment_id,
                plan_digest=plan_digest,
                deployment_attempt_id=deployment_attempt_id,
                approver_id=approver_id,
                decision=decision.value,
                rationale=rationale.strip(),
                expires_at=datetime.now(UTC) + timedelta(minutes=expires_in_minutes),
            )
            session.add(approval)
            session.flush()
            if action is ApprovalAction.MERGE or (
                action is ApprovalAction.DEPLOY and decision is ApprovalDecision.REJECTED
            ):
                approval.consumed_at = datetime.now(UTC)
                target_state = (
                    WorkflowState.APPROVED
                    if decision is ApprovalDecision.APPROVED
                    else WorkflowState.REJECTED
                )
                self._transition(
                    session,
                    workflow,
                    target_state,
                    actor_id=approver_id,
                    reason=(
                        f"human {decision.value.lower()} {action.value.lower()} for exact revision"
                    ),
                    extra={"approval_id": approval.id, "revision": revision, "target": target},
                )
            elif action is ApprovalAction.DEPLOY:
                append_audit_event(
                    session,
                    workflow_id=workflow.id,
                    event_type="deployment.approved",
                    actor_id=approver_id,
                    resource_type="approval",
                    resource_id=approval.id,
                    outcome="APPROVED",
                    payload={
                        "environment_id": environment_id,
                        "plan_digest": plan_digest,
                        "revision": revision,
                        "policy_version": policy_version,
                        "expires_at": approval.expires_at.isoformat(),
                    },
                )
            else:
                if decision is ApprovalDecision.REJECTED:
                    approval.consumed_at = datetime.now(UTC)
                append_audit_event(
                    session,
                    workflow_id=workflow.id,
                    event_type=(
                        "deployment.rollback_approved"
                        if decision is ApprovalDecision.APPROVED
                        else "deployment.rollback_rejected"
                    ),
                    actor_id=approver_id,
                    resource_type="approval",
                    resource_id=approval.id,
                    outcome=decision.value,
                    payload={
                        "deployment_attempt_id": deployment_attempt_id,
                        "environment_id": environment_id,
                        "plan_digest": plan_digest,
                        "revision": revision,
                        "rollback_reference": target.strip(),
                        "expires_at": approval.expires_at.isoformat(),
                    },
                )
            session.flush()
            return self._approval_dict(approval)

    def cancel_workflow(self, workflow_id: str, *, principal_id: str) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            self.policy.authorize(session, principal_id, Capability.CANCEL_WORKFLOW)
            workflow = self._get_workflow(session, workflow_id, lock=True)
            self._transition(
                session,
                workflow,
                WorkflowState.CANCELLED,
                actor_id=principal_id,
                reason="human cancellation requested",
            )
            for task in workflow.tasks:
                if task.status in {
                    TaskStatus.PENDING.value,
                    TaskStatus.READY.value,
                    TaskStatus.LEASED.value,
                    TaskStatus.RUNNING.value,
                }:
                    task.status = TaskStatus.CANCELLED.value
                    self._deactivate_task_grant(session, task.id)
            session.flush()
            return self._workflow_dict(workflow)

    def _schedule_task(self, session: Session, workflow: Workflow, kind: TaskKind) -> Task:
        role = TASK_ROLE[kind]
        agent_id = AGENT_FOR_ROLE[role]
        task = Task(
            workflow=workflow,
            kind=kind.value,
            required_role=role.value,
            required_capability=TASK_CAPABILITY[kind].value,
            work_capability=TASK_WORK_CAPABILITY[kind].value,
            objective=TASK_OBJECTIVES[kind],
            status=TaskStatus.READY.value,
            position=len(workflow.tasks) + 1,
        )
        session.add(task)
        session.flush()
        session.add(
            CapabilityGrant(
                principal_id=agent_id,
                workflow_id=workflow.id,
                task_id=task.id,
                capabilities=[TASK_CAPABILITY[kind].value],
                policy_version=workflow.policy_version,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        append_audit_event(
            session,
            workflow_id=workflow.id,
            event_type="task.scheduled",
            actor_id="orchestrator",
            resource_type="task",
            resource_id=task.id,
            outcome="SUCCEEDED",
            payload={"kind": kind.value, "role": role.value, "agent_id": agent_id},
        )
        return task

    def _advance_after_task(
        self, session: Session, workflow: Workflow, completed_kind: TaskKind
    ) -> None:
        if completed_kind == TaskKind.PLAN:
            self._transition(
                session,
                workflow,
                WorkflowState.ARCHITECTURE_REVIEW,
                actor_id="orchestrator",
                reason="plan evidence recorded",
            )
            self._schedule_task(session, workflow, TaskKind.ARCHITECTURE_REVIEW)
        elif completed_kind == TaskKind.ARCHITECTURE_REVIEW:
            self._transition(
                session,
                workflow,
                WorkflowState.APPROVED_FOR_IMPLEMENTATION,
                actor_id="orchestrator",
                reason="independent critique completed with no blocking findings",
            )
            self._transition(
                session,
                workflow,
                WorkflowState.IMPLEMENTING,
                actor_id="orchestrator",
                reason="implementation task scoped and granted",
            )
            self._schedule_task(session, workflow, TaskKind.IMPLEMENT)
        elif completed_kind == TaskKind.IMPLEMENT:
            self._transition(
                session,
                workflow,
                WorkflowState.TESTING,
                actor_id="orchestrator",
                reason="candidate revision and change evidence recorded",
            )
            self._schedule_task(session, workflow, TaskKind.TEST)
        elif completed_kind == TaskKind.TEST:
            self._require_candidate_revision(workflow)
            self._transition(
                session,
                workflow,
                WorkflowState.SECURITY_REVIEW,
                actor_id="orchestrator",
                reason="test evidence passed for candidate revision",
            )
            self._schedule_task(session, workflow, TaskKind.SECURITY_REVIEW)
        elif completed_kind == TaskKind.SECURITY_REVIEW:
            self._require_candidate_revision(workflow)
            self._transition(
                session,
                workflow,
                WorkflowState.CODE_REVIEW,
                actor_id="orchestrator",
                reason="security evidence passed for candidate revision",
            )
            self._schedule_task(session, workflow, TaskKind.CODE_REVIEW)
        elif completed_kind == TaskKind.CODE_REVIEW:
            self._require_candidate_revision(workflow)
            self._transition(
                session,
                workflow,
                WorkflowState.AWAITING_HUMAN_APPROVAL,
                actor_id="orchestrator",
                reason="all deterministic evidence recorded for one revision",
            )

    def _transition(
        self,
        session: Session,
        workflow: Workflow,
        target: WorkflowState,
        *,
        actor_id: str,
        reason: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        current = WorkflowState(workflow.state)
        assert_transition_allowed(current, target)
        workflow.state = target.value
        workflow.version += 1
        payload: dict[str, Any] = {
            "from_state": current.value,
            "to_state": target.value,
            "reason": reason,
            "workflow_version": workflow.version,
        }
        if extra:
            payload.update(extra)
        append_audit_event(
            session,
            workflow_id=workflow.id,
            event_type="workflow.transitioned",
            actor_id=actor_id,
            resource_type="workflow",
            resource_id=workflow.id,
            outcome="SUCCEEDED",
            payload=payload,
        )

    def _record_attempt_failure(
        self,
        session: Session,
        workflow: Workflow,
        task: Task,
        attempt: TaskAttempt,
        exc: Exception,
        *,
        worker_id: str,
    ) -> dict[str, Any]:
        attempt.status = TaskStatus.FAILED.value
        if attempt.model == "pending":
            attempt.model = "unknown"
        attempt.error_code = type(exc).__name__
        attempt.output = {"error": "provider or executor operation failed"}
        attempt.completed_at = datetime.now(UTC)
        retrying = attempt.attempt_number < task.max_attempts
        task.status = TaskStatus.READY.value if retrying else TaskStatus.FAILED.value
        task.lease_owner = None
        task.lease_token = None
        task.lease_expires_at = None
        if not retrying:
            self._deactivate_task_grant(session, task.id)
        append_audit_event(
            session,
            workflow_id=workflow.id,
            event_type="task.retry_scheduled" if retrying else "task.failed",
            actor_id=worker_id,
            resource_type="task",
            resource_id=task.id,
            outcome="FAILED",
            payload={
                "attempt_id": attempt.id,
                "attempt_number": attempt.attempt_number,
                "error_code": type(exc).__name__,
                "retrying": retrying,
            },
        )
        if not retrying:
            self._transition(
                session,
                workflow,
                WorkflowState.FAILED,
                actor_id="orchestrator",
                reason="task retry limit exhausted",
            )
        session.flush()
        return self._task_dict(task)

    @staticmethod
    def _validate_lease(task: Task, lease_token: str, worker_id: str) -> None:
        if task.status != TaskStatus.LEASED.value:
            raise ConflictError("task does not have an active lease")
        if task.lease_owner != worker_id or task.lease_token != lease_token:
            raise AuthorizationError("lease owner or token does not match")
        if task.lease_expires_at is None:
            raise ConflictError("task lease has no expiry")
        expires_at = task.lease_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            raise ConflictError("task lease has expired")

    @staticmethod
    def _validate_windsurf_claimable(workflow: Workflow, task: Task) -> None:
        if TaskKind(task.kind) is not TaskKind.IMPLEMENT:
            raise ConflictError("Windsurf may claim only implementation tasks")
        if task.status != TaskStatus.READY.value:
            raise ConflictError("Windsurf task is not ready to claim")
        if WorkflowState(workflow.state) is not WorkflowState.IMPLEMENTING:
            raise ConflictError("workflow is not accepting implementation work")
        if workflow.repository_scope is None:
            raise ConflictError("Windsurf handoff requires a registered repository scope")

    @staticmethod
    def _windsurf_attempt(session: Session, task_id: str) -> TaskAttempt:
        attempt = session.scalar(
            select(TaskAttempt)
            .where(TaskAttempt.task_id == task_id, TaskAttempt.provider == "windsurf")
            .order_by(TaskAttempt.attempt_number.desc())
            .limit(1)
        )
        if attempt is None:
            raise ConflictError("task has no Windsurf handoff attempt")
        return attempt

    @classmethod
    def _existing_windsurf_claim(cls, task: Task, principal_id: str) -> dict[str, Any] | None:
        if task.status != TaskStatus.RUNNING.value or task.lease_owner != principal_id:
            return None
        cls._validate_running_lease(task, str(task.lease_token), principal_id)
        attempts = sorted(task.attempts, key=lambda item: item.attempt_number, reverse=True)
        attempt = next((item for item in attempts if item.provider == "windsurf"), None)
        if attempt is None:
            raise ConflictError("running task is not a Windsurf handoff")
        handoff = attempt.output.get("handoff")
        if not isinstance(handoff, dict):
            raise ConflictError("stored Windsurf handoff is invalid")
        return cls._windsurf_claim_dict(task, handoff)

    @staticmethod
    def _windsurf_claim_dict(task: Task, handoff: dict[str, Any]) -> dict[str, Any]:
        if task.lease_token is None or task.lease_expires_at is None:
            raise ConflictError("Windsurf task has no active lease")
        return {
            "task_id": task.id,
            "workflow_id": task.workflow_id,
            "lease_token": task.lease_token,
            "lease_expires_at": task.lease_expires_at.isoformat(),
            "handoff": handoff,
        }

    @staticmethod
    def _validate_running_lease(task: Task, lease_token: str, worker_id: str) -> None:
        if task.status != TaskStatus.RUNNING.value:
            raise ConflictError("task does not have an active running lease")
        if task.lease_owner != worker_id or task.lease_token != lease_token:
            raise AuthorizationError("running lease owner or token does not match")
        if task.lease_expires_at is None:
            raise ConflictError("running task lease has no expiry")
        expires_at = task.lease_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            raise ConflictError("running task lease has expired")

    @staticmethod
    def _require_candidate_revision(workflow: Workflow) -> None:
        if not workflow.candidate_revision:
            raise ConflictError("candidate revision is required for evidence binding")

    @staticmethod
    def _deactivate_task_grant(session: Session, task_id: str) -> None:
        grants = session.scalars(
            select(CapabilityGrant).where(
                CapabilityGrant.task_id == task_id,
                CapabilityGrant.active.is_(True),
            )
        ).all()
        for grant in grants:
            grant.active = False

    @staticmethod
    def _get_workflow(session: Session, workflow_id: str, *, lock: bool = False) -> Workflow:
        query = select(Workflow).where(Workflow.id == workflow_id)
        if lock:
            query = query.with_for_update()
        workflow = session.scalar(query)
        if workflow is None:
            raise NotFoundError("workflow not found")
        return workflow

    @staticmethod
    def _digest(value: Any) -> str:
        return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _task_dict(task: Task, *, include_lease_token: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": task.id,
            "workflow_id": task.workflow_id,
            "kind": task.kind,
            "required_role": task.required_role,
            "status": task.status,
            "position": task.position,
            "attempts": len(task.attempts),
            "lease_owner": task.lease_owner,
            "lease_expires_at": (
                task.lease_expires_at.isoformat() if task.lease_expires_at else None
            ),
        }
        if include_lease_token:
            result["lease_token"] = task.lease_token
        return result

    @classmethod
    def _workflow_dict(cls, workflow: Workflow) -> dict[str, Any]:
        return {
            "id": workflow.id,
            "title": workflow.title,
            "description": workflow.description,
            "state": workflow.state,
            "version": workflow.version,
            "risk_class": workflow.risk_class,
            "complexity_tier": workflow.complexity_tier,
            "data_classification": workflow.data_classification,
            "repository_scope": workflow.repository_scope,
            "inspection_signals": workflow.inspection_signals,
            "containment_required": workflow.containment_required,
            "block_reason": workflow.block_reason,
            "requester_id": workflow.requester_id,
            "policy_version": workflow.policy_version,
            "candidate_revision": workflow.candidate_revision,
            "merged_revision": workflow.merged_revision,
            "tasks": [cls._task_dict(task) for task in workflow.tasks],
            "created_at": workflow.created_at.isoformat(),
            "updated_at": workflow.updated_at.isoformat(),
        }

    @staticmethod
    def _event_dict(event: AuditEvent) -> dict[str, Any]:
        return {
            "id": event.id,
            "workflow_id": event.workflow_id,
            "sequence": event.sequence,
            "event_type": event.event_type,
            "actor_id": event.actor_id,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "outcome": event.outcome,
            "payload": event.payload,
            "previous_hash": event.previous_hash,
            "event_hash": event.event_hash,
            "created_at": event.created_at.isoformat(),
        }

    @staticmethod
    def _routing_record_dict(record: RoutingRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "workflow_id": record.workflow_id,
            "task_id": record.task_id,
            "attempt_id": record.attempt_id,
            "policy_version": record.policy_version,
            "request": record.request_json,
            "ranked_candidates": record.ranked_candidates,
            "rejected_candidates": record.rejected_candidates,
            "selected_provider_id": record.selected_provider_id,
            "selected_provider_family": record.selected_provider_family,
            "selected_model_version": record.selected_model_version,
            "created_at": record.created_at.isoformat(),
        }

    @staticmethod
    def _ci_check_dict(check: CiCheckEvidence) -> dict[str, Any]:
        return {
            "id": check.id,
            "workflow_id": check.workflow_id,
            "source": check.source,
            "delivery_id": check.delivery_id,
            "repository": check.repository,
            "check_run_id": check.check_run_id,
            "check_name": check.check_name,
            "revision": check.revision,
            "status": check.status,
            "conclusion": check.conclusion,
            "details_url": check.details_url,
            "app_slug": check.app_slug,
            "payload_digest": check.payload_digest,
            "created_at": check.created_at.isoformat(),
        }

    def _require_github_app(self) -> GitHubAppClient:
        if self.github_app is None:
            raise IntegrationDisabledError("GitHub App integration is disabled")
        return self.github_app

    def _mark_pull_request_unknown(self, record_id: str, error_code: str) -> None:
        with self.session_factory() as session, session.begin():
            record = session.get(PullRequestProposalRecord, record_id, with_for_update=True)
            if record is None or record.status != "RUNNING":
                return
            record.status = "UNKNOWN"
            record.error_code = error_code[:64]
            record.completed_at = datetime.now(UTC)
            append_audit_event(
                session,
                workflow_id=record.workflow_id,
                event_type="git.pull_request_outcome_unknown",
                actor_id="orchestrator",
                resource_type="pull_request_proposal",
                resource_id=record.id,
                outcome="UNKNOWN",
                payload={"revision": record.revision, "error_code": record.error_code},
            )

    def _complete_pull_request(
        self,
        record_id: str,
        proposal: PullRequestProposal,
        *,
        reconciled: bool = False,
    ) -> dict[str, Any]:
        stale = False
        with self.session_factory() as session, session.begin():
            record = session.get(PullRequestProposalRecord, record_id, with_for_update=True)
            if record is None:
                raise NotFoundError("pull-request proposal was not found")
            workflow = self._get_workflow(session, record.workflow_id, lock=True)
            if workflow.candidate_revision != record.revision:
                record.status = "STALE"
                record.pull_number = proposal.number
                record.pull_url = proposal.url
                record.error_code = "CandidateRevisionChanged"
                record.completed_at = datetime.now(UTC)
                stale = True
            else:
                record.status = "SUCCEEDED"
                record.pull_number = proposal.number
                record.pull_url = proposal.url
                record.error_code = None
                record.completed_at = datetime.now(UTC)
            append_audit_event(
                session,
                workflow_id=record.workflow_id,
                event_type=(
                    "git.pull_request_reconciled" if reconciled else "git.pull_request_created"
                ),
                actor_id="orchestrator",
                resource_type="pull_request_proposal",
                resource_id=record.id,
                outcome="STALE" if stale else "SUCCEEDED",
                payload={
                    "revision": record.revision,
                    "pull_number": proposal.number,
                    "draft": proposal.draft,
                    "reconciled": reconciled,
                },
            )
            result = self._pull_request_record_dict(record)
        if stale:
            raise ConflictError("candidate revision changed during pull-request creation")
        return result

    def _record_merge_readiness(
        self,
        assessment_id: str,
        result: MergeReadiness,
    ) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            record = session.get(
                MergeReadinessAssessmentRecord, assessment_id, with_for_update=True
            )
            if record is None:
                raise NotFoundError("readiness assessment was not found")
            workflow_id = record.workflow_id
            workflow = self._get_workflow(session, workflow_id, lock=True)
            if workflow.candidate_revision != result.revision:
                raise ConflictError("candidate revision changed during readiness assessment")
            record.status = "SUCCEEDED"
            record.ready = result.ready
            record.reasons = list(result.reasons)
            record.checks = result.checks
            record.policy_version = result.policy_version
            record.completed_at = datetime.now(UTC)
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="git.merge_readiness_assessed",
                actor_id=record.actor_id,
                resource_type="merge_readiness",
                resource_id=record.id,
                outcome="SUCCEEDED" if result.ready else "BLOCKED",
                payload={
                    "revision": result.revision,
                    "ready": result.ready,
                    "reasons": list(result.reasons),
                    "policy_version": result.policy_version,
                },
            )
            return self._merge_readiness_dict(record)

    def _mark_readiness_failed(self, assessment_id: str, error_code: str) -> None:
        with self.session_factory() as session, session.begin():
            record = session.get(
                MergeReadinessAssessmentRecord, assessment_id, with_for_update=True
            )
            if record is None or record.status != "RUNNING":
                return
            record.status = "FAILED"
            record.error_code = error_code[:64]
            record.completed_at = datetime.now(UTC)
            append_audit_event(
                session,
                workflow_id=record.workflow_id,
                event_type="git.merge_readiness_failed",
                actor_id="orchestrator",
                resource_type="merge_readiness",
                resource_id=record.id,
                outcome="FAILED",
                payload={"revision": record.revision, "error_code": record.error_code},
            )

    def _complete_merge_confirmation(
        self, record_id: str, result: MergeConfirmation
    ) -> dict[str, Any]:
        with self.session_factory() as session, session.begin():
            record = session.get(MergeConfirmationRecord, record_id, with_for_update=True)
            if record is None or record.status != "RUNNING":
                raise ConflictError("merge confirmation is not pending")
            workflow = self._get_workflow(session, record.workflow_id, lock=True)
            proposal = session.get(PullRequestProposalRecord, record.proposal_id)
            if proposal is None or proposal.pull_number is None:
                raise ConflictError("pull-request proposal disappeared")
            if (
                WorkflowState(workflow.state) != WorkflowState.APPROVED
                or workflow.candidate_revision != record.revision
                or result.repository != proposal.repository
                or result.pull_number != proposal.pull_number
                or result.head_revision != record.revision
                or result.base_branch != proposal.base_branch
            ):
                raise ConflictError("merge confirmation no longer matches the approved workflow")
            record.status = "SUCCEEDED"
            record.merge_commit_revision = result.merge_commit_revision
            record.completed_at = datetime.now(UTC)
            workflow.merged_revision = result.merge_commit_revision
            self._transition(
                session,
                workflow,
                WorkflowState.MERGED,
                actor_id=record.actor_id,
                reason="GitHub independently confirmed the protected pull-request merge",
                extra={
                    "proposal_id": record.proposal_id,
                    "assessment_id": record.assessment_id,
                    "pull_number": result.pull_number,
                    "revision": record.revision,
                    "merge_commit_revision": result.merge_commit_revision,
                },
            )
            append_audit_event(
                session,
                workflow_id=record.workflow_id,
                event_type="git.merge_confirmed",
                actor_id=record.actor_id,
                resource_type="merge_confirmation",
                resource_id=record.id,
                outcome="SUCCEEDED",
                payload={
                    "revision": record.revision,
                    "merge_commit_revision": result.merge_commit_revision,
                },
            )
            return self._merge_confirmation_dict(record)

    def _mark_merge_confirmation_failed(self, record_id: str, error_code: str) -> None:
        with self.session_factory() as session, session.begin():
            record = session.get(MergeConfirmationRecord, record_id, with_for_update=True)
            if record is None or record.status != "RUNNING":
                return
            record.status = "FAILED"
            record.error_code = error_code[:64]
            record.completed_at = datetime.now(UTC)
            append_audit_event(
                session,
                workflow_id=record.workflow_id,
                event_type="git.merge_confirmation_failed",
                actor_id=record.actor_id,
                resource_type="merge_confirmation",
                resource_id=record.id,
                outcome="FAILED",
                payload={"revision": record.revision, "error_code": record.error_code},
            )

    def _fail_deployment_dry_run(self, attempt_id: str, error_code: str) -> None:
        with self.session_factory() as session, session.begin():
            attempt = session.get(DeploymentAttemptRecord, attempt_id, with_for_update=True)
            if attempt is None or attempt.status != DeploymentAttemptStatus.RUNNING.value:
                return
            attempt.status = DeploymentAttemptStatus.FAILED.value
            attempt.error_code = error_code[:64]
            attempt.completed_at = datetime.now(UTC)
            attempt.result_json = {
                "credential_requested": False,
                "external_target_contacted": False,
                "error": "dry-run adapter failed before any target change",
            }
            append_audit_event(
                session,
                workflow_id=attempt.workflow_id,
                event_type="deployment.dry_run_failed",
                actor_id="orchestrator",
                resource_type="deployment_attempt",
                resource_id=attempt.id,
                outcome="FAILED",
                payload={
                    "error_code": attempt.error_code,
                    "credential_requested": False,
                    "external_target_contacted": False,
                },
            )

    def _fail_local_deployment(
        self,
        attempt_id: str,
        status: DeploymentAttemptStatus,
        error_code: str,
        *,
        target_contacted: bool,
    ) -> None:
        if status not in {
            DeploymentAttemptStatus.FAILED,
            DeploymentAttemptStatus.UNKNOWN,
            DeploymentAttemptStatus.ROLLBACK_REQUIRED,
        }:
            raise ValueError("invalid local deployment failure status")
        with self.session_factory() as session, session.begin():
            attempt = session.get(DeploymentAttemptRecord, attempt_id, with_for_update=True)
            if attempt is None or attempt.status != DeploymentAttemptStatus.RUNNING.value:
                return
            attempt.status = status.value
            attempt.error_code = error_code[:64]
            attempt.completed_at = datetime.now(UTC)
            attempt.result_json = {
                "credential_requested": True,
                "error": "local deployment did not produce verified success",
                "target_contacted": target_contacted,
            }
            if status is DeploymentAttemptStatus.ROLLBACK_REQUIRED:
                workflow = self._get_workflow(session, attempt.workflow_id, lock=True)
                self._transition(
                    session,
                    workflow,
                    WorkflowState.ROLLBACK_REQUIRED,
                    actor_id="orchestrator",
                    reason="local deployment changed but verification was not established",
                    extra={"deployment_attempt_id": attempt.id},
                )
            append_audit_event(
                session,
                workflow_id=attempt.workflow_id,
                event_type="deployment.local_failed",
                actor_id="orchestrator",
                resource_type="deployment_attempt",
                resource_id=attempt.id,
                outcome=status.value,
                payload={
                    "error_code": attempt.error_code,
                    "target_contacted": target_contacted,
                },
            )

    def _fail_local_rollback(
        self,
        rollback_id: str,
        status: RollbackStatus,
        error_code: str,
        *,
        target_contacted: bool,
        recovery_duration_ms: int,
    ) -> None:
        if status not in {RollbackStatus.FAILED, RollbackStatus.UNKNOWN}:
            raise ValueError("invalid local rollback failure status")
        with self.session_factory() as session, session.begin():
            rollback = session.get(DeploymentRollbackRecord, rollback_id, with_for_update=True)
            if rollback is None or rollback.status != RollbackStatus.RUNNING.value:
                return
            rollback.status = status.value
            rollback.error_code = error_code[:64]
            rollback.completed_at = datetime.now(UTC)
            rollback.recovery_duration_ms = max(recovery_duration_ms, 0)
            rollback.evidence = {
                "credential_requested": True,
                "error": "local rollback did not produce verified recovery",
                "target_contacted": target_contacted,
            }
            append_audit_event(
                session,
                workflow_id=rollback.workflow_id,
                event_type="deployment.rollback_failed",
                actor_id="orchestrator",
                resource_type="deployment_rollback",
                resource_id=rollback.id,
                outcome=status.value,
                payload={
                    "error_code": rollback.error_code,
                    "recovery_duration_ms": rollback.recovery_duration_ms,
                    "target_contacted": target_contacted,
                },
            )

    @staticmethod
    def _credential_handle_dict(handle: WorkloadCredentialHandle) -> dict[str, Any]:
        return {
            "reference": handle.reference,
            "broker_id": handle.broker_id,
            "environment_id": handle.environment_id,
            "plan_digest": handle.plan_digest,
            "operation": handle.operation.value,
            "audience": handle.audience,
            "subject": handle.subject,
            "resource_scope": list(handle.resource_scope),
            "issued_at": handle.issued_at.isoformat(),
            "expires_at": handle.expires_at.isoformat(),
            "simulated": handle.simulated,
        }

    @staticmethod
    def _deployment_plan_value(record: DeploymentPlanRecord) -> DeploymentPlan:
        return DeploymentPlan(
            plan_id=record.id,
            workflow_id=record.workflow_id,
            environment_id=record.environment_id,
            revision=record.revision,
            artifact_digests=tuple(record.artifact_digests),
            operations=tuple(record.operations),
            verification_probes=tuple(record.verification_probes),
            rollback_reference=record.rollback_reference,
            policy_version=record.policy_version,
            digest=record.digest,
        )

    @staticmethod
    def _require_active_evaluation_workflow(workflow: Workflow) -> None:
        if WorkflowState(workflow.state) in {
            WorkflowState.APPROVED,
            WorkflowState.MERGED,
            WorkflowState.DEPLOYED,
            WorkflowState.FAILED,
            WorkflowState.REJECTED,
            WorkflowState.CANCELLED,
            WorkflowState.ROLLBACK_REQUIRED,
            WorkflowState.ROLLED_BACK,
        }:
            raise ConflictError("evaluation workflow is terminal")

    @staticmethod
    def _evaluation_policy(record: EvaluationCampaignRecord) -> EvaluationPolicy:
        return EvaluationPolicy(
            required_checks=frozenset(record.required_checks),
            risk=RiskLevel[record.risk],
            max_candidates=record.max_candidates,
            max_prompt_variants=record.max_prompt_variants,
            max_iterations=record.max_iterations,
            max_total_cost_microunits=record.max_total_cost_microunits,
            minimum_independent_reviews=record.minimum_independent_reviews,
        )

    @staticmethod
    def _evaluation_campaign_dict(record: EvaluationCampaignRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "workflow_id": record.workflow_id,
            "prompt_contract_version": record.prompt_contract_version,
            "work_capability": record.work_capability,
            "required_checks": record.required_checks,
            "risk": record.risk,
            "max_candidates": record.max_candidates,
            "max_prompt_variants": record.max_prompt_variants,
            "max_iterations": record.max_iterations,
            "max_total_cost_microunits": record.max_total_cost_microunits,
            "minimum_independent_reviews": record.minimum_independent_reviews,
            "policy_version": record.policy_version,
            "status": record.status,
            "current_iteration": record.current_iteration,
            "total_cost_microunits": record.total_cost_microunits,
            "winner_candidate_id": record.winner_candidate_id,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
        }

    @staticmethod
    def _evaluation_batch_dict(record: EvaluationBatchRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "campaign_id": record.campaign_id,
            "workflow_id": record.workflow_id,
            "repair_id": record.repair_id,
            "iteration": record.iteration,
            "status": record.status,
            "winner_candidate_id": record.winner_candidate_id,
            "ranked_candidates": record.ranked_candidates,
            "rejected_candidates": record.rejected_candidates,
            "total_cost_microunits": record.total_cost_microunits,
            "policy_version": record.policy_version,
            "routing_snapshot": record.routing_snapshot,
            "created_at": record.created_at.isoformat(),
        }

    @staticmethod
    def _evaluation_candidate_dict(record: EvaluationCandidateRecord) -> dict[str, Any]:
        return {
            "candidate_id": record.candidate_id,
            "provider_id": record.provider_id,
            "provider_family": record.provider_family,
            "model_version": record.model_version,
            "profile_version": record.profile_version,
            "prompt_variant_id": record.prompt_variant_id,
            "prompt_contract_version": record.prompt_contract_version,
            "iteration": record.iteration,
            "succeeded": record.succeeded,
            "output_digest": record.output_digest,
            "latency_ms": record.latency_ms,
            "cost_microunits": record.cost_microunits,
            "routing_score": record.routing_score,
            "checks": record.checks,
            "reviews": record.reviews,
            "rejection_reasons": record.rejection_reasons,
            "rank": record.rank,
            "created_at": record.created_at.isoformat(),
        }

    def _evaluation_execution_dict(
        self, session: Session, record: EvaluationExecutionRecord
    ) -> dict[str, Any]:
        runs = session.scalars(
            select(EvaluationProviderRunRecord)
            .where(EvaluationProviderRunRecord.execution_id == record.id)
            .order_by(
                EvaluationProviderRunRecord.provider_id,
                EvaluationProviderRunRecord.prompt_variant_id,
            )
        ).all()
        return {
            "id": record.id,
            "campaign_id": record.campaign_id,
            "workflow_id": record.workflow_id,
            "task_id": record.task_id,
            "repair_id": record.repair_id,
            "iteration": record.iteration,
            "workflow_version": record.workflow_version,
            "candidate_revision": record.candidate_revision,
            "prompt_variants": record.prompt_variants,
            "routing_snapshot": record.routing_snapshot,
            "status": record.status,
            "error_code": record.error_code,
            "runs": [self._evaluation_provider_run_dict(run) for run in runs],
            "created_at": record.created_at.isoformat(),
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        }

    @staticmethod
    def _evaluation_provider_run_dict(record: EvaluationProviderRunRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "provider_id": record.provider_id,
            "provider_family": record.provider_family,
            "model_version": record.model_version,
            "profile_version": record.profile_version,
            "prompt_variant_id": record.prompt_variant_id,
            "prompt_contract_version": record.prompt_contract_version,
            "request_digest": record.request_digest,
            "status": record.status,
            "output_digest": record.output_digest,
            "output": record.output_json,
            "usage": record.usage_json,
            "latency_ms": record.latency_ms,
            "error_code": record.error_code,
            "started_at": record.started_at.isoformat() if record.started_at else None,
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        }

    def _evaluation_assessment_dict(
        self, session: Session, record: EvaluationAssessmentRecord
    ) -> dict[str, Any]:
        artifacts = session.scalars(
            select(EvaluationArtifactRecord)
            .where(EvaluationArtifactRecord.assessment_id == record.id)
            .order_by(EvaluationArtifactRecord.provider_run_id)
        ).all()
        checks = session.scalars(
            select(EvaluationCheckRecord)
            .where(EvaluationCheckRecord.assessment_id == record.id)
            .order_by(EvaluationCheckRecord.artifact_id, EvaluationCheckRecord.check_name)
        ).all()
        checks_by_artifact: dict[str, list[dict[str, Any]]] = {}
        for check in checks:
            checks_by_artifact.setdefault(check.artifact_id, []).append(
                self._evaluation_check_dict(check)
            )
        reviews = session.scalars(
            select(EvaluationReviewRunRecord)
            .where(EvaluationReviewRunRecord.assessment_id == record.id)
            .order_by(
                EvaluationReviewRunRecord.artifact_id,
                EvaluationReviewRunRecord.reviewer_provider_id,
            )
        ).all()
        reviews_by_artifact: dict[str, list[dict[str, Any]]] = {}
        for review in reviews:
            reviews_by_artifact.setdefault(review.artifact_id, []).append(
                self._evaluation_review_dict(review)
            )
        result: dict[str, Any] = {
            "id": record.id,
            "execution_id": record.execution_id,
            "campaign_id": record.campaign_id,
            "workflow_id": record.workflow_id,
            "status": record.status,
            "batch_id": record.batch_id,
            "error_code": record.error_code,
            "artifacts": [
                {
                    "id": artifact.id,
                    "provider_run_id": artifact.provider_run_id,
                    "artifact_type": artifact.artifact_type,
                    "digest": artifact.digest,
                    "workflow_version": artifact.workflow_version,
                    "candidate_revision": artifact.candidate_revision,
                    "content": artifact.content_json,
                    "checks": checks_by_artifact.get(artifact.id, []),
                    "reviews": reviews_by_artifact.get(artifact.id, []),
                    "created_at": artifact.created_at.isoformat(),
                }
                for artifact in artifacts
            ],
            "created_at": record.created_at.isoformat(),
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        }
        if record.batch_id is not None:
            batch = session.get(EvaluationBatchRecord, record.batch_id)
            if batch is not None:
                result["decision"] = self._evaluation_batch_dict(batch)
        result["recoveries"] = [
            self._evaluation_recovery_dict(recovery)
            for recovery in session.scalars(
                select(EvaluationRecoveryRecord)
                .where(EvaluationRecoveryRecord.assessment_id == record.id)
                .order_by(EvaluationRecoveryRecord.created_at, EvaluationRecoveryRecord.id)
            ).all()
        ]
        promotion = session.scalar(
            select(EvaluationPromotionRecord).where(
                EvaluationPromotionRecord.assessment_id == record.id
            )
        )
        result["promotion"] = (
            self._evaluation_promotion_dict(promotion) if promotion is not None else None
        )
        return result

    @staticmethod
    def _evaluation_check_dict(record: EvaluationCheckRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "name": record.check_name,
            "validator_version": record.validator_version,
            "status": record.status,
            "passed": record.passed,
            "evidence_digest": record.evidence_digest,
            "validated_output_digest": record.validated_output_digest,
            "details": record.details_json,
            "created_at": record.created_at.isoformat(),
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        }

    @staticmethod
    def _evaluation_review_dict(record: EvaluationReviewRunRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "artifact_id": record.artifact_id,
            "candidate_provider_run_id": record.candidate_provider_run_id,
            "reviewer_provider_id": record.reviewer_provider_id,
            "reviewer_provider_family": record.reviewer_provider_family,
            "reviewer_model_version": record.reviewer_model_version,
            "reviewer_profile_version": record.reviewer_profile_version,
            "status": record.status,
            "passed": record.passed,
            "evidence_digest": record.evidence_digest,
            "reviewed_output_digest": record.reviewed_output_digest,
            "latency_ms": record.latency_ms,
            "error_code": record.error_code,
            "created_at": record.created_at.isoformat(),
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        }

    @staticmethod
    def _evaluation_reconciliation_dict(
        record: EvaluationReconciliationRecord,
    ) -> dict[str, Any]:
        return {
            "id": record.id,
            "workflow_id": record.workflow_id,
            "campaign_id": record.campaign_id,
            "target_type": record.target_type,
            "target_id": record.target_id,
            "decision": record.decision,
            "rationale": record.rationale,
            "affected_run_ids": record.affected_run_ids,
            "created_at": record.created_at.isoformat(),
        }

    @staticmethod
    def _evaluation_repair_dict(record: EvaluationRepairRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "workflow_id": record.workflow_id,
            "campaign_id": record.campaign_id,
            "source_batch_id": record.source_batch_id,
            "source_iteration": record.source_iteration,
            "target_iteration": record.target_iteration,
            "workflow_version": record.workflow_version,
            "candidate_revision": record.candidate_revision,
            "prompt_variants": record.prompt_variants,
            "failure_snapshot": record.failure_snapshot,
            "rationale": record.rationale,
            "created_at": record.created_at.isoformat(),
        }

    @staticmethod
    def _evaluation_recovery_dict(record: EvaluationRecoveryRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "workflow_id": record.workflow_id,
            "campaign_id": record.campaign_id,
            "assessment_id": record.assessment_id,
            "prior_status": record.prior_status,
            "outcome_status": record.outcome_status,
            "decision": record.decision,
            "rationale": record.rationale,
            "affected_record_ids": record.affected_record_ids,
            "created_at": record.created_at.isoformat(),
        }

    @staticmethod
    def _evaluation_promotion_dict(record: EvaluationPromotionRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "workflow_id": record.workflow_id,
            "campaign_id": record.campaign_id,
            "assessment_id": record.assessment_id,
            "batch_id": record.batch_id,
            "candidate_id": record.candidate_id,
            "artifact_id": record.artifact_id,
            "artifact_digest": record.artifact_digest,
            "workflow_version": record.workflow_version,
            "candidate_revision": record.candidate_revision,
            "rationale": record.rationale,
            "created_at": record.created_at.isoformat(),
        }

    @staticmethod
    def _deployment_environment_dict(record: DeploymentEnvironment) -> dict[str, Any]:
        return {
            "id": record.id,
            "name": record.name,
            "classification": record.classification,
            "provider": record.provider,
            "account_scope": record.account_scope,
            "region": record.region,
            "resource_scope": record.resource_scope,
            "adapter_id": record.adapter_id,
            "policy_version": record.policy_version,
            "repository": record.repository,
            "base_branch": record.base_branch,
            "required_checks": record.required_checks,
            "required_attestations": record.required_attestations,
            "verification_policy": record.verification_policy,
            "rollback_policy": record.rollback_policy,
            "config_digest": record.config_digest,
            "active": record.active,
            "created_by": record.created_by,
            "created_at": record.created_at.isoformat(),
        }

    @staticmethod
    def _deployment_plan_dict(record: DeploymentPlanRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "workflow_id": record.workflow_id,
            "environment_id": record.environment_id,
            "revision": record.revision,
            "artifact_digests": record.artifact_digests,
            "operations": record.operations,
            "declared_impact": record.declared_impact,
            "verification_probes": record.verification_probes,
            "rollback_reference": record.rollback_reference,
            "policy_version": record.policy_version,
            "digest": record.digest,
            "created_at": record.created_at.isoformat(),
        }

    @staticmethod
    def _deployment_attempt_dict(record: DeploymentAttemptRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "workflow_id": record.workflow_id,
            "plan_id": record.plan_id,
            "approval_id": record.approval_id,
            "adapter_id": record.adapter_id,
            "status": record.status,
            "operation_reference": record.operation_reference,
            "simulated": record.simulated,
            "result": record.result_json,
            "error_code": record.error_code,
            "started_at": record.started_at.isoformat(),
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        }

    @staticmethod
    def _deployment_rollback_dict(record: DeploymentRollbackRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "workflow_id": record.workflow_id,
            "attempt_id": record.attempt_id,
            "approval_id": record.approval_id,
            "actor_id": record.actor_id,
            "rollback_reference": record.rollback_reference,
            "decision": record.decision,
            "rationale": record.rationale,
            "status": record.status,
            "operation_reference": record.operation_reference,
            "evidence": record.evidence,
            "error_code": record.error_code,
            "recovery_duration_ms": record.recovery_duration_ms,
            "created_at": record.created_at.isoformat(),
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        }

    @staticmethod
    def _is_sha256_digest(value: str) -> bool:
        suffix = value.removeprefix("sha256:")
        return (
            value.startswith("sha256:")
            and len(suffix) == 64
            and suffix == suffix.lower()
            and set(suffix) <= set("0123456789abcdef")
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _pull_request_record_dict(record: PullRequestProposalRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "workflow_id": record.workflow_id,
            "repository": record.repository,
            "head_branch": record.head_branch,
            "base_branch": record.base_branch,
            "revision": record.revision,
            "status": record.status,
            "pull_number": record.pull_number,
            "pull_url": record.pull_url,
            "error_code": record.error_code,
            "created_at": record.created_at.isoformat(),
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        }

    @staticmethod
    def _merge_readiness_dict(record: MergeReadinessAssessmentRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "workflow_id": record.workflow_id,
            "proposal_id": record.proposal_id,
            "revision": record.revision,
            "status": record.status,
            "ready": record.ready,
            "reasons": record.reasons,
            "checks": record.checks,
            "policy_version": record.policy_version,
            "error_code": record.error_code,
            "created_at": record.created_at.isoformat(),
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        }

    @staticmethod
    def _merge_confirmation_dict(record: MergeConfirmationRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "workflow_id": record.workflow_id,
            "proposal_id": record.proposal_id,
            "assessment_id": record.assessment_id,
            "revision": record.revision,
            "status": record.status,
            "merge_commit_revision": record.merge_commit_revision,
            "error_code": record.error_code,
            "created_at": record.created_at.isoformat(),
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        }

    @staticmethod
    def _approval_dict(approval: Approval) -> dict[str, Any]:
        return {
            "id": approval.id,
            "workflow_id": approval.workflow_id,
            "action": approval.action,
            "target": approval.target,
            "revision": approval.revision,
            "policy_version": approval.policy_version,
            "environment_id": approval.environment_id,
            "plan_digest": approval.plan_digest,
            "deployment_attempt_id": approval.deployment_attempt_id,
            "approver_id": approval.approver_id,
            "decision": approval.decision,
            "rationale": approval.rationale,
            "expires_at": approval.expires_at.isoformat(),
            "consumed_at": approval.consumed_at.isoformat() if approval.consumed_at else None,
        }
