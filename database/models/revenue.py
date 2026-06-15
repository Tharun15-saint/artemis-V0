from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, Boolean
from sqlalchemy.sql import func
from database.base import Base


class RevenueTransaction(Base):
    """Every monetisation event. Every time Artemis earns money, this gets a row."""
    __tablename__ = "revenue_transaction"
    transaction_id      = Column(Integer, primary_key=True)
    program_id          = Column(Integer)   # FK → program
    revenue_type        = Column(String(100))  # See REVENUE_TYPES in constants
    partner_name        = Column(String(255))
    gross_amount        = Column(Numeric(12, 2))
    net_to_artemis      = Column(Numeric(12, 2))
    transaction_date    = Column(Date)
    status              = Column(String(50))    # PENDING / CONFIRMED / PAID
    reference_id        = Column(String(255))   # Partner's reference
    created_at          = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at          = Column(DateTime, server_default=func.now(),
                                  onupdate=func.now(), nullable=False)


class IntelligenceSubscription(Base):
    __tablename__ = "intelligence_subscription"
    revenue_id              = Column(Integer, primary_key=True)
    importer_id             = Column(Integer)   # FK → importer
    monthly_fee             = Column(Numeric(10, 2))
    tier                    = Column(String(50))
    billing_cycle           = Column(String(50))
    target_gross_margin_pct = Column(Numeric(5, 2))
    created_at              = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at              = Column(DateTime, server_default=func.now(),
                                     onupdate=func.now(), nullable=False)


class DataLicensingRevenue(Base):
    __tablename__ = "data_licensing_revenue"
    revenue_id            = Column(Integer, primary_key=True)
    annual_fee_range      = Column(String(50))
    customer_types        = Column(String(255))
    data_products         = Column(String(255))
    exclusivity_available = Column(Boolean)
    created_at            = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at            = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
