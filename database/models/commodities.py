from sqlalchemy import Boolean, Column, Integer, String, Numeric, Date, DateTime
from sqlalchemy.sql import func
from database.base import Base


class Cotton(Base):
    __tablename__ = "cotton"
    cotton_id            = Column(Integer, primary_key=True)
    origin_country       = Column(String(100), nullable=False)
    grade                = Column(String(50))
    staple_length        = Column(String(30))
    spot_price           = Column(Numeric(10, 4))
    ice_futures_near     = Column(Numeric(10, 4))
    ice_futures_3m       = Column(Numeric(10, 4))
    ice_futures_6m       = Column(Numeric(10, 4))
    ice_futures_9m       = Column(Numeric(10, 4))
    ice_futures_12m      = Column(Numeric(10, 4))
    contango_signal      = Column(Numeric(10, 4))
    wasde_forecast       = Column(Numeric(10, 4))
    wasde_ending_stocks  = Column(Numeric(14, 2))
    wasde_su_ratio_pct   = Column(Numeric(6, 2))
    crop_year            = Column(Integer)
    as_of_date           = Column(Date)
    source               = Column(String(100), nullable=False, server_default="unknown")
    data_source_url      = Column(String(500), nullable=False, server_default="unknown")
    refresh              = Column(String(50))
    pulled_at            = Column(DateTime, nullable=False, server_default=func.now())
    is_latest            = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at           = Column(DateTime, server_default=func.now(),
                                  onupdate=func.now(), nullable=False)


class CrudeOil(Base):
    __tablename__ = "crude_oil"
    crude_oil_id         = Column(Integer, primary_key=True)
    brent_spot           = Column(Numeric(10, 4))
    wti_spot             = Column(Numeric(10, 4))
    trend_30d_pct        = Column(Numeric(6, 2))
    days_since_refresh   = Column(Integer)
    as_of_date           = Column(Date)
    source               = Column(String(100), nullable=False, server_default="unknown")
    data_source_url      = Column(String(500), nullable=False, server_default="unknown")
    refresh              = Column(String(50))
    pulled_at            = Column(DateTime, nullable=False, server_default=func.now())
    is_latest            = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at           = Column(DateTime, server_default=func.now(),
                                  onupdate=func.now(), nullable=False)


class PxParaxylene(Base):
    __tablename__ = "px_paraxylene"
    px_id                = Column(Integer, primary_key=True)
    asian_spot_price     = Column(Numeric(10, 4))
    pta_price_lag_1_2w   = Column(Numeric(10, 4))
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at           = Column(DateTime, server_default=func.now(),
                                  onupdate=func.now(), nullable=False)


class Pta(Base):
    __tablename__ = "pta"
    pta_id               = Column(Integer, primary_key=True)
    chinese_spot         = Column(Numeric(10, 4))
    asian_export         = Column(Numeric(10, 4))
    polyester_chip_price = Column(Numeric(10, 4))
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at           = Column(DateTime, server_default=func.now(),
                                  onupdate=func.now(), nullable=False)


class PolyesterPetChips(Base):
    __tablename__ = "polyester_pet_chips"
    chip_id                    = Column(Integer, primary_key=True)
    chinese_spot               = Column(Numeric(10, 4))
    asian_spot                 = Column(Numeric(10, 4))
    polyester_yarn_price_lag   = Column(Numeric(10, 4))
    created_at                 = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                 = Column(DateTime, server_default=func.now(),
                                        onupdate=func.now(), nullable=False)


class ViscoseRayon(Base):
    __tablename__ = "viscose_rayon"
    viscose_id           = Column(Integer, primary_key=True)
    asian_spot_price     = Column(Numeric(10, 4))
    blended_yarn_price   = Column(Numeric(10, 4))
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at           = Column(DateTime, server_default=func.now(),
                                  onupdate=func.now(), nullable=False)
