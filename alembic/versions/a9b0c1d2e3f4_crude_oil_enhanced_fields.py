"""crude_oil: add spread, INR materialization, and trend columns

Revision ID: a9b0c1d2e3f4
Revises: f0a1b2c3d4e5
Create Date: 2026-06-15

Adds 4 new columns to crude_oil:

  brent_wti_spread_usd     — Brent minus WTI spot (structural signal; normally +$2-5,
                             inversion is a supply stress indicator for Asian refiners)

  brent_inr_per_barrel     — Brent spot × usd_inr materialized at ingestion time.
  wti_inr_per_barrel       — WTI spot × usd_inr materialized at ingestion time.
                             These are the entry points for the synthetic fiber cost chain:
                             crude_inr → PX paraxylene → PTA → polyester chip → yarn.
                             Materializing avoids runtime FX JOINs in every cost query.

  fx_usd_inr_at_ingestion  — The USD/INR rate used above (audit trail for reconciliation).

All columns are nullable — existing rows will have NULL until crude_oil_cleanup.py is run
to backfill them from the fx_rates table.

trend_30d_pct was already in the schema (added in initial migration) but never populated.
crude_oil_cleanup.py backfills it for all existing rows.
"""

from alembic import op
import sqlalchemy as sa

revision = "a9b0c1d2e3f4"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("crude_oil") as batch_op:
        batch_op.add_column(
            sa.Column("brent_wti_spread_usd", sa.Numeric(8, 4), nullable=True)
        )
        batch_op.add_column(
            sa.Column("brent_inr_per_barrel", sa.Numeric(12, 4), nullable=True)
        )
        batch_op.add_column(
            sa.Column("wti_inr_per_barrel", sa.Numeric(12, 4), nullable=True)
        )
        batch_op.add_column(
            sa.Column("fx_usd_inr_at_ingestion", sa.Numeric(10, 4), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("crude_oil") as batch_op:
        batch_op.drop_column("fx_usd_inr_at_ingestion")
        batch_op.drop_column("wti_inr_per_barrel")
        batch_op.drop_column("brent_inr_per_barrel")
        batch_op.drop_column("brent_wti_spread_usd")
