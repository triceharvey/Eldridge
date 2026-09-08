"""Add durable pull-request proposals and merge-readiness assessments.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pull_request_proposals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("repository", sa.String(length=500), nullable=False),
        sa.Column("head_branch", sa.String(length=200), nullable=False),
        sa.Column("base_branch", sa.String(length=200), nullable=False),
        sa.Column("revision", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("pull_number", sa.Integer(), nullable=True),
        sa.Column("pull_url", sa.String(length=1000), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["actor_id"], ["principals.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_id", "idempotency_key", name="uq_pull_request_actor_idempotency"
        ),
    )
    op.create_index(
        "ix_pull_request_proposals_workflow_id", "pull_request_proposals", ["workflow_id"]
    )
    op.create_table(
        "merge_readiness_assessments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("proposal_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("ready", sa.Boolean(), nullable=True),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["actor_id"], ["principals.id"]),
        sa.ForeignKeyConstraint(["proposal_id"], ["pull_request_proposals.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_id", "idempotency_key", name="uq_merge_readiness_actor_idempotency"
        ),
    )
    op.create_index(
        "ix_merge_readiness_assessments_proposal_id",
        "merge_readiness_assessments",
        ["proposal_id"],
    )
    op.create_index(
        "ix_merge_readiness_assessments_workflow_id",
        "merge_readiness_assessments",
        ["workflow_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_merge_readiness_assessments_workflow_id",
        table_name="merge_readiness_assessments",
    )
    op.drop_index(
        "ix_merge_readiness_assessments_proposal_id",
        table_name="merge_readiness_assessments",
    )
    op.drop_table("merge_readiness_assessments")
    op.drop_index("ix_pull_request_proposals_workflow_id", table_name="pull_request_proposals")
    op.drop_table("pull_request_proposals")
