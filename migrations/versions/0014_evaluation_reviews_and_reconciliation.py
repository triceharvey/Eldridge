"""Add durable independent evaluation reviews and reconciliation.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_review_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_provider_run_id", sa.String(length=36), nullable=False),
        sa.Column("reviewer_provider_id", sa.String(length=128), nullable=False),
        sa.Column("reviewer_provider_family", sa.String(length=128), nullable=False),
        sa.Column("reviewer_model_version", sa.String(length=128), nullable=False),
        sa.Column("reviewer_profile_version", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("evidence_digest", sa.String(length=71), nullable=True),
        sa.Column("reviewed_output_digest", sa.String(length=71), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=False),
        sa.Column("usage_json", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["artifact_id"], ["evaluation_artifacts.id"]),
        sa.ForeignKeyConstraint(["assessment_id"], ["evaluation_assessments.id"]),
        sa.ForeignKeyConstraint(["candidate_provider_run_id"], ["evaluation_provider_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "artifact_id", "reviewer_provider_id", name="uq_evaluation_artifact_reviewer"
        ),
    )
    for column in (
        "assessment_id",
        "artifact_id",
        "candidate_provider_run_id",
        "reviewer_provider_id",
    ):
        op.create_index(f"ix_evaluation_review_runs_{column}", "evaluation_review_runs", [column])

    op.create_table(
        "evaluation_reconciliations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=36), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("affected_run_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["principals.id"]),
        sa.ForeignKeyConstraint(["campaign_id"], ["evaluation_campaigns.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_id", "idempotency_key", name="uq_evaluation_reconciliation_actor_key"
        ),
        sa.UniqueConstraint("target_type", "target_id", name="uq_evaluation_reconciliation_target"),
    )
    op.create_index(
        "ix_evaluation_reconciliations_workflow_id",
        "evaluation_reconciliations",
        ["workflow_id"],
    )
    op.create_index(
        "ix_evaluation_reconciliations_campaign_id",
        "evaluation_reconciliations",
        ["campaign_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_evaluation_reconciliations_campaign_id", table_name="evaluation_reconciliations"
    )
    op.drop_index(
        "ix_evaluation_reconciliations_workflow_id", table_name="evaluation_reconciliations"
    )
    op.drop_table("evaluation_reconciliations")
    for column in (
        "reviewer_provider_id",
        "candidate_provider_run_id",
        "artifact_id",
        "assessment_id",
    ):
        op.drop_index(f"ix_evaluation_review_runs_{column}", table_name="evaluation_review_runs")
    op.drop_table("evaluation_review_runs")
