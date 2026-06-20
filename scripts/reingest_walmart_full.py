"""
Full historical re-ingest of Walmart retailer_financials with the corrected, end-date-derived
fiscal labelling (see _fiscal_quarter_from_end in walmart_tier1_ingestion).

Why a dedicated driver: a routine run only refreshes QUARTER_FETCH_COUNT (=5) recent quarters,
and mark_latest supersedes on (retailer, fy, fq). Because the fix RELABELS the 2009-2013 era
(off-by-one) and adds quarters (FY2014, FY2025 Q2, real discrete Q4s), the changed (fy,fq)
keys would otherwise leave the old mislabelled rows as is_latest ghosts. So we:

  1. snapshot run_start
  2. re-ingest the FULL history (override the fetch count)
  3. retire every WMT row older than run_start that is still is_latest (the relabel ghosts)

Safe by construction: if step 2 fails, step 3 never runs, so the existing is_latest set is left
intact (no data loss). period_end_date is the true anchor and is preserved throughout; the SEC
reconciliation gate (anchored on period_end) is the post-hoc proof.

    .venv/bin/python -m scripts.reingest_walmart_full
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text

import data.ingestion.walmart_tier1_ingestion as wmt
from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.ingestion_context import IngestionContext

load_project_env()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("reingest_walmart_full")

FULL_HISTORY_QUARTERS = 80   # WMT has ~65 quarters of XBRL; 80 covers all with headroom


def main() -> int:
    wmt.QUARTER_FETCH_COUNT = FULL_HISTORY_QUARTERS          # override the 5-quarter default
    db = SessionLocal()
    try:
        rid = wmt._get_walmart_retailer_id(db)
        run_start = datetime.now(timezone.utc)
        before = db.execute(text(
            "SELECT count(*) FROM retailer_financials WHERE retailer_id=:r AND is_latest"),
            {"r": rid}).scalar()
        logger.info("WMT is_latest rows before: %d  (run_start=%s)", before, run_start.isoformat())

        with IngestionContext(source_name=wmt.SOURCE_NAME, script_version=wmt.SCRIPT_VERSION,
                              data_source_url=wmt._COMPANYFACTS_URL, db=db) as ctx:
            summaries = wmt.run_walmart_tier1_ingestion(db, ctx)
            if not summaries:
                ctx.set_failed("No quarters written")
                logger.error("Re-ingest produced no quarters — aborting (old rows untouched)")
                return 1
        db.commit()
        logger.info("Re-ingest wrote %d quarter(s)", len(summaries))

        # Retire the relabel ghosts: old is_latest rows the fresh run did not overwrite.
        retired = db.execute(text(
            "UPDATE retailer_financials SET is_latest=False "
            "WHERE retailer_id=:r AND is_latest AND pulled_at < :ts"),
            {"r": rid, "ts": run_start}).rowcount
        db.commit()
        after = db.execute(text(
            "SELECT count(*) FROM retailer_financials WHERE retailer_id=:r AND is_latest"),
            {"r": rid}).scalar()
        logger.info("Retired %d ghost row(s); WMT is_latest rows after: %d", retired, after)
        return 0
    except Exception:
        db.rollback()
        logger.exception("Re-ingest failed — rolled back; existing is_latest set intact")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
