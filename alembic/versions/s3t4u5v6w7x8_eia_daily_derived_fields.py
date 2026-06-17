"""Add EIA daily derived fields to crude_oil table.

Four new columns computed from the eia_daily daily-resolution rows:
  brent_rolling_13w_avg  — 91-day rolling average Brent (USD/bbl)
  brent_t_minus_8w       — Brent spot 56 days prior (USD/bbl)
  brent_yoy_pct          — year-over-year Brent price change (%)
  wti_brent_spread       — WTI minus Brent basis spread (USD/bbl)

NOTE on wti_brent_spread: the existing brent_wti_spread_usd column holds
(brent - wti). This new column holds (wti - brent) — the opposite sign,
matching the corridor-pricing convention used in transmission calibration
(US corridor priced off WTI, Asian corridor off Brent). Kept distinct to
avoid overloading the established sign of brent_wti_spread_usd.

These complement the existing brent_rolling_4w_avg and brent_t_minus_4w columns
added in q1r2s3t4u5v6 and are only meaningful at daily resolution.

Revision ID: s3t4u5v6w7x8
Revises: r2s3t4u5v6w7
"""
from alembic import op
import sqlalchemy as sa

revision = "s3t4u5v6w7x8"
down_revision = "r2s3t4u5v6w7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("crude_oil") as batch_op:
        batch_op.add_column(sa.Column(
            "brent_rolling_13w_avg",
            sa.Numeric(10, 4),
            nullable=True,
        ))
        batch_op.add_column(sa.Column(
            "brent_t_minus_8w",
            sa.Numeric(10, 4),
            nullable=True,
        ))
        batch_op.add_column(sa.Column(
            "brent_yoy_pct",
            sa.Numeric(6, 2),
            nullable=True,
        ))
        batch_op.add_column(sa.Column(
            "wti_brent_spread",
            sa.Numeric(8, 4),
            nullable=True,
        ))


def downgrade() -> None:
    with op.batch_alter_table("crude_oil") as batch_op:
        batch_op.drop_column("brent_rolling_13w_avg")
        batch_op.drop_column("brent_t_minus_8w")
        batch_op.drop_column("brent_yoy_pct")
        batch_op.drop_column("wti_brent_spread")
