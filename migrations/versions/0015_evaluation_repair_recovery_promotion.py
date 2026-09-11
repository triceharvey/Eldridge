"""Add bounded evaluation repair, recovery, and promotion.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_repairs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("source_batch_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("source_iteration", sa.Integer(), nullable=False),
        sa.Column("target_iteration", sa.Integer(), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("candidate_revision", sa.String(length=128), nullable=True),
        sa.Column("prompt_variants", sa.JSON(), nullable=False),
        sa.Column("failure_snapshot", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["principals.id"]),
        sa.ForeignKeyConstraint(["campaign_id"], ["evaluation_campaigns.id"]),
        sa.ForeignKeyConstraint(["source_batch_id"], ["evaluation_batches.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_evaluation_repair_actor_key"),
        sa.UniqueConstraint(
            "campaign_id", "target_iteration", name="uq_evaluation_repair_iteration"
        ),
    )
    op.create_index("ix_evaluation_repairs_workflow_id", "evaluation_repairs", ["workflow_id"])
    op.create_index("ix_evaluation_repairs_campaign_id", "evaluation_repairs", ["campaign_id"])
    op.add_column("evaluation_batches", sa.Column("repair_id", sa.String(length=36)))
    op.create_index("ix_evaluation_batches_repair_id", "evaluation_batches", ["repair_id"])
    op.add_column("evaluation_executions", sa.Column("repair_id", sa.String(length=36)))
    op.create_foreign_key(
        "fk_evaluation_executions_repair_id",
        "evaluation_executions",
        "evaluation_repairs",
        ["repair_id"],
        ["id"],
    )
    op.create_index("ix_evaluation_executions_repair_id", "evaluation_executions", ["repair_id"])

    op.create_table(
        "evaluation_recoveries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("prior_status", sa.String(length=32), nullable=False),
        sa.Column("outcome_status", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=64), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("affected_record_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["principals.id"]),
        sa.ForeignKeyConstraint(["assessment_id"], ["evaluation_assessments.id"]),
        sa.ForeignKeyConstraint(["campaign_id"], ["evaluation_campaigns.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_evaluation_recovery_actor_key"),
        sa.UniqueConstraint(
            "assessment_id", "prior_status", name="uq_evaluation_recovery_assessment_status"
        ),
    )
    op.create_index(
        "ix_evaluation_recoveries_workflow_id", "evaluation_recoveries", ["workflow_id"]
    )
    op.create_index(
        "ix_evaluation_recoveries_campaign_id", "evaluation_recoveries", ["campaign_id"]
    )

    op.create_table(
        "evaluation_promotions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=128), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_digest", sa.String(length=71), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("candidate_revision", sa.String(length=128), nullable=True),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["principals.id"]),
        sa.ForeignKeyConstraint(["artifact_id"], ["evaluation_artifacts.id"]),
        sa.ForeignKeyConstraint(["assessment_id"], ["evaluation_assessments.id"]),
        sa.ForeignKeyConstraint(["batch_id"], ["evaluation_batches.id"]),
        sa.ForeignKeyConstraint(["campaign_id"], ["evaluation_campaigns.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_id", "idempotency_key", name="uq_evaluation_promotion_actor_key"
        ),
        sa.UniqueConstraint("campaign_id", name="uq_evaluation_promotion_campaign"),
    )
    op.create_index(
        "ix_evaluation_promotions_workflow_id", "evaluation_promotions", ["workflow_id"]
    )
    op.create_index(
        "ix_evaluation_promotions_campaign_id", "evaluation_promotions", ["campaign_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_evaluation_promotions_campaign_id", table_name="evaluation_promotions")
    op.drop_index("ix_evaluation_promotions_workflow_id", table_name="evaluation_promotions")
    op.drop_table("evaluation_promotions")
    op.drop_index("ix_evaluation_recoveries_campaign_id", table_name="evaluation_recoveries")
    op.drop_index("ix_evaluation_recoveries_workflow_id", table_name="evaluation_recoveries")
    op.drop_table("evaluation_recoveries")
    op.drop_index("ix_evaluation_executions_repair_id", table_name="evaluation_executions")
    op.drop_constraint(
        "fk_evaluation_executions_repair_id", "evaluation_executions", type_="foreignkey"
    )
    op.drop_column("evaluation_executions", "repair_id")
    op.drop_index("ix_evaluation_batches_repair_id", table_name="evaluation_batches")
    op.drop_column("evaluation_batches", "repair_id")
    op.drop_index("ix_evaluation_repairs_campaign_id", table_name="evaluation_repairs")
    op.drop_index("ix_evaluation_repairs_workflow_id", table_name="evaluation_repairs")
    op.drop_table("evaluation_repairs")
