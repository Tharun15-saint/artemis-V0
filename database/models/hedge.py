from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime
from sqlalchemy.sql import func
from database.base import Base


class HedgeOpportunity(Base):
    __tablename__ = "hedge_opportunity"
    hedge_opportunity_id = Column(Integer, primary_key=True)
    program_id           = Column(Integer)
    commodity            = Column(String(50))
    tenor_months         = Column(Integer)
    recommended_quantity = Column(Numeric(10, 4))
    spot_price           = Column(Numeric(10, 4))
    futures_price        = Column(Numeric(10, 4))
    basis                = Column(Numeric(10, 4))
    potential_saving_doz = Column(Numeric(10, 4))
    risk_if_unhedged_usd = Column(Numeric(12, 2))
    recommended_action   = Column(String(255))
    pillar_quote_id      = Column(String(255))
    created_at           = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at           = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class HedgePortfolio(Base):
    __tablename__ = "hedge_portfolio"
    hedge_id         = Column(Integer, primary_key=True)
    program_id       = Column(Integer)
    opportunity_id   = Column(Integer)
    hedged_commodity = Column(String(50))
    strike_price     = Column(Numeric(10, 4))
    quantity_bales   = Column(Numeric(10, 4))
    notional_usd     = Column(Numeric(12, 2))
    premium_usd      = Column(Numeric(12, 2))
    execution_date   = Column(Date)
    expiry_date      = Column(Date)
    settlement_date  = Column(Date)
    status           = Column(String(50))
    current_mtm_usd  = Column(Numeric(12, 2))
    unrealised_pnl   = Column(Numeric(12, 2))
    pillar_hedge_ref = Column(String(255))
    created_at       = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at       = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
