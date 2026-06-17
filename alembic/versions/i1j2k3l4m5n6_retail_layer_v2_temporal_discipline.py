"""retail_layer_v2: MajorRetailers becomes identity-only, DemandSignals gains
temporal keys, seasonal_patterns seeded with industry commit windows.

Revision ID: i1j2k3l4m5n6
Revises: h9b0c1d2e3f4
Create Date: 2026-06-16
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "i1j2k3l4m5n6"
down_revision = "h9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────────
    # 1. major_retailers → pure identity table
    #    cik and ticker were already added by an earlier migration; only drop
    #    the financial snapshot columns that must not live on the identity table.
    # ──────────────────────────────────────────────────────────────────────────
    with op.batch_alter_table("major_retailers", schema=None) as batch_op:
        batch_op.drop_column("store_count")
        batch_op.drop_column("total_sales")
        batch_op.drop_column("apparel_revenue")
        batch_op.drop_column("gross_margin")
        batch_op.drop_column("inventory_turnover")
        batch_op.drop_column("forward_guidance")
        batch_op.drop_column("demand_signal_interpretation")

    # ──────────────────────────────────────────────────────────────────────────
    # 2. demand_signals → append-only temporal discipline
    #    Add fiscal_year / fiscal_quarter / period_end_date so every signal
    #    row is anchored to a specific reporting period, not overwritten.
    #    Also narrow signal VARCHAR columns to their actual valid widths.
    # ──────────────────────────────────────────────────────────────────────────
    with op.batch_alter_table("demand_signals", schema=None) as batch_op:
        batch_op.add_column(sa.Column("fiscal_year", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("fiscal_quarter", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("period_end_date", sa.Date(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "is_latest",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "source",
                sa.String(100),
                nullable=False,
                server_default=sa.text("'unknown'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "data_source_url",
                sa.String(500),
                nullable=False,
                server_default=sa.text("'unknown'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "pulled_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
        batch_op.add_column(sa.Column("revenue_growth_pct", sa.Numeric(8, 4), nullable=True))
        batch_op.add_column(sa.Column("turnover_change_pct", sa.Numeric(8, 4), nullable=True))
        batch_op.add_column(sa.Column("margin_change_pct", sa.Numeric(8, 4), nullable=True))
        # Narrow signal columns from VARCHAR(255) to actual required widths
        batch_op.alter_column("store_expansion", type_=sa.String(20), existing_nullable=True)
        batch_op.alter_column("inventory_improving", type_=sa.String(20), existing_nullable=True)
        batch_op.alter_column("margin_compression", type_=sa.String(20), existing_nullable=True)
        batch_op.alter_column("buying_volume_signal", type_=sa.String(30), existing_nullable=True)

    # ──────────────────────────────────────────────────────────────────────────
    # 3. Seed seasonal_patterns with Tirupur / Bangladesh industry commit windows
    #    These drive the retail engine's commit timing logic.
    #    Previously this table was always empty → engine used hardcoded defaults.
    # ──────────────────────────────────────────────────────────────────────────
    seasonal_table = sa.table(
        "seasonal_patterns",
        sa.column("seasonal_pattern_id", sa.Integer),
        sa.column("ss_factory_commit_window", sa.String),
        sa.column("ss_delivery_window", sa.String),
        sa.column("fw_factory_commit_window", sa.String),
        sa.column("fw_delivery_window", sa.String),
        sa.column("freight_book_lead_days", sa.Integer),
        sa.column("hedge_window_days", sa.Integer),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    now = datetime(2026, 6, 16)
    op.bulk_insert(
        seasonal_table,
        [
            {
                "seasonal_pattern_id": 1,
                "ss_factory_commit_window": "Sep-Nov",
                "ss_delivery_window": "Jan-Apr",
                "fw_factory_commit_window": "Mar-May",
                "fw_delivery_window": "Jul-Oct",
                "freight_book_lead_days": 45,
                "hedge_window_days": 90,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )


def downgrade() -> None:
    # Remove seasonal seed
    op.execute("DELETE FROM seasonal_patterns WHERE seasonal_pattern_id = 1")

    # Revert demand_signals (column drops only — narrowed types not critical to revert)
    with op.batch_alter_table("demand_signals", schema=None) as batch_op:
        batch_op.drop_column("margin_change_pct")
        batch_op.drop_column("turnover_change_pct")
        batch_op.drop_column("revenue_growth_pct")
        batch_op.drop_column("pulled_at")
        batch_op.drop_column("data_source_url")
        batch_op.drop_column("source")
        batch_op.drop_column("is_latest")
        batch_op.drop_column("period_end_date")
        batch_op.drop_column("fiscal_quarter")
        batch_op.drop_column("fiscal_year")

    # Revert major_retailers (data is gone — columns are restored empty)
    # cik and ticker pre-existed this migration and are NOT removed on downgrade.
    with op.batch_alter_table("major_retailers", schema=None) as batch_op:
        batch_op.add_column(sa.Column("demand_signal_interpretation", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("forward_guidance", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("inventory_turnover", sa.Numeric(8, 2), nullable=True))
        batch_op.add_column(sa.Column("gross_margin", sa.Numeric(6, 2), nullable=True))
        batch_op.add_column(sa.Column("apparel_revenue", sa.Numeric(14, 2), nullable=True))
        batch_op.add_column(sa.Column("total_sales", sa.Numeric(14, 2), nullable=True))
        batch_op.add_column(sa.Column("store_count", sa.Integer(), nullable=True))
