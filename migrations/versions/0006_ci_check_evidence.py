"""Add revision-bound CI check evidence.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ci_check_evidence",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("delivery_id", sa.String(length=128), nullable=False),
        sa.Column("repository", sa.String(length=500), nullable=False),
        sa.Column("check_run_id", sa.String(length=64), nullable=False),
        sa.Column("check_name", sa.String(length=200), nullable=False),
        sa.Column("revision", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("conclusion", sa.String(length=32), nullable=False),
        sa.Column("details_url", sa.String(length=1000), nullable=True),
        sa.Column("app_slug", sa.String(length=200), nullable=True),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "delivery_id", name="uq_ci_check_source_delivery"),
        sa.UniqueConstraint("source", "check_run_id", name="uq_ci_check_source_run"),
    )
    op.create_index(
        "ix_ci_check_evidence_workflow_id",
        "ci_check_evidence",
        ["workflow_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ci_check_evidence_workflow_id", table_name="ci_check_evidence")
    op.drop_table("ci_check_evidence")
