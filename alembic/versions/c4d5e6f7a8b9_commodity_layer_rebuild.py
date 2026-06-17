"""commodity_layer_rebuild

Revision ID: c4d5e6f7a8b9
Revises: f0a1b2c3d4e5
Create Date: 2026-06-15 21:00:00.000000

Commodity layer A+ rebuild — real data only.

New tables:
  cotton_supply_demand       — full USDA WASDE world cotton balance sheet
                               (replaces legacy broken model in database/models.py)
  cotton_region_weather      — weekly NASA POWER weather for 7 cotton-growing regions
                               (Gujarat, Vidarbha, Telangana, Andhra Pradesh, West Texas,
                                Mississippi Delta, Southeast Georgia)
  india_harvest_signal       — monthly India cotton production estimates (USDA FAS PSD + CAI)
  tirupur_yarn_market_rate   — weekly Tirupur local yarn prices (TEXPROCIL + manual upload)
                               with ICE cotton correlation context fields

Column additions to cotton:
  spot_price_inr_per_kg      — ICE spot converted to INR/kg at ingestion time using fx_rates
  fx_usd_inr_at_ingestion    — the FX rate used for the above materialization (audit trail)
  is_real_futures_data       — True if >= 3 real ICE contracts were available
  futures_contracts_available — count of real ICE contracts retrieved (0-5)
  data_quality_tier          — 'full' | 'partial' | 'spot_only'

Real-data-only policy:
  No synthetic futures curve is ever stored. When ICE contract data is unavailable,
  ice_futures_* columns are NULL and data_quality_tier = 'spot_only'.
  A NULL is honest; a synthetic price is a false training signal.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── cotton: INR materialisation and data quality fields ──────────────────
    with op.batch_alter_table("cotton") as batch_op:
        batch_op.add_column(
            sa.Column("spot_price_inr_per_kg", sa.Numeric(10, 4), nullable=True)
        )
        batch_op.add_column(
            sa.Column("fx_usd_inr_at_ingestion", sa.Numeric(10, 4), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "is_real_futures_data",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("futures_contracts_available", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("data_quality_tier", sa.String(20), nullable=True)
        )

    # ── cotton_supply_demand ─────────────────────────────────────────────────
    op.create_table(
        "cotton_supply_demand",
        sa.Column(
            "supply_demand_id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("marketing_year", sa.Integer(), nullable=False),
        sa.Column("report_month", sa.Date(), nullable=False),
        sa.Column("forecast_provider", sa.String(50), nullable=False),
        sa.Column("world_production_million_bales", sa.Numeric(10, 4), nullable=True),
        sa.Column("world_mill_use_million_bales", sa.Numeric(10, 4), nullable=True),
        sa.Column("world_exports_million_bales", sa.Numeric(10, 4), nullable=True),
        sa.Column("world_ending_stocks_million_bales", sa.Numeric(10, 4), nullable=True),
        sa.Column("world_stocks_to_use_ratio_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("us_production_million_bales", sa.Numeric(10, 4), nullable=True),
        sa.Column("us_harvested_area_thousand_acres", sa.Numeric(10, 4), nullable=True),
        sa.Column("india_production_million_bales", sa.Numeric(10, 4), nullable=True),
        sa.Column("china_production_million_bales", sa.Numeric(10, 4), nullable=True),
        sa.Column("pakistan_production_million_bales", sa.Numeric(10, 4), nullable=True),
        sa.Column("australia_production_million_bales", sa.Numeric(10, 4), nullable=True),
        sa.Column("brazil_production_million_bales", sa.Numeric(10, 4), nullable=True),
        sa.Column("west_africa_production_million_bales", sa.Numeric(10, 4), nullable=True),
        sa.Column("us_pct_planted", sa.Numeric(6, 4), nullable=True),
        sa.Column("us_crop_condition_good_excellent_pct", sa.Numeric(6, 4), nullable=True),
        sa.Column("usda_season_avg_price_cents_per_lb", sa.Numeric(10, 4), nullable=True),
        sa.Column("cotlook_a_index_cents_per_lb", sa.Numeric(10, 4), nullable=True),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("data_source_url", sa.String(500), nullable=True),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column(
            "pulled_at", sa.DateTime(), nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column("is_latest", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
    )

    # ── cotton_region_weather ────────────────────────────────────────────────
    op.create_table(
        "cotton_region_weather",
        sa.Column(
            "weather_id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("region_name", sa.String(100), nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("latitude", sa.Numeric(7, 4), nullable=False),
        sa.Column("longitude", sa.Numeric(8, 4), nullable=False),
        sa.Column("week_ending", sa.Date(), nullable=False),
        sa.Column("avg_temp_celsius", sa.Numeric(5, 2), nullable=True),
        sa.Column("max_temp_celsius", sa.Numeric(5, 2), nullable=True),
        sa.Column("min_temp_celsius", sa.Numeric(5, 2), nullable=True),
        sa.Column("total_rainfall_mm", sa.Numeric(8, 2), nullable=True),
        sa.Column("rainfall_vs_normal_pct", sa.Numeric(8, 2), nullable=True),
        sa.Column("solar_radiation_mj_m2", sa.Numeric(8, 2), nullable=True),
        sa.Column("relative_humidity_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("growing_degree_days", sa.Numeric(6, 2), nullable=True),
        sa.Column("season_assessment", sa.String(50), nullable=True),
        sa.Column("is_cotton_season", sa.Boolean(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("data_source_url", sa.String(500), nullable=True),
        sa.Column(
            "pulled_at", sa.DateTime(), nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column("is_latest", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
    )

    # ── india_harvest_signal ─────────────────────────────────────────────────
    op.create_table(
        "india_harvest_signal",
        sa.Column(
            "harvest_id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("marketing_year", sa.Integer(), nullable=False),
        sa.Column("report_month", sa.Date(), nullable=False),
        sa.Column("estimated_production_lakh_bales", sa.Numeric(8, 2), nullable=True),
        sa.Column("acreage_lakh_hectares", sa.Numeric(8, 2), nullable=True),
        sa.Column("arrivals_lakh_bales", sa.Numeric(8, 2), nullable=True),
        sa.Column("closing_stock_lakh_bales", sa.Numeric(8, 2), nullable=True),
        sa.Column("vs_previous_estimate_lakh_bales", sa.Numeric(8, 2), nullable=True),
        sa.Column("vs_last_year_production_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("season_assessment", sa.String(50), nullable=True),
        sa.Column("source_agency", sa.String(100), nullable=True),
        sa.Column("report_url", sa.String(500), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column(
            "pulled_at", sa.DateTime(), nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column("is_latest", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
    )

    # ── tirupur_yarn_market_rate ─────────────────────────────────────────────
    op.create_table(
        "tirupur_yarn_market_rate",
        sa.Column(
            "rate_id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("week_ending", sa.Date(), nullable=False),
        sa.Column("yarn_count_ne", sa.Integer(), nullable=False),
        sa.Column("spinning_method", sa.String(50), nullable=False),
        sa.Column("fibre_type", sa.String(50), nullable=False),
        sa.Column("cotton_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("price_per_kg_inr", sa.Numeric(10, 4), nullable=False),
        sa.Column("price_change_vs_prior_week_inr", sa.Numeric(10, 4), nullable=True),
        sa.Column("price_change_vs_prior_week_pct", sa.Numeric(6, 4), nullable=True),
        sa.Column("price_change_vs_4w_avg_pct", sa.Numeric(6, 4), nullable=True),
        sa.Column("ice_cotton_near_cents_lb_at_obs", sa.Numeric(10, 4), nullable=True),
        sa.Column("ice_cotton_near_inr_kg_at_obs", sa.Numeric(10, 4), nullable=True),
        sa.Column("ice_cotton_near_inr_kg_6w_prior", sa.Numeric(10, 4), nullable=True),
        sa.Column("implied_yarn_premium_over_cotton_inr", sa.Numeric(10, 4), nullable=True),
        sa.Column("observed_transmission_lag_weeks", sa.Integer(), nullable=True),
        sa.Column("data_quality", sa.String(50), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("source_url", sa.String(500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column(
            "pulled_at", sa.DateTime(), nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column("is_latest", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
    )


def downgrade() -> None:
    op.drop_table("tirupur_yarn_market_rate")
    op.drop_table("india_harvest_signal")
    op.drop_table("cotton_region_weather")
    op.drop_table("cotton_supply_demand")

    with op.batch_alter_table("cotton") as batch_op:
        batch_op.drop_column("data_quality_tier")
        batch_op.drop_column("futures_contracts_available")
        batch_op.drop_column("is_real_futures_data")
        batch_op.drop_column("fx_usd_inr_at_ingestion")
        batch_op.drop_column("spot_price_inr_per_kg")
