from __future__ import annotations

import json
from dataclasses import asdict, dataclass
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
    ApprovalAction,
    ApprovalDecision,
    AuthorizationError,
    Capability,
    ConflictError,
    DeploymentOutcomeUnknownError,
    DeploymentVerificationError,
    DispositionDecision,
    ExecutionResult,
    IntegrationDisabledError,
    InvalidTransitionError,
    NotFoundError,
    ProviderResult,
    ReconciliationDecision,
    TaskKind,
    TaskStatus,
    ValidationError,
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
from control_plane.integrations import WindsurfHandoff
from control_plane.learning import ProviderEvidenceStore
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
        return self.workflow_tasks.lease_next_task(worker_id=worker_id)

    def reclaim_expired_tasks(self, *, worker_id: str) -> int:
        return self.workflow_tasks.reclaim_expired_tasks(worker_id=worker_id)

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
