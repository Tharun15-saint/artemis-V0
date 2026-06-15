from sqlalchemy import Column, Integer, String, Numeric, Boolean, Date, DateTime
from sqlalchemy.sql import func
from database.base import Base


class Importer(Base):
    __tablename__ = "importer"
    importer_id                 = Column(Integer, primary_key=True)
    company_name                = Column(String(255))
    annual_revenue_usd          = Column(Numeric(14, 2))
    primary_corridors           = Column(String(255))
    subscription_tier           = Column(String(50))
    subscription_fee_monthly    = Column(Numeric(10, 2))
    subscribes_to_intelligence  = Column(Boolean)
    executes_hedges             = Column(Boolean)
    books_freight               = Column(Boolean)
    manages_programs            = Column(Boolean)
    discovers_factories         = Column(Boolean)
    account_manager             = Column(String(255))
    joined_date                 = Column(Date)
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class Manufacturer(Base):
    __tablename__ = "manufacturer"
    manufacturer_id             = Column(Integer, primary_key=True)
    company_name                = Column(String(255))
    primary_product_type        = Column(String(255))
    annual_export_usd           = Column(Numeric(14, 2))
    primary_importer_markets    = Column(String(255))
    platform_tier               = Column(String(50))
    manages_programs            = Column(Boolean)
    receives_pos                = Column(Boolean)
    submits_milestones          = Column(Boolean)
    accesses_scf                = Column(Boolean)
    builds_profile              = Column(Boolean)
    joined_date                 = Column(Date)
    created_at                  = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                  = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class ManufacturerProfile(Base):
    __tablename__ = "manufacturer_profile"
    profile_id                    = Column(Integer, primary_key=True)
    factory_id                    = Column(Integer)
    manufacturer_id               = Column(Integer)
    legal_name                    = Column(String(255))
    trade_name                    = Column(String(255))
    factory_type                  = Column(String(50))
    utilisation_pct_live          = Column(Numeric(5, 2))
    otd_rate_verified             = Column(Numeric(5, 2))
    platform_tier                 = Column(String(50))
    profile_visibility            = Column(String(50))
    supply_chain_finance_eligible = Column(Boolean)
    contact_primary               = Column(String(255))
    created_at                    = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at                    = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class ImporterWorkingCapital(Base):
    __tablename__ = "importer_working_capital"
    working_capital_id      = Column(Integer, primary_key=True)
    importer_id             = Column(Integer)
    annual_borrowing_rate   = Column(Numeric(8, 4))
    typical_inventory_days  = Column(Integer)
    cost_of_carry_per_dozen = Column(Numeric(10, 4))
    created_at              = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at              = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
