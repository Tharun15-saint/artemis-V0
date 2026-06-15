from sqlalchemy import Boolean, Column, Integer, String, Numeric, Date, DateTime
from sqlalchemy.sql import func
from database.base import Base


class FxRates(Base):
    __tablename__ = "fx_rates"
    fx_rate_id  = Column(Integer, primary_key=True)
    usd_inr     = Column(Numeric(10, 4))
    usd_bdt     = Column(Numeric(10, 4))
    usd_vnd     = Column(Numeric(10, 4))
    usd_cny     = Column(Numeric(10, 4))
    usd_try     = Column(Numeric(10, 4))
    usd_mad     = Column(Numeric(10, 4))
    usd_pkr     = Column(Numeric(10, 4))
    source           = Column(String(100), nullable=False, server_default="unknown")
    data_source_url  = Column(String(500), nullable=False, server_default="unknown")
    refresh     = Column(String(50))
    status      = Column(String(50))
    as_of_date  = Column(Date)
    pulled_at   = Column(DateTime, nullable=False, server_default=func.now())
    is_latest   = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at  = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class CommodityFutures(Base):
    __tablename__ = "commodity_futures"
    commodity_futures_id = Column(Integer, primary_key=True)
    ice_cotton_2_spot    = Column(Numeric(10, 4))
    ice_cotton_2_3m      = Column(Numeric(10, 4))
    ice_cotton_2_6m      = Column(Numeric(10, 4))
    ice_cotton_2_9m      = Column(Numeric(10, 4))
    ice_cotton_2_12m     = Column(Numeric(10, 4))
    ocean_freight_ffa    = Column(Numeric(10, 4))
    as_of_date           = Column(Date)
    source               = Column(String(100), nullable=False, server_default="unknown")
    data_source_url      = Column(String(500), nullable=False, server_default="unknown")
    status               = Column(String(50))
    pulled_at            = Column(DateTime, nullable=False, server_default=func.now())
    is_latest            = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at           = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class Shipper(Base):
    __tablename__ = "shipper"
    shipper_id       = Column(Integer, primary_key=True)
    factory_name     = Column(String(255))
    country          = Column(String(100))
    hs_codes_shipped = Column(String(255))
    volume_by_month  = Column(String(255))
    active_buyers    = Column(String(255))
    yoy_trend        = Column(Numeric(8, 2))
    as_of_date       = Column(Date)
    source           = Column(String(255))
    created_at       = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at       = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class Consignee(Base):
    __tablename__ = "consignee"
    consignee_id              = Column(Integer, primary_key=True)
    company_name              = Column(String(255))
    sourcing_country_mix      = Column(String(255))
    monthly_volume            = Column(String(255))
    yoy_origin_shift          = Column(String(255))
    new_factory_relationships = Column(Integer)
    as_of_date                = Column(Date)
    created_at                = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class TradeFlowSignals(Base):
    __tablename__ = "trade_flow_signals"
    trade_signal_id        = Column(Integer, primary_key=True)
    market_share_by_origin = Column(String(255))
    competitor_shifts      = Column(String(255))
    new_entrants           = Column(String(255))
    seasonal_patterns      = Column(String(255))
    created_at             = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at             = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
