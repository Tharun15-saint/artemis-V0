"""Add temporal/append-only discipline to polyester chain tables.

px_paraxylene, pta, polyester_pet_chips were stub tables with no as_of_date,
source, is_latest, or pulled_at. This makes them usable as time series.

The tables now support two ingestion modes:
  source='crude_derived_proxy' — computed from Brent spot using industry-calibrated
    coefficients. is_proxy=True. Useful for direction signals but NOT for precise
    cost estimation. gap_severity degrades from blocks_reasoning → degrades_accuracy.
  source='icis_weekly' — when ICIS subscription is obtained; is_proxy=False.
    At that point, KnowledgeGap status → resolved.

ViscoseRayon is intentionally excluded: viscose is wood-pulp-derived, not
crude-derived. No proxy is valid for it. Its KnowledgeGap remains blocks_reasoning.

Revision ID: p8q9r0s1t2u3
Revises: o7p8q9r0s1t2
"""
from alembic import op
import sqlalchemy as sa

revision = "p8q9r0s1t2u3"
down_revision = "o7p8q9r0s1t2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PX Paraxylene — first step in crude → polyester chain
    with op.batch_alter_table("px_paraxylene") as batch_op:
        batch_op.add_column(sa.Column("as_of_date", sa.Date(), nullable=True))
        # Canonical price column (Asian spot, USD/tonne; replaces ambiguous asian_spot_price)
        batch_op.add_column(sa.Column("spot_usd_tonne", sa.Numeric(10, 2), nullable=True))
        # Derived: px / (brent_spot * 7.33) — the processing premium over crude
        # 7.33 = barrels per tonne for naphtha-range crude
        batch_op.add_column(sa.Column("crude_to_px_ratio", sa.Numeric(6, 4), nullable=True))
        batch_op.add_column(sa.Column("brent_spot_ref", sa.Numeric(10, 4), nullable=True))
        batch_op.add_column(
            sa.Column("source", sa.String(100), nullable=False, server_default="unknown")
        )
        batch_op.add_column(
            sa.Column("data_source_url", sa.String(500), nullable=False, server_default="unknown")
        )
        # is_proxy: True when derived from crude, False when ICIS/market price
        batch_op.add_column(
            sa.Column("is_proxy", sa.Boolean(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("is_latest", sa.Boolean(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("pulled_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"))
        )

    # PTA (Purified Terephthalic Acid) — PX → PTA → chip
    with op.batch_alter_table("pta") as batch_op:
        batch_op.add_column(sa.Column("as_of_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("spot_usd_tonne", sa.Numeric(10, 2), nullable=True))
        # Derived: pta - px (the conversion spread; normally +$80–120/tonne)
        batch_op.add_column(sa.Column("px_to_pta_spread_usd", sa.Numeric(8, 2), nullable=True))
        batch_op.add_column(sa.Column("brent_spot_ref", sa.Numeric(10, 4), nullable=True))
        batch_op.add_column(
            sa.Column("source", sa.String(100), nullable=False, server_default="unknown")
        )
        batch_op.add_column(
            sa.Column("data_source_url", sa.String(500), nullable=False, server_default="unknown")
        )
        batch_op.add_column(
            sa.Column("is_proxy", sa.Boolean(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("is_latest", sa.Boolean(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("pulled_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"))
        )

    # Polyester PET chips — PTA → chip → yarn (most operationally relevant for Tirupur)
    with op.batch_alter_table("polyester_pet_chips") as batch_op:
        batch_op.add_column(sa.Column("as_of_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("spot_usd_tonne", sa.Numeric(10, 2), nullable=True))
        # Derived: chip - pta (the polymerisation spread; normally +$80–150/tonne)
        batch_op.add_column(sa.Column("pta_to_chip_spread_usd", sa.Numeric(8, 2), nullable=True))
        batch_op.add_column(sa.Column("brent_spot_ref", sa.Numeric(10, 4), nullable=True))
        batch_op.add_column(
            sa.Column("source", sa.String(100), nullable=False, server_default="unknown")
        )
        batch_op.add_column(
            sa.Column("data_source_url", sa.String(500), nullable=False, server_default="unknown")
        )
        batch_op.add_column(
            sa.Column("is_proxy", sa.Boolean(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("is_latest", sa.Boolean(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("pulled_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"))
        )


def downgrade() -> None:
    temporal_cols = [
        "as_of_date", "spot_usd_tonne", "brent_spot_ref",
        "source", "data_source_url", "is_proxy", "is_latest", "pulled_at",
    ]
    for table, extras in [
        ("px_paraxylene", ["crude_to_px_ratio"]),
        ("pta", ["px_to_pta_spread_usd"]),
        ("polyester_pet_chips", ["pta_to_chip_spread_usd"]),
    ]:
        with op.batch_alter_table(table) as batch_op:
            for col in temporal_cols + extras:
                batch_op.drop_column(col)
