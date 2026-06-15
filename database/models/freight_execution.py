from sqlalchemy import Column, Integer, String, Numeric, Boolean, Date, DateTime
from sqlalchemy.sql import func
from database.base import Base


class Carrier(Base):
    __tablename__ = "carrier"
    carrier_id        = Column(Integer, primary_key=True)
    carrier_name      = Column(String(255))
    carrier_type      = Column(String(50))
    api_key           = Column(String(255))
    operating_routes  = Column(String(255))
    certifications    = Column(String(255))
    reliability_score = Column(Numeric(5, 2))
    avg_transit_days  = Column(Integer)
    contact_primary   = Column(String(255))
    active            = Column(Boolean)
    joined_date       = Column(Date)
    status            = Column(String(50))
    created_at        = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at        = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class CarrierNetwork(Base):
    __tablename__ = "carrier_network"
    carrier_network_id  = Column(Integer, primary_key=True)
    receives_rfqs       = Column(Boolean)
    submits_bids        = Column(Boolean)
    wins_contracts      = Column(Boolean)
    total_carriers      = Column(Integer)
    ocean_carriers      = Column(Integer)
    drayage_carriers    = Column(Integer)
    intermodal_carriers = Column(Integer)
    status              = Column(String(50))
    created_at          = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at          = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class OceanFreightRfq(Base):
    __tablename__ = "ocean_freight_rfq"
    rfq_id              = Column(Integer, primary_key=True)
    program_id          = Column(Integer)
    origin_port         = Column(String(255))
    destination_port    = Column(String(255))
    cargo_spec          = Column(String(255))
    container_size      = Column(String(50))
    ready_to_ship_date  = Column(Date)
    hs_code_id          = Column(Integer)
    estimated_weight_kg = Column(Numeric(12, 2))
    bid_deadline        = Column(Date)
    status              = Column(String(50))
    awarded_carrier_id  = Column(Integer)
    awarded_rate_usd    = Column(Numeric(12, 2))
    award_timestamp     = Column(DateTime)
    created_at          = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at          = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class CarrierBid(Base):
    __tablename__ = "carrier_bid"
    bid_id          = Column(Integer, primary_key=True)
    rfq_id          = Column(Integer, nullable=False)
    carrier_id      = Column(Integer, nullable=False)
    rate_usd        = Column(Numeric(12, 2))
    transit_days    = Column(Integer)
    vessel_schedule = Column(String(255))
    validity_hours  = Column(Integer)
    bid_status      = Column(String(50))
    bid_timestamp   = Column(DateTime)
    created_at      = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at      = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class UsDrayageRfq(Base):
    __tablename__ = "us_drayage_rfq"
    drayage_rfq_id     = Column(Integer, primary_key=True)
    program_id         = Column(Integer)
    origin_port        = Column(String(255))
    destination_dc     = Column(String(255))
    container_type     = Column(String(50))
    pickup_date        = Column(Date)
    delivery_deadline  = Column(Date)
    status             = Column(String(50))
    awarded_carrier_id = Column(Integer)
    awarded_rate_usd   = Column(Numeric(12, 2))
    created_at         = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at         = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class IntermodalRailRfq(Base):
    __tablename__ = "intermodal_rail_rfq"
    rail_rfq_id        = Column(Integer, primary_key=True)
    program_id         = Column(Integer)
    origin_port        = Column(String(255))
    destination_dc     = Column(String(255))
    distance_miles     = Column(Integer)
    container_count    = Column(Integer)
    status             = Column(String(50))
    awarded_carrier_id = Column(Integer)
    created_at         = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at         = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class OriginDrayageRfq(Base):
    __tablename__ = "origin_drayage_rfq"
    origin_drayage_id  = Column(Integer, primary_key=True)
    program_id         = Column(Integer)
    factory_location   = Column(String(255))
    origin_port        = Column(String(255))
    ready_date         = Column(Date)
    status             = Column(String(50))
    awarded_carrier_id = Column(Integer)
    created_at         = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at         = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
