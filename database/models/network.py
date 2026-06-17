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
    # World 2 deep actor profile — who this importer actually is
    headquarters_country        = Column(String(100))   # "Jordan" for Classic Fashion
    headquarters_city           = Column(String(100))   # "Amman"
    has_own_manufacturing       = Column(Boolean)       # True for Classic Fashion / Athlux Studio
    own_manufacturing_country   = Column(String(100))   # "Jordan"
    own_manufacturing_capacity_day = Column(Integer)    # pieces/day of own factories
    # trade_names: JSON array e.g. ["Classic Fashion", "Athlux Studio"]
    trade_names_json            = Column(String)
    primary_buying_hub          = Column(String(100))   # "Tirupur" — primary sourcing cluster
    buying_relationship_since   = Column(Date)          # date of first purchase order placed
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
    # World 2 deep actor profile — who this manufacturer actually is
    manufacturing_hub           = Column(String(100))   # "Tirupur" — the geographic cluster
    established_year            = Column(Integer)       # 1994 for RRK (30+ years)
    daily_capacity_units        = Column(Integer)       # 200000 for RRK
    active_production_units     = Column(Integer)       # 5 for RRK
    # vertical_integration: JSON array e.g. ["knitting", "dyeing", "finishing", "cut_make_trim"]
    vertical_integration_json   = Column(String)
    # product_capabilities: JSON dict e.g. {"single_jersey": true, "french_terry": true}
    product_capabilities_json   = Column(String)
    # compliance_certificates: JSON array e.g. ["GOTS", "OCS", "Sedex", "BSCI"]
    compliance_certificates_json = Column(String)
    # trade_names: JSON array e.g. ["RRK Knit Wear", "RRK Exports", "RRK Textiles"]
    trade_names_json            = Column(String)
    # export_markets: JSON array e.g. ["US", "EU", "Canada", "Australia"]
    export_markets_json         = Column(String)
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


class CompanyFactoryRelationship(Base):
    """Legacy class — references table from old architecture. Table not yet migrated to current schema."""
    __tablename__ = "company_factory_relationship"
    id               = Column(Integer, primary_key=True)
    company_id       = Column(Integer)
    factory_name     = Column(String(255))
    factory_location = Column(String(255))
    factory_corridor = Column(String(100))
    relationship_years = Column(Integer)
    programs_completed = Column(Integer)
    avg_otd_rate     = Column(Numeric(10, 4))
    avg_quality_acceptance_rate = Column(Numeric(10, 4))
    avg_price_vs_market_pct = Column(Numeric(10, 4))
    typical_payment_terms = Column(String(100))
