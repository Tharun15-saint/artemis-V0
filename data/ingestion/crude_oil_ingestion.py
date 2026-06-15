import argparse
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import requests
from sqlalchemy import desc
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from database.base import SessionLocal, is_duplicate_row, mark_latest
from database.ingestion_context import IngestionContext
from database.models import CrudeOil
from database.validation.ingestion_validators import (
    validate_and_log,
    validate_crude_price,
)

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

SCRIPT_VERSION = "2.0.0"
SOURCE_NAME = "crude_oil_fred"
SOURCE_SYSTEM = "fred_api"
FRED_DATA_URL = "https://api.stlouisfed.org/fred/series/observations"

FRED_API_KEY = os.getenv("FRED_API_KEY", "")
FRED_BASE = FRED_DATA_URL
BRENT_SERIES = "DCOILBRENTEU"
WTI_SERIES = "DCOILWTICO"
BACKFILL_START = "2011-01-01"
SCHEDULE_HOURS = 24
REQUEST_TIMEOUT = 30
STALE_DAYS = 2


def fetch_fred_oil_series(
    series_id: str,
    start_date: str,
    end_date: str,
) -> list[tuple[date, Decimal]]:
    if not FRED_API_KEY:
        logger.error("FRED_API_KEY not set.")
        return []

    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "observation_start": start_date,
        "observation_end": end_date,
        "frequency": "w",
        "aggregation_method": "eop",
        "output_type": 1,
    }

    try:
        response = requests.get(FRED_BASE, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        observations = response.json().get("observations", [])
    except requests.RequestException as exc:
        logger.error(f"FRED oil fetch failed for {series_id}: {exc}")
        return []

    rows: list[tuple[date, Decimal]] = []
    for obs in observations:
        raw = obs.get("value")
        if raw in (None, ".", ""):
            continue
        try:
            rows.append((date.fromisoformat(obs["date"]), Decimal(str(raw))))
        except (ValueError, TypeError):
            continue
    return sorted(rows, key=lambda x: x[0])


def _days_since_refresh(as_of: date) -> int:
    return (date.today() - as_of).days


def get_latest_crude_row(db: Session) -> Optional[CrudeOil]:
    return (
        db.query(CrudeOil)
        .filter(CrudeOil.is_latest.is_(True))
        .filter(CrudeOil.as_of_date.isnot(None))
        .order_by(desc(CrudeOil.as_of_date))
        .first()
    )


def _merge_weekly_series(
    brent_rows: list[tuple[date, Decimal]],
    wti_rows: list[tuple[date, Decimal]],
) -> dict[date, dict[str, Optional[Decimal]]]:
    brent_map = dict(brent_rows)
    wti_map = dict(wti_rows)
    all_dates = sorted(set(brent_map) | set(wti_map))
    return {
        d: {"brent": brent_map.get(d), "wti": wti_map.get(d)} for d in all_dates
    }


def _previous_spot(db: Session, as_of: date) -> tuple[Optional[Decimal], Optional[Decimal]]:
    prior = (
        db.query(CrudeOil)
        .filter(CrudeOil.is_latest.is_(True))
        .filter(CrudeOil.as_of_date < as_of)
        .order_by(desc(CrudeOil.as_of_date))
        .first()
    )
    if prior is None:
        return None, None
    return prior.brent_spot, prior.wti_spot


def append_crude_row(
    db: Session,
    ctx: IngestionContext,
    as_of: date,
    brent: Optional[Decimal],
    wti: Optional[Decimal],
    source: str,
) -> bool:
    if brent is None or wti is None:
        ctx.increment_rejected("crude_oil: missing Brent or WTI for row")
        return False

    prev_brent, prev_wti = _previous_spot(db, as_of)
    brent_valid = validate_and_log(
        brent,
        lambda v: validate_crude_price(v, prev_brent),
        ctx,
    )
    wti_valid = validate_and_log(
        wti,
        lambda v: validate_crude_price(v, prev_wti),
        ctx,
    )
    if brent_valid is None or wti_valid is None:
        return False

    value_kwargs = {
        "brent_spot": brent_valid,
        "wti_spot": wti_valid,
        "as_of_date": as_of,
        "source": SOURCE_SYSTEM,
        "data_source_url": FRED_DATA_URL,
    }
    if is_duplicate_row(db, CrudeOil, {"as_of_date": as_of}, value_kwargs):
        ctx.stale()
        logger.info(f"Crude oil unchanged for as_of_date={as_of} — skipping insert")
        return True

    pulled_at = datetime.now(timezone.utc)
    mark_latest(db, CrudeOil, {"as_of_date": as_of})
    db.add(
        CrudeOil(
            brent_spot=brent_valid,
            wti_spot=wti_valid,
            as_of_date=as_of,
            days_since_refresh=_days_since_refresh(as_of),
            source=SOURCE_SYSTEM,
            data_source_url=FRED_DATA_URL,
            refresh="weekly",
            pulled_at=pulled_at,
            is_latest=True,
        )
    )
    ctx.increment_inserted()
    return True


def run_historical_backfill() -> int:
    end_date = date.today().isoformat()
    brent_rows = fetch_fred_oil_series(BRENT_SERIES, BACKFILL_START, end_date)
    wti_rows = fetch_fred_oil_series(WTI_SERIES, BACKFILL_START, end_date)
    merged = _merge_weekly_series(brent_rows, wti_rows)

    db = SessionLocal()
    written = 0
    try:
        with IngestionContext(
            source_name=f"{SOURCE_NAME}_backfill",
            script_version=SCRIPT_VERSION,
            data_source_url=FRED_DATA_URL,
            db=db,
        ) as ctx:
            for week_date, prices in merged.items():
                if append_crude_row(
                    db,
                    ctx,
                    week_date,
                    prices["brent"],
                    prices["wti"],
                    "FRED_DCOILBRENTEU+DCOILWTICO_historical",
                ):
                    written += 1
                    if written % 50 == 0:
                        logger.info(f"Written {written} crude oil rows...")
        logger.info(f"Crude oil backfill complete: {written} rows written")
        return written
    except Exception as exc:
        logger.critical(f"Crude oil backfill failed: {exc}", exc_info=True)
        db.rollback()
        return written
    finally:
        db.close()


def run_once() -> bool:
    db = SessionLocal()
    try:
        latest_db = get_latest_crude_row(db)
        db_age_days = (
            _days_since_refresh(latest_db.as_of_date)
            if latest_db and latest_db.as_of_date
            else STALE_DAYS + 1
        )
        is_stale = db_age_days > STALE_DAYS

        lookback_days = 60 if is_stale else 14
        start = (date.today() - timedelta(days=lookback_days)).isoformat()
        end = date.today().isoformat()
        brent_rows = fetch_fred_oil_series(BRENT_SERIES, start, end)
        wti_rows = fetch_fred_oil_series(WTI_SERIES, start, end)
        merged = _merge_weekly_series(brent_rows, wti_rows)

        if not merged:
            logger.warning("No FRED crude oil observations returned.")
            return False

        latest_date = max(merged.keys())
        prices = merged[latest_date]

        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=FRED_DATA_URL,
            db=db,
        ) as ctx:
            ctx.set_as_of_date(latest_date)
            has_newer_fred_date = (
                latest_db is None
                or latest_db.as_of_date is None
                or latest_date > latest_db.as_of_date
            )

            if not is_stale and not has_newer_fred_date:
                ctx.increment_stale()
                logger.info(
                    f"Crude oil data is current | as_of_date={latest_db.as_of_date} | "
                    f"days_since_refresh={latest_db.days_since_refresh}"
                )
                return True

            if is_stale:
                logger.info(
                    f"Crude oil data stale ({db_age_days} days old) — refreshing from FRED."
                )

            if not append_crude_row(
                db,
                ctx,
                latest_date,
                prices["brent"],
                prices["wti"],
                "FRED_DCOILBRENTEU+DCOILWTICO",
            ):
                return False

            days = _days_since_refresh(latest_date)
            logger.info(
                f"Crude oil appended: 1 row | as_of_date={latest_date} | "
                f"Brent={prices['brent']} WTI={prices['wti']} | "
                f"days_since_refresh={days}"
            )
            return True
    except Exception as exc:
        logger.critical(f"Crude oil ingestion failed: {exc}", exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


def run_scheduled() -> None:
    logger.info(f"Crude oil scheduler started — every {SCHEDULE_HOURS} hours.")
    while True:
        run_once()
        time.sleep(SCHEDULE_HOURS * 3600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FRED Brent/WTI crude oil ingestion")
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--schedule", action="store_true")
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Fetch latest Brent/WTI and write one row if new data exists.",
    )
    args = parser.parse_args()

    if args.backfill:
        raise SystemExit(0 if run_historical_backfill() > 0 else 1)
    if args.schedule:
        run_scheduled()
    else:
        raise SystemExit(0 if run_once() else 1)
