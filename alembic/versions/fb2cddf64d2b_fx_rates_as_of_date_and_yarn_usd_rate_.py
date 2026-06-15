"""fx_rates_as_of_date_and_yarn_usd_rate_date

Revision ID: fb2cddf64d2b
Revises: d92251dd65ff
Create Date: 2026-06-09 14:12:57.626628

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fb2cddf64d2b'
down_revision: Union[str, Sequence[str], None] = 'd92251dd65ff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('fx_rates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('as_of_date', sa.Date(), nullable=True))

    op.execute(
        "UPDATE fx_rates SET as_of_date = date(pulled_at) WHERE as_of_date IS NULL"
    )

    with op.batch_alter_table('yarn', schema=None) as batch_op:
        batch_op.add_column(sa.Column('price_per_kg_usd_rate_date', sa.Date(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('yarn', schema=None) as batch_op:
        batch_op.drop_column('price_per_kg_usd_rate_date')

    with op.batch_alter_table('fx_rates', schema=None) as batch_op:
        batch_op.drop_column('as_of_date')
