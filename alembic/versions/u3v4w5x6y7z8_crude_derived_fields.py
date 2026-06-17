"""Add derived analytical fields to crude_oil table.

New columns support:
  brent_rolling_4w_avg       — 4-week rolling average Brent spot (computed at ingestion).
                                Used for the dyeing premium trigger: stable signal vs noisy weekly EOP.
  brent_dyeing_premium_active — True when brent_rolling_4w_avg > $85/bbl (CRUDE_OIL_DYEING_PRESSURE_THRESHOLD).
                                The primary cost-engine trigger for elevated dyeing chemical cost premiums.
  brent_t_minus_4w            — Brent spot observed 28 days prior (±7d window).
                                The 'crude input price' feeding dyeing costs for programs being manufactured
                                TODAY — based on the observed ~4-week crude → dye chemical transmission lag.
  price_anomaly_flag          — Set True at ingestion when the new price is >3σ from the 30-day mean.
                                Human review gate before values propagate to processed layer.
  price_anomaly_sigma         — Z-score of the price relative to 30d mean (NULL when not anomalous).
                                Non-NULL only when price_anomaly_flag=True.

Source hierarchy documented:
  PRIMARY (operational signals): fred_api weekly EOP — weekly cadence, up-to-date for cost engine triggers
  ANCHOR (historical analysis):  world_bank_pink_sheet monthly — long-run calibration and model training

Revision ID: u3v4w5x6y7z8
Revises: t2u3v4w5x6y7
"""
from alembic import op
import sqlalchemy as sa

revision = "u3v4w5x6y7z8"
down_revision = "t2u3v4w5x6y7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("crude_oil") as batch_op:
        # Rolling average for dyeing premium signal (smoothed, not noisy EOP)
        batch_op.add_column(sa.Column("brent_rolling_4w_avg", sa.Numeric(10, 4), nullable=True))
        # Boolean trigger: True when rolling_4w_avg > $85/bbl
        batch_op.add_column(sa.Column("brent_dyeing_premium_active", sa.Boolean, nullable=True))
        # Brent spot 4 weeks prior — the crude price feeding current manufacturing costs
        batch_op.add_column(sa.Column("brent_t_minus_4w", sa.Numeric(10, 4), nullable=True))
        # Anomaly detection: >3σ from 30d mean
        batch_op.add_column(sa.Column("price_anomaly_flag", sa.Boolean, nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("price_anomaly_sigma", sa.Numeric(6, 3), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("crude_oil") as batch_op:
        for col in [
            "brent_rolling_4w_avg", "brent_dyeing_premium_active",
            "brent_t_minus_4w", "price_anomaly_flag", "price_anomaly_sigma",
        ]:
            batch_op.drop_column(col)
