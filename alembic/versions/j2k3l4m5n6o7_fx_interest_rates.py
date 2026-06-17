"""Add fx_interest_rates table (central bank policy rates from FRED IMF-IFS)

Revision ID: j2k3l4m5n6o7
Revises: i1j2k3l4m5n6
Create Date: 2026-06-16
"""

from alembic import op
import sqlalchemy as sa

revision = "j2k3l4m5n6o7"
down_revision = "i1j2k3l4m5n6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fx_interest_rates",
        sa.Column("ir_id", sa.Integer, primary_key=True),
        sa.Column("country_code", sa.String(3), nullable=False),
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column("policy_rate_pct", sa.Numeric(7, 4)),
        sa.Column("gov_bond_1yr_pct", sa.Numeric(7, 4)),
        sa.Column("source", sa.String(50)),
        sa.Column("fred_series", sa.String(30)),
        sa.Column("pulled_at", sa.DateTime),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("country_code", "as_of_date", name="uq_ir_country_date"),
    )
    op.create_index("ix_ir_country_code", "fx_interest_rates", ["country_code"])
    op.create_index("ix_ir_as_of_date",   "fx_interest_rates", ["as_of_date"])


def downgrade() -> None:
    op.drop_table("fx_interest_rates")
