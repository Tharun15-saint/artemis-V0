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


class BunkerFuelPrices(Base):
    """Marine/proxy bunker fuel prices — the crude→freight transmission variable.

    VLSFO (very-low-sulfur fuel oil) is what container ships actually burn and is
    the mechanical link between crude prices and freight surcharges (BAF). Real
    VLSFO assessments are paid (Platts/Argus/Ship&Bunker). Until that feed is in,
    we ingest free EIA distillate spot prices as an HONEST proxy (is_proxy=True) —
    distillates track marine fuel closely and have decades of clean weekly history,
    enough to calibrate the crude→fuel leg with real statistical power.

    Forward-compatible: real VLSFO rows just set is_proxy=False, grade='VLSFO',
    price_unit='USD/tonne'. No schema change needed when the paid feed arrives.
    """

    __tablename__ = "bunker_fuel_prices"

    bunker_price_id   = Column(Integer, primary_key=True, autoincrement=True)

    port              = Column(String(100), nullable=False)   # 'US Gulf Coast', 'Singapore'
    port_region       = Column(String(50), nullable=False)    # 'US','Asia','Europe','Middle East'
    grade             = Column(String(20), nullable=False)     # 'ULSD','VLSFO','MGO','IFO380'
    price_usd         = Column(Numeric(10, 4), nullable=False)
    price_unit        = Column(String(20), nullable=False, server_default="USD/gallon")

    is_proxy          = Column(Boolean, nullable=False, server_default="1")
    proxy_basis       = Column(String(500), nullable=True)

    as_of_date        = Column(Date, nullable=False, primary_key=True)
    source            = Column(String(100), nullable=False, server_default="unknown")
    source_system     = Column(String(50), nullable=True)      # 'eia_api','fred_api','shipandbunker'
    data_source_url   = Column(String(500), nullable=False, server_default="unknown")
    series_id         = Column(String(60), nullable=True)      # EIA/FRED series id for provenance
    data_notes        = Column(Text, nullable=True)

    pulled_at         = Column(DateTime, nullable=False, server_default=func.now())
    is_latest         = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at        = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at        = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
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
