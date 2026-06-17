"""ORM models for tables that previously existed in the database (created by raw
Alembic migrations) but had no ORM model.

Giving them first-class models makes their data queryable through the ORM and,
critically, stops `alembic revision --autogenerate` from trying to DROP them.
Every column type, nullability, primary key, foreign key and index here is matched
exactly to the live Postgres schema so this change is purely additive — no
migration is generated and no data is touched.
"""

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.sql import func, text

from database.base import Base


class CottonPriceSeries(Base):
    """Catalogue of cotton price series (one row per series definition)."""

    __tablename__ = "cotton_price_series"

    series_id = Column(Integer, primary_key=True, autoincrement=True)
    series_code = Column(String, nullable=False)
    series_name = Column(String, nullable=False)
    price_type = Column(String, nullable=False)
    geographic_basis = Column(String, nullable=False)
    delivery_basis = Column(String)
    quality_spec = Column(String)
    currency = Column(String, server_default="USD")
    unit = Column(String, server_default="cents_per_lb")
    frequency = Column(String, nullable=False)
    source_name = Column(String, nullable=False)
    source_url = Column(String)
    free_to_access = Column(Boolean, server_default=text("true"))
    history_available_from = Column(Date)
    notes = Column(Text)
    is_active = Column(Boolean, server_default=text("true"))
    created_at = Column(DateTime, server_default=func.now())


class CottonPriceObservation(Base):
    """Append-only cotton price observations (TimescaleDB hypertable on as_of_date)."""

    __tablename__ = "cotton_price_observation"
    __table_args__ = (
        Index("ix_cpo_date", "as_of_date"),
        Index("ix_cpo_series_code_date", "series_code", "as_of_date"),
        Index("ix_cpo_series_date", "series_id", "as_of_date", "is_latest"),
    )

    observation_id = Column(Integer, primary_key=True, autoincrement=True)
    series_id = Column(Integer, ForeignKey("cotton_price_series.series_id"), nullable=False)
    series_code = Column(String, nullable=False)
    as_of_date = Column(Date, primary_key=True, nullable=False)
    price_value = Column(Numeric(10, 4))
    price_unit = Column(String, nullable=False)
    price_in_usd_cents_per_lb = Column(Numeric(10, 4))
    price_in_usd_per_kg = Column(Numeric(10, 4))
    raw_value_original_unit = Column(Numeric(10, 4))
    original_unit = Column(String)
    yoy_change_pct = Column(Numeric(8, 4))
    mom_change_pct = Column(Numeric(8, 4))
    source_document = Column(String)
    source_url = Column(String)
    data_quality = Column(String, server_default="verified")
    data_notes = Column(Text)
    is_estimate = Column(Boolean, server_default=text("false"))
    is_latest = Column(Boolean, server_default=text("true"))
    pulled_at = Column(DateTime, server_default=func.now())
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now())


class CrudeTransmissionCalibration(Base):
    """Calibrated crude→cost transmission coefficients (regression outputs)."""

    __tablename__ = "crude_transmission_calibration"

    transmission_id = Column(Integer, primary_key=True, autoincrement=True)
    cost_component = Column(String, nullable=False)
    data_source = Column(String, nullable=False)
    obs_count = Column(Integer)
    lag_weeks_empirical = Column(Numeric(6, 2))
    lag_weeks_ci_low = Column(Numeric(6, 2))
    lag_weeks_ci_high = Column(Numeric(6, 2))
    transmission_coeff = Column(Numeric(10, 6))
    r_squared = Column(Numeric(6, 4))
    brent_series_used = Column(String)
    calibration_date = Column(Date)
    invoice_date_range_start = Column(Date)
    invoice_date_range_end = Column(Date)
    is_active = Column(Boolean, nullable=False, server_default=text("false"))
    notes = Column(Text)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    p_value = Column(Numeric(8, 6))
    coeff_ci_low = Column(Numeric(12, 6))
    coeff_ci_high = Column(Numeric(12, 6))
    calibrated_from = Column(String)
    empirical_threshold = Column(Numeric(8, 2))
    threshold_f_statistic = Column(Numeric(12, 4))
    threshold_p_value = Column(Numeric(8, 6))


class OceanFreightCorridorConfig(Base):
    """Configuration of ocean-freight corridors and their derivation tier."""

    __tablename__ = "ocean_freight_corridor_config"

    corridor_id = Column(Integer, primary_key=True, autoincrement=True)
    origin_port = Column(String, nullable=False)
    origin_country = Column(String, nullable=False)
    destination_port = Column(String, nullable=False)
    destination_country = Column(String, nullable=False)
    corridor_code = Column(String, nullable=False)
    source_tier = Column(String, nullable=False)
    base_drewry_corridor = Column(String)
    differential_pct = Column(Numeric(6, 2))
    differential_source = Column(String)
    differential_calculated_on = Column(Date)
    transit_days_estimate = Column(Integer)
    notes = Column(Text)
    is_active = Column(Boolean, server_default=text("true"))
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now())


class SignalCategoryTaxonomy(Base):
    """Canonical taxonomy of retailer-signal categories."""

    __tablename__ = "signal_category_taxonomy"

    category_id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_category = Column(String, nullable=False)
    category_label = Column(String, nullable=False)
    category_description = Column(Text)
    applies_to_retailer_types = Column(String)
    supply_chain_layer = Column(String)
    created_at = Column(DateTime, server_default=func.now())


class AffectedDecisionReference(Base):
    """Reference mapping of decision codes to program layers."""

    __tablename__ = "affected_decision_reference"

    decision_code = Column(String, primary_key=True)
    decision_label = Column(String)
    decision_description = Column(Text)
    maps_to_program_layer = Column(String)
    created_at = Column(DateTime, server_default=func.now())


class QualityCheckLog(Base):
    """Log of data-quality checks and their resolution."""

    __tablename__ = "quality_check_log"

    check_id = Column(Integer, primary_key=True, autoincrement=True)
    check_name = Column(String, nullable=False)
    check_date = Column(Date, nullable=False)
    result = Column(String, nullable=False)
    details = Column(Text)
    resolved = Column(Boolean, nullable=False, server_default=text("false"))
    resolved_by = Column(String)
    resolved_at = Column(DateTime)
    resolution_note = Column(Text)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class ImporterRetailerMix(Base):
    """Which retailers an importer serves, and the revenue mix."""

    __tablename__ = "importer_retailer_mix"
    __table_args__ = (
        Index("ix_importer_retailer_mix", "importer_id", "retailer_id"),
    )

    mix_id = Column(Integer, primary_key=True, autoincrement=True)
    importer_id = Column(Integer, ForeignKey("importer.importer_id"))
    retailer_id = Column(Integer, ForeignKey("major_retailers.retailer_id"))
    revenue_share_pct = Column(Numeric(5, 2))
    program_count_current = Column(Integer)
    relationship_since = Column(Date)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now())


class RetailerSignalCorrelation(Base):
    """Cross-retailer signal correlations for a given quarter."""

    __tablename__ = "retailer_signal_correlation"
    __table_args__ = (
        Index("ix_rsc_calendar", "calendar_year", "calendar_quarter"),
        Index("ix_rsc_retailers", "retailer_a_id", "retailer_b_id"),
    )

    correlation_id = Column(Integer, primary_key=True, autoincrement=True)
    calendar_year = Column(Integer)
    calendar_quarter = Column(Integer)
    retailer_a_id = Column(Integer, ForeignKey("major_retailers.retailer_id"))
    retailer_b_id = Column(Integer, ForeignKey("major_retailers.retailer_id"))
    signal_category = Column(String)
    retailer_a_sentiment = Column(String)
    retailer_b_sentiment = Column(String)
    correlation_type = Column(String)
    artemis_implication = Column(Text)
    computed_at = Column(DateTime, server_default=func.now())
    model_version = Column(String)
