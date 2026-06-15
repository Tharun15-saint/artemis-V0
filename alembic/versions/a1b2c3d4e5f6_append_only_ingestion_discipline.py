"""append_only_ingestion_discipline

Revision ID: a1b2c3d4e5f6
Revises: c6f6e9e7753e
Create Date: 2026-06-08 14:00:00.000000

Adds pulled_at / is_latest to append-only market data tables and creates ingestion_log.
DO NOT run until confirmed — migration file only.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "c6f6e9e7753e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APPEND_ONLY_TABLES = (
    "cotton",
    "crude_oil",
    "fx_rates",
    "commodity_futures",
    "ocean_freight_rates",
    "retailer_financials",
    "retailer_intelligence_extract",
    "retailer_signal_evidence",
    "labour_cost_by_country",
    "energy_cost",
    "factory_financing_cost",
    "yarn",
)


def upgrade() -> None:
    for table in APPEND_ONLY_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
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

    op.create_table(
        "ingestion_log",
        sa.Column("log_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_name", sa.String(length=100), nullable=False),
        sa.Column("pull_started_at", sa.DateTime(), nullable=False),
        sa.Column("pull_completed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("rows_attempted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_stale", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data_as_of_date", sa.Date(), nullable=True),
        sa.Column("data_source_url", sa.String(length=500), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("validation_failures", sa.Text(), nullable=True),
        sa.Column("script_version", sa.String(length=20), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("log_id"),
    )


def downgrade() -> None:
    op.drop_table("ingestion_log")
    for table in reversed(APPEND_ONLY_TABLES):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("is_latest")
            batch_op.drop_column("pulled_at")
