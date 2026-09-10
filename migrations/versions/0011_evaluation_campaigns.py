"""Add durable multi-model evaluation campaigns and evidence.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_campaigns",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("prompt_contract_version", sa.String(length=128), nullable=False),
        sa.Column("work_capability", sa.String(length=64), nullable=False),
        sa.Column("required_checks", sa.JSON(), nullable=False),
        sa.Column("risk", sa.String(length=32), nullable=False),
        sa.Column("max_candidates", sa.Integer(), nullable=False),
        sa.Column("max_prompt_variants", sa.Integer(), nullable=False),
        sa.Column("max_iterations", sa.Integer(), nullable=False),
        sa.Column("max_total_cost_microunits", sa.BigInteger(), nullable=False),
        sa.Column("minimum_independent_reviews", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_iteration", sa.Integer(), nullable=False),
        sa.Column("total_cost_microunits", sa.BigInteger(), nullable=False),
        sa.Column("winner_candidate_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["principals.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_evaluation_campaign_actor_key"),
    )
    op.create_index("ix_evaluation_campaigns_workflow_id", "evaluation_campaigns", ["workflow_id"])

    op.create_table(
        "evaluation_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("winner_candidate_id", sa.String(length=128), nullable=True),
        sa.Column("ranked_candidates", sa.JSON(), nullable=False),
        sa.Column("rejected_candidates", sa.JSON(), nullable=False),
        sa.Column("total_cost_microunits", sa.BigInteger(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("routing_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["principals.id"]),
        sa.ForeignKeyConstraint(["campaign_id"], ["evaluation_campaigns.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "iteration", name="uq_evaluation_batch_iteration"),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_evaluation_batch_actor_key"),
    )
    op.create_index("ix_evaluation_batches_campaign_id", "evaluation_batches", ["campaign_id"])
    op.create_index("ix_evaluation_batches_workflow_id", "evaluation_batches", ["workflow_id"])

    op.create_table(
        "evaluation_candidates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=128), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("provider_family", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("profile_version", sa.String(length=128), nullable=False),
        sa.Column("prompt_variant_id", sa.String(length=128), nullable=False),
        sa.Column("prompt_contract_version", sa.String(length=128), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("output_digest", sa.String(length=71), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("cost_microunits", sa.BigInteger(), nullable=False),
        sa.Column("routing_score", sa.Float(), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("reviews", sa.JSON(), nullable=False),
        sa.Column("rejection_reasons", sa.JSON(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["evaluation_batches.id"]),
        sa.ForeignKeyConstraint(["campaign_id"], ["evaluation_campaigns.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "candidate_id", name="uq_evaluation_candidate_id"),
    )
    op.create_index(
        "ix_evaluation_candidates_campaign_id", "evaluation_candidates", ["campaign_id"]
    )
    op.create_index("ix_evaluation_candidates_batch_id", "evaluation_candidates", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_evaluation_candidates_batch_id", table_name="evaluation_candidates")
    op.drop_index("ix_evaluation_candidates_campaign_id", table_name="evaluation_candidates")
    op.drop_table("evaluation_candidates")
    op.drop_index("ix_evaluation_batches_workflow_id", table_name="evaluation_batches")
    op.drop_index("ix_evaluation_batches_campaign_id", table_name="evaluation_batches")
    op.drop_table("evaluation_batches")
    op.drop_index("ix_evaluation_campaigns_workflow_id", table_name="evaluation_campaigns")
    op.drop_table("evaluation_campaigns")
