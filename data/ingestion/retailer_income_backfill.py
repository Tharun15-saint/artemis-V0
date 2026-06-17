"""
Backfill operating_income_usd / net_income_usd / margins on retailer_financials
from SEC EDGAR XBRL (OperatingIncomeLoss, NetIncomeLoss).

Profitability is a vital demand-side datapoint — earnings power governs a
retailer's buying confidence and how much FOB cost pressure it transmits to
apparel suppliers. These standard XBRL concepts were never captured.

Discipline:
  - Matches each quarterly income fact to its retailer_financials row by PERIOD
    END DATE (unambiguous — sidesteps the fiscal-vs-calendar-quarter offset).
  - Uses only ~3-month-duration facts (true quarterly figures, not YTD cumulative).
  - Updates is_latest rows in place; records provenance in source/data_quality.
  - Never imputes: a row with no matching XBRL fact is left null.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import requests
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.models.retail import MajorRetailers, RetailerFinancials

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

SCRIPT_VERSION = "retailer-income-backfill-v1.0"
SEC_USER_AGENT = "ArtemisV0/1.0 (retail intelligence ingestion; contact@artemis.local)"
_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
REQUEST_TIMEOUT = 30

_OPERATING_INCOME_CONCEPTS = ["OperatingIncomeLoss"]
_NET_INCOME_CONCEPTS = [
    "NetIncomeLoss",
    "ProfitLoss",
    "NetIncomeLossAvailableToCommonStockholdersBasic",
]

# A quarterly duration is ~13 weeks; accept 80–100 days to admit 4-4-5 calendars.
_Q_MIN_DAYS = 80
_Q_MAX_DAYS = 100
# Match an XBRL period end to a financial row's period_end_date within this slack.
_END_DATE_SLACK_DAYS = 8


def _sec_get_json(url: str) -> Optional[dict]:
    headers = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    try:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("SEC request failed for %s: %s", url, exc)
        return None


def _parse_date(s: str) -> Optional[date]:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def quarterly_facts_by_end_date(us_gaap: dict, concepts: list[str]) -> dict[date, Decimal]:
    """Collect quarterly (3-month) facts keyed by period end date.

    When multiple filings report the same end date (original 10-Q + later 10-K
    restatement), the latest-filed value wins.
    """
    out: dict[date, Decimal] = {}
    # Track which concept supplied each end date so a higher-priority concept can
    # override, and a later filing of the same concept can supersede a restatement.
    chosen: dict[date, tuple[int, str]] = {}  # end -> (concept_rank, filed)
    for rank, concept in enumerate(concepts):
        node = us_gaap.get(concept)
        if not node:
            continue
        for fact in node.get("units", {}).get("USD", []):
            start = _parse_date(fact.get("start"))
            end = _parse_date(fact.get("end"))
            val = fact.get("val")
            filed = fact.get("filed", "")
            if start is None or end is None or val is None:
                continue
            dur = (end - start).days
            if not (_Q_MIN_DAYS <= dur <= _Q_MAX_DAYS):
                continue  # skip YTD / annual cumulative figures
            prior = chosen.get(end)
            # Prefer the higher-priority concept (lower rank); within the same
            # concept, prefer the later-filed (restated) value. Gaps get filled
            # by any lower-priority concept that has the period.
            if prior is None or rank < prior[0] or (rank == prior[0] and filed > prior[1]):
                out[end] = Decimal(str(val))
                chosen[end] = (rank, filed)
    return out


def _match_by_end_date(target: date, facts: dict[date, Decimal]) -> Optional[Decimal]:
    if target in facts:
        return facts[target]
    best, best_gap = None, _END_DATE_SLACK_DAYS + 1
    for end, val in facts.items():
        gap = abs((end - target).days)
        if gap < best_gap:
            best, best_gap = val, gap
    return best if best_gap <= _END_DATE_SLACK_DAYS else None


def _q(value: Decimal, places: str = "0.0001") -> Decimal:
    return value.quantize(Decimal(places))


def backfill_retailer(db: Session, retailer_id: int, cik: str, dry_run: bool = False) -> dict[str, int]:
    facts_json = _sec_get_json(_COMPANYFACTS_URL.format(cik=cik.zfill(10)))
    if not facts_json or not facts_json.get("facts"):
        logger.error("No companyfacts for CIK %s", cik)
        return {"matched_oi": 0, "matched_ni": 0, "rows": 0}

    us_gaap = facts_json["facts"].get("us-gaap", {})
    oi_facts = quarterly_facts_by_end_date(us_gaap, _OPERATING_INCOME_CONCEPTS)
    ni_facts = quarterly_facts_by_end_date(us_gaap, _NET_INCOME_CONCEPTS)
    logger.info("CIK %s: %d operating-income, %d net-income quarterly facts", cik, len(oi_facts), len(ni_facts))

    rows = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
            RetailerFinancials.period_end_date.isnot(None),
        )
        .all()
    )

    matched_oi = matched_ni = 0
    for row in rows:
        ped = row.period_end_date
        oi = _match_by_end_date(ped, oi_facts)
        ni = _match_by_end_date(ped, ni_facts)
        sales = row.total_net_sales_usd

        if oi is not None:
            row.operating_income_usd = _q(oi, "0.01")
            if sales:
                row.operating_margin_pct = _q(oi / Decimal(sales) * Decimal("100"))
            matched_oi += 1
        if ni is not None:
            row.net_income_usd = _q(ni, "0.01")
            if sales:
                row.net_margin_pct = _q(ni / Decimal(sales) * Decimal("100"))
            matched_ni += 1

        if oi is not None or ni is not None:
            row.source = (row.source or "unknown")
            _stamp_provenance(row, cik)

    if dry_run:
        db.rollback()
        logger.info("DRY RUN — rolled back")
    else:
        db.commit()
    return {"matched_oi": matched_oi, "matched_ni": matched_ni, "rows": len(rows)}


def _stamp_provenance(row: RetailerFinancials, cik: str) -> None:
    """Record income provenance in the per-field data_quality JSON ledger."""
    url = _COMPANYFACTS_URL.format(cik=cik.zfill(10))
    entry = {"source_type": "xbrl_companyfacts", "source_url": url, "confidence": "high", "script": SCRIPT_VERSION}
    try:
        ledger = json.loads(row.data_quality) if row.data_quality and row.data_quality.strip().startswith("{") else {}
    except (json.JSONDecodeError, TypeError):
        ledger = {}
    if row.operating_income_usd is not None:
        ledger["operating_income_usd"] = entry
    if row.net_income_usd is not None:
        ledger["net_income_usd"] = entry
    row.data_quality = json.dumps(ledger)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill retailer income from SEC XBRL")
    parser.add_argument("--retailer-id", type=int, default=None, help="Single retailer; default = all with a cik")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        q = db.query(MajorRetailers).filter(MajorRetailers.cik.isnot(None))
        if args.retailer_id:
            q = q.filter(MajorRetailers.retailer_id == args.retailer_id)
        for retailer in q.all():
            cik = (retailer.cik or "").lstrip("0") or retailer.cik
            if not cik:
                continue
            stats = backfill_retailer(db, retailer.retailer_id, retailer.cik, dry_run=args.dry_run)
            logger.info(
                "%s (id=%d): %d/%d rows got operating income, %d got net income",
                retailer.name, retailer.retailer_id, stats["matched_oi"], stats["rows"], stats["matched_ni"],
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
