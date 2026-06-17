"""fx_rates: add usd_khr (Cambodian Riel)

Revision ID: g8a9b0c1d2e3
Revises: f7a8b9c0d1e2
Create Date: 2026-06-15

Cambodia's garment sector exports ~$7B/year, making it a meaningful manufacturing
country to track. KHR is maintained by the National Bank of Cambodia at ~4,000/USD
(soft peg). Most factory wages and orders are in USD, but the peg rate matters for
local cost context and competitor analysis.
"""

from alembic import op
import sqlalchemy as sa

revision = "g8a9b0c1d2e3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("fx_rates") as batch_op:
        batch_op.add_column(sa.Column("usd_khr", sa.Numeric(10, 2), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("fx_rates") as batch_op:
        batch_op.drop_column("usd_khr")
