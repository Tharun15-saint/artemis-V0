import argparse
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

import requests
import yfinance as yf
from dotenv import load_dotenv
from fredapi import Fred
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from database.base import SessionLocal, is_duplicate_row, mark_latest
from database.ingestion_context import IngestionContext
from database.models import CommodityFutures, Cotton
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

SCRIPT_VERSION = "2.0.0"
SOURCE_NAME = "cotton_ice_yfinance"
SOURCE_SYSTEM = "ice_yfinance"
YFINANCE_COTTON_URL = "https://finance.yahoo.com/quote/CT=F"

COTTON_TICKER = "CT=F"
FRED_COTTON_SERIES = "PCOTTINDUSDM"
SCHEDULE_INTERVAL_HOURS = 168
REQUEST_TIMEOUT = 30
ORIGIN_COUNTRY = "ICE No.2 Global"
US_ORIGIN = "US"

# NASS calibration (reference only — never write to database)
# USDA NASS PRICE RECEIVED is a US farm-gate survey price paid to domestic producers.
# Artemis cotton rows store ICE international traded prices (CT=F / FRED PCOTTINDUSDM),
# which reflect the world export market. For an importer cost intelligence platform,
# ICE traded prices are the correct commodity input for landed-cost and hedge models.
# fetch_nass_annual_calibration() may be used to compare series offline; do not call
# apply_nass_calibration() against production data.
NASS_CALIBRATION_DB_APPLY_ENABLED = False

NASS_BASE = "https://quickstats.nass.usda.gov/api/api_GET/"
NASS_CALIBRATION_START_YEAR = 2010
NASS_API_KEY = os.getenv("NASS_API_KEY", "")
GRADE = "No.2 SLM"
STAPLE = "1-3/32 inch"

CALIBRATED_PRICE_FIELDS = (
    "spot_price",
    "ice_futures_near",
    "ice_futures_3m",
    "ice_futures_6m",
    "ice_futures_9m",
    "ice_futures_12m",
)

NEUTRAL_SPREADS = {
    "3m": Decimal("1.0087"),
    "6m": Decimal("1.0175"),
    "9m": Decimal("1.0263"),
    "12m": Decimal("1.0350"),
}


def get_su_calibrated_spreads(db_session: Session) -> dict[str, Decimal]:
    """Query latest WASDE S/U ratio from cotton rows and calibrate spreads."""
    try:
        latest = (
            db_session.query(Cotton)
            .filter(Cotton.wasde_su_ratio_pct.isnot(None))
            .filter(Cotton.is_latest.is_(True))
            .order_by(desc(Cotton.as_of_date))
            .first()
        )

        if not latest or not latest.wasde_su_ratio_pct:
            logger.warning("No WASDE S/U ratio found — using neutral market spreads.")
            su = 55.0
        else:
            su = float(latest.wasde_su_ratio_pct)

        logger.info(f"S/U ratio for curve calibration: {su:.2f}%")

        if su > 62:
            label = "BEARISH"
            spreads = {
                "3m": Decimal("1.0050"),
                "6m": Decimal("1.0100"),
                "9m": Decimal("1.0150"),
                "12m": Decimal("1.0200"),
            }
        elif su > 55:
            label = "NEUTRAL"
            spreads = NEUTRAL_SPREADS.copy()
        elif su > 47:
            label = "SLIGHTLY_BULLISH"
            spreads = {
                "3m": Decimal("1.0063"),
                "6m": Decimal("1.0125"),
                "9m": Decimal("1.0188"),
                "12m": Decimal("1.0250"),
            }
        elif su > 40:
            label = "BULLISH"
            spreads = {
                "3m": Decimal("1.0038"),
                "6m": Decimal("1.0075"),
                "9m": Decimal("1.0113"),
                "12m": Decimal("1.0150"),
            }
        else:
            label = "SPIKE_RISK_BACKWARDATION"
            spreads = {
                "3m": Decimal("0.9975"),
                "6m": Decimal("0.9960"),
                "9m": Decimal("0.9950"),
                "12m": Decimal("0.9940"),
            }

        logger.info(
            f"Synthetic curve calibrated to {label} (S/U={su:.1f}%): "
            f"3m={spreads['3m']} 6m={spreads['6m']} "
            f"9m={spreads['9m']} 12m={spreads['12m']}"
        )
        return spreads

    except Exception as exc:
        logger.warning(f"S/U calibration failed: {exc} — using neutral spreads")
        return NEUTRAL_SPREADS.copy()


def _quantize_cotton_price(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _nass_price_to_cents_per_lb(raw_value: Any, unit_desc: str) -> Optional[Decimal]:
    try:
        amount = Decimal(str(raw_value))
    except Exception:
        return None

    unit = (unit_desc or "").upper()
    if unit == "$ / LB":
        return _quantize_cotton_price(amount * Decimal("100"))
    if unit in ("CENTS / LB", "¢ / LB"):
        return _quantize_cotton_price(amount)
    return None


def fetch_nass_annual_prices(
    start_year: int = NASS_CALIBRATION_START_YEAR,
    end_year: Optional[int] = None,
) -> dict[int, Decimal]:
    """Fetch US national annual cotton PRICE RECEIVED (farm gate) in cents/lb."""
    if not NASS_API_KEY:
        logger.error("NASS_API_KEY is not set.")
        return {}

    if end_year is None:
        end_year = date.today().year

    params = {
        "key": NASS_API_KEY,
        "commodity_desc": "COTTON",
        "statisticcat_desc": "PRICE RECEIVED",
        "format": "JSON",
        "freq_desc": "ANNUAL",
        "agg_level_desc": "NATIONAL",
    }

    try:
        response = requests.get(NASS_BASE, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        records = response.json().get("data", [])
    except requests.RequestException as exc:
        logger.error(f"NASS annual price fetch failed: {exc}")
        return {}

    prices: dict[int, Decimal] = {}
    for item in records:
        if item.get("class_desc") != "ALL CLASSES":
            continue
        if item.get("reference_period_desc") != "MARKETING YEAR":
            continue

        try:
            year = int(item.get("year"))
        except (TypeError, ValueError):
            continue
        if year < start_year or year > end_year:
            continue

        cents = _nass_price_to_cents_per_lb(item.get("Value"), item.get("unit_desc", ""))
        if cents is None:
            continue

        prices[year] = cents

    logger.info(
        f"NASS annual PRICE RECEIVED: {len(prices)} marketing years "
        f"({start_year}–{end_year})"
    )
    return prices


def fetch_nass_annual_calibration(db: Session) -> dict[int, Decimal]:
    """
    Reference helper: NASS farm-gate annual avg / DB US avg spot per crop_year.

    For offline comparison only. NASS farm-gate and ICE international traded prices
    measure different markets; factors must not be applied to cotton rows in the DB.
    """
    nass_prices = fetch_nass_annual_prices()
    if not nass_prices:
        return {}

    end_year = date.today().year
    factors: dict[int, Decimal] = {}

    for year in range(NASS_CALIBRATION_START_YEAR, end_year + 1):
        nass_price = nass_prices.get(year)
        if nass_price is None:
            logger.warning(f"NASS calibration {year}: no annual price — skipped")
            continue

        db_avg = (
            db.query(func.avg(Cotton.spot_price))
            .filter(
                Cotton.origin_country == US_ORIGIN,
                Cotton.crop_year == year,
                Cotton.spot_price.isnot(None),
            )
            .scalar()
        )
        if db_avg is None:
            logger.warning(
                f"NASS calibration {year}: no US cotton rows for crop_year — skipped"
            )
            continue

        db_avg_dec = _quantize_cotton_price(Decimal(str(db_avg)))
        if db_avg_dec == 0:
            logger.warning(f"NASS calibration {year}: DB average spot is zero — skipped")
            continue

        factor = _quantize_cotton_price(nass_price / db_avg_dec)
        factors[year] = factor
        logger.info(
            f"NASS calibration {year}: NASS={nass_price} ¢/lb | "
            f"DB US avg={db_avg_dec} ¢/lb | factor={factor}"
        )

    return factors


def apply_nass_calibration(
    db: Session,
    calibration_factors: Optional[dict[int, Decimal]] = None,
) -> int:
    """
    Reference implementation — disabled for production use.

    NASS PRICE RECEIVED is a US farm-gate price; our database uses ICE international
    traded prices, which are the correct input for importer cost intelligence.
    """
    if not NASS_CALIBRATION_DB_APPLY_ENABLED:
        logger.warning(
            "apply_nass_calibration is disabled: NASS farm-gate prices must not be "
            "scaled onto ICE international traded prices in the database."
        )
        return 0

    factors = calibration_factors or fetch_nass_annual_calibration(db)
    if not factors:
        logger.warning("No NASS calibration factors — nothing to apply.")
        return 0

    updated = 0
    for year, factor in sorted(factors.items()):
        rows = (
            db.query(Cotton)
            .filter(
                Cotton.origin_country == US_ORIGIN,
                Cotton.crop_year == year,
            )
            .all()
        )
        if not rows:
            continue

        for row in rows:
            for field in CALIBRATED_PRICE_FIELDS:
                current = getattr(row, field)
                if current is None:
                    continue
                setattr(
                    row,
                    field,
                    _quantize_cotton_price(Decimal(str(current)) * factor),
                )
            updated += 1

        logger.info(
            f"Applied NASS calibration factor {factor} to {len(rows)} US row(s) "
            f"(crop_year {year})"
        )

    if updated:
        db.commit()

    logger.info(f"NASS calibration complete: {updated} US cotton row(s) updated")
    return updated


def fetch_spot_from_fred() -> Optional[dict[str, Any]]:
    api_key = os.getenv("FRED_API_KEY", "")
    if not api_key:
        logger.error("FRED_API_KEY is not set.")
        return None

    try:
        fred = Fred(api_key=api_key)
        series = fred.get_series(FRED_COTTON_SERIES)
        if series is None or series.empty:
            logger.error("FRED returned no cotton history for %s.", FRED_COTTON_SERIES)
            return None

        series = series.dropna()
        if series.empty:
            logger.error("FRED cotton series has no valid observations.")
            return None

        spot = Decimal(str(round(float(series.iloc[-1]), 4)))
        obs_ts = series.index[-1]
        record_date = obs_ts.date() if hasattr(obs_ts, "date") else date.today()
        return {
            "date": record_date,
            "spot": spot,
            "source": f"FRED_{FRED_COTTON_SERIES}",
        }
    except Exception as exc:
        logger.error(f"FRED spot fetch failed: {exc}")
        return None


def get_active_cotton_contracts(reference_date: Optional[date] = None) -> list[dict[str, Any]]:
    if reference_date is None:
        reference_date = date.today()

    month_codes = {3: "H", 5: "K", 7: "N", 10: "V", 12: "Z"}
    trading_months = sorted(month_codes.keys())
    labels = ["spot_month", "approx_3m", "approx_6m", "approx_9m", "approx_12m"]

    contracts: list[dict[str, Any]] = []
    year = reference_date.year

    while len(contracts) < 5:
        for month in trading_months:
            delivery = date(year, month, 1)
            if delivery > reference_date + timedelta(days=7):
                code = month_codes[month]
                year_suffix = str(year)[2:]
                ticker = f"CT{code}{year_suffix}.NYB"
                months_fwd = round((delivery - reference_date).days / 30.44, 1)
                contracts.append(
                    {
                        "ticker": ticker,
                        "label": labels[len(contracts)],
                        "delivery": delivery,
                        "months_forward": months_fwd,
                    }
                )
                if len(contracts) >= 5:
                    break
        year += 1

    return contracts[:5]


def _contract_close_as_of(ticker: str, reference_date: date) -> Optional[Decimal]:
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
) -> Optional[dict[str, Any]]:
    if reference_date is None:
        reference_date = date.today()

    contracts = get_active_cotton_contracts(reference_date)
    results: dict[str, Any] = {}
    working_tickers: list[str] = []

    for contract in contracts:
        ticker = contract["ticker"]
        label = contract["label"]
        price = _contract_close_as_of(ticker, reference_date)
        if price is not None:
            is_valid, reason = validate_cotton_price(price, previous_spot)
            if is_valid:
                if reason.startswith("FLAG:") and ctx is not None:
                    ctx.record_flag(f"{label}: {reason}")
                results[label] = price
                working_tickers.append(ticker)
                logger.info(
                    f"  {ticker} ({label}, {contract['months_forward']}m fwd): {price} ¢/lb"
                )
            else:
                if ctx is not None:
                    ctx.increment_rejected(f"{label}: {reason}")
                logger.warning(f"  {ticker}: price {price} failed validation — skipped")
        else:
            logger.warning(f"  {ticker}: no data returned by yfinance")

    if len(results) < 3:
        logger.error(
            f"Real ICE curve fetch returned only {len(results)}/5 contracts. "
            f"Minimum 3 required. Falling back to synthetic."
        )
        return None

    results["contracts"] = working_tickers
    results["source"] = "yfinance_ICE_individual_contracts"
    logger.info(
        f"Real ICE curve fetched: {len(working_tickers)}/5 contracts. "
        f"Spot={results.get('spot_month')} | 12m={results.get('approx_12m')}"
    )
    return results


def build_curve(
    spot: Decimal,
    ice_curve_data: Optional[dict[str, Any]],
    db_session: Session,
) -> dict[str, Any]:
    if ice_curve_data:
        return {
            "3m": ice_curve_data.get("approx_3m", spot),
            "6m": ice_curve_data.get("approx_6m", spot),
            "9m": ice_curve_data.get("approx_9m", spot),
            "12m": ice_curve_data.get("approx_12m", spot),
            "source": "yfinance_ICE_individual_contracts",
            "is_real": True,
        }

    logger.warning(
        "Using S/U-calibrated synthetic curve — real ICE data unavailable. "
        "Hedging recommendations suppressed until real data returns."
    )
    spreads = get_su_calibrated_spreads(db_session)
    return {
        "3m": (spot * spreads["3m"]).quantize(Decimal("0.0001")),
        "6m": (spot * spreads["6m"]).quantize(Decimal("0.0001")),
        "9m": (spot * spreads["9m"]).quantize(Decimal("0.0001")),
        "12m": (spot * spreads["12m"]).quantize(Decimal("0.0001")),
        "source": "SYNTHETIC_SU_CALIBRATED_REAL_ICE_UNAVAILABLE",
        "is_real": False,
    }


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
    return prior.spot_price if prior else None


def _compute_contango(spot: Decimal, twelve_month: Decimal) -> Optional[Decimal]:
    if spot is None or spot == 0:
        return None
    return ((twelve_month - spot) / spot * Decimal("100")).quantize(Decimal("0.0001"))


def append_cotton_rows(
    db: Session,
    ctx: IngestionContext,
    spot: Decimal,
    curve: dict[str, Any],
    record_date: date,
    spot_source: str,
) -> bool:
    previous_spot = _previous_cotton_spot(db, record_date)
    validated_spot = validate_and_log(
        spot,
        lambda v: validate_cotton_price(v, previous_spot),
        ctx,
    )
    if validated_spot is None:
        return False

    twelve_m = curve["12m"]
    contango = _compute_contango(validated_spot, twelve_m)
    pulled_at = datetime.now(timezone.utc)
    cotton_filter = {"origin_country": ORIGIN_COUNTRY}
    cotton_values = {
        "as_of_date": record_date,
        "spot_price": validated_spot,
        "ice_futures_near": validated_spot,
        "ice_futures_3m": curve["3m"],
        "ice_futures_6m": curve["6m"],
        "ice_futures_9m": curve["9m"],
        "ice_futures_12m": twelve_m,
        "contango_signal": contango,
        "source": SOURCE_SYSTEM,
        "data_source_url": YFINANCE_COTTON_URL,
    }
    futures_values = {
        "as_of_date": record_date,
        "ice_cotton_2_spot": validated_spot,
        "ice_cotton_2_3m": curve["3m"],
        "ice_cotton_2_6m": curve["6m"],
        "ice_cotton_2_9m": curve["9m"],
        "ice_cotton_2_12m": twelve_m,
        "ocean_freight_ffa": None,
        "source": SOURCE_SYSTEM,
        "data_source_url": YFINANCE_COTTON_URL,
        "status": "LIVE" if curve.get("is_real") else "SYNTHETIC",
    }

    cotton_dup = is_duplicate_row(db, Cotton, cotton_filter, cotton_values)
    futures_dup = is_duplicate_row(
        db, CommodityFutures, {"as_of_date": record_date}, futures_values
    )
    if cotton_dup and futures_dup:
        ctx.stale()
        logger.info("Cotton and commodity_futures unchanged — skipping insert")
        return True

    if not cotton_dup:
        mark_latest(db, Cotton, {"origin_country": ORIGIN_COUNTRY, "as_of_date": record_date})
        db.add(
            Cotton(
                origin_country=ORIGIN_COUNTRY,
                grade=GRADE,
                staple_length=STAPLE,
                spot_price=validated_spot,
                ice_futures_near=validated_spot,
                ice_futures_3m=curve["3m"],
                ice_futures_6m=curve["6m"],
                ice_futures_9m=curve["9m"],
                ice_futures_12m=twelve_m,
                contango_signal=contango,
                crop_year=record_date.year,
                as_of_date=record_date,
                source=SOURCE_SYSTEM,
                data_source_url=YFINANCE_COTTON_URL,
                refresh="weekly",
                pulled_at=pulled_at,
                is_latest=True,
            )
        )
        ctx.increment_inserted()

    if not futures_dup:
        mark_latest(db, CommodityFutures, {"as_of_date": record_date})
        db.add(
            CommodityFutures(
                ice_cotton_2_spot=validated_spot,
                ice_cotton_2_3m=curve["3m"],
                ice_cotton_2_6m=curve["6m"],
                ice_cotton_2_9m=curve["9m"],
                ice_cotton_2_12m=twelve_m,
                ocean_freight_ffa=None,
                as_of_date=record_date,
                source=SOURCE_SYSTEM,
                data_source_url=YFINANCE_COTTON_URL,
                status="LIVE" if curve.get("is_real") else "SYNTHETIC",
                pulled_at=pulled_at,
                is_latest=True,
            )
        )
        ctx.increment_inserted()

    if cotton_dup:
        ctx.stale()
    if futures_dup:
        ctx.stale()

    return True


def run_once() -> bool:
    logger.info("Starting cotton ingestion...")
    db = SessionLocal()
    try:
        spot_data = fetch_spot_from_fred()
        if not spot_data:
            logger.error("Cotton spot fetch failed. Aborting.")
            return False

        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=YFINANCE_COTTON_URL,
            db=db,
        ) as ctx:
            ctx.set_as_of_date(spot_data["date"])
            previous_spot = _previous_cotton_spot(db, spot_data["date"])

            logger.info("Fetching real ICE futures curve from individual contracts...")
            ice_curve = fetch_real_ice_curve(
                spot_data["date"],
                previous_spot=previous_spot,
                ctx=ctx,
            )

            curve = build_curve(
                spot=spot_data["spot"],
                ice_curve_data=ice_curve,
                db_session=db,
            )

            if not append_cotton_rows(
                db=db,
                ctx=ctx,
                spot=spot_data["spot"],
                curve=curve,
                record_date=spot_data["date"],
                spot_source=spot_data["source"],
            ):
                return False

            if curve.get("is_real"):
                logger.info(
                    f"Cotton appended with REAL ICE curve: "
                    f"spot={spot_data['spot']} | "
                    f"3m={curve['3m']} | 6m={curve['6m']} | "
                    f"9m={curve['9m']} | 12m={curve['12m']}"
                )
            else:
                logger.warning(
                    f"Cotton appended with SYNTHETIC curve — real ICE unavailable. "
                    f"spot={spot_data['spot']}"
                )
            return True
    except Exception as exc:
        logger.critical(f"Cotton ingestion failed: {exc}", exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


def run_scheduled() -> None:
    logger.info(
        f"Cotton ingestion scheduler started. Running every {SCHEDULE_INTERVAL_HOURS} hours."
    )
    while True:
        success = run_once()
        status = "SUCCESS" if success else "FAILED"
        logger.info(
            f"Ingestion cycle complete [{status}]. "
            f"Next run in {SCHEDULE_INTERVAL_HOURS} hours."
        )
        time.sleep(SCHEDULE_INTERVAL_HOURS * 3600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch and store weekly ICE cotton spot and futures curve."
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help=(
            f"Run continuously every {SCHEDULE_INTERVAL_HOURS} hours. "
            "Without this flag, runs once and exits."
        ),
    )
    args = parser.parse_args()

    if args.schedule:
        run_scheduled()
    else:
        raise SystemExit(0 if run_once() else 1)
