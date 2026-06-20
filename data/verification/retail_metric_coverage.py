"""
Coverage gate: per retailer, compare the metrics EXPECTED for its archetype (the catalog
is the checklist) against what's actually captured in retailer_metric. Surfaces any missing
metric — so "we didn't miss anything important" is machine-checked, not hoped.

A metric is expected for a retailer if its applies_to_archetypes is 'all' or includes the
retailer's retailer_type. Reports, per retailer: covered / gaps, and flags VISION-CRITICAL
gaps loudly. Exit 1 if any vision-critical expected metric has zero coverage.
"""

from __future__ import annotations

import argparse
import json
import logging

from sqlalchemy import func

from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.models.retail import MajorRetailers
from database.models.retail_metrics import MetricDefinition, RetailerMetric

load_project_env()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def _applies(defn: MetricDefinition, archetype: str) -> bool:
    raw = defn.applies_to_archetypes or "all"
    if raw == "all":
        return True
    try:
        return archetype in json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False


def main() -> int:
    argparse.ArgumentParser(description="Coverage gate for retailer_metric").parse_args()
    db = SessionLocal()
    try:
        catalog = db.query(MetricDefinition).all()
        retailer_ids = {rid for (rid,) in db.query(RetailerMetric.retailer_id)
                        .filter(RetailerMetric.is_latest.is_(True)).distinct()}
        retailers = db.query(MajorRetailers).filter(MajorRetailers.retailer_id.in_(retailer_ids)).all()

        critical_gap = False
        for r in retailers:
            archetype = r.retailer_type or "unknown"
            rows = (db.query(RetailerMetric.metric_key,
                             func.count().label("n"),
                             func.count().filter(RetailerMetric.certified).label("c"))
                    .filter(RetailerMetric.retailer_id == r.retailer_id, RetailerMetric.is_latest.is_(True))
                    .group_by(RetailerMetric.metric_key).all())
            captured = {mk: (n, c) for mk, n, c in rows}
            expected = [d for d in catalog if _applies(d, archetype)]
            gaps = sorted(d.metric_key for d in expected if d.metric_key not in captured)
            vgaps = sorted(d.metric_key for d in expected if d.metric_key not in captured and d.vision_critical)
            certified_total = sum(c for _n, c in captured.values())
            print(f"\n=== {r.name} [{r.ticker}] archetype={archetype} ===")
            print(f"  expected {len(expected)} | covered {len(expected) - len(gaps)} | gaps {len(gaps)} "
                  f"| vision-critical gaps {len(vgaps)} | certified rows {certified_total}")
            if gaps:
                print("  GAPS: " + ", ".join(gaps))
            if vgaps:
                critical_gap = True
                print("  ⚠ VISION-CRITICAL GAPS: " + ", ".join(vgaps))
        return 1 if critical_gap else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
