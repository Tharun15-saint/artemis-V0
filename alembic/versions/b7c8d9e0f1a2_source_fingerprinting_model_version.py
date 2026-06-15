"""source_fingerprinting_model_version

Revision ID: b7c8d9e0f1a2
Revises: 45b6d66a6ecf
Create Date: 2026-06-09 12:00:00.000000

Rule 6: source + data_source_url NOT NULL on append-only tables.
Rule 7: model_version NOT NULL on intelligence output tables.
DO NOT run until confirmed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "45b6d66a6ecf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APPEND_ONLY_TABLES = (
    "cotton",
    "crude_oil",
    "fx_rates",
    "commodity_futures",
    "ocean_freight_rates",
    "retailer_financials",
    "retailer_intelligence_extract",
    "retailer_signal_evidence",
    "labour_cost_by_country",
    "energy_cost",
    "factory_financing_cost",
    "yarn",
)

OUTPUT_TABLES = (
    "current_landed_cost_per_dozen",
    "forward_landed_cost_90day",
    "most_cost_effective_corridor",
    "commodity_risk_in_open_programs",
    "hedge_opportunity_recommendation",
    "top5_competitor_sourcing",
    "retailer_demand_forecast",
    "tariff_exposure_analysis",
    "factory_financing_impact",
    "factory_capacity_constraints",
    "otd_risk_score_per_program",
    "freight_booking_window",
    "scf_opportunity_per_factory",
    "competitor_factory_intel",
    "program_pnl_with_levers",
)

TABLES_WITH_EXISTING_SOURCE = (
    "cotton",
    "crude_oil",
    "fx_rates",
    "commodity_futures",
    "ocean_freight_rates",
    "labour_cost_by_country",
    "factory_financing_cost",
    "yarn",
)

TABLES_NEEDING_SOURCE = (
    "retailer_financials",
    "retailer_intelligence_extract",
    "retailer_signal_evidence",
    "energy_cost",
)


def upgrade() -> None:
    for table in TABLES_WITH_EXISTING_SOURCE:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column(
                "source",
                existing_type=sa.String(length=255),
                type_=sa.String(length=100),
                nullable=False,
                server_default="unknown",
            )
            batch_op.add_column(
                sa.Column(
                    "data_source_url",
                    sa.String(length=500),
                    nullable=False,
                    server_default="unknown",
                )
            )

    for table in TABLES_NEEDING_SOURCE:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "source",
                    sa.String(length=100),
                    nullable=False,
                    server_default="unknown",
                )
            )
            batch_op.add_column(
                sa.Column(
                    "data_source_url",
                    sa.String(length=500),
                    nullable=False,
                    server_default="unknown",
                )
            )

    for table in OUTPUT_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column(
                "model_version",
                existing_type=sa.String(length=20),
                nullable=False,
                server_default="1.0.0",
            )


def downgrade() -> None:
    for table in reversed(OUTPUT_TABLES):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column(
                "model_version",
                existing_type=sa.String(length=20),
                nullable=True,
                server_default=None,
            )

    for table in reversed(TABLES_NEEDING_SOURCE):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("data_source_url")
            batch_op.drop_column("source")

    for table in reversed(TABLES_WITH_EXISTING_SOURCE):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("data_source_url")
            batch_op.alter_column(
                "source",
                existing_type=sa.String(length=100),
                type_=sa.String(length=255),
                nullable=True,
                server_default=None,
            )
