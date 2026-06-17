"""bunker_fuel_prices table

Revision ID: b60a895d2482
Revises: v4w5x6y7z8a9
Create Date: 2026-06-16 13:23:22.038564

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b60a895d2482'
down_revision: Union[str, Sequence[str], None] = 'v4w5x6y7z8a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bunker_fuel_prices",
        sa.Column("bunker_price_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("port", sa.String(length=100), nullable=False),
        sa.Column("port_region", sa.String(length=50), nullable=False),
        sa.Column("grade", sa.String(length=20), nullable=False),
        sa.Column("price_usd", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("price_unit", sa.String(length=20), nullable=False, server_default="USD/gallon"),
        sa.Column("is_proxy", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("proxy_basis", sa.String(length=200), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False, server_default="unknown"),
        sa.Column("source_system", sa.String(length=50), nullable=True),
        sa.Column("data_source_url", sa.String(length=500), nullable=False, server_default="unknown"),
        sa.Column("series_id", sa.String(length=60), nullable=True),
        sa.Column("data_notes", sa.Text(), nullable=True),
        sa.Column("pulled_at", sa.DateTime(), nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.Column("is_latest", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("bunker_price_id"),
    )
    op.create_index(
        "ix_bunker_fuel_port_grade_date",
        "bunker_fuel_prices",
        ["port", "grade", "as_of_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_bunker_fuel_port_grade_date", table_name="bunker_fuel_prices")
    op.drop_table("bunker_fuel_prices")
