"""Add 9-month forward columns to commodity_futures + correct source docs to EIA STEO.

Taxonomy 8.1 requires 3m/6m/9m/12m forward tenors for all commodity prices.
The previous migration (o7p8q9r0s1t2) added only 3m/6m/12m — this adds the missing
9m tenor for both Brent and WTI.

Source correction: the prior migration docstring referenced EIA RCLC/RBRTEC series
which DO NOT EXIST on EIA v2 for those tenors. The correct source is:
  EIA Short-Term Energy Outlook (STEO) — /v2/steo/data/
  Series: BREPUUS (Brent), WTIPUUS (WTI)
  Monthly forecasts extending 18 months forward, updated monthly.

Revision ID: t2u3v4w5x6y7
Revises: s1t2u3v4w5x6
"""
from alembic import op
import sqlalchemy as sa

revision = "t2u3v4w5x6y7"
down_revision = "s1t2u3v4w5x6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("commodity_futures") as batch_op:
        batch_op.add_column(sa.Column("brent_9m_fwd", sa.Numeric(10, 4), nullable=True))
        batch_op.add_column(sa.Column("wti_9m_fwd", sa.Numeric(10, 4), nullable=True))
        # WTI contango mirrors (Brent already exists from prior migration)
        batch_op.add_column(sa.Column("wti_3m_contango_pct", sa.Numeric(6, 2), nullable=True))
        batch_op.add_column(sa.Column("wti_12m_contango_pct", sa.Numeric(6, 2), nullable=True))
        # Brent 9m (12m already exists, adding 9m for complete taxonomy)
        batch_op.add_column(sa.Column("brent_9m_contango_pct", sa.Numeric(6, 2), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("commodity_futures") as batch_op:
        for col in [
            "brent_9m_fwd", "wti_9m_fwd",
            "wti_3m_contango_pct", "wti_12m_contango_pct", "brent_9m_contango_pct",
        ]:
            batch_op.drop_column(col)
