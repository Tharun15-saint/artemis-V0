"""Add Brent futures market-provenance fields to crude_oil.

Distinguishes real ICE Brent settlement prices from EIA STEO monthly forecasts.

  brent_futures_source         — 'ice_yfinance' / 'cme_delayed' / 'steo_forecast'
  brent_futures_is_market_price — True if a real market settlement, False if forecast
  brent_futures_delay_minutes  — 0 settlement, 15 delayed, NULL for STEO forecast

Historical rows (all STEO) are backfilled in this migration to
source='steo_forecast', is_market_price=False, delay_minutes=NULL so the
provenance is never ambiguous.

Revision ID: t4u5v6w7x8y9
Revises: s3t4u5v6w7x8
"""
from alembic import op
import sqlalchemy as sa

revision = "t4u5v6w7x8y9"
down_revision = "s3t4u5v6w7x8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("crude_oil") as batch_op:
        batch_op.add_column(sa.Column("brent_futures_source", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("brent_futures_is_market_price", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("brent_futures_delay_minutes", sa.Integer(), nullable=True))

    # Backfill provenance for existing futures rows — all were EIA STEO forecast.
    op.execute("""
        UPDATE crude_oil
        SET brent_futures_source = 'steo_forecast',
            brent_futures_is_market_price = 0,
            brent_futures_delay_minutes = NULL
        WHERE source = 'eia_petroleum_futures'
          AND brent_futures_1m IS NOT NULL
    """)


def downgrade() -> None:
    with op.batch_alter_table("crude_oil") as batch_op:
        batch_op.drop_column("brent_futures_source")
        batch_op.drop_column("brent_futures_is_market_price")
        batch_op.drop_column("brent_futures_delay_minutes")
