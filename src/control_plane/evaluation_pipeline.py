from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from time import monotonic
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from control_plane.audit import append_audit_event
from control_plane.domain import (
    TASK_CAPABILITY,
    TASK_ROLE,
    Capability,
    ConflictError,
    NotFoundError,
    ProviderRequest,
    TaskKind,
    ValidationError,
    WorkflowState,
)
from control_plane.evaluation import (
    CandidateEvidence,
    DeterministicCheck,
    EvaluationAssessmentStatus,
    EvaluationExecutionStatus,
    EvaluationReconciliationDecision,
    EvaluationStatus,
    IndependentReview,
    PromptVariant,
)
from control_plane.evaluation_read_models import (
    evaluation_assessment_dict,
    evaluation_execution_dict,
    evaluation_reconciliation_dict,
)
from control_plane.evaluation_validation import (
    EvaluationArtifact as TrustedEvaluationArtifact,
)
from control_plane.evaluation_validation import (
    EvaluationValidator,
    SecurityBoundaryValidator,
    ValidationOutcome,
)
from control_plane.learning import ProviderEvidenceStore
from control_plane.persistence import (
    EvaluationArtifactRecord,
    EvaluationAssessmentRecord,
    EvaluationCampaignRecord,
    EvaluationCandidateRecord,
    EvaluationCheckRecord,
    EvaluationExecutionRecord,
    EvaluationObservationRecord,
    EvaluationProviderRunRecord,
    EvaluationReconciliationRecord,
    EvaluationRepairRecord,
    EvaluationReviewRunRecord,
    Task,
    Workflow,
)
from control_plane.policy import PolicyEngine
from control_plane.providers import ModelProvider
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
)
from control_plane.validation import validate_provider_result


class EvaluationProviderBinding(Protocol):
    @property
    def profile(self) -> ProviderProfile: ...

    @property
    def provider(self) -> ModelProvider: ...


@dataclass(frozen=True)
class PreparedEvaluationRun:
    run_id: str
    execution_id: str
    binding: EvaluationProviderBinding
    profile: ProviderProfile
    request: ProviderRequest


@dataclass(frozen=True)
class EvaluationRunOutcome:
    run_id: str
    status: str
    output: dict[str, Any]
    output_digest: str | None
    usage: dict[str, int]
    latency_ms: int
    error_code: str | None


@dataclass(frozen=True)
class PreparedEvaluationCheck:
    check_id: str
    validator: EvaluationValidator
    artifact: TrustedEvaluationArtifact


@dataclass(frozen=True)
class EvaluationCheckOutcome:
    check_id: str
    passed: bool
    details: dict[str, Any]


class EvaluationPipelineService:
    """Committed provider execution and trusted assessment boundary."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        policy: PolicyEngine,
        *,
        provider_bindings: Mapping[str, EvaluationProviderBinding],
        router: CapabilityRouter,
        evidence_store: ProviderEvidenceStore,
        allowed_egress: frozenset[EgressBoundary],
        high_risk_min_evidence_samples: int,
        provider_policy_version: str,
        routing_objective: RoutingObjective,
        evaluation_validators: Mapping[str, EvaluationValidator],
        submit_evaluation_evidence: Callable[..., dict[str, Any]],
        resume_assessment: Callable[[str, str], dict[str, Any]],
    ) -> None:
        self.session_factory = session_factory
        self.policy = policy
        self.provider_bindings = provider_bindings
        self.router = router
        self.evidence_store = evidence_store
        self.allowed_egress = allowed_egress
        self.high_risk_min_evidence_samples = high_risk_min_evidence_samples
        self.provider_policy_version = provider_policy_version
        self.routing_objective = routing_objective
        self.evaluation_validators = evaluation_validators
        self._evaluation_execution_dict = evaluation_execution_dict
        self._evaluation_assessment_dict = evaluation_assessment_dict
        self._evaluation_reconciliation_dict = evaluation_reconciliation_dict
        self.submit_evaluation_evidence = submit_evaluation_evidence
        self.resume_assessment = resume_assessment

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
    def _provider_is_healthy(provider: ModelProvider) -> bool:
        try:
            return provider.health()
        except Exception:
            return False

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
        if not 8 <= len(idempotency_key.strip()) <= 128:
            raise ValidationError("evaluation execution idempotency key is invalid")
        if not prompt_variants:
            raise ValidationError("evaluation execution requires prompt variants")
        variant_ids = [variant.variant_id for variant in prompt_variants]
        if len(variant_ids) != len(set(variant_ids)):
            raise ValidationError("evaluation prompt variant IDs must be unique")
        variant_payload = [
            asdict(item) for item in sorted(prompt_variants, key=lambda x: x.variant_id)
        ]
        request_digest = self._digest(
            {
                "campaign_id": campaign_id,
                "task_id": task_id,
                "prompt_variants": variant_payload,
                "repair_id": repair_id,
            }
        )
        # Do not let an unauthorized caller trigger even provider health I/O.
        with self.session_factory() as session:
            self.policy.authorize(
                session, actor_id, Capability.EXECUTE_EVALUATION, require_human=True
            )
            existing = session.scalar(
                select(EvaluationExecutionRecord).where(
                    EvaluationExecutionRecord.actor_id == actor_id,
                    EvaluationExecutionRecord.idempotency_key == idempotency_key.strip(),
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError("evaluation execution idempotency key was reused")
                if existing.workflow_id != workflow_id:
                    raise ConflictError("evaluation execution idempotency key was reused")
                return {**self._evaluation_execution_dict(session, existing), "replayed": True}
        # Provider health checks may perform I/O. Resolve them before opening the
        # transaction that persists the immutable execution plan.
        provider_health = {
            provider_id: binding.profile.enabled and self._provider_is_healthy(binding.provider)
            for provider_id, binding in self.provider_bindings.items()
        }

        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session, actor_id, Capability.EXECUTE_EVALUATION, require_human=True
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            self._require_active_evaluation_workflow(workflow)
            campaign = session.get(EvaluationCampaignRecord, campaign_id, with_for_update=True)
            task = session.get(Task, task_id)
            if campaign is None or campaign.workflow_id != workflow_id:
                raise NotFoundError("evaluation campaign was not found")
            if task is None or task.workflow_id != workflow_id:
                raise NotFoundError("evaluation task was not found")
            if task.work_capability != campaign.work_capability:
                raise ConflictError("evaluation task capability does not match the campaign")

            existing = session.scalar(
                select(EvaluationExecutionRecord).where(
                    EvaluationExecutionRecord.actor_id == actor_id,
                    EvaluationExecutionRecord.idempotency_key == idempotency_key.strip(),
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError("evaluation execution idempotency key was reused")
                return {**self._evaluation_execution_dict(session, existing), "replayed": True}
            if campaign.status not in {"OPEN", EvaluationStatus.REFINEMENT_REQUIRED.value}:
                raise ConflictError("evaluation campaign is terminal")
            if len(prompt_variants) > campaign.max_prompt_variants:
                raise ValidationError("evaluation execution exceeds the prompt-variant ceiling")
            if campaign.max_total_cost_microunits != 0:
                raise ConflictError(
                    "priced evaluation execution requires a configured cost estimator"
                )
            repair: EvaluationRepairRecord | None = None
            if campaign.current_iteration == 1:
                if repair_id is not None:
                    raise ValidationError("initial evaluation execution cannot use a repair plan")
            else:
                if repair_id is None:
                    raise ConflictError("refinement execution requires a committed repair plan")
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
                if repair.prompt_variants != variant_payload:
                    raise ConflictError("evaluation prompt variants do not match the repair plan")
            existing_iteration = session.scalar(
                select(EvaluationExecutionRecord).where(
                    EvaluationExecutionRecord.campaign_id == campaign.id,
                    EvaluationExecutionRecord.iteration == campaign.current_iteration,
                )
            )
            if existing_iteration is not None:
                raise ConflictError("evaluation execution already exists for campaign iteration")

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
            profiles_by_id = {profile.provider_id: profile for profile in profiles}
            # The accepted Phase 4.2 ceiling is zero. External egress is never
            # eligible for this execution slice, even if globally configured.
            zero_cost_rejections = {
                candidate.provider_id: ["zero_cost_execution_requires_local_egress"]
                for candidate in routing.ranked_candidates
                if profiles_by_id[candidate.provider_id].egress_boundary is not EgressBoundary.LOCAL
            }
            selected = tuple(
                candidate
                for candidate in routing.ranked_candidates
                if profiles_by_id[candidate.provider_id].egress_boundary is EgressBoundary.LOCAL
            )[: campaign.max_candidates]
            if not selected:
                raise ConflictError("no policy-eligible providers are available for evaluation")
            routing_snapshot = {
                "policy_version": routing.policy_version,
                "provider_policy_version": self.provider_policy_version,
                "objective": routing.objective.value,
                "objective_profile_version": routing.objective_profile_version,
                "selected": [asdict(item) for item in selected],
                "rejected": {
                    provider_id: list(reasons) for provider_id, reasons in routing.rejected.items()
                }
                | zero_cost_rejections,
            }
            execution = EvaluationExecutionRecord(
                campaign_id=campaign.id,
                workflow_id=workflow.id,
                task_id=task.id,
                actor_id=actor_id,
                idempotency_key=idempotency_key.strip(),
                request_digest=request_digest,
                repair_id=repair.id if repair is not None else None,
                iteration=campaign.current_iteration,
                workflow_version=workflow.version,
                candidate_revision=workflow.candidate_revision,
                prompt_variants=variant_payload,
                routing_snapshot=routing_snapshot,
                status=EvaluationExecutionStatus.PREPARED.value,
            )
            session.add(execution)
            session.flush()
            prepared: list[PreparedEvaluationRun] = []
            kind = TaskKind(task.kind)
            for ranked in selected:
                profile = profiles_by_id[ranked.provider_id]
                binding = self.provider_bindings[ranked.provider_id]
                for variant in prompt_variants:
                    context = {
                        "workflow_title": workflow.title,
                        "workflow_description": workflow.description,
                        "candidate_revision": workflow.candidate_revision,
                        "workflow_version": workflow.version,
                        "content_trust": "UNTRUSTED_REPOSITORY_CONTEXT",
                        "data_classification": workflow.data_classification,
                        "repository_scope": workflow.repository_scope,
                        "evaluation_campaign_id": campaign.id,
                        "evaluation_iteration": campaign.current_iteration,
                        "prompt_contract_version": campaign.prompt_contract_version,
                        "prompt_variant_id": variant.variant_id,
                        "prompt_variant_instruction": variant.instruction,
                    }
                    request_payload = {
                        "workflow_id": workflow.id,
                        "task_id": task.id,
                        "task_kind": kind.value,
                        "role": TASK_ROLE[kind].value,
                        "objective": task.objective,
                        "context": context,
                        "provider_id": profile.provider_id,
                        "model_version": profile.model_version,
                    }
                    run = EvaluationProviderRunRecord(
                        execution_id=execution.id,
                        campaign_id=campaign.id,
                        provider_id=profile.provider_id,
                        provider_family=profile.provider_family,
                        model_version=profile.model_version,
                        profile_version=profile.profile_version,
                        prompt_variant_id=variant.variant_id,
                        prompt_contract_version=campaign.prompt_contract_version,
                        request_digest=self._digest(request_payload),
                        status=EvaluationExecutionStatus.PREPARED.value,
                        output_json={},
                        usage_json={},
                    )
                    session.add(run)
                    session.flush()
                    prepared.append(
                        PreparedEvaluationRun(
                            run_id=run.id,
                            execution_id=execution.id,
                            binding=binding,
                            profile=profile,
                            request=ProviderRequest(
                                run_id=run.id,
                                workflow_id=workflow.id,
                                task_id=task.id,
                                task_kind=kind,
                                role=TASK_ROLE[kind],
                                objective=task.objective,
                                context=context,
                                required_capability=TASK_CAPABILITY[kind],
                                idempotency_key=run.id,
                            ),
                        )
                    )
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="evaluation.execution_prepared",
                actor_id=actor_id,
                resource_type="evaluation_execution",
                resource_id=execution.id,
                outcome="SUCCEEDED",
                payload={
                    "campaign_id": campaign.id,
                    "iteration": campaign.current_iteration,
                    "provider_ids": [item.provider_id for item in selected],
                    "prompt_variant_ids": sorted(variant_ids),
                    "run_count": len(prepared),
                    "routing_policy_version": routing.policy_version,
                    "repair_id": repair.id if repair is not None else None,
                },
            )
            session.flush()
            execution_id = execution.id
            workflow_version = workflow.version
            candidate_revision = workflow.candidate_revision

        with self.session_factory() as session, session.begin():
            running_execution = session.get(
                EvaluationExecutionRecord, execution_id, with_for_update=True
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            if running_execution is None:
                raise ConflictError("prepared evaluation execution disappeared")
            if (
                workflow.version != workflow_version
                or workflow.candidate_revision != candidate_revision
            ):
                now = datetime.now(UTC)
                running_execution.status = EvaluationExecutionStatus.FAILED.value
                running_execution.error_code = "StaleWorkflowSnapshot"
                running_execution.completed_at = now
                stale_runs = session.scalars(
                    select(EvaluationProviderRunRecord).where(
                        EvaluationProviderRunRecord.execution_id == execution_id
                    )
                ).all()
                for stale_run in stale_runs:
                    stale_run.status = EvaluationExecutionStatus.FAILED.value
                    stale_run.error_code = "StaleWorkflowSnapshot"
                    stale_run.completed_at = now
                append_audit_event(
                    session,
                    workflow_id=workflow_id,
                    event_type="evaluation.execution_completed",
                    actor_id="orchestrator",
                    resource_type="evaluation_execution",
                    resource_id=running_execution.id,
                    outcome=EvaluationExecutionStatus.FAILED.value,
                    payload={
                        "campaign_id": campaign_id,
                        "run_count": len(stale_runs),
                        "error_code": "StaleWorkflowSnapshot",
                    },
                )
                return {
                    **self._evaluation_execution_dict(session, running_execution),
                    "replayed": False,
                }
            running_execution.status = EvaluationExecutionStatus.RUNNING.value
            now = datetime.now(UTC)
            runs = session.scalars(
                select(EvaluationProviderRunRecord).where(
                    EvaluationProviderRunRecord.execution_id == execution_id
                )
            ).all()
            for run in runs:
                run.status = EvaluationExecutionStatus.RUNNING.value
                run.started_at = now

        outcomes: list[EvaluationRunOutcome] = []
        with ThreadPoolExecutor(max_workers=min(len(prepared), 8)) as pool:
            futures = {
                pool.submit(self._execute_evaluation_provider_run, item): item.run_id
                for item in prepared
            }
            for future in as_completed(futures):
                try:
                    outcomes.append(future.result())
                except Exception:
                    outcomes.append(
                        EvaluationRunOutcome(
                            run_id=futures[future],
                            status=EvaluationExecutionStatus.UNKNOWN.value,
                            output={},
                            output_digest=None,
                            usage={},
                            latency_ms=0,
                            error_code="InternalExecutionError",
                        )
                    )

        with self.session_factory() as session, session.begin():
            final_execution = session.get(
                EvaluationExecutionRecord, execution_id, with_for_update=True
            )
            if (
                final_execution is None
                or final_execution.status != EvaluationExecutionStatus.RUNNING.value
            ):
                raise ConflictError("evaluation execution state changed during provider calls")
            for outcome in outcomes:
                run_record = session.get(
                    EvaluationProviderRunRecord, outcome.run_id, with_for_update=True
                )
                if run_record is None or run_record.execution_id != final_execution.id:
                    raise ConflictError("evaluation provider run disappeared")
                run_record.status = outcome.status
                run_record.output_json = outcome.output
                run_record.output_digest = outcome.output_digest
                run_record.usage_json = outcome.usage
                run_record.latency_ms = outcome.latency_ms
                run_record.error_code = outcome.error_code
                run_record.completed_at = datetime.now(UTC)
            statuses = {outcome.status for outcome in outcomes}
            success_count = sum(outcome.status == "SUCCEEDED" for outcome in outcomes)
            if EvaluationExecutionStatus.UNKNOWN.value in statuses:
                final_status = EvaluationExecutionStatus.UNKNOWN
            elif success_count == len(outcomes):
                final_status = EvaluationExecutionStatus.OUTPUTS_READY
            elif success_count:
                final_status = EvaluationExecutionStatus.PARTIAL
            else:
                final_status = EvaluationExecutionStatus.FAILED
            final_execution.status = final_status.value
            final_execution.completed_at = datetime.now(UTC)
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="evaluation.execution_completed",
                actor_id="orchestrator",
                resource_type="evaluation_execution",
                resource_id=final_execution.id,
                outcome=final_status.value,
                payload={
                    "campaign_id": campaign_id,
                    "run_count": len(outcomes),
                    "succeeded": success_count,
                    "failed": len(outcomes) - success_count,
                    "requires_reconciliation": final_status is EvaluationExecutionStatus.UNKNOWN,
                },
            )
            session.flush()
            return {
                **self._evaluation_execution_dict(session, final_execution),
                "replayed": False,
            }

    def get_evaluation_execution(
        self, workflow_id: str, execution_id: str, *, principal_id: str
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_EVALUATION)
            self._get_workflow(session, workflow_id)
            execution = session.get(EvaluationExecutionRecord, execution_id)
            if execution is None or execution.workflow_id != workflow_id:
                raise NotFoundError("evaluation execution was not found")
            return self._evaluation_execution_dict(session, execution)

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
        if not 8 <= len(idempotency_key.strip()) <= 128:
            raise ValidationError("evaluation reconciliation idempotency key is invalid")
        if not rationale.strip() or len(rationale.strip()) > 2_000:
            raise ValidationError("evaluation reconciliation rationale is invalid")
        request_digest = self._digest(
            {
                "target_type": "PROVIDER_EXECUTION",
                "target_id": execution_id,
                "decision": decision.value,
                "rationale": rationale.strip(),
            }
        )
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session,
                actor_id,
                Capability.RECONCILE_EVALUATION,
                require_human=True,
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            existing = session.scalar(
                select(EvaluationReconciliationRecord).where(
                    EvaluationReconciliationRecord.actor_id == actor_id,
                    EvaluationReconciliationRecord.idempotency_key == idempotency_key.strip(),
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError("evaluation reconciliation idempotency key was reused")
                execution = session.get(EvaluationExecutionRecord, execution_id)
                if execution is None or execution.workflow_id != workflow_id:
                    raise NotFoundError("evaluation execution was not found")
                return {
                    **self._evaluation_execution_dict(session, execution),
                    "reconciliation": self._evaluation_reconciliation_dict(existing),
                    "replayed": True,
                }
            self._require_active_evaluation_workflow(workflow)
            execution = session.get(EvaluationExecutionRecord, execution_id, with_for_update=True)
            if execution is None or execution.workflow_id != workflow_id:
                raise NotFoundError("evaluation execution was not found")
            if execution.status != EvaluationExecutionStatus.UNKNOWN.value:
                raise ConflictError("evaluation execution is not awaiting reconciliation")
            if decision is not EvaluationReconciliationDecision.MARK_FAILED:
                raise ValidationError("unsupported evaluation reconciliation decision")
            runs = session.scalars(
                select(EvaluationProviderRunRecord).where(
                    EvaluationProviderRunRecord.execution_id == execution.id
                )
            ).all()
            unknown_runs = [
                run for run in runs if run.status == EvaluationExecutionStatus.UNKNOWN.value
            ]
            if not unknown_runs:
                raise ConflictError("evaluation execution has no unknown provider runs")
            affected_run_ids = sorted(run.id for run in unknown_runs)
            for run in unknown_runs:
                run.status = EvaluationExecutionStatus.FAILED.value
                run.error_code = "HumanReconciledUnknownAsFailed"
                run.completed_at = datetime.now(UTC)
            succeeded = sum(run.status == "SUCCEEDED" for run in runs)
            execution.status = (
                EvaluationExecutionStatus.PARTIAL.value
                if succeeded
                else EvaluationExecutionStatus.FAILED.value
            )
            execution.error_code = "HumanReconciledUnknownAsFailed"
            execution.completed_at = datetime.now(UTC)
            reconciliation = EvaluationReconciliationRecord(
                workflow_id=workflow_id,
                campaign_id=execution.campaign_id,
                actor_id=actor_id,
                idempotency_key=idempotency_key.strip(),
                request_digest=request_digest,
                target_type="PROVIDER_EXECUTION",
                target_id=execution.id,
                decision=decision.value,
                rationale=rationale.strip(),
                affected_run_ids=affected_run_ids,
            )
            session.add(reconciliation)
            session.flush()
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="evaluation.execution_reconciled",
                actor_id=actor_id,
                resource_type="evaluation_execution",
                resource_id=execution.id,
                outcome=execution.status,
                payload={
                    "campaign_id": execution.campaign_id,
                    "decision": decision.value,
                    "affected_run_ids": affected_run_ids,
                    "reconciliation_id": reconciliation.id,
                },
            )
            return {
                **self._evaluation_execution_dict(session, execution),
                "reconciliation": self._evaluation_reconciliation_dict(reconciliation),
                "replayed": False,
            }

    def validate_evaluation_execution(
        self,
        *,
        workflow_id: str,
        execution_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not 8 <= len(idempotency_key.strip()) <= 128:
            raise ValidationError("evaluation assessment idempotency key is invalid")
        request_digest = self._digest({"workflow_id": workflow_id, "execution_id": execution_id})

        resume_assessment_id: str | None = None
        with self.session_factory() as session:
            self.policy.authorize(
                session, actor_id, Capability.VALIDATE_EVALUATION, require_human=True
            )
            existing = session.scalar(
                select(EvaluationAssessmentRecord).where(
                    EvaluationAssessmentRecord.actor_id == actor_id,
                    EvaluationAssessmentRecord.idempotency_key == idempotency_key.strip(),
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest or existing.workflow_id != workflow_id:
                    raise ConflictError("evaluation assessment idempotency key was reused")
                if existing.status == EvaluationAssessmentStatus.DECIDED.value:
                    return {**self._evaluation_assessment_dict(session, existing), "replayed": True}
                if existing.status in {
                    EvaluationAssessmentStatus.CHECKS_READY.value,
                    EvaluationAssessmentStatus.REVIEWS_READY.value,
                }:
                    resume_assessment_id = existing.id
                else:
                    raise ConflictError("evaluation assessment requires recovery")

        if resume_assessment_id is not None:
            result = self.resume_assessment(resume_assessment_id, actor_id)
            return {**result, "replayed": True}

        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session, actor_id, Capability.VALIDATE_EVALUATION, require_human=True
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            self._require_active_evaluation_workflow(workflow)
            execution = session.get(EvaluationExecutionRecord, execution_id, with_for_update=True)
            if execution is None or execution.workflow_id != workflow_id:
                raise NotFoundError("evaluation execution was not found")
            if execution.status == EvaluationExecutionStatus.UNKNOWN.value:
                raise ConflictError("unknown evaluation execution requires reconciliation")
            if execution.status not in {
                EvaluationExecutionStatus.OUTPUTS_READY.value,
                EvaluationExecutionStatus.PARTIAL.value,
                EvaluationExecutionStatus.FAILED.value,
            }:
                raise ConflictError("evaluation execution is not ready for validation")
            campaign = session.get(
                EvaluationCampaignRecord, execution.campaign_id, with_for_update=True
            )
            if campaign is None:
                raise ConflictError("evaluation campaign disappeared")
            if campaign.status not in {"OPEN", EvaluationStatus.REFINEMENT_REQUIRED.value}:
                raise ConflictError("evaluation campaign is terminal")
            if campaign.current_iteration != execution.iteration:
                raise ConflictError("evaluation execution iteration is stale")
            if (
                workflow.version != execution.workflow_version
                or workflow.candidate_revision != execution.candidate_revision
            ):
                raise ConflictError("evaluation execution workflow snapshot is stale")
            missing_validators = sorted(
                set(campaign.required_checks) - self.evaluation_validators.keys()
            )
            if missing_validators:
                raise ConflictError(
                    "trusted validators are not configured: " + ",".join(missing_validators)
                )
            execution_assessment = session.scalar(
                select(EvaluationAssessmentRecord).where(
                    EvaluationAssessmentRecord.execution_id == execution.id
                )
            )
            if execution_assessment is not None:
                raise ConflictError("evaluation execution already has an assessment")

            assessment = EvaluationAssessmentRecord(
                execution_id=execution.id,
                campaign_id=campaign.id,
                workflow_id=workflow.id,
                actor_id=actor_id,
                idempotency_key=idempotency_key.strip(),
                request_digest=request_digest,
                status=EvaluationAssessmentStatus.PREPARED.value,
            )
            session.add(assessment)
            session.flush()
            task = session.get(Task, execution.task_id)
            if task is None:
                raise ConflictError("evaluation task disappeared")
            prepared_checks: list[PreparedEvaluationCheck] = []
            runs = session.scalars(
                select(EvaluationProviderRunRecord)
                .where(EvaluationProviderRunRecord.execution_id == execution.id)
                .order_by(
                    EvaluationProviderRunRecord.provider_id,
                    EvaluationProviderRunRecord.prompt_variant_id,
                )
            ).all()
            for run in runs:
                if run.status != "SUCCEEDED":
                    continue
                if run.output_digest is None:
                    raise ConflictError("successful evaluation run lacks an output digest")
                artifact = EvaluationArtifactRecord(
                    assessment_id=assessment.id,
                    execution_id=execution.id,
                    provider_run_id=run.id,
                    campaign_id=campaign.id,
                    workflow_id=workflow.id,
                    task_id=task.id,
                    artifact_type="MODEL_EVALUATION_OUTPUT",
                    digest=run.output_digest,
                    workflow_version=execution.workflow_version,
                    candidate_revision=execution.candidate_revision,
                    content_json=run.output_json,
                )
                session.add(artifact)
                session.flush()
                trusted_artifact = TrustedEvaluationArtifact(
                    artifact_id=artifact.id,
                    output=artifact.content_json,
                    output_digest=artifact.digest,
                    task_kind=TaskKind(task.kind),
                    workflow_version=artifact.workflow_version,
                    candidate_revision=artifact.candidate_revision,
                )
                for check_name in sorted(campaign.required_checks):
                    validator = self.evaluation_validators[check_name]
                    check = EvaluationCheckRecord(
                        assessment_id=assessment.id,
                        artifact_id=artifact.id,
                        check_name=check_name,
                        validator_version=validator.version,
                        status=EvaluationAssessmentStatus.PREPARED.value,
                        validated_output_digest=artifact.digest,
                        details_json={},
                    )
                    session.add(check)
                    session.flush()
                    prepared_checks.append(
                        PreparedEvaluationCheck(
                            check_id=check.id,
                            validator=validator,
                            artifact=trusted_artifact,
                        )
                    )
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="evaluation.assessment_prepared",
                actor_id=actor_id,
                resource_type="evaluation_assessment",
                resource_id=assessment.id,
                outcome="SUCCEEDED",
                payload={
                    "execution_id": execution.id,
                    "artifact_count": sum(run.status == "SUCCEEDED" for run in runs),
                    "check_count": len(prepared_checks),
                    "validator_versions": {
                        name: self.evaluation_validators[name].version
                        for name in sorted(campaign.required_checks)
                    },
                },
            )
            assessment_id = assessment.id

        with self.session_factory() as session, session.begin():
            running_assessment = session.get(
                EvaluationAssessmentRecord, assessment_id, with_for_update=True
            )
            if running_assessment is None:
                raise ConflictError("prepared evaluation assessment disappeared")
            running_assessment.status = EvaluationAssessmentStatus.RUNNING.value
            checks = session.scalars(
                select(EvaluationCheckRecord).where(
                    EvaluationCheckRecord.assessment_id == running_assessment.id
                )
            ).all()
            for check in checks:
                check.status = EvaluationAssessmentStatus.RUNNING.value

        check_outcomes: list[EvaluationCheckOutcome] = []
        for prepared in prepared_checks:
            try:
                validation_outcome = prepared.validator.validate(prepared.artifact)
                if (
                    not isinstance(validation_outcome, ValidationOutcome)
                    or type(validation_outcome.passed) is not bool
                    or not isinstance(validation_outcome.details, dict)
                ):
                    raise TypeError("validator returned an invalid result")
                canonical_details = json.dumps(
                    validation_outcome.details, sort_keys=True, separators=(",", ":")
                ).encode()
                if len(canonical_details) > 65_536:
                    raise ValueError("validator evidence exceeds 64 KiB")
                check_outcomes.append(
                    EvaluationCheckOutcome(
                        check_id=prepared.check_id,
                        passed=validation_outcome.passed,
                        details=validation_outcome.details,
                    )
                )
            except Exception:
                check_outcomes.append(
                    EvaluationCheckOutcome(
                        check_id=prepared.check_id,
                        passed=False,
                        details={"reason": "validator_error"},
                    )
                )

        with self.session_factory() as session, session.begin():
            final_assessment = session.get(
                EvaluationAssessmentRecord, assessment_id, with_for_update=True
            )
            if (
                final_assessment is None
                or final_assessment.status != EvaluationAssessmentStatus.RUNNING.value
            ):
                raise ConflictError("evaluation assessment state changed during validation")
            now = datetime.now(UTC)
            for outcome in check_outcomes:
                check_record = session.get(
                    EvaluationCheckRecord, outcome.check_id, with_for_update=True
                )
                if check_record is None or check_record.assessment_id != final_assessment.id:
                    raise ConflictError("evaluation check disappeared")
                evidence_payload = {
                    "artifact_id": check_record.artifact_id,
                    "check_name": check_record.check_name,
                    "validator_version": check_record.validator_version,
                    "validated_output_digest": check_record.validated_output_digest,
                    "passed": outcome.passed,
                    "details": outcome.details,
                }
                check_record.status = "COMPLETED"
                check_record.passed = outcome.passed
                check_record.details_json = outcome.details
                check_record.evidence_digest = "sha256:" + self._digest(evidence_payload)
                check_record.completed_at = now
            final_assessment.status = EvaluationAssessmentStatus.CHECKS_READY.value
            append_audit_event(
                session,
                workflow_id=final_assessment.workflow_id,
                event_type="evaluation.assessment_checks_completed",
                actor_id="trusted-validator",
                resource_type="evaluation_assessment",
                resource_id=final_assessment.id,
                outcome="SUCCEEDED",
                payload={
                    "check_count": len(check_outcomes),
                    "passed": sum(outcome.passed for outcome in check_outcomes),
                    "failed": sum(not outcome.passed for outcome in check_outcomes),
                },
            )

        return self.resume_assessment(assessment_id, actor_id)

    def get_evaluation_assessment(
        self, workflow_id: str, assessment_id: str, *, principal_id: str
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            self.policy.authorize(session, principal_id, Capability.READ_EVALUATION)
            self._get_workflow(session, workflow_id)
            assessment = session.get(EvaluationAssessmentRecord, assessment_id)
            if assessment is None or assessment.workflow_id != workflow_id:
                raise NotFoundError("evaluation assessment was not found")
            return self._evaluation_assessment_dict(session, assessment)

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
        if not 8 <= len(idempotency_key.strip()) <= 128:
            raise ValidationError("evaluation reconciliation idempotency key is invalid")
        if not rationale.strip() or len(rationale.strip()) > 2_000:
            raise ValidationError("evaluation reconciliation rationale is invalid")
        request_digest = self._digest(
            {
                "target_type": "REVIEW_ASSESSMENT",
                "target_id": assessment_id,
                "decision": decision.value,
                "rationale": rationale.strip(),
            }
        )
        resume_reconciliation: dict[str, Any] | None = None
        with self.session_factory() as session:
            self.policy.authorize(
                session,
                actor_id,
                Capability.RECONCILE_EVALUATION,
                require_human=True,
            )
            existing = session.scalar(
                select(EvaluationReconciliationRecord).where(
                    EvaluationReconciliationRecord.actor_id == actor_id,
                    EvaluationReconciliationRecord.idempotency_key == idempotency_key.strip(),
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError("evaluation reconciliation idempotency key was reused")
                assessment = session.get(EvaluationAssessmentRecord, assessment_id)
                if assessment is None or assessment.workflow_id != workflow_id:
                    raise NotFoundError("evaluation assessment was not found")
                if assessment.status == EvaluationAssessmentStatus.DECIDED.value:
                    result = self._evaluation_assessment_dict(session, assessment)
                    result["reconciliation"] = self._evaluation_reconciliation_dict(existing)
                    result["replayed"] = True
                    return result
                if assessment.status != EvaluationAssessmentStatus.REVIEWS_READY.value:
                    raise ConflictError("evaluation reconciliation requires recovery")
                resume_reconciliation = self._evaluation_reconciliation_dict(existing)
        if resume_reconciliation is not None:
            result = self.submit_trusted_assessment(assessment_id, actor_id)
            result["reconciliation"] = resume_reconciliation
            result["replayed"] = True
            return result

        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session,
                actor_id,
                Capability.RECONCILE_EVALUATION,
                require_human=True,
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            existing = session.scalar(
                select(EvaluationReconciliationRecord).where(
                    EvaluationReconciliationRecord.actor_id == actor_id,
                    EvaluationReconciliationRecord.idempotency_key == idempotency_key.strip(),
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError("evaluation reconciliation idempotency key was reused")
                raise ConflictError("evaluation reconciliation completed concurrently; retry")
            self._require_active_evaluation_workflow(workflow)
            assessment = session.get(
                EvaluationAssessmentRecord, assessment_id, with_for_update=True
            )
            if assessment is None or assessment.workflow_id != workflow_id:
                raise NotFoundError("evaluation assessment was not found")
            if assessment.status != EvaluationAssessmentStatus.REVIEW_UNKNOWN.value:
                raise ConflictError("evaluation reviews are not awaiting reconciliation")
            if decision is not EvaluationReconciliationDecision.MARK_FAILED:
                raise ValidationError("unsupported evaluation reconciliation decision")
            review_runs = session.scalars(
                select(EvaluationReviewRunRecord).where(
                    EvaluationReviewRunRecord.assessment_id == assessment.id
                )
            ).all()
            unknown_runs = [
                run for run in review_runs if run.status == EvaluationExecutionStatus.UNKNOWN.value
            ]
            if not unknown_runs:
                raise ConflictError("evaluation assessment has no unknown review runs")
            affected_run_ids = sorted(run.id for run in unknown_runs)
            for run in unknown_runs:
                run.status = EvaluationExecutionStatus.FAILED.value
                run.error_code = "HumanReconciledUnknownAsFailed"
                run.completed_at = datetime.now(UTC)
            assessment.status = EvaluationAssessmentStatus.REVIEWS_READY.value
            reconciliation = EvaluationReconciliationRecord(
                workflow_id=workflow_id,
                campaign_id=assessment.campaign_id,
                actor_id=actor_id,
                idempotency_key=idempotency_key.strip(),
                request_digest=request_digest,
                target_type="REVIEW_ASSESSMENT",
                target_id=assessment.id,
                decision=decision.value,
                rationale=rationale.strip(),
                affected_run_ids=affected_run_ids,
            )
            session.add(reconciliation)
            session.flush()
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="evaluation.reviews_reconciled",
                actor_id=actor_id,
                resource_type="evaluation_assessment",
                resource_id=assessment.id,
                outcome=assessment.status,
                payload={
                    "campaign_id": assessment.campaign_id,
                    "decision": decision.value,
                    "affected_run_ids": affected_run_ids,
                    "reconciliation_id": reconciliation.id,
                },
            )
            reconciliation_result = self._evaluation_reconciliation_dict(reconciliation)

        result = self.submit_trusted_assessment(assessment_id, actor_id)
        result["reconciliation"] = reconciliation_result
        result["replayed"] = False
        return result

    def resume_trusted_assessment(self, assessment_id: str, actor_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            assessment = session.get(EvaluationAssessmentRecord, assessment_id)
            if assessment is None:
                raise NotFoundError("evaluation assessment was not found")
            campaign = session.get(EvaluationCampaignRecord, assessment.campaign_id)
            if campaign is None:
                raise ConflictError("evaluation campaign disappeared")
            if assessment.status == EvaluationAssessmentStatus.REVIEWS_READY.value:
                return self.submit_trusted_assessment(assessment.id, actor_id)
            if assessment.status != EvaluationAssessmentStatus.CHECKS_READY.value:
                raise ConflictError("evaluation assessment is not ready for review")
            if campaign.minimum_independent_reviews == 0:
                return self.submit_trusted_assessment(assessment.id, actor_id)

        provider_health = {
            provider_id: binding.profile.enabled and self._provider_is_healthy(binding.provider)
            for provider_id, binding in self.provider_bindings.items()
        }
        prepared_reviews: list[PreparedEvaluationRun] = []
        with self.session_factory() as session, session.begin():
            assessment = session.get(
                EvaluationAssessmentRecord, assessment_id, with_for_update=True
            )
            if (
                assessment is None
                or assessment.status != EvaluationAssessmentStatus.CHECKS_READY.value
            ):
                raise ConflictError("evaluation assessment state changed before review")
            campaign = session.get(EvaluationCampaignRecord, assessment.campaign_id)
            workflow = session.get(Workflow, assessment.workflow_id)
            execution = session.get(EvaluationExecutionRecord, assessment.execution_id)
            if campaign is None or workflow is None or execution is None:
                raise ConflictError("evaluation review source disappeared")
            profiles = tuple(
                replace(
                    binding.profile,
                    healthy=provider_health[binding.profile.provider_id],
                )
                for binding in self.provider_bindings.values()
            )
            profiles = self.evidence_store.hydrate_profiles(session, profiles)
            profiles_by_id = {profile.provider_id: profile for profile in profiles}
            producer_runs = {
                run.id: run
                for run in session.scalars(
                    select(EvaluationProviderRunRecord).where(
                        EvaluationProviderRunRecord.execution_id == execution.id
                    )
                ).all()
            }
            artifacts = session.scalars(
                select(EvaluationArtifactRecord)
                .where(EvaluationArtifactRecord.assessment_id == assessment.id)
                .order_by(EvaluationArtifactRecord.provider_run_id)
            ).all()
            check_records = session.scalars(
                select(EvaluationCheckRecord).where(
                    EvaluationCheckRecord.assessment_id == assessment.id
                )
            ).all()
            passed_checks_by_artifact: dict[str, set[str]] = {}
            for check in check_records:
                if check.status == "COMPLETED" and check.passed is True:
                    passed_checks_by_artifact.setdefault(check.artifact_id, set()).add(
                        check.check_name
                    )
            required_checks = set(campaign.required_checks)
            for artifact in artifacts:
                if not required_checks.issubset(passed_checks_by_artifact.get(artifact.id, set())):
                    continue
                producer = producer_runs.get(artifact.provider_run_id)
                if producer is None:
                    raise ConflictError("evaluation artifact producer disappeared")
                routing = self.router.route(
                    RoutingRequest(
                        required_capabilities=frozenset(
                            {
                                WorkCapability(campaign.work_capability),
                                WorkCapability.CODE_REVIEW,
                            }
                        ),
                        data_classification=DataClassification[workflow.data_classification],
                        risk=RiskLevel[campaign.risk],
                        purpose=RoutingPurpose.REVIEW,
                        allowed_egress=frozenset({EgressBoundary.LOCAL}),
                        minimum_evidence_samples=(
                            self.high_risk_min_evidence_samples
                            if RiskLevel[campaign.risk] >= RiskLevel.HIGH
                            else 0
                        ),
                        producer_provider_id=producer.provider_id,
                        producer_family=producer.provider_family,
                        objective=self.routing_objective,
                    ),
                    profiles,
                )
                selected_reviewers: list[str] = []
                selected_families: set[str] = set()
                for ranked in routing.ranked_candidates:
                    profile = profiles_by_id[ranked.provider_id]
                    if (
                        RiskLevel[campaign.risk] >= RiskLevel.HIGH
                        and profile.provider_family in selected_families
                    ):
                        continue
                    selected_reviewers.append(profile.provider_id)
                    selected_families.add(profile.provider_family)
                    if len(selected_reviewers) == campaign.minimum_independent_reviews:
                        break
                if len(selected_reviewers) < campaign.minimum_independent_reviews:
                    raise ConflictError("insufficient policy-eligible independent reviewers")
                for reviewer_id in selected_reviewers:
                    profile = profiles_by_id[reviewer_id]
                    review = EvaluationReviewRunRecord(
                        assessment_id=assessment.id,
                        artifact_id=artifact.id,
                        candidate_provider_run_id=producer.id,
                        reviewer_provider_id=profile.provider_id,
                        reviewer_provider_family=profile.provider_family,
                        reviewer_model_version=profile.model_version,
                        reviewer_profile_version=profile.profile_version,
                        request_digest="pending",
                        status=EvaluationAssessmentStatus.REVIEW_PREPARED.value,
                        reviewed_output_digest=artifact.digest,
                        output_json={},
                        usage_json={},
                    )
                    session.add(review)
                    session.flush()
                    context = {
                        "content_trust": "UNTRUSTED_CANDIDATE_OUTPUT",
                        "evaluation_campaign_id": campaign.id,
                        "evaluation_assessment_id": assessment.id,
                        "candidate_provider_id": producer.provider_id,
                        "candidate_provider_family": producer.provider_family,
                        "reviewed_output_digest": artifact.digest,
                        "candidate_output": artifact.content_json,
                        "workflow_version": artifact.workflow_version,
                        "candidate_revision": artifact.candidate_revision,
                    }
                    request = ProviderRequest(
                        run_id=review.id,
                        workflow_id=workflow.id,
                        task_id=execution.task_id,
                        task_kind=TaskKind.CODE_REVIEW,
                        role=TASK_ROLE[TaskKind.CODE_REVIEW],
                        objective=(
                            "Independently review the candidate output against the workflow "
                            "objective. Return only the bounded code-review schema."
                        ),
                        context=context,
                        required_capability=Capability.RUN_CODE_REVIEW,
                        idempotency_key=review.id,
                    )
                    review.request_digest = self._digest(asdict(request))
                    prepared_reviews.append(
                        PreparedEvaluationRun(
                            run_id=review.id,
                            execution_id=assessment.id,
                            binding=self.provider_bindings[reviewer_id],
                            profile=profile,
                            request=request,
                        )
                    )
            assessment.status = EvaluationAssessmentStatus.REVIEW_PREPARED.value
            append_audit_event(
                session,
                workflow_id=assessment.workflow_id,
                event_type="evaluation.reviews_prepared",
                actor_id=actor_id,
                resource_type="evaluation_assessment",
                resource_id=assessment.id,
                outcome="SUCCEEDED",
                payload={
                    "review_count": len(prepared_reviews),
                    "minimum_independent_reviews": campaign.minimum_independent_reviews,
                    "local_egress_only": True,
                },
            )

        if not prepared_reviews:
            with self.session_factory() as session, session.begin():
                assessment = session.get(
                    EvaluationAssessmentRecord, assessment_id, with_for_update=True
                )
                if (
                    assessment is None
                    or assessment.status != EvaluationAssessmentStatus.REVIEW_PREPARED.value
                ):
                    raise ConflictError(
                        "evaluation assessment state changed before empty review completion"
                    )
                assessment.status = EvaluationAssessmentStatus.REVIEWS_READY.value
            return self.submit_trusted_assessment(assessment_id, actor_id)

        with self.session_factory() as session, session.begin():
            assessment = session.get(
                EvaluationAssessmentRecord, assessment_id, with_for_update=True
            )
            if (
                assessment is None
                or assessment.status != EvaluationAssessmentStatus.REVIEW_PREPARED.value
            ):
                raise ConflictError("evaluation assessment state changed before reviews ran")
            assessment.status = EvaluationAssessmentStatus.REVIEWS_RUNNING.value
            review_runs = session.scalars(
                select(EvaluationReviewRunRecord).where(
                    EvaluationReviewRunRecord.assessment_id == assessment.id
                )
            ).all()
            now = datetime.now(UTC)
            for review in review_runs:
                review.status = EvaluationAssessmentStatus.REVIEWS_RUNNING.value
                review.started_at = now

        outcomes: list[EvaluationRunOutcome] = []
        with ThreadPoolExecutor(max_workers=min(len(prepared_reviews), 8)) as pool:
            futures = {
                pool.submit(self._execute_evaluation_review_run, item): item.run_id
                for item in prepared_reviews
            }
            for future in as_completed(futures):
                try:
                    outcomes.append(future.result())
                except Exception:
                    outcomes.append(
                        EvaluationRunOutcome(
                            run_id=futures[future],
                            status=EvaluationExecutionStatus.UNKNOWN.value,
                            output={},
                            output_digest=None,
                            usage={},
                            latency_ms=0,
                            error_code="InternalReviewExecutionError",
                        )
                    )

        with self.session_factory() as session, session.begin():
            assessment = session.get(
                EvaluationAssessmentRecord, assessment_id, with_for_update=True
            )
            if (
                assessment is None
                or assessment.status != EvaluationAssessmentStatus.REVIEWS_RUNNING.value
            ):
                raise ConflictError("evaluation assessment state changed during reviews")
            for outcome in outcomes:
                review_record = session.get(
                    EvaluationReviewRunRecord, outcome.run_id, with_for_update=True
                )
                if review_record is None or review_record.assessment_id != assessment.id:
                    raise ConflictError("evaluation review run disappeared")
                review_record.status = outcome.status
                review_record.output_json = outcome.output
                review_record.usage_json = outcome.usage
                review_record.latency_ms = outcome.latency_ms
                review_record.error_code = outcome.error_code
                review_record.completed_at = datetime.now(UTC)
                if outcome.status == "SUCCEEDED":
                    review_record.passed = outcome.output.get("review_passed") is True
                    review_record.evidence_digest = outcome.output_digest
            unknown = any(
                outcome.status == EvaluationExecutionStatus.UNKNOWN.value for outcome in outcomes
            )
            assessment.status = (
                EvaluationAssessmentStatus.REVIEW_UNKNOWN.value
                if unknown
                else EvaluationAssessmentStatus.REVIEWS_READY.value
            )
            append_audit_event(
                session,
                workflow_id=assessment.workflow_id,
                event_type="evaluation.reviews_completed",
                actor_id="orchestrator",
                resource_type="evaluation_assessment",
                resource_id=assessment.id,
                outcome=assessment.status,
                payload={
                    "review_count": len(outcomes),
                    "succeeded": sum(item.status == "SUCCEEDED" for item in outcomes),
                    "requires_reconciliation": unknown,
                },
            )
            if unknown:
                result = self._evaluation_assessment_dict(session, assessment)
                result["replayed"] = False
                return result

        return self.submit_trusted_assessment(assessment_id, actor_id)

    def submit_trusted_assessment(self, assessment_id: str, actor_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            assessment = session.get(EvaluationAssessmentRecord, assessment_id)
            if assessment is None:
                raise NotFoundError("evaluation assessment was not found")
            if assessment.status == EvaluationAssessmentStatus.DECIDED.value:
                return self._evaluation_assessment_dict(session, assessment)
            if assessment.status not in {
                EvaluationAssessmentStatus.CHECKS_READY.value,
                EvaluationAssessmentStatus.REVIEWS_READY.value,
            }:
                raise ConflictError("evaluation assessment evidence is not ready")
            execution = session.get(EvaluationExecutionRecord, assessment.execution_id)
            campaign = session.get(EvaluationCampaignRecord, assessment.campaign_id)
            if execution is None or campaign is None:
                raise ConflictError("evaluation assessment source disappeared")
            runs = session.scalars(
                select(EvaluationProviderRunRecord)
                .where(EvaluationProviderRunRecord.execution_id == execution.id)
                .order_by(
                    EvaluationProviderRunRecord.provider_id,
                    EvaluationProviderRunRecord.prompt_variant_id,
                )
            ).all()
            artifacts = session.scalars(
                select(EvaluationArtifactRecord).where(
                    EvaluationArtifactRecord.assessment_id == assessment.id
                )
            ).all()
            artifacts_by_run = {artifact.provider_run_id: artifact for artifact in artifacts}
            checks = session.scalars(
                select(EvaluationCheckRecord).where(
                    EvaluationCheckRecord.assessment_id == assessment.id
                )
            ).all()
            checks_by_artifact: dict[str, list[EvaluationCheckRecord]] = {}
            for check in checks:
                checks_by_artifact.setdefault(check.artifact_id, []).append(check)
            reviews = session.scalars(
                select(EvaluationReviewRunRecord).where(
                    EvaluationReviewRunRecord.assessment_id == assessment.id
                )
            ).all()
            reviews_by_artifact: dict[str, list[EvaluationReviewRunRecord]] = {}
            for review in reviews:
                reviews_by_artifact.setdefault(review.artifact_id, []).append(review)
            routing_scores = {
                str(item["provider_id"]): float(item["score"])
                for item in execution.routing_snapshot.get("selected", [])
                if isinstance(item, dict)
                and isinstance(item.get("provider_id"), str)
                and isinstance(item.get("score"), (int, float))
                and not isinstance(item.get("score"), bool)
            }
            candidates: list[CandidateEvidence] = []
            for run in runs:
                artifact = artifacts_by_run.get(run.id)
                run_checks = checks_by_artifact.get(artifact.id, []) if artifact else []
                deterministic_checks = tuple(
                    DeterministicCheck(
                        name=check.check_name,
                        passed=check.passed is True,
                        evidence_digest=check.evidence_digest,
                        validated_output_digest=check.validated_output_digest,
                    )
                    for check in sorted(run_checks, key=lambda item: item.check_name)
                    if check.status == "COMPLETED" and check.evidence_digest is not None
                )
                independent_reviews = tuple(
                    IndependentReview(
                        reviewer_provider_id=review.reviewer_provider_id,
                        reviewer_provider_family=review.reviewer_provider_family,
                        reviewer_model_version=review.reviewer_model_version,
                        reviewer_profile_version=review.reviewer_profile_version,
                        reviewed_output_digest=review.reviewed_output_digest,
                        passed=review.passed is True,
                        evidence_digest=review.evidence_digest,
                    )
                    for review in sorted(
                        reviews_by_artifact.get(artifact.id, []) if artifact else [],
                        key=lambda item: item.reviewer_provider_id,
                    )
                    if review.status == "SUCCEEDED" and review.evidence_digest is not None
                )
                candidates.append(
                    CandidateEvidence(
                        candidate_id=run.id,
                        provider_id=run.provider_id,
                        provider_family=run.provider_family,
                        model_version=run.model_version,
                        profile_version=run.profile_version,
                        prompt_variant_id=run.prompt_variant_id,
                        prompt_contract_version=run.prompt_contract_version,
                        iteration=execution.iteration,
                        succeeded=artifact is not None and run.status == "SUCCEEDED",
                        output_digest=artifact.digest if artifact else None,
                        latency_ms=run.latency_ms or 0,
                        cost_microunits=0,
                        routing_score=routing_scores.get(run.provider_id, 0.0),
                        checks=deterministic_checks,
                        reviews=independent_reviews,
                    )
                )
            workflow_id = assessment.workflow_id
            campaign_id = assessment.campaign_id

        decision = self.submit_evaluation_evidence(
            workflow_id=workflow_id,
            campaign_id=campaign_id,
            actor_id=actor_id,
            idempotency_key=f"trusted-validation:{assessment_id}",
            candidates=tuple(candidates),
            repair_id=execution.repair_id,
        )
        with self.session_factory() as session, session.begin():
            assessment = session.get(
                EvaluationAssessmentRecord, assessment_id, with_for_update=True
            )
            if assessment is None:
                raise ConflictError("evaluation assessment disappeared after decision")
            if assessment.status in {
                EvaluationAssessmentStatus.CHECKS_READY.value,
                EvaluationAssessmentStatus.REVIEWS_READY.value,
            }:
                assessment.status = EvaluationAssessmentStatus.DECIDED.value
                assessment.batch_id = str(decision["id"])
                assessment.completed_at = datetime.now(UTC)
                campaign = session.get(EvaluationCampaignRecord, assessment.campaign_id)
                if campaign is None:
                    raise ConflictError("evaluation campaign disappeared after decision")
                finalized_execution = session.get(
                    EvaluationExecutionRecord, assessment.execution_id
                )
                if finalized_execution is None:
                    raise ConflictError("evaluation execution disappeared after decision")
                provider_runs = session.scalars(
                    select(EvaluationProviderRunRecord).where(
                        EvaluationProviderRunRecord.execution_id == assessment.execution_id
                    )
                ).all()
                candidate_records = session.scalars(
                    select(EvaluationCandidateRecord).where(
                        EvaluationCandidateRecord.batch_id == assessment.batch_id
                    )
                ).all()
                candidates_by_id = {
                    candidate.candidate_id: candidate for candidate in candidate_records
                }
                existing_observation_run_ids = set(
                    session.scalars(
                        select(EvaluationObservationRecord.provider_run_id).where(
                            EvaluationObservationRecord.provider_run_id.in_(
                                [run.id for run in provider_runs]
                            )
                        )
                    ).all()
                )
                winner_candidate_id = decision.get("winner_candidate_id")
                observation_count = 0
                for run in provider_runs:
                    if run.id in existing_observation_run_ids:
                        continue
                    candidate = candidates_by_id.get(run.id)
                    session.add(
                        EvaluationObservationRecord(
                            assessment_id=assessment.id,
                            provider_run_id=run.id,
                            campaign_id=assessment.campaign_id,
                            workflow_id=assessment.workflow_id,
                            task_id=finalized_execution.task_id,
                            provider_id=run.provider_id,
                            provider_family=run.provider_family,
                            model_version=run.model_version,
                            profile_version=run.profile_version,
                            work_capability=campaign.work_capability,
                            succeeded=run.status == "SUCCEEDED",
                            validation_passed=(
                                candidate is not None and not candidate.rejection_reasons
                            ),
                            selected_winner=winner_candidate_id == run.id,
                            latency_ms=max(run.latency_ms or 0, 0),
                            error_code=run.error_code,
                        )
                    )
                    observation_count += 1
                append_audit_event(
                    session,
                    workflow_id=assessment.workflow_id,
                    event_type="evaluation.assessment_decided",
                    actor_id=actor_id,
                    resource_type="evaluation_assessment",
                    resource_id=assessment.id,
                    outcome=str(decision["status"]),
                    payload={
                        "batch_id": assessment.batch_id,
                        "campaign_id": assessment.campaign_id,
                        "winner_candidate_id": decision.get("winner_candidate_id"),
                        "observation_count": observation_count,
                    },
                )
            result = self._evaluation_assessment_dict(session, assessment)
            result["decision"] = decision
            result["replayed"] = False
            return result

    @staticmethod
    def _execute_evaluation_review_run(
        prepared: PreparedEvaluationRun,
    ) -> EvaluationRunOutcome:
        started = monotonic()
        try:
            result = prepared.binding.provider.submit(prepared.request)
        except Exception as exc:
            return EvaluationRunOutcome(
                run_id=prepared.run_id,
                status=EvaluationExecutionStatus.UNKNOWN.value,
                output={},
                output_digest=None,
                usage={},
                latency_ms=max(int((monotonic() - started) * 1000), 0),
                error_code=type(exc).__name__[:64],
            )
        try:
            if result.status != "SUCCEEDED":
                raise ValidationError("evaluation reviewer did not report success")
            if result.provider != prepared.binding.provider.name:
                raise ValidationError("evaluation reviewer identity mismatch")
            if result.model != prepared.profile.model_version:
                raise ValidationError("evaluation reviewer model mismatch")
            if type(result.output.get("review_passed")) is not bool:
                raise ValidationError("evaluation review decision must be boolean")
            if not isinstance(result.output.get("blocking_findings"), list):
                raise ValidationError("evaluation review findings must be a list")
            if any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in result.usage.values()
            ):
                raise ValidationError("evaluation reviewer usage is invalid")
            canonical_output = json.dumps(
                result.output, sort_keys=True, separators=(",", ":")
            ).encode()
            if len(canonical_output) > 1_048_576:
                raise ValidationError("evaluation reviewer output exceeds one MiB")
            output_digest = "sha256:" + sha256(canonical_output).hexdigest()
            context = prepared.request.context
            boundary = SecurityBoundaryValidator().validate(
                TrustedEvaluationArtifact(
                    artifact_id=prepared.run_id,
                    output=result.output,
                    output_digest=output_digest,
                    task_kind=TaskKind.CODE_REVIEW,
                    workflow_version=int(context["workflow_version"]),
                    candidate_revision=context.get("candidate_revision"),
                )
            )
            if not boundary.passed:
                raise ValidationError("evaluation reviewer crossed the output boundary")
        except (KeyError, TypeError, ValueError, ValidationError):
            return EvaluationRunOutcome(
                run_id=prepared.run_id,
                status=EvaluationExecutionStatus.FAILED.value,
                output={},
                output_digest=None,
                usage={},
                latency_ms=max(int((monotonic() - started) * 1000), 0),
                error_code="InvalidReviewResult",
            )
        return EvaluationRunOutcome(
            run_id=prepared.run_id,
            status="SUCCEEDED",
            output=result.output,
            output_digest=output_digest,
            usage=result.usage,
            latency_ms=max(int((monotonic() - started) * 1000), 0),
            error_code=None,
        )

    @staticmethod
    def _execute_evaluation_provider_run(
        prepared: PreparedEvaluationRun,
    ) -> EvaluationRunOutcome:
        started = monotonic()
        try:
            result = prepared.binding.provider.submit(prepared.request)
        except Exception as exc:
            return EvaluationRunOutcome(
                run_id=prepared.run_id,
                status=EvaluationExecutionStatus.UNKNOWN.value,
                output={},
                output_digest=None,
                usage={},
                latency_ms=max(int((monotonic() - started) * 1000), 0),
                error_code=type(exc).__name__[:64],
            )
        try:
            if result.status != "SUCCEEDED":
                raise ValidationError("evaluation provider did not report success")
            if result.provider != prepared.binding.provider.name:
                raise ValidationError("evaluation provider identity mismatch")
            if result.model != prepared.profile.model_version:
                raise ValidationError("evaluation provider model mismatch")
            validate_provider_result(prepared.request.task_kind, result)
            if any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in result.usage.values()
            ):
                raise ValidationError("evaluation provider usage is invalid")
            canonical_output = json.dumps(
                result.output, sort_keys=True, separators=(",", ":")
            ).encode()
            if len(canonical_output) > 1_048_576:
                raise ValidationError("evaluation provider output exceeds one MiB")
        except (TypeError, ValueError, ValidationError):
            return EvaluationRunOutcome(
                run_id=prepared.run_id,
                status=EvaluationExecutionStatus.FAILED.value,
                output={},
                output_digest=None,
                usage={},
                latency_ms=max(int((monotonic() - started) * 1000), 0),
                error_code="InvalidProviderResult",
            )
        return EvaluationRunOutcome(
            run_id=prepared.run_id,
            status="SUCCEEDED",
            output=result.output,
            output_digest="sha256:" + sha256(canonical_output).hexdigest(),
            usage=result.usage,
            latency_ms=max(int((monotonic() - started) * 1000), 0),
            error_code=None,
        )
