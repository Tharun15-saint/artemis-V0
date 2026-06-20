"""
Certification gate: set retailer_metric.certified = true ONLY on rows that pass every
check. Certified rows are the model-grade GOLD layer — what models train on. Everything
else stays certified=false (present and inspectable in the refined layer, but never fed
to training).

A row is certified iff ALL hold:
  1. value_numeric is not null
  2. provenance ledger (data_quality) present AND source_concept present
  3. source in the known-good set (sec_companyfacts | derived | retailer_financials_reported)
  4. confidence >= CERT_MIN_CONFIDENCE
  5. passes hard sanity bounds for its unit (catch impossible values)
  6. non-negative if the metric is inherently non-negative (sales, inventory, COGS, …)

Re-runnable: recomputes the flag every time (no drift). Reports what was decertified and why
so a real defect is never silently hidden.
"""

from __future__ import annotations

import argparse
import logging
from decimal import Decimal

from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.models.retail_metrics import MetricDefinition, RetailerMetric

load_project_env()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

CERT_MIN_CONFIDENCE = Decimal("0.70")
GOOD_SOURCES = {"sec_companyfacts", "derived", "retailer_financials_reported"}

# Hard sanity bounds per unit — generous, only to catch IMPOSSIBLE values (not normal extremes).
BOUNDS = {
    "usd": (Decimal("-1e13"), Decimal("1e13")),
    "usd_per_share": (Decimal("-1000"), Decimal("1000")),
    "pct": (Decimal("-1000"), Decimal("5000")),
    "ratio": (Decimal("-1000"), Decimal("100000")),
    "days": (Decimal("-2000"), Decimal("5000")),
    "bps": (Decimal("-100000"), Decimal("100000")),
    "count": (Decimal("0"), Decimal("1e8")),
}
# Metrics that can never legitimately be negative.
NONNEG = {
    "merchandise_sales_usd", "total_revenue_usd", "cogs_usd", "inventory_usd",
    "current_assets_usd", "current_liabilities_usd", "accounts_payable_usd",
    "cash_and_equivalents_usd", "total_debt_usd", "accounts_receivable_usd",
    "capex_usd", "depreciation_amortization_usd", "inventory_turnover",
    "inventory_days", "days_payable_outstanding", "current_ratio",
    "apparel_revenue_usd", "store_count_total",
}


def _check(row: RetailerMetric, unit: str) -> str:
    if row.value_numeric is None:
        return "null_value"
    if not row.data_quality:
        return "no_provenance"
    if not row.source_concept:
        return "no_source_concept"
    if row.source not in GOOD_SOURCES:
        return f"bad_source:{row.source}"
    if row.confidence is None or Decimal(str(row.confidence)) < CERT_MIN_CONFIDENCE:
        return "low_confidence"
    val = Decimal(str(row.value_numeric))
    lo, hi = BOUNDS.get(unit, (Decimal("-1e15"), Decimal("1e15")))
    if not (lo <= val <= hi):
        return f"out_of_bounds[{unit}]"
    if row.metric_key in NONNEG and val < 0:
        return "negative_nonneg"
    return ""  # certified


def certify(db, dry_run: bool) -> dict:
    units = {d.metric_key: d.unit for d in db.query(MetricDefinition).all()}
    rows = db.query(RetailerMetric).filter(RetailerMetric.is_latest.is_(True)).all()
    certified = 0
    reasons: dict[str, int] = {}
    for row in rows:
        reason = _check(row, units.get(row.metric_key, "usd"))
        ok = (reason == "")
        if ok:
            certified += 1
        else:
            reasons[reason] = reasons.get(reason, 0) + 1
            logger.info("DECERTIFY r%s %s FY%sQ%s: %s (val=%s)", row.retailer_id, row.metric_key,
                        row.fiscal_year, row.fiscal_quarter, reason, row.value_numeric)
        if not dry_run:
            row.certified = ok
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return {"total": len(rows), "certified": certified, "decertified": len(rows) - certified, "reasons": reasons}


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify retailer_metric rows (gold layer)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        s = certify(db, args.dry_run)
        mode = "DRY-RUN" if args.dry_run else "WROTE"
        print(f"{mode}: {s['certified']}/{s['total']} certified, {s['decertified']} decertified")
        if s["reasons"]:
            print("decertification reasons:", s["reasons"])
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
