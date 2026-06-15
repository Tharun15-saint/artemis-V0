from sqlalchemy import Column, Integer, String, Numeric, Boolean, DateTime
from sqlalchemy.sql import func
from database.base import Base


class SpinningMills(Base):
    __tablename__ = "spinning_mills"
    spinning_mill_id    = Column(Integer, primary_key=True)
    location_country    = Column(String(100))
    location_city       = Column(String(100))
    capacity_tons_month = Column(Numeric(10, 2))
    utilisation_pct     = Column(Numeric(5, 2))
    certifications      = Column(String(255))
    lead_time_weeks     = Column(Integer)
    financing_rate      = Column(Numeric(8, 4))
    created_at          = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at          = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class KnittingMills(Base):
    __tablename__ = "knitting_mills"
    knitting_mill_id    = Column(Integer, primary_key=True)
    location            = Column(String(255))
    capacity_tons_month = Column(Numeric(10, 2))
    utilisation_pct     = Column(Numeric(5, 2))
    machine_types       = Column(String(255))
    certifications      = Column(String(255))
    created_at          = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at          = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class DyeingUnits(Base):
    __tablename__ = "dyeing_units"
    dyeing_unit_id          = Column(Integer, primary_key=True)
    location                = Column(String(255))
    capacity_tons_month     = Column(Numeric(10, 2))
    chemical_cost_structure = Column(String(255))
    energy_intensity        = Column(String(100))
    crude_sensitivity_score = Column(Numeric(5, 2))
    created_at              = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at              = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class CmtFactories(Base):
    __tablename__ = "cmt_factories"
    factory_id                    = Column(Integer, primary_key=True)
    manufacturer_id               = Column(Integer)
    location_country              = Column(String(100))
    location_city                 = Column(String(100))
    capacity_pieces_month         = Column(Integer)
    utilisation_pct               = Column(Numeric(5, 2))
    order_book_depth_weeks        = Column(Integer)
    certifications                = Column(String(255))
    on_time_delivery_rate         = Column(Numeric(5, 2))
    lead_time_weeks               = Column(Integer)
    financing_rate_annual_pct     = Column(Numeric(8, 4))
    platform_verified             = Column(Boolean)
    active_importer_count         = Column(Integer)
    supply_chain_finance_eligible = Column(Boolean)
    created_at                    = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                    = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
