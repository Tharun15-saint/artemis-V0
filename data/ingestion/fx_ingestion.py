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
from database.base import SessionLocal, is_duplicate_row, mark_latest
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

CURRENCY_TO_FIELD = {
    "INR": ("usd_inr", "USD_INR"),
    "BDT": ("usd_bdt", "USD_BDT"),
    "VND": ("usd_vnd", "USD_VND"),
    "CNY": ("usd_cny", "USD_CNY"),
    "TRY": ("usd_try", "USD_TRY"),
    "PKR": ("usd_pkr", "USD_PKR"),
}

SCHEDULE_INTERVAL_HOURS = 6
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 10
AV_RATE_LIMIT_SLEEP = 13


def _extract_rates(raw_rates: dict) -> dict[str, Decimal]:
    extracted: dict[str, Decimal] = {}
    for code in CURRENCY_TO_FIELD:
        if code not in raw_rates:
            continue
        extracted[code] = Decimal(str(raw_rates[code]))
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
        if len(rates) != len(CURRENCY_TO_FIELD):
            missing = set(CURRENCY_TO_FIELD) - set(rates)
            logger.error(f"Primary API missing currencies: {missing}")
            return None

        logger.info(f"Primary API success — USD/INR={rates['INR']}")
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
    for currency in CURRENCY_TO_FIELD:
        try:
            url = AV_FALLBACK_URL.format(currency=currency)
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code == 200:
                data = response.json()
                rate_data = data.get("Realtime Currency Exchange Rate", {})
                rate_str = rate_data.get("5. Exchange Rate")
                if rate_str:
                    rates[currency] = Decimal(str(rate_str))
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

    if len(rates) < 6:
        logger.error(
            f"Alpha Vantage fallback incomplete: got {len(rates)}/6 currencies"
        )
        return None if len(rates) < 4 else rates

    logger.info("Alpha Vantage fallback: all 6 currencies fetched")
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
            "PKR": latest.usd_pkr,
        }

    validated: dict[str, Optional[Decimal]] = {}
    for code, (_field_name, pair) in CURRENCY_TO_FIELD.items():
        validated[code] = validate_and_log(
            rates[code],
            lambda v, p=pair, prev=previous_by_code.get(code): validate_fx_rate(
                v, p, prev
            ),
            ctx,
        )

    if any(v is None for v in validated.values()):
        logger.error("FX validation failed for one or more currencies.")
        return None

    data_source_url = DATA_SOURCE_URL_TEMPLATE
    value_kwargs = {
        "usd_inr": validated["INR"],
        "usd_bdt": validated["BDT"],
        "usd_vnd": validated["VND"],
        "usd_cny": validated["CNY"],
        "usd_try": validated["TRY"],
        "usd_pkr": validated["PKR"],
        "source": SOURCE_SYSTEM,
        "data_source_url": data_source_url,
        "status": _quality_tag_for_source(source),
    }
    if is_duplicate_row(db, FxRates, {}, value_kwargs):
        ctx.stale()
        logger.info("FX rates unchanged from latest pull — skipping insert")
        return latest

    pulled_at = datetime.now(timezone.utc)
    mark_latest(db, FxRates, {"as_of_date": today})
    record = FxRates(
        usd_inr=validated["INR"],
        usd_bdt=validated["BDT"],
        usd_vnd=validated["VND"],
        usd_cny=validated["CNY"],
        usd_try=validated["TRY"],
        usd_pkr=validated["PKR"],
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
    logger.info(f"FX rates appended for data_as_of={today}")
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
                f"USD/TRY: {record.usd_try} | USD/PKR: {record.usd_pkr}"
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
