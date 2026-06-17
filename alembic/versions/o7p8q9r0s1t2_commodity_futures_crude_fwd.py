"""Add crude oil forward curve columns to commodity_futures.

Taxonomy 8.1 explicitly requires futures_3m / futures_6m / futures_12m for crude.
Brent forward prices are the primary signal for whether the crude cost pressure on
polyester yarn will persist or reverse — contango means the market expects prices
to stay high, backwardation means they expect a decline.

Source: EIA API v2 (/petroleum/pri/fut/data/) — free with registration.
Series: RCLC3 / RCLC6 / RCLC12 (WTI) and RBRTEC3 / RBRTEC6 / RBRTEC12 (Brent).

Revision ID: o7p8q9r0s1t2
Revises: n6o7p8q9r0s1
"""
from alembic import op
import sqlalchemy as sa

revision = "o7p8q9r0s1t2"
down_revision = "n6o7p8q9r0s1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("commodity_futures") as batch_op:
        # Brent forward prices (USD/barrel, ICE futures via EIA API)
        batch_op.add_column(sa.Column("brent_3m_fwd", sa.Numeric(10, 4), nullable=True))
        batch_op.add_column(sa.Column("brent_6m_fwd", sa.Numeric(10, 4), nullable=True))
        batch_op.add_column(sa.Column("brent_12m_fwd", sa.Numeric(10, 4), nullable=True))
        # WTI forward prices (USD/barrel, NYMEX via EIA API)
        batch_op.add_column(sa.Column("wti_3m_fwd", sa.Numeric(10, 4), nullable=True))
        batch_op.add_column(sa.Column("wti_6m_fwd", sa.Numeric(10, 4), nullable=True))
        batch_op.add_column(sa.Column("wti_12m_fwd", sa.Numeric(10, 4), nullable=True))
        # Derived: contango/backwardation signal vs brent spot from crude_oil table
        # Positive = contango (market expects higher prices → polyester cost pressure persists)
        # Negative = backwardation (market expects lower prices → cost pressure transient)
        batch_op.add_column(sa.Column("brent_3m_contango_pct", sa.Numeric(6, 2), nullable=True))
        batch_op.add_column(sa.Column("brent_12m_contango_pct", sa.Numeric(6, 2), nullable=True))
        # Signal enum for synthesis engine: 'contango' | 'flat' | 'backwardation'
        batch_op.add_column(sa.Column("crude_curve_signal", sa.String(20), nullable=True))
        # Separate source tag for crude futures rows (may differ from cotton futures source)
        batch_op.add_column(sa.Column("crude_source", sa.String(50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("commodity_futures") as batch_op:
        for col in [
            "brent_3m_fwd", "brent_6m_fwd", "brent_12m_fwd",
            "wti_3m_fwd", "wti_6m_fwd", "wti_12m_fwd",
            "brent_3m_contango_pct", "brent_12m_contango_pct",
            "crude_curve_signal", "crude_source",
        ]:
            batch_op.drop_column(col)
