"""fx governance + executability: fx_currency_config table, vol regime methodology,
forward-curve market observability

Revision ID: s1t2u3v4w5x6
Revises: r0s1t2u3v4w5
Create Date: 2026-06-16

Three additions that make the FX layer safe and self-describing for downstream
models (training + hedging):

  1. fx_currency_config — reference metadata governing which currencies matter for
     apparel sourcing, their tier, and (critically) whether a real forward market
     exists. Underscore PK (USD_INR) to join against the rest of the FX layer.

  2. fx_volatility regime methodology columns — so a regime label ('stressed') is
     traceable to its percentile definition rather than being a magic string.
     Documents the AS-BUILT bands: calm <25th, normal 25–75th, elevated 75–95th,
     stressed >95th of the trailing 3-year vol_90d_ann distribution.

  3. fx_forward_curve executability columns — distinguishes observable/tradeable
     forwards from CIP-implied theoretical rates, so the engine never recommends
     hedging at a rate that has no market (e.g. USD_BDT, USD_PKR, USD_LKR).
"""

from alembic import op
import sqlalchemy as sa

revision = "s1t2u3v4w5x6"
down_revision = "r0s1t2u3v4w5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fx_currency_config",
        sa.Column("currency_pair",            sa.String(10), primary_key=True),
        sa.Column("local_currency",           sa.String(5), nullable=False),
        sa.Column("local_currency_name",      sa.String(50), nullable=False),
        sa.Column("country",                  sa.String(50), nullable=False),
        sa.Column("manufacturing_relevance",  sa.String(20), nullable=False),
        sa.Column("sourcing_tier",            sa.Integer, nullable=False),
        sa.Column("fx_table_field",           sa.String(20)),
        sa.Column("yfinance_ticker",          sa.String(20)),
        sa.Column("fred_series",              sa.String(30)),
        sa.Column("forward_market_liquidity", sa.String(20)),
        sa.Column("classic_fashion_relevant", sa.Boolean, server_default="0"),
        sa.Column("notes",                    sa.Text),
        sa.Column("is_active",                sa.Boolean, server_default="1"),
        sa.Column("created_at",               sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "manufacturing_relevance IN ('PRIMARY','SECONDARY','MONITOR')",
            name="ck_fxcfg_relevance",
        ),
        sa.CheckConstraint("sourcing_tier IN (1,2,3)", name="ck_fxcfg_tier"),
        sa.CheckConstraint(
            "forward_market_liquidity IN ('liquid','semi_liquid','cip_implied_only')",
            name="ck_fxcfg_liquidity",
        ),
    )

    with op.batch_alter_table("fx_volatility", schema=None) as batch_op:
        batch_op.add_column(sa.Column("vol_window_days",        sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("regime_methodology",     sa.Text, nullable=True))
        batch_op.add_column(sa.Column("regime_percentile_low",  sa.Numeric(5, 2), nullable=True))
        batch_op.add_column(sa.Column("regime_percentile_high", sa.Numeric(5, 2), nullable=True))

    with op.batch_alter_table("fx_forward_curve", schema=None) as batch_op:
        batch_op.add_column(sa.Column("is_market_observable", sa.Boolean, nullable=True))
        batch_op.add_column(sa.Column("market_liquidity",     sa.String(20), nullable=True))
        batch_op.add_column(sa.Column("execution_note",       sa.Text, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("fx_forward_curve", schema=None) as batch_op:
        batch_op.drop_column("execution_note")
        batch_op.drop_column("market_liquidity")
        batch_op.drop_column("is_market_observable")

    with op.batch_alter_table("fx_volatility", schema=None) as batch_op:
        batch_op.drop_column("regime_percentile_high")
        batch_op.drop_column("regime_percentile_low")
        batch_op.drop_column("regime_methodology")
        batch_op.drop_column("vol_window_days")

    op.drop_table("fx_currency_config")
