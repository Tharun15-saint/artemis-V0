from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime
from sqlalchemy.sql import func
from database.base import Base


class SupplyChainFinanceOffer(Base):
    __tablename__ = "supply_chain_finance_offer"
    scf_offer_id          = Column(Integer, primary_key=True)
    program_id            = Column(Integer)
    factory_id            = Column(Integer)
    invoice_value         = Column(Numeric(12, 2))
    advance_rate_pct      = Column(Numeric(5, 2))
    discount_rate_pct     = Column(Numeric(5, 2))
    effective_rate_saving = Column(Numeric(10, 4))
    offer_date            = Column(Date)
    acceptance_date       = Column(Date)
    disbursement_date     = Column(Date)
    status                = Column(String(50))
    scf_provider          = Column(String(255))
    created_at            = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at            = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
