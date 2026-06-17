"""Add EIA futures curve fields and data-quality flags to crude_oil.

New columns on crude_oil:
  wti_futures_1m    — WTI NYMEX 1st nearby contract (EIA RCLC1, USD/bbl)
  wti_futures_3m    — WTI NYMEX 3rd nearby contract (EIA RCLC3, USD/bbl)
  wti_futures_6m    — WTI NYMEX 4th nearby contract (EIA RCLC4, USD/bbl — 4th contract proxy)
  wti_futures_12m   — WTI 12m forward, EIA STEO WTIPUUS forecast (USD/bbl)
  brent_futures_1m  — Brent 1m forward, EIA STEO BREPUUS (USD/bbl)
  brent_futures_3m  — Brent 3m forward, EIA STEO BREPUUS (USD/bbl)
  brent_futures_6m  — Brent 6m forward, EIA STEO BREPUUS (USD/bbl)
  brent_futures_12m — Brent 12m forward, EIA STEO BREPUUS (USD/bbl)
  brent_contango_signal — (brent_futures_12m - brent_spot) / brent_spot * 100
  wti_contango_signal   — (wti_futures_12m - wti_spot) / wti_spot * 100
  crude_market_structure — 'contango' / 'backwardation' / 'flat'
                           >1.5% = contango, <-1.5% = backwardation, else flat

  data_quality_flag — VARCHAR flag set by quality audit (e.g. 'DEVIATION_FLAGGED')
  data_quality_note — Human-readable explanation of any flag

Data source:
  WTI 1m/3m: EIA RCLC1/RCLC3 (real NYMEX settlement prices, petroleum/pri/fut)
  WTI 6m:    EIA RCLC4 (4th nearby contract — longest tenor available)
  WTI 12m:   EIA STEO WTIPUUS (monthly forecast)
  Brent all: EIA STEO BREPUUS (monthly forecast — no Brent futures in EIA petroleum/pri/fut)

Revision ID: q1r2s3t4u5v6
Revises: b60a895d2482
"""
from alembic import op
import sqlalchemy as sa

revision = "q1r2s3t4u5v6"
down_revision = "b60a895d2482"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("crude_oil") as batch_op:
        # WTI futures (NYMEX settlement for 1m/3m/6m; STEO forecast for 12m)
        batch_op.add_column(sa.Column("wti_futures_1m",  sa.Numeric(10, 4), nullable=True))
        batch_op.add_column(sa.Column("wti_futures_3m",  sa.Numeric(10, 4), nullable=True))
        batch_op.add_column(sa.Column("wti_futures_6m",  sa.Numeric(10, 4), nullable=True))
        batch_op.add_column(sa.Column("wti_futures_12m", sa.Numeric(10, 4), nullable=True))
        # Brent futures (all from EIA STEO — no ICE Brent in EIA petroleum/pri/fut)
        batch_op.add_column(sa.Column("brent_futures_1m",  sa.Numeric(10, 4), nullable=True))
        batch_op.add_column(sa.Column("brent_futures_3m",  sa.Numeric(10, 4), nullable=True))
        batch_op.add_column(sa.Column("brent_futures_6m",  sa.Numeric(10, 4), nullable=True))
        batch_op.add_column(sa.Column("brent_futures_12m", sa.Numeric(10, 4), nullable=True))
        # Derived contango signals and market structure
        batch_op.add_column(sa.Column("brent_contango_signal", sa.Numeric(6, 4), nullable=True))
        batch_op.add_column(sa.Column("wti_contango_signal",   sa.Numeric(6, 4), nullable=True))
        batch_op.add_column(sa.Column("crude_market_structure", sa.String(20), nullable=True))
        # Data quality flags (for deviation audits)
        batch_op.add_column(sa.Column("data_quality_flag", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("data_quality_note", sa.Text, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("crude_oil") as batch_op:
        for col in [
            "wti_futures_1m", "wti_futures_3m", "wti_futures_6m", "wti_futures_12m",
            "brent_futures_1m", "brent_futures_3m", "brent_futures_6m", "brent_futures_12m",
            "brent_contango_signal", "wti_contango_signal", "crude_market_structure",
            "data_quality_flag", "data_quality_note",
        ]:
            batch_op.drop_column(col)
