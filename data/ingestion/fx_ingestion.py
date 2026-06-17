import argparse
import logging
import os
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

import requests
from sqlalchemy import desc
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from database.base import SessionLocal, is_duplicate_row
from database.ingestion_context import IngestionContext
from database.models import FxRates
from database.validation.ingestion_validators import (
    validate_and_log,
    validate_fx_rate,
)

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

SCRIPT_VERSION = "2.0.0"
SOURCE_NAME = "fx_rates_exchangerate_api"
SOURCE_SYSTEM = "exchangerate_api"
DATA_SOURCE_URL_TEMPLATE = "https://v6.exchangerate-api.com/v6/{api_key}/latest/USD"

# Live-ingestion source lineage. is_latest is tracked PER data product: the live
# daily product (these sources) and the historical weekly product
# (yfinance_historical_weekly / FRED fallback, owned by fx_historical_backfill.py)
# each maintain their own is_latest series and must never demote each other.
LIVE_SOURCES = ("exchangerate_api", "AlphaVantage_fallback")

EXCHANGE_RATE_API_KEY = os.getenv("EXCHANGE_RATE_API_KEY", "")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")

PRIMARY_URL = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_RATE_API_KEY}/latest/USD"
AV_FALLBACK_URL = (
    "https://www.alphavantage.co/query"
    "?function=CURRENCY_EXCHANGE_RATE"
    "&from_currency=USD"
    "&to_currency={currency}"
    f"&apikey={ALPHA_VANTAGE_KEY}"
)

# (field_on_model, validation_pair, invert_api_rate)
# ExchangeRate-API returns "X per 1 USD" for all codes.
# EUR and GBP are quoted "USD per 1 EUR/GBP" in our schema — so we invert.
CURRENCY_TO_FIELD = {
    "INR": ("usd_inr", "USD_INR", False),
    "BDT": ("usd_bdt", "USD_BDT", False),
    "VND": ("usd_vnd", "USD_VND", False),
    "CNY": ("usd_cny", "USD_CNY", False),
    "TRY": ("usd_try", "USD_TRY", False),
    "MAD": ("usd_mad", "USD_MAD", False),
    "PKR": ("usd_pkr", "USD_PKR", False),
    "IDR": ("usd_idr", "USD_IDR", False),
    "LKR": ("usd_lkr", "USD_LKR", False),
    "MXN": ("usd_mxn", "USD_MXN", False),
    "THB": ("usd_thb", "USD_THB", False),
    "KHR": ("usd_khr", "USD_KHR", False),
    "EUR": ("eur_usd", "EUR_USD", True),   # inverted: API gives USD/EUR
    "GBP": ("gbp_usd", "GBP_USD", True),   # inverted: API gives USD/GBP
}

SCHEDULE_INTERVAL_HOURS = 6
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 10
AV_RATE_LIMIT_SLEEP = 13


def _extract_rates(raw_rates: dict) -> dict[str, Decimal]:
    extracted: dict[str, Decimal] = {}
    for code, (_field, _pair, invert) in CURRENCY_TO_FIELD.items():
        if code not in raw_rates:
            continue
        val = Decimal(str(raw_rates[code]))
        extracted[code] = (Decimal("1") / val) if invert else val
    return extracted


def fetch_from_primary() -> Optional[dict[str, Decimal]]:
    if not EXCHANGE_RATE_API_KEY:
        logger.error("EXCHANGE_RATE_API_KEY is not set.")
        return None

    try:
        response = requests.get(PRIMARY_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        conversion_rates = payload.get("conversion_rates")
        if not conversion_rates:
            logger.error("Primary API response missing conversion_rates.")
            return None

        rates = _extract_rates(conversion_rates)
        missing = set(CURRENCY_TO_FIELD) - set(rates)
        if missing:
            logger.warning(f"Primary API missing currencies: {missing}")
        if "INR" not in rates:
            logger.error("Primary API missing INR — core rate unavailable")
            return None

        logger.info(f"Primary API success — USD/INR={rates['INR']}, EUR/USD={rates.get('EUR')}, GBP/USD={rates.get('GBP')}")
        return rates
    except requests.RequestException as exc:
        logger.error(f"Primary API request failed: {exc}")
        return None
    except (KeyError, TypeError, ValueError) as exc:
        logger.error(f"Primary API response parse failed: {exc}")
        return None


def fetch_from_alpha_vantage_fallback() -> Optional[dict[str, Decimal]]:
    if not ALPHA_VANTAGE_KEY:
        logger.error("ALPHA_VANTAGE_KEY is not set — Alpha Vantage fallback unavailable.")
        return None

    rates: dict[str, Decimal] = {}
    for currency, (_field, _pair, invert) in CURRENCY_TO_FIELD.items():
        try:
            url = AV_FALLBACK_URL.format(currency=currency)
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code == 200:
                data = response.json()
                rate_data = data.get("Realtime Currency Exchange Rate", {})
                rate_str = rate_data.get("5. Exchange Rate")
                if rate_str:
                    val = Decimal(str(rate_str))
                    rates[currency] = (Decimal("1") / val) if invert else val
                    time.sleep(AV_RATE_LIMIT_SLEEP)
                else:
                    logger.warning(
                        f"Alpha Vantage fallback: {currency} missing exchange rate in response"
                    )
            else:
                logger.warning(
                    f"Alpha Vantage fallback: {currency} returned {response.status_code}"
                )
        except Exception as exc:
            logger.warning(f"Alpha Vantage fallback: {currency} failed — {exc}")

    n_total = len(CURRENCY_TO_FIELD)
    if len(rates) < n_total - 3:
        logger.error(
            f"Alpha Vantage fallback incomplete: got {len(rates)}/{n_total} currencies"
        )
        return None if "INR" not in rates else rates

    logger.info(f"Alpha Vantage fallback: {len(rates)}/{n_total} currencies fetched")
    return rates


def fetch_rates_with_retry() -> tuple[Optional[dict[str, Decimal]], Optional[str]]:
    for attempt in range(1, MAX_RETRIES + 1):
        rates = fetch_from_primary()
        if rates:
            return rates, "ExchangeRate-API"
        logger.warning(
            f"Primary attempt {attempt} failed. Retrying in {RETRY_DELAY_SECONDS}s..."
        )
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_SECONDS)

    logger.error("Primary failed all retries. Trying Alpha Vantage fallback...")
    rates = fetch_from_alpha_vantage_fallback()
    if rates:
        return rates, "AlphaVantage_fallback"

    logger.critical(
        "Both FX sources failed. Using last DB row. "
        "Data will be stale. Check API keys and connectivity."
    )
    return None, None


def get_latest_db_rates(db: Session) -> Optional[FxRates]:
    return (
        db.query(FxRates)
        .filter(FxRates.is_latest.is_(True))
        .order_by(desc(FxRates.pulled_at))
        .first()
    )


def _quality_tag_for_source(source: str) -> str:
    if source == "ExchangeRate-API":
        return "EXCHANGE_RATE_API"
    if source == "AlphaVantage_fallback":
        return "ALPHA_VANTAGE"
    return "EXCHANGE_RATE_API"


def write_rates_to_db(
    rates: dict[str, Decimal],
    db: Session,
    ctx: IngestionContext,
    source: str,
) -> Optional[FxRates]:
    today = date.today()
    latest = get_latest_db_rates(db)
    previous_by_code: dict[str, Optional[Decimal]] = {}
    if latest is not None:
        previous_by_code = {
            "INR": latest.usd_inr,
            "BDT": latest.usd_bdt,
            "VND": latest.usd_vnd,
            "CNY": latest.usd_cny,
            "TRY": latest.usd_try,
            "MAD": latest.usd_mad,
            "PKR": latest.usd_pkr,
            "IDR": latest.usd_idr,
            "LKR": latest.usd_lkr,
            "MXN": latest.usd_mxn,
            "THB": latest.usd_thb,
            "KHR": latest.usd_khr,
            "EUR": latest.eur_usd,
            "GBP": latest.gbp_usd,
        }

    validated: dict[str, Optional[Decimal]] = {}
    for code, (_field_name, pair, _invert) in CURRENCY_TO_FIELD.items():
        raw = rates.get(code)
        if raw is None:
            validated[code] = None
            continue
        validated[code] = validate_and_log(
            raw,
            lambda v, p=pair, prev=previous_by_code.get(code): validate_fx_rate(
                v, p, prev
            ),
            ctx,
        )

    if validated.get("INR") is None:
        logger.error("INR validation failed — core rate missing, aborting write.")
        return None

    data_source_url = DATA_SOURCE_URL_TEMPLATE
    value_kwargs = {
        "usd_inr": validated["INR"],
        "usd_bdt": validated.get("BDT"),
        "usd_vnd": validated.get("VND"),
        "usd_cny": validated.get("CNY"),
        "usd_try": validated.get("TRY"),
        "usd_mad": validated.get("MAD"),
        "usd_pkr": validated.get("PKR"),
        "usd_idr": validated.get("IDR"),
        "usd_lkr": validated.get("LKR"),
        "usd_mxn": validated.get("MXN"),
        "usd_thb": validated.get("THB"),
        "usd_khr": validated.get("KHR"),
        "eur_usd": validated.get("EUR"),
        "gbp_usd": validated.get("GBP"),
        "source": SOURCE_SYSTEM,
        "data_source_url": data_source_url,
        "status": _quality_tag_for_source(source),
    }
    # Duplicate check scoped to today — prevents same-day re-runs from inserting twice
    if is_duplicate_row(db, FxRates, {"as_of_date": today}, value_kwargs):
        ctx.stale()
        logger.info("FX rates unchanged from today's pull — skipping insert")
        return latest

    pulled_at = datetime.now(timezone.utc)
    # Demote only PRIOR LIVE rows for today — never the historical weekly/FRED
    # backbone. (Was mark_latest({"as_of_date": today}), which demoted every
    # source for the date and could knock a weekly row out of the latest view.)
    db.query(FxRates).filter(
        FxRates.as_of_date == today,
        FxRates.source.in_(LIVE_SOURCES),
        FxRates.is_latest.is_(True),
    ).update({"is_latest": False}, synchronize_session="fetch")
    record = FxRates(
        usd_inr=validated["INR"],
        usd_bdt=validated.get("BDT"),
        usd_vnd=validated.get("VND"),
        usd_cny=validated.get("CNY"),
        usd_try=validated.get("TRY"),
        usd_mad=validated.get("MAD"),
        usd_pkr=validated.get("PKR"),
        usd_idr=validated.get("IDR"),
        usd_lkr=validated.get("LKR"),
        usd_mxn=validated.get("MXN"),
        usd_thb=validated.get("THB"),
        usd_khr=validated.get("KHR"),
        eur_usd=validated.get("EUR"),
        gbp_usd=validated.get("GBP"),
        source=SOURCE_SYSTEM,
        data_source_url=data_source_url,
        refresh="real_time_6h",
        status=_quality_tag_for_source(source),
        as_of_date=today,
        pulled_at=pulled_at,
        is_latest=True,
    )
    db.add(record)
    db.flush()
    ctx.increment_inserted()
    logger.info(
        f"FX rates written for {today} — "
        f"USD/INR={record.usd_inr} EUR/USD={record.eur_usd} GBP/USD={record.gbp_usd}"
    )
    return record


def run_once() -> bool:
    db = SessionLocal()
    try:
        logger.info("Starting FX rate ingestion...")
        today = date.today()

        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=PRIMARY_URL.split("/v6/")[0] + "/v6/{api_key}/latest/USD",
            db=db,
        ) as ctx:
            ctx.set_as_of_date(today)
            rates, source = fetch_rates_with_retry()

            if rates is None:
                last_record = get_latest_db_rates(db)
                if last_record:
                    age_hours = (
                        datetime.now(timezone.utc) - (last_record.pulled_at or last_record.created_at)
                    ).total_seconds() / 3600
                    logger.warning(
                        f"Using cached rates from {age_hours:.1f} hours ago. "
                        f"USD/INR={last_record.usd_inr} (stale)"
                    )
                    ctx.set_failed("Both FX API sources failed")
                    return False
                ctx.set_failed("No FX data available — database empty and APIs down")
                return False

            record = write_rates_to_db(rates, db, ctx, source or "ExchangeRate-API")
            if record is None:
                return False

            logger.info(
                f"FX rates written — ID: {record.fx_rate_id} | "
                f"USD/INR: {record.usd_inr} | USD/BDT: {record.usd_bdt} | "
                f"USD/VND: {record.usd_vnd} | USD/CNY: {record.usd_cny} | "
                f"USD/TRY: {record.usd_try} | USD/MAD: {record.usd_mad} | "
                f"USD/PKR: {record.usd_pkr} | EUR/USD: {record.eur_usd} | "
                f"GBP/USD: {record.gbp_usd}"
            )
            return True
    except Exception as exc:
        logger.critical(f"Unexpected error in FX ingestion: {exc}", exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


def run_scheduled() -> None:
    logger.info(
        f"FX ingestion scheduler started. Running every {SCHEDULE_INTERVAL_HOURS} hours."
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
        description="Fetch and store live FX rates for the Artemis intelligence platform."
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
        success = run_once()
        raise SystemExit(0 if success else 1)
