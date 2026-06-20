"""
Re-extract the v1.0/v2.0 (8-K-sourced) quarters from REAL earnings-call transcripts via FMP,
at the rigorous v4.0 prompt — every quarter gated against SEC truth before extraction.

Per quarter:  FMP fetch  ->  verify_source (SEC-truth gate)  ->  process_transcript (v4.0)
              ->  demote old v1.0 signals (handled inside process_transcript).

Discipline:
  - VERIFIED-only: a transcript that doesn't pass the SEC-truth gate (date + financials +
    no period contradiction) is SKIPPED, never extracted. Honest coverage gaps are logged.
  - Idempotent; --dry-run runs the full fetch+verify audit with NO extraction (no billing).
  - --retailer / --fy / --fq / --limit to scope; single-quarter test before the full run.
"""

from __future__ import annotations

import argparse
import logging
import time

from sqlalchemy import text

from data.ingestion._env import load_project_env
from data.ingestion.earnings_transcript_ingestion import process_transcript
from data.ingestion.fmp_transcript_client import fetch_fmp_transcript, transcript_url
from data.verification.verify_transcript_source import verify_source
from database.base import SessionLocal

load_project_env()
logger = logging.getLogger("fmp_transcript_backfill")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

SYMBOL = {1: "TGT", 2: "WMT"}
# A real full call = prepared remarks + a Q&A section. FMP sometimes returns a truncated
# (prepared-remarks-only) transcript; we must NOT ingest that as the whole call.
COMPLETE_MIN_CHARS = 25000


def _is_complete(txt: str) -> bool:
    low = txt.lower()
    has_qa = ("questions and answers" in low or "q&a" in low
              or low.count("question") >= 3 or low.count("analyst") >= 2)
    return len(txt) >= COMPLETE_MIN_CHARS and has_qa


def quarters_to_do(db, rid, fy, fq):
    sql = ("SELECT retailer_id, fiscal_year, fiscal_quarter FROM retailer_intelligence_extract "
           "WHERE is_latest AND extraction_prompt_ver IN ('v1.0','v2.0') ")
    if rid:
        sql += f" AND retailer_id={int(rid)} "
    if fy:
        sql += f" AND fiscal_year={int(fy)} "
    if fq:
        sql += f" AND fiscal_quarter={int(fq)} "
    sql += " GROUP BY 1,2,3 ORDER BY 1,2,3"
    return [(r[0], r[1], r[2]) for r in db.execute(text(sql)).fetchall()]


def run(rid=None, fy=None, fq=None, dry_run=False, limit=None) -> dict:
    db = SessionLocal()
    try:
        qs = quarters_to_do(db, rid, fy, fq)
        if limit:
            qs = qs[:limit]
        res = {"total": len(qs), "verified": 0, "extracted": 0, "signals": 0,
               "no_coverage": 0, "not_verified": 0, "truncated": 0}
        for i, (r, y, q) in enumerate(qs):
            sym = SYMBOL[r]
            content, meta = fetch_fmp_transcript(sym, y, q)
            if not content or len(content) < 5000:
                res["no_coverage"] += 1
                logger.info("NO COVERAGE  %s FY%sQ%s", sym, y, q)
                continue
            v = verify_source(db, r, y, q, transcript_url(sym, y, q), txt=content, call_date=meta.get("date"))
            if v["verdict"] != "VERIFIED":
                res["not_verified"] += 1
                fails = [c for c, ok in v["checks"].items() if not ok]
                logger.warning("NOT VERIFIED %s FY%sQ%s vs SEC -> %s (SKIP)", sym, y, q, fails)
                continue
            if not _is_complete(content):
                res["truncated"] += 1
                logger.warning("TRUNCATED    %s FY%sQ%s (%d chars) — verified but partial (no full Q&A); "
                               "keeping 8-K fallback, FLAG for a fuller source (SKIP)", sym, y, q, len(content))
                continue
            res["verified"] += 1
            if dry_run:
                logger.info("DRY VERIFIED %s FY%sQ%s (%d chars, date=%s)", sym, y, q, len(content), meta.get("date"))
                continue
            stats = process_transcript(
                content, retailer_id=r, fiscal_year=y, fiscal_quarter=q,
                source_url=transcript_url(sym, y, q), source_format="fmp")
            if stats.signals_extracted == 0:
                logger.error("EXTRACTED 0 signals %s FY%sQ%s (%d-char transcript) — investigate",
                             sym, y, q, len(content))
            else:
                res["extracted"] += 1
                res["signals"] += stats.signals_extracted
                logger.info("EXTRACTED    %s FY%sQ%s -> %d signals", sym, y, q, stats.signals_extracted)
            time.sleep(1)
        logger.info("DONE %s — verified&complete=%d extracted=%d signals=%d | no_coverage=%d not_verified=%d truncated=%d (of %d)",
                    "DRY-RUN" if dry_run else "RUN", res["verified"], res["extracted"], res["signals"],
                    res["no_coverage"], res["not_verified"], res["truncated"], res["total"])
        return res
    finally:
        db.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Re-extract v1.0/v2.0 quarters from FMP transcripts (SEC-gated)")
    p.add_argument("--retailer-id", type=int, choices=[1, 2])
    p.add_argument("--fy", type=int)
    p.add_argument("--fq", type=int)
    p.add_argument("--limit", type=int)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(args.retailer_id, args.fy, args.fq, args.dry_run, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
