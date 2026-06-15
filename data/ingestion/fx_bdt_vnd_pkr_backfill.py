"""One-time backfill: replace interpolated BDT/VND/PKR with Alpha Vantage weekly rates."""

import logging
import os
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

import requests
from sqlalchemy import and_
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from database.database import SessionLocal
from database.models import FxRates

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
AV_BASE = "https://www.alphavantage.co/query"

CURRENCIES_TO_FIX = {
    "BDT": "usd_bdt",
    "VND": "usd_vnd",
    "PKR": "usd_pkr",
}

BOUNDS = {
    "BDT": (75.0, 145.0),
    "VND": (18000.0, 30000.0),
    "PKR": (75.0, 355.0),
}

RATE_LIMIT_SLEEP = 15
REQUEST_TIMEOUT = 30


def fetch_av_weekly(currency_code: str) -> dict[date, Decimal]:
    """Fetch Alpha Vantage FX_WEEKLY for USD/{currency_code}."""
    if not ALPHA_VANTAGE_KEY:
        logger.error("ALPHA_VANTAGE_KEY is not set.")
        return {}

    params = {
        "function": "FX_WEEKLY",
        "from_symbol": "USD",
        "to_symbol": currency_code,
        "apikey": ALPHA_VANTAGE_KEY,
        "outputsize": "full",
    }

    try:
        response = requests.get(AV_BASE, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        logger.error(f"Alpha Vantage request failed for {currency_code}: {exc}")
        return {}
    except ValueError as exc:
        logger.error(f"Alpha Vantage JSON parse failed for {currency_code}: {exc}")
        return {}

    if "Note" in payload or "Information" in payload:
        logger.error(
            f"Alpha Vantage rate limit or info for {currency_code}: "
            f"{payload.get('Note') or payload.get('Information')}"
        )
        return {}

    series = payload.get("Time Series FX (Weekly)")
    if not series:
        logger.error(
            f"Alpha Vantage missing weekly series for {currency_code}: "
            f"keys={list(payload.keys())}"
        )
        return {}

    lo, hi = BOUNDS[currency_code]
    data: dict[date, Decimal] = {}

    for date_str, values in series.items():
        try:
            week_date = date.fromisoformat(date_str)
            rate = float(values["4. close"])
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(f"Skipping malformed {currency_code} row {date_str}: {exc}")
            continue

        if not (lo <= rate <= hi):
            logger.warning(
                f"Skipping {currency_code} {date_str}: rate {rate} outside [{lo}, {hi}]"
            )
            continue

        data[week_date] = Decimal(str(rate))

    logger.info(f"Fetched {len(data)} weekly {currency_code} rates from Alpha Vantage")
    return data


def find_closest_fx_row(
    db: Session,
    target_date: date,
    tolerance_days: int = 3,
) -> Optional[FxRates]:
    """Find latest FX row whose pulled_at date is closest to target_date within tolerance."""
    start = target_date - timedelta(days=tolerance_days)
    end = target_date + timedelta(days=tolerance_days)
    rows = (
        db.query(FxRates)
        .filter(
            and_(
                FxRates.is_latest.is_(True),
                FxRates.pulled_at >= datetime.combine(start, datetime.min.time()),
                FxRates.pulled_at <= datetime.combine(end, datetime.max.time()),
            )
        )
        .all()
    )
    if not rows:
        return None
    return min(rows, key=lambda r: abs((r.pulled_at.date() - target_date).days))


def run_backfill() -> dict:
    """Replace interpolated BDT/VND/PKR with Alpha Vantage weekly rates."""
    summary: dict = {}
    currency_keys = list(CURRENCIES_TO_FIX.keys())

    for currency_code, field_name in CURRENCIES_TO_FIX.items():
        logger.info(f"Fetching Alpha Vantage weekly data for {currency_code}...")
        av_data = fetch_av_weekly(currency_code)

        if not av_data:
            logger.error(f"{currency_code}: Alpha Vantage fetch failed. Skipping.")
            summary[currency_code] = {"fetched": 0, "updated": 0, "skipped": 0}
            if currency_code != currency_keys[-1]:
                time.sleep(RATE_LIMIT_SLEEP)
            continue

        updated = 0
        skipped = 0

        db = SessionLocal()
        try:
            for av_date, real_rate in av_data.items():
                row = find_closest_fx_row(db, av_date)
                if not row:
                    skipped += 1
                    continue

                setattr(row, field_name, real_rate)

                if row.data_source_quality == "FRED_ALL":
                    row.data_source_quality = "FRED+ALPHA_VANTAGE"
                elif row.data_source_quality is None:
                    row.data_source_quality = "ALPHA_VANTAGE_PARTIAL"

                updated += 1

            db.commit()
            logger.info(
                f"{currency_code}: {updated} rows updated, "
                f"{skipped} dates had no matching FX row"
            )
            summary[currency_code] = {
                "fetched": len(av_data),
                "updated": updated,
                "skipped": skipped,
            }
        except Exception as exc:
            db.rollback()
            logger.error(f"{currency_code} backfill failed: {exc}")
            summary[currency_code] = {"error": str(exc)}
        finally:
            db.close()

        if currency_code != currency_keys[-1]:
            logger.info(
                f"Waiting {RATE_LIMIT_SLEEP}s before next currency (rate limit)..."
            )
            time.sleep(RATE_LIMIT_SLEEP)

    return summary


if __name__ == "__main__":
    print("Alpha Vantage BDT/VND/PKR historical backfill")
    print("This replaces World Bank interpolated data with real weekly rates.")
    print("Three API calls. Takes ~45 seconds total (rate limit sleeps).")
    print()
    results = run_backfill()
    print("\nSUMMARY:")
    for currency, stats in results.items():
        print(f"  {currency}: {stats}")
