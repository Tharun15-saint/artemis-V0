"""add business_segment to retailer_intelligence_extract for taxonomy consolidation

Revision ID: q9r0s1t2u3v4
Revises: p8q9r0s1t2u3
Create Date: 2026-06-16

The earnings-call signal taxonomy had fragmented across prompt versions into 34
categories, many of which encoded the *business segment* (walmart_us, sams_club)
inside the category name (e.g. inventory_positioning_walmart_us). This splits the
segment out into its own dimension so signal_category can collapse to a clean,
sophisticated, apparel-focused canonical set while segment granularity is preserved.
"""

from alembic import op
import sqlalchemy as sa

revision = "q9r0s1t2u3v4"
down_revision = "p8q9r0s1t2u3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("retailer_intelligence_extract", schema=None) as batch_op:
        batch_op.add_column(sa.Column("business_segment", sa.String(40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("retailer_intelligence_extract", schema=None) as batch_op:
        batch_op.drop_column("business_segment")
