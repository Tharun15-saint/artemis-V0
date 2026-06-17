"""Add fx_forward_curve table (CIP-implied forward rates, 5 tenors per pair per date)

Revision ID: l4m5n6o7p8q9
Revises: k3l4m5n6o7p8
Create Date: 2026-06-16
"""

from alembic import op
import sqlalchemy as sa

revision = "l4m5n6o7p8q9"
down_revision = "k3l4m5n6o7p8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fx_forward_curve",
        sa.Column("fwd_id",                  sa.Integer, primary_key=True),
        sa.Column("as_of_date",              sa.Date, nullable=False),
        sa.Column("currency_pair",           sa.String(10), nullable=False),
        sa.Column("tenor_days",              sa.Integer, nullable=False),
        sa.Column("spot_rate",               sa.Numeric(14, 6), nullable=False),
        sa.Column("implied_forward_rate",    sa.Numeric(14, 6)),
        sa.Column("forward_premium_pct_ann", sa.Numeric(7, 4)),
        sa.Column("domestic_rate_pct",       sa.Numeric(7, 4)),
        sa.Column("foreign_rate_pct",        sa.Numeric(7, 4)),
        sa.Column("cip_quality",             sa.String(10)),
        sa.Column("computed_at",             sa.DateTime),
        sa.Column("created_at",              sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "as_of_date", "currency_pair", "tenor_days",
            name="uq_fwd_date_pair_tenor",
        ),
    )
    op.create_index("ix_fwd_as_of_date",    "fx_forward_curve", ["as_of_date"])
    op.create_index("ix_fwd_currency_pair", "fx_forward_curve", ["currency_pair"])


def downgrade() -> None:
    op.drop_table("fx_forward_curve")
