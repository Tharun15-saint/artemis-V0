"""Add cftc_cotton_cot table for CFTC Commitments of Traders positioning data.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa

revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cftc_cotton_cot",
        sa.Column("cot_id", sa.Integer(), primary_key=True),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("report_week", sa.String(20)),

        sa.Column("open_interest", sa.Integer()),

        sa.Column("noncomm_long", sa.Integer()),
        sa.Column("noncomm_short", sa.Integer()),
        sa.Column("noncomm_spreading", sa.Integer()),
        sa.Column("noncomm_net", sa.Integer()),
        sa.Column("noncomm_net_pct_oi", sa.Numeric(6, 2)),

        sa.Column("comm_long", sa.Integer()),
        sa.Column("comm_short", sa.Integer()),
        sa.Column("comm_net", sa.Integer()),

        sa.Column("nonrept_long", sa.Integer()),
        sa.Column("nonrept_short", sa.Integer()),

        sa.Column("traders_noncomm_long", sa.Integer()),
        sa.Column("traders_noncomm_short", sa.Integer()),
        sa.Column("traders_comm_long", sa.Integer()),
        sa.Column("traders_comm_short", sa.Integer()),
        sa.Column("traders_total", sa.Integer()),

        sa.Column("chg_open_interest", sa.Integer()),
        sa.Column("chg_noncomm_long", sa.Integer()),
        sa.Column("chg_noncomm_short", sa.Integer()),
        sa.Column("chg_noncomm_net", sa.Integer()),
        sa.Column("chg_comm_long", sa.Integer()),
        sa.Column("chg_comm_short", sa.Integer()),

        sa.Column("pct_oi_noncomm_long", sa.Numeric(6, 2)),
        sa.Column("pct_oi_noncomm_short", sa.Numeric(6, 2)),
        sa.Column("pct_oi_comm_long", sa.Numeric(6, 2)),
        sa.Column("pct_oi_comm_short", sa.Numeric(6, 2)),

        sa.Column("source", sa.String(100), nullable=False, server_default="cftc_socrata"),
        sa.Column("data_source_url", sa.String(500)),
        sa.Column("pulled_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("is_latest", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_cftc_cotton_cot_report_date", "cftc_cotton_cot", ["report_date"])


def downgrade() -> None:
    op.drop_index("ix_cftc_cotton_cot_report_date", "cftc_cotton_cot")
    op.drop_table("cftc_cotton_cot")
