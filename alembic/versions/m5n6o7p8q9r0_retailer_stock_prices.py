"""retailer_stock_prices: daily equity OHLCV time series per retailer.

Adds the market-signal dimension to the retail intelligence layer — the market's
aggregate forward view of a retailer's demand trajectory, cross-referenceable
against fundamentals (retailer_financials), demand signals, and earnings-call
intelligence (retailer_intelligence_extract) by retailer_id.

Revision ID: m5n6o7p8q9r0
Revises: l4m5n6o7p8q9
Create Date: 2026-06-16
"""

import sqlalchemy as sa
from alembic import op

revision = "m5n6o7p8q9r0"
down_revision = "l4m5n6o7p8q9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retailer_stock_prices",
        sa.Column("stock_price_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("retailer_id", sa.Integer(), nullable=True),
        sa.Column("ticker", sa.String(10), nullable=True),
        sa.Column("price_date", sa.Date(), nullable=False),
        sa.Column("open_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("high_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("low_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("close_price", sa.Numeric(12, 4), nullable=False),
        sa.Column("vwap", sa.Numeric(12, 4), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("pct_change", sa.Numeric(8, 4), nullable=True),
        sa.Column("is_split_adjusted", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("data_quality", sa.String(200), nullable=True),
        sa.Column("source", sa.String(100), nullable=False, server_default="unknown"),
        sa.Column("data_source_url", sa.String(500), nullable=False, server_default="unknown"),
        sa.Column("pulled_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("is_latest", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    # Hot path: "latest close per retailer", "price series for retailer over date range"
    op.create_index(
        "ix_retailer_stock_prices_retailer_date",
        "retailer_stock_prices",
        ["retailer_id", "price_date", "is_latest"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_retailer_stock_prices_retailer_date",
        table_name="retailer_stock_prices",
    )
    op.drop_table("retailer_stock_prices")
