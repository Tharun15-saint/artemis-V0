"""
FMP pre-purchase probe — verify, for $0 of our effort and before any annual commit, that
FMP earnings-call transcripts are (1) ACCESSIBLE on this key/tier, (2) COVER our exact
quarters, (3) are REAL full transcripts that pass our SEC-truth gate, and (4) carry the
apparel/sourcing/demand COLOR the vision needs.

Read-only. Tries the documented FMP endpoint variants (the exact paths/tiers differ by
plan), reports what works, cross-checks coverage against the quarters we still need, and
runs one sample transcript through verify_source (SEC ground truth).

    python -m data.verification.fmp_probe
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict

import requests
from sqlalchemy import text

from data.ingestion._env import load_project_env
from data.verification.verify_transcript_source import verify_source
from database.base import SessionLocal

load_project_env()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

BASE = "https://financialmodelingprep.com"
SYMS = {2: "WMT", 1: "TGT"}


def _get(url: str):
    try:
        r = requests.get(url, timeout=30)
        ct = r.headers.get("Content-Type", "")
        body = r.json() if "json" in ct or url_ok(r) else r.text[:200]
        return r.status_code, body
    except requests.RequestException as exc:
        return None, str(exc)


def url_ok(r) -> bool:
    try:
        r.json()
        return True
    except ValueError:
        return False


def _mask(url: str, key: str) -> str:
    return url.replace(key, "***") if key else url


def list_available(sym: str, key: str):
    """Try the known 'available transcripts' endpoints; return parsed [(year,quarter,date)]."""
    variants = [
        f"{BASE}/api/v4/earning_call_transcript?symbol={sym}&apikey={key}",
        f"{BASE}/stable/earnings-transcript-list?symbol={sym}&apikey={key}",
        f"{BASE}/api/v3/earning_call_transcript/{sym}?apikey={key}",
    ]
    for url in variants:
        status, body = _get(url)
        print(f"  [list] {status}  {_mask(url, key)}")
        if status == 200 and isinstance(body, list) and body:
            out = []
            for item in body:
                if isinstance(item, list) and len(item) >= 2:            # [quarter, year, date]
                    out.append((item[1], item[0], item[2] if len(item) > 2 else None))
                elif isinstance(item, dict):
                    out.append((item.get("year"), item.get("quarter"), item.get("date")))
            if out:
                return url, out
        elif status in (401, 402, 403):
            print(f"     -> access denied (tier?): {str(body)[:120]}")
    return None, []


def fetch_one(sym: str, year, quarter, key: str):
    variants = [
        f"{BASE}/stable/earning-call-transcript?symbol={sym}&year={year}&quarter={quarter}&apikey={key}",
        f"{BASE}/api/v3/earning_call_transcript/{sym}?year={year}&quarter={quarter}&apikey={key}",
    ]
    for url in variants:
        status, body = _get(url)
        if status == 200 and isinstance(body, list) and body and isinstance(body[0], dict):
            content = body[0].get("content") or ""
            if content:
                return url, content, body[0]
        print(f"  [fetch] {status}  {_mask(url, key)} -> {str(body)[:100]}")
    return None, None, None


def main() -> int:
    key = os.getenv("FMP_API_KEY")
    if not key:
        print("FMP_API_KEY not set in .env — add it and re-run.")
        return 1
    print(f"FMP_API_KEY loaded ({key[:3]}***{key[-2:]}, len={len(key)})\n")
    db = SessionLocal()
    try:
        needed = defaultdict(set)
        for r in db.execute(text(
            "SELECT retailer_id, fiscal_year, fiscal_quarter FROM retailer_intelligence_extract "
            "WHERE is_latest AND extraction_prompt_ver IN ('v1.0','v2.0') "
            "GROUP BY 1,2,3")).fetchall():
            needed[r[0]].add((r[1], r[2]))

        for rid, sym in SYMS.items():
            print(f"=== {sym} (retailer_id={rid}) ===")
            used_url, avail = list_available(sym, key)
            if not avail:
                print("  NO available-transcript list returned (endpoint/tier issue).\n")
                continue
            yrs = sorted({a[0] for a in avail if a[0]})
            print(f"  FMP lists {len(avail)} transcripts, years {min(yrs)}-{max(yrs)} (via {_mask(used_url,key)})")
            print(f"  sample: {avail[:3]}")
            print(f"  WE STILL NEED {len(needed[rid])} quarters for this retailer "
                  f"(FY {min(q[0] for q in needed[rid])}-{max(q[0] for q in needed[rid])})")

        # quality + SEC-gate + vision-fit on ONE Walmart sample (try fiscal & calendar labels)
        print("\n=== SAMPLE QUALITY / SEC-TRUTH / VISION-FIT (Walmart) ===")
        got = False
        for (y, q, fy, fq) in [(2024, 3, 2025, 3), (2025, 3, 2025, 3), (2018, 3, 2019, 3), (2019, 3, 2019, 3)]:
            url, content, meta = fetch_one("WMT", y, q, key)
            if not content:
                continue
            print(f"  fetched WMT FMP(year={y},q={q}) -> {len(content)} chars, "
                  f"Operator={'Operator' in content}, date={meta.get('date')}")
            v = verify_source(db, 2, fy, fq, url, txt=content)
            print(f"  SEC-truth gate for WMT FY{fy}Q{fq}: {v['verdict']}  checks={ {k:x for k,x in v['checks'].items()} }")
            print("  --- content snippet (vision-fit eyeball) ---")
            print("  " + " ".join(content[:400].split()))
            got = True
            break
        if not got:
            print("  Could not fetch a sample transcript — likely the transcript endpoint needs a higher tier.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
