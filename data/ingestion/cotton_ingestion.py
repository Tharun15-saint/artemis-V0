"""
ICE No.2 cotton price ingestion — real data only.

Sources:
  Spot:    FRED PCOTTINDUSDM (Cotlook A Index, monthly, USD cents/lb)
  Futures: yfinance individual ICE No.2 contracts (CT{M}{YY}.NYB)

Real data only policy:
  - When real ICE futures contracts are unavailable, futures fields are written
    as NULL and data_quality_tier = 'spot_only'.
  - No synthetic or S/U-calibrated curves are ever written to the database.
  - No data is better than fabricated data: a NULL is honest; a synthetic price
    is a lie that the model will learn from and get wrong.

INR materialization:
  spot_price_inr_per_kg is computed at write time from:
    (spot_price / 100) / 0.453592 × usd_inr
  where usd_inr is taken from the latest FxRates row.
  This is the entry point for the RRK cost chain (cotton → yarn → fabric).

Scheduling: weekly (SCHEDULE_INTERVAL_HOURS = 168).
WASDE enrichment is handled separately by wasde_ingestion.py.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

import yfinance as yf
from dotenv import load_dotenv
from fredapi import Fred
from sqlalchemy import desc
from sqlalchemy.orm import Session

from database.base import SessionLocal, is_duplicate_row, mark_latest
from database.ingestion_context import IngestionContext
from database.models import CommodityFutures, Cotton, FxRates
from database.validation.ingestion_validators import (
    validate_and_log,
    validate_cotton_price,
)

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

SCRIPT_VERSION = "3.0.0"
SOURCE_NAME = "cotton_ice_real_only"
SOURCE_SYSTEM = "ice_yfinance_fred"
YFINANCE_COTTON_URL = "https://finance.yahoo.com/quote/CT=F"
FRED_COTTON_SERIES = "PCOTTINDUSDM"

COTTON_TICKER = "CT=F"
SCHEDULE_INTERVAL_HOURS = 168
REQUEST_TIMEOUT = 30
ORIGIN_COUNTRY = "ICE No.2 Global"
GRADE = "No.2 SLM"
STAPLE = "1-3/32 inch"

# ICE No.2 contracts trade on March/May/July/October/December delivery months
ICE_MONTH_CODES = {3: "H", 5: "K", 7: "N", 10: "V", 12: "Z"}

# lbs per kg — fixed physical constant
LBS_PER_KG = Decimal("2.20462")


# ---------------------------------------------------------------------------
# FX lookup for INR materialisation
# ---------------------------------------------------------------------------

def _get_latest_usd_inr(db: Session) -> Optional[Decimal]:
    """Return the most recent USD/INR rate from fx_rates table."""
    row = (
        db.query(FxRates)
        .filter(FxRates.usd_inr.isnot(None), FxRates.is_latest.is_(True))
        .order_by(desc(FxRates.as_of_date))
        .first()
    )
    if row and row.usd_inr:
        return Decimal(str(row.usd_inr))
    return None


def _cotton_inr_per_kg(spot_cents_per_lb: Decimal, usd_inr: Decimal) -> Decimal:
    """
    Convert ICE cotton spot price (USD cents/lb) to INR per kg.

    price_usd_per_lb   = spot_cents_per_lb / 100
    price_usd_per_kg   = price_usd_per_lb × 2.20462
    price_inr_per_kg   = price_usd_per_kg × usd_inr
    """
    return (
        (spot_cents_per_lb / Decimal("100")) * LBS_PER_KG * usd_inr
    ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# FRED spot price fetch
# ---------------------------------------------------------------------------

def fetch_spot_from_fred() -> Optional[dict[str, Any]]:
    """
    Fetch the latest Cotlook A / PCOTTINDUSDM monthly average from FRED.
    This is always the real, authoritative world cotton price benchmark.
    Returns None only on network failure — never falls back to fabricated data.
    """
    api_key = os.getenv("FRED_API_KEY", "")
    if not api_key:
        logger.error("FRED_API_KEY is not set — cannot fetch cotton spot price.")
        return None

    try:
        fred = Fred(api_key=api_key)
        series = fred.get_series(FRED_COTTON_SERIES)
        if series is None or series.empty:
            logger.error("FRED returned no data for %s.", FRED_COTTON_SERIES)
            return None

        series = series.dropna()
        if series.empty:
            logger.error("FRED %s series has no non-null observations.", FRED_COTTON_SERIES)
            return None

        spot = Decimal(str(round(float(series.iloc[-1]), 4)))
        obs_ts = series.index[-1]
        record_date = obs_ts.date() if hasattr(obs_ts, "date") else date.today()
        logger.info("FRED %s: %.4f ¢/lb as of %s", FRED_COTTON_SERIES, spot, record_date)
        return {"date": record_date, "spot": spot, "source": f"FRED_{FRED_COTTON_SERIES}"}
    except Exception as exc:
        logger.error("FRED spot fetch failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# ICE futures curve (real contracts only)
# ---------------------------------------------------------------------------

def get_active_cotton_contracts(reference_date: Optional[date] = None) -> list[dict[str, Any]]:
    """
    Build the list of the next 5 ICE No.2 contract delivery months after reference_date.
    Returns contracts sorted by delivery date, labelled spot_month through approx_12m.
    """
    if reference_date is None:
        reference_date = date.today()

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
                months_fwd = round((delivery - reference_date).days / 30.44, 1)
                contracts.append({
                    "ticker": ticker,
                    "label": labels[len(contracts)],
                    "delivery": delivery,
                    "months_forward": months_fwd,
                })
                if len(contracts) >= 5:
                    break
        year += 1

    return contracts[:5]


def _contract_close(ticker: str, reference_date: date) -> Optional[Decimal]:
    """
    Return the closing price for an ICE contract as of reference_date.
    Returns None cleanly when data is unavailable — never raises.
    """
    try:
        t = yf.Ticker(ticker)
        if reference_date >= date.today():
            hist = t.history(period="5d")
        else:
            start = (reference_date - timedelta(days=45)).isoformat()
            end = (reference_date + timedelta(days=1)).isoformat()
            hist = t.history(start=start, end=end)

        if hist.empty:
            return None

        if reference_date < date.today():
            last_close = None
            for bar_idx, bar in hist.iterrows():
                bar_date = bar_idx.date() if hasattr(bar_idx, "date") else bar_idx
                if bar_date <= reference_date:
                    last_close = bar["Close"]
            if last_close is None:
                return None
            return Decimal(str(round(float(last_close), 4)))

        return Decimal(str(round(float(hist["Close"].iloc[-1]), 4)))
    except Exception:
        return None


def fetch_real_ice_curve(
    reference_date: Optional[date] = None,
    previous_spot: Optional[Decimal] = None,
    ctx: Optional[IngestionContext] = None,
) -> dict[str, Any]:
    """
    Attempt to fetch the real ICE No.2 futures curve.

    Returns a dict with keys: spot_month, approx_3m, approx_6m, approx_9m,
    approx_12m, contracts_available, is_real.

    is_real = True only when at least 3 of 5 contracts returned valid prices.
    When is_real = False, all futures price values are None.
    No synthetic fallback is ever computed.
    """
    if reference_date is None:
        reference_date = date.today()

    contracts = get_active_cotton_contracts(reference_date)
    results: dict[str, Optional[Decimal]] = {}
    contracts_available = 0

    for contract in contracts:
        ticker = contract["ticker"]
        label = contract["label"]
        price = _contract_close(ticker, reference_date)

        if price is None:
            logger.info("  %s (%s): no data from yfinance", ticker, label)
            continue

        is_valid, reason = validate_cotton_price(price, previous_spot)
        if not is_valid:
            logger.warning("  %s: %s — rejected", ticker, reason)
            if ctx is not None:
                ctx.increment_rejected(f"{label}: {reason}")
            continue

        results[label] = price
        contracts_available += 1
        logger.info(
            "  %s (%s, %.1fm fwd): %.4f ¢/lb",
            ticker, label, contract["months_forward"], price,
        )

    if contracts_available < 3:
        logger.warning(
            "Real ICE curve: only %d/5 contracts available (minimum 3 required). "
            "Futures prices will be NULL for this row — no synthetic fallback.",
            contracts_available,
        )
        return {
            "spot_month": None, "approx_3m": None, "approx_6m": None,
            "approx_9m": None, "approx_12m": None,
            "contracts_available": contracts_available,
            "is_real": False,
        }

    logger.info(
        "Real ICE curve: %d/5 contracts. spot=%.4f | 12m=%s",
        contracts_available,
        results.get("spot_month") or 0,
        results.get("approx_12m"),
    )
    return {
        "spot_month": results.get("spot_month"),
        "approx_3m": results.get("approx_3m"),
        "approx_6m": results.get("approx_6m"),
        "approx_9m": results.get("approx_9m"),
        "approx_12m": results.get("approx_12m"),
        "contracts_available": contracts_available,
        "is_real": True,
    }


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _previous_cotton_spot(db: Session, as_of: date) -> Optional[Decimal]:
    prior = (
        db.query(Cotton)
        .filter(
            Cotton.origin_country == ORIGIN_COUNTRY,
            Cotton.as_of_date < as_of,
            Cotton.is_latest.is_(True),
        )
        .order_by(desc(Cotton.as_of_date))
        .first()
    )
    return Decimal(str(prior.spot_price)) if prior and prior.spot_price else None


def _compute_contango(spot: Decimal, twelve_month: Optional[Decimal]) -> Optional[Decimal]:
    if twelve_month is None or spot == 0:
        return None
    return ((twelve_month - spot) / spot * Decimal("100")).quantize(Decimal("0.0001"))


def _data_quality_tier(is_real: bool, contracts_available: int) -> str:
    if is_real and contracts_available >= 4:
        return "full"
    if is_real and contracts_available >= 3:
        return "partial"
    return "spot_only"


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def append_cotton_rows(
    db: Session,
    ctx: IngestionContext,
    spot: Decimal,
    curve: dict[str, Any],
    record_date: date,
) -> bool:
    """
    Write one Cotton row and one CommodityFutures row for record_date.

    Futures prices are written as-is from curve (may be None).
    spot_price_inr_per_kg is materialized from the latest FxRates.
    """
    previous_spot = _previous_cotton_spot(db, record_date)
    validated_spot = validate_and_log(
        spot,
        lambda v: validate_cotton_price(v, previous_spot),
        ctx,
    )
    if validated_spot is None:
        return False

    # Materialise INR/kg at write time
    usd_inr = _get_latest_usd_inr(db)
    spot_inr_per_kg: Optional[Decimal] = None
    if usd_inr is not None:
        spot_inr_per_kg = _cotton_inr_per_kg(validated_spot, usd_inr)
        logger.info(
            "INR materialisation: %.4f ¢/lb × %.4f INR/USD ÷ %.6f lb/kg = %.4f INR/kg",
            validated_spot, usd_inr, float(Decimal("1") / LBS_PER_KG), spot_inr_per_kg,
        )
    else:
        logger.warning(
            "No USD/INR rate in fx_rates — spot_price_inr_per_kg will be NULL. "
            "Run fx_ingestion.py first."
        )

    twelve_m = curve.get("approx_12m")
    contango = _compute_contango(validated_spot, twelve_m)
    is_real = curve["is_real"]
    contracts_available = curve["contracts_available"]
    quality_tier = _data_quality_tier(is_real, contracts_available)
    pulled_at = datetime.now(timezone.utc)

    cotton_filter = {"origin_country": ORIGIN_COUNTRY, "as_of_date": record_date}
    cotton_values = {
        "spot_price": validated_spot,
        "ice_futures_near": curve.get("spot_month"),
        "ice_futures_3m": curve.get("approx_3m"),
        "ice_futures_6m": curve.get("approx_6m"),
        "ice_futures_9m": curve.get("approx_9m"),
        "ice_futures_12m": twelve_m,
    }

    cotton_dup = is_duplicate_row(db, Cotton, cotton_filter, cotton_values)
    futures_dup = is_duplicate_row(
        db, CommodityFutures, {"as_of_date": record_date},
        {"ice_cotton_2_spot": validated_spot, "ice_cotton_2_3m": curve.get("approx_3m")},
    )

    if cotton_dup and futures_dup:
        ctx.stale()
        logger.info("Cotton and commodity_futures unchanged — skipping insert")
        return True

    if not cotton_dup:
        mark_latest(db, Cotton, {"origin_country": ORIGIN_COUNTRY, "as_of_date": record_date})
        db.add(Cotton(
            origin_country=ORIGIN_COUNTRY,
            grade=GRADE,
            staple_length=STAPLE,
            spot_price=validated_spot,
            spot_price_inr_per_kg=spot_inr_per_kg,
            fx_usd_inr_at_ingestion=usd_inr,
            ice_futures_near=curve.get("spot_month"),
            ice_futures_3m=curve.get("approx_3m"),
            ice_futures_6m=curve.get("approx_6m"),
            ice_futures_9m=curve.get("approx_9m"),
            ice_futures_12m=twelve_m,
            contango_signal=contango,
            is_real_futures_data=is_real,
            futures_contracts_available=contracts_available,
            data_quality_tier=quality_tier,
            crop_year=record_date.year,
            as_of_date=record_date,
            source=SOURCE_SYSTEM,
            data_source_url=YFINANCE_COTTON_URL,
            refresh="weekly",
            pulled_at=pulled_at,
            is_latest=True,
        ))
        ctx.increment_inserted()

    if not futures_dup:
        mark_latest(db, CommodityFutures, {"as_of_date": record_date})
        db.add(CommodityFutures(
            ice_cotton_2_spot=validated_spot,
            ice_cotton_2_3m=curve.get("approx_3m"),
            ice_cotton_2_6m=curve.get("approx_6m"),
            ice_cotton_2_9m=curve.get("approx_9m"),
            ice_cotton_2_12m=twelve_m,
            ocean_freight_ffa=None,
            as_of_date=record_date,
            source=SOURCE_SYSTEM,
            data_source_url=YFINANCE_COTTON_URL,
            status="LIVE" if is_real else "SPOT_ONLY",
            pulled_at=pulled_at,
            is_latest=True,
        ))
        ctx.increment_inserted()

    if cotton_dup:
        ctx.stale()
    if futures_dup:
        ctx.stale()

    return True


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run_once() -> bool:
    logger.info("Starting cotton ingestion (real data only)...")
    db = SessionLocal()
    try:
        spot_data = fetch_spot_from_fred()
        if not spot_data:
            logger.error("Cotton spot fetch failed — aborting. No data written.")
            return False

        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=YFINANCE_COTTON_URL,
            db=db,
        ) as ctx:
            ctx.set_as_of_date(spot_data["date"])
            previous_spot = _previous_cotton_spot(db, spot_data["date"])

            logger.info("Fetching real ICE futures contracts...")
            curve = fetch_real_ice_curve(
                spot_data["date"],
                previous_spot=previous_spot,
                ctx=ctx,
            )

            if not append_cotton_rows(
                db=db,
                ctx=ctx,
                spot=spot_data["spot"],
                curve=curve,
                record_date=spot_data["date"],
            ):
                return False

            if curve["is_real"]:
                logger.info(
                    "Cotton written — REAL curve: spot=%.4f | 3m=%s | 6m=%s | 9m=%s | 12m=%s "
                    "| INR/kg=%s | quality=%s",
                    spot_data["spot"],
                    curve.get("approx_3m"), curve.get("approx_6m"),
                    curve.get("approx_9m"), curve.get("approx_12m"),
                    "pending" if not _get_latest_usd_inr(db) else "computed",
                    _data_quality_tier(curve["is_real"], curve["contracts_available"]),
                )
            else:
                logger.warning(
                    "Cotton written — SPOT ONLY: spot=%.4f | futures=NULL (%d/5 contracts). "
                    "This is correct — no synthetic data written.",
                    spot_data["spot"], curve["contracts_available"],
                )
            return True

    except Exception as exc:
        logger.critical("Cotton ingestion failed: %s", exc, exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


def run_scheduled() -> None:
    logger.info(
        "Cotton ingestion scheduler started — every %d hours.", SCHEDULE_INTERVAL_HOURS
    )
    while True:
        success = run_once()
        logger.info(
            "Ingestion cycle %s — next run in %d hours.",
            "SUCCESS" if success else "FAILED",
            SCHEDULE_INTERVAL_HOURS,
        )
        time.sleep(SCHEDULE_INTERVAL_HOURS * 3600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch real ICE cotton spot and futures. No synthetic fallback."
    )
    parser.add_argument("--schedule", action="store_true")
    args = parser.parse_args()

    if args.schedule:
        run_scheduled()
    else:
        raise SystemExit(0 if run_once() else 1)
