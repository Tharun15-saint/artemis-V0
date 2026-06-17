import argparse
import logging
import os
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

import requests

from data.ingestion._env import load_project_env
from data.ingestion.wasde_common import current_marketing_year
from database.base import SessionLocal
from database.models.weather import CottonSupplyDemand

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

NASS_BASE = "https://quickstats.nass.usda.gov/api/api_GET/"
NASS_API_KEY = os.getenv("NASS_API_KEY", "")
COTTON_SEASON_START_MONTH = 4
COTTON_SEASON_END_MONTH = 11
BACKFILL_START_YEAR = 2011
CURRENT_MARKETING_YEAR = current_marketing_year()
REQUEST_TIMEOUT = 30


def is_cotton_season() -> bool:
    return COTTON_SEASON_START_MONTH <= date.today().month <= COTTON_SEASON_END_MONTH


def fetch_nass(statisticcat: str, unit: str, year: int) -> list[dict[str, Any]]:
    if not NASS_API_KEY:
        logger.error("NASS_API_KEY not set — register at quickstats.nass.usda.gov/api/")
        return []

    params = {
        "key": NASS_API_KEY,
        "source_desc": "SURVEY",
        "sector_desc": "CROPS",
        "group_desc": "FIELD CROPS",
        "commodity_desc": "COTTON",
        "statisticcat_desc": statisticcat,
        "unit_desc": unit,
        "agg_level_desc": "NATIONAL",
        "state_fips_code": "99",
        "year": year,
        "format": "JSON",
    }

    try:
        response = requests.get(NASS_BASE, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        records = payload.get("data", [])
    except requests.RequestException as exc:
        logger.error(f"NASS fetch failed ({unit}, {year}): {exc}")
        return []

    observations: list[dict[str, Any]] = []
    for item in records:
        raw = item.get("Value")
        if raw in (None, "(D)", "(Z)", "(NA)", ""):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        week_str = item.get("week_ending") or item.get("load_time")
        if not week_str:
            continue
        try:
            week_date = datetime.strptime(week_str[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        observations.append({"week_ending": week_date, "value": value})

    observations.sort(key=lambda x: x["week_ending"])
    return observations


def get_latest_week_value(
    observations: list[dict[str, Any]],
) -> Optional[tuple[date, Decimal]]:
    if not observations:
        return None
    latest = observations[-1]
    return latest["week_ending"], Decimal(str(latest["value"])).quantize(Decimal("0.0001"))


def get_season_final_value(
    observations: list[dict[str, Any]],
) -> Optional[Decimal]:
    if not observations:
        return None
    in_season = [
        o
        for o in observations
        if COTTON_SEASON_START_MONTH <= o["week_ending"].month <= COTTON_SEASON_END_MONTH
    ]
    target = in_season if in_season else observations
    return Decimal(str(target[-1]["value"])).quantize(Decimal("0.0001"))


def _update_crop_progress_row(
    db,
    marketing_year: int,
    pct_planted: Optional[Decimal],
    good_excellent: Optional[Decimal],
) -> bool:
    report_month = date(marketing_year, 8, 1)
    row = (
        db.query(CottonSupplyDemand)
        .filter(CottonSupplyDemand.report_month == report_month)
        .first()
    )
    if not row:
        # Create a stub row so crop progress data is not lost even if WASDE hasn't run
        from datetime import datetime, timezone
        row = CottonSupplyDemand(
            marketing_year=marketing_year,
            report_month=report_month,
            forecast_provider="USDA_NASS",
            source="crop_progress_ingestion",
            pulled_at=datetime.now(timezone.utc),
            is_latest=True,
        )
        db.add(row)
        logger.info(
            f"Created stub CottonSupplyDemand row for MY {marketing_year} "
            f"(WASDE fields will be populated when wasde_ingestion.py runs)"
        )

    if pct_planted is not None:
        row.us_pct_planted = pct_planted
    if good_excellent is not None:
        row.us_crop_condition_good_excellent_pct = good_excellent
    db.commit()
    return True


def run_once() -> bool:
    if not is_cotton_season():
        logger.info("Outside cotton season (Apr–Nov) — skipping crop progress update.")
        return True

    year = date.today().year
    planted_obs = fetch_nass("PROGRESS", "PCT PLANTED", year)
    good_obs = fetch_nass("CONDITION", "PCT GOOD", year)
    excellent_obs = fetch_nass("CONDITION", "PCT EXCELLENT", year)

    planted = get_latest_week_value(planted_obs)
    good = get_latest_week_value(good_obs)
    excellent = get_latest_week_value(excellent_obs)

    pct_planted = planted[1] if planted else None
    good_excellent: Optional[Decimal] = None
    if good and excellent:
        good_excellent = (good[1] + excellent[1]).quantize(Decimal("0.0001"))
    elif good:
        good_excellent = good[1]

    db = SessionLocal()
    try:
        updated = _update_crop_progress_row(
            db, CURRENT_MARKETING_YEAR, pct_planted, good_excellent
        )
        if updated and pct_planted is not None:
            ge = float(good_excellent) if good_excellent is not None else 0.0
            if good_excellent is not None and ge < 40:
                signal = "STRESS"
            elif good_excellent is not None and ge > 65:
                signal = "HEALTHY"
            else:
                signal = "NORMAL"
            logger.info(
                f"Crop progress: planted={pct_planted}% | "
                f"good+excellent={good_excellent}% | {signal}"
            )
        return updated
    except Exception as exc:
        logger.critical(f"Crop progress ingestion failed: {exc}", exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


def run_historical_backfill() -> None:
    db = SessionLocal()
    updated = 0
    try:
        for calendar_year in range(BACKFILL_START_YEAR, date.today().year + 1):
            marketing_year = calendar_year - 1
            planted = get_season_final_value(
                fetch_nass("PROGRESS", "PCT PLANTED", calendar_year)
            )
            good = get_season_final_value(
                fetch_nass("CONDITION", "PCT GOOD", calendar_year)
            )
            excellent = get_season_final_value(
                fetch_nass("CONDITION", "PCT EXCELLENT", calendar_year)
            )
            ge: Optional[Decimal] = None
            if good is not None and excellent is not None:
                ge = (good + excellent).quantize(Decimal("0.0001"))
            elif good is not None:
                ge = good

            if _update_crop_progress_row(db, marketing_year, planted, ge):
                updated += 1
            time.sleep(1)

        logger.info(f"Crop progress backfill complete: {updated} rows updated")
    finally:
        db.close()


def run_scheduled() -> None:
    logger.info("Crop progress scheduler started — weekly cycle.")
    while True:
        if is_cotton_season():
            run_once()
            time.sleep(7 * 86400)
        else:
            logger.info("Outside cotton season — checking again in 7 days.")
            time.sleep(7 * 86400)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="USDA NASS cotton crop progress ingestion")
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--schedule", action="store_true")
    args = parser.parse_args()

    if args.backfill:
        run_historical_backfill()
    elif args.schedule:
        run_scheduled()
    else:
        raise SystemExit(0 if run_once() else 1)
