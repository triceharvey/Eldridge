from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

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
    EvaluationAssessmentStatus,
    EvaluationExecutionStatus,
    EvaluationRecoveryDecision,
    EvaluationStatus,
    PromptVariant,
)
from control_plane.evaluation_read_models import (
    evaluation_promotion_dict,
    evaluation_recovery_dict,
    evaluation_repair_dict,
)
from control_plane.persistence import (
    EvaluationArtifactRecord,
    EvaluationAssessmentRecord,
    EvaluationBatchRecord,
    EvaluationCampaignRecord,
    EvaluationCheckRecord,
    EvaluationExecutionRecord,
    EvaluationPromotionRecord,
    EvaluationRecoveryRecord,
    EvaluationRepairRecord,
    EvaluationReviewRunRecord,
    Workflow,
)
from control_plane.policy import PolicyEngine


class EvaluationLifecycleService:
    """Human-controlled repair, recovery, and promotion boundary."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        policy: PolicyEngine,
        *,
        resume_assessment: Callable[[str, str], dict[str, Any]],
        read_assessment: Callable[[str, str, str], dict[str, Any]],
    ) -> None:
        self.session_factory = session_factory
        self.policy = policy
        self.resume_assessment = resume_assessment
        self.read_assessment = read_assessment
        self._evaluation_repair_dict = evaluation_repair_dict
        self._evaluation_recovery_dict = evaluation_recovery_dict
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
        if not 8 <= len(idempotency_key.strip()) <= 128:
            raise ValidationError("evaluation repair idempotency key is invalid")
        if not rationale.strip() or len(rationale.strip()) > 2_000:
            raise ValidationError("evaluation repair rationale is invalid")
        if not prompt_variants:
            raise ValidationError("evaluation repair requires prompt variants")
        variant_ids = [variant.variant_id for variant in prompt_variants]
        if len(variant_ids) != len(set(variant_ids)):
            raise ValidationError("evaluation repair prompt variant IDs must be unique")
        variant_payload = [
            asdict(item) for item in sorted(prompt_variants, key=lambda item: item.variant_id)
        ]
        request_digest = self._digest(
            {
                "campaign_id": campaign_id,
                "prompt_variants": variant_payload,
                "rationale": rationale.strip(),
            }
        )
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session,
                actor_id,
                Capability.PLAN_EVALUATION_REPAIR,
                require_human=True,
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            existing = session.scalar(
                select(EvaluationRepairRecord).where(
                    EvaluationRepairRecord.actor_id == actor_id,
                    EvaluationRepairRecord.idempotency_key == idempotency_key.strip(),
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest or existing.workflow_id != workflow_id:
                    raise ConflictError("evaluation repair idempotency key was reused")
                return {**self._evaluation_repair_dict(existing), "replayed": True}
            self._require_active_evaluation_workflow(workflow)
            campaign = session.get(EvaluationCampaignRecord, campaign_id, with_for_update=True)
            if campaign is None or campaign.workflow_id != workflow_id:
                raise NotFoundError("evaluation campaign was not found")
            if campaign.status != EvaluationStatus.REFINEMENT_REQUIRED.value:
                raise ConflictError("evaluation campaign does not require repair")
            if len(prompt_variants) > campaign.max_prompt_variants:
                raise ValidationError("evaluation repair exceeds the prompt-variant ceiling")
            source_iteration = campaign.current_iteration - 1
            source_batch = session.scalar(
                select(EvaluationBatchRecord).where(
                    EvaluationBatchRecord.campaign_id == campaign.id,
                    EvaluationBatchRecord.iteration == source_iteration,
                )
            )
            if (
                source_batch is None
                or source_batch.status != EvaluationStatus.REFINEMENT_REQUIRED.value
            ):
                raise ConflictError("evaluation repair source is unavailable")
            repair = EvaluationRepairRecord(
                workflow_id=workflow.id,
                campaign_id=campaign.id,
                source_batch_id=source_batch.id,
                actor_id=actor_id,
                idempotency_key=idempotency_key.strip(),
                request_digest=request_digest,
                source_iteration=source_iteration,
                target_iteration=campaign.current_iteration,
                workflow_version=workflow.version,
                candidate_revision=workflow.candidate_revision,
                prompt_variants=variant_payload,
                failure_snapshot=source_batch.rejected_candidates,
                rationale=rationale.strip(),
            )
            session.add(repair)
            session.flush()
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="evaluation.repair_planned",
                actor_id=actor_id,
                resource_type="evaluation_repair",
                resource_id=repair.id,
                outcome="SUCCEEDED",
                payload={
                    "campaign_id": campaign.id,
                    "source_batch_id": source_batch.id,
                    "source_iteration": source_iteration,
                    "target_iteration": campaign.current_iteration,
                    "prompt_variant_ids": sorted(variant_ids),
                    "workflow_version": workflow.version,
                    "candidate_revision": workflow.candidate_revision,
                },
            )
            return {**self._evaluation_repair_dict(repair), "replayed": False}

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
        if not 8 <= len(idempotency_key.strip()) <= 128:
            raise ValidationError("evaluation recovery idempotency key is invalid")
        if not rationale.strip() or len(rationale.strip()) > 2_000:
            raise ValidationError("evaluation recovery rationale is invalid")
        if decision is not EvaluationRecoveryDecision.MARK_INTERRUPTED_FAILED:
            raise ValidationError("unsupported evaluation recovery decision")
        request_digest = self._digest(
            {
                "assessment_id": assessment_id,
                "decision": decision.value,
                "rationale": rationale.strip(),
            }
        )
        replayed_recovery: dict[str, Any] | None = None
        replayed_status: str | None = None
        with self.session_factory() as session:
            self.policy.authorize(
                session, actor_id, Capability.RECOVER_EVALUATION, require_human=True
            )
            existing = session.scalar(
                select(EvaluationRecoveryRecord).where(
                    EvaluationRecoveryRecord.actor_id == actor_id,
                    EvaluationRecoveryRecord.idempotency_key == idempotency_key.strip(),
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest or existing.workflow_id != workflow_id:
                    raise ConflictError("evaluation recovery idempotency key was reused")
                assessment = session.get(EvaluationAssessmentRecord, assessment_id)
                if assessment is None or assessment.workflow_id != workflow_id:
                    raise NotFoundError("evaluation assessment was not found")
                replayed_recovery = self._evaluation_recovery_dict(existing)
                replayed_status = assessment.status
        if replayed_recovery is not None:
            if replayed_status in {
                EvaluationAssessmentStatus.CHECKS_READY.value,
                EvaluationAssessmentStatus.REVIEWS_READY.value,
            }:
                replayed_result = self.resume_assessment(assessment_id, actor_id)
            else:
                replayed_result = self.read_assessment(workflow_id, assessment_id, actor_id)
            replayed_result["recovery"] = replayed_recovery
            replayed_result["replayed"] = True
            return replayed_result
        continue_assessment = False
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session, actor_id, Capability.RECOVER_EVALUATION, require_human=True
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            self._require_active_evaluation_workflow(workflow)
            assessment = session.get(
                EvaluationAssessmentRecord, assessment_id, with_for_update=True
            )
            if assessment is None or assessment.workflow_id != workflow_id:
                raise NotFoundError("evaluation assessment was not found")
            prior_status = assessment.status
            recoverable_checks = {
                EvaluationAssessmentStatus.PREPARED.value,
                EvaluationAssessmentStatus.RUNNING.value,
            }
            affected: list[str] = []
            if prior_status in recoverable_checks:
                checks = session.scalars(
                    select(EvaluationCheckRecord).where(
                        EvaluationCheckRecord.assessment_id == assessment.id,
                        EvaluationCheckRecord.status.in_(tuple(recoverable_checks)),
                    )
                ).all()
                now = datetime.now(UTC)
                for check in checks:
                    details = {"reason": "interrupted_assessment_recovered_as_failed"}
                    check.status = "COMPLETED"
                    check.passed = False
                    check.details_json = details
                    check.evidence_digest = "sha256:" + self._digest(
                        {
                            "artifact_id": check.artifact_id,
                            "check_name": check.check_name,
                            "validator_version": check.validator_version,
                            "validated_output_digest": check.validated_output_digest,
                            "passed": False,
                            "details": details,
                        }
                    )
                    check.completed_at = now
                    affected.append(check.id)
                assessment.status = EvaluationAssessmentStatus.CHECKS_READY.value
                assessment.error_code = "InterruptedChecksMarkedFailed"
                continue_assessment = True
            elif prior_status == EvaluationAssessmentStatus.REVIEW_PREPARED.value:
                reviews = session.scalars(
                    select(EvaluationReviewRunRecord).where(
                        EvaluationReviewRunRecord.assessment_id == assessment.id,
                        EvaluationReviewRunRecord.status
                        == EvaluationAssessmentStatus.REVIEW_PREPARED.value,
                    )
                ).all()
                now = datetime.now(UTC)
                for review in reviews:
                    review.status = EvaluationExecutionStatus.FAILED.value
                    review.error_code = "InterruptedPreparedReviewMarkedFailed"
                    review.completed_at = now
                    affected.append(review.id)
                assessment.status = EvaluationAssessmentStatus.REVIEWS_READY.value
                assessment.error_code = "InterruptedPreparedReviewsMarkedFailed"
                continue_assessment = True
            elif prior_status == EvaluationAssessmentStatus.REVIEWS_RUNNING.value:
                reviews = session.scalars(
                    select(EvaluationReviewRunRecord).where(
                        EvaluationReviewRunRecord.assessment_id == assessment.id,
                        EvaluationReviewRunRecord.status
                        == EvaluationAssessmentStatus.REVIEWS_RUNNING.value,
                    )
                ).all()
                for review in reviews:
                    review.status = EvaluationExecutionStatus.UNKNOWN.value
                    review.error_code = "InterruptedReviewOutcomeUnknown"
                    affected.append(review.id)
                assessment.status = EvaluationAssessmentStatus.REVIEW_UNKNOWN.value
                assessment.error_code = "InterruptedReviewOutcomeUnknown"
            else:
                raise ConflictError("evaluation assessment is not in a recoverable state")
            recovery = EvaluationRecoveryRecord(
                workflow_id=workflow_id,
                campaign_id=assessment.campaign_id,
                assessment_id=assessment.id,
                actor_id=actor_id,
                idempotency_key=idempotency_key.strip(),
                request_digest=request_digest,
                prior_status=prior_status,
                outcome_status=assessment.status,
                decision=decision.value,
                rationale=rationale.strip(),
                affected_record_ids=sorted(affected),
            )
            session.add(recovery)
            session.flush()
            append_audit_event(
                session,
                workflow_id=workflow_id,
                event_type="evaluation.assessment_recovered",
                actor_id=actor_id,
                resource_type="evaluation_assessment",
                resource_id=assessment.id,
                outcome=assessment.status,
                payload={
                    "recovery_id": recovery.id,
                    "decision": decision.value,
                    "prior_status": prior_status,
                    "affected_record_ids": sorted(affected),
                },
            )
            recovery_id = recovery.id

        if continue_assessment:
            result = self.resume_assessment(assessment_id, actor_id)
        else:
            result = self.read_assessment(workflow_id, assessment_id, actor_id)
        with self.session_factory() as session:
            recovered_record = session.get(EvaluationRecoveryRecord, recovery_id)
            if recovered_record is None:
                raise ConflictError("evaluation recovery disappeared")
            result["recovery"] = self._evaluation_recovery_dict(recovered_record)
        result["replayed"] = False
        return result

    def promote_evaluation_winner(
        self,
        *,
        workflow_id: str,
        assessment_id: str,
        actor_id: str,
        idempotency_key: str,
        rationale: str,
    ) -> dict[str, Any]:
        if not 8 <= len(idempotency_key.strip()) <= 128:
            raise ValidationError("evaluation promotion idempotency key is invalid")
        if not rationale.strip() or len(rationale.strip()) > 2_000:
            raise ValidationError("evaluation promotion rationale is invalid")
        request_digest = self._digest(
            {"assessment_id": assessment_id, "rationale": rationale.strip()}
        )
        with self.session_factory() as session, session.begin():
            self.policy.authorize(
                session, actor_id, Capability.PROMOTE_EVALUATION, require_human=True
            )
            workflow = self._get_workflow(session, workflow_id, lock=True)
            existing = session.scalar(
                select(EvaluationPromotionRecord).where(
                    EvaluationPromotionRecord.actor_id == actor_id,
                    EvaluationPromotionRecord.idempotency_key == idempotency_key.strip(),
                )
            )
            if existing is not None:
                if existing.request_digest != request_digest or existing.workflow_id != workflow_id:
                    raise ConflictError("evaluation promotion idempotency key was reused")
                return {**self._evaluation_promotion_dict(existing), "replayed": True}
            self._require_active_evaluation_workflow(workflow)
            assessment = session.get(EvaluationAssessmentRecord, assessment_id)
            if (
                assessment is None
                or assessment.workflow_id != workflow_id
                or assessment.status != EvaluationAssessmentStatus.DECIDED.value
                or assessment.batch_id is None
            ):
                raise ConflictError("evaluation assessment is not promotable")
            campaign = session.get(
                EvaluationCampaignRecord, assessment.campaign_id, with_for_update=True
            )
            batch = session.get(EvaluationBatchRecord, assessment.batch_id)
            execution = session.get(EvaluationExecutionRecord, assessment.execution_id)
            if campaign is None or batch is None or execution is None:
                raise ConflictError("evaluation promotion source disappeared")
            if (
                campaign.status != EvaluationStatus.WINNER_SELECTED.value
                or campaign.winner_candidate_id is None
                or batch.winner_candidate_id != campaign.winner_candidate_id
            ):
                raise ConflictError("evaluation campaign has no validated winner")
            if (
                execution.workflow_version != workflow.version
                or execution.candidate_revision != workflow.candidate_revision
            ):
                raise ConflictError("evaluation winner workflow snapshot is stale")
            artifact = session.scalar(
                select(EvaluationArtifactRecord).where(
                    EvaluationArtifactRecord.assessment_id == assessment.id,
                    EvaluationArtifactRecord.provider_run_id == campaign.winner_candidate_id,
                )
            )
            if artifact is None:
                raise ConflictError("evaluation winner artifact is unavailable")
            if (
                artifact.workflow_version != workflow.version
                or artifact.candidate_revision != workflow.candidate_revision
            ):
                raise ConflictError("evaluation winner artifact snapshot is stale")
            promotion = EvaluationPromotionRecord(
                workflow_id=workflow.id,
                campaign_id=campaign.id,
                assessment_id=assessment.id,
                batch_id=batch.id,
                candidate_id=campaign.winner_candidate_id,
                artifact_id=artifact.id,
                artifact_digest=artifact.digest,
                workflow_version=workflow.version,
                candidate_revision=workflow.candidate_revision,
                actor_id=actor_id,
                idempotency_key=idempotency_key.strip(),
                request_digest=request_digest,
                rationale=rationale.strip(),
            )
            session.add(promotion)
            session.flush()
            append_audit_event(
                session,
                workflow_id=workflow.id,
                event_type="evaluation.winner_promoted",
                actor_id=actor_id,
                resource_type="evaluation_promotion",
                resource_id=promotion.id,
                outcome="PROMOTED",
                payload={
                    "campaign_id": campaign.id,
                    "assessment_id": assessment.id,
                    "batch_id": batch.id,
                    "candidate_id": promotion.candidate_id,
                    "artifact_id": artifact.id,
                    "artifact_digest": artifact.digest,
                    "workflow_version": workflow.version,
                    "candidate_revision": workflow.candidate_revision,
                    "grants_merge_or_deployment_authority": False,
                },
            )
            return {**self._evaluation_promotion_dict(promotion), "replayed": False}
