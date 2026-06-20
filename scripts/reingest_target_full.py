"""
Full historical re-ingest of Target retailer_financials with the corrected, end-date-derived
fiscal labelling (see _fiscal_quarter_from_end in target_tier1_ingestion).

Mirror of scripts/reingest_walmart_full.py — same rationale: a routine run only refreshes
QUARTER_FETCH_COUNT (=5) quarters, and mark_latest supersedes on (retailer, fy, fq). The fix
fills the FY2022 Q3 hole (SEC mis-tagged it fy=2023, colliding it with the real FY2023 Q3) and
hardens every quarter against the SEC `fy`/`fp` drift. Clean-delete TGT first so changed keys
leave no is_latest ghosts.

Safe by construction: if the re-ingest fails, the ghost-retirement never runs. target_tier1
self-derives comps + apparel + guidance per quarter, so a full rebuild is self-contained.

    .venv/bin/python -m scripts.reingest_target_full
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text

import data.ingestion.target_tier1_ingestion as tgt
from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.ingestion_context import IngestionContext

load_project_env()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("reingest_target_full")

FULL_HISTORY_QUARTERS = 90   # TGT has ~70 quarters of XBRL; 90 covers all with headroom


def main() -> int:
    tgt.QUARTER_FETCH_COUNT = FULL_HISTORY_QUARTERS
    db = SessionLocal()
    try:
        rid = tgt._get_target_retailer_id(db)
        run_start = datetime.now(timezone.utc)
        before = db.execute(text(
            "SELECT count(*) FROM retailer_financials WHERE retailer_id=:r AND is_latest"),
            {"r": rid}).scalar()
        logger.info("TGT is_latest rows before: %d  (run_start=%s)", before, run_start.isoformat())

        with IngestionContext(source_name=tgt.SOURCE_NAME, script_version=tgt.SCRIPT_VERSION,
                              data_source_url=tgt._COMPANYFACTS_URL, db=db) as ctx:
            summaries = tgt.run_target_tier1_ingestion(db, ctx)
            if not summaries:
                ctx.set_failed("No quarters written")
                logger.error("Re-ingest produced no quarters — aborting (old rows untouched)")
                return 1
        db.commit()
        logger.info("Re-ingest wrote %d quarter(s)", len(summaries))

        retired = db.execute(text(
            "UPDATE retailer_financials SET is_latest=False "
            "WHERE retailer_id=:r AND is_latest AND pulled_at < :ts"),
            {"r": rid, "ts": run_start}).rowcount
        db.commit()
        after = db.execute(text(
            "SELECT count(*) FROM retailer_financials WHERE retailer_id=:r AND is_latest"),
            {"r": rid}).scalar()
        logger.info("Retired %d ghost row(s); TGT is_latest rows after: %d", retired, after)
        return 0
    except Exception:
        db.rollback()
        logger.exception("Re-ingest failed — rolled back; existing is_latest set intact")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
