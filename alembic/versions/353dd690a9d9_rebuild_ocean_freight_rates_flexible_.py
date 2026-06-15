"""rebuild_ocean_freight_rates_flexible_routes

Revision ID: 353dd690a9d9
Revises: b7c8d9e0f1a2
Create Date: 2026-06-09 16:30:00.000000

Rebuilds ocean_freight_rates with flexible origin/destination routes and
three container rate fields. Safe to drop — no production freight data yet.
DO NOT run until confirmed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "353dd690a9d9"
down_revision: Union[str, Sequence[str], None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("ocean_freight_rates")

    op.create_table(
        "ocean_freight_rates",
        sa.Column("ocean_rate_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("origin_port", sa.String(length=100), nullable=False),
        sa.Column("origin_country", sa.String(length=100), nullable=False),
        sa.Column("destination_port", sa.String(length=100), nullable=False),
        sa.Column("destination_country", sa.String(length=100), nullable=False),
        sa.Column("rate_20ft_usd", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("rate_40ft_usd", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("rate_40ft_hc_usd", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("transit_days", sa.Integer(), nullable=True),
        sa.Column("vessel_availability", sa.String(length=20), nullable=True),
        sa.Column("port_congestion_index", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column(
            "source",
            sa.String(length=100),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column(
            "data_source_url",
            sa.String(length=500),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("data_notes", sa.Text(), nullable=True),
        sa.Column(
            "pulled_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column(
            "is_latest",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
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
        sa.PrimaryKeyConstraint("ocean_rate_id"),
    )


def downgrade() -> None:
    op.drop_table("ocean_freight_rates")

    op.create_table(
        "ocean_freight_rates",
        sa.Column("ocean_rate_id", sa.Integer(), nullable=False),
        sa.Column("chittagong_la_usd", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("chennai_la_usd", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("hcmc_la_usd", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("shanghai_la_usd", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("rate_per_40ft_usd", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("transit_days", sa.Integer(), nullable=True),
        sa.Column("port_congestion_index", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=False, server_default="unknown"),
        sa.Column(
            "data_source_url",
            sa.String(length=500),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column(
            "pulled_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column(
            "is_latest",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
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
        sa.PrimaryKeyConstraint("ocean_rate_id"),
    )
