"""Add trusted evaluation artifacts, assessments, and checks.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_assessments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["actor_id"], ["principals.id"]),
        sa.ForeignKeyConstraint(["batch_id"], ["evaluation_batches.id"]),
        sa.ForeignKeyConstraint(["campaign_id"], ["evaluation_campaigns.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["evaluation_executions.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_id", "idempotency_key", name="uq_evaluation_assessment_actor_key"
        ),
        sa.UniqueConstraint("execution_id", name="uq_evaluation_assessment_execution"),
    )
    op.create_index(
        "ix_evaluation_assessments_execution_id", "evaluation_assessments", ["execution_id"]
    )
    op.create_index(
        "ix_evaluation_assessments_campaign_id", "evaluation_assessments", ["campaign_id"]
    )
    op.create_index(
        "ix_evaluation_assessments_workflow_id", "evaluation_assessments", ["workflow_id"]
    )

    op.create_table(
        "evaluation_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("provider_run_id", sa.String(length=36), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_type", sa.String(length=64), nullable=False),
        sa.Column("digest", sa.String(length=71), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("candidate_revision", sa.String(length=128), nullable=True),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["evaluation_assessments.id"]),
        sa.ForeignKeyConstraint(["campaign_id"], ["evaluation_campaigns.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["evaluation_executions.id"]),
        sa.ForeignKeyConstraint(["provider_run_id"], ["evaluation_provider_runs.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_run_id"),
    )
    for column in (
        "assessment_id",
        "execution_id",
        "provider_run_id",
        "campaign_id",
        "workflow_id",
        "task_id",
    ):
        op.create_index(f"ix_evaluation_artifacts_{column}", "evaluation_artifacts", [column])

    op.create_table(
        "evaluation_checks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("check_name", sa.String(length=128), nullable=False),
        sa.Column("validator_version", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("evidence_digest", sa.String(length=71), nullable=True),
        sa.Column("validated_output_digest", sa.String(length=71), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["artifact_id"], ["evaluation_artifacts.id"]),
        sa.ForeignKeyConstraint(["assessment_id"], ["evaluation_assessments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_id", "check_name", name="uq_evaluation_artifact_check"),
    )
    op.create_index("ix_evaluation_checks_assessment_id", "evaluation_checks", ["assessment_id"])
    op.create_index("ix_evaluation_checks_artifact_id", "evaluation_checks", ["artifact_id"])

    op.create_table(
        "evaluation_observations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=False),
        sa.Column("provider_run_id", sa.String(length=36), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("provider_family", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("profile_version", sa.String(length=128), nullable=False),
        sa.Column("work_capability", sa.String(length=64), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("validation_passed", sa.Boolean(), nullable=False),
        sa.Column("selected_winner", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["evaluation_assessments.id"]),
        sa.ForeignKeyConstraint(["campaign_id"], ["evaluation_campaigns.id"]),
        sa.ForeignKeyConstraint(["provider_run_id"], ["evaluation_provider_runs.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_run_id"),
    )
    for column in (
        "assessment_id",
        "provider_run_id",
        "campaign_id",
        "workflow_id",
        "task_id",
        "provider_id",
        "work_capability",
    ):
        op.create_index(f"ix_evaluation_observations_{column}", "evaluation_observations", [column])


def downgrade() -> None:
    for column in (
        "work_capability",
        "provider_id",
        "task_id",
        "workflow_id",
        "campaign_id",
        "provider_run_id",
        "assessment_id",
    ):
        op.drop_index(
            f"ix_evaluation_observations_{column}",
            table_name="evaluation_observations",
            if_exists=True,
        )
    op.drop_table("evaluation_observations", if_exists=True)
    op.drop_index("ix_evaluation_checks_artifact_id", table_name="evaluation_checks")
    op.drop_index("ix_evaluation_checks_assessment_id", table_name="evaluation_checks")
    op.drop_table("evaluation_checks")
    for column in (
        "task_id",
        "workflow_id",
        "campaign_id",
        "provider_run_id",
        "execution_id",
        "assessment_id",
    ):
        op.drop_index(f"ix_evaluation_artifacts_{column}", table_name="evaluation_artifacts")
    op.drop_table("evaluation_artifacts")
    op.drop_index("ix_evaluation_assessments_workflow_id", table_name="evaluation_assessments")
    op.drop_index("ix_evaluation_assessments_campaign_id", table_name="evaluation_assessments")
    op.drop_index("ix_evaluation_assessments_execution_id", table_name="evaluation_assessments")
    op.drop_table("evaluation_assessments")
