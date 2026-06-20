"""
Capture API — the single doorway through which raw payloads enter Artemis.

Usage (a loader, e.g. the RRK corpus load):

    from database.base import SessionLocal
    from data.raw.capture import start_run, capture_file, capture_path, finish_run

    db = SessionLocal()
    run = start_run(db, source_system="rrk_hdd", run_kind="rrk_corpus_load",
                    parameters={"hdd_path": "/Volumes/RRK"}, operator="loader:tharun")
    try:
        capture_path(db, run, "/Volumes/RRK/costing_sheets", artifact_kind="rrk_costing_sheet")
        finish_run(db, run, status="completed")
    except Exception as exc:
        finish_run(db, run, status="failed", error_message=str(exc))
        raise

Guarantees:
  * The bytes are stored in the CAS (deduplicated, integrity-addressable) BEFORE the
    manifest row is written, so a manifest row never points at a missing blob.
  * Within one run, identical bytes yield one manifest row (uq_raw_artifact_run_hash);
    the same payload captured in a LATER run is a new row (full history preserved).
  * capture_bytes/capture_file flush but DO NOT commit — the caller controls the txn so
    capture-then-derive can be atomic. capture_path commits per file for durability.

This module is the ONLY writer of raw_artifact. There is no update or delete path —
that is what makes L1 immutable.
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from data.raw.raw_store import RawStore
from database.base import SessionLocal
from database.models.raw_capture import RawArtifact, RawIngestRun

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

TOOL_VERSION = "raw-capture-v1.0"

# Python's mimetypes misses several corpus formats (Office documents, email), so the L2
# router could get a NULL media_type for exactly the RRK file types that matter. Fill the
# gaps explicitly so every captured artifact carries a reliable routing hint.
_MEDIA_FALLBACK = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xls":  "application/vnd.ms-excel",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc":  "application/msword",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".eml":  "message/rfc822",
    ".msg":  "application/vnd.ms-outlook",
    ".md":   "text/markdown",
    ".csv":  "text/csv",
    ".tsv":  "text/tab-separated-values",
    ".json": "application/json",
    ".pdf":  "application/pdf",
    ".txt":  "text/plain",
    ".tiff": "image/tiff",
    ".tif":  "image/tiff",
}


def guess_media_type(filename: str) -> Optional[str]:
    """Best-effort MIME for a filename: stdlib mimetypes first, then a corpus-format fallback."""
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or _MEDIA_FALLBACK.get(Path(filename).suffix.lower())


# ── run lifecycle ─────────────────────────────────────────────────────────────
def start_run(
    db: Session,
    *,
    source_system: str,
    run_kind: str,
    parameters: Optional[dict] = None,
    operator: Optional[str] = None,
    tool_version: str = TOOL_VERSION,
) -> RawIngestRun:
    run = RawIngestRun(
        run_uuid=str(uuid.uuid4()),
        source_system=source_system,
        run_kind=run_kind,
        status="running",
        started_at=datetime.utcnow(),
        artifact_count=0,
        total_bytes=0,
        operator=operator,
        tool_version=tool_version,
        parameters_json=json.dumps(parameters, default=str) if parameters else None,
    )
    db.add(run)
    db.commit()           # the run row exists immediately, before any capture
    db.refresh(run)
    logger.info("raw ingest run %s started (%s / %s)", run.run_uuid, source_system, run_kind)
    return run


def finish_run(
    db: Session,
    run: RawIngestRun,
    *,
    status: str = "completed",
    error_message: Optional[str] = None,
) -> RawIngestRun:
    run.status = status
    run.completed_at = datetime.utcnow()
    if error_message:
        run.error_message = error_message
    db.commit()
    db.refresh(run)
    logger.info(
        "raw ingest run %s %s — %d artifacts, %d bytes",
        run.run_uuid, status, run.artifact_count, run.total_bytes,
    )
    return run


# ── capture ───────────────────────────────────────────────────────────────────
def capture_bytes(
    db: Session,
    run: RawIngestRun,
    data: bytes,
    *,
    artifact_kind: str,
    source_system: Optional[str] = None,
    media_type: Optional[str] = None,
    source_uri: Optional[str] = None,
    original_filename: Optional[str] = None,
    source_locator: Optional[dict] = None,
    fetched_at: Optional[datetime] = None,
    notes: Optional[str] = None,
    store: Optional[RawStore] = None,
) -> RawArtifact:
    """Capture a payload. Stores bytes in the CAS, then writes the immutable manifest row.

    Flushes (assigns the id) but does not commit — the caller owns the transaction.
    Idempotent within a run: re-capturing identical bytes returns the existing row.
    """
    store = store or RawStore()
    blob = store.put(data)  # bytes land on disk first

    # Within-run dedup: identical bytes captured twice in one run is one artifact.
    existing = (
        db.query(RawArtifact)
        .filter(RawArtifact.ingest_run_id == run.raw_ingest_run_id,
                RawArtifact.content_sha256 == blob.sha256)
        .first()
    )
    if existing is not None:
        return existing

    artifact = RawArtifact(
        ingest_run_id=run.raw_ingest_run_id,
        content_sha256=blob.sha256,
        byte_size=blob.byte_size,
        media_type=media_type,
        artifact_kind=artifact_kind,
        source_system=source_system or run.source_system,
        source_uri=source_uri,
        original_filename=original_filename,
        source_locator_json=json.dumps(source_locator, default=str) if source_locator else None,
        storage_backend=blob.storage_backend,
        storage_path=blob.storage_path,
        fetched_at=fetched_at or datetime.utcnow(),
        notes=notes,
    )
    db.add(artifact)
    db.flush()  # assign raw_artifact_id without committing

    run.artifact_count = (run.artifact_count or 0) + 1
    run.total_bytes = (run.total_bytes or 0) + blob.byte_size
    return artifact


def capture_file(
    db: Session,
    run: RawIngestRun,
    path: os.PathLike | str,
    *,
    artifact_kind: str,
    source_system: Optional[str] = None,
    media_type: Optional[str] = None,
    source_locator: Optional[dict] = None,
    notes: Optional[str] = None,
    store: Optional[RawStore] = None,
) -> RawArtifact:
    """Capture a single file. Infers media type, filename, and uri from the path; uses the
    file's mtime as fetched_at (the moment we obtained it from source)."""
    p = Path(path)
    with open(p, "rb") as fh:
        data = fh.read()
    fetched_at = datetime.fromtimestamp(p.stat().st_mtime)
    return capture_bytes(
        db, run, data,
        artifact_kind=artifact_kind,
        source_system=source_system,
        media_type=media_type or guess_media_type(p.name),
        source_uri=p.resolve().as_uri(),
        original_filename=p.name,
        source_locator=source_locator,
        fetched_at=fetched_at,
        notes=notes,
        store=store,
    )


def capture_path(
    db: Session,
    run: RawIngestRun,
    root_path: os.PathLike | str,
    *,
    artifact_kind: str,
    source_system: Optional[str] = None,
    recursive: bool = True,
    skip_hidden: bool = True,
    store: Optional[RawStore] = None,
) -> list[RawArtifact]:
    """Capture every file under a path (the RRK corpus entry point). Commits per file so a
    crash mid-walk loses nothing already captured. Returns the artifacts written."""
    store = store or RawStore()
    root = Path(root_path)
    if root.is_file():
        files: Iterable[Path] = [root]
    else:
        globber = root.rglob("*") if recursive else root.glob("*")
        files = sorted(p for p in globber if p.is_file())

    captured: list[RawArtifact] = []
    for p in files:
        if skip_hidden and any(part.startswith(".") for part in p.relative_to(root if root.is_dir() else root.parent).parts):
            continue
        try:
            artifact = capture_file(
                db, run, p,
                artifact_kind=artifact_kind,
                source_system=source_system,
                source_locator={"root": str(root), "relpath": str(p.relative_to(root) if root.is_dir() else p.name)},
                store=store,
            )
            db.commit()
            captured.append(artifact)
            logger.info("captured %s (%s, %d bytes)", p.name, artifact.content_sha256[:12], artifact.byte_size)
        except Exception:
            db.rollback()
            logger.exception("FAILED to capture %s", p)
            raise
    return captured


# ── CLI — the RRK corpus loader entry point ─────────────────────────────────────
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Capture raw payloads into the immutable L1 store.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--path", help="Directory tree to capture (recursively).")
    src.add_argument("--file", help="A single file to capture.")
    ap.add_argument("--source-system", required=True, help="e.g. rrk_hdd, sec_edgar")
    ap.add_argument("--run-kind", required=True, help="e.g. rrk_corpus_load, sec_backfill")
    ap.add_argument("--artifact-kind", required=True, help="L2 routing hint, e.g. rrk_costing_sheet")
    ap.add_argument("--operator", default=None, help="who/what triggered this run")
    ap.add_argument("--no-recursive", action="store_true", help="do not descend into subdirectories")
    args = ap.parse_args(argv)

    target = args.path or args.file
    operator = args.operator or f"cli:{os.getenv('USER', 'unknown')}"

    db = SessionLocal()
    run = start_run(
        db,
        source_system=args.source_system,
        run_kind=args.run_kind,
        parameters={"target": target, "recursive": not args.no_recursive},
        operator=operator,
    )
    try:
        captured = capture_path(
            db, run, target,
            artifact_kind=args.artifact_kind,
            source_system=args.source_system,
            recursive=not args.no_recursive,
        )
        finish_run(db, run, status="completed")
        print(f"\n✓ run {run.run_uuid}: captured {len(captured)} artifacts, {run.total_bytes} bytes")
        return 0
    except Exception as exc:
        finish_run(db, run, status="failed", error_message=str(exc))
        print(f"\n✗ run {run.run_uuid} FAILED: {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
