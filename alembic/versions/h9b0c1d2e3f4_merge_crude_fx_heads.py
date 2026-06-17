"""Merge crude_oil_enhanced and fx_rates_khr heads

Revision ID: h9b0c1d2e3f4
Revises: a9b0c1d2e3f4, g8a9b0c1d2e3
Create Date: 2026-06-15

Merge migration: the crude_oil branch (a9b0c1d2e3f4) and the fx_rates
KHR addition (g8a9b0c1d2e3) both descend from the world_foundation_complete
branch point. This merge creates a single head going forward.
"""

from alembic import op

revision = "h9b0c1d2e3f4"
down_revision = ("a9b0c1d2e3f4", "g8a9b0c1d2e3")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
