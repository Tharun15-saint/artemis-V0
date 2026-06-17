"""
v4.0 transcript re-extraction backfill for FY2021+ quarters (Target + Walmart).

Fetches the full earnings-call transcript for each quarter and re-extracts with the
v4.0 prompt (23-category taxonomy + business_segment + time_horizon) via
process_transcript() in earnings_transcript_ingestion.py.

Sources (source-aware fetcher, validated selectors):
  - fool.com           -> div.transcript-content
  - insidermonkey.com  -> div.single-content
Motley Fool stopped publishing free transcripts for recent quarters, so those are
covered via MANUAL_URL_OVERRIDES pointing at Insider Monkey (verified full
transcripts). URLs are never guessed — every override was fetch-verified
(HTTP 200, >5,000 chars, "Operator" present).

Discipline:
  - fool.com wins over sec.gov for a quarter (COALESCE in the query); a quarter
    with no fool.com URL and no override is SKIPPED (logged), never fed a SEC
    press-release exhibit.
  - Overrides also INJECT quarters that have no rows in the extract table yet
    (e.g. Walmart FY2026 Q1/Q2).
  - Idempotent: quarters already carrying v4.0 is_latest=1 rows are skipped
    unless --force.
  - Fetched text is validated (>5,000 chars AND "Operator") before any extraction;
    failures are logged and skipped, never silently swallowed.

Run:
  python data/ingestion/transcript_v4_backfill.py --dry-run
  python data/ingestion/transcript_v4_backfill.py --retailer-id 2 --fiscal-year 2027 --fiscal-quarter 1
  python data/ingestion/transcript_v4_backfill.py            # full backfill
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup
from sqlalchemy import text

from data.ingestion._env import load_project_env
from data.ingestion.earnings_transcript_ingestion import process_transcript
from database.base import SessionLocal

load_project_env()

logger = logging.getLogger("transcript_v4_backfill")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

BACKFILL_START = "2021-01-01"
REQUEST_TIMEOUT = 30
BETWEEN_TRANSCRIPT_SLEEP = 4  # seconds — polite to the source sites
MIN_TRANSCRIPT_CHARS = 5000
FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_RETAILER_NAME = {1: "Target", 2: "Walmart"}

# Verified transcript URLs for quarters Motley Fool no longer covers (recent) or
# that had no fool.com URL stored. Every URL below was fetch-verified:
# HTTP 200, >5,000 chars via its source's selector, "Operator" present.
# Keyed by (retailer_id, fiscal_year, fiscal_quarter).
MANUAL_URL_OVERRIDES: dict[tuple[int, int, int], str] = {
    (2, 2026, 1): "https://www.insidermonkey.com/blog/walmart-inc-nysewmt-q1-2026-earnings-call-transcript-1535792/",
    (2, 2026, 2): "https://www.insidermonkey.com/blog/walmart-inc-nysewmt-q2-2026-earnings-call-transcript-1595564/",
    (2, 2026, 3): "https://www.insidermonkey.com/blog/walmart-inc-nysewmt-q3-2026-earnings-call-transcript-1648901/",
    (1, 2025, 1): "https://www.insidermonkey.com/blog/target-corporation-nysetgt-q1-2025-earnings-call-transcript-1539267/",
    (1, 2025, 2): "https://www.insidermonkey.com/blog/target-corporation-nysetgt-q2-2025-earnings-call-transcript-1594867/",
    (1, 2025, 3): "https://www.insidermonkey.com/blog/target-corporation-nysetgt-q3-2025-earnings-call-transcript-1648382/",
    (1, 2021, 1): "https://www.fool.com/earnings/call-transcripts/2021/05/19/target-tgt-q1-2021-earnings-call-transcript/",
    (1, 2020, 4): "https://www.fool.com/earnings/call-transcripts/2021/03/02/target-tgt-q4-2020-earnings-call-transcript/",
}

# Quarters whose earnings call happened but have NO extractable transcript anywhere.
# The signals for these stay sourced from the SEC press-release exhibit (8-K Ex-99).
# Documented, not silently skipped.
DOCUMENTED_GAPS: dict[tuple[int, int, int], str] = {
    # Target FY2025Q4 call was 2026-03-03; the only transcript copy is on Seeking Alpha,
    # which is paywalled/bot-blocked (fetch returns a login stub, no "Operator"). No
    # extractable copy on fool.com / Insider Monkey / Investing.com / MarketBeat.
    (1, 2025, 4): "press_release_only_no_earnings_call_transcript_found",
}

_QUARTERS_SQL = """
    SELECT
        retailer_id, fiscal_year, fiscal_quarter,
        MAX(period_end_date) AS period_end_date,
        COALESCE(
            MAX(CASE WHEN source_url LIKE '%fool.com%' THEN source_url END),
            MAX(source_url)
        ) AS source_url,
        MAX(CASE WHEN source_url LIKE '%fool.com%' THEN 1 ELSE 0 END) AS has_fool
    FROM retailer_intelligence_extract
    WHERE period_end_date >= :start AND source_url IS NOT NULL AND source_url != ''
      {retailer_filter}
    GROUP BY retailer_id, fiscal_year, fiscal_quarter
"""

_V4_DONE_SQL = """
    SELECT DISTINCT retailer_id, fiscal_year, fiscal_quarter
    FROM retailer_intelligence_extract
    WHERE extraction_prompt_ver = 'v4.0' AND is_latest = 1
"""


def _period_end_for(db, rid: int, fy: int, fq: int) -> Optional[str]:
    row = db.execute(
        text(
            "SELECT period_end_date FROM retailer_financials "
            "WHERE retailer_id=:r AND fiscal_year=:y AND fiscal_quarter=:q AND is_latest=1 LIMIT 1"
        ),
        {"r": rid, "y": fy, "q": fq},
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def get_quarters(db, retailer_id, fy, fq) -> list[dict]:
    filt, params = "", {"start": BACKFILL_START}
    if retailer_id:
        filt += " AND retailer_id = :rid"; params["rid"] = retailer_id
    if fy:
        filt += " AND fiscal_year = :fy"; params["fy"] = fy
    if fq:
        filt += " AND fiscal_quarter = :fq"; params["fq"] = fq
    rows = db.execute(text(_QUARTERS_SQL.format(retailer_filter=filt)), params).fetchall()
    by_key: dict[tuple[int, int, int], dict] = {}
    for r in rows:
        m = dict(r._mapping)
        by_key[(m["retailer_id"], m["fiscal_year"], m["fiscal_quarter"])] = m

    # Apply overrides (replace URL, mark usable) and inject override-only quarters.
    for key, url in MANUAL_URL_OVERRIDES.items():
        if retailer_id and key[0] != retailer_id:
            continue
        if fy and key[1] != fy:
            continue
        if fq and key[2] != fq:
            continue
        if key in by_key:
            by_key[key]["source_url"] = url
            by_key[key]["override"] = True
        else:
            by_key[key] = {
                "retailer_id": key[0], "fiscal_year": key[1], "fiscal_quarter": key[2],
                "period_end_date": _period_end_for(db, *key), "source_url": url,
                "has_fool": 0, "override": True,
            }

    out = list(by_key.values())
    out.sort(key=lambda q: (q["retailer_id"], -q["fiscal_year"], -q["fiscal_quarter"]))
    return out


def v4_done_set(db) -> set[tuple[int, int, int]]:
    return {(r[0], r[1], r[2]) for r in db.execute(text(_V4_DONE_SQL)).fetchall()}


def _selector_for(url: str):
    if "insidermonkey.com" in url:
        return ("single-content", None)
    return ("transcript-content", "article-body-transcript")  # fool.com


def fetch_transcript_text(url: str, _retried: bool = False) -> Optional[str]:
    """Source-aware fetch: pick the validated selector by domain.

    On HTTP 429 (rate limited, e.g. Insider Monkey), sleep 90s and retry once;
    if the retry still 429s or fails, return None so the caller logs a skip.
    """
    try:
        resp = requests.get(url, headers=FETCH_HEADERS, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        logger.error("fetch failed for %s: %s", url, exc)
        return None
    if resp.status_code == 429 and not _retried:
        logger.warning("429 (rate limited) from %s — sleeping 90s then retrying once", url)
        time.sleep(90)
        return fetch_transcript_text(url, _retried=True)
    if resp.status_code != 200:
        logger.error("HTTP %s for %s", resp.status_code, url)
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    cls, alt_id = _selector_for(url)
    node = soup.find("div", class_=cls) or (soup.find("div", id=alt_id) if alt_id else None)
    if node is None:
        logger.error("no transcript container (%s) found at %s", cls, url)
        return None
    for t in node(["script", "style", "aside", "figure"]):
        t.decompose()
    return "\n".join(ln.strip() for ln in node.get_text(separator="\n", strip=True).splitlines() if ln.strip())


def _label(q: dict) -> str:
    return f"{_RETAILER_NAME.get(q['retailer_id'], q['retailer_id'])} FY{q['fiscal_year']}Q{q['fiscal_quarter']}"


def _has_source(q: dict) -> bool:
    return bool(q["has_fool"]) or bool(q.get("override"))


def run(retailer_id=None, fy=None, fq=None, dry_run=False, force=False) -> dict:
    db = SessionLocal()
    try:
        quarters = get_quarters(db, retailer_id, fy, fq)
        done = set() if force else v4_done_set(db)

        to_process, skip_no_src, skip_v4 = [], [], []
        for q in quarters:
            key = (q["retailer_id"], q["fiscal_year"], q["fiscal_quarter"])
            if not _has_source(q):
                skip_no_src.append(q)
            elif key in done:
                skip_v4.append(q)
            else:
                to_process.append(q)

        if dry_run:
            avg = db.execute(
                text(
                    "SELECT CAST(ROUND(AVG(c)) AS INT) FROM (SELECT COUNT(*) c FROM "
                    "retailer_intelligence_extract WHERE is_latest=1 AND period_end_date>=:s "
                    "GROUP BY retailer_id, fiscal_year, fiscal_quarter)"
                ),
                {"s": BACKFILL_START},
            ).scalar() or 30
            print("\n================ DRY RUN — transcript_v4_backfill ================\n")
            print(f"{'#':>3}  {'QUARTER':<16} {'PERIOD END':<12} {'SOURCE':<13} URL")
            for i, q in enumerate(to_process, 1):
                src = "insidermonkey" if "insidermonkey" in q["source_url"] else "fool.com"
                tag = " (override)" if q.get("override") else ""
                print(f"{i:>3}  {_label(q):<16} {str(q['period_end_date']):<12} {src+tag:<13} {q['source_url']}")
            print(f"\nQuarters to PROCESS : {len(to_process)}  "
                  f"(fool.com={sum(1 for q in to_process if 'fool.com' in q['source_url'])}, "
                  f"insidermonkey={sum(1 for q in to_process if 'insidermonkey' in q['source_url'])})")
            if skip_v4:
                print(f"\nSkipped (already v4.0): {len(skip_v4)}")
            if skip_no_src:
                print(f"\nSkipped (NO transcript source): {len(skip_no_src)}")
                for q in skip_no_src:
                    gap = DOCUMENTED_GAPS.get((q["retailer_id"], q["fiscal_year"], q["fiscal_quarter"]))
                    note = f"DOCUMENTED GAP: source='{gap}'" if gap else f"only: {q['source_url']}"
                    print(f"     - {_label(q)}  ({note})")
            print(f"\nEstimated signals  : ~{len(to_process)} quarters x ~{avg}/quarter = ~{len(to_process) * avg}")
            print("Claude API calls   : one per parsed passage (more than signals) — exact count known at run time.")
            print("\nNo extraction performed (dry run).\n")
            return {"dry_run": True, "to_process": len(to_process),
                    "skip_v4": len(skip_v4), "skip_no_src": len(skip_no_src)}

        results = {"attempted": 0, "succeeded": 0, "failed": 0,
                   "skipped": len(skip_no_src) + len(skip_v4), "total_signals": 0}
        for q in skip_no_src:
            logger.info("SKIP (no transcript source): %s", _label(q))
        for q in skip_v4:
            logger.info("SKIP (already v4.0): %s", _label(q))

        for i, q in enumerate(to_process):
            lbl, url = _label(q), q["source_url"]
            results["attempted"] += 1
            logger.info("[%d/%d] %s — fetching %s", i + 1, len(to_process), lbl, url)
            txt = fetch_transcript_text(url)
            if txt is None or len(txt) < MIN_TRANSCRIPT_CHARS or "Operator" not in txt:
                reason = ("fetch returned None" if txt is None
                          else f"too short ({len(txt)} chars)" if len(txt) < MIN_TRANSCRIPT_CHARS
                          else "no 'Operator' marker")
                logger.error("FAIL %s — %s — skipping", lbl, reason)
                results["failed"] += 1
            else:
                try:
                    stats = process_transcript(
                        txt, retailer_id=q["retailer_id"], fiscal_year=q["fiscal_year"],
                        fiscal_quarter=q["fiscal_quarter"], source_url=url,
                        source_format=("insider_monkey" if "insidermonkey" in url else "motley_fool"),
                    )
                    src = "insidermonkey" if "insidermonkey" in url else "fool.com"
                    # A valid transcript (passed >5000 chars + Operator) that yields 0
                    # signals is a parser/fetch fault, not a success — a real earnings
                    # call always produces signals. Prior signals are NOT demoted here
                    # (process_transcript only demotes when pending_rows is non-empty),
                    # so the quarter keeps its existing signals; we count it as failed.
                    if stats.signals_extracted == 0:
                        reason = "parsed_0_passages" if stats.passages_processed == 0 else "extracted_0_signals"
                        logger.error(
                            "FAIL %s (%s) — reason=%s — 0 signals from a %d-char transcript "
                            "(passages=%d); prior signals preserved, counted as FAILURE",
                            lbl, src, reason, len(txt), stats.passages_processed,
                        )
                        results["failed"] += 1
                        if i < len(to_process) - 1:
                            time.sleep(BETWEEN_TRANSCRIPT_SLEEP)
                        continue
                    results["succeeded"] += 1
                    results["total_signals"] += stats.signals_extracted
                    logger.info(
                        "[%d/%d] %s (%s) — Status: success | signals: %d | analyst_pressure: %d | "
                        "contradictions: %d || Cumulative: %d/%d | signals_written: %d | failures: %d",
                        i + 1, len(to_process), lbl, src, stats.signals_extracted,
                        stats.analyst_pressure_signals, stats.contradictions_found,
                        results["succeeded"], len(to_process), results["total_signals"], results["failed"],
                    )
                except Exception as exc:  # noqa: BLE001 — never silently swallow
                    logger.exception("FAIL %s — extraction error: %s", lbl, exc)
                    results["failed"] += 1
            if i < len(to_process) - 1:
                time.sleep(BETWEEN_TRANSCRIPT_SLEEP)

        logger.info("BACKFILL COMPLETE — attempted=%d succeeded=%d failed=%d skipped=%d total_signals=%d",
                    results["attempted"], results["succeeded"], results["failed"],
                    results["skipped"], results["total_signals"])
        return results
    finally:
        db.close()


def main() -> int:
    p = argparse.ArgumentParser(description="v4.0 transcript re-extraction backfill (FY2021+)")
    p.add_argument("--retailer-id", type=int, choices=[1, 2])
    p.add_argument("--fiscal-year", type=int)
    p.add_argument("--fiscal-quarter", type=int)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    res = run(args.retailer_id, args.fiscal_year, args.fiscal_quarter, args.dry_run, args.force)
    if args.dry_run:
        return 0
    return 0 if res.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
