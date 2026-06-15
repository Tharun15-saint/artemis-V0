#!/usr/bin/env python3
"""
Daily job: refresh yarn price_per_kg_usd using grn_date-matched historical FX.

Updates rows where price_per_kg_usd is NULL, or where the stored USD value
differs by more than 2% from a fresh grn_date-matched calculation, or where
the FX rate date used has changed.

Run:
  python scripts/update_yarn_usd_prices.py
"""

from __future__ import annotations

import logging
from decimal import Decimal

from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.yarn_usd_prices import get_usd_inr_rate_for_grn_date, update_yarn_usd_prices

load_project_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DRIFT_THRESHOLD_PCT = Decimal("2")


def main() -> int:
    db = SessionLocal()
    try:
        sample_lookup = get_usd_inr_rate_for_grn_date(db, None)
        if sample_lookup is None:
            logger.error("No USD/INR FX rate available — aborting")
            return 1

        updated = update_yarn_usd_prices(
            db,
            only_null=False,
            drift_threshold_pct=DRIFT_THRESHOLD_PCT,
        )
        print("=== YARN USD PRICE UPDATE COMPLETE ===")
        print("  FX lookup:            grn_date-matched historical rate")
        print(f"  Latest FX available:  USD/INR {sample_lookup.usd_inr} "
              f"dated {sample_lookup.rate_date}")
        print(f"  Rows updated:         {updated}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
