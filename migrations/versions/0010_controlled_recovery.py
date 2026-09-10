"""Add exact rollback approval and durable controlled-recovery evidence.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    rollback_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM deployment_rollbacks")
    ).scalar_one()
    if rollback_count:
        raise RuntimeError(
            "0010 cannot replace the reserved rollback table while legacy rows exist"
        )

    op.add_column(
        "approvals",
        sa.Column("deployment_attempt_id", sa.String(length=36), nullable=True),
    )
    op.create_index("ix_approvals_deployment_attempt_id", "approvals", ["deployment_attempt_id"])

    op.drop_index("ix_deployment_rollbacks_attempt_id", table_name="deployment_rollbacks")
    op.drop_index("ix_deployment_rollbacks_workflow_id", table_name="deployment_rollbacks")
    op.drop_table("deployment_rollbacks")
    op.create_table(
        "deployment_rollbacks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("rollback_reference", sa.String(length=256), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("operation_reference", sa.String(length=256), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("recovery_duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["actor_id"], ["principals.id"]),
        sa.ForeignKeyConstraint(["approval_id"], ["approvals.id"]),
        sa.ForeignKeyConstraint(["attempt_id"], ["deployment_attempts.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_deployment_rollback_actor_key"),
    )
    op.create_index("ix_deployment_rollbacks_workflow_id", "deployment_rollbacks", ["workflow_id"])
    op.create_index("ix_deployment_rollbacks_attempt_id", "deployment_rollbacks", ["attempt_id"])
    op.create_index("ix_deployment_rollbacks_approval_id", "deployment_rollbacks", ["approval_id"])


def downgrade() -> None:
    op.drop_index("ix_deployment_rollbacks_approval_id", table_name="deployment_rollbacks")
    op.drop_index("ix_deployment_rollbacks_attempt_id", table_name="deployment_rollbacks")
    op.drop_index("ix_deployment_rollbacks_workflow_id", table_name="deployment_rollbacks")
    op.drop_table("deployment_rollbacks")
    op.create_table(
        "deployment_rollbacks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["principals.id"]),
        sa.ForeignKeyConstraint(["attempt_id"], ["deployment_attempts.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deployment_rollbacks_workflow_id", "deployment_rollbacks", ["workflow_id"])
    op.create_index("ix_deployment_rollbacks_attempt_id", "deployment_rollbacks", ["attempt_id"])
    op.drop_index("ix_approvals_deployment_attempt_id", table_name="approvals")
    op.drop_column("approvals", "deployment_attempt_id")
