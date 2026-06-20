"""
Migrate REPORTED (8-K / MD&A) metrics from retailer_financials into retailer_metric.

These don't exist in SEC XBRL facts — they're management-reported figures (comparable
sales, apparel-segment revenue, e-commerce penetration, EPS guidance) plus Target's
reported gross-margin rate. They were already extracted + vetted in retailer_financials;
this lifts them into the canonical tall store with provenance, so the metric store is the
single home for every signal.

Discipline:
  - Carries each value with origin column + the original filing URL in the provenance
    ledger; source='retailer_financials_reported', confidence below direct-XBRL (these are
    reported, not from structured facts).
  - gross_margin_pct is migrated ONLY for retailers whose GM is reported (Target); Walmart's
    GM is XBRL-derived in the derive pass — never both.
  - Writes only non-null values; idempotent upsert of the is_latest row per (retailer,
    metric_key, fiscal period). Never touches direct/derived rows.
"""

from __future__ import annotations

import argparse
import json
import logging
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from data.verification.retail_financials_reconcile import RETAILER_PROFILES, DEFAULT_PROFILE
from database.base import SessionLocal
from database.models.retail import MajorRetailers, RetailerFinancials
from database.models.retail_metrics import RetailerMetric

load_project_env()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

SCRIPT_VERSION = "retailer-metric-migrate-reported-v1.0"

# (retailer_financials column, metric_key, unit, confidence)
REPORTED = [
    ("comparable_sales_growth_pct", "comparable_sales_growth_pct", "pct", "0.85"),
    ("apparel_revenue_usd", "apparel_revenue_usd", "usd", "0.85"),
    ("apparel_revenue_pct_total", "apparel_revenue_pct_total", "pct", "0.85"),
    ("apparel_yoy_growth_pct", "apparel_yoy_growth_pct", "pct", "0.85"),
    ("ecommerce_penetration_pct", "ecommerce_penetration_pct", "pct", "0.85"),
    ("guidance_eps_low", "guidance_eps_low_usd", "usd_per_share", "0.70"),
    ("guidance_eps_high", "guidance_eps_high_usd", "usd_per_share", "0.70"),
]
# gross_margin only where it is a REPORTED rate (verify_gross_margin == False, e.g. Target).
GM_REPORTED = ("gross_margin_pct", "gross_margin_pct", "pct", "0.90")


def _q(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.0001"))


def _upsert(db, retailer_id, metric_key, unit, row, value, column, confidence, dry_run) -> bool:
    url = row.source_8k_url or row.source_10q_url or row.data_source_url or "retailer_financials"
    ledger = json.dumps({"source_type": "retailer_financials_reported", "origin_column": column,
                         "source_url": url, "script": SCRIPT_VERSION})
    val = _q(value)
    existing = (db.query(RetailerMetric)
                .filter_by(retailer_id=retailer_id, metric_key=metric_key,
                           fiscal_year=row.fiscal_year, fiscal_quarter=row.fiscal_quarter, is_latest=True).first())
    if existing:
        if existing.value_numeric is not None and Decimal(str(existing.value_numeric)) == val and existing.source == "retailer_financials_reported":
            return False
        if not dry_run:
            existing.value_numeric, existing.unit = val, unit
            existing.source, existing.source_concept, existing.source_url = "retailer_financials_reported", column, url
            existing.confidence, existing.data_quality = Decimal(confidence), ledger
        return True
    if not dry_run:
        ped = row.period_end_date
        db.add(RetailerMetric(
            retailer_id=retailer_id, metric_key=metric_key, fiscal_year=row.fiscal_year,
            fiscal_quarter=row.fiscal_quarter, period_end_date=ped, filing_date=row.filing_date,
            calendar_year=ped.year if ped else None,
            calendar_quarter=((ped.month - 1) // 3 + 1) if ped else None,
            value_numeric=val, unit=unit, source="retailer_financials_reported", source_concept=column,
            source_url=url, confidence=Decimal(confidence), data_quality=ledger,
            certified=False, is_latest=True))
    return True


def migrate_retailer(db: Session, retailer: MajorRetailers, dry_run: bool) -> dict:
    ticker = (retailer.ticker or "").upper()
    profile = RETAILER_PROFILES.get(ticker, DEFAULT_PROFILE)
    cols = list(REPORTED)
    if not profile.get("verify_gross_margin", True):   # reported-GM retailers (Target)
        cols.append(GM_REPORTED)

    rows = (db.query(RetailerFinancials)
            .filter(RetailerFinancials.retailer_id == retailer.retailer_id,
                    RetailerFinancials.is_latest.is_(True)).all())
    written = unchanged = 0
    for row in rows:
        for column, metric_key, unit, conf in cols:
            value = getattr(row, column, None)
            if value is None:
                continue
            if _upsert(db, retailer.retailer_id, metric_key, unit, row, value, column, conf, dry_run):
                written += 1
            else:
                unchanged += 1
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return {"written": written, "unchanged": unchanged}


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate reported metrics into retailer_metric")
    parser.add_argument("--retailer", metavar="TICKER")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        q = db.query(MajorRetailers).filter(MajorRetailers.cik.isnot(None))
        if args.retailer:
            q = q.filter(MajorRetailers.ticker == args.retailer.upper())
        else:
            have = {rid for (rid,) in db.query(RetailerFinancials.retailer_id)
                    .filter(RetailerFinancials.is_latest.is_(True)).distinct()}
            q = q.filter(MajorRetailers.retailer_id.in_(have))
        for retailer in q.all():
            s = migrate_retailer(db, retailer, args.dry_run)
            mode = "DRY-RUN" if args.dry_run else "WROTE"
            print(f"{mode} {retailer.name} [{retailer.ticker}]: {s['written']} reported rows written/changed, {s['unchanged']} unchanged")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
