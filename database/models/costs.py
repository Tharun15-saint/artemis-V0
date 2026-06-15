from sqlalchemy import Boolean, Column, Integer, String, Numeric, Date, DateTime
from sqlalchemy.sql import func
from database.base import Base


class LabourCostByCountry(Base):
    __tablename__ = "labour_cost_by_country"
    labour_cost_id          = Column(Integer, primary_key=True)
    india_tirupur           = Column(Numeric(10, 4))
    india_coimbatore        = Column(Numeric(10, 4))
    india_bangalore         = Column(Numeric(10, 4))
    bangladesh_dhaka        = Column(Numeric(10, 4))
    bangladesh_gazipur      = Column(Numeric(10, 4))
    bangladesh_chittagong   = Column(Numeric(10, 4))
    vietnam_hcmc            = Column(Numeric(10, 4))
    vietnam_hanoi           = Column(Numeric(10, 4))
    china_guangdong         = Column(Numeric(10, 4))
    china_zhejiang          = Column(Numeric(10, 4))
    turkey_istanbul         = Column(Numeric(10, 4))
    morocco_casablanca      = Column(Numeric(10, 4))
    cambodia_national       = Column(Numeric(10, 4))
    pakistan_national       = Column(Numeric(10, 4))
    effective_date          = Column(Date)
    source                  = Column(String(100), nullable=False, server_default="unknown")
    data_source_url         = Column(String(500), nullable=False, server_default="unknown")
    refresh                 = Column(String(50))
    pulled_at               = Column(DateTime, nullable=False, server_default=func.now())
    is_latest               = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at              = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at              = Column(DateTime, server_default=func.now(),
                                     onupdate=func.now(), nullable=False)


class EnergyCost(Base):
    __tablename__ = "energy_cost"
    energy_cost_id          = Column(Integer, primary_key=True)
    india_kwh_usd           = Column(Numeric(10, 4))
    bangladesh_kwh_usd      = Column(Numeric(10, 4))
    vietnam_kwh_usd         = Column(Numeric(10, 4))
    china_kwh_usd           = Column(Numeric(10, 4))
    turkey_kwh_usd          = Column(Numeric(10, 4))
    morocco_kwh_usd         = Column(Numeric(10, 4))
    cambodia_kwh_usd        = Column(Numeric(10, 4))
    pakistan_kwh_usd        = Column(Numeric(10, 4))
    effective_date          = Column(Date)
    update_frequency        = Column(String(50))
    source                  = Column(String(100), nullable=False, server_default="unknown")
    data_source_url         = Column(String(500), nullable=False, server_default="unknown")
    pulled_at               = Column(DateTime, nullable=False, server_default=func.now())
    is_latest               = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at              = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at              = Column(DateTime, server_default=func.now(),
                                     onupdate=func.now(), nullable=False)


class FactoryFinancingCost(Base):
    __tablename__ = "factory_financing_cost"
    # CRITICAL: All rates are Numeric(6,2). Never String.
    # Used in arithmetic: financing_cost_doz = fob × rate/100 × days/365
    financing_cost_id       = Column(Integer, primary_key=True)
    india_rate_pct          = Column(Numeric(6, 2))   # 11.00
    bangladesh_rate_pct     = Column(Numeric(6, 2))   # 13.00
    vietnam_rate_pct        = Column(Numeric(6, 2))   # 9.00
    china_rate_pct          = Column(Numeric(6, 2))   # 6.50
    turkey_rate_pct         = Column(Numeric(6, 2))   # 27.00
    morocco_rate_pct        = Column(Numeric(6, 2))   # 10.00
    cambodia_rate_pct       = Column(Numeric(6, 2))   # 12.00
    pakistan_rate_pct       = Column(Numeric(6, 2))   # 16.00
    source                  = Column(String(100), nullable=False, server_default="unknown")
    data_source_url         = Column(String(500), nullable=False, server_default="unknown")
    refresh                 = Column(String(50))
    pulled_at               = Column(DateTime, nullable=False, server_default=func.now())
    is_latest               = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at              = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at              = Column(DateTime, server_default=func.now(),
                                     onupdate=func.now(), nullable=False)


class TrimCost(Base):
    __tablename__ = "trim_cost"
    trim_id                 = Column(Integer, primary_key=True)
    product_type            = Column(String(255))
    labels_per_doz          = Column(Numeric(10, 4))
    buttons_zippers_doz     = Column(Numeric(10, 4))
    polybag_packaging_doz   = Column(Numeric(10, 4))
    total_trim_cost_doz     = Column(Numeric(10, 4))
    created_at              = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at              = Column(DateTime, server_default=func.now(),
                                     onupdate=func.now(), nullable=False)


class FobPriceCalculation(Base):
    __tablename__ = "fob_price_calculation"
    fob_calc_id             = Column(Integer, primary_key=True)
    spec_id                 = Column(Integer)   # FK → product_specification
    corridor                = Column(String(100))
    fabric_cost_doz         = Column(Numeric(10, 4))
    cmt_cost_doz            = Column(Numeric(10, 4))
    trim_cost_doz           = Column(Numeric(10, 4))
    overhead_doz            = Column(Numeric(10, 4))
    financing_cost_doz      = Column(Numeric(10, 4))  # Always stored separately
    fob_price_doz           = Column(Numeric(10, 4))
    confidence_score        = Column(Numeric(5, 2))
    source                  = Column(String(255))
    created_at              = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at              = Column(DateTime, server_default=func.now(),
                                     onupdate=func.now(), nullable=False)
