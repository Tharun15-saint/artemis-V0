"""
Capture the RAW, untouched FMP earnings-call-transcript payloads into the immutable L1
store (medallion Layer 1), before any parsing/extraction. Critical for scale: lets us
re-derive signals (e.g. re-extract with a better prompt) deterministically and OFFLINE,
without ever re-hitting FMP, and preserves the exact bytes we paid for as the reference
of record.

L1 preserves EVERYTHING FMP returns — complete, partial, even empty-content responses are
recorded as raw (the truth of what the source gave us). Completeness/verification judgement
happens later at L2 (extraction), never here. Bytes are content-addressed + deduped by the
CAS; re-running is safe (identical bytes are not re-stored).

    python -m data.ingestion.fmp_transcript_capture            # all tracked quarters
    python -m data.ingestion.fmp_transcript_capture --retailer-id 1
"""

from __future__ import annotations

import argparse
import logging
import os
import time

import requests
from sqlalchemy import text

from data.ingestion._env import load_project_env
from data.ingestion.fmp_transcript_client import transcript_url
from data.raw.capture import capture_bytes, finish_run, start_run
from database.base import SessionLocal

load_project_env()
logger = logging.getLogger("fmp_transcript_capture")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

SYMBOL = {1: "TGT", 2: "WMT"}
_ENDPOINT = "https://financialmodelingprep.com/stable/earning-call-transcript"


def _quarters(db, rid):
    sql = ("SELECT DISTINCT retailer_id, fiscal_year, fiscal_quarter FROM retailer_intelligence_extract "
           "WHERE is_latest ")
    if rid:
        sql += f" AND retailer_id={int(rid)} "
    sql += " ORDER BY retailer_id, fiscal_year, fiscal_quarter"
    return [(r[0], r[1], r[2]) for r in db.execute(text(sql)).fetchall()]


def main() -> int:
    p = argparse.ArgumentParser(description="Capture raw FMP transcript payloads into L1")
    p.add_argument("--retailer-id", type=int, choices=[1, 2])
    args = p.parse_args()
    key = os.getenv("FMP_API_KEY")
    if not key:
        print("FMP_API_KEY not set")
        return 1

    db = SessionLocal()
    run = start_run(db, source_system="fmp", run_kind="fmp_transcript_capture",
                    parameters={"retailer_id": args.retailer_id},
                    operator=f"cli:{os.getenv('USER', 'unknown')}")
    captured = empty = 0
    try:
        for rid, fy, fq in _quarters(db, args.retailer_id):
            sym = SYMBOL[rid]
            url = f"{_ENDPOINT}?symbol={sym}&year={fy}&quarter={fq}&apikey={key}"
            try:
                r = requests.get(url, timeout=45)
            except requests.RequestException as exc:
                logger.warning("fetch failed %s FY%sQ%s: %s", sym, fy, fq, exc)
                continue
            if r.status_code != 200 or not r.content or len(r.content) < 200:
                empty += 1
                logger.info("no raw payload %s FY%sQ%s (status %s, %d bytes)", sym, fy, fq, r.status_code, len(r.content))
                continue
            capture_bytes(
                db, run, r.content,
                artifact_kind="fmp_earnings_transcript",
                source_system="fmp",
                media_type="application/json",
                source_uri=transcript_url(sym, fy, fq),      # key stripped
                source_locator={"symbol": sym, "fiscal_year": fy, "fiscal_quarter": fq},
            )
            db.commit()                                       # durable per quarter
            captured += 1
            logger.info("captured raw %s FY%sQ%s (%d bytes)", sym, fy, fq, len(r.content))
            time.sleep(0.4)
        finish_run(db, run, status="completed")
        print(f"\n✓ raw FMP transcript capture: {captured} artifacts stored, {empty} empty/no-payload")
        return 0
    except Exception as exc:                                  # noqa: BLE001
        finish_run(db, run, status="failed", error_message=str(exc))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
