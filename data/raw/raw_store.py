"""
Content-addressable store (CAS) for raw payload bytes — the physical L1 "originals box".

The database (raw_artifact) holds the manifest; the bytes live here, on disk, named by
their SHA-256. Properties this gives us, by construction:

  * Integrity      — the filename IS the hash; any corruption is detectable by re-hashing.
  * Deduplication  — identical bytes hash to the same path and are stored exactly once.
  * Offline replay — once captured, downstream layers rebuild from disk, never re-fetching.
  * Portability     — paths are RELATIVE to a configurable root (RAW_STORE_ROOT), so the
                     store can sit in the project, on the external RRK drive, or (later)
                     behind an S3-backed implementation with the same interface.

Writes are atomic (temp file + os.replace) and idempotent (a present blob is never
rewritten). This module knows nothing about the DB — it is pure storage.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_ROOT = _PROJECT_ROOT / "raw_store"

SHARD_PREFIX = "sha256"


@dataclass(frozen=True)
class BlobRef:
    """The result of storing bytes: enough to fill a raw_artifact manifest row."""

    sha256: str
    byte_size: int
    storage_path: str        # relative to the store root — what we persist in the DB
    storage_backend: str = "local_cas"
    was_new: bool = True     # False if the blob was already present (dedup hit)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RawStore:
    """Local filesystem content-addressable store, sharded two levels deep by hash."""

    def __init__(self, root: Optional[os.PathLike | str] = None):
        if root is None:
            root = os.getenv("RAW_STORE_ROOT") or _DEFAULT_ROOT
        self.root = Path(root).expanduser().resolve()

    # ── path math ────────────────────────────────────────────────────────────
    @staticmethod
    def relpath_for(sha256: str) -> str:
        """sha256/ab/cd/<full-hash> — two shard levels keep any directory small."""
        if len(sha256) < 4:
            raise ValueError(f"not a sha256 hex digest: {sha256!r}")
        return f"{SHARD_PREFIX}/{sha256[:2]}/{sha256[2:4]}/{sha256}"

    def abspath_for(self, sha256: str) -> Path:
        return self.root / self.relpath_for(sha256)

    def abspath(self, storage_path: str) -> Path:
        return self.root / storage_path

    # ── operations ───────────────────────────────────────────────────────────
    def exists(self, sha256: str) -> bool:
        return self.abspath_for(sha256).exists()

    def put(self, data: bytes) -> BlobRef:
        """Store bytes; return a BlobRef. Idempotent — present blobs are not rewritten."""
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("RawStore.put requires bytes")
        digest = sha256_hex(data)
        dest = self.abspath_for(digest)
        relpath = self.relpath_for(digest)
        if dest.exists():
            return BlobRef(digest, len(data), relpath, was_new=False)

        dest.parent.mkdir(parents=True, exist_ok=True)
        # Atomic, collision-safe write: unique temp in the same dir, fsync, then replace.
        tmp = dest.parent / f".{digest}.{os.getpid()}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest)
        return BlobRef(digest, len(data), relpath, was_new=True)

    def put_file(self, path: os.PathLike | str) -> BlobRef:
        """Store a file's contents (read fully — L1 keeps the whole payload)."""
        with open(path, "rb") as fh:
            return self.put(fh.read())

    def get(self, sha256: str) -> bytes:
        with open(self.abspath_for(sha256), "rb") as fh:
            return fh.read()

    def verify(self, sha256: str) -> bool:
        """Re-hash the stored blob and confirm it still equals its address."""
        p = self.abspath_for(sha256)
        if not p.exists():
            return False
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest() == sha256
