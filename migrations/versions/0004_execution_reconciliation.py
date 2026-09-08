"""Add explicit reconciliation records for abandoned executions.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_reconciliations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["principals.id"]),
        sa.ForeignKeyConstraint(["attempt_id"], ["task_attempts.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id"),
    )
    op.create_index(
        "ix_execution_reconciliations_attempt_id",
        "execution_reconciliations",
        ["attempt_id"],
    )
    op.create_index(
        "ix_execution_reconciliations_task_id", "execution_reconciliations", ["task_id"]
    )
    op.create_index(
        "ix_execution_reconciliations_workflow_id",
        "execution_reconciliations",
        ["workflow_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_execution_reconciliations_workflow_id",
        table_name="execution_reconciliations",
    )
    op.drop_index("ix_execution_reconciliations_task_id", table_name="execution_reconciliations")
    op.drop_index(
        "ix_execution_reconciliations_attempt_id",
        table_name="execution_reconciliations",
    )
    op.drop_table("execution_reconciliations")
