"""Add durable policy-eligible evaluation provider execution.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_executions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("candidate_revision", sa.String(length=128), nullable=True),
        sa.Column("prompt_variants", sa.JSON(), nullable=False),
        sa.Column("routing_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["actor_id"], ["principals.id"]),
        sa.ForeignKeyConstraint(["campaign_id"], ["evaluation_campaigns.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_id", "idempotency_key", name="uq_evaluation_execution_actor_key"
        ),
        sa.UniqueConstraint("campaign_id", "iteration", name="uq_evaluation_execution_iteration"),
    )
    op.create_index(
        "ix_evaluation_executions_campaign_id", "evaluation_executions", ["campaign_id"]
    )
    op.create_index(
        "ix_evaluation_executions_workflow_id", "evaluation_executions", ["workflow_id"]
    )
    op.create_index("ix_evaluation_executions_task_id", "evaluation_executions", ["task_id"])

    op.create_table(
        "evaluation_provider_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("provider_family", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("profile_version", sa.String(length=128), nullable=False),
        sa.Column("prompt_variant_id", sa.String(length=128), nullable=False),
        sa.Column("prompt_contract_version", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("output_digest", sa.String(length=71), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=False),
        sa.Column("usage_json", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["campaign_id"], ["evaluation_campaigns.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["evaluation_executions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "execution_id",
            "provider_id",
            "prompt_variant_id",
            name="uq_evaluation_provider_variant",
        ),
    )
    op.create_index(
        "ix_evaluation_provider_runs_execution_id", "evaluation_provider_runs", ["execution_id"]
    )
    op.create_index(
        "ix_evaluation_provider_runs_campaign_id", "evaluation_provider_runs", ["campaign_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_evaluation_provider_runs_campaign_id", table_name="evaluation_provider_runs")
    op.drop_index("ix_evaluation_provider_runs_execution_id", table_name="evaluation_provider_runs")
    op.drop_table("evaluation_provider_runs")
    op.drop_index("ix_evaluation_executions_task_id", table_name="evaluation_executions")
    op.drop_index("ix_evaluation_executions_workflow_id", table_name="evaluation_executions")
    op.drop_index("ix_evaluation_executions_campaign_id", table_name="evaluation_executions")
    op.drop_table("evaluation_executions")
