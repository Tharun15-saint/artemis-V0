"""Add fx_volatility table (realized vol features + hedging signals per pair per week)

Revision ID: k3l4m5n6o7p8
Revises: j2k3l4m5n6o7
Create Date: 2026-06-16
"""

from alembic import op
import sqlalchemy as sa

revision = "k3l4m5n6o7p8"
down_revision = "j2k3l4m5n6o7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fx_volatility",
        sa.Column("vol_id",        sa.Integer, primary_key=True),
        sa.Column("as_of_date",    sa.Date, nullable=False),
        sa.Column("currency_pair", sa.String(10), nullable=False),
        sa.Column("spot_rate",     sa.Numeric(14, 6), nullable=False),
        # Annualized realized volatility
        sa.Column("vol_30d_ann",   sa.Numeric(7, 4)),
        sa.Column("vol_90d_ann",   sa.Numeric(7, 4)),
        sa.Column("vol_180d_ann",  sa.Numeric(7, 4)),
        sa.Column("vol_365d_ann",  sa.Numeric(7, 4)),
        # Moving averages
        sa.Column("ma_50d",        sa.Numeric(14, 6)),
        sa.Column("ma_200d",       sa.Numeric(14, 6)),
        sa.Column("above_ma_200d", sa.Boolean),
        # Momentum
        sa.Column("ret_30d",       sa.Numeric(8, 4)),
        sa.Column("ret_90d",       sa.Numeric(8, 4)),
        sa.Column("ret_180d",      sa.Numeric(8, 4)),
        sa.Column("ret_365d",      sa.Numeric(8, 4)),
        # Percentile rank
        sa.Column("pct_rank_1yr",  sa.Numeric(5, 2)),
        sa.Column("pct_rank_3yr",  sa.Numeric(5, 2)),
        sa.Column("pct_rank_5yr",  sa.Numeric(5, 2)),
        # Regime & signals
        sa.Column("vol_regime",    sa.String(10)),
        sa.Column("hedge_urgency", sa.String(10)),
        sa.Column("suggested_hedge_ratio_pct", sa.Numeric(5, 1)),
        sa.Column("computed_at",   sa.DateTime),
        sa.Column("created_at",    sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("as_of_date", "currency_pair", name="uq_vol_date_pair"),
    )
    op.create_index("ix_vol_as_of_date",    "fx_volatility", ["as_of_date"])
    op.create_index("ix_vol_currency_pair", "fx_volatility", ["currency_pair"])


def downgrade() -> None:
    op.drop_table("fx_volatility")
