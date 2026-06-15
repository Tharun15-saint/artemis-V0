"""Context manager for ingestion runs — creates and finalises IngestionLog rows."""

from __future__ import annotations

import json
import traceback
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from database.models.ingestion_log import IngestionLog


class IngestionContext:
    """
    Context manager for ingestion runs.
    Creates a log row at start, updates it at end.
    Catches all exceptions and marks as failed.

    Usage:
        with IngestionContext(
            source_name='cotton_ice_yfinance',
            script_version='cotton-v1.0',
            data_source_url='https://finance.yahoo.com/...',
            db=db
        ) as ctx:
            # ctx.log is the IngestionLog row
            # Use ctx.inserted() to increment rows_inserted
            # Use ctx.rejected(reason) to increment rows_rejected
            # Use ctx.stale() to increment rows_stale
            # Use ctx.set_as_of_date(date) to record data date
    """

    def __init__(
        self,
        source_name: str,
        script_version: str,
        data_source_url: Optional[str],
        db: Session,
    ) -> None:
        self.source_name = source_name
        self.script_version = script_version
        self.data_source_url = data_source_url
        self.db = db
        self.log: Optional[IngestionLog] = None
        self._validation_failures: list[str] = []
        self._status_override: Optional[str] = None
        self._error_message: Optional[str] = None

    def __enter__(self) -> IngestionContext:
        self.log = IngestionLog(
            source_name=self.source_name,
            script_version=self.script_version,
            data_source_url=self.data_source_url,
            pull_started_at=datetime.now(timezone.utc),
            status="running",
            rows_attempted=0,
            rows_inserted=0,
            rows_rejected=0,
            rows_stale=0,
        )
        self.db.add(self.log)
        self.db.commit()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if self.log is None:
            return False

        self.log.pull_completed_at = datetime.now(timezone.utc)
        self.log.validation_failures = (
            json.dumps(self._validation_failures) if self._validation_failures else None
        )
        if exc_type is not None:
            self.log.status = "failed"
            self.log.error_message = "".join(
                traceback.format_exception(exc_type, exc_val, exc_tb)
            )
        elif self._status_override == "failed":
            self.log.status = "failed"
            if self._error_message:
                self.log.error_message = self._error_message
        elif self._status_override:
            self.log.status = self._status_override
        elif self.log.rows_rejected > 0 and self.log.rows_inserted > 0:
            self.log.status = "partial"
        elif self.log.rows_rejected > 0 and self.log.rows_inserted == 0:
            self.log.status = "failed"
        else:
            self.log.status = "success"

        self.db.add(self.log)
        self.db.commit()
        return False

    def inserted(self) -> None:
        self.log.rows_inserted += 1
        self.log.rows_attempted += 1

    def rejected(self, reason: str) -> None:
        self.log.rows_rejected += 1
        self.log.rows_attempted += 1
        self._validation_failures.append(reason)

    def stale(self) -> None:
        self.log.rows_stale += 1

    def set_as_of_date(self, d: date) -> None:
        self.log.data_as_of_date = d

    # Backward-compatible aliases
    @property
    def log_row(self) -> Optional[IngestionLog]:
        return self.log

    def increment_inserted(self, count: int = 1) -> None:
        for _ in range(count):
            self.inserted()

    def increment_attempted(self, count: int = 1) -> None:
        self.log.rows_attempted += count

    def increment_rejected(self, reason: str) -> None:
        self.rejected(reason)

    def increment_stale(self, count: int = 1) -> None:
        for _ in range(count):
            self.stale()

    def record_flag(self, reason: str) -> None:
        self._validation_failures.append(f"FLAG: {reason}")

    def set_failed(self, error: str | Exception) -> None:
        self._status_override = "failed"
        self._error_message = str(error)

    def set_partial(self) -> None:
        self._status_override = "partial"
