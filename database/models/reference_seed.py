"""Reference tables used by database/seed and intelligence/synthesis."""

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database.base import Base


class MarineInsurance(Base):
    __tablename__ = "marine_insurance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    corridor = Column(String, nullable=False, index=True)
    all_risk_rate_pct_cif = Column(Numeric(10, 4), nullable=False)
    war_risk_rate_pct_cif = Column(Numeric(10, 4), nullable=False)
    total_effective_rate_pct_cif = Column(Numeric(10, 4), nullable=False)
    route_risk_level = Column(String, nullable=False)
    active_war_risk_surcharge = Column(Boolean, nullable=False)
    as_of_date = Column(Date, nullable=False, index=True)
    source = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )


class GeopoliticalRiskEvent(Base):
    __tablename__ = "geopolitical_risk_event"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_name = Column(String, nullable=False)
    event_type = Column(String, nullable=False)
    affected_region = Column(String, nullable=False)
    affected_corridors = Column(String, nullable=False)
    freight_impact_pct = Column(Numeric(10, 4), nullable=False)
    lead_time_impact_days = Column(Integer, nullable=False)
    production_disruption_risk = Column(String, nullable=False)
    risk_level = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False)
    start_date = Column(Date, nullable=False)
    expected_resolution_date = Column(Date, nullable=True)
    actual_resolution_date = Column(Date, nullable=True)
    source = Column(String, nullable=False)
    as_of_date = Column(Date, nullable=False, index=True)
    analyst_note = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )

    shipping_lane_risks = relationship("ShippingLaneRisk", back_populates="current_event")


class ShippingLaneRisk(Base):
    __tablename__ = "shipping_lane_risk"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lane_name = Column(String, nullable=False)
    corridors_affected = Column(String, nullable=False)
    current_risk_level = Column(String, nullable=False)
    is_currently_disrupted = Column(Boolean, nullable=False)
    alternative_route = Column(String, nullable=True)
    additional_transit_days = Column(Integer, nullable=True)
    additional_cost_per_40ft_usd = Column(Numeric(10, 4), nullable=True)
    current_event_id = Column(Integer, ForeignKey("geopolitical_risk_event.id"), nullable=True)
    disruption_since = Column(Date, nullable=True)
    as_of_date = Column(Date, nullable=False, index=True)
    source = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )

    current_event = relationship("GeopoliticalRiskEvent", back_populates="shipping_lane_risks")


class UsImportDutyRate(Base):
    __tablename__ = "us_import_duty_rate"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ntr_rate_6109_10_pct = Column(Numeric(10, 4), nullable=False)
    ntr_rate_6110_20_pct = Column(Numeric(10, 4), nullable=False)
    ntr_rate_6109_90_pct = Column(Numeric(10, 4), nullable=False)
    ntr_rate_6111_pct = Column(Numeric(10, 4), nullable=False)
    section_301_china_6109_10_pct = Column(Numeric(10, 4), nullable=False)
    section_301_china_6110_20_pct = Column(Numeric(10, 4), nullable=False)
    section_301_china_6109_90_pct = Column(Numeric(10, 4), nullable=False)
    gsp_status_by_country = Column(String, nullable=False)
    effective_date = Column(Date, nullable=False, index=True)
    source = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )


class GovernmentExportIncentive(Base):
    __tablename__ = "government_export_incentive"

    id = Column(Integer, primary_key=True, autoincrement=True)
    country = Column(String, nullable=False)
    program_name = Column(String, nullable=False)
    program_type = Column(String, nullable=False)
    applicable_hs_codes = Column(String, nullable=False)
    benefit_rate_pct_fob = Column(Numeric(10, 4), nullable=False)
    benefit_per_dozen_usd_estimate = Column(Numeric(10, 4), nullable=False)
    benefit_recipient = Column(String, nullable=False)
    eligibility_criteria = Column(String, nullable=True)
    processing_time_days = Column(Integer, nullable=True)
    annual_cap_usd = Column(Numeric(10, 4), nullable=True)
    is_active = Column(Boolean, nullable=False)
    effective_date = Column(Date, nullable=False, index=True)
    expiry_date = Column(Date, nullable=True)
    source = Column(String, nullable=False)
    last_verified = Column(Date, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )
