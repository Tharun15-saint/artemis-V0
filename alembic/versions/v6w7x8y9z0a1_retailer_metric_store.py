"""Canonical retailer metric store: metric_definition, retailer_metric, metric_interpretation.

The god-table refactor + medallion refined/gold layer for retail financials. Tall metric
facts keyed by (retailer, fiscal period, metric_key), a metric catalog as data, and an
archetype-aware interpretation layer. Adding a metric becomes a row, not a schema change.

Revision ID: v6w7x8y9z0a1
Revises: u5v6w7x8y9z0
"""
from alembic import op
import sqlalchemy as sa

revision = "v6w7x8y9z0a1"
down_revision = "u5v6w7x8y9z0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "metric_definition",
        sa.Column("metric_key", sa.String(60), primary_key=True),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("definition", sa.Text, nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("investor_grade", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("vision_critical", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("direction", sa.String(20)),
        sa.Column("applies_to_archetypes", sa.Text, nullable=False, server_default="all"),
        sa.Column("xbrl_concepts_json", sa.Text),
        sa.Column("derivation", sa.Text),
        sa.Column("source_priority", sa.String(40)),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "retailer_metric",
        sa.Column("retailer_metric_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("retailer_id", sa.Integer, nullable=False),
        sa.Column("metric_key", sa.String(60), nullable=False),
        sa.Column("fiscal_year", sa.Integer, nullable=False),
        sa.Column("fiscal_quarter", sa.Integer),
        sa.Column("period_end_date", sa.Date),
        sa.Column("filing_date", sa.Date),
        sa.Column("calendar_year", sa.Integer),
        sa.Column("calendar_quarter", sa.Integer),
        sa.Column("value_numeric", sa.Numeric(20, 4), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("source", sa.String(100), nullable=False, server_default="unknown"),
        sa.Column("source_concept", sa.String(120)),
        sa.Column("source_url", sa.String(500), nullable=False, server_default="unknown"),
        sa.Column("confidence", sa.Numeric(4, 2)),
        sa.Column("data_quality", sa.Text),
        sa.Column("certified", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("is_latest", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("pulled_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_retailer_metric_lookup", "retailer_metric",
                    ["retailer_id", "metric_key", "is_latest"])
    op.create_index("ix_retailer_metric_period", "retailer_metric",
                    ["retailer_id", "fiscal_year", "fiscal_quarter"])

    op.create_table(
        "metric_interpretation",
        sa.Column("interpretation_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("archetype", sa.String(40), nullable=False),
        sa.Column("metric_key", sa.String(60), nullable=False),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("benchmark_low", sa.Numeric(20, 4)),
        sa.Column("benchmark_high", sa.Numeric(20, 4)),
        sa.Column("demand_implication", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_metric_interpretation_lookup", "metric_interpretation",
                    ["archetype", "metric_key"])


def downgrade() -> None:
    op.drop_index("ix_metric_interpretation_lookup", table_name="metric_interpretation")
    op.drop_table("metric_interpretation")
    op.drop_index("ix_retailer_metric_period", table_name="retailer_metric")
    op.drop_index("ix_retailer_metric_lookup", table_name="retailer_metric")
    op.drop_table("retailer_metric")
    op.drop_table("metric_definition")
