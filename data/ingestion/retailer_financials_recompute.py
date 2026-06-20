"""
Authoritative recompute of SEC-XBRL-derived fields on retailer_financials.

This corrects the historical defects found by the reconciliation gate:
  D1 — inventory_usd shifted +1 fiscal year (calendar-frame keying bug)
  D2 — total_net_sales_usd mixed net-sales / total-revenues across eras
  D3 — gross_margin_pct computed on the total-revenue denominator

It re-derives each field directly from SEC companyfacts by EXACT period-end date
(the discipline proven correct by data/verification/retail_financials_reconcile.py,
whose extraction helpers this module reuses — single source of truth for "correct").

Discipline:
  - Updates is_latest rows IN PLACE; stamps per-field provenance in data_quality.
  - Writes a field ONLY when SEC has a value for that exact period — NEVER nulls an
    existing value, NEVER imputes. Q4-from-10K (no discrete quarterly fact) is left
    to whatever sourced it (e.g. 8-K).
  - Idempotent and re-runnable. --dry-run reports the diff without writing.
  - Segment fields (walmart_us_*, sams_club_*) and 8-K-sourced comp sales are NOT
    touched — only the core companyfacts-derived figures.
"""

from __future__ import annotations

import argparse
import json
import logging
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from data.verification.retail_financials_reconcile import (
    COGS_CONCEPTS,
    COMPANYFACTS_URL,
    GROSS_PROFIT_CONCEPTS,
    INVENTORY_CONCEPTS,
    NET_INCOME_CONCEPTS,
    OPERATING_INCOME_CONCEPTS,
    REVENUE_CONCEPTS,
    _match,
    _sec_get_json,
    duration_facts_by_end,
    instant_facts_by_end,
)
from database.base import SessionLocal
from database.models.retail import MajorRetailers, RetailerFinancials

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

SCRIPT_VERSION = "retailer-financials-recompute-v1.0"
_TRAILING_YEAR_DAYS = 371  # window for the trailing-4-quarter COGS annualisation


def _q(value: Decimal, places: str) -> Decimal:
    return value.quantize(Decimal(places))


def _annualised_cogs(cogs_by_end: dict, period_end) -> Optional[Decimal]:
    """Sum the 4 most-recent quarterly COGS facts ending at/just before period_end.
    Returns None unless a full 4 quarters are available (never annualise a partial)."""
    window = [(end, val) for end, val in cogs_by_end.items()
              if 0 <= (period_end - end).days < _TRAILING_YEAR_DAYS]
    window.sort(reverse=True)
    if len(window) < 4:
        return None
    return sum(val for _end, val in window[:4])


def _stamp(row: RetailerFinancials, fields: list[str], cik: str) -> None:
    url = COMPANYFACTS_URL.format(cik=cik.zfill(10))
    entry = {"source_type": "xbrl_companyfacts_recompute", "source_url": url,
             "confidence": "high", "script": SCRIPT_VERSION, "note": "fiscal-period keyed correction"}
    try:
        ledger = json.loads(row.data_quality) if row.data_quality and row.data_quality.strip().startswith("{") else {}
    except (json.JSONDecodeError, TypeError):
        ledger = {}
    for f in fields:
        ledger[f] = entry
    row.data_quality = json.dumps(ledger)


def recompute_retailer(db: Session, retailer: MajorRetailers, dry_run: bool) -> dict:
    cik = (retailer.cik or "").zfill(10)
    fj = _sec_get_json(COMPANYFACTS_URL.format(cik=cik))
    if not fj or not fj.get("facts"):
        logger.error("No companyfacts for %s", retailer.ticker)
        return {}
    ug = fj["facts"].get("us-gaap", {})

    revenue = duration_facts_by_end(ug, REVENUE_CONCEPTS)
    cogs = duration_facts_by_end(ug, COGS_CONCEPTS)
    gross_profit = duration_facts_by_end(ug, GROSS_PROFIT_CONCEPTS)
    op_income = duration_facts_by_end(ug, OPERATING_INCOME_CONCEPTS)
    net_income = duration_facts_by_end(ug, NET_INCOME_CONCEPTS)
    inventory = instant_facts_by_end(ug, INVENTORY_CONCEPTS)

    rows = (db.query(RetailerFinancials)
            .filter(RetailerFinancials.retailer_id == retailer.retailer_id,
                    RetailerFinancials.is_latest.is_(True),
                    RetailerFinancials.period_end_date.isnot(None))
            .order_by(RetailerFinancials.fiscal_year, RetailerFinancials.fiscal_quarter)
            .all())

    changed_cells = 0
    changed_rows = 0
    for row in rows:
        ped = row.period_end_date
        ns = _match(ped, revenue)
        cg = _match(ped, cogs)
        gp = _match(ped, gross_profit)
        if gp is None and ns is not None and cg is not None:
            gp = ns - cg
        inv = _match(ped, inventory)
        oi = _match(ped, op_income)
        ni = _match(ped, net_income)

        updates: dict[str, Decimal] = {}
        if ns is not None:
            updates["total_net_sales_usd"] = _q(ns, "0.01")
            if gp is not None and ns != 0:
                updates["gross_margin_pct"] = _q(gp / ns * Decimal("100"), "0.0001")
            if oi is not None:
                updates["operating_income_usd"] = _q(oi, "0.01")
                if ns != 0:
                    updates["operating_margin_pct"] = _q(oi / ns * Decimal("100"), "0.0001")
            if ni is not None:
                updates["net_income_usd"] = _q(ni, "0.01")
                if ns != 0:
                    updates["net_margin_pct"] = _q(ni / ns * Decimal("100"), "0.0001")
        if inv is not None:
            updates["inventory_usd"] = _q(inv, "0.01")
            ann = _annualised_cogs(cogs, ped)
            if ann:
                updates["inventory_days"] = _q(inv / (ann / Decimal("365")), "0.01")

        # apply only genuine changes
        applied = []
        for field, newval in updates.items():
            old = getattr(row, field)
            if old is None or Decimal(str(old)) != newval:
                applied.append(field)
                if not dry_run:
                    setattr(row, field, newval)
        if applied:
            changed_rows += 1
            changed_cells += len(applied)
            if not dry_run:
                _stamp(row, applied, cik)
            logger.info("FY%sQ%s end=%s: %s", row.fiscal_year, row.fiscal_quarter, ped,
                        ", ".join(applied))

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return {"rows": len(rows), "changed_rows": changed_rows, "changed_cells": changed_cells}


def main() -> int:
    parser = argparse.ArgumentParser(description="Recompute SEC-XBRL fields on retailer_financials")
    parser.add_argument("--retailer", metavar="TICKER", required=True, help="ticker, e.g. WMT")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        retailer = db.query(MajorRetailers).filter(MajorRetailers.ticker == args.retailer.upper()).first()
        if not retailer or not retailer.cik:
            print(f"No retailer / cik for {args.retailer}")
            return 1
        stats = recompute_retailer(db, retailer, args.dry_run)
        mode = "DRY RUN" if args.dry_run else "WROTE"
        print(f"{mode} {retailer.name}: {stats.get('changed_cells', 0)} cells across "
              f"{stats.get('changed_rows', 0)}/{stats.get('rows', 0)} rows")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
