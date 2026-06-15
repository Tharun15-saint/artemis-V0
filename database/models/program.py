from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, Boolean
from sqlalchemy.sql import func
from database.base import Base


class ProductSpecification(Base):
    __tablename__ = "product_specification"
    spec_id                 = Column(Integer, primary_key=True)
    product_name            = Column(String(255))
    hs_code_id              = Column(Integer)   # FK → hs_codes
    fibre_content           = Column(String(255))
    construction            = Column(String(255))
    weight_gsm              = Column(Numeric(8, 2))
    typical_fob_low         = Column(Numeric(10, 4))
    typical_fob_high        = Column(Numeric(10, 4))
    prototype_corridor_1    = Column(String(100))
    prototype_corridor_2    = Column(String(100))
    prototype_corridor_3    = Column(String(100))
    created_at              = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at              = Column(DateTime, server_default=func.now(),
                                     onupdate=func.now(), nullable=False)


class Program(Base):
    __tablename__ = "program"
    program_id              = Column(Integer, primary_key=True)
    importer_id             = Column(Integer)   # FK → importer
    factory_id              = Column(Integer)   # FK → cmt_factories
    spec_id                 = Column(Integer)   # FK → product_specification
    season                  = Column(String(20))
    quantity_units          = Column(Integer)
    fob_price_agreed        = Column(Numeric(10, 4))
    delivery_date_committed = Column(Date)
    origin_port             = Column(String(255))
    destination_port        = Column(String(255))
    destination_dc          = Column(String(255))
    cmt_start_date          = Column(Date)
    fabric_cut_date         = Column(Date)
    ship_date_planned       = Column(Date)
    ship_date_actual        = Column(Date)
    status                  = Column(String(50))   # See PROGRAM_STATUSES in constants
    commodity_hedge_status  = Column(String(50))   # See HEDGE_STATUSES in constants
    hedge_id                = Column(Integer)       # FK → hedge_portfolio
    freight_booking_id      = Column(Integer)       # FK → ocean_freight_rfq
    customs_clearance_id    = Column(Integer)       # FK → customs_clearance_filing
    otd_risk_score          = Column(Numeric(5, 2))
    landed_cost_estimated   = Column(Numeric(10, 4))
    landed_cost_actual      = Column(Numeric(10, 4))  # Set only after duty confirmed
    created_at              = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at              = Column(DateTime, server_default=func.now(),
                                     onupdate=func.now(), nullable=False)
