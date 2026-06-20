"""
Discover fool.com transcript URLs for the v1.0/v2.0 (8-K-sourced) quarters — without
manual search — by CONSTRUCTING candidates from SEC truth and confirming each against it.

fool URL = /earnings/call-transcripts/{YYYY}/{MM}/{DD}/{company}-{ticker}-q{N}-{CALYEAR}-...
The date path = the earnings-release date = our SEC filing_date; fool labels by the call's
calendar year = filing_date.year. So we build candidates (slug/format/date-offset variants),
fetch each (cheap 404s skip fast), and accept ONLY a URL that passes verify_source against
SEC truth (fiscal period + call date + EPS/revenue/comp).

Output: a paste-ready MANUAL_URL_OVERRIDES block of VERIFIED quarters + a list of the ones
that didn't construct (need manual discovery — honest gaps, never guessed).
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import timedelta

import requests
from sqlalchemy import text

from data.ingestion._env import load_project_env
from data.verification.verify_transcript_source import verify_source
from database.base import SessionLocal

_HDRS = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")}


def _is_200(url: str) -> bool:
    """Cheap existence check — a constructed 404 skips instantly, no full download / 90s retry."""
    try:
        r = requests.get(url, headers=_HDRS, timeout=12, stream=True)
        code = r.status_code
        r.close()
        return code == 200
    except requests.RequestException:
        return False

load_project_env()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

BASE = "https://www.fool.com/earnings/call-transcripts/{y}/{m:02d}/{d:02d}/{slug}-q{fq}-{cal}-{tail}"


def _quarters(db, rid):
    sql = ("SELECT e.retailer_id, e.fiscal_year, e.fiscal_quarter, MAX(e.period_end_date) AS period_end "
           "FROM retailer_intelligence_extract e "
           "WHERE e.is_latest AND e.extraction_prompt_ver IN ('v1.0','v2.0') ")
    if rid:
        sql += f" AND e.retailer_id={int(rid)} "
    sql += " GROUP BY e.retailer_id, e.fiscal_year, e.fiscal_quarter ORDER BY 1,2,3"
    return [dict(r._mapping) for r in db.execute(text(sql)).fetchall()]


def _candidates(rid, fy, fq, period_end):
    """fool URL date = earnings-CALL date (~period_end + offset). Patterns differ by retailer
    (learned from the corpus): Target is clean (target-tgt, fiscal-year label, full tail);
    Walmart varies in slug, label (calendar vs fiscal), and tail truncation."""
    offs = (30, 31, 32, 29, 33, 28, 34, 35) if fq == 4 else (18, 19, 17, 20, 21, 16, 22, 23, 15)
    if rid == 1:  # Target — predictable
        slugs = ["target-tgt", "target-corporation-tgt"]
        labels = [fy]
        tails = ["earnings-call-transcript/", "earnings-conference-call-transcript.aspx",
                 "earnings-conference-call-t.aspx"]
    else:  # Walmart — messy: try both label conventions + observed slug/tail variants
        slugs = ["walmart-inc-wmt", "wal-mart-stores-inc-wmt", "walmart-wmt", "wal-mart-inc-wmt"]
        labels = [fy, (period_end + timedelta(days=20)).year]
        tails = ["earnings-call-transcript/", "earnings-call-tran/",
                 "earnings-conference-call-t.aspx", "earnings-conference-call-transcript.aspx"]
    urls = []
    for off in offs:
        d = period_end + timedelta(days=off)
        for slug in slugs:
            for lab in labels:
                for tail in tails:
                    urls.append(BASE.format(y=d.year, m=d.month, d=d.day, slug=slug,
                                            fq=fq, cal=lab, tail=tail))
    return urls


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--retailer-id", type=int, choices=[1, 2])
    p.add_argument("--max-candidates", type=int, default=24, help="cap fetches per quarter")
    args = p.parse_args()
    db = SessionLocal()
    try:
        quarters = _quarters(db, args.retailer_id)
        verified, missing = {}, []
        for q in quarters:
            rid, fy, fq, pe = q["retailer_id"], q["fiscal_year"], q["fiscal_quarter"], q["period_end"]
            if pe is None:
                missing.append((rid, fy, fq, "no_period_end"))
                continue
            hit = None
            for url in _candidates(rid, fy, fq, pe)[:args.max_candidates]:
                if not _is_200(url):          # cheap skip for the many wrong constructions
                    time.sleep(0.4)
                    continue
                v = verify_source(db, rid, fy, fq, url)   # full fetch + SEC truth gate
                time.sleep(0.8)
                if v["verdict"] == "VERIFIED":
                    hit = url
                    break
            if hit:
                verified[(rid, fy, fq)] = hit
                print(f"VERIFIED {rid} FY{fy}Q{fq} -> {hit}", flush=True)
            else:
                missing.append((rid, fy, fq, "no_constructed_url_verified"))
                print(f"MISS     {rid} FY{fy}Q{fq} (period_end {pe})", flush=True)

        print(f"\n===== VERIFIED {len(verified)} / {len(quarters)} quarters =====")
        for (rid, fy, fq), url in sorted(verified.items()):
            print(f'    ({rid}, {fy}, {fq}): "{url}",')
        print(f"\n===== MISSING {len(missing)} (need manual discovery) =====")
        for rid, fy, fq, why in missing:
            print(f"    ({rid}, {fy}, {fq})  {why}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
