from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, Text
from sqlalchemy.sql import func
from database.base import Base


class PredictionLog(Base):
    """
    Every intelligence output is logged here.
    When actual outcome is known, actual_value and accuracy_score are filled.
    This is how the model gets smarter over time.
    """
    __tablename__ = "prediction_log"
    prediction_id       = Column(Integer, primary_key=True)
    program_id          = Column(Integer)   # FK → program
    spec_id             = Column(Integer)   # FK → product_specification
    prediction_type     = Column(String(100))  # landed_cost / otd_risk / hedge_outcome
    corridor            = Column(String(100))
    predicted_value     = Column(Numeric(12, 4))
    p10                 = Column(Numeric(12, 4))
    p50                 = Column(Numeric(12, 4))
    p90                 = Column(Numeric(12, 4))
    prediction_timestamp = Column(DateTime, server_default=func.now())
    target_date         = Column(Date)
    actual_value        = Column(Numeric(12, 4))    # Filled retrospectively
    accuracy_score      = Column(Numeric(5, 2))     # Filled retrospectively
    model_version       = Column(String(100))
    data_snapshot_id    = Column(String(255))
    metadata_json       = Column(Text)
    created_at          = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at          = Column(DateTime, server_default=func.now(),
                                  onupdate=func.now(), nullable=False)
