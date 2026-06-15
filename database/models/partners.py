from sqlalchemy import Column, Integer, String, Numeric, Boolean, Date, DateTime
from sqlalchemy.sql import func
from database.base import Base


class PillarHqPartner(Base):
    __tablename__ = "pillar_hq_partner"
    partner_id           = Column(Integer, primary_key=True)
    partner_name         = Column(String(255))
    integration_type     = Column(String(255))
    api_base_url         = Column(String(255))
    commodities_covered  = Column(String(255))
    revenue_share_pct    = Column(Numeric(5, 2))
    contract_signed_date = Column(Date)
    status               = Column(String(50))
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at           = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class CustomsBrokerPartner(Base):
    __tablename__ = "customs_broker_partner"
    partner_id        = Column(Integer, primary_key=True)
    partner_name      = Column(String(255))
    api_base_url      = Column(String(255))
    license_number    = Column(String(255))
    files_with_cbp    = Column(Boolean)
    revenue_model     = Column(String(255))
    fee_per_clearance = Column(Numeric(10, 2))
    status            = Column(String(50))
    created_at        = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at        = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class ScfProviderPartner(Base):
    __tablename__ = "scf_provider_partner"
    partner_id           = Column(Integer, primary_key=True)
    partner_name         = Column(String(255))
    api_base_url         = Column(String(255))
    funds_factories      = Column(Boolean)
    max_advance_rate_pct = Column(Numeric(5, 2))
    min_invoice_value    = Column(Numeric(12, 2))
    revenue_share_pct    = Column(Numeric(5, 2))
    status               = Column(String(50))
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at           = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
