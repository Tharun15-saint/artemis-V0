"""
Integrity gate for the L1 raw-capture layer.

For every raw_artifact manifest row this confirms, against the content-addressable store:
  1. the blob exists on disk at its storage_path;
  2. re-hashing the blob reproduces content_sha256 (no silent corruption / tampering);
  3. the on-disk size equals byte_size;
  4. storage_path is the canonical sharded path for that hash (no manifest/path drift).

It also reports orphan blobs (bytes on disk with no manifest row) as a WARNING — those are
harmless (e.g. a blob written by a run that crashed before committing the manifest) and do
not fail the gate, because L1's contract is "every manifest row resolves to its exact bytes",
not "every byte has a manifest row".

Designed to be re-run as a permanent gate:
    exit 0  -> every manifest row resolves to its exact, intact bytes
    exit 1  -> one or more rows have a missing / corrupt / mismatched blob

This module NEVER mutates the database or the store. Verification only.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from data.raw.raw_store import RawStore, SHARD_PREFIX
from database.base import SessionLocal
from database.models.raw_capture import RawArtifact

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def check_manifest(db: Session, store: RawStore) -> list[str]:
    """Return a list of failure descriptions (empty == clean)."""
    failures: list[str] = []
    artifacts = db.query(RawArtifact).order_by(RawArtifact.raw_artifact_id).all()
    logger.info("checking %d manifest rows against store at %s", len(artifacts), store.root)

    for a in artifacts:
        tag = f"raw_artifact_id={a.raw_artifact_id} sha={a.content_sha256[:12]}"
        path = store.abspath(a.storage_path)

        if not path.exists():
            failures.append(f"{tag}: MISSING blob at {a.storage_path}")
            continue

        canonical = store.relpath_for(a.content_sha256)
        if a.storage_path != canonical:
            failures.append(f"{tag}: storage_path {a.storage_path} != canonical {canonical}")

        actual_size = path.stat().st_size
        if actual_size != a.byte_size:
            failures.append(f"{tag}: size {actual_size} != manifest byte_size {a.byte_size}")

        actual_hash = _hash_file(path)
        if actual_hash != a.content_sha256:
            failures.append(f"{tag}: CORRUPT — re-hash {actual_hash[:12]} != manifest {a.content_sha256[:12]}")

    return failures


def find_orphan_blobs(db: Session, store: RawStore) -> list[str]:
    """Blobs on disk not referenced by any manifest row (informational only)."""
    shard_root = store.root / SHARD_PREFIX
    if not shard_root.exists():
        return []
    known = {row[0] for row in db.query(RawArtifact.content_sha256).all()}
    orphans = []
    for p in shard_root.rglob("*"):
        if p.is_file() and p.name not in known:
            orphans.append(str(p.relative_to(store.root)))
    return orphans


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="L1 raw-capture integrity gate.")
    ap.add_argument("--store-root", default=None, help="override RAW_STORE_ROOT")
    args = ap.parse_args(argv)

    store = RawStore(args.store_root) if args.store_root else RawStore()
    db = SessionLocal()
    try:
        failures = check_manifest(db, store)
        orphans = find_orphan_blobs(db, store)
    finally:
        db.close()

    if orphans:
        logger.warning("%d orphan blob(s) on disk (no manifest row) — harmless:", len(orphans))
        for o in orphans[:20]:
            logger.warning("  orphan: %s", o)

    if failures:
        logger.error("L1 INTEGRITY FAILED — %d issue(s):", len(failures))
        for f in failures:
            logger.error("  %s", f)
        print(f"\n✗ L1 integrity gate FAILED: {len(failures)} issue(s)")
        return 1

    print("\n✓ L1 integrity gate PASSED — every manifest row resolves to its exact, intact bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
