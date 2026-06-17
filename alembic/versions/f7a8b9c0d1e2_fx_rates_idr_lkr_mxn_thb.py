"""fx_rates: add usd_idr, usd_lkr, usd_mxn, usd_thb

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-06-15

Fills the four missing major apparel manufacturing country currencies:
  usd_idr: Indonesia — 4th largest global apparel exporter
  usd_lkr: Sri Lanka — direct Tirupur knitwear competitor (FRED DEXSLUS)
  usd_mxn: Mexico   — USMCA nearshoring rival (FRED DEXMXUS)
  usd_thb: Thailand — SE Asian apparel hub (FRED DEXTHUS)
"""

from alembic import op
import sqlalchemy as sa

revision = "f7a8b9c0d1e2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("fx_rates") as batch_op:
        batch_op.add_column(sa.Column("usd_idr", sa.Numeric(12, 2), nullable=True))
        batch_op.add_column(sa.Column("usd_lkr", sa.Numeric(10, 4), nullable=True))
        batch_op.add_column(sa.Column("usd_mxn", sa.Numeric(10, 4), nullable=True))
        batch_op.add_column(sa.Column("usd_thb", sa.Numeric(10, 4), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("fx_rates") as batch_op:
        batch_op.drop_column("usd_thb")
        batch_op.drop_column("usd_mxn")
        batch_op.drop_column("usd_lkr")
        batch_op.drop_column("usd_idr")
