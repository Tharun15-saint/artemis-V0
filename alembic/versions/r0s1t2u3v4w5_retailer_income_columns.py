"""add operating_income_usd, net_income_usd, net_margin_pct to retailer_financials

Revision ID: r0s1t2u3v4w5
Revises: q9r0s1t2u3v4
Create Date: 2026-06-16

Profitability (operating income, net income) is a vital demand-side datapoint —
a retailer's earnings power governs its buying confidence and FOB-price tolerance.
These standard XBRL concepts (OperatingIncomeLoss, NetIncomeLoss) were never
captured. Adds the dollar columns plus a net_margin_pct to sit beside the
existing operating_margin_pct.
"""

from alembic import op
import sqlalchemy as sa

revision = "r0s1t2u3v4w5"
down_revision = "q9r0s1t2u3v4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("retailer_financials", schema=None) as batch_op:
        batch_op.add_column(sa.Column("operating_income_usd", sa.Numeric(18, 2), nullable=True))
        batch_op.add_column(sa.Column("net_income_usd", sa.Numeric(18, 2), nullable=True))
        batch_op.add_column(sa.Column("net_margin_pct", sa.Numeric(6, 4), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("retailer_financials", schema=None) as batch_op:
        batch_op.drop_column("net_margin_pct")
        batch_op.drop_column("net_income_usd")
        batch_op.drop_column("operating_income_usd")
