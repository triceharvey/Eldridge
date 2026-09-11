from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, replace
from hashlib import sha256
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from control_plane.audit import append_audit_event
from control_plane.domain import (
    Capability,
    ConflictError,
    NotFoundError,
    ValidationError,
    WorkflowState,
)
from control_plane.evaluation import (
    EVALUATION_POLICY_VERSION,
    CandidateEvidence,
    EvaluationBatch,
    EvaluationPolicy,
    EvaluationStatus,
    MultiModelEvaluator,
    validate_evaluation_identifier,
)
from control_plane.evaluation_read_models import (
    evaluation_batch_dict,
    evaluation_campaign_dict,
    evaluation_candidate_dict,
    evaluation_policy,
    evaluation_promotion_dict,
    evaluation_repair_dict,
)
from control_plane.learning import ProviderEvidenceStore
from control_plane.persistence import (
    EvaluationBatchRecord,
    EvaluationCampaignRecord,
    EvaluationCandidateRecord,
    EvaluationPromotionRecord,
    EvaluationRepairRecord,
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


class CampaignProviderBinding(Protocol):
    @property
    def profile(self) -> ProviderProfile: ...

    @property
    def provider(self) -> ModelProvider: ...


class EvaluationCampaignService:
    """Evaluation campaign policy, decision, and query boundary."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        policy: PolicyEngine,
        *,
        evaluator: MultiModelEvaluator,
        provider_bindings: Mapping[str, CampaignProviderBinding],
        router: CapabilityRouter,
        evidence_store: ProviderEvidenceStore,
        allowed_egress: frozenset[EgressBoundary],
        high_risk_min_evidence_samples: int,
        provider_policy_version: str,
        routing_objective: RoutingObjective,
    ) -> None:
        self.session_factory = session_factory
        self.policy = policy
        self.evaluator = evaluator
        self.provider_bindings = provider_bindings
        self.router = router
        self.evidence_store = evidence_store
        self.allowed_egress = allowed_egress
        self.high_risk_min_evidence_samples = high_risk_min_evidence_samples
        self.provider_policy_version = provider_policy_version
        self.routing_objective = routing_objective
        self._evaluation_policy = evaluation_policy
        self._evaluation_campaign_dict = evaluation_campaign_dict
        self._evaluation_batch_dict = evaluation_batch_dict
        self._evaluation_candidate_dict = evaluation_candidate_dict
        self._evaluation_repair_dict = evaluation_repair_dict
        self._evaluation_promotion_dict = evaluation_promotion_dict

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
