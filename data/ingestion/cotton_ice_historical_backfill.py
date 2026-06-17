"""
Real ICE No.2 cotton futures — historical backfill.

This script creates real ICE No.2 Global rows for historical dates using
actual contract prices from yfinance. No synthetic data. If a contract
is unavailable for a date, that tenor is NULL on that row.

Strategy:
  - Fetch distinct as_of_dates from cotton table (spot_only or synthetic_legacy rows)
  - For each date, identify the 5 nearest active ICE No.2 contracts
  - Fetch real closing prices via yfinance for each contract as of that date
  - Look up the FRED spot (already stored in cotton table for that date) and
    the USD/INR rate nearest that date
  - Insert a new cotton row with origin_country='ICE No.2 Global' containing
    real futures data + INR materialisation
  - Skip dates already covered by a real ICE No.2 Global row

Data availability reality:
  yfinance has reliable ICE cotton contract data from ~2008 forward.
  Older dates (pre-2008) will return no data — those stay as spot_only.
  Recent dates (last 12-24 months) get full 5/5 contract coverage.
  Dates 2-8 years back get 3-5 contracts (some expired before yfinance indexed them).

Run order:
  1. cotton_synthetic_purge.py    (mark existing rows)
  2. wasde_ingestion.py --backfill (seed cotton_supply_demand)
  3. THIS SCRIPT                  (real ICE futures history)
  4. cotton_ingestion.py          (weekly, ongoing)
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

import yfinance as yf
from sqlalchemy import desc, func, text
from sqlalchemy.orm import Session

from database.base import SessionLocal, is_duplicate_row, mark_latest
from database.ingestion_context import IngestionContext
from database.models.commodities import Cotton
from database.models.market_data import FxRates, CommodityFutures

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

SCRIPT_VERSION = "1.0.0"
SOURCE_NAME = "cotton_ice_real_backfill"
SOURCE_SYSTEM = "ice_yfinance_backfill"
YFINANCE_COTTON_URL = "https://finance.yahoo.com/quote/CT=F"

ORIGIN_COUNTRY = "ICE No.2 Global"
GRADE = "No.2 SLM"
STAPLE = "1-3/32 inch"
LBS_PER_KG = Decimal("2.20462")

ICE_MONTH_CODES = {3: "H", 5: "K", 7: "N", 10: "V", 12: "Z"}

# Throttle: yfinance will rate-limit if hammered
SLEEP_BETWEEN_DATES = 2.0    # seconds between date batches
SLEEP_BETWEEN_CONTRACTS = 0.3  # seconds between contract fetches per date

# Don't attempt ICE fetch for dates older than this — yfinance rarely has data
MAX_BACKFILL_YEARS = 15


# ---------------------------------------------------------------------------
# Contract helpers (same logic as cotton_ingestion.py)
# ---------------------------------------------------------------------------

def _active_contracts(reference_date: date) -> list[dict[str, Any]]:
    trading_months = sorted(ICE_MONTH_CODES.keys())
    labels = ["spot_month", "approx_3m", "approx_6m", "approx_9m", "approx_12m"]
    contracts: list[dict[str, Any]] = []
    year = reference_date.year
    while len(contracts) < 5:
        for month in trading_months:
            delivery = date(year, month, 1)
            if delivery > reference_date + timedelta(days=7):
                code = ICE_MONTH_CODES[month]
                year_suffix = str(year)[2:]
                ticker = f"CT{code}{year_suffix}.NYB"
                contracts.append({
                    "ticker": ticker,
                    "label": labels[len(contracts)],
                    "delivery": delivery,
                })
                if len(contracts) >= 5:
                    break
        year += 1
    return contracts[:5]


def _contract_close_on_date(ticker: str, reference_date: date) -> Optional[Decimal]:
    """
    Fetch the closing price of a specific ICE contract as close to reference_date
    as possible, looking back up to 7 trading days.
    Returns None cleanly if data is unavailable.
    """
    try:
        t = yf.Ticker(ticker)
        start = (reference_date - timedelta(days=10)).isoformat()
        end = (reference_date + timedelta(days=1)).isoformat()
        hist = t.history(start=start, end=end)
        if hist.empty:
            return None
        # Find the last bar on or before reference_date
        last_close: Optional[float] = None
        for bar_ts, bar in hist.iterrows():
            bar_date = bar_ts.date() if hasattr(bar_ts, "date") else bar_ts
            if bar_date <= reference_date:
                last_close = float(bar["Close"])
        if last_close is None:
            return None
        price = Decimal(str(round(last_close, 4)))
        # Basic sanity: ICE cotton trades 50–250 ¢/lb in any reasonable period
        if not (Decimal("40") <= price <= Decimal("280")):
            return None
        return price
    except Exception:
        return None


# ---------------------------------------------------------------------------
# FX lookup
# ---------------------------------------------------------------------------

def _usd_inr_on_date(db: Session, target: date) -> Optional[Decimal]:
    row = (
        db.query(FxRates)
        .filter(FxRates.usd_inr.isnot(None), FxRates.as_of_date <= target)
        .order_by(desc(FxRates.as_of_date))
        .first()
    )
    return Decimal(str(row.usd_inr)) if row and row.usd_inr else None


def _inr_per_kg(spot_cents_per_lb: Decimal, usd_inr: Decimal) -> Decimal:
    return (
        (spot_cents_per_lb / Decimal("100")) * LBS_PER_KG * usd_inr
    ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Fetch real curve for a historical date
# ---------------------------------------------------------------------------

def fetch_real_curve_for_date(reference_date: date) -> dict[str, Any]:
    """
    Fetch the real ICE No.2 futures curve for a historical date.
    Returns is_real=True only when >=3 of 5 contracts have valid prices.
    All None values are honest — no synthetic fallback.
    """
    contracts = _active_contracts(reference_date)
    results: dict[str, Optional[Decimal]] = {}
    available = 0

    for c in contracts:
        time.sleep(SLEEP_BETWEEN_CONTRACTS)
        price = _contract_close_on_date(c["ticker"], reference_date)
        if price is not None:
            results[c["label"]] = price
            available += 1

    is_real = available >= 3
    return {
        "spot_month": results.get("spot_month"),
        "approx_3m": results.get("approx_3m"),
        "approx_6m": results.get("approx_6m"),
        "approx_9m": results.get("approx_9m"),
        "approx_12m": results.get("approx_12m"),
        "contracts_available": available,
        "is_real": is_real,
    }


# ---------------------------------------------------------------------------
# FRED spot for a date (from existing cotton rows)
# ---------------------------------------------------------------------------

def _fred_spot_for_date(db: Session, reference_date: date) -> Optional[Decimal]:
    """
    Return the FRED spot price nearest to reference_date from existing cotton rows.
    We use any origin row (they all have the same FRED spot).
    """
    rows = (
        db.query(Cotton)
        .filter(
            Cotton.spot_price.isnot(None),
            Cotton.as_of_date >= reference_date - timedelta(days=35),
            Cotton.as_of_date <= reference_date + timedelta(days=7),
        )
        .order_by(func.abs(func.julianday(Cotton.as_of_date) - func.julianday(text(f"'{reference_date}'"))))
        .limit(1)
        .all()
    )
    if rows and rows[0].spot_price:
        return Decimal(str(rows[0].spot_price))
    return None


# ---------------------------------------------------------------------------
# Write row
# ---------------------------------------------------------------------------

def _contango(spot: Decimal, twelve_m: Optional[Decimal]) -> Optional[Decimal]:
    if twelve_m is None or spot == 0:
        return None
    return ((twelve_m - spot) / spot * Decimal("100")).quantize(Decimal("0.0001"))


def _quality_tier(is_real: bool, contracts: int) -> str:
    if is_real and contracts >= 4:
        return "full"
    if is_real and contracts >= 3:
        return "partial"
    return "spot_only"


def write_ice_row(
    db: Session,
    ctx: IngestionContext,
    reference_date: date,
    spot: Decimal,
    curve: dict[str, Any],
    usd_inr: Optional[Decimal],
) -> bool:
    twelve_m = curve.get("approx_12m")
    is_real = curve["is_real"]
    n_contracts = curve["contracts_available"]
    tier = _quality_tier(is_real, n_contracts)
    pulled = datetime.now(timezone.utc)

    spot_inr = _inr_per_kg(spot, usd_inr) if usd_inr else None

    dup_filter = {"origin_country": ORIGIN_COUNTRY, "as_of_date": reference_date}
    dup_values = {
        "spot_price": spot,
        "ice_futures_near": curve.get("spot_month"),
        "data_quality_tier": tier,
    }
    if is_duplicate_row(db, Cotton, dup_filter, dup_values):
        ctx.stale()
        return True

    mark_latest(db, Cotton, dup_filter)
    db.add(Cotton(
        origin_country=ORIGIN_COUNTRY,
        grade=GRADE,
        staple_length=STAPLE,
        spot_price=spot,
        spot_price_inr_per_kg=spot_inr,
        fx_usd_inr_at_ingestion=usd_inr,
        ice_futures_near=curve.get("spot_month"),
        ice_futures_3m=curve.get("approx_3m"),
        ice_futures_6m=curve.get("approx_6m"),
        ice_futures_9m=curve.get("approx_9m"),
        ice_futures_12m=twelve_m,
        contango_signal=_contango(spot, twelve_m),
        is_real_futures_data=is_real,
        futures_contracts_available=n_contracts,
        data_quality_tier=tier,
        crop_year=reference_date.year,
        as_of_date=reference_date,
        source=SOURCE_SYSTEM,
        data_source_url=YFINANCE_COTTON_URL,
        refresh="weekly",
        pulled_at=pulled,
        is_latest=True,
    ))
    db.commit()
    ctx.inserted()
    return True


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _dates_needing_backfill(db: Session, max_years_back: int = MAX_BACKFILL_YEARS) -> list[date]:
    """
    Return distinct as_of_dates from cotton table that do NOT yet have
    a real ICE No.2 Global row, ordered newest-first.
    Only goes back max_years_back years (yfinance limit).
    """
    cutoff = date.today() - timedelta(days=max_years_back * 365)

    # Dates that already have a real ICE No.2 Global row
    covered = {
        row[0] for row in db.execute(
            text("""
                SELECT DISTINCT as_of_date FROM cotton
                WHERE origin_country = 'ICE No.2 Global'
                  AND is_real_futures_data = 1
            """)
        ).fetchall()
    }

    # All distinct dates in cotton table within range
    all_dates = [
        row[0] for row in db.execute(
            text("""
                SELECT DISTINCT as_of_date FROM cotton
                WHERE as_of_date >= :cutoff
                  AND spot_price IS NOT NULL
                ORDER BY as_of_date DESC
            """),
            {"cutoff": cutoff.isoformat()},
        ).fetchall()
    ]

    # Filter to dates not already covered — convert strings to date objects
    result = []
    for d in all_dates:
        d_obj = date.fromisoformat(str(d)) if not isinstance(d, date) else d
        if d_obj not in covered:
            result.append(d_obj)

    logger.info(
        "Backfill target: %d dates (%d already covered, %d to process)",
        len(all_dates), len(covered), len(result),
    )
    return result


def run_backfill(max_years_back: int = MAX_BACKFILL_YEARS) -> bool:
    db = SessionLocal()
    try:
        dates = _dates_needing_backfill(db, max_years_back)
        if not dates:
            logger.info("No dates need backfilling.")
            return True

        logger.info(
            "Starting real ICE historical backfill: %d dates, newest first", len(dates)
        )

        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=YFINANCE_COTTON_URL,
            db=db,
        ) as ctx:
            ctx.set_as_of_date(dates[0])

            for i, ref_date in enumerate(dates):
                spot = _fred_spot_for_date(db, ref_date)
                if spot is None:
                    logger.warning("%s: no FRED spot found — skipping", ref_date)
                    ctx.increment_rejected(f"{ref_date}: no spot price")
                    continue

                usd_inr = _usd_inr_on_date(db, ref_date)
                curve = fetch_real_curve_for_date(ref_date)

                tier = _quality_tier(curve["is_real"], curve["contracts_available"])
                logger.info(
                    "[%d/%d] %s | spot=%.4f | contracts=%d/5 | tier=%s | INR=%s",
                    i + 1, len(dates), ref_date,
                    float(spot), curve["contracts_available"], tier,
                    f"{float(_inr_per_kg(spot, usd_inr)):.2f}" if usd_inr else "N/A",
                )

                write_ice_row(db, ctx, ref_date, spot, curve, usd_inr)

                if (i + 1) % 10 == 0:
                    logger.info(
                        "Progress: %d/%d dates | inserted=%d stale=%d rejected=%d",
                        i + 1, len(dates),
                        ctx.log.rows_inserted, ctx.log.rows_stale, ctx.log.rows_rejected,
                    )

                time.sleep(SLEEP_BETWEEN_DATES)

        logger.info("ICE historical backfill complete.")
        return True

    except Exception as exc:
        logger.critical("ICE backfill failed: %s", exc, exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Backfill real ICE No.2 futures for historical dates. "
            "Fetches actual contract prices from yfinance — no synthetic curves."
        )
    )
    parser.add_argument(
        "--years-back",
        type=int,
        default=MAX_BACKFILL_YEARS,
        help=f"How many years back to attempt (default {MAX_BACKFILL_YEARS}, "
             "yfinance rarely has data beyond 15 years for specific contracts)",
    )
    args = parser.parse_args()
    raise SystemExit(0 if run_backfill(args.years_back) else 1)
