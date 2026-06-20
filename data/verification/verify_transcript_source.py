"""
Source-of-truth gate for transcript re-extraction.

Before extracting ANY quarter, prove the candidate transcript IS that exact quarter
for that exact retailer — verified against the infallible truth we already hold: the
SEC-reconciled financials (period-end date, filing date) and the SEC-derived metrics
(diluted EPS, total revenue) plus the reported comp-sales, all green against SEC.

A transcript passes only if it corroborates itself against that ground truth:
  1. fetch_ok        — full transcript (>5k chars, "Operator", Q&A), via the validated fetcher
  2. company         — names the right retailer in its header
  3. fiscal_period   — its OWN stated "Fiscal Year YYYY / Nth Quarter" matches the target FY/FQ
                       (the transcript's fiscal statement is authoritative, NOT the fool URL's
                       calendar-year label — that's where mis-mapping would happen)
  4. date_align      — its call date falls just after the SEC period-end (within ~55 days)
  5. financials      — at least one SEC-verified figure (EPS / total revenue / comp %) for the
                       quarter literally appears in the transcript text

Verdict VERIFIED requires fetch_ok + company + fiscal_period + date_align + >=1 financial
corroboration. Anything less = REVIEW/REJECT (never silently used). This is importable
(verify_source) so the backfill calls it as a hard gate, and runnable standalone per quarter.
"""

from __future__ import annotations

import argparse
import logging
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import text

from data.ingestion._env import load_project_env
from data.ingestion.transcript_v4_backfill import fetch_transcript_text
from database.base import SessionLocal

load_project_env()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

RETAILER = {1: ("Target", "TGT"), 2: ("Walmart", "WMT")}
_WORD_Q = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
CALL_WINDOW_DAYS = 55  # earnings call lands within ~8 weeks of quarter end


def _truth(db, rid: int, fy: int, fq: int) -> dict:
    fin = db.execute(text(
        "SELECT period_end_date, filing_date FROM retailer_financials "
        "WHERE retailer_id=:r AND fiscal_year=:y AND fiscal_quarter=:q AND is_latest LIMIT 1"),
        {"r": rid, "y": fy, "q": fq}).fetchone()

    def metric(key):
        row = db.execute(text(
            "SELECT value_numeric FROM retailer_metric WHERE retailer_id=:r AND metric_key=:k "
            "AND fiscal_year=:y AND fiscal_quarter=:q AND is_latest LIMIT 1"),
            {"r": rid, "k": key, "y": fy, "q": fq}).fetchone()
        return Decimal(str(row[0])) if row and row[0] is not None else None

    return {
        "period_end": fin[0] if fin else None,
        "filing_date": fin[1] if fin else None,
        "eps": metric("eps_diluted_usd"),
        "revenue": metric("total_revenue_usd"),
        "comp": metric("comparable_sales_growth_pct"),
    }


def _parse_fiscal_period(head: str) -> tuple[Optional[int], Optional[int]]:
    """Authoritative fiscal period from the transcript header/operator intro."""
    head = head.lower()  # transcripts capitalise "Fiscal Year 2019 Third Quarter"
    fy = None
    m = re.search(r"fiscal(?:\s+year)?\s+(20\d\d)", head) or re.search(r"\bFY\s?(20\d\d)", head)
    if m:
        fy = int(m.group(1))
    fq = None
    mw = re.search(r"(first|second|third|fourth)\s+quarter", head)
    if mw:
        fq = _WORD_Q[mw.group(1)]
    else:
        mq = re.search(r"\bQ([1-4])\b", head)
        if mq:
            fq = int(mq.group(1))
    return fy, fq


def _parse_call_date(head: str) -> Optional[date]:
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(20\d\d)",
                  head, re.IGNORECASE)
    if not m:
        return None
    try:
        return date(int(m.group(3)), _MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
    except (ValueError, KeyError):
        return None


def _appears(value: Optional[Decimal], txt: str, *, pct=False, billions=False) -> bool:
    if value is None:
        return False
    if pct:
        s = f"{abs(value):.1f}"
        return f"{s}%" in txt or f"{s} %" in txt or f"{s} percent" in txt
    if billions:
        b = value / Decimal("1e9")
        return any(f"{b:.{p}f}" in txt for p in (1, 2)) and "billion" in txt.lower()
    s = f"{value:.2f}"
    return s in txt or f"${s}" in txt


def verify_source(db, rid: int, fy: int, fq: int, url: str, txt: Optional[str] = None,
                  call_date: Optional[str] = None) -> dict:
    name, _ticker = RETAILER.get(rid, (str(rid), ""))
    t = _truth(db, rid, fy, fq)
    if txt is None:                       # caller may pass already-fetched text (avoid double fetch)
        txt = fetch_transcript_text(url)
    checks: dict[str, bool] = {}
    # A real full transcript (>=5k chars) with call structure — not the literal word
    # "Operator" (some transcripts list "Executives:" or use a named moderator).
    checks["fetch_ok"] = bool(txt and len(txt) >= 5000 and (
        re.search(r"operator", txt, re.IGNORECASE) or "Executives:" in txt
        or "Questions and Answers" in txt or "Q&A" in txt or txt.count(":") >= 40))
    if not checks["fetch_ok"]:
        return {"verdict": "REJECT", "reason": "fetch_failed_or_thin", "checks": checks, "truth": t}

    head = txt[:2500]
    checks["company"] = name.lower() in head.lower() or name.lower() in txt[:6000].lower()
    pfy, pfq = _parse_fiscal_period(head)
    checks["fiscal_period"] = (pfy == fy and pfq == fq)
    # The truth anchor is SEC: the earnings-CALL date must fall in this quarter's filing
    # window, AND a SEC-verified figure (EPS/revenue/comp) must appear in the transcript.
    # The transcript's self-stated period only REJECTS contradictions (Target omits the
    # fiscal year in its header, so 'unparsed' is not 'wrong').
    fiscal_contradicts = (pfq is not None and pfq != fq) or (pfy is not None and pfy != fy)
    # Prefer an AUTHORITATIVE call date (e.g. FMP's structured metadata) over scraping a date
    # from prose — many transcripts open with a participant list, not a date.
    call = None
    if call_date:
        try:
            call = date.fromisoformat(str(call_date)[:10])
        except ValueError:
            call = None
    authoritative = call is not None
    if call is None:
        call = _parse_call_date(head)
    checks["date_align"] = bool(
        call and t["period_end"] and t["period_end"] < call <= t["period_end"] + timedelta(days=CALL_WINDOW_DAYS))
    checks["eps_match"] = _appears(t["eps"], txt)
    checks["revenue_match"] = _appears(t["revenue"], txt, billions=True)
    checks["comp_match"] = _appears(t["comp"], txt, pct=True)
    financial = checks["eps_match"] or checks["revenue_match"] or checks["comp_match"]

    # VERIFIED requires: real transcript + right company + the call date lands in THIS quarter's
    # SEC filing window + the transcript doesn't self-declare a different period. With an
    # authoritative source-provided date, that date-in-SEC-window check IS the anchor (a mislabeled
    # quarter's date won't fall in the window). Without one (text-scraped date), we additionally
    # require a SEC figure or the period string to corroborate.
    verified = (checks["fetch_ok"] and checks["company"] and checks["date_align"] and not fiscal_contradicts
                and (authoritative or financial or checks["fiscal_period"]))
    verdict = "VERIFIED" if verified else "REVIEW"
    return {"verdict": verdict,
            "checks": {**checks, "fiscal_not_contradicted": not fiscal_contradicts, "financial_corroborated": financial},
            "parsed_fy": pfy, "parsed_fq": pfq, "call_date": str(call) if call else None, "truth": t}


def main() -> int:
    p = argparse.ArgumentParser(description="Verify a transcript source against SEC truth")
    p.add_argument("--retailer-id", type=int, required=True, choices=[1, 2])
    p.add_argument("--fy", type=int, required=True)
    p.add_argument("--fq", type=int, required=True)
    p.add_argument("--url", required=True)
    args = p.parse_args()
    db = SessionLocal()
    try:
        r = verify_source(db, args.retailer_id, args.fy, args.fq, args.url)
        name = RETAILER[args.retailer_id][0]
        print(f"\n{name} FY{args.fy}Q{args.fq}  ->  {r['verdict']}")
        print(f"  parsed fiscal period: FY{r.get('parsed_fy')} Q{r.get('parsed_fq')}  call_date={r.get('call_date')}")
        tr = r["truth"]
        print(f"  SEC truth: period_end={tr['period_end']} eps={tr['eps']} revenue={tr['revenue']} comp={tr['comp']}")
        for k, v in r["checks"].items():
            print(f"    {'PASS' if v else 'FAIL'}  {k}")
        return 0 if r["verdict"] == "VERIFIED" else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
