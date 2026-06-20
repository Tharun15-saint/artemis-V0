"""Partial unique indexes on is_latest natural keys — make duplicate-latest impossible.

The append-only is_latest discipline was enforced only in application code; it has bitten
before (copy-forward duplicates). These partial unique indexes (WHERE is_latest) make a
second is_latest row for the same natural key a hard DB error. Only tables with one-latest-
per-key semantics get one (NOT retailer_intelligence_extract / _signal_evidence, which
legitimately have many rows per period).

Revision ID: w7x8y9z0a1b2
Revises: v6w7x8y9z0a1
"""
from alembic import op
import sqlalchemy as sa

revision = "w7x8y9z0a1b2"
down_revision = "v6w7x8y9z0a1"
branch_labels = None
depends_on = None

# (index_name, table, columns) — partial: WHERE is_latest
_INDEXES = [
    ("uq_retailer_financials_latest", "retailer_financials", ["retailer_id", "fiscal_year", "fiscal_quarter"]),
    ("uq_demand_signals_latest", "demand_signals", ["retailer_id", "fiscal_year", "fiscal_quarter"]),
    ("uq_retailer_stock_prices_latest", "retailer_stock_prices", ["retailer_id", "price_date"]),
    ("uq_retailer_metric_latest", "retailer_metric", ["retailer_id", "metric_key", "fiscal_year", "fiscal_quarter"]),
]


def upgrade() -> None:
    for name, table, cols in _INDEXES:
        op.create_index(name, table, cols, unique=True,
                        postgresql_where=sa.text("is_latest"),
                        sqlite_where=sa.text("is_latest"))


def downgrade() -> None:
    for name, table, _cols in _INDEXES:
        op.drop_index(name, table_name=table)
