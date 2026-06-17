"""Add aggregation_period to crude_oil.

Distinguishes World Bank Pink Sheet rows (monthly averages, 1960–present)
from FRED API rows (weekly end-of-period, current-month gap-fill).
Downstream models must not treat these two row types identically — a monthly
average smooths intra-month volatility that a weekly EOP preserves.

Revision ID: n6o7p8q9r0s1
Revises: m5n6o7p8q9r0
"""
from alembic import op
import sqlalchemy as sa

revision = "n6o7p8q9r0s1"
down_revision = "m5n6o7p8q9r0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("crude_oil") as batch_op:
        batch_op.add_column(
            sa.Column(
                "aggregation_period",
                sa.String(10),
                nullable=True,
                comment="monthly (World Bank Pink Sheet) | weekly (FRED API end-of-period)",
            )
        )

    # Backfill existing rows from their source field.
    # 'fred_api' rows are weekly EOP; everything else is World Bank monthly averages.
    op.execute(
        "UPDATE crude_oil SET aggregation_period = 'weekly' WHERE source = 'fred_api'"
    )
    op.execute(
        "UPDATE crude_oil SET aggregation_period = 'monthly' WHERE source != 'fred_api'"
    )


def downgrade() -> None:
    with op.batch_alter_table("crude_oil") as batch_op:
        batch_op.drop_column("aggregation_period")
