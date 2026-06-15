"""yarn_forward_met_assumption

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-15 18:00:00.000000

Add yarn_forward_met_assumption to us_duty_country_effective_rate.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("us_duty_country_effective_rate", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "yarn_forward_met_assumption",
                sa.String(length=20),
                server_default="assumed_met",
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("us_duty_country_effective_rate", schema=None) as batch_op:
        batch_op.drop_column("yarn_forward_met_assumption")
