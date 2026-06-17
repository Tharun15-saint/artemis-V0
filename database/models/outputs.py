from sqlalchemy import Column, Integer, String, Numeric, Boolean, Date, DateTime, Text
from sqlalchemy.sql import func
from database.base import Base


class CurrentLandedCostPerDozen(Base):
    __tablename__ = "current_landed_cost_per_dozen"
    output_id            = Column(Integer, primary_key=True)
    spec_id              = Column(Integer)
    corridor             = Column(String(100))
    landed_cost_doz      = Column(Numeric(10, 4))
    fob_component        = Column(Numeric(10, 4))
    freight_component    = Column(Numeric(10, 4))
    duty_component       = Column(Numeric(10, 4))
    insurance_component  = Column(Numeric(10, 4))
    market_benchmark_doz = Column(Numeric(10, 4))
    variance_pct         = Column(Numeric(6, 2))
    data_quality_score   = Column(Numeric(5, 2))
    as_of_date           = Column(Date)
    model_version        = Column(String(64), nullable=False, server_default="1.0.0")
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at           = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class ForwardLandedCost90Day(Base):
    __tablename__ = "forward_landed_cost_90day"
    output_id            = Column(Integer, primary_key=True)
    spec_id              = Column(Integer)
    corridor             = Column(String(100))
    p10                  = Column(Numeric(10, 4))
    p50                  = Column(Numeric(10, 4))
    p90                  = Column(Numeric(10, 4))
    dominant_risk_factor = Column(String(255))
    as_of_date           = Column(Date)
    model_version        = Column(String(64), nullable=False, server_default="1.0.0")
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at           = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class MostCostEffectiveCorridor(Base):
    __tablename__ = "most_cost_effective_corridor"
    output_id             = Column(Integer, primary_key=True)
    spec_id               = Column(Integer)
    best_corridor         = Column(String(100))
    cost_differential_pct = Column(Numeric(6, 2))
    second_best_corridor  = Column(String(100))
    key_driver            = Column(String(255))
    as_of_date            = Column(Date)
    model_version         = Column(String(20), nullable=False, server_default="1.0.0")
    created_at            = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at            = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class CommodityRiskInOpenPrograms(Base):
    __tablename__ = "commodity_risk_in_open_programs"
    output_id                  = Column(Integer, primary_key=True)
    program_id                 = Column(Integer)
    corridor                   = Column(String(100))
    commodity_exposure_usd     = Column(Numeric(12, 2))
    cotton_sensitivity_10pct   = Column(Numeric(10, 4))
    freight_sensitivity_10pct  = Column(Numeric(10, 4))
    fx_sensitivity_5pct        = Column(Numeric(10, 4))
    total_risk_usd             = Column(Numeric(12, 2))
    risk_rating                = Column(String(50))
    as_of_date                 = Column(Date)
    model_version              = Column(String(20), nullable=False, server_default="1.0.0")
    created_at                 = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                 = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class HedgeOpportunityRecommendation(Base):
    __tablename__ = "hedge_opportunity_recommendation"
    output_id             = Column(Integer, primary_key=True)
    program_id            = Column(Integer)
    commodity             = Column(String(50))
    tenor_months          = Column(Integer)
    recommended_action    = Column(String(255))
    potential_saving_doz  = Column(Numeric(10, 4))
    risk_if_unhedged      = Column(Numeric(12, 2))
    confidence_score      = Column(Numeric(5, 2))
    as_of_date            = Column(Date)
    model_version         = Column(String(20), nullable=False, server_default="1.0.0")
    created_at            = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at            = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class Top5CompetitorSourcing(Base):
    __tablename__ = "top5_competitor_sourcing"
    output_id        = Column(Integer, primary_key=True)
    importer_name    = Column(String(255))
    from_corridor    = Column(String(100))
    to_corridor      = Column(String(100))
    volume_shift_pct = Column(Numeric(6, 2))
    date_detected    = Column(Date)
    confidence_score = Column(Numeric(5, 2))
    signal_source    = Column(String(255))
    as_of_date       = Column(Date)
    model_version    = Column(String(20), nullable=False, server_default="1.0.0")
    created_at       = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at       = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class RetailerDemandForecast(Base):
    __tablename__ = "retailer_demand_forecast"
    output_id            = Column(Integer, primary_key=True)
    retailer_id          = Column(Integer)
    buying_volume_signal = Column(String(255))
    store_count_trend    = Column(String(255))
    unit_growth_pct      = Column(Numeric(6, 2))
    category_focus       = Column(String(255))
    confidence_score     = Column(Numeric(5, 2))
    as_of_date           = Column(Date)
    fiscal_year_latest   = Column(Integer)
    fiscal_quarter_latest = Column(Integer)
    model_version        = Column(String(64), nullable=False, server_default="1.0.0")
    metadata_json        = Column(Text)
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at           = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class TariffExposureAnalysis(Base):
    __tablename__ = "tariff_exposure_analysis"
    output_id             = Column(Integer, primary_key=True)
    spec_id               = Column(Integer)
    current_corridor      = Column(String(100))
    current_duty_rate_pct = Column(Numeric(6, 2))
    alt_corridor_1        = Column(String(100))
    duty_rate_alt_1_pct   = Column(Numeric(6, 2))
    saving_pct_alt_1      = Column(Numeric(6, 2))
    alt_corridor_2        = Column(String(100))
    duty_rate_alt_2_pct   = Column(Numeric(6, 2))
    saving_pct_alt_2      = Column(Numeric(6, 2))
    as_of_date            = Column(Date)
    model_version         = Column(String(20), nullable=False, server_default="1.0.0")
    created_at            = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at            = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class FactoryFinancingImpact(Base):
    __tablename__ = "factory_financing_impact"
    output_id             = Column(Integer, primary_key=True)
    country_a             = Column(String(100))
    country_b             = Column(String(100))
    rate_a_pct            = Column(Numeric(6, 2))
    rate_b_pct            = Column(Numeric(6, 2))
    rate_diff_pct         = Column(Numeric(6, 2))
    impact_per_dozen_usd  = Column(Numeric(10, 4))
    annualised_impact_usd = Column(Numeric(12, 2))
    implication           = Column(String(255))
    as_of_date            = Column(Date)
    model_version         = Column(String(20), nullable=False, server_default="1.0.0")
    created_at            = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at            = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class FactoryCapacityConstraints(Base):
    __tablename__ = "factory_capacity_constraints"
    output_id              = Column(Integer, primary_key=True)
    factory_id             = Column(Integer)
    corridor               = Column(String(100))
    constraint_type        = Column(String(255))
    severity_score         = Column(Numeric(5, 2))
    lead_time_change_wks   = Column(Integer)
    affected_product_types = Column(String(255))
    implication            = Column(String(255))
    as_of_date             = Column(Date)
    model_version          = Column(String(20), nullable=False, server_default="1.0.0")
    created_at             = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at             = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class OtdRiskScorePerProgram(Base):
    __tablename__ = "otd_risk_score_per_program"
    output_id           = Column(Integer, primary_key=True)
    program_id          = Column(Integer)
    risk_score          = Column(Numeric(5, 2))
    sector_utilisation  = Column(Numeric(5, 2))
    red_sea_transit_add = Column(Integer)
    cmt_days_remaining  = Column(Integer)
    risk_factors        = Column(String(255))
    as_of_date          = Column(Date)
    model_version       = Column(String(20), nullable=False, server_default="1.0.0")
    created_at          = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at          = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class FreightBookingWindow(Base):
    __tablename__ = "freight_booking_window"
    output_id            = Column(Integer, primary_key=True)
    program_id           = Column(Integer)
    recommended_book_by  = Column(Date)
    current_rate_usd     = Column(Numeric(12, 2))
    rate_trend           = Column(String(50))
    urgency              = Column(String(50))
    as_of_date           = Column(Date)
    model_version        = Column(String(64), nullable=False, server_default="1.0.0")
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at           = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class ScfOpportunityPerFactory(Base):
    __tablename__ = "scf_opportunity_per_factory"
    output_id                  = Column(Integer, primary_key=True)
    factory_id                 = Column(Integer)
    program_id                 = Column(Integer)
    current_financing_rate_pct = Column(Numeric(6, 2))
    scf_rate_offered_pct       = Column(Numeric(6, 2))
    saving_per_dozen           = Column(Numeric(10, 4))
    total_saving_program       = Column(Numeric(12, 2))
    eligible                   = Column(Boolean)
    as_of_date                 = Column(Date)
    model_version              = Column(String(20), nullable=False, server_default="1.0.0")
    created_at                 = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                 = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class CompetitorFactoryIntel(Base):
    __tablename__ = "competitor_factory_intel"
    output_id             = Column(Integer, primary_key=True)
    factory_name          = Column(String(255))
    gaining_from_importer = Column(String(255))
    volume_trend          = Column(String(255))
    corridors_active      = Column(String(255))
    qualify_recommended   = Column(Boolean)
    confidence_score      = Column(Numeric(5, 2))
    as_of_date            = Column(Date)
    model_version         = Column(String(20), nullable=False, server_default="1.0.0")
    created_at            = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at            = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class ProgramPnlWithLevers(Base):
    __tablename__ = "program_pnl_with_levers"
    output_id                  = Column(Integer, primary_key=True)
    program_id                 = Column(Integer)
    landed_cost_current        = Column(Numeric(12, 2))
    retail_price               = Column(Numeric(12, 2))
    margin_current_pct         = Column(Numeric(6, 2))
    hedge_saving_available     = Column(Numeric(12, 2))
    scf_saving_available       = Column(Numeric(12, 2))
    freight_saving_available   = Column(Numeric(12, 2))
    corridor_shift_saving      = Column(Numeric(12, 2))
    margin_with_all_levers_pct = Column(Numeric(6, 2))
    priority_lever             = Column(String(255))
    as_of_date                 = Column(Date)
    model_version              = Column(String(20), nullable=False, server_default="1.0.0")
    created_at                 = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                 = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
