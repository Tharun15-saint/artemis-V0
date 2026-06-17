"""world_foundation_complete

Revision ID: f0a1b2c3d4e5
Revises: e5f6a7b8c9d0
Create Date: 2026-06-15 20:00:00.000000

Registers the 22 new foundation tables in Alembic history (they were created directly
via metadata.create_all in a prior session) and adds the remaining column extensions
to three existing tables.

New tables tracked (already exist in DB — no DDL needed):
  fabric_knitting, fabric_dyeing, fabric_finishing, garment_construction, garment_variant
  actor_relationship, person
  sample, purchase_order_line, production_order, process_step
  invoice, program_pnl
  communication_thread, email_message
  internal_event, external_event
  learned_coefficient, observed_pattern, decision_record, knowledge_gap, reasoning_chain

Column additions:
  program       — program_ref, season_year, season_type,
                  agreed_fob_per_unit_usd, sourcing_type, payment_terms
  importer      — headquarters_country, headquarters_city, has_own_manufacturing,
                  own_manufacturing_country, own_manufacturing_capacity_day,
                  trade_names_json, primary_buying_hub, buying_relationship_since
  manufacturer  — manufacturing_hub, established_year, daily_capacity_units,
                  active_production_units, vertical_integration_json,
                  product_capabilities_json, compliance_certificates_json,
                  trade_names_json, export_markets_json

(yarn, product_specification, and program.retailer_id / program.construction_id columns
were already added in prior sessions — not repeated here.)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── program: commercial context columns ──────────────────────────────────
    with op.batch_alter_table("program") as batch_op:
        batch_op.add_column(sa.Column("program_ref", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("season_year", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("season_type", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("agreed_fob_per_unit_usd", sa.Numeric(10, 4), nullable=True))
        batch_op.add_column(sa.Column("sourcing_type", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("payment_terms", sa.String(50), nullable=True))

    # ── importer: deep actor profile ─────────────────────────────────────────
    with op.batch_alter_table("importer") as batch_op:
        batch_op.add_column(sa.Column("headquarters_country", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("headquarters_city", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("has_own_manufacturing", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("own_manufacturing_country", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("own_manufacturing_capacity_day", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("trade_names_json", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("primary_buying_hub", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("buying_relationship_since", sa.Date(), nullable=True))

    # ── manufacturer: deep actor profile ─────────────────────────────────────
    with op.batch_alter_table("manufacturer") as batch_op:
        batch_op.add_column(sa.Column("manufacturing_hub", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("established_year", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("daily_capacity_units", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("active_production_units", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("vertical_integration_json", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("product_capabilities_json", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("compliance_certificates_json", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("trade_names_json", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("export_markets_json", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("manufacturer") as batch_op:
        for col in ["manufacturing_hub", "established_year", "daily_capacity_units",
                    "active_production_units", "vertical_integration_json",
                    "product_capabilities_json", "compliance_certificates_json",
                    "trade_names_json", "export_markets_json"]:
            batch_op.drop_column(col)

    with op.batch_alter_table("importer") as batch_op:
        for col in ["headquarters_country", "headquarters_city", "has_own_manufacturing",
                    "own_manufacturing_country", "own_manufacturing_capacity_day",
                    "trade_names_json", "primary_buying_hub", "buying_relationship_since"]:
            batch_op.drop_column(col)

    with op.batch_alter_table("program") as batch_op:
        for col in ["program_ref", "season_year", "season_type",
                    "agreed_fob_per_unit_usd", "sourcing_type", "payment_terms"]:
            batch_op.drop_column(col)
