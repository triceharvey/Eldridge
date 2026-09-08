"""Allow separately scoped agent and integration grants on one task.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("capability_grants_task_id_key", "capability_grants", type_="unique")
    op.create_unique_constraint(
        "uq_capability_grants_principal_task",
        "capability_grants",
        ["principal_id", "task_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_capability_grants_principal_task", "capability_grants", type_="unique")
    op.create_unique_constraint("capability_grants_task_id_key", "capability_grants", ["task_id"])
