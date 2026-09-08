"""Add workflow classification, routing decisions, and provider observations.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE workflows SET risk_class = 'MEDIUM' WHERE risk_class = 'STANDARD'")
    op.add_column(
        "workflows",
        sa.Column(
            "complexity_tier",
            sa.String(length=32),
            nullable=False,
            server_default="STANDARD",
        ),
    )
    op.add_column(
        "workflows",
        sa.Column(
            "data_classification",
            sa.String(length=32),
            nullable=False,
            server_default="INTERNAL",
        ),
    )
    op.add_column("workflows", sa.Column("repository_scope", sa.String(length=500), nullable=True))
    op.add_column(
        "workflows",
        sa.Column("inspection_signals", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.alter_column("workflows", "complexity_tier", server_default=None)
    op.alter_column("workflows", "data_classification", server_default=None)
    op.alter_column("workflows", "inspection_signals", server_default=None)
    op.add_column("tasks", sa.Column("work_capability", sa.String(length=64), nullable=True))
    op.execute(
        """
        UPDATE tasks SET work_capability = CASE kind
            WHEN 'PLAN' THEN 'PLANNING'
            WHEN 'ARCHITECTURE_REVIEW' THEN 'ARCHITECTURE'
            WHEN 'IMPLEMENT' THEN 'CODE_GENERATION'
            WHEN 'TEST' THEN 'TEST_EXECUTION'
            WHEN 'SECURITY_REVIEW' THEN 'SECURITY_ANALYSIS'
            WHEN 'CODE_REVIEW' THEN 'CODE_REVIEW'
        END
        """
    )
    op.alter_column("tasks", "work_capability", nullable=False)

    op.create_table(
        "routing_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("ranked_candidates", sa.JSON(), nullable=False),
        sa.Column("rejected_candidates", sa.JSON(), nullable=False),
        sa.Column("selected_provider_id", sa.String(length=128), nullable=True),
        sa.Column("selected_provider_family", sa.String(length=128), nullable=True),
        sa.Column("selected_model_version", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], ["task_attempts.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id"),
    )
    op.create_index("ix_routing_records_attempt_id", "routing_records", ["attempt_id"])
    op.create_index("ix_routing_records_task_id", "routing_records", ["task_id"])
    op.create_index("ix_routing_records_workflow_id", "routing_records", ["workflow_id"])

    op.create_table(
        "provider_observations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("provider_family", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("profile_version", sa.String(length=64), nullable=False),
        sa.Column("work_capability", sa.String(length=64), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("validation_passed", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], ["task_attempts.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id"),
    )
    op.create_index("ix_provider_observations_attempt_id", "provider_observations", ["attempt_id"])
    op.create_index(
        "ix_provider_observations_provider_id", "provider_observations", ["provider_id"]
    )
    op.create_index("ix_provider_observations_task_id", "provider_observations", ["task_id"])
    op.create_index(
        "ix_provider_observations_work_capability",
        "provider_observations",
        ["work_capability"],
    )
    op.create_index(
        "ix_provider_observations_workflow_id", "provider_observations", ["workflow_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_provider_observations_workflow_id", table_name="provider_observations")
    op.drop_index("ix_provider_observations_work_capability", table_name="provider_observations")
    op.drop_index("ix_provider_observations_task_id", table_name="provider_observations")
    op.drop_index("ix_provider_observations_provider_id", table_name="provider_observations")
    op.drop_index("ix_provider_observations_attempt_id", table_name="provider_observations")
    op.drop_table("provider_observations")
    op.drop_index("ix_routing_records_workflow_id", table_name="routing_records")
    op.drop_index("ix_routing_records_task_id", table_name="routing_records")
    op.drop_index("ix_routing_records_attempt_id", table_name="routing_records")
    op.drop_table("routing_records")
    op.drop_column("tasks", "work_capability")
    op.drop_column("workflows", "inspection_signals")
    op.drop_column("workflows", "repository_scope")
    op.drop_column("workflows", "data_classification")
    op.drop_column("workflows", "complexity_tier")
