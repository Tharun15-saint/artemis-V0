from sqlalchemy import Column, Date, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from database.base import Base


class IngestionLog(Base):
    __tablename__ = "ingestion_log"

    log_id              = Column(Integer, primary_key=True, autoincrement=True)
    source_name         = Column(String(100), nullable=False)
    pull_started_at     = Column(DateTime, nullable=False)
    pull_completed_at   = Column(DateTime, nullable=True)
    status              = Column(String(20), nullable=False)
    rows_attempted      = Column(Integer, default=0)
    rows_inserted       = Column(Integer, default=0)
    rows_rejected       = Column(Integer, default=0)
    rows_stale          = Column(Integer, default=0)
    data_as_of_date     = Column(Date, nullable=True)
    data_source_url     = Column(String(500), nullable=True)
    error_message       = Column(Text, nullable=True)
    validation_failures = Column(Text, nullable=True)
    script_version      = Column(String(64), nullable=True)
    created_at          = Column(DateTime, server_default=func.now())
    updated_at          = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )
