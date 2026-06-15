"""fix_missing_discipline_columns

Revision ID: 45b6d66a6ecf
Revises: a1b2c3d4e5f6
Create Date: 2026-06-09 11:41:35.215174

Adds discipline columns missed by the prior migration:
  yarn.pulled_at, yarn.is_latest
  ingestion_log.pull_started_at (migrated from pull_timestamp), ingestion_log.updated_at
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "45b6d66a6ecf"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("yarn", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "pulled_at",
                sa.DateTime(),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "is_latest",
                sa.Boolean(),
                server_default=sa.text("1"),
                nullable=False,
            )
        )

    with op.batch_alter_table("ingestion_log", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("pull_started_at", sa.DateTime(), nullable=True)
        )

    op.execute(
        "UPDATE ingestion_log SET pull_started_at = pull_timestamp "
        "WHERE pull_started_at IS NULL"
    )

    with op.batch_alter_table("ingestion_log", schema=None) as batch_op:
        batch_op.alter_column("pull_started_at", nullable=False)
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=True,
            )
        )
        batch_op.drop_column("pull_timestamp")


def downgrade() -> None:
    with op.batch_alter_table("ingestion_log", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("pull_timestamp", sa.DateTime(), nullable=True)
        )

    op.execute(
        "UPDATE ingestion_log SET pull_timestamp = pull_started_at "
        "WHERE pull_timestamp IS NULL"
    )

    with op.batch_alter_table("ingestion_log", schema=None) as batch_op:
        batch_op.alter_column("pull_timestamp", nullable=False)
        batch_op.drop_column("updated_at")
        batch_op.drop_column("pull_started_at")

    with op.batch_alter_table("yarn", schema=None) as batch_op:
        batch_op.drop_column("is_latest")
        batch_op.drop_column("pulled_at")
