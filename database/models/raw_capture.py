"""
Medallion Layer 1 — immutable raw capture.

The bottom of the data stack. Before any value is parsed, typed, reconciled, or
certified, the EXACT source payload is captured here, byte-for-byte, with its
provenance. This makes every downstream fact (L2 refined → L3 certified) traceable
to the precise bytes it came from, and makes bug-fix re-derivation deterministic and
OFFLINE — we never have to re-fetch from SEC (or re-read the RRK disk) to rebuild.

Two tables:
  - RawIngestRun — one row per capture batch (a single execution of a loader). Mutable
    metadata only: status, counts, parameters, operator. This is NOT IngestionLog —
    IngestionLog accounts for DERIVED ROWS in the refined layer; RawIngestRun accounts
    for RAW ARTIFACTS captured.
  - RawArtifact — the immutable manifest: one row per captured payload. The payload
    BYTES live in a content-addressable store on disk (see data/raw/raw_store.py),
    addressed by content_sha256; this table holds only the manifest + provenance. There
    is deliberately NO updated_at column — raw artifacts are append-only and never
    mutated; the absence of an update path IS the immutability guarantee. Re-fetching
    the same logical source on another day yields a NEW artifact (new bytes → new hash),
    so full history is preserved for time-travel.

Source-agnostic by construction: the same two tables hold today's SEC companyfacts JSON
and tomorrow's RRK costing spreadsheet, PO-log scan, or .eml — only source_system,
media_type, and artifact_kind differ. L1 stores bytes + provenance; it never interprets.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from database.base import Base


class RawIngestRun(Base):
    """One capture batch. Ties many raw artifacts to a single, accountable execution."""

    __tablename__ = "raw_ingest_run"

    raw_ingest_run_id = Column(Integer, primary_key=True, autoincrement=True)
    run_uuid          = Column(String(36), nullable=False, unique=True)   # stable external id
    source_system     = Column(String(80), nullable=False)               # 'sec_edgar' | 'rrk_hdd' | ...
    run_kind          = Column(String(60), nullable=False)               # 'rrk_corpus_load' | 'sec_backfill' | ...
    status            = Column(String(20), nullable=False, server_default="running")  # running|completed|failed
    started_at        = Column(DateTime, nullable=False)
    completed_at      = Column(DateTime, nullable=True)
    artifact_count    = Column(Integer, nullable=False, server_default="0")
    total_bytes       = Column(BigInteger, nullable=False, server_default="0")
    operator          = Column(String(160), nullable=True)               # script + user that ran it
    tool_version      = Column(String(64), nullable=True)                # capture code version
    parameters_json   = Column(Text, nullable=True)                      # the run's params (JSON)
    error_message     = Column(Text, nullable=True)
    notes             = Column(Text, nullable=True)
    created_at        = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at        = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class RawArtifact(Base):
    """Immutable manifest row for one captured payload. Bytes live in the CAS by hash.

    Append-only: never updated, never deleted. Same bytes within a run are stored once
    (uq_raw_artifact_run_hash). The same logical source re-fetched later = a new row with
    a new hash, preserving full lineage.
    """

    __tablename__ = "raw_artifact"

    # BigInteger on Postgres (room for billions of artifacts); Integer on SQLite, where
    # only an INTEGER PRIMARY KEY autoincrements (the local test harness uses SQLite).
    raw_artifact_id   = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    ingest_run_id     = Column(
        Integer,
        ForeignKey("raw_ingest_run.raw_ingest_run_id"),
        nullable=False,
    )
    content_sha256    = Column(String(64), nullable=False)   # integrity + dedup key (hex)
    byte_size         = Column(BigInteger, nullable=False)
    media_type        = Column(String(120), nullable=True)   # MIME: application/json, application/pdf, ...
    artifact_kind     = Column(String(60), nullable=False)   # L2 routing hint: 'sec_companyfacts' | 'rrk_costing_sheet' | ...
    source_system     = Column(String(80), nullable=False)   # 'sec_edgar' | 'rrk_hdd' | 'motley_fool' | ...
    source_uri        = Column(Text, nullable=True)          # URL, or file:// path, or logical locator
    original_filename = Column(String(500), nullable=True)   # as named at source (RRK files)
    source_locator_json = Column(Text, nullable=True)        # structured extra: {"cik":..,"accession":..} / {"hdd_path":..}
    storage_backend   = Column(String(20), nullable=False, server_default="local_cas")  # 'local_cas' | 's3' (future)
    storage_path      = Column(Text, nullable=False)         # path within the store, derived from hash
    fetched_at        = Column(DateTime, nullable=False)     # when obtained from source
    captured_at       = Column(DateTime, server_default=func.now(), nullable=False)  # when this row was written
    notes             = Column(Text, nullable=True)
    created_at        = Column(DateTime, server_default=func.now(), nullable=False)
    # NOTE: intentionally NO updated_at — raw artifacts are immutable.

    __table_args__ = (
        UniqueConstraint("ingest_run_id", "content_sha256", name="uq_raw_artifact_run_hash"),
        Index("ix_raw_artifact_hash", "content_sha256"),
        Index("ix_raw_artifact_source", "source_system", "artifact_kind"),
        Index("ix_raw_artifact_run", "ingest_run_id"),
    )
