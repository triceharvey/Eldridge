from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, cast

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, sessionmaker

from control_plane.audit import append_audit_event
from control_plane.credentials import (
    CredentialBroker,
    CredentialSessionBroker,
    CredentialSessionBrokerFactory,
    DenyCredentialBroker,
)
from control_plane.deployment import (
    CredentialedDeploymentAdapter,
    DeploymentAdapter,
    DryRunDeploymentAdapter,
    EnvironmentClassification,
)
from control_plane.deployment_recovery import DeploymentRecoveryService
from control_plane.domain import (
    TASK_CAPABILITY,
    TASK_ROLE,
    ApprovalAction,
    ApprovalDecision,
    AuthorizationError,
    Capability,
    ConflictError,
    DispositionDecision,
    ExecutionResult,
    NotFoundError,
    ProviderResult,
    ReconciliationDecision,
    TaskKind,
    TaskStatus,
    WorkflowState,
)
from control_plane.evaluation import (
    CandidateEvidence,
    EvaluationReconciliationDecision,
    EvaluationRecoveryDecision,
    MultiModelEvaluator,
    PromptVariant,
)
from control_plane.evaluation_campaigns import EvaluationCampaignService
from control_plane.evaluation_lifecycle import EvaluationLifecycleService
from control_plane.evaluation_pipeline import EvaluationPipelineService
from control_plane.evaluation_validation import (
    EvaluationValidator,
    default_evaluation_validators,
)
from control_plane.executors import FakeExecutor, TaskExecutor
from control_plane.git_pull_requests import GitPullRequestService
from control_plane.github_app import (
    GitHubAppClient,
)
from control_plane.learning import ProviderEvidenceStore
from control_plane.persistence import (
    Approval,
    AuditEvent,
    CapabilityGrant,
    CiCheckEvidence,
    ProviderObservation,
    RoutingRecord,
    Task,
    TaskAttempt,
    Workflow,
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
    WorkCapability,
    mock_profiles,
)
from control_plane.state_machine import assert_transition_allowed
from control_plane.task_strategy import (
    ComplexityTier,
    InspectionSignal,
    TaskStrategyPlanner,
)
from control_plane.windsurf_integration import WindsurfIntegrationService
from control_plane.workflow_tasks import (
    AGENT_FOR_ROLE,
    TASK_OBJECTIVES,
    TASK_WORK_CAPABILITY,
    PreparedExecution,
    WorkflowTaskService,
)
from control_plane.workspaces import RepositoryRegistry


@dataclass(frozen=True)
class ProviderBinding:
    profile: ProviderProfile
    provider: ModelProvider


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
        self.evaluation_campaigns = EvaluationCampaignService(
            self.session_factory,
            self.policy,
            evaluator=self.evaluator,
            provider_bindings=self.provider_bindings,
            router=self.router,
            evidence_store=self.evidence_store,
            allowed_egress=self.allowed_egress,
            high_risk_min_evidence_samples=self.high_risk_min_evidence_samples,
            provider_policy_version=self.provider_policy_version,
            routing_objective=self.routing_objective,
        )
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
        self.workflow_tasks = WorkflowTaskService(
            self.session_factory,
            self.policy,
            executor=self.executor,
            lease_seconds=self.lease_seconds,
            heartbeat_interval_seconds=self.heartbeat_interval_seconds,
            provider_bindings=self.provider_bindings,
            router=self.router,
            evidence_store=self.evidence_store,
            strategy_planner=self.strategy_planner,
            allowed_egress=self.allowed_egress,
            high_risk_min_evidence_samples=self.high_risk_min_evidence_samples,
            provider_policy_version=self.provider_policy_version,
            routing_objective=self.routing_objective,
        )
        self.git_pull_requests = GitPullRequestService(
            self.session_factory,
            self.policy,
            github_app=self.github_app,
            transition=self.workflow_tasks._transition,
        )
        self.deployment_recovery = DeploymentRecoveryService(
            self.session_factory,
            self.policy,
            deployment_adapters=self.deployment_adapters,
            credential_broker=self.credential_broker,
            credential_broker_factory=self.credential_broker_factory,
            enable_local_deployment=self.enable_local_deployment,
            transition=self.workflow_tasks._transition,
        )
        self.windsurf_integration = WindsurfIntegrationService(
            self.session_factory,
            self.policy,
            lease_seconds=self.lease_seconds,
            repository_registry=self.repository_registry,
            workflow_tasks=self.workflow_tasks,
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
        return self.workflow_tasks.create_workflow(
            requester_id=requester_id,
            title=title,
            description=description,
            idempotency_key=idempotency_key,
            complexity=complexity,
            risk=risk,
            data_classification=data_classification,
            repository_scope=repository_scope,
            inspection_signals=inspection_signals,
        )

    def get_workflow(self, workflow_id: str, *, principal_id: str) -> dict[str, Any]:
        return self.workflow_tasks.get_workflow(workflow_id, principal_id=principal_id)

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
        return self.evaluation_campaigns.create_evaluation_campaign(
            workflow_id=workflow_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            prompt_contract_version=prompt_contract_version,
            work_capability=work_capability,
            required_checks=required_checks,
            max_candidates=max_candidates,
            max_prompt_variants=max_prompt_variants,
            max_iterations=max_iterations,
            max_total_cost_microunits=max_total_cost_microunits,
            minimum_independent_reviews=minimum_independent_reviews,
        )

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
        return self.evaluation_campaigns.submit_evaluation_evidence(
            workflow_id=workflow_id,
            campaign_id=campaign_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            candidates=candidates,
            repair_id=repair_id,
        )

    def get_evaluation_campaign(
        self, workflow_id: str, campaign_id: str, *, principal_id: str
    ) -> dict[str, Any]:
        return self.evaluation_campaigns.get_evaluation_campaign(
            workflow_id, campaign_id, principal_id=principal_id
        )

    def list_evaluation_campaigns(
        self, workflow_id: str, *, principal_id: str
    ) -> list[dict[str, Any]]:
        return self.evaluation_campaigns.list_evaluation_campaigns(
            workflow_id, principal_id=principal_id
        )

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
        return self.git_pull_requests.list_ci_check_evidence(workflow_id, principal_id=principal_id)

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
        return self.git_pull_requests.ingest_ci_check_evidence(
            actor_id=actor_id,
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
        return self.git_pull_requests.propose_pull_request(
            workflow_id=workflow_id,
            actor_id=actor_id,
            head_branch=head_branch,
            title=title,
            body=body,
            idempotency_key=idempotency_key,
        )

    def reconcile_pull_request(
        self, *, workflow_id: str, proposal_id: str, actor_id: str
    ) -> dict[str, Any]:
        return self.git_pull_requests.reconcile_pull_request(
            workflow_id=workflow_id,
            proposal_id=proposal_id,
            actor_id=actor_id,
        )

    def assess_merge_readiness(
        self,
        *,
        workflow_id: str,
        proposal_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self.git_pull_requests.assess_merge_readiness(
            workflow_id=workflow_id,
            proposal_id=proposal_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )

    def list_pull_request_proposals(
        self, workflow_id: str, *, principal_id: str
    ) -> list[dict[str, Any]]:
        return self.git_pull_requests.list_pull_request_proposals(
            workflow_id, principal_id=principal_id
        )

    def confirm_pull_request_merged(
        self,
        *,
        workflow_id: str,
        proposal_id: str,
        assessment_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self.git_pull_requests.confirm_pull_request_merged(
            workflow_id=workflow_id,
            proposal_id=proposal_id,
            assessment_id=assessment_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )

    def list_merge_confirmations(
        self, workflow_id: str, *, principal_id: str
    ) -> list[dict[str, Any]]:
        return self.git_pull_requests.list_merge_confirmations(
            workflow_id, principal_id=principal_id
        )

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
        return self.deployment_recovery.register_deployment_environment(
            actor_id=actor_id,
            environment_id=environment_id,
            name=name,
            classification=classification,
            repository=repository,
            base_branch=base_branch,
            resource_scope=resource_scope,
            required_checks=required_checks,
            required_attestations=required_attestations,
            verification_policy=verification_policy,
            rollback_policy=rollback_policy,
            policy_version=policy_version,
            provider=provider,
            account_scope=account_scope,
            region=region,
            adapter_id=adapter_id,
        )

    def list_deployment_environments(self, *, principal_id: str) -> list[dict[str, Any]]:
        return self.deployment_recovery.list_deployment_environments(principal_id=principal_id)

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
        return self.deployment_recovery.create_deployment_plan(
            workflow_id=workflow_id,
            actor_id=actor_id,
            environment_id=environment_id,
            artifact_digests=artifact_digests,
            operations=operations,
            declared_impact=declared_impact,
            verification_probes=verification_probes,
            rollback_reference=rollback_reference,
            idempotency_key=idempotency_key,
        )

    def list_deployment_plans(self, workflow_id: str, *, principal_id: str) -> list[dict[str, Any]]:
        return self.deployment_recovery.list_deployment_plans(
            workflow_id, principal_id=principal_id
        )

    def execute_deployment_dry_run(
        self,
        *,
        workflow_id: str,
        plan_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self.deployment_recovery.execute_deployment_dry_run(
            workflow_id=workflow_id,
            plan_id=plan_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )

    def execute_local_deployment(
        self,
        *,
        workflow_id: str,
        plan_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self.deployment_recovery.execute_local_deployment(
            workflow_id=workflow_id,
            plan_id=plan_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )

    def list_deployment_attempts(
        self, workflow_id: str, *, principal_id: str
    ) -> list[dict[str, Any]]:
        return self.deployment_recovery.list_deployment_attempts(
            workflow_id, principal_id=principal_id
        )

    def execute_local_rollback(
        self,
        *,
        workflow_id: str,
        attempt_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self.deployment_recovery.execute_local_rollback(
            workflow_id=workflow_id,
            attempt_id=attempt_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
        )

    def list_deployment_rollbacks(
        self, workflow_id: str, *, principal_id: str
    ) -> list[dict[str, Any]]:
        return self.deployment_recovery.list_deployment_rollbacks(
            workflow_id, principal_id=principal_id
        )

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
        return self.workflow_tasks.lease_next_task(worker_id=worker_id)

    def reclaim_expired_tasks(self, *, worker_id: str) -> int:
        return self.workflow_tasks.reclaim_expired_tasks(worker_id=worker_id)

    def claim_windsurf_task(self, *, task_id: str, principal_id: str) -> dict[str, Any]:
        return self.windsurf_integration.claim_windsurf_task(
            task_id=task_id, principal_id=principal_id
        )

    def heartbeat_windsurf_task(
        self, *, task_id: str, lease_token: str, principal_id: str
    ) -> dict[str, Any]:
        return self.windsurf_integration.heartbeat_windsurf_task(
            task_id=task_id,
            lease_token=lease_token,
            principal_id=principal_id,
        )

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
        return self.windsurf_integration.submit_windsurf_evidence(
            task_id=task_id,
            lease_token=lease_token,
            principal_id=principal_id,
            handoff_digest=handoff_digest,
            result_revision=result_revision,
            files_changed=files_changed,
            tests_passed=tests_passed,
            test_summary=test_summary,
            tool_activity_summary=tool_activity_summary,
        )

    def execute_leased_task(
        self, *, task_id: str, lease_token: str, worker_id: str
    ) -> dict[str, Any]:
        return self.workflow_tasks.execute_leased_task(
            task_id=task_id,
            lease_token=lease_token,
            worker_id=worker_id,
        )

    def _prepare_execution(
        self, task_id: str, lease_token: str, worker_id: str
    ) -> PreparedExecution | dict[str, Any]:
        return self.workflow_tasks._prepare_execution(task_id, lease_token, worker_id)

    def heartbeat_task(self, *, task_id: str, lease_token: str, worker_id: str) -> None:
        self.workflow_tasks.heartbeat_task(
            task_id=task_id,
            lease_token=lease_token,
            worker_id=worker_id,
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
        return self.workflow_tasks._finalize_execution_success(
            prepared,
            provider_result,
            execution,
            candidate_revision=candidate_revision,
            latency_ms=latency_ms,
        )

    def _finalize_execution_failure(
        self,
        prepared: PreparedExecution,
        exc: Exception,
        *,
        validation_passed: bool,
        latency_ms: int,
    ) -> dict[str, Any]:
        return self.workflow_tasks._finalize_execution_failure(
            prepared,
            exc,
            validation_passed=validation_passed,
            latency_ms=latency_ms,
        )

    def disposition_workflow(
        self,
        *,
        workflow_id: str,
        actor_id: str,
        decision: DispositionDecision,
        rationale: str,
    ) -> dict[str, Any]:
        return self.workflow_tasks.disposition_workflow(
            workflow_id=workflow_id,
            actor_id=actor_id,
            decision=decision,
            rationale=rationale,
        )

    def reconcile_execution(
        self,
        *,
        workflow_id: str,
        task_id: str,
        actor_id: str,
        decision: ReconciliationDecision,
        rationale: str,
    ) -> dict[str, Any]:
        return self.workflow_tasks.reconcile_execution(
            workflow_id=workflow_id,
            task_id=task_id,
            actor_id=actor_id,
            decision=decision,
            rationale=rationale,
        )

    def _route_attempt(
        self,
        session: Session,
        workflow: Workflow,
        task: Task,
        attempt: TaskAttempt,
    ) -> tuple[ProviderBinding, ProviderProfile] | None:
        routed = self.workflow_tasks._route_attempt(session, workflow, task, attempt)
        if routed is None:
            return None
        binding, profile = routed
        return cast(ProviderBinding, binding), profile

    def _provider_is_healthy(self, provider: ModelProvider) -> bool:
        return self.workflow_tasks._provider_is_healthy(provider)

    def _record_routing_block(
        self,
        session: Session,
        workflow: Workflow,
        task: Task,
        attempt: TaskAttempt,
    ) -> dict[str, Any]:
        return self.workflow_tasks._record_routing_block(session, workflow, task, attempt)

    def _record_provider_observation(
        self,
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
        self.workflow_tasks._record_provider_observation(
            session,
            workflow,
            task,
            attempt,
            profile,
            succeeded=succeeded,
            validation_passed=validation_passed,
            latency_ms=latency_ms,
            error_code=error_code,
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
        return self.workflow_tasks.approve(
            workflow_id=workflow_id,
            approver_id=approver_id,
            action=action,
            target=target,
            revision=revision,
            decision=decision,
            rationale=rationale,
            expires_in_minutes=expires_in_minutes,
            environment_id=environment_id,
            plan_digest=plan_digest,
            deployment_attempt_id=deployment_attempt_id,
        )

    def cancel_workflow(self, workflow_id: str, *, principal_id: str) -> dict[str, Any]:
        return self.workflow_tasks.cancel_workflow(workflow_id, principal_id=principal_id)

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
        return self.workflow_tasks._record_attempt_failure(
            session,
            workflow,
            task,
            attempt,
            exc,
            worker_id=worker_id,
        )

    def _validate_lease(self, task: Task, lease_token: str, worker_id: str) -> None:
        self.workflow_tasks._validate_lease(task, lease_token, worker_id)

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
