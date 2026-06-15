from sqlalchemy import Column, Integer, String, Numeric, Boolean, Date, DateTime
from sqlalchemy.sql import func
from database.base import Base


class CustomsClearanceFiling(Base):
    __tablename__ = "customs_clearance_filing"
    filing_id           = Column(Integer, primary_key=True)
    program_id          = Column(Integer)
    hs_code_id          = Column(Integer)
    importer_of_record  = Column(String(255))
    declared_value      = Column(Numeric(12, 2))
    quantity_units      = Column(Integer)
    country_of_origin   = Column(String(100))
    vessel_name         = Column(String(255))
    bol_number          = Column(String(255))
    entry_number        = Column(String(255))
    duty_amount_paid    = Column(Numeric(12, 2))
    clearance_date      = Column(Date)
    cbp_response_status = Column(String(50))
    created_at          = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at          = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class DutyDrawback(Base):
    __tablename__ = "duty_drawback"
    drawback_id         = Column(Integer, primary_key=True)
    program_id          = Column(Integer)
    eligibility_flag    = Column(Boolean)
    estimated_recovery  = Column(Numeric(12, 2))
    drawback_type       = Column(String(255))
    specialist_referred = Column(Boolean)
    created_at          = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at          = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
