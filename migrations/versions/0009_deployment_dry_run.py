"""Add immutable deployment plans and no-credential dry-run evidence.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workflows", sa.Column("merged_revision", sa.String(length=128), nullable=True))
    op.add_column("approvals", sa.Column("environment_id", sa.String(length=128), nullable=True))
    op.add_column("approvals", sa.Column("plan_digest", sa.String(length=64), nullable=True))

    op.create_table(
        "deployment_environments",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("classification", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("account_scope", sa.String(length=256), nullable=False),
        sa.Column("region", sa.String(length=128), nullable=False),
        sa.Column("resource_scope", sa.JSON(), nullable=False),
        sa.Column("adapter_id", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("repository", sa.String(length=500), nullable=False),
        sa.Column("base_branch", sa.String(length=200), nullable=False),
        sa.Column("required_checks", sa.JSON(), nullable=False),
        sa.Column("required_attestations", sa.JSON(), nullable=False),
        sa.Column("verification_policy", sa.JSON(), nullable=False),
        sa.Column("rollback_policy", sa.String(length=256), nullable=False),
        sa.Column("config_digest", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["principals.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "deployment_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("environment_id", sa.String(length=128), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.String(length=128), nullable=False),
        sa.Column("artifact_digests", sa.JSON(), nullable=False),
        sa.Column("operations", sa.JSON(), nullable=False),
        sa.Column("declared_impact", sa.Text(), nullable=False),
        sa.Column("verification_probes", sa.JSON(), nullable=False),
        sa.Column("rollback_reference", sa.String(length=256), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["principals.id"]),
        sa.ForeignKeyConstraint(["environment_id"], ["deployment_environments.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_deployment_plan_actor_key"),
    )
    op.create_index("ix_deployment_plans_workflow_id", "deployment_plans", ["workflow_id"])
    op.create_index("ix_deployment_plans_environment_id", "deployment_plans", ["environment_id"])
    op.create_table(
        "deployment_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("adapter_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("operation_reference", sa.String(length=256), nullable=True),
        sa.Column("simulated", sa.Boolean(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["actor_id"], ["principals.id"]),
        sa.ForeignKeyConstraint(["approval_id"], ["approvals.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["deployment_plans.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_deployment_attempt_actor_key"),
    )
    op.create_index("ix_deployment_attempts_workflow_id", "deployment_attempts", ["workflow_id"])
    op.create_index("ix_deployment_attempts_plan_id", "deployment_attempts", ["plan_id"])
    op.create_index("ix_deployment_attempts_approval_id", "deployment_attempts", ["approval_id"])
    op.create_table(
        "deployment_verifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("observed_revision", sa.String(length=128), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], ["deployment_attempts.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id"),
    )
    op.create_index(
        "ix_deployment_verifications_workflow_id", "deployment_verifications", ["workflow_id"]
    )
    op.create_index(
        "ix_deployment_verifications_attempt_id", "deployment_verifications", ["attempt_id"]
    )
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


def downgrade() -> None:
    op.drop_index("ix_deployment_rollbacks_attempt_id", table_name="deployment_rollbacks")
    op.drop_index("ix_deployment_rollbacks_workflow_id", table_name="deployment_rollbacks")
    op.drop_table("deployment_rollbacks")
    op.drop_index("ix_deployment_verifications_attempt_id", table_name="deployment_verifications")
    op.drop_index("ix_deployment_verifications_workflow_id", table_name="deployment_verifications")
    op.drop_table("deployment_verifications")
    op.drop_index("ix_deployment_attempts_approval_id", table_name="deployment_attempts")
    op.drop_index("ix_deployment_attempts_plan_id", table_name="deployment_attempts")
    op.drop_index("ix_deployment_attempts_workflow_id", table_name="deployment_attempts")
    op.drop_table("deployment_attempts")
    op.drop_index("ix_deployment_plans_environment_id", table_name="deployment_plans")
    op.drop_index("ix_deployment_plans_workflow_id", table_name="deployment_plans")
    op.drop_table("deployment_plans")
    op.drop_table("deployment_environments")
    op.drop_column("approvals", "plan_digest")
    op.drop_column("approvals", "environment_id")
    op.drop_column("workflows", "merged_revision")
