"""fx_rates: add eur_usd and gbp_usd columns

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-06-15

These columns are required for UK buyer (Classic Fashion) and EU buyer value-flow
calculations. Sourced from FRED DEXUSEU (EUR/USD) and DEXUSUK (GBP/USD).
Quoted as USD per 1 foreign unit, so gbp_usd ≈ 1.26, eur_usd ≈ 1.08.
"""

from alembic import op
import sqlalchemy as sa

revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("fx_rates") as batch_op:
        batch_op.add_column(sa.Column("eur_usd", sa.Numeric(10, 6), nullable=True))
        batch_op.add_column(sa.Column("gbp_usd", sa.Numeric(10, 6), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("fx_rates") as batch_op:
        batch_op.drop_column("gbp_usd")
        batch_op.drop_column("eur_usd")
