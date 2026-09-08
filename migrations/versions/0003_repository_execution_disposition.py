"""Add repository execution containment and human disposition records.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "containment_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column("workflows", sa.Column("block_reason", sa.String(length=64), nullable=True))
    op.alter_column("workflows", "containment_required", server_default=None)
    op.create_table(
        "workflow_dispositions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("original_signals", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["principals.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id"),
    )
    op.create_index(
        "ix_workflow_dispositions_workflow_id", "workflow_dispositions", ["workflow_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_dispositions_workflow_id", table_name="workflow_dispositions")
    op.drop_table("workflow_dispositions")
    op.drop_column("workflows", "block_reason")
    op.drop_column("workflows", "containment_required")
