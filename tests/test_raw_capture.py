"""
Tests for the Medallion Layer 1 raw-capture layer:
  * RawStore — content-addressing, dedup, round-trip, tamper detection.
  * capture API — manifest rows, within-run dedup, run accounting, file metadata.
  * integrity gate — passes clean, catches corruption / missing / orphan blobs.

Self-contained: a temp SQLite DB (the models are dialect-agnostic) + a temp store root,
so it never touches the real Postgres or the real raw_store/.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data.raw import capture as cap
from data.raw.raw_store import RawStore, sha256_hex
from data.verification.raw_integrity_check import check_manifest, find_orphan_blobs
from database.models.raw_capture import RawArtifact, RawIngestRun


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'raw_test.db'}")
    RawIngestRun.__table__.create(engine)
    RawArtifact.__table__.create(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture
def store(tmp_path):
    return RawStore(tmp_path / "store")


# ── RawStore ────────────────────────────────────────────────────────────────
def test_store_addresses_by_content(store):
    data = b"hello artemis"
    ref = store.put(data)
    assert ref.sha256 == sha256_hex(data)
    assert ref.byte_size == len(data)
    assert ref.storage_path == f"sha256/{ref.sha256[:2]}/{ref.sha256[2:4]}/{ref.sha256}"
    assert ref.was_new is True


def test_store_roundtrip_bytes_identical(store):
    data = bytes(range(256)) * 10  # binary, not just text
    ref = store.put(data)
    assert store.get(ref.sha256) == data


def test_store_dedups_identical_bytes(store):
    first = store.put(b"same bytes")
    second = store.put(b"same bytes")
    assert first.sha256 == second.sha256
    assert first.was_new is True
    assert second.was_new is False  # already present, not rewritten


def test_store_verify_detects_tampering(store):
    ref = store.put(b"trustworthy payload")
    assert store.verify(ref.sha256) is True
    # Tamper with the stored blob on disk.
    store.abspath_for(ref.sha256).write_bytes(b"corrupted")
    assert store.verify(ref.sha256) is False


# ── capture API ───────────────────────────────────────────────────────────────
def test_capture_bytes_writes_manifest_and_blob(session, store):
    run = cap.start_run(session, source_system="test", run_kind="unit")
    art = cap.capture_bytes(
        session, run, b"a costing sheet payload",
        artifact_kind="rrk_costing_sheet", store=store,
    )
    session.commit()
    assert art.raw_artifact_id is not None
    assert art.content_sha256 == sha256_hex(b"a costing sheet payload")
    assert store.exists(art.content_sha256)            # bytes really landed
    assert store.get(art.content_sha256) == b"a costing sheet payload"
    assert run.artifact_count == 1
    assert run.total_bytes == len(b"a costing sheet payload")


def test_capture_dedups_within_run(session, store):
    run = cap.start_run(session, source_system="test", run_kind="unit")
    a1 = cap.capture_bytes(session, run, b"dup", artifact_kind="x", store=store)
    a2 = cap.capture_bytes(session, run, b"dup", artifact_kind="x", store=store)
    session.commit()
    assert a1.raw_artifact_id == a2.raw_artifact_id   # one row, not two
    assert run.artifact_count == 1                    # counted once
    assert session.query(RawArtifact).count() == 1


def test_same_bytes_new_run_is_new_artifact(session, store):
    run1 = cap.start_run(session, source_system="test", run_kind="unit")
    cap.capture_bytes(session, run1, b"payload", artifact_kind="x", store=store)
    cap.finish_run(session, run1)

    run2 = cap.start_run(session, source_system="test", run_kind="unit")
    cap.capture_bytes(session, run2, b"payload", artifact_kind="x", store=store)
    cap.finish_run(session, run2)

    # Two manifest rows (full history), one physical blob (dedup).
    assert session.query(RawArtifact).count() == 2
    assert len(list((store.root / "sha256").rglob("*"))) >= 1


def test_capture_file_infers_metadata(session, store, tmp_path):
    src = tmp_path / "PO_log.json"
    src.write_bytes(b'{"po": 1}')
    run = cap.start_run(session, source_system="rrk_hdd", run_kind="rrk_corpus_load")
    art = cap.capture_file(session, run, src, artifact_kind="rrk_po_log", store=store)
    session.commit()
    assert art.original_filename == "PO_log.json"
    assert art.media_type == "application/json"
    assert art.source_uri.startswith("file://")
    assert art.fetched_at is not None


def test_capture_path_walks_tree(session, store, tmp_path):
    root = tmp_path / "corpus"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_bytes(b"alpha")
    (root / "sub" / "b.txt").write_bytes(b"bravo")
    (root / ".hidden").write_bytes(b"secret")  # must be skipped
    run = cap.start_run(session, source_system="rrk_hdd", run_kind="rrk_corpus_load")
    captured = cap.capture_path(session, run, root, artifact_kind="rrk_file", store=store)
    assert len(captured) == 2
    names = {a.original_filename for a in captured}
    assert names == {"a.txt", "b.txt"}


def test_media_type_inference_covers_corpus_formats():
    from data.raw.capture import guess_media_type
    assert guess_media_type("costing.xlsx") == \
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert guess_media_type("thread.eml") == "message/rfc822"
    assert guess_media_type("scan.pdf") == "application/pdf"
    assert guess_media_type("po_log.json") == "application/json"  # stdlib path still works


def test_raw_artifact_is_immutable_no_updated_at():
    # The absence of updated_at is the immutability signal — assert it stays absent.
    assert not hasattr(RawArtifact, "updated_at")
    assert hasattr(RawArtifact, "created_at")


# ── integrity gate ────────────────────────────────────────────────────────────
def test_integrity_gate_passes_clean(session, store):
    run = cap.start_run(session, source_system="test", run_kind="unit")
    cap.capture_bytes(session, run, b"one", artifact_kind="x", store=store)
    cap.capture_bytes(session, run, b"two", artifact_kind="x", store=store)
    session.commit()
    assert check_manifest(session, store) == []


def test_integrity_gate_catches_corruption(session, store):
    run = cap.start_run(session, source_system="test", run_kind="unit")
    art = cap.capture_bytes(session, run, b"intact", artifact_kind="x", store=store)
    session.commit()
    store.abspath_for(art.content_sha256).write_bytes(b"tampered different length")
    failures = check_manifest(session, store)
    assert any("CORRUPT" in f or "size" in f for f in failures)


def test_integrity_gate_catches_missing_blob(session, store):
    run = cap.start_run(session, source_system="test", run_kind="unit")
    art = cap.capture_bytes(session, run, b"will vanish", artifact_kind="x", store=store)
    session.commit()
    store.abspath_for(art.content_sha256).unlink()
    failures = check_manifest(session, store)
    assert any("MISSING" in f for f in failures)


def test_orphan_blob_detected_but_not_failed(session, store):
    run = cap.start_run(session, source_system="test", run_kind="unit")
    cap.capture_bytes(session, run, b"manifested", artifact_kind="x", store=store)
    session.commit()
    store.put(b"orphan with no manifest row")  # blob only, no DB row
    assert check_manifest(session, store) == []          # orphans don't fail the gate
    assert len(find_orphan_blobs(session, store)) == 1   # but they are reported
