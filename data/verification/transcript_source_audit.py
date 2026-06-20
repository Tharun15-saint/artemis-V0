"""
Per-quarter, per-year transcript SOURCE SELECTION audit (read-only, no billing).

For every quarter of every retailer we already track, this decides — on evidence — which
source holds the MOST COMPLETE, SEC-verified transcript:

  existing corpus  (v1.0/v2.0 = 8-K press-release bullets; v4.0 = fool/insidermonkey full call)
  vs  FMP          (fetched live: chars, full-call completeness, SEC-truth gate)
  + cross-source agreement (do our existing real-transcript passages appear in FMP?)

Decision rules (fidelity/completeness — NOT prediction; transcripts are records):
  - existing = 8-K bullets  -> use FMP IF complete+verified (real call > bullets); else KEEP 8-K + FLAG
  - existing = real call     -> KEEP it unless FMP is clearly MORE complete; never downgrade to a
                                truncated FMP (e.g. the TGT FY2024Q3 case: FMP 20k vs fool full)

Output: a precise per-quarter table + actionable lists. Nothing is written; this is the map the
extraction run follows.
"""

from __future__ import annotations

import argparse
import logging

from sqlalchemy import text

from data.ingestion._env import load_project_env
from data.ingestion.fmp_transcript_client import fetch_fmp_transcript, transcript_url
from data.verification.verify_transcript_faithfulness import _norm, _shingle_overlap
from data.verification.verify_transcript_source import verify_source

from database.base import SessionLocal
from database.models.retail import RetailerIntelligenceExtract as E

load_project_env()
logger = logging.getLogger("transcript_source_audit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

SYMBOL = {1: "TGT", 2: "WMT"}
COMPLETE_MIN = 25000


def _is_complete(txt: str) -> bool:
    low = txt.lower()
    return len(txt) >= COMPLETE_MIN and ("questions and answers" in low or "q&a" in low
                                         or low.count("question") >= 3 or low.count("analyst") >= 2)


def _existing_quarters(db, rid):
    rows = db.execute(text(
        "SELECT fiscal_year, fiscal_quarter, max(extraction_prompt_ver) ver, count(*) n "
        "FROM retailer_intelligence_extract WHERE is_latest AND retailer_id=:r "
        "GROUP BY fiscal_year, fiscal_quarter ORDER BY fiscal_year, fiscal_quarter"), {"r": rid}).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def _agreement(db, rid, fy, fq, fmp_norm):
    rows = (db.query(E.raw_text_passage)
            .filter(E.retailer_id == rid, E.fiscal_year == fy, E.fiscal_quarter == fq, E.is_latest.is_(True))
            .limit(12).all())
    checked = hit = 0
    for (p,) in rows:
        pn = _norm(p or "")
        if len(pn) < 30:
            continue
        checked += 1
        if pn in fmp_norm or _shingle_overlap(pn, fmp_norm) >= 0.85:
            hit += 1
    return (hit / checked) if checked else None


def decide(ver, fmp_ok, agreement):
    existing_is_call = ver in ("v2.0", "v4.0")
    if not existing_is_call:                                  # existing = 8-K bullets
        return "FMP_UPGRADE" if fmp_ok else "KEEP_8K_FLAG"
    if not fmp_ok:
        return "KEEP_EXISTING"                                # FMP weaker/truncated
    if agreement is not None and agreement < 0.6:
        return "KEEP_EXISTING"                                # FMP missing our content (less complete)
    return "KEEP_EXISTING_FMP_OK"                             # equivalent full calls


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--retailer-id", type=int, choices=[1, 2])
    args = p.parse_args()
    db = SessionLocal()
    try:
        rids = [args.retailer_id] if args.retailer_id else [2, 1]
        buckets = {}
        print(f"\n{'RET':<4}{'FY':<6}{'Q':<3}{'EXISTING':<10}{'sig':<5}{'FMPchars':<9}{'cmpl':<5}{'SECok':<6}{'agree':<7}DECISION")
        for rid in rids:
            sym = SYMBOL[rid]
            for fy, fq, ver, n in _existing_quarters(db, rid):
                content, meta = fetch_fmp_transcript(sym, fy, fq)
                chars = len(content)
                complete = _is_complete(content)
                verified = False
                if content and len(content) >= 5000:
                    v = verify_source(db, rid, fy, fq, transcript_url(sym, fy, fq), txt=content, call_date=meta.get("date"))
                    verified = (v["verdict"] == "VERIFIED")
                fmp_ok = bool(content and complete and verified)
                agreement = _agreement(db, rid, fy, fq, _norm(content)) if (content and ver in ("v2.0", "v4.0")) else None
                d = decide(ver, fmp_ok, agreement)
                buckets.setdefault(d, []).append((rid, fy, fq))
                ag = f"{agreement:.2f}" if agreement is not None else "-"
                exist = "8K" if ver in ("v1.0", "v2.0") and ver == "v1.0" else ver
                print(f"{sym:<4}{fy:<6}{fq:<3}{exist:<10}{n:<5}{chars:<9}{str(complete):<5}{str(verified):<6}{ag:<7}{d}", flush=True)
        print("\n===== SUMMARY (per decision) =====")
        for d, items in sorted(buckets.items()):
            print(f"  {d}: {len(items)}")
        print("\nFMP_UPGRADE (extract these from FMP):",
              ", ".join(f"{SYMBOL[r]}FY{y}Q{q}" for r, y, q in buckets.get("FMP_UPGRADE", [])))
        print("\nKEEP_8K_FLAG (FMP truncated/unverified — needs a fuller source):",
              ", ".join(f"{SYMBOL[r]}FY{y}Q{q}" for r, y, q in buckets.get("KEEP_8K_FLAG", [])))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
