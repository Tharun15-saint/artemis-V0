from sqlalchemy import Boolean, Column, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.sql import func

from database.base import Base


class OceanFreightRates(Base):
    __tablename__ = "ocean_freight_rates"

    ocean_rate_id           = Column(Integer, primary_key=True, autoincrement=True)

    origin_port             = Column(String(100), nullable=False)
    origin_country          = Column(String(100), nullable=False)
    destination_port        = Column(String(100), nullable=False)
    destination_country     = Column(String(100), nullable=False)

    rate_20ft_usd           = Column(Numeric(10, 2))
    rate_40ft_usd           = Column(Numeric(10, 2))
    rate_40ft_hc_usd        = Column(Numeric(10, 2))

    transit_days            = Column(Integer)
    vessel_availability     = Column(String(20))
    port_congestion_index   = Column(Numeric(5, 2))

    as_of_date              = Column(Date, nullable=False)
    source                  = Column(String(100), nullable=False, server_default="unknown")
    data_source_url         = Column(String(500), nullable=False, server_default="unknown")
    data_notes              = Column(Text)

    rate_source_tier        = Column(String(30), nullable=True)
    corridor_differential_pct = Column(Numeric(6, 2), nullable=True)
    base_corridor           = Column(String(100), nullable=True)

    pulled_at               = Column(DateTime, nullable=False, server_default=func.now())
    is_latest               = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at              = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at              = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class RedSeaDisruption(Base):
    __tablename__ = "red_sea_disruption"
    disruption_id      = Column(Integer, primary_key=True)
    disruption_name    = Column(String(255))
    affected_routes    = Column(String(255))
    severity_score     = Column(Numeric(5, 2))
    extra_transit_days = Column(Integer)
    extra_cost_usd     = Column(Numeric(10, 4))
    date_resolved      = Column(Date)
    created_at         = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at         = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class LocalInlandFreight(Base):
    __tablename__ = "local_inland_freight"
    inland_freight_id            = Column(Integer, primary_key=True)
    factory_to_port_cost_country = Column(String(255))
    created_at                   = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                   = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class AirFreight(Base):
    __tablename__ = "air_freight"
    air_freight_id = Column(Integer, primary_key=True)
    rate_per_kg    = Column(Numeric(10, 4))
    key_corridors  = Column(String(255))
    created_at     = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at     = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
