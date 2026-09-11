from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from control_plane.evaluation import EvaluationPolicy
from control_plane.persistence import (
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
)
from control_plane.routing import RiskLevel


def evaluation_policy(record: EvaluationCampaignRecord) -> EvaluationPolicy:
    return EvaluationPolicy(
        required_checks=frozenset(record.required_checks),
        risk=RiskLevel[record.risk],
        max_candidates=record.max_candidates,
        max_prompt_variants=record.max_prompt_variants,
        max_iterations=record.max_iterations,
        max_total_cost_microunits=record.max_total_cost_microunits,
        minimum_independent_reviews=record.minimum_independent_reviews,
    )


def evaluation_campaign_dict(record: EvaluationCampaignRecord) -> dict[str, Any]:
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


def evaluation_batch_dict(record: EvaluationBatchRecord) -> dict[str, Any]:
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


def evaluation_candidate_dict(record: EvaluationCandidateRecord) -> dict[str, Any]:
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


def evaluation_execution_dict(
    session: Session, record: EvaluationExecutionRecord
) -> dict[str, Any]:
    runs = session.scalars(
        select(EvaluationProviderRunRecord)
        .where(EvaluationProviderRunRecord.execution_id == record.id)
        .order_by(
            EvaluationProviderRunRecord.provider_id, EvaluationProviderRunRecord.prompt_variant_id
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
        "runs": [evaluation_provider_run_dict(run) for run in runs],
        "created_at": record.created_at.isoformat(),
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
    }


def evaluation_provider_run_dict(record: EvaluationProviderRunRecord) -> dict[str, Any]:
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


def evaluation_assessment_dict(
    session: Session, record: EvaluationAssessmentRecord
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
        checks_by_artifact.setdefault(check.artifact_id, []).append(evaluation_check_dict(check))
    reviews = session.scalars(
        select(EvaluationReviewRunRecord)
        .where(EvaluationReviewRunRecord.assessment_id == record.id)
        .order_by(
            EvaluationReviewRunRecord.artifact_id, EvaluationReviewRunRecord.reviewer_provider_id
        )
    ).all()
    reviews_by_artifact: dict[str, list[dict[str, Any]]] = {}
    for review in reviews:
        reviews_by_artifact.setdefault(review.artifact_id, []).append(
            evaluation_review_dict(review)
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
            result["decision"] = evaluation_batch_dict(batch)
    result["recoveries"] = [
        evaluation_recovery_dict(recovery)
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
    result["promotion"] = evaluation_promotion_dict(promotion) if promotion is not None else None
    return result


def evaluation_check_dict(record: EvaluationCheckRecord) -> dict[str, Any]:
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


def evaluation_review_dict(record: EvaluationReviewRunRecord) -> dict[str, Any]:
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


def evaluation_reconciliation_dict(record: EvaluationReconciliationRecord) -> dict[str, Any]:
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


def evaluation_repair_dict(record: EvaluationRepairRecord) -> dict[str, Any]:
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


def evaluation_recovery_dict(record: EvaluationRecoveryRecord) -> dict[str, Any]:
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


def evaluation_promotion_dict(record: EvaluationPromotionRecord) -> dict[str, Any]:
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
