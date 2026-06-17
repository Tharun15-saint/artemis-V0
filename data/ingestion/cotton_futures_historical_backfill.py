"""
Historical cotton futures curve backfill — S/U-calibrated synthetic curve on all origins.

PERMANENTLY DISABLED. This script generates fabricated ICE futures curves using
WASDE S/U calibration. That is synthetic data. It corrupts model training.

Real historical ICE futures backfill is in cotton_ice_historical_backfill.py.
"""
raise SystemExit(
    "\n"
    "PERMANENTLY DISABLED — cotton_futures_historical_backfill.py\n"
    "This script writes S/U-calibrated synthetic futures curves to the database.\n"
    "Synthetic futures data is worse than no data for model training.\n"
    "Use cotton_ice_historical_backfill.py for real ICE contract history.\n"
)

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import yfinance as yf
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from data.ingestion.historical_macro_backfill import (
    ORIGIN_COTTON_START_DATE,
    fetch_fred_pcottindusdm_weekly,
)
from database.base import SessionLocal
from database.ingestion_context import IngestionContext
from database.models import Cotton

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

SCRIPT_VERSION = "1.1.0"
SOURCE_NAME = "cotton_futures_curve_su_calibrated"
COTTON_TICKER = "CT=F"
YFINANCE_COTTON_URL = "https://finance.yahoo.com/quote/CT=F"
DEFAULT_SU_RATIO = Decimal("50")
SU_NEUTRAL_PCT = Decimal("50")
SU_RATE_SLOPE = Decimal("0.008")  # percent-points per S/U point
MONTHLY_RATE_CAP_PCT = Decimal("0.5")
MONTHLY_RATE_FLOOR_PCT = Decimal("-0.3")
COMMIT_BATCH_SIZE = 500


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _quantize_price(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def monthly_curve_rate_pct(su: Decimal) -> Decimal:
    """
    Continuous monthly curve rate in percent (e.g. 0.334 means +0.334%/month).

    monthly_rate_pct = (wasde_su_ratio_pct - 50) * 0.008
    Capped at +0.5%/month and -0.3%/month.
    """
    raw = (su - SU_NEUTRAL_PCT) * SU_RATE_SLOPE
    if raw > MONTHLY_RATE_CAP_PCT:
        return MONTHLY_RATE_CAP_PCT
    if raw < MONTHLY_RATE_FLOOR_PCT:
        return MONTHLY_RATE_FLOOR_PCT
    return raw.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def monthly_curve_rate_decimal(su: Decimal) -> Decimal:
    """Per-month shift as a decimal fraction for forward price multiplication."""
    return monthly_curve_rate_pct(su) / Decimal("100")


def build_su_calibrated_curve(near: Decimal, su: Decimal) -> dict[str, Optional[Decimal]]:
    rate = monthly_curve_rate_decimal(su)
    tenors = {
        "3m": 3,
        "6m": 6,
        "9m": 9,
        "12m": 12,
    }
    curve: dict[str, Optional[Decimal]] = {"near": _quantize_price(near)}
    for label, months in tenors.items():
        curve[label] = _quantize_price(near * (Decimal("1") + rate * Decimal(months)))

    three_m = curve["3m"]
    if near and near != 0 and three_m is not None:
        curve["contango_signal"] = _quantize_price((three_m - near) / near * Decimal("100"))
    else:
        curve["contango_signal"] = None
    return curve


def fetch_ct_f_weekly_history(
    start: date,
    end: date,
) -> dict[date, Decimal]:
    """Weekly CT=F settlement closes keyed by Monday of each week."""
    ticker = yf.Ticker(COTTON_TICKER)
    hist = ticker.history(start=start.isoformat(), end=end.isoformat(), interval="1wk")
    if hist is None or hist.empty:
        logger.warning("yfinance returned no CT=F weekly history.")
        return {}

    lookup: dict[date, Decimal] = {}
    for ts, row in hist.iterrows():
        week_monday = _monday_of(ts.date())
        close = row.get("Close")
        if close is None:
            continue
        try:
            lookup[week_monday] = _quantize_price(Decimal(str(float(close))))
        except (TypeError, ValueError):
            continue

    logger.info(f"yfinance {COTTON_TICKER}: {len(lookup)} weekly observations")
    return lookup


def _lookup_on_or_before(
    lookup: dict[date, Decimal],
    target: date,
) -> Optional[Decimal]:
    if not lookup:
        return None
    target_monday = _monday_of(target)
    if target_monday in lookup:
        return lookup[target_monday]
    prior_dates = [d for d in lookup if d <= target_monday]
    if prior_dates:
        return lookup[max(prior_dates)]
    return None


def resolve_near_price(
    row: Cotton,
    ct_lookup: dict[date, Decimal],
    fred_lookup: dict[date, Decimal],
) -> Optional[Decimal]:
    if row.as_of_date is None:
        return None

    near = _lookup_on_or_before(ct_lookup, row.as_of_date)
    if near is not None:
        return near

    near = _lookup_on_or_before(fred_lookup, row.as_of_date)
    if near is not None:
        return near

    if row.spot_price is not None:
        return _quantize_price(Decimal(str(row.spot_price)))

    return None


def apply_curve_to_row(
    row: Cotton,
    ct_lookup: dict[date, Decimal],
    fred_lookup: dict[date, Decimal],
) -> bool:
    near = resolve_near_price(row, ct_lookup, fred_lookup)
    if near is None:
        return False

    su = (
        Decimal(str(row.wasde_su_ratio_pct))
        if row.wasde_su_ratio_pct is not None
        else DEFAULT_SU_RATIO
    )
    curve = build_su_calibrated_curve(near, su)

    row.ice_futures_near = curve["near"]
    row.ice_futures_3m = curve["3m"]
    row.ice_futures_6m = curve["6m"]
    row.ice_futures_9m = curve["9m"]
    row.ice_futures_12m = curve["12m"]
    row.contango_signal = curve["contango_signal"]
    return True


def run_backfill(db: Optional[Session] = None) -> int:
    end = date.today()
    ct_lookup = fetch_ct_f_weekly_history(ORIGIN_COTTON_START_DATE, end)
    fred_weekly = fetch_fred_pcottindusdm_weekly(ORIGIN_COTTON_START_DATE, end)
    fred_lookup = {obs_date: price for obs_date, price in fred_weekly}

    if not ct_lookup and not fred_lookup:
        raise RuntimeError(
            "No CT=F or FRED PCOTTINDUSDM price history — cannot calibrate futures curve."
        )

    owns_session = db is None
    if db is None:
        db = SessionLocal()

    updated = 0
    rejected = 0
    try:
        rows = (
            db.query(Cotton)
            .filter(Cotton.as_of_date.isnot(None))
            .order_by(Cotton.as_of_date, Cotton.cotton_id)
            .all()
        )
        logger.info(f"Processing {len(rows)} cotton rows...")

        data_as_of = max((row.as_of_date for row in rows if row.as_of_date), default=end)

        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=YFINANCE_COTTON_URL,
            db=db,
        ) as ctx:
            ctx.set_as_of_date(data_as_of)
            for row in rows:
                if apply_curve_to_row(row, ct_lookup, fred_lookup):
                    ctx.increment_inserted()
                    updated += 1
                    if updated % COMMIT_BATCH_SIZE == 0:
                        db.commit()
                        logger.info(f"Updated {updated} cotton rows...")
                else:
                    ctx.increment_rejected(
                        f"cotton_id={row.cotton_id} as_of_date={row.as_of_date}: no near price"
                    )
                    rejected += 1

            db.commit()

        logger.info(
            f"S/U-calibrated futures backfill complete: {updated} updated, "
            f"{rejected} rejected, {len(rows)} total"
        )
        return updated
    except Exception:
        db.rollback()
        raise
    finally:
        if owns_session:
            db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Backfill ice_futures_* and contango_signal on all cotton rows using "
            "CT=F near-month prices and WASDE S/U-calibrated forward curve."
        )
    )
    parser.parse_args()
    try:
        run_backfill()
    except Exception as exc:
        logger.critical(f"Backfill failed: {exc}", exc_info=True)
        raise SystemExit(1) from exc
    raise SystemExit(0)
