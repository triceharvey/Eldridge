"""Add authoritative read-only merge confirmations.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "merge_confirmations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("proposal_id", sa.String(length=36), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("merge_commit_revision", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["actor_id"], ["principals.id"]),
        sa.ForeignKeyConstraint(["assessment_id"], ["merge_readiness_assessments.id"]),
        sa.ForeignKeyConstraint(["proposal_id"], ["pull_request_proposals.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_merge_confirmation_actor_key"),
    )
    op.create_index("ix_merge_confirmations_workflow_id", "merge_confirmations", ["workflow_id"])
    op.create_index("ix_merge_confirmations_proposal_id", "merge_confirmations", ["proposal_id"])
    op.create_index(
        "ix_merge_confirmations_assessment_id", "merge_confirmations", ["assessment_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_merge_confirmations_assessment_id", table_name="merge_confirmations")
    op.drop_index("ix_merge_confirmations_proposal_id", table_name="merge_confirmations")
    op.drop_index("ix_merge_confirmations_workflow_id", table_name="merge_confirmations")
    op.drop_table("merge_confirmations")
